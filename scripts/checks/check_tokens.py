"""Reject presentation literals in new frontend code; keep legacy isolation explicit."""

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS_VALUE = r"(?:var\(--zy-[\w-]+\)|inherit|initial|unset|revert|none|currentColor|transparent)"


def scan_text(text: str) -> list[str]:
    findings = []
    rules = {
        "literal color": r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla)\s*\(",
        "literal font size": r"font-size\s*:\s*[^;\n}]*\d+(?:\.\d+)?(?:px|rem|em|%)|fontSize\s*:\s*(?:['\"])?\d",
        "literal font shorthand": r"\bfont\s*:\s*[^;\n}]*\d+(?:\.\d+)?(?:px|rem|em|%)",
        "foreign token": r"var\(--(?!zy-)[\w-]+",
    }
    for label, pattern in rules.items():
        for match in re.finditer(pattern, text):
            findings.append(f"{text.count(chr(10), 0, match.start()) + 1}: {label}: {match.group(0)}")
    for match in re.finditer(
        r"\b(border-(?:top-left-|top-right-|bottom-left-|bottom-right-|start-start-|start-end-|end-start-|end-end-)?radius"
        r"|box-shadow|text-shadow|border(?:(?:Top|Bottom)(?:Left|Right)|(?:Start|End)(?:Start|End))?Radius"
        r"|boxShadow|textShadow|color|backgroundColor|background-color|background)\s*:\s*([^;\n},]+)",
        text,
    ):
        value = match.group(2).strip().strip("'\"")
        remainder = re.sub(r"var\(--zy-[\w-]+\)", "", value).strip()
        if (
            remainder
            and not re.fullmatch(CSS_VALUE, remainder)
            and not remainder.startswith(("#", "rgb(", "rgba(", "hsl(", "hsla("))
        ):
            findings.append(f"{text.count(chr(10), 0, match.start()) + 1}: literal {match.group(1)}: {value}")
    for match in re.finditer(r"\bborder(?:-(?:left|right|top|bottom))?\s*:\s*([^;\n}]+)", text):
        value = re.sub(r"var\(--zy-[\w-]+\)", "", match.group(1))
        if re.search(r"\b(?:red|green|blue|white|black|yellow|orange|purple|pink|gray|grey)\b", value):
            findings.append("literal border color: " + match.group(1))
    declared = set(re.findall(r"(--zy-[\w-]+)\s*:", (ROOT / "frontend/src/design/tokens.css").read_text()))
    for token in re.findall(r"var\((--zy-[\w-]+)", text):
        if token not in declared:
            findings.append(f"unknown design token: {token}")
    return findings


def check_tree(root: Path) -> list[str]:
    findings = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".css", ".ts", ".tsx"}:
            continue
        relative = path.relative_to(root).as_posix()
        if relative.split("/")[0] in {"legacy", "design"}:
            continue
        findings.extend(f"{relative}:{item}" for item in scan_text(path.read_text(encoding="utf-8")))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=ROOT / "frontend/src")
    errors = check_tree(parser.parse_args().root)
    for error in errors:
        print(error)
    print(f"Design token violations: {len(errors)}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
