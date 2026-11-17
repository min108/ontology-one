"""Keep routed application pages inside the shared R3 layout boundary."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE_ROOT = r"(?:page|page-root|page-container|container|layout|home|gallery)"
FORBIDDEN = r"(?:padding(?:-(?:inline|block)(?:-start|-end)?|-left|-right|-top|-bottom)?|max-width)"


def scan_page(text: str) -> list[str]:
    errors = []
    if not re.search(r"<PageLayout\b", text):
        errors.append("routed page must use PageLayout")
    if not re.search(r"<PageHeader\b", text):
        errors.append("routed page must use PageHeader")
    if re.search(r"<PageLayout\b[^>]*\bstyle\s*=", text):
        errors.append("PageLayout sizing belongs to the platform component")
    return errors


def scan_css(text: str) -> list[str]:
    errors = []
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        selector, body = rule.groups()
        # Nested card/control padding is legitimate; page roots are not.
        if not re.search(rf"\.{PAGE_ROOT}(?![\w-])(?:\s*,|\s*$)", selector.strip()):
            continue
        if re.search(rf"\b{FORBIDDEN}\s*:", body) or re.search(r"\bmargin(?:-inline)?\s*:[^;]*\bauto\b", body):
            errors.append("feature page container must not set padding, maximum width, or centering")
    return errors


def check_tree(source: Path) -> list[str]:
    errors = []
    app_routes = source / "app/AppRoutes.tsx"
    pages = set()
    if app_routes.exists():
        for name in re.findall(r"import\(['\"](\.\./features/[^'\"]+)['\"]\)", app_routes.read_text()):
            pages.add((app_routes.parent / name).resolve().with_suffix(".tsx"))
    # A newly added conventional page is checked before it is wired to a route.
    # Login is intentionally outside the authenticated application shell.
    pages.update(
        p
        for p in (source / "features").rglob("*Page.tsx")
        if ".test." not in p.name and p != source / "features/auth/LoginPage.tsx"
    )
    pages.update(p for p in (source / "features/dev/gallery").glob("Gallery.tsx"))
    landing = source / "app/WorkspaceLanding.tsx"
    if landing.exists():
        pages.add(landing)
    for page in sorted(pages):
        if not page.exists():
            errors.append(f"{page}: routed page is missing")
        else:
            errors.extend(f"{page.relative_to(source)}: {error}" for error in scan_page(page.read_text()))
    for css in sorted((source / "features").rglob("*.css")):
        errors.extend(f"{css.relative_to(source)}: {error}" for error in scan_css(css.read_text()))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, nargs="?", default=ROOT / "frontend/src")
    errors = check_tree(parser.parse_args().root.resolve())
    for error in errors:
        print(error)
    print(f"Page layout violations: {len(errors)}")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
