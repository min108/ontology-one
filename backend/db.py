"""MySQL 8 persistence and the compatibility layer used by the business modules.

All application data is stored in MySQL. The adapter only preserves existing
calling conventions (named/indexed rows, qmark parameters and transactions).
"""
from __future__ import annotations

import json
import os
import re
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymysql
from pymysql.cursors import DictCursor

ROOT = Path(__file__).resolve().parent.parent
ASSET_KINDS = ('data', 'rule', 'algorithm', 'knowledge')


def now():
    return datetime.now(timezone.utc).isoformat()


def mysql_config():
    """Read a MySQL URL or discrete MYSQL_* settings without persisting secrets."""
    result = {
        'host': os.environ.get('MYSQL_HOST', '127.0.0.1'),
        'port': int(os.environ.get('MYSQL_PORT', '3306')),
        'user': os.environ.get('MYSQL_USER', 'root'),
        'password': os.environ.get('MYSQL_PASSWORD', ''),
        'database': os.environ.get('MYSQL_DATABASE', 'ontology_one'),
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
        super().__init__(value)
        self._values = list(value.values())

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._values[key]
        return super().__getitem__(key)

    def __iter__(self):
        return iter(self._values)


def _wrap(value):
    return Row(value) if isinstance(value, dict) and not isinstance(value, Row) else value


class Cursor:
    def __init__(self, cursor):
        self._cursor = cursor

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
        self._cursor.execute(translate(sql), tuple(params or ()))
        return self

    def executemany(self, sql, params):
        self._cursor.executemany(translate(sql), params)
        return self

    def fetchone(self):
        return _wrap(self._cursor.fetchone())

    def fetchmany(self, size=None):
        return [_wrap(x) for x in self._cursor.fetchmany(size)]

    def fetchall(self):
        return [_wrap(x) for x in self._cursor.fetchall()]

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row

    def close(self):
        self._cursor.close()


class Connection:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        cursor = Cursor(self.raw.cursor())
        try:
            return cursor.execute(sql, params)
        except Exception as exc:
            if exc.args and exc.args[0] == 1061 and re.match(r'(?i)^\s*CREATE\s+(?:UNIQUE\s+)?INDEX', sql):
                return cursor
            cursor.close()
            raise

    def executemany(self, sql, params):
        cursor = Cursor(self.raw.cursor())
        try:
            return cursor.executemany(sql, params)
        except Exception:
            cursor.close()
            raise

    def executescript(self, script):
        for statement in split_script(script):
            self.execute(statement)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def begin(self):
        self.raw.begin()

    def close(self):
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


def translate(sql):
    """Translate the small, deliberate legacy SQL vocabulary to MySQL 8."""
    value = re.sub(r'(?i)\bjson_extract\(([^()]+)\)', _json_value, sql)
    value = re.sub(r'(?i)AS\s+TEXT\s*\)', 'AS CHAR)', value)
    value = re.sub(r'(?i)^\s*INSERT\s+OR\s+REPLACE\s+INTO\b', 'REPLACE INTO', value)
    value = re.sub(r'(?i)^\s*INSERT\s+OR\s+IGNORE\s+INTO\b', 'INSERT IGNORE INTO', value)
    value = re.sub(r'(?i)^\s*BEGIN\s+IMMEDIATE\s*$', 'BEGIN', value)
    value = re.sub(r'(?i)^(\s*CREATE\s+(?:UNIQUE\s+)?INDEX)\s+IF\s+NOT\s+EXISTS\b', r'\1', value)
    return _literal_percents(_parameters(value))


def connect():
    settings = mysql_config()
    options = dict(host=settings['host'], port=settings['port'], user=settings['user'],
                   password=settings['password'], charset='utf8mb4', cursorclass=DictCursor,
                   autocommit=False, connect_timeout=15, read_timeout=120, write_timeout=120)
    try:
        raw = pymysql.connect(database=settings['database'], **options)
    except pymysql.err.OperationalError as exc:
        if not exc.args or exc.args[0] != 1049:
            raise
        with closing(pymysql.connect(**options)) as server:
            with server.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE `{settings['database']}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            server.commit()
        raw = pymysql.connect(database=settings['database'], **options)
    with raw.cursor() as cursor:
        cursor.execute("SET NAMES utf8mb4 COLLATE utf8mb4_unicode_ci")
        cursor.execute("SET SESSION time_zone='+00:00', lock_wait_timeout=60")
    return Connection(raw)


def entity_json(row):
    if row is None:
        return None
    value = dict(row)
    try:
        value['payload'] = json.loads(value.get('payload') or '{}')
    except (TypeError, ValueError):
        value['payload'] = {}
    return value


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS workspace_meta(
        meta_key VARCHAR(100) PRIMARY KEY,meta_value LONGTEXT,updated_at VARCHAR(40)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
    """CREATE TABLE IF NOT EXISTS entities(
        id VARCHAR(191) PRIMARY KEY,kind VARCHAR(64) NOT NULL,title TEXT,summary LONGTEXT,
        domain VARCHAR(255),org_id VARCHAR(191),status VARCHAR(64),source_kind VARCHAR(64),
        payload LONGTEXT,created_at VARCHAR(40),updated_at VARCHAR(40),version INT DEFAULT 1
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


def initialize():
    fresh = False
    with closing(connect()) as conn, conn:
        for statement in SCHEMA:
            conn.execute(statement)
        if not conn.execute('SELECT 1 FROM entities LIMIT 1').fetchone():
            fresh = True
            from .seed import seed
            seed(conn)
        from .datasets import seed_datasets
        seed_datasets(conn)
        from .provenance import initialize_provenance
        initialize_provenance(conn)
        from .special_demo import seed_special_demo
        seed_special_demo(conn)
        from .authority_seed import seed_authority
        seed_authority(conn)
        normalize_collations(conn)
    return fresh


def log(conn, entity_id, action, detail='', actor='本地体验用户'):
    conn.execute('INSERT INTO activities(entity_id,action,detail,actor,created_at) VALUES(?,?,?,?,?)',
                 (entity_id, action, detail, actor, now()))
