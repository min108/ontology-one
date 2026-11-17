"""Review and stage the R3 63-image matrix from one aggregate CI artifact."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from visual_baselines import BROWSER, sha256, states, validate_state


def inventory(source: Path, commit: str) -> dict[str, Path]:
    images = {stem: source / layout / state for stem, (layout, state) in states().items()}
    actual = {p.parent.resolve() for p in source.rglob("actual.png")}
    if actual != {p.resolve() for p in images.values()}:
        raise ValueError("The aggregate must contain exactly the canonical 63-image matrix")
    for stem, directory in images.items():
        if any(path.is_symlink() for path in (directory, *directory.parents)):
            raise ValueError("Symbolic visual paths are not accepted")
        validate_state(directory, stem, commit, "capture")
    return images


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("review", "stage"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    images = inventory(args.source, args.commit)
    if args.command == "review":
        if args.review.exists():
            raise ValueError("Never overwrite an existing review")
        args.review.write_text(
            json.dumps(
                {
                    "capture_commit": args.commit,
                    "browser": BROWSER,
                    "reviewer": "",
                    "images": [
                        {"name": stem, "sha256": sha256(directory / "actual.png"), "reviewed": False, "notes": ""}
                        for stem, directory in sorted(images.items())
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        return 0
    review = json.loads(args.review.read_text())
    if (
        review.get("capture_commit") != args.commit
        or review.get("browser") != BROWSER
        or not str(review.get("reviewer", "")).strip()
    ):
        raise ValueError("Review must identify the captured commit, browser and reviewer")
    rows = review.get("images", [])
    if len(rows) != 63 or {row.get("name") for row in rows} != set(images):
        raise ValueError("Review must cover every image exactly once")
    for row in rows:
        if (
            row.get("reviewed") is not True
            or not str(row.get("notes", "")).strip()
            or row.get("sha256") != sha256(images[row["name"]] / "actual.png")
        ):
            raise ValueError("Every captured image requires an explicit matching review")
    if args.output is None or args.output.exists():
        raise ValueError("Stage into a new directory before replacing the old baseline")
    args.output.mkdir(parents=True)
    for stem, directory in images.items():
        shutil.copyfile(directory / "actual.png", args.output / f"{stem}.png")
        shutil.copyfile(directory / "metrics.json", args.output / f"{stem}.json")
    shutil.copyfile(args.review, args.output / "review.json")
    print("Staged 63 individually reviewed canonical baselines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
