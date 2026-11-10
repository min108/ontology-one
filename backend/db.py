"""MySQL 8 persistence and the compatibility layer used by the business modules.

All application data is stored in MySQL. The adapter only preserves existing
calling conventions (named/indexed rows, qmark parameters and transactions).
"""
from __future__ import annotations

import json
import os
import re
import time
import hashlib
from collections import OrderedDict
import threading
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymysql
from pymysql.cursors import DictCursor

ROOT = Path(__file__).resolve().parent.parent
ASSET_KINDS = ('data', 'rule', 'skill', 'knowledge')


def now():
    return datetime.now(timezone.utc).isoformat()


def mysql_config():
    """Read a MySQL URL or discrete MYSQL_* settings without persisting secrets."""
    result = {
        'host': os.environ.get('MYSQL_HOST', '127.0.0.1'),
        'port': int(os.environ.get('MYSQL_PORT', '3306')),
        'user': os.environ.get('MYSQL_USER', 'root'),
        'password': os.environ.get('MYSQL_PASSWORD', ''),
        'database': os.environ.get('MYSQL_DATABASE', 'ontology_one_v020'),
    }
    url = os.environ.get('ONTOLOGY_MYSQL_URL', '').strip()
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ('mysql', 'mysql+pymysql') or not parsed.hostname:
            raise RuntimeError('ONTOLOGY_MYSQL_URL 须使用 mysql://user:password@host:port/database 格式。')
        result.update(host=parsed.hostname, port=parsed.port or 3306,
                      user=unquote(parsed.username or ''), password=unquote(parsed.password or ''),
                      database=parsed.path.lstrip('/'))
    if not re.fullmatch(r'[A-Za-z0-9_]+', result['database']):
        raise RuntimeError('MYSQL_DATABASE 仅允许字母、数字和下划线。')
    return result


class Row(dict):
    """A dict row that also supports the numeric indexing used by the application."""
    def __init__(self, value):
        dict.__init__(self, value)
        self._values = list(value.values())

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._values[key]
        return dict.__getitem__(self, key)

    def __iter__(self):
        return iter(self._values)

    def copy(self):
        # dict.copy/constructor use Python __getitem__ for this subclass because
        # its legacy iterator yields values. The item view copies in C instead.
        return dict(dict.items(self))


def _wrap(value):
    return Row(value) if isinstance(value, dict) and not isinstance(value, Row) else value


class Cursor:
    def __init__(self, cursor, guard=None):
        self._cursor = cursor
        self._guard = guard

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    def execute(self, sql, params=()):
        if self._guard:
            params = self._guard(sql, params)
        self._cursor.execute(translate(sql), tuple(params or ()))
        return self

    def executemany(self, sql, params):
        if self._guard:
            self._guard(sql, ())
        self._cursor.executemany(translate(sql), params)
        return self

    def fetchone(self):
        return _wrap(self._cursor.fetchone())

    def fetchmany(self, size=None):
        return [_wrap(x) for x in self._cursor.fetchmany(size)]

    def fetchall(self):
        return [_wrap(x) for x in self._cursor.fetchall()]

    def __iter__(self):
        fetchone = self._cursor.fetchone
        while True:
            row = fetchone()
            if row is None:
                return
            yield _wrap(row)

    def close(self):
        self._cursor.close()


class Connection:
    def __init__(self, raw, principal=None, readonly=False, release=None):
        self.raw = raw
        self.principal = principal
        self.readonly = readonly
        self._release = release
        self._closed = False
        self._dirty = False

    def _guard(self, sql, params):
        command = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ''
        if self.principal and self.principal.identity_kind == 'application':
            # Application tools generate fixed SQL. Reject statement stacking,
            # comments and SELECT file writes even on a reused public cursor.
            if re.search(r';|--|/\*|\*/|#|\b(?:OUTFILE|DUMPFILE|LOAD_FILE)\b', sql, re.I):
                from fastapi import HTTPException
                raise HTTPException(403, '应用查询语句不受支持。')
        writes = command not in ('SELECT', 'SHOW', 'DESCRIBE', 'EXPLAIN', 'SET', 'BEGIN', 'START')
        if writes:
            if self.readonly:
                from fastapi import HTTPException
                raise HTTPException(403, '当前连接只允许读取。')
            if self.principal and self.principal.identity_kind == 'application':
                from .security.applications import validate_application_write
                validate_application_write(self.principal, sql)
            if self.principal and not self._dirty:
                from .jobs import assert_execution_principal_current
                assert_execution_principal_current(self.principal, require_write=True)
            self._dirty = True
        return tuple(params or ())

    def execute(self, sql, params=()):
        cursor = Cursor(self.raw.cursor(), self._guard)
        try:
            return cursor.execute(sql, params)
        except Exception as exc:
            if exc.args and exc.args[0] == 1061 and re.match(r'(?i)^\s*CREATE\s+(?:UNIQUE\s+)?INDEX', sql):
                return cursor
            cursor.close()
            raise

    def executemany(self, sql, params):
        cursor = Cursor(self.raw.cursor(), self._guard)
        try:
            return cursor.executemany(sql, params)
        except Exception:
            cursor.close()
            raise

    def executescript(self, script):
        for statement in split_script(script):
            self.execute(statement)

    def commit(self):
        if self._dirty and self.principal:
            from .jobs import assert_job_lease_current, assert_execution_principal_current
            assert_execution_principal_current(self.principal, require_write=True)
            assert_job_lease_current()
        self.raw.commit()
        if self._dirty and self.principal:
            import sys
            module = sys.modules.get('backend.ai')
            if module and hasattr(module, 'invalidate_search_cache'):
                module.invalidate_search_cache(self.principal.tenant_id)
            module = sys.modules.get('backend.graph')
            if module and hasattr(module, 'invalidate_graph_cache'):
                module.invalidate_graph_cache(self.principal.tenant_id)
        self._dirty = False

    def rollback(self):
        self.raw.rollback()

    def begin(self):
        self.raw.begin()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self.raw.rollback()
        finally:
            if self._release:
                self._release(self.raw)
            else:
                self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def split_script(script):
    """Split the project's DDL scripts without splitting quoted semicolons."""
    output, current, quote = [], [], ''
    escaped = False
    for char in script:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == '\\' and quote:
            current.append(char)
            escaped = True
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = ''
            continue
        if char in ("'", '"', '`'):
            quote = char
            current.append(char)
        elif char == ';':
            statement = ''.join(current).strip()
            if statement:
                output.append(statement)
            current = []
        else:
            current.append(char)
    statement = ''.join(current).strip()
    if statement:
        output.append(statement)
    return output


def _parameters(sql):
    """Convert qmark parameters while leaving quoted question marks untouched."""
    output, quote = [], ''
    escaped = False
    for char in sql:
        if escaped:
            output.append(char)
            escaped = False
            continue
        if char == '\\' and quote:
            output.append(char)
            escaped = True
            continue
        if quote:
            output.append(char)
            if char == quote:
                quote = ''
            continue
        if char in ("'", '"', '`'):
            quote = char
            output.append(char)
        else:
            output.append('%s' if char == '?' else char)
    return ''.join(output)


def _literal_percents(sql):
    """PyMySQL uses percent formatting; preserve quoted SQL LIKE literals."""
    output, quote = [], ''
    escaped = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if escaped:
            output.append(char)
            escaped = False
            index += 1
            continue
        if char == '\\' and quote:
            output.append(char)
            escaped = True
            index += 1
            continue
        if quote:
            if char == '%':
                output.append('%%')
                index += 2 if index + 1 < len(sql) and sql[index + 1] == '%' else 1
                continue
            output.append(char)
            if char == quote:
                quote = ''
            index += 1
            continue
        output.append(char)
        if char in ("'", '"', '`'):
            quote = char
        index += 1
    return ''.join(output)


def _json_value(match):
    arguments = match.group(1)
    if re.search(r"'\$\.(?:active_asset|active|authority_managed)'\s*$", arguments, re.I):
        # SQLite returns 1/0 for JSON booleans. MySQL JSON true/false needs an
        # explicit numeric cast before the legacy numeric comparisons.
        return f'CAST(JSON_EXTRACT({arguments}) AS UNSIGNED)'
    return f'JSON_UNQUOTE(JSON_EXTRACT({arguments})) COLLATE utf8mb4_unicode_ci'


@lru_cache(maxsize=2048)
def translate(sql):
    """Translate the small, deliberate legacy SQL vocabulary to MySQL 8."""
    value = re.sub(r'(?i)\bjson_extract\(([^()]+)\)', _json_value, sql)
    value = re.sub(r'(?i)AS\s+TEXT\s*\)', 'AS CHAR)', value)
    value = re.sub(r'(?i)^\s*INSERT\s+OR\s+REPLACE\s+INTO\b', 'REPLACE INTO', value)
    value = re.sub(r'(?i)^\s*INSERT\s+OR\s+IGNORE\s+INTO\b', 'INSERT IGNORE INTO', value)
    value = re.sub(r'(?i)^\s*BEGIN\s+IMMEDIATE\s*$', 'BEGIN', value)
    value = re.sub(r'(?i)^(\s*CREATE\s+(?:UNIQUE\s+)?INDEX)\s+IF\s+NOT\s+EXISTS\b', r'\1', value)
    return _literal_percents(_parameters(value))


_REQUEST_READONLY = ContextVar('ontology_request_readonly', default=False)


@contextmanager
def business_readonly(enabled=True):
    token = _REQUEST_READONLY.set(enabled)
    try:
        yield
    finally:
        _REQUEST_READONLY.reset(token)


# A process-wide bound covers active AND idle connections across all tenants.
# Idle connections retain only physical database identity and are evicted LRU.
_POOL_IDLE = OrderedDict()
_POOL_CONDITION = threading.Condition()
_POOL_TOTAL = 0
_READY = set()


def _raw_connection(database, *, create=False, timeout=15):
    settings = mysql_config()
    options = dict(host=settings['host'], port=settings['port'], user=settings['user'],
                   password=settings['password'], charset='utf8mb4', cursorclass=DictCursor,
                   autocommit=False, connect_timeout=timeout, read_timeout=120, write_timeout=120)
    if not re.fullmatch(r'[A-Za-z0-9_]{1,64}', database):
        raise ValueError('Invalid server-side database identifier')
    if create:
        with closing(pymysql.connect(**options)) as server:
            with server.cursor() as cursor:
                cursor.execute(f'CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
            server.commit()
    raw = pymysql.connect(database=database, **options)
    with raw.cursor() as cursor:
        cursor.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci")
        cursor.execute("SET SESSION time_zone='+00:00', lock_wait_timeout=10")
    return raw


def _borrow(database):
    global _POOL_TOTAL
    from fastapi import HTTPException
    settings = mysql_config()
    key = (settings['host'], settings['port'], settings['user'], database,
           hashlib.sha256(settings['password'].encode()).digest())
    capacity = max(1, min(64, int(os.getenv('ONTOLOGY_DB_POOL_SIZE', '16'))))
    deadline = time.monotonic() + 10
    raw, evicted = None, None
    with _POOL_CONDITION:
        while True:
            matching = next((ident for ident in reversed(_POOL_IDLE) if _POOL_IDLE[ident][0] == key), None)
            if matching is not None:
                _, raw = _POOL_IDLE.pop(matching)
                break
            if _POOL_TOTAL < capacity:
                _POOL_TOTAL += 1
                break
            if _POOL_IDLE:
                _, (_, evicted) = _POOL_IDLE.popitem(last=False)
                break  # Replace one idle slot; total stays bounded.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HTTPException(503, '业务连接池繁忙，请稍后重试。')
            _POOL_CONDITION.wait(remaining)
    try:
        if evicted is not None:
            try:
                evicted.close()
            except Exception:
                pass  # The server may already have disconnected an idle socket.
        if raw is not None:
            try:
                raw.ping(reconnect=False)
            except pymysql.MySQLError:
                raw.close()
                raw = None
        if raw is None:
            raw = _raw_connection(database)
    except Exception:
        with _POOL_CONDITION:
            _POOL_TOTAL -= 1
            _POOL_CONDITION.notify()
        raise

    def release(connection):
        global _POOL_TOTAL
        healthy = False
        try:
            connection.rollback()
            with connection.cursor() as cursor:
                cursor.execute('SET SESSION TRANSACTION READ WRITE')
            healthy = True
        except Exception:
            connection.close()
        with _POOL_CONDITION:
            if healthy:
                _POOL_IDLE[id(connection)] = (key, connection)
            else:
                _POOL_TOTAL -= 1
            _POOL_CONDITION.notify()
    return raw, release


def connect():
    from .security.context import require_context
    from .jobs import assert_execution_principal_current
    principal = require_context()
    assert_execution_principal_current(principal)
    raw, release = _borrow(principal.database_name)
    readonly = principal.readonly or _REQUEST_READONLY.get()
    try:
        if readonly:
            with raw.cursor() as cursor:
                cursor.execute('SET SESSION TRANSACTION READ ONLY')
        return Connection(raw, principal, readonly, release)
    except Exception:
        release(raw)
        raise


@contextmanager
def tenant_connection(tenant_id, readonly=True):
    from .security.context import require_context
    from .security.store import can_read_tenant
    from fastapi import HTTPException
    principal = require_context()
    if principal.identity_kind != 'session':
        raise HTTPException(403, '应用身份只能访问其固定机构。')
    # can_read_tenant atomically validates the live session and its target grant.
    access = can_read_tenant(principal, tenant_id)
    if not access:
        raise HTTPException(403, '未获该机构的访问授权。')
    if not readonly:
        raise HTTPException(403, '跨机构聚合连接仅用于读取。')
    database = access.get('db_name') or access.get('database_name')
    if not database:
        tenant = access.get('tenant') or {}
        database = tenant.get('db_name') or tenant.get('database_name')
    raw, release = _borrow(database)
    connection = None
    try:
        with raw.cursor() as cursor:
            cursor.execute('SET SESSION TRANSACTION READ ONLY')
        connection = Connection(raw, principal, True, release)
        yield connection
    finally:
        if connection is not None:
            connection.close()
        else:
            release(raw)


def current_org_id():
    from .security.context import require_context
    return require_context().tenant_id


def entity_json(row):
    if row is None:
        return None
    value = row.copy() if isinstance(row, dict) else dict(row)
    try:
        value['payload'] = json.loads(value.get('payload') or '{}')
    except (TypeError, ValueError):
        value['payload'] = {}
    from .asset_kinds import canonical_entity
    value = canonical_entity(value)
    from .security.context import get_context
    principal = get_context()
    if principal:
        value['owner_tenant_id'] = principal.tenant_id
        value['owner_org_id'] = principal.tenant_id
    return value


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS entity_access(
        entity_id VARCHAR(191) PRIMARY KEY,visibility VARCHAR(32) NOT NULL DEFAULT 'institution',
        updated_by VARCHAR(191),updated_at VARCHAR(40)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS entity_ownership(
        entity_id VARCHAR(191) PRIMARY KEY,tenant_id VARCHAR(191) NOT NULL,created_by VARCHAR(191) NOT NULL,
        created_at VARCHAR(40) NOT NULL,updated_by VARCHAR(191),updated_at VARCHAR(40)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS workspace_meta(
        meta_key VARCHAR(100) PRIMARY KEY,meta_value LONGTEXT,updated_at VARCHAR(40)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS entities(
        id VARCHAR(191) PRIMARY KEY,kind VARCHAR(64) NOT NULL,title TEXT,summary LONGTEXT,
        domain VARCHAR(255),org_id VARCHAR(191),status VARCHAR(64),source_kind VARCHAR(64),
        payload JSON,created_at VARCHAR(40),updated_at VARCHAR(40),version INT DEFAULT 1
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS relations(
        id VARCHAR(191) PRIMARY KEY,source VARCHAR(191),target VARCHAR(191),type VARCHAR(100),
        label VARCHAR(255),status VARCHAR(64),payload LONGTEXT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS versions(
        id BIGINT PRIMARY KEY AUTO_INCREMENT,entity_id VARCHAR(191),version INT,
        snapshot LONGTEXT,created_at VARCHAR(40),reason TEXT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS activities(
        id BIGINT PRIMARY KEY AUTO_INCREMENT,entity_id VARCHAR(191),action VARCHAR(100),
        detail TEXT,actor VARCHAR(255),created_at VARCHAR(40)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS chunks(
        id VARCHAR(255) PRIMARY KEY,material_id VARCHAR(191),position INT,heading TEXT,
        content LONGTEXT,locator TEXT,status VARCHAR(64)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS dataset_rows(
        dataset_id VARCHAR(191),row_no INT,values_json LONGTEXT,org_id VARCHAR(191),
        PRIMARY KEY(dataset_id,row_no)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS mappings(
        dataset_id VARCHAR(191),column_name VARCHAR(500),standard_id VARCHAR(191),transform VARCHAR(100),
        status VARCHAR(64),payload LONGTEXT,rowid BIGINT NOT NULL AUTO_INCREMENT UNIQUE,
        PRIMARY KEY(dataset_id,column_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS jobs(
        id VARCHAR(191) PRIMARY KEY,kind VARCHAR(100),entity_id VARCHAR(191),status VARCHAR(64),
        progress INT,message TEXT,result LONGTEXT,created_at VARCHAR(40),updated_at VARCHAR(40)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS application_findings(
        run_id VARCHAR(191),category VARCHAR(100),sequence INTEGER,payload LONGTEXT,
        PRIMARY KEY(run_id,category,sequence)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS asset_import_batches(
        id VARCHAR(191) PRIMARY KEY,kind VARCHAR(32) NOT NULL,filename VARCHAR(500),sha256 VARCHAR(64),
        material_id VARCHAR(191),created_count INT,updated_count INT,status VARCHAR(32),created_at VARCHAR(40)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    "CREATE INDEX IF NOT EXISTS idx_entities_kind ON entities(kind)",
    "CREATE INDEX IF NOT EXISTS idx_entities_domain ON entities(domain)",
    "CREATE INDEX IF NOT EXISTS idx_entities_org ON entities(org_id)",
    "CREATE INDEX IF NOT EXISTS idx_entities_status ON entities(status)",
    "CREATE INDEX IF NOT EXISTS idx_entities_recent ON entities(updated_at DESC,id)",
    "CREATE INDEX IF NOT EXISTS idx_entities_kind_recent ON entities(kind,updated_at DESC,id)",
    "CREATE INDEX IF NOT EXISTS idx_relation_source ON relations(source)",
    "CREATE INDEX IF NOT EXISTS idx_relation_target ON relations(target)",
    "CREATE INDEX IF NOT EXISTS idx_relation_status ON relations(status)",
    "CREATE INDEX IF NOT EXISTS idx_chunks_material ON chunks(material_id,position)",
    "CREATE INDEX IF NOT EXISTS idx_activity ON activities(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_versions ON versions(entity_id,version)",
)


def normalize_collations(conn):
    """Keep cross-table text joins compatible on existing MySQL 8 installations."""
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema=DATABASE() AND table_type='BASE TABLE' "
        "AND table_collation IS NOT NULL AND table_collation<>'utf8mb4_unicode_ci'"
    ).fetchall()
    for row in rows:
        table = row[0]
        if re.fullmatch(r'[A-Za-z0-9_]+', table):
            conn.execute(f'ALTER TABLE `{table}` CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')


def ensure_entity_json_storage(conn):
    """Parse entity payloads once on write, retaining the 12-column row contract.

    Existing workspaces are checked before conversion: a malformed legacy value
    stops the migration rather than being discarded or silently replaced. The
    caller holds the tenant schema lock, so concurrent workers cannot race DDL.
    """
    column = conn.execute(
        "SELECT DATA_TYPE FROM information_schema.columns "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='entities' AND COLUMN_NAME='payload'"
    ).fetchone()
    if not column:
        raise RuntimeError('机构实体表缺少 payload 字段，初始化已停止。')
    if str(column[0]).lower() == 'json':
        return
    invalid = conn.execute(
        'SELECT id FROM entities WHERE payload IS NOT NULL AND JSON_VALID(payload)=0 LIMIT 1'
    ).fetchone()
    if invalid:
        raise RuntimeError('机构实体包含非法 JSON，无法升级存储；请在维护模式核查原始数据后重试。')
    conn.execute('ALTER TABLE entities MODIFY COLUMN payload JSON')
    # Refresh estimates after the table rebuild so ordered page reads can use
    # the composite indexes immediately, including on small existing tenants.
    conn.execute('ANALYZE TABLE entities').fetchall()


def ensure_tenant_schema(tenant_id):
    """Provision only directory-owned database names; never accept a client database."""
    from .security.store import get_tenant
    tenant = get_tenant(tenant_id)
    if not tenant:
        raise ValueError('Unknown tenant')
    database = tenant.get('db_name') or tenant.get('database_name')
    if database in _READY:
        return
    with closing(_raw_connection(database, create=True)) as raw:
        conn = Connection(raw)
        lock_name = 'ontology-schema-' + database[-40:]
        if conn.execute('SELECT GET_LOCK(?,30)', (lock_name,)).fetchone()[0] != 1:
            raise RuntimeError('租户初始化繁忙，请重试。')
        try:
            for statement in SCHEMA:
                conn.execute(statement)
            ensure_entity_json_storage(conn)
            from .datasets import ensure_schema
            ensure_schema(conn)
            from .provenance import initialize_provenance
            initialize_provenance(conn)
            from .special_projects import init_special_schema
            init_special_schema(conn)
            from .sources import ensure_schema as ensure_source_schema
            ensure_source_schema(conn)
            from .skills import ensure_schema as ensure_skill_schema
            ensure_skill_schema(conn)
            name = tenant.get('name') or tenant.get('title')
            category = tenant.get('category') or tenant.get('kind')
            payload = {'type': category, 'group': '一级子公司' if category == 'subsidiary' else '总部职能部门',
                       'tenant_id': tenant_id, 'hide_in_graph': category == 'headquarters', 'managed_directory': True}
            timestamp = now()
            conn.execute('INSERT IGNORE INTO entities VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (tenant_id, 'organization', name, '机构根节点', '组织管理', tenant_id, 'published', 'directory',
                          json.dumps(payload, ensure_ascii=False), timestamp, timestamp, 1))
            conn.execute('REPLACE INTO workspace_meta(meta_key,meta_value,updated_at) VALUES(?,?,?)',
                         ('schema_version', '0.5.0', timestamp))
            normalize_collations(conn)
            from .asset_kinds import migrate_skill_assets
            migrate_skill_assets(conn)
            conn.commit()
            _READY.add(database)
        finally:
            conn.execute('SELECT RELEASE_LOCK(?)', (lock_name,))


def initialize():
    from .security.context import require_context
    ensure_tenant_schema(require_context().tenant_id)
    return False


def log(conn, entity_id, action, detail='', actor='系统'):
    from .security.context import get_context
    principal = get_context()
    if principal:
        actor = principal.username
        conn.execute('INSERT INTO entity_ownership(entity_id,tenant_id,created_by,created_at,updated_by,updated_at) VALUES(?,?,?,?,?,?) ON DUPLICATE KEY UPDATE updated_by=VALUES(updated_by),updated_at=VALUES(updated_at)',
                     (entity_id, principal.tenant_id, principal.user_id, now(), principal.user_id, now()))
    conn.execute('INSERT INTO activities(entity_id,action,detail,actor,created_at) VALUES(?,?,?,?,?)',
                 (entity_id, action, detail, actor, now()))
