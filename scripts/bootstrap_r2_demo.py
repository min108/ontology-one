"""Container-only demo initialization; no model credentials or plaintext passwords saved."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import demo
from backend.security import store
from backend.security.context import use_context


def main() -> None:
    value = json.loads(sys.stdin.read(16384))
    if set(value) != {"password"} or not isinstance(value["password"], str):
        raise SystemExit("仅接受演示账号密码。")
    password = value["password"]
    store.bootstrap_superadmin("demo.admin", password, "演示管理员")
    token = store.login("demo.admin", password, "127.0.0.1", "r2-demo-setup")
    person = store.authenticate_session(token, "r2-demo-setup")[0]
    with use_context(person):
        for username, label, role in (
            ("demo.builder", "演示建设人员", "modeler"),
            ("demo.reviewer", "演示审核人员", "reviewer"),
        ):
            with store.control_connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT home_tenant_id,role FROM sec_users WHERE username=%s", (username,))
                existing = cur.fetchone()
            if existing:
                if existing["home_tenant_id"] != "hq" or existing["role"] != role:
                    raise SystemExit("同名账号身份不同，未覆盖；请在管理页面核对。")
                continue
            store.create_user(
                person,
                username=username,
                display_name=label,
                tenant_id="hq",
                role=role,
                password=password,
                must_change_password=False,
            )
        result = demo.load(demo.Confirm(confirm=True))
        print("0.7.0演示资料已装载；模拟材料、台账及问题可在管理 / 演示资料查看。")
        print("重复启动保留已有账号与资料。" if result.get("already_loaded") else "已创建本次演示资料。")


if __name__ == "__main__":
    main()
