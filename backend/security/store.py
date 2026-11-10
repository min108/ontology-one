"""MySQL identity control plane, separate from all tenant business databases.

Only token hashes are persisted. Authorization is revalidated against live account,
session and grant rows; directory identifiers and database names are server-owned.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
import queue
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import pymysql
from pymysql.cursors import DictCursor
from fastapi import HTTPException
from .context import Principal

ROLES = {'admin': '管理员', 'modeler': '本体建模师', 'reviewer': '审核官'}
ACCESS = {'read_published', 'read_private', 'write'}
COOKIE_NAME = 'ontology_session'
SESSION_TTL = 8 * 60 * 60
HASH_ROUNDS = 600_000


def _config():
    # mysql_config only reads deployment configuration; never call business connect.
    from ..db import mysql_config
    config = mysql_config().copy()
    base = config['database']
    config['database'] = base[:48] + '_control'
    config.update(charset='utf8mb4', cursorclass=DictCursor, autocommit=False,
                  connect_timeout=8, read_timeout=20, write_timeout=20)
    return config


_POOL_LOCK = threading.Lock()
_POOLS = {}


def _pool(config):
    # Configuration changes in tests or deployment never reuse another database's connection.
    key = tuple(str(config[k]) for k in ('host', 'port', 'user', 'database')) + (token_hash(config.get('password', '')),)
    with _POOL_LOCK:
        return _POOLS.setdefault(key, (queue.LifoQueue(maxsize=16), threading.BoundedSemaphore(16)))


@contextmanager
def control_connection():
    config = _config()
    idle, capacity = _pool(config)
    if not capacity.acquire(timeout=10):
        raise HTTPException(503, '身份服务繁忙，请稍后重试。')
    conn = None
    healthy = False
    try:
        try:
            conn = idle.get_nowait()
            conn.ping(reconnect=False)
        except (queue.Empty, pymysql.MySQLError):
            if conn is not None:
                conn.close()
            conn = pymysql.connect(**config)
        yield conn
        conn.commit()
        healthy = True
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
                healthy = bool(conn.open)
            except Exception:
                healthy = False
        raise
    finally:
        if conn is not None:
            if healthy and conn.open:
                try:
                    idle.put_nowait(conn)
                except queue.Full:
                    conn.close()
            else:
                conn.close()
        capacity.release()


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def csrf_token(raw_token: str) -> str:
    return hmac.new(raw_token.encode('utf-8'), b'ontology-one:csrf:v1', hashlib.sha256).hexdigest()


def validate_password(password: str):
    if not isinstance(password, str) or not 12 <= len(password) <= 128 or not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        raise HTTPException(422, '密码须为12至128个字符，且包含英文字母和数字。')


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), HASH_ROUNDS).hex()
    return f'pbkdf2_sha256${HASH_ROUNDS}${salt}${digest}'


def verify_password(password: str, stored: str) -> bool:
    try:
        method, rounds, salt, expected = stored.split('$')
        if method != 'pbkdf2_sha256' or not 100_000 <= int(rounds) <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError):
        return False


_DUMMY_HASH = 'pbkdf2_sha256$600000$9e2511d5884634cd8ed5302489516240$' + '0' * 64


def _utc(epoch):
    return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat() if epoch else None


def _directory():
    values = json.loads((Path(__file__).parent / 'organizations.json').read_text(encoding='utf-8'))
    result = [{'id': 'hq', 'name': values['headquarters'], 'category': 'headquarters', 'org_id': None}]
    for category, prefix in [('resident', 'resident'), ('headquarters_department', 'dept'), ('subsidiary', 'sub')]:
        for index, name in enumerate(values[category], 1):
            ident = f'{prefix}-{index:02d}'
            result.append({'id': ident, 'name': name, 'category': category, 'org_id': ident})
    assert len(result) == 51 and len({x['id'] for x in result}) == 51
    return result


DDL = [
    '''CREATE TABLE IF NOT EXISTS sec_tenants(
       id VARCHAR(64) PRIMARY KEY,name VARCHAR(255) NOT NULL,category VARCHAR(64) NOT NULL,
       org_id VARCHAR(100) NULL,db_name VARCHAR(64) UNIQUE NOT NULL,status VARCHAR(20) NOT NULL DEFAULT 'active',
       created_at DOUBLE NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
    '''CREATE TABLE IF NOT EXISTS sec_users(
       id VARCHAR(64) PRIMARY KEY,username VARCHAR(80) NOT NULL UNIQUE,display_name VARCHAR(120) NOT NULL,
       home_tenant_id VARCHAR(64) NOT NULL,role VARCHAR(20) NOT NULL,password_hash VARCHAR(255) NOT NULL,
       is_active TINYINT NOT NULL DEFAULT 1,is_superadmin TINYINT NOT NULL DEFAULT 0,
       must_change_password TINYINT NOT NULL DEFAULT 1,auth_version INT NOT NULL DEFAULT 1,
       created_at DOUBLE NOT NULL,updated_at DOUBLE NOT NULL,INDEX idx_user_tenant(home_tenant_id),
       FOREIGN KEY(home_tenant_id) REFERENCES sec_tenants(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
    '''CREATE TABLE IF NOT EXISTS sec_sessions(
       id VARCHAR(64) PRIMARY KEY,token_hash CHAR(64) NOT NULL UNIQUE,csrf_hash CHAR(64) NOT NULL,
       user_id VARCHAR(64) NOT NULL,tenant_id VARCHAR(64) NOT NULL,auth_version INT NOT NULL,
       created_at DOUBLE NOT NULL,last_seen_at DOUBLE NOT NULL,expires_at DOUBLE NOT NULL,
       revoked_at DOUBLE NULL,user_agent VARCHAR(255) NOT NULL,ip_hint VARCHAR(100) NOT NULL,
       INDEX idx_session_user(user_id),FOREIGN KEY(user_id) REFERENCES sec_users(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
    '''CREATE TABLE IF NOT EXISTS sec_grants(
       id VARCHAR(64) PRIMARY KEY,user_id VARCHAR(64) NOT NULL,tenant_id VARCHAR(64) NOT NULL,
       access VARCHAR(32) NOT NULL,reason VARCHAR(500) NOT NULL,created_by VARCHAR(64) NOT NULL,
       created_at DOUBLE NOT NULL,UNIQUE KEY uq_grant(user_id,tenant_id),
       FOREIGN KEY(user_id) REFERENCES sec_users(id),FOREIGN KEY(tenant_id) REFERENCES sec_tenants(id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
    '''CREATE TABLE IF NOT EXISTS sec_login_limits(
       bucket CHAR(64) PRIMARY KEY,failures INT NOT NULL,window_start DOUBLE NOT NULL,
       locked_until DOUBLE NOT NULL DEFAULT 0) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
    '''CREATE TABLE IF NOT EXISTS sec_audit(
       id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,created_at DOUBLE NOT NULL,
       actor_id VARCHAR(64) NULL,actor_username VARCHAR(80) NULL,tenant_id VARCHAR(64) NULL,
       action VARCHAR(100) NOT NULL,resource VARCHAR(500) NOT NULL,outcome VARCHAR(32) NOT NULL,
       request_id VARCHAR(100) NOT NULL,details LONGTEXT NOT NULL,
       INDEX idx_audit_tenant(tenant_id,id),INDEX idx_audit_actor(actor_id,id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''',
]


def _initialize_control_unlocked():
    config = _config()
    database = config.pop('database')
    with pymysql.connect(**config) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
    with control_connection() as conn:
        with conn.cursor() as cur:
            for statement in DDL:
                cur.execute(statement)
            # Immutable audit records: not only the HTTP API but SQL UPDATE/DELETE
            # is rejected. Database administrators still own database recovery.
            cur.execute('SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=%s', (database,))
            existing = {r['TRIGGER_NAME'] for r in cur.fetchall()}
            for operation in ('UPDATE', 'DELETE'):
                name = 'sec_audit_no_' + operation.lower()
                if name not in existing:
                    try:
                        cur.execute(f"CREATE TRIGGER `{name}` BEFORE {operation} ON sec_audit FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Security audit is append-only'")
                    except pymysql.MySQLError as exc:
                        if exc.args and exc.args[0] in (1142, 1227, 1419):
                            raise RuntimeError('控制库审计约束尚未安装，请先由数据库迁移账号运行身份库初始化，再使用受限运行账号启动；应用不会扩大数据库权限。') from exc
                        raise
            cur.execute("UPDATE sec_users SET is_superadmin=CASE WHEN home_tenant_id='hq' AND role='admin' THEN 1 ELSE 0 END")
            for tenant in _directory():
                # Fixed-length derived names prevent user-controlled database selection.
                digest = hashlib.sha256((database + ':' + tenant['id']).encode()).hexdigest()[:24]
                db_name = database.removesuffix('_control')[:32] + '_t_' + digest
                cur.execute('INSERT INTO sec_tenants(id,name,category,org_id,db_name,status,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE name=VALUES(name),category=VALUES(category),org_id=VALUES(org_id)',
                            (tenant['id'], tenant['name'], tenant['category'], tenant['org_id'], db_name, 'active', time.time()))



def initialize_control():
    """Serialize migrations across API workers without granting runtime SUPER."""
    config = _config()
    database = config.pop('database')
    lock_name = 'ontology-control-' + token_hash(database)[:32]
    connection = pymysql.connect(**config)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT GET_LOCK(%s,60) AS acquired', (lock_name,))
            if cursor.fetchone()['acquired'] != 1:
                raise RuntimeError('身份库初始化繁忙，请稍后重试。')
        try:
            _initialize_control_unlocked()
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT RELEASE_LOCK(%s)', (lock_name,))
    finally:
        connection.close()

def _audit(cur, principal, action, resource, outcome, details=None, request_id=''):
    safe_details = dict(details or {})
    if any(re.search(r'password|token|secret|cookie|authorization', str(key), re.I) for key in safe_details):
        raise ValueError('Secret-bearing audit field is forbidden')
    cur.execute('INSERT INTO sec_audit(created_at,actor_id,actor_username,tenant_id,action,resource,outcome,request_id,details) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (time.time(), principal.user_id if principal else None, principal.username if principal else None,
                 principal.tenant_id if principal else None, action[:100], str(resource)[:500], outcome[:32],
                 principal.request_id if principal else request_id, json.dumps(safe_details, ensure_ascii=False, default=str)))


def append_audit(principal, action, resource, outcome, details=None):
    with control_connection() as conn, conn.cursor() as cur:
        _audit(cur, principal, action, resource, outcome, details)


def list_tenants():
    with control_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM sec_tenants ORDER BY CASE category WHEN \'headquarters\' THEN 0 WHEN \'resident\' THEN 1 WHEN \'headquarters_department\' THEN 2 ELSE 3 END,id')
        return list(cur.fetchall())


def get_tenant(tenant_id):
    with control_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM sec_tenants WHERE id=%s', (tenant_id,))
        return cur.fetchone()


def _grant(cur, user_id, tenant_id):
    cur.execute('SELECT * FROM sec_grants WHERE user_id=%s AND tenant_id=%s', (user_id, tenant_id))
    return cur.fetchone()


def _principal(cur, user, session, request_id):
    if not user or not user['is_active'] or session['revoked_at'] is not None or session['expires_at'] <= time.time() or user['auth_version'] != session['auth_version']:
        return None
    cur.execute('SELECT * FROM sec_tenants WHERE id IN (%s,%s)', (user['home_tenant_id'], session['tenant_id']))
    tenants = {r['id']: r for r in cur.fetchall()}
    home, target = tenants.get(user['home_tenant_id']), tenants.get(session['tenant_id'])
    if not home or not target or home['status'] != 'active' or target['status'] != 'active':
        return None
    superadmin = bool(user['role'] == 'admin' and user['home_tenant_id'] == 'hq')
    if target['id'] != home['id'] and not superadmin:
        return None
    return Principal(user_id=user['id'], username=user['username'], home_tenant_id=home['id'], tenant_id=target['id'],
                     role=user['role'], is_superadmin=superadmin, session_id=session['id'], auth_version=user['auth_version'],
                     database_name=target['db_name'], request_id=request_id, must_change_password=bool(user['must_change_password']))



def _session_row(cursor, *, raw=None, principal=None, captured_tenant=None):
    target_join = 't.id=s.tenant_id' if captured_tenant is None else 't.id=%s'
    params = [] if captured_tenant is None else [captured_tenant]
    if raw is not None:
        where = 's.token_hash=%s'
        params.append(token_hash(raw))
    else:
        where = 's.id=%s AND s.user_id=%s'
        params.extend([principal.session_id, principal.user_id])
    cursor.execute("""SELECT s.id AS session_id,s.user_id,s.auth_version AS session_version,
        s.expires_at,s.revoked_at,s.csrf_hash,s.last_seen_at,u.username,u.home_tenant_id,
        u.role,u.auth_version,u.is_active,u.must_change_password,h.status AS home_status,
        t.id AS effective_tenant_id,t.status AS target_status,t.db_name
        FROM sec_sessions s JOIN sec_users u ON u.id=s.user_id
        JOIN sec_tenants h ON h.id=u.home_tenant_id JOIN sec_tenants t ON """ + target_join + ' WHERE ' + where, params)
    return cursor.fetchone()


def _joined_principal(row, request_id):
    if not row or not row['is_active'] or row['revoked_at'] is not None or row['expires_at'] <= time.time() or row['auth_version'] != row['session_version'] or row['home_status'] != 'active' or row['target_status'] != 'active' or row['role'] not in ROLES:
        return None
    superadmin = row['home_tenant_id'] == 'hq' and row['role'] == 'admin'
    if row['effective_tenant_id'] != row['home_tenant_id'] and not superadmin:
        return None
    return Principal(user_id=row['user_id'],username=row['username'],home_tenant_id=row['home_tenant_id'],
                     tenant_id=row['effective_tenant_id'],role=row['role'],is_superadmin=superadmin,
                     session_id=row['session_id'],auth_version=row['auth_version'],database_name=row['db_name'],
                     request_id=request_id,must_change_password=bool(row['must_change_password']))


def _same_identity(current, principal):
    return current and all(getattr(current, field) == getattr(principal, field) for field in
                           ('auth_version','role','home_tenant_id','tenant_id','database_name','is_superadmin'))

def authenticate_session(raw_cookie, request_id):
    if not isinstance(raw_cookie, str) or not 30 <= len(raw_cookie) <= 150:
        return None
    with control_connection() as conn, conn.cursor() as cur:
        row = _session_row(cur, raw=raw_cookie)
        principal = _joined_principal(row, request_id)
        if not principal:
            return None
        if row['last_seen_at'] < time.time() - 60:
            cur.execute('UPDATE sec_sessions SET last_seen_at=%s WHERE id=%s AND last_seen_at<%s', (time.time(), row['session_id'], time.time()-60))
        return principal, row['csrf_hash']

def assert_principal_current(principal, require_write=False):
    if principal is None:
        raise HTTPException(401, '登录状态无效。')
    with control_connection() as conn, conn.cursor() as cur:
        current = _joined_principal(_session_row(cur, principal=principal), principal.request_id)
        if not _same_identity(current, principal):
            raise HTTPException(401, '会话、权限或工作区已变更，请刷新后重试。')
        if require_write and (principal.readonly or current.readonly or current.must_change_password):
            raise HTTPException(403, '当前会话不允许写入，请先完成密码更新或授权。')
        return current

def assert_job_principal_current(principal, require_write=True):
    """Only a queue-validated lease may use the captured tenant instead of UI scope."""
    if principal is None:
        raise HTTPException(401, '任务身份无效。')
    with control_connection() as conn, conn.cursor() as cur:
        current = _joined_principal(_session_row(cur, principal=principal, captured_tenant=principal.tenant_id), principal.request_id)
        if not _same_identity(current, principal):
            raise HTTPException(401, '任务所属账号、机构或授权已变化。')
        if require_write and (principal.readonly or current.readonly or current.must_change_password):
            raise HTTPException(403, '任务当前不具备写入授权。')
        return current


@contextmanager
def authorized_control_connection(principal, mode='standard'):
    """Lock the actor and revalidate immediately before committing a control write.

    Revalidation uses this cursor, never another pooled transaction. Own password,
    logout and workspace changes have narrow explicit postconditions; a concurrent
    account disable/role change waits on the actor row and cannot race the write.
    """
    with control_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            cursor.execute('SELECT * FROM sec_users WHERE id=%s FOR UPDATE', (principal.user_id,))
            original_user = cursor.fetchone()
            cursor.execute('SELECT * FROM sec_sessions WHERE id=%s AND user_id=%s FOR UPDATE', (principal.session_id, principal.user_id))
            original_session = cursor.fetchone()
            if not original_user or not original_session:
                raise HTTPException(401, '操作身份已失效。')
            initial = _principal(cursor, original_user, original_session, principal.request_id)
            if not initial or any(getattr(initial, field) != getattr(principal, field) for field in ('auth_version','role','home_tenant_id','tenant_id','database_name','is_superadmin')):
                raise HTTPException(401, '操作身份或工作区已变化。')
            if mode not in ('credential', 'revoke') and initial.must_change_password:
                raise HTTPException(403, '请先修改初始密码。')
        yield connection
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM sec_users WHERE id=%s', (principal.user_id,))
            user = cursor.fetchone()
            cursor.execute('SELECT * FROM sec_sessions WHERE id=%s AND user_id=%s', (principal.session_id, principal.user_id))
            session = cursor.fetchone()
            if not user or not session:
                raise HTTPException(401, '提交时身份已失效。')
            expected_versions = {original_user['auth_version']} | ({original_user['auth_version'] + 1} if mode == 'credential' else set())
            if user['auth_version'] not in expected_versions or any(user[field] != original_user[field] for field in ('home_tenant_id','role','is_active','is_superadmin')):
                raise HTTPException(401, '提交时账号权限已变化。')
            checked_user = dict(user)
            checked_session = dict(session)
            if mode == 'credential':
                checked_user['auth_version'] = original_user['auth_version']
                checked_session['revoked_at'] = original_session['revoked_at']
            elif mode == 'revoke':
                checked_session['revoked_at'] = original_session['revoked_at']
            if mode == 'workspace':
                # Validate the target grant and database with the new workspace.
                expected_tenant = session['tenant_id']
            else:
                expected_tenant = principal.tenant_id
            current = _principal(cursor, checked_user, checked_session, principal.request_id)
            if not current or current.tenant_id != expected_tenant or current.role != principal.role or current.is_superadmin != principal.is_superadmin:
                raise HTTPException(401, '提交时会话或机构授权已变化。')

def _authorized_rows(cur, principal, lock=False):
    cur.execute('SELECT t.*,g.access FROM sec_tenants t LEFT JOIN sec_grants g ON g.tenant_id=t.id AND g.user_id=%s WHERE t.status=\'active\' AND (t.id=%s OR g.id IS NOT NULL) ORDER BY t.id' + (' FOR SHARE' if lock else ''), (principal.user_id, principal.home_tenant_id))
    rows = []
    for row in cur.fetchall():
        row['visibility'] = 'private' if row['id'] == principal.home_tenant_id or row['access'] in ('read_private', 'write') else 'published'
        rows.append(row)
    return rows


def _live_scope_query(cursor, principal, tenant_id=None):
    # One live JOIN validates the session and selects only explicitly authorized
    # target directories; it never caches account state across requests.
    sql = """SELECT t.*,g.access,
        CASE WHEN t.id=u.home_tenant_id OR g.access IN ('read_private','write') THEN 'private' ELSE 'published' END AS visibility
        FROM sec_sessions s JOIN sec_users u ON u.id=s.user_id
        JOIN sec_tenants h ON h.id=u.home_tenant_id
        JOIN sec_tenants active ON active.id=s.tenant_id
        JOIN sec_tenants t ON t.status='active'
        LEFT JOIN sec_grants g ON g.tenant_id=t.id AND g.user_id=u.id
        WHERE s.id=%s AND s.user_id=%s AND s.revoked_at IS NULL AND s.expires_at>%s
          AND s.auth_version=u.auth_version AND u.auth_version=%s AND u.is_active=1
          AND u.role=%s AND u.home_tenant_id=%s AND s.tenant_id=%s AND active.db_name=%s
          AND h.status='active' AND active.status='active'
          AND (s.tenant_id=u.home_tenant_id OR (u.home_tenant_id='hq' AND u.role='admin'))
          AND (t.id=u.home_tenant_id OR g.id IS NOT NULL)"""
    args=[principal.session_id,principal.user_id,time.time(),principal.auth_version,principal.role,
          principal.home_tenant_id,principal.tenant_id,principal.database_name]
    if tenant_id is not None:
        sql += ' AND t.id=%s'
        args.append(tenant_id)
    cursor.execute(sql + ' ORDER BY t.id', args)
    return list(cursor.fetchall())


def authorized_read_tenants(principal):
    with control_connection() as conn, conn.cursor() as cur:
        rows = _live_scope_query(cur, principal)
        if not rows:
            raise HTTPException(401, '会话或机构权限已变化。')
        return rows


def authorized_read_tenants_in_transaction(principal, connection):
    """Fresh scope for a guarded federation write, sharing its actor transaction."""
    with connection.cursor() as cursor:
        cursor.execute('SELECT * FROM sec_users WHERE id=%s', (principal.user_id,))
        user = cursor.fetchone()
        cursor.execute('SELECT * FROM sec_sessions WHERE id=%s AND user_id=%s', (principal.session_id, principal.user_id))
        session = cursor.fetchone()
        current = _principal(cursor, user, session, principal.request_id) if user and session else None
        if not current or any(getattr(current, field) != getattr(principal, field) for field in ('auth_version','role','tenant_id','is_superadmin')):
            raise HTTPException(401, '联邦写入身份或授权已变化。')
        return _authorized_rows(cursor, current, lock=True)

def can_read_tenant(principal, target):
    tenant_id = target['id'] if isinstance(target, dict) else target
    with control_connection() as conn, conn.cursor() as cur:
        rows = _live_scope_query(cur, principal, tenant_id)
        return rows[0] if rows else None


get_grant_info = can_read_tenant


def _default_grants(cur, user_id, home_id, actor_id):
    cur.execute('SELECT * FROM sec_tenants WHERE status=\'active\' ORDER BY id')
    tenants = list(cur.fetchall())
    home = next(t for t in tenants if t['id'] == home_id)
    for tenant in tenants:
        if tenant['id'] == home_id:
            continue
        access = 'read_private' if home_id == 'hq' else 'read_published' if home['category'] == 'headquarters_department' and tenant['category'] in ('headquarters', 'headquarters_department') else None
        if access:
            cur.execute('INSERT INTO sec_grants VALUES(%s,%s,%s,%s,%s,%s,%s)', (uuid.uuid4().hex, user_id, tenant['id'], access, '总部汇总账户默认只读授权' if home_id == 'hq' else '总部公开成果目录访问', actor_id, time.time()))


def bootstrap_superadmin(username=None, password=None, display_name='平台管理员'):
    username = username or os.getenv('ONTOLOGY_BOOTSTRAP_USERNAME')
    password = password or os.getenv('ONTOLOGY_BOOTSTRAP_PASSWORD')
    if not username or not password:
        return None
    username = normalize_username(username)
    with control_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT id FROM sec_users WHERE is_superadmin=1 LIMIT 1')
        existing = cur.fetchone()
        if existing:
            return existing['id']
        uid = uuid.uuid4().hex
        cur.execute('INSERT INTO sec_users(id,username,display_name,home_tenant_id,role,password_hash,is_active,is_superadmin,must_change_password,auth_version,created_at,updated_at) VALUES(%s,%s,%s,\'hq\',\'admin\',%s,1,1,1,1,%s,%s)', (uid, username, display_name, hash_password(password), time.time(), time.time()))
        _default_grants(cur, uid, 'hq', 'bootstrap')
        _audit(cur, None, 'bootstrap_superadmin', uid, 'success', {'username': username, 'tenant_id': 'hq', 'grants': 'explicit_read_private'})
        return uid


def normalize_username(value):
    value = str(value).strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_.@-]{2,79}', value):
        raise HTTPException(422, '用户名须为3至80位英文字母、数字及_.@-。')
    return value


def _ip_hint(ip):
    if '.' in ip:
        return '.'.join(ip.split('.')[:3]) + '.*'
    return ip[:16] + '…'


def _new_session(cur, user, user_agent, ip, tenant_id=None):
    raw = secrets.token_urlsafe(32)
    ident = uuid.uuid4().hex
    now = time.time()
    cur.execute('INSERT INTO sec_sessions(id,token_hash,csrf_hash,user_id,tenant_id,auth_version,created_at,last_seen_at,expires_at,revoked_at,user_agent,ip_hint) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,%s,%s)',
                (ident, token_hash(raw), token_hash(csrf_token(raw)), user['id'], tenant_id or user['home_tenant_id'], user['auth_version'], now, now, now + SESSION_TTL, str(user_agent)[:255], _ip_hint(ip)))
    return raw


def login(username, password, ip, user_agent, request_id=''):
    # Account and source rate buckets persist across app workers and restarts.
    username = str(username).strip().lower()[:80]
    buckets = [(token_hash('account:' + username), 5), (token_hash('address:' + ip), 30)]
    denied = False
    with control_connection() as conn, conn.cursor() as cur:
        for bucket, limit in sorted(buckets):
            cur.execute('INSERT IGNORE INTO sec_login_limits VALUES(%s,0,%s,0)', (bucket, time.time()))
            cur.execute('SELECT * FROM sec_login_limits WHERE bucket=%s FOR UPDATE', (bucket,))
            row = cur.fetchone()
            if row['locked_until'] > time.time():
                denied = True
        if denied:
            _audit(cur, None, 'login', 'account', 'rate_limited', {'account_hint': token_hash(username)[:12]}, request_id=request_id)
        else:
            cur.execute('SELECT * FROM sec_users WHERE username=%s', (username,))
            user = cur.fetchone()
            valid = verify_password(str(password)[:129], user['password_hash'] if user else _DUMMY_HASH)
            if user:
                cur.execute('SELECT status FROM sec_tenants WHERE id=%s', (user['home_tenant_id'],))
                tenant = cur.fetchone()
            else:
                tenant = None
            if not valid or not user or not user['is_active'] or not tenant or tenant['status'] != 'active':
                for bucket, limit in buckets:
                    cur.execute('SELECT * FROM sec_login_limits WHERE bucket=%s', (bucket,))
                    row = cur.fetchone()
                    count = row['failures'] + 1 if row['window_start'] > time.time() - 600 else 1
                    window = row['window_start'] if count > 1 else time.time()
                    cur.execute('UPDATE sec_login_limits SET failures=%s,window_start=%s,locked_until=%s WHERE bucket=%s', (count, window, time.time() + 600 if count >= limit else 0, bucket))
                _audit(cur, None, 'login', 'account', 'denied', {'account_hint': token_hash(username)[:12]}, request_id=request_id)
                raw = None
            else:
                cur.execute('DELETE FROM sec_login_limits WHERE bucket=%s', (buckets[0][0],))
                raw = _new_session(cur, user, user_agent, ip)
                actor = Principal(user['id'], user['username'], user['home_tenant_id'], user['home_tenant_id'], user['role'], bool(user['is_superadmin']), '', user['auth_version'], '', request_id=request_id)
                _audit(cur, actor, 'login', user['id'], 'success')
    if denied:
        raise HTTPException(429, '登录尝试过多，请10分钟后重试。', headers={'Retry-After': '600'})
    if not raw:
        raise HTTPException(401, '用户名或密码不正确，或账号不可用。')
    return raw


def _user_public(row):
    result = {key: row[key] for key in ('id', 'username', 'display_name', 'home_tenant_id', 'role', 'is_active', 'is_superadmin', 'must_change_password', 'auth_version')}
    for key in ('is_active', 'is_superadmin', 'must_change_password'):
        result[key] = bool(result[key])
    return result


def session_payload(principal, raw_token):
    principal = assert_principal_current(principal)
    with control_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM sec_users WHERE id=%s', (principal.user_id,))
        user = cur.fetchone()
        if principal.is_superadmin:
            cur.execute('SELECT * FROM sec_tenants WHERE status=\'active\' ORDER BY id')
        else:
            cur.execute('SELECT * FROM sec_tenants WHERE status=\'active\' AND id=%s', (principal.home_tenant_id,))
        workspaces = [{'tenant_id': t['id'], 'name': t['name'], 'category': t['category'], 'readonly': False, 'visibility': 'private'} for t in cur.fetchall()]
        current = next((t for t in workspaces if t['tenant_id'] == principal.tenant_id), None)
        if not current:
            raise HTTPException(403, '工作区授权已撤销。')
        return {'authenticated': True, 'user': _user_public(user), 'workspace': current, 'workspaces': workspaces,
                'roles': [{'id': key, 'name': label} for key, label in ROLES.items()],
                'permissions': {'manage_users': principal.role == 'admin', 'manage_grants': principal.is_superadmin,
                                'can_edit': principal.role in ('admin', 'modeler'), 'can_review': principal.role in ('admin', 'reviewer')},
                'csrf_token': csrf_token(raw_token), 'must_change_password': bool(user['must_change_password'])}


def switch_workspace(principal, tenant_id):
    principal = assert_principal_current(principal, require_write=True)
    with authorized_control_connection(principal, mode='workspace') as conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM sec_tenants WHERE id=%s AND status=\'active\'', (tenant_id,))
        target = cur.fetchone()
        if not target:
            raise HTTPException(404, '机构工作区不存在或已停用。')
        if tenant_id != principal.home_tenant_id and not principal.is_superadmin:
            raise HTTPException(403, '该机构仅可通过已授权的成果目录访问，不能进入业务工作区。')
        cur.execute('UPDATE sec_sessions SET tenant_id=%s WHERE id=%s AND revoked_at IS NULL', (tenant_id, principal.session_id))
        _audit(cur, principal, 'workspace_switch', tenant_id, 'success')


def revoke_session(principal, session_id):
    assert_principal_current(principal)
    with authorized_control_connection(principal, mode='revoke' if session_id == principal.session_id else 'standard') as conn, conn.cursor() as cur:
        cur.execute('SELECT user_id FROM sec_sessions WHERE id=%s', (session_id,))
        row = cur.fetchone()
        if not row or row['user_id'] != principal.user_id:
            raise HTTPException(404, '未找到当前账号的会话。')
        cur.execute('UPDATE sec_sessions SET revoked_at=%s WHERE id=%s AND revoked_at IS NULL', (time.time(), session_id))
        _audit(cur, principal, 'session_revoke', session_id, 'success')


def sessions(principal):
    assert_principal_current(principal)
    with control_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT id,tenant_id,created_at,last_seen_at,expires_at,user_agent,ip_hint FROM sec_sessions WHERE user_id=%s AND revoked_at IS NULL AND expires_at>%s ORDER BY created_at DESC', (principal.user_id, time.time()))
        return [{**row, 'current': row['id'] == principal.session_id, **{key: _utc(row[key]) for key in ('created_at', 'last_seen_at', 'expires_at')}} for row in cur.fetchall()]


def change_password(principal, current_password, new_password, ip, user_agent):
    validate_password(new_password)
    assert_principal_current(principal)
    with authorized_control_connection(principal, mode='credential') as conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM sec_users WHERE id=%s FOR UPDATE', (principal.user_id,))
        user = cur.fetchone()
        if not verify_password(current_password, user['password_hash']):
            raise HTTPException(400, '当前密码不正确。')
        if hmac.compare_digest(current_password, new_password):
            raise HTTPException(422, '新密码须与当前密码不同。')
        cur.execute('UPDATE sec_users SET password_hash=%s,must_change_password=0,auth_version=auth_version+1,updated_at=%s WHERE id=%s', (hash_password(new_password), time.time(), user['id']))
        cur.execute('UPDATE sec_sessions SET revoked_at=%s WHERE user_id=%s AND revoked_at IS NULL', (time.time(), user['id']))
        user['auth_version'] += 1
        raw = _new_session(cur, user, user_agent, ip, principal.tenant_id)
        _audit(cur, principal, 'password_change', user['id'], 'success', {'other_sessions_revoked': True})
        return raw


def require_admin(principal, superadmin=False):
    principal = assert_principal_current(principal, require_write=True)
    if principal.role != 'admin' or (superadmin and not principal.is_superadmin):
        raise HTTPException(403, '此操作需要总部平台管理员权限。' if superadmin else '此操作需要机构管理员权限。')
    return principal


def create_user(principal, *, username, display_name, tenant_id, role, password, must_change_password=True):
    principal = require_admin(principal)
    if not principal.is_superadmin and tenant_id != principal.home_tenant_id:
        raise HTTPException(403, '机构管理员只能开通本机构账号。')
    if role not in ROLES:
        raise HTTPException(422, '请选择有效角色。')
    is_superadmin = tenant_id == 'hq' and role == 'admin'
    username = normalize_username(username)
    password_hash = hash_password(password)
    with authorized_control_connection(principal, mode='standard') as conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM sec_tenants WHERE id=%s AND status=\'active\'', (tenant_id,))
        if not cur.fetchone():
            raise HTTPException(422, '目标机构不存在或已停用。')
        cur.execute('SELECT id FROM sec_users WHERE username=%s', (username,))
        if cur.fetchone():
            raise HTTPException(409, '用户名已存在。')
        uid = uuid.uuid4().hex
        cur.execute('INSERT INTO sec_users(id,username,display_name,home_tenant_id,role,password_hash,is_active,is_superadmin,must_change_password,auth_version,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s,1,%s,%s,1,%s,%s)', (uid, username, display_name.strip()[:120] or username, tenant_id, role, password_hash, int(is_superadmin), int(must_change_password), time.time(), time.time()))
        _default_grants(cur, uid, tenant_id, principal.user_id)
        _audit(cur, principal, 'user_create', uid, 'success', {'username': username, 'home_tenant_id': tenant_id, 'role': role, 'is_superadmin': bool(is_superadmin), 'default_grants_recorded': True})
        return uid


def list_users(principal):
    principal = require_admin(principal)
    with control_connection() as conn, conn.cursor() as cur:
        where, args = ('', ()) if principal.is_superadmin else (' WHERE u.home_tenant_id=%s', (principal.home_tenant_id,))
        cur.execute('SELECT u.*,t.name AS tenant_name FROM sec_users u JOIN sec_tenants t ON t.id=u.home_tenant_id' + where + ' ORDER BY u.created_at,u.id', args)
        return [{**_user_public(row), 'tenant_name': row['tenant_name']} for row in cur.fetchall()]


def update_user(principal, user_id, changes):
    principal = require_admin(principal)
    allowed = {'display_name', 'role', 'is_active', 'password', 'reason'}
    if not changes or set(changes) - allowed:
        raise HTTPException(422, '账号更新字段不受支持。')
    if 'role' in changes and changes['role'] not in ROLES:
        raise HTTPException(422, '请选择有效角色。')
    if 'password' in changes:
        validate_password(changes['password'])
    with authorized_control_connection(principal, mode='credential' if user_id == principal.user_id else 'standard') as conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM sec_users WHERE id=%s FOR UPDATE', (user_id,))
        user = cur.fetchone()
        if not user or (not principal.is_superadmin and user['home_tenant_id'] != principal.home_tenant_id):
            raise HTTPException(404, '账号不存在或不可管理。')
        if user_id == principal.user_id and (changes.get('is_active', True) is False or changes.get('role', principal.role) != principal.role):
            raise HTTPException(409, '不能停用自己或更改自己的角色，请由其他管理员处理。')
        if user['is_superadmin']:
            if not principal.is_superadmin:
                raise HTTPException(403, '不可修改平台管理员。')
            if user_id != principal.user_id and not str(changes.get('reason', '')).strip():
                raise HTTPException(422, '修改其他平台管理员须填写管理原因。')
            if changes.get('role', 'admin') != 'admin' or changes.get('is_active', True) is False:
                cur.execute('SELECT COUNT(*) AS n FROM sec_users WHERE is_superadmin=1 AND is_active=1 AND id<>%s', (user_id,))
                if cur.fetchone()['n'] == 0:
                    raise HTTPException(409, '不能停用或降级最后一个平台管理员。')
        values, clauses = [], []
        for field, value in changes.items():
            if field == 'reason':
                continue
            if field == 'password':
                clauses.extend(['password_hash=%s', 'must_change_password=1']); values.append(hash_password(value))
            else:
                clauses.append(field + '=%s'); values.append(value)
        clauses += ['is_superadmin=%s', 'auth_version=auth_version+1', 'updated_at=%s']
        values += [int(user['home_tenant_id'] == 'hq' and changes.get('role', user['role']) == 'admin'), time.time(), user_id]
        cur.execute('UPDATE sec_users SET ' + ','.join(clauses) + ' WHERE id=%s', values)
        cur.execute('UPDATE sec_sessions SET revoked_at=%s WHERE user_id=%s AND revoked_at IS NULL', (time.time(), user_id))
        _audit(cur, principal, 'user_update', user_id, 'success', {'changed_fields': ['credential_reset' if k == 'password' else k for k in changes], 'sessions_revoked': True, 'reason': changes.get('reason', '')})


def list_grants(principal):
    require_admin(principal, superadmin=True)
    with control_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT g.*,u.username,t.name AS tenant_name FROM sec_grants g JOIN sec_users u ON u.id=g.user_id JOIN sec_tenants t ON t.id=g.tenant_id ORDER BY g.created_at DESC,g.id')
        return [{**row, 'created_at': _utc(row['created_at'])} for row in cur.fetchall()]


def grant_access(principal, user_id, tenant_id, access, reason):
    principal = require_admin(principal, superadmin=True)
    if access not in ACCESS or not str(reason).strip():
        raise HTTPException(422, '请选择授权范围并填写依据。')
    with authorized_control_connection(principal, mode='standard') as conn, conn.cursor() as cur:
        cur.execute('SELECT * FROM sec_users WHERE id=%s AND is_active=1', (user_id,))
        user = cur.fetchone()
        cur.execute('SELECT * FROM sec_tenants WHERE id=%s AND status=\'active\'', (tenant_id,))
        target = cur.fetchone()
        if not user or not target:
            raise HTTPException(422, '账号或机构不可用。')
        if tenant_id == user['home_tenant_id']:
            raise HTTPException(422, '本机构权限由账号角色控制，无需跨机构授权。')
        if access == 'write' and not (user['home_tenant_id'] == 'hq' and user['role'] == 'admin'):
            raise HTTPException(403, '跨机构操作仅允许总部管理员；普通总部账号只能跨机构读取。')
        if user['home_tenant_id'] != 'hq' and access != 'read_published':
            raise HTTPException(403, '跨机构私有读取和操作授权仅面向总部实体账号。')
        if user['home_tenant_id'] != 'hq':
            cur.execute('SELECT category FROM sec_tenants WHERE id=%s', (user['home_tenant_id'],))
            home = cur.fetchone()
            if home['category'] != 'headquarters_department' or target['category'] not in ('headquarters', 'headquarters_department'):
                raise HTTPException(403, '总部公开成果仅面向总部实体和总部部门；其他机构保持隔离。')
        old = _grant(cur, user_id, tenant_id)
        ident = old['id'] if old else uuid.uuid4().hex
        cur.execute('INSERT INTO sec_grants VALUES(%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE access=VALUES(access),reason=VALUES(reason),created_by=VALUES(created_by),created_at=VALUES(created_at)', (ident, user_id, tenant_id, access, str(reason).strip()[:500], principal.user_id, time.time()))
        # No session-wide logout is needed: every request rechecks the live grant.
        if old and old['access'] == 'write' and access != 'write' and not (user['home_tenant_id']=='hq' and user['role']=='admin'):
            cur.execute('UPDATE sec_sessions SET tenant_id=%s WHERE user_id=%s AND tenant_id=%s AND revoked_at IS NULL', (user['home_tenant_id'], user_id, tenant_id))
        _audit(cur, principal, 'grant_set', ident, 'success', {'user_id': user_id, 'tenant_id': tenant_id, 'access': access, 'reason': str(reason)[:500]})
        return ident


def revoke_grant(principal, grant_id):
    principal = require_admin(principal, superadmin=True)
    with authorized_control_connection(principal, mode='standard') as conn, conn.cursor() as cur:
        cur.execute('SELECT g.*,u.home_tenant_id,u.role FROM sec_grants g JOIN sec_users u ON u.id=g.user_id WHERE g.id=%s FOR UPDATE', (grant_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, '授权不存在。')
        cur.execute('DELETE FROM sec_grants WHERE id=%s', (grant_id,))
        if not (row['home_tenant_id']=='hq' and row['role']=='admin'):
            cur.execute('UPDATE sec_sessions SET tenant_id=%s WHERE user_id=%s AND tenant_id=%s AND revoked_at IS NULL', (row['home_tenant_id'], row['user_id'], row['tenant_id']))
        _audit(cur, principal, 'grant_revoke', grant_id, 'success', {'user_id': row['user_id'], 'tenant_id': row['tenant_id'], 'access': row['access']})


def audit_records(principal, page=1, page_size=50):
    require_admin(principal)
    where, args = ('', []) if principal.is_superadmin else (' WHERE tenant_id=%s', [principal.home_tenant_id])
    with control_connection() as conn, conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) AS n FROM sec_audit' + where, args)
        total = cur.fetchone()['n']
        cur.execute('SELECT * FROM sec_audit' + where + ' ORDER BY id DESC LIMIT %s OFFSET %s', [*args, page_size, (page - 1) * page_size])
        rows = [{**row, 'created_at': _utc(row['created_at']), 'details': json.loads(row['details'])} for row in cur.fetchall()]
        return {'items': rows, 'total': total, 'page': page, 'page_size': page_size}
