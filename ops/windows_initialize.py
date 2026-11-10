"""Installation-owned migration/bootstrap helper; credentials arrive on stdin."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    body = json.loads(sys.stdin.buffer.read(4096))
    if not isinstance(body, dict) or set(body) not in (set(), {'username', 'password'}):
        raise ValueError('Invalid bootstrap request')
    from ops.provision import main as provision
    provision()
    from backend.security.store import (bootstrap_superadmin, control_connection, normalize_username,
                                        validate_password, verify_password)
    try:
        if body:
            username = normalize_username(body['username'])
            validate_password(body['password'])
            identity = bootstrap_superadmin(username, body['password'])
            # A crash may have committed the account before the parent wrote
            # initialized.json. Resume only with those same credentials; never
            # report success for a different password or reset an existing user.
            with control_connection() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT username,password_hash FROM sec_users WHERE id=%s AND is_active=1 "
                               "AND is_superadmin=1 AND home_tenant_id='hq' AND role='admin' FOR SHARE", (identity,))
                existing = cursor.fetchone()
                if (not existing or existing['username'] != username
                        or not verify_password(body['password'], existing['password_hash'])):
                    raise RuntimeError('Initial administrator credentials do not match')
        else:
            with control_connection() as connection, connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS n FROM sec_users WHERE is_active=1 AND is_superadmin=1 "
                               "AND home_tenant_id='hq' AND role='admin'")
                if not cursor.fetchone()['n']:
                    raise RuntimeError('Initial administrator unavailable')
    finally:
        body.clear()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # SQL and dependency exceptions may embed credentials or local paths.
        print('LOCAL_INITIALIZATION_FAILED', file=sys.stderr)
        raise SystemExit(1)
