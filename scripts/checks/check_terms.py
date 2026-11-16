"""Check the shared vocabulary and keep business copy out of implementation files."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "terms/zh-CN.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BRANDS = ("知元", "Ontology One", "集团本体管理平台")


def source_data() -> dict[str, Any]:
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def blocked(text: str, blacklist: list[str]) -> list[str]:
    return [
        word
        for word in blacklist
        if re.search(rf"\b{re.escape(word)}\b" if word.isascii() else re.escape(word), text, re.IGNORECASE)
    ]


def without_technical_identifiers(text: str) -> str:
    for identifier, reason in source_data()["technical_identifiers"].items():
        if reason.strip():
            text = text.replace(identifier, "")
    return text


def scan_text(text: str) -> list[str]:
    text = without_technical_identifiers(text)
    errors = [f"brand outside configuration: {name}" for name in BRANDS if name in text]
    clean = re.sub(r"/\*.*?\*/|^\s*//[^\n]*", "", text, flags=re.DOTALL | re.MULTILINE)
    strings = re.findall(r"(?:'([^'\n]*)'|\"([^\"\n]*)\"|>([^<>{}\n]+)<)", clean)
    blacklist = source_data()["blacklist"]
    for groups in strings:
        value = next((part for part in groups if part), "")
        visible = bool(groups[2]) or bool(re.search(r"[\u4e00-\u9fff]", value))
        if visible:
            errors.extend(f"internal term: {word}" for word in blocked(value, blacklist))
            if re.search(r"[\u4e00-\u9fff]", value):
                errors.append(f"copy outside vocabulary: {value[:50]}")
    for value in re.findall(r"`([^`]+)`", clean):
        if re.search(r"[\u4e00-\u9fff]", value):
            errors.append(f"copy outside vocabulary: {value[:50]}")
    for value in re.findall(r'(?:placeholder|aria-label|title|label)\s*=\s*[\'"]([^\'"]+)[\'"]', clean):
        errors.extend(f"internal label: {word}" for word in blocked(value, blacklist))
    visible_expressions = re.findall(r">\s*\{\s*([\"'`])(.+?)\1\s*\}\s*<", clean)
    visible_expressions += re.findall(r"(?:placeholder|aria-label|title|label)\s*=\s*\{\s*([\"'`])(.+?)\1\s*\}", clean)
    for _quote, value in visible_expressions:
        errors.extend(f"internal visible expression: {word}" for word in blocked(value, blacklist))
    return errors


def validate_source(source: dict[str, Any]) -> list[str]:
    errors = []
    exceptions = source.get("length_exceptions", {})
    for group, limit in (("actions", 8), ("labels", 40), ("help", 40)):
        for key, value in source[group].items():
            path = f"{group}.{key}"
            maximum = (
                12 if group == "labels" and (key.endswith("_title") or key in {"gallery", "system_info"}) else limit
            )
            if len(value) > maximum and not str(exceptions.get(path, "")).strip():
                errors.append(f"{path}: exceeds {maximum} characters without a reason")
            errors.extend(f"{path}: forbidden {word}" for word in blocked(value, source["blacklist"]))
    for state, messages in source["states"].items():
        for key in ("title", "description"):
            value = messages[key]
            if len(value) > (12 if key == "title" else 40):
                errors.append(f"states.{state}.{key}: too long")
            errors.extend(f"states.{state}.{key}: forbidden {word}" for word in blocked(value, source["blacklist"]))
    for domain, values in source["statuses"].items():
        for key, value in values.items():
            if not isinstance(value, str) or not value.strip():
                errors.append(f"statuses.{domain}.{key}: missing business label")
    for key, reason in source["technical_identifiers"].items():
        if not reason.strip():
            errors.append(f"technical_identifiers.{key}: missing exemption reason")
    return errors


def generated_contract_error(relative: str, content: str) -> list[str] | None:
    """Only byte-exact outputs of the reviewed API generator are derived copy."""
    if relative not in {"api/generated/openapi.json", "api/generated/types.ts"}:
        return None
    from scripts.gen_api_types import artifacts

    expected = artifacts()[Path(relative).name]
    return [] if content == expected else ["generated contract differs from its generator"]


def check_tree(root: Path) -> list[str]:
    errors = []
    for path in sorted(root.rglob("*")):
        if path.suffix not in {".ts", ".tsx", ".json"}:
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith("i18n/"):
            continue
        content = path.read_text(encoding="utf-8")
        generated = generated_contract_error(relative, content)
        if generated is not None:
            errors.extend(f"{relative}: {error}" for error in generated)
            continue
        if relative.startswith("legacy/"):
            found = [
                f"brand outside configuration: {name}"
                for name in BRANDS
                if name in without_technical_identifiers(content)
            ]
        else:
            found = scan_text(content)
        errors.extend(f"{relative}: {error}" for error in found)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=ROOT / "frontend/src")
    errors = validate_source(source_data()) + check_tree(parser.parse_args().root)
    for error in errors:
        print(error)
    print(f"Vocabulary violations: {len(errors)}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
