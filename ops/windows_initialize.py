"""Installation-owned migration/bootstrap helper; credentials arrive on stdin."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


PROGRESS_PHASES = ('received', 'control', 'federation', 'queue', 'tenants',
                   'runtime_account', 'provisioned', 'bootstrap', 'completed')


class ProgressWriter:
    """Private installation metadata, never SQL, account names or secrets."""
    def __init__(self):
        import re
        self.run_id = os.environ.get('ONTOLOGY_INITIALIZATION_RUN_ID', '')
        if not re.fullmatch('[0-9a-f]{32}', self.run_id):
            raise ValueError('Missing initialization run identity')
        self.completed, self.total = 0, 0

    def __call__(self, phase, completed=None, total=None):
        from backend.platform_io import atomic_write
        if phase not in PROGRESS_PHASES:
            raise ValueError('Invalid initialization phase')
        if completed is not None:
            if type(completed) is not int or type(total) is not int or not 0 <= completed <= total <= 10000:
                raise ValueError('Invalid initialization progress')
            self.completed, self.total = completed, total
        value = {'run_id': self.run_id, 'phase': phase, 'completed': self.completed, 'total': self.total}
        atomic_write(Path(__file__).resolve().parents[1] / 'data' / 'local' / 'migration-progress.json',
                     json.dumps(value, separators=(',', ':')).encode('ascii'))


def main(progress=None):
    body = json.loads(sys.stdin.buffer.read(4096))
    if not isinstance(body, dict) or set(body) not in (set(), {'username', 'password'}):
        raise ValueError('Invalid bootstrap request')
    if progress is not None:
        progress('received')
    from ops.provision import main as provision
    provision(progress=progress) if progress is not None else provision()
    from backend.security.store import (bootstrap_superadmin, control_connection, normalize_username,
                                        validate_password, verify_password)
    try:
        if progress is not None:
            progress('bootstrap')
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
        if progress is not None:
            progress('completed')
    finally:
        body.clear()


if __name__ == '__main__':
    try:
        main(progress=ProgressWriter())
    except Exception:
        # SQL and dependency exceptions may embed credentials or local paths.
        print('LOCAL_INITIALIZATION_FAILED', file=sys.stderr)
        raise SystemExit(1)
