"""Download and verify official PyPI wheels; install candidate only in a private venv."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
PROBE_DEPENDENCIES = (
    "pydantic==2.14.3",
    "pydantic_core==2.50.4",
    "annotated-types==0.8.0",
    "typing_extensions==4.16.0",
    "typing-inspection==0.4.5",
)


def verify_digest(path: Path, expected: str) -> str:
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != expected:
        raise ValueError("Wheel checksum mismatch")
    return actual


def inspect_version(version: str, directory: Path) -> list[dict[str, Any]]:
    records = []
    for distribution in ("deepseek-harness-sdk", "deepseek-harness-runtime-bin"):
        with urllib.request.urlopen(f"https://pypi.org/pypi/{distribution}/{version}/json", timeout=30) as response:
            metadata = json.load(response)
        for item in metadata["urls"]:
            filename = item["filename"]
            if not any(tag in filename for tag in ("none-any.whl", "manylinux_2_28_x86_64.whl", "win_amd64.whl")):
                continue
            path = directory / filename
            if not path.exists():
                with urllib.request.urlopen(item["url"], timeout=60) as response, path.open("wb") as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
            digest = verify_digest(path, item["digests"]["sha256"])
            record = {"version": version, "file": filename, "sha256": digest, "pypi_digest_matches": True}
            with zipfile.ZipFile(path) as wheel:
                for member in wheel.namelist():
                    if member.endswith(
                        ("/deepseek-harness-sdk-runtime-linux-x64", "/deepseek-harness-sdk-runtime-win-x64.exe")
                    ):
                        with wheel.open(member) as stream:
                            record["executable_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
            records.append(record)
    if len(records) != 3:
        raise ValueError("Expected SDK plus Linux x64 and Windows x64 runtime wheels")
    lock = json.loads((ROOT / "ops" / "harness-runtime.lock.json").read_text())
    windows = json.loads((ROOT / "ops" / "windows-runtime.lock.json").read_text())
    if version == lock["sdk_version"]:
        worker = (ROOT / "backend" / "skill_harness_worker.py").read_text()
        for record in records:
            if "none-any" in record["file"]:
                expected = lock["sdk_wheel_sha256"]
            elif "manylinux" in record["file"]:
                expected = lock["runtime_wheel_sha256"]
                executable = re.search(r'^RUNTIME_SHA256 = "([a-f0-9]{64})"', worker, re.MULTILINE).group(1)
                if record["executable_sha256"] != executable:
                    raise ValueError("Pinned Linux executable digest mismatch")
            else:
                expected = record["sha256"]
                executable = re.search(r'^WINDOWS_RUNTIME_SHA256 = "([a-f0-9]{64})"', worker, re.MULTILINE).group(1)
                if record["executable_sha256"] != executable or executable not in json.dumps(windows):
                    raise ValueError("Pinned Windows executable digest mismatch")
            if record["sha256"] != expected:
                raise ValueError("Pinned wheel digest mismatch")
            record["repository_pin_matches"] = True
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.2.2rc2")
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    if not args.version.replace(".", "").isalnum():
        parser.error("Invalid version")
    directory = ROOT / "work" / "r1-10" / args.version
    directory.mkdir(parents=True, exist_ok=True)
    records = inspect_version(args.version, directory)
    if args.install:
        environment = directory / "venv"
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True, stdout=subprocess.DEVNULL)
        wheels = [str(directory / row["file"]) for row in records if "win_amd64" not in row["file"]]
        subprocess.run(
            [
                str(environment / "bin" / "python"),
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                *wheels,
                *PROBE_DEPENDENCIES,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
