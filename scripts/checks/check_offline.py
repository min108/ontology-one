"""Reject public URLs in runtime source and built assets, with reviewed exceptions."""

from __future__ import annotations

import argparse
import ast
import ipaddress
import json
import re
import sys
import tokenize
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
URL = re.compile(
    r"""(?<![A-Za-z0-9:])(?:(?:https?|wss?):)?//(?:[A-Za-z0-9_.%:+-]+@)?(?:[A-Za-z0-9][A-Za-z0-9_.%-]*|\[[0-9a-fA-F:]+\])(?::[0-9]*)?(?:[/?#][^\s"'`<>\\),;{}]*)?""",
    re.IGNORECASE,
)
MODEL_URL = "https://api.deepseek.com/anthropic"
RELEASE_URL = "https://github.com/min108/ontology-one/releases"
NAMESPACES = {
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/2000/xmlns/",
    "http://www.w3.org/1999/xhtml",
    "http://www.w3.org/1998/Math/MathML",
    "http://www.w3.org/1999/xlink",
    "http://www.w3.org/XML/1998/namespace",
}
SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".css", ".html", ".svg", ".json"}


def runtime_python(text: str) -> str:
    """Documentation is not executed; keep line offsets for diagnostics."""
    lines = text.splitlines(keepends=True)
    try:
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.body
                and isinstance(node.body[0], ast.Expr)
            ):
                expression = node.body[0]
                if isinstance(expression.value, ast.Constant) and isinstance(expression.value.value, str):
                    for index in range(expression.lineno - 1, expression.end_lineno or expression.lineno):
                        lines[index] = "\n"
        for token in tokenize.generate_tokens(StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                index, column = token.start
                lines[index - 1] = lines[index - 1][:column] + "\n"
    except (SyntaxError, tokenize.TokenError):
        return text
    return "".join(lines)


def local_url(url: str) -> bool:
    host = urlsplit(url if not url.startswith("//") else "https:" + url).hostname or ""
    if (
        host == "localhost"
        or host.endswith((".localhost", ".internal", ".test"))
        or host in {"ontology_host", "source.internal.example"}
    ):
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or address.is_private or address.is_link_local
    except ValueError:
        return False


def permitted(text: str, start: int, url: str, path: str) -> bool:
    before = text[max(0, start - 100) : start]
    request = re.search(r'(?:fetch|import|url|src|sendBeacon|WebSocket|EventSource)\s*[:=(]\s*["\']?$', before)
    if url in NAMESPACES and not request:
        return True
    compiled = path.startswith("frontend/dist/")
    model_paths = {
        "backend/skill_harness.py",
        "frontend/src/components/HarnessConfiguration.tsx",
        "frontend/src/legacy/components/HarnessConfiguration.tsx",
        "frontend/src/features/admin/model/ModelPage.tsx",
    }
    if url == MODEL_URL and (path in model_paths or compiled) and not request:
        return True
    release_paths = {
        "frontend/src/pages/Settings.tsx",
        "frontend/src/legacy/pages/Settings.tsx",
        "frontend/src/features/admin/backup/BackupPage.tsx",
    }
    if url == RELEASE_URL and (path in release_paths or compiled):
        return bool(re.search(r'href\s*[:=]\s*["\']$', before))
    if compiled and not request:
        vendor = json.loads(Path(__file__).with_name("offline_vendor_urls.json").read_text())
        kind = next((entry["kind"] for entry in vendor if entry["url"] == url), "")
        after = text[start + len(url) : start + len(url) + 600]
        if kind == "react_diagnostic":
            return bool(re.search(r'var [\w$]+="$', before) and "encodeURIComponent" in after)
        if kind == "router_diagnostic":
            return before.endswith("must be used within a data router.  See ")
        if kind == "polyfill_diagnostic":
            return before.endswith("we recommend you load a polyfill such as ")
        if kind == "reactflow_attribution":
            variable = re.search(r'const ([\w$]+)="$', before)
            return bool(
                variable
                and '"aria-label":"React Flow attribution"' in after
                and re.search(r"href:" + re.escape(variable.group(1)) + r',target:"_blank"', after)
            )
    return False


def scan_text(text: str, path: str) -> list[str]:
    if path.endswith(".py"):
        text = runtime_python(text)
    text = text.replace(r"/\//g", "/__escaped_slash__/g")
    text = re.sub(
        r"\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})", lambda match: chr(int(match.group(1) or match.group(2), 16)), text
    )
    text = re.sub(r"\\+\.", ".", text).replace(r"\/", "/")
    errors = []
    for match in URL.finditer(text):
        url = match.group()
        if local_url(url) or permitted(text, match.start(), url, path):
            continue
        line = text.count("\n", 0, match.start()) + 1
        errors.append(f"{path}:{line}: public URL {url}")
    return errors


def scan_tree(root: Path, *, require_dist: bool = False) -> list[str]:
    errors = []
    dist = root / "frontend/dist"
    if require_dist and not (dist / "index.html").is_file():
        errors.append("frontend/dist/index.html: build output missing")
    paths = [root / "backend", root / "frontend/src", root / "frontend/public", dist]
    for directory in paths:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix in SUFFIXES:
                errors.extend(scan_text(path.read_text(encoding="utf-8"), path.relative_to(root).as_posix()))
    index = root / "frontend/index.html"
    if index.is_file():
        errors.extend(scan_text(index.read_text(), "frontend/index.html"))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--require-dist", action="store_true")
    args = parser.parse_args()
    errors = scan_tree(args.root, require_dist=args.require_dist)
    for error in errors:
        print(error, file=sys.stderr)
    print(f"Offline URL check: {len(errors)} violation(s)")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
