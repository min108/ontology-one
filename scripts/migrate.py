"""The single offline entry point for versioned deployment migrations."""

from __future__ import annotations

from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.infra.migrations import run_migrations
from backend.version import APP_VERSION


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provision-all", action="store_true", help="同时开通目录中尚未建立的机构工作区。")
    args = parser.parse_args(argv)
    result = run_migrations(provision_all=args.provision_all)
    print(f"{APP_VERSION}数据库迁移完成；已更新{len(result['migrated'])}个数据库。")
    return result


if __name__ == "__main__":
    main()
