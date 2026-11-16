"""Small, validated, per-user preferences; no caller-supplied ownership."""

import json

from fastapi import HTTPException

from backend import db
from backend.security import store
from backend.security.context import Principal

ENUMS = {
    "view_mode": {"simple", "pro"},
    "density": {"comfortable", "compact"},
    "autopilot_default": {"step", "standard", "fast"},
}


def validate_preference(key: str, value: object) -> None:
    if key in ENUMS:
        valid = isinstance(value, str) and value in ENUMS[key]
    elif key == "onboarding":
        valid = (
            isinstance(value, dict)
            and len(value) <= 50
            and all(isinstance(k, str) and 0 < len(k) <= 80 and isinstance(v, bool) for k, v in value.items())
        )
    elif key == "graph_views":
        valid = (
            isinstance(value, list)
            and len(value) <= 50
            and all(isinstance(v, dict) and set(v) <= {"id", "name", "perspective", "filters", "focus"} for v in value)
        )
    else:
        valid = False
    if not valid or len(json.dumps(value, ensure_ascii=False).encode()) > 16384:
        raise HTTPException(422, "偏好设置的名称或取值无效，请重新选择。")


def read_preferences(principal: Principal) -> dict:
    principal = store.assert_principal_current(principal)
    with store.control_connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pref_key,value FROM sec_user_prefs WHERE user_id=%s ORDER BY pref_key", (principal.user_id,)
        )
        return {row["pref_key"]: json.loads(row["value"]) for row in cur.fetchall()}


def write_preference(principal: Principal, key: str, value: object) -> dict:
    if principal.identity_kind != "session" or principal.readonly:
        raise HTTPException(403, "当前只读身份不能修改偏好。")
    validate_preference(key, value)
    with store.authorized_control_connection(principal, mode="standard") as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sec_user_prefs(user_id,pref_key,value,updated_at) VALUES(%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE value=VALUES(value),updated_at=VALUES(updated_at)",
            (principal.user_id, key, json.dumps(value, ensure_ascii=False), db.now()),
        )
        store._audit(cur, principal, "preference_update", key, "success", {"pref_key": key})
    return read_preferences(principal)
