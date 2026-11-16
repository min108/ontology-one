"""Restore a complete verified pre-upgrade backup while all services are stopped."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.infra.backup import restore_upgrade_backup
from backend.maintenance import runtime_guard


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path)
    parser.add_argument(
        "--maintenance",
        action="store_true",
        required=True,
        help="确认所有共享此部署的 API、智能体服务与 worker 已停止。",
    )
    args = parser.parse_args(argv)
    with runtime_guard(shared=False):
        result = restore_upgrade_backup(args.backup)
    print(f"升级前备份已恢复，共{result['databases']}个数据库。请使用对应旧版本启动。")


if __name__ == "__main__":
    main()
