"""Check the effective route table against independently reviewed policy entries."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def canonical(path: str) -> str:
    """Parameter names are immaterial; literal segments are never wildcards."""
    return re.sub(r"\{[^}]+\}", "{}", path)


def check_routes(app: object) -> list[str]:
    from fastapi.routing import iter_route_contexts

    from backend.security import policy

    registry = json.loads(Path(__file__).with_name("policy_control_routes.json").read_text())
    controls = {(entry["method"], entry["path"]) for entry in registry}
    reads = {canonical(path) for path in policy.READ_PATHS}
    writes = {(method, canonical(path)) for method, path, _roles in policy.WRITE_RULES}
    v2 = {(method, canonical(path)) for method, path, _permissions in policy.V2_RULES}
    errors = []
    for route in iter_route_contexts(app.routes):
        path = route.path or ""
        if not path.startswith("/api/"):
            continue
        if not route.methods:
            errors.append(f"{path}: unregistered API transport or mount")
            continue
        for method in sorted(route.methods or ()):
            key = (method, canonical(path))
            if path == "/api/v2" or path.startswith("/api/v2/"):
                registered = key in v2
            else:
                registered = key in controls
                if not policy.is_control_path(path):
                    registered |= (method in {"GET", "HEAD"} and canonical(path) in reads) or key in writes
            if not registered:
                errors.append(f"{method} {path}: no explicit policy registration")
    return errors


def default_deny_errors(authorizer: Callable[..., object] | None = None) -> list[str]:
    from backend.security import policy
    from backend.security.permissions import ROLES as V2_ROLES

    authorize = authorizer or policy.authorize
    errors = []
    for role in sorted(policy.ROLES | V2_ROLES):
        principal = SimpleNamespace(
            identity_kind="session",
            role=role,
            readonly=False,
            must_change_password=False,
            is_superadmin=True,
            home_tenant_id="hq",
            tenant_id="hq",
        )
        for method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
            for path in ("/api/__quality_gate_unregistered__", "/api/v2/__quality_gate_unregistered__"):
                if getattr(authorize(principal, method, path), "allowed", True):
                    errors.append(f"{role} {method} {path}: default deny violated")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    from backend.main import app

    errors = check_routes(app) + default_deny_errors()
    for error in errors:
        print(error, file=sys.stderr)
    print(f"Policy coverage: {len(errors)} violation(s)")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
