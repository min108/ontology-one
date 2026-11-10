"""Bounded, read-only external adapters. Credentials and results are never cached.

The MCP client supports the explicit ontology-source/1 profile through the official
SDK; it never forwards arbitrary tool names, SQL, incoming tokens or instructions.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from contextvars import ContextVar
from datetime import date, datetime, time as daytime, timedelta
from decimal import Decimal
import json
import logging
import math
import os
from pathlib import Path
import re
import ssl
import stat
import time
from urllib.parse import urlsplit

import httpx2
import pymysql
from pymysql.cursors import SSDictCursor
from pymysql.constants import CLIENT
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import REQUEST_TIMEOUT

MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_CELL_BYTES = 16 * 1024
MAX_WIRE_BYTES = MAX_JSON_BYTES + 64 * 1024
SESSION_TIMEOUT = 20
SOCKET_TIMEOUT = 5
_RESOURCE_KEYS = {'key', 'name', 'kind', 'columns', 'primary_key', 'description', 'definition', 'definition_version'}
_COLUMN_KEYS = {'name', 'type', 'nullable', 'primary_key', 'comment'}
_OPERATORS = {'eq': '=', 'ne': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}
_MYSQL_TYPES = {'tinyint', 'smallint', 'mediumint', 'int', 'integer', 'bigint', 'float', 'double', 'real',
                'decimal', 'numeric', 'bool', 'boolean', 'date', 'datetime', 'timestamp', 'time', 'year',
                'char', 'varchar', 'tinytext', 'text', 'mediumtext', 'longtext', 'enum', 'set', 'json'}
_REMOTE_LOG_SCOPE = ContextVar('ontology_source_remote_logging', default=False)


class SourceFailure(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _fail(code, message, status=422):
    raise SourceFailure(code, message, status)


def _safe_log_factory(previous):
    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        if _REMOTE_LOG_SCOPE.get() and record.name.startswith(('mcp', 'httpx', 'httpcore')):
            record.msg, record.args = 'External source transport event (details suppressed)', ()
            record.exc_info, record.exc_text, record.stack_info = None, None, None
        return record
    return factory


# Task-local redaction leaves unrelated application logging unchanged, including
# concurrent requests. SDK child tasks inherit this context before any network IO.
logging.setLogRecordFactory(_safe_log_factory(logging.getLogRecordFactory()))


def _text(value, *, max_bytes=MAX_CELL_BYTES, empty=False):
    return isinstance(value, str) and (empty or bool(value)) and len(value.encode('utf-8')) <= max_bytes and '\x00' not in value


def _size(value, maximum=MAX_JSON_BYTES, code='SOURCE_RESULT_LIMIT'):
    try:
        size = len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8'))
    except (TypeError, ValueError, OverflowError, RecursionError):
        _fail('SOURCE_INVALID_RESULT', '数据源返回了不支持的数据格式。', 502)
    if size > maximum:
        _fail(code, '数据源响应超过允许大小，请缩小查询范围。', 502)
    return size


def _endpoint(value):
    if not _text(value, max_bytes=2000) or any(ord(ch) < 33 for ch in value):
        _fail('SOURCE_CONFIG_INVALID', 'MCP地址格式不正确。')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        _fail('SOURCE_CONFIG_INVALID', 'MCP地址格式不正确。')
    if parsed.scheme not in {'https', 'http'} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.query:
        _fail('SOURCE_CONFIG_INVALID', 'MCP地址须为HTTP(S)地址，不能包含账号、密码、查询参数或片段。')
    if port is not None and not 1 <= port <= 65535:
        _fail('SOURCE_CONFIG_INVALID', 'MCP端口不正确。')
    return value


def validate_config(kind, config):
    if not isinstance(config, dict):
        _fail('SOURCE_CONFIG_INVALID', '数据源配置须为对象。')
    allowed = {'host', 'port', 'database', 'allowed_objects'} if kind == 'mysql' else {'endpoint', 'allowed_objects'} if kind == 'mcp' else None
    if allowed is None or set(config) != allowed:
        _fail('SOURCE_CONFIG_INVALID', '数据源类型或配置字段不受支持。')
    objects = config['allowed_objects']
    if not isinstance(objects, list) or not 1 <= len(objects) <= 100 or any(not _text(name, max_bytes=1000) for name in objects) or len(set(objects)) != len(objects):
        _fail('SOURCE_CONFIG_INVALID', '请配置1至100个不重复的允许访问对象。')
    result = dict(config, allowed_objects=list(objects))
    if kind == 'mysql':
        if not _text(config['host'], max_bytes=255) or any(char.isspace() for char in config['host']) or any(char in config['host'] for char in '/\\@'):
            _fail('SOURCE_CONFIG_INVALID', 'MySQL主机格式不正确。')
        if type(config['port']) is not int or not 1 <= config['port'] <= 65535 or not _text(config['database'], max_bytes=255):
            _fail('SOURCE_CONFIG_INVALID', 'MySQL端口或数据库名称不正确。')
    else:
        result['endpoint'] = _endpoint(config['endpoint'])
    return result


def _object_pairs(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            _fail('SOURCE_CREDENTIALS_INVALID', '部署凭据文件格式不正确。', 503)
        value[key] = child
    return value


def _resolve_credentials(source):
    kind = source.get('kind')
    config = validate_config(kind, source.get('config'))
    reference, tenant = source.get('credential_ref'), source.get('tenant_id')
    if not _text(reference, max_bytes=255) or not _text(tenant, max_bytes=100):
        _fail('SOURCE_CREDENTIALS_UNAVAILABLE', '数据源尚未配置有效的机构凭据引用。', 503)
    raw_path = os.environ.get('ONTOLOGY_SOURCE_CREDENTIALS_FILE', '')
    try:
        path = Path(raw_path)
        if not raw_path or not path.is_absolute() or '..' in path.parts:
            raise ValueError()
        for part in (path, *path.parents):
            if part.is_symlink():
                raise ValueError()
        resolved = path.resolve(strict=True)
        forbidden = [Path(__file__).resolve().parent.parent]
        if os.environ.get('ONTOLOGY_STORAGE_ROOT'):
            forbidden.append(Path(os.environ['ONTOLOGY_STORAGE_ROOT']).resolve())
        if any(resolved == root or root in resolved.parents for root in forbidden) or 'uploads' in resolved.parts:
            raise ValueError()
        fd = os.open(resolved, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1024 * 1024:
                raise ValueError()
            raw = handle.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        registry = json.loads(raw, object_pairs_hook=_object_pairs)
        entry = registry.get('refs', {}).get(reference) if isinstance(registry, dict) else None
    except SourceFailure:
        raise
    except (OSError, ValueError, TypeError, RecursionError):
        _fail('SOURCE_CREDENTIALS_UNAVAILABLE', '部署凭据文件不可用；须由管理员配置安全位置的600权限文件。', 503)
    if not isinstance(entry, dict) or entry.get('tenant_id') != tenant or entry.get('kind') != kind:
        _fail('SOURCE_CREDENTIAL_SCOPE', '凭据引用不存在、已撤销或不属于当前机构与数据源类型。', 403)
    keys = ('host', 'port', 'database') if kind == 'mysql' else ('endpoint',)
    if any(entry.get(key) != config[key] or type(entry.get(key)) is not type(config[key]) for key in keys):
        _fail('SOURCE_CREDENTIAL_SCOPE', '凭据绑定目标与数据源配置不一致。', 403)
    if 'allow_insecure' in entry and type(entry['allow_insecure']) is not bool:
        _fail('SOURCE_CREDENTIALS_INVALID', '部署凭据配置不正确。', 503)
    if kind == 'mysql':
        if not _text(entry.get('username'), max_bytes=255) or not _text(entry.get('password'), max_bytes=16384, empty=True):
            _fail('SOURCE_CREDENTIALS_INVALID', 'MySQL凭据配置不完整。', 503)
    else:
        if urlsplit(config['endpoint']).scheme == 'http' and entry.get('allow_insecure') is not True:
            _fail('SOURCE_TLS_REQUIRED', 'MCP数据源须使用HTTPS；本地测试例外须由部署管理员明确配置。', 403)
        if not _text(entry.get('token'), max_bytes=16384, empty=True) or any(char in entry.get('token', '') for char in '\r\n'):
            _fail('SOURCE_CREDENTIALS_INVALID', 'MCP凭据配置不完整。', 503)
    return dict(entry)


def _validate_resources(resources, allowed_objects):
    _size(resources, code='SOURCE_METADATA_LIMIT')
    if not isinstance(resources, list) or len(resources) > 100:
        _fail('SOURCE_METADATA_LIMIT', '数据源最多返回100个资源。', 502)
    seen, output = set(), []
    for item in resources:
        if not isinstance(item, dict) or set(item) - _RESOURCE_KEYS or not {'key', 'name', 'kind', 'columns', 'primary_key'} <= set(item):
            _fail('SOURCE_METADATA_INVALID', '数据源资源结构不符合约定。', 502)
        if not _text(item['key'], max_bytes=1000) or item['key'] in seen or not _text(item['name']) or item['kind'] not in {'table', 'view', 'saved_query'}:
            _fail('SOURCE_METADATA_INVALID', '数据源资源标识、名称或类型不正确。', 502)
        seen.add(item['key'])
        if not isinstance(item['columns'], list) or len(item['columns']) > 200:
            _fail('SOURCE_METADATA_LIMIT', '每个资源最多支持200个字段。', 502)
        names, columns = set(), []
        for column in item['columns']:
            if not isinstance(column, dict) or set(column) - _COLUMN_KEYS or not {'name', 'type', 'nullable', 'primary_key'} <= set(column):
                _fail('SOURCE_METADATA_INVALID', '字段结构不符合约定。', 502)
            if not _text(column['name'], max_bytes=1000) or column['name'] in names or not _text(column['type']) or type(column['nullable']) is not bool or type(column['primary_key']) is not bool:
                _fail('SOURCE_METADATA_INVALID', '字段名称、类型或约束不正确。', 502)
            if 'comment' in column and not _text(column['comment'], empty=True):
                _fail('SOURCE_METADATA_INVALID', '字段说明格式不正确。', 502)
            names.add(column['name'])
            columns.append(dict(column))
        primary = item['primary_key']
        if not isinstance(primary, list) or any(not isinstance(name, str) or name not in names for name in primary) or len(set(primary)) != len(primary) or set(primary) != {col['name'] for col in columns if col['primary_key']}:
            _fail('SOURCE_METADATA_INVALID', '主键声明与字段元数据不一致。', 502)
        for key in ('description', 'definition', 'definition_version'):
            if key in item and not _text(item[key], empty=key == 'description'):
                _fail('SOURCE_METADATA_INVALID', '资源说明或固定查询版本不正确。', 502)
        if item['kind'] == 'saved_query' and (not item.get('definition_version') or not item.get('definition')):
            _fail('SOURCE_METADATA_INVALID', '固定查询必须提供可审阅的查询定义及定义版本。', 502)
        if item['key'] in allowed_objects:
            output.append({**item, 'columns': columns, 'primary_key': list(primary)})
    return sorted(output, key=lambda item: item['key'])


def _input_scalar(value):
    return value is None or type(value) in (bool, int) or (isinstance(value, float) and math.isfinite(value)) or (isinstance(value, str) and len(value) <= 1000 and '\x00' not in value)


def _validate_selection(selection, columns):
    names = {item['name'] if isinstance(item, dict) else item for item in columns}
    if not isinstance(selection, dict) or set(selection) - {'columns', 'filters', 'order_by', 'limit'}:
        _fail('SOURCE_QUERY_INVALID', '查询只能指定字段、条件、排序和数量。')
    selected = selection.get('columns')
    if not isinstance(selected, list) or not 1 <= len(selected) <= 200 or any(not isinstance(col, str) or col not in names for col in selected) or len(set(selected)) != len(selected):
        _fail('SOURCE_QUERY_INVALID', '查询字段必须来自已发现的资源且不能重复。')
    filters, orders, limit = selection.get('filters', []), selection.get('order_by', []), selection.get('limit', 50)
    if type(limit) is not int or not 1 <= limit <= 200 or not isinstance(filters, list) or len(filters) > 12 or not isinstance(orders, list) or len(orders) > 12:
        _fail('SOURCE_QUERY_INVALID', '查询最多支持12个条件、12项排序及200条记录。')
    for entry in filters:
        if not isinstance(entry, dict) or set(entry) - {'column', 'op', 'value'} or not isinstance(entry.get('column'), str) or entry['column'] not in names or entry.get('op') not in {*_OPERATORS, 'in', 'is_null'}:
            _fail('SOURCE_QUERY_INVALID', '筛选字段或运算符不受支持。')
        value = entry.get('value')
        if entry['op'] == 'in':
            if not isinstance(value, list) or not 1 <= len(value) <= 50 or not all(_input_scalar(cell) for cell in value):
                _fail('SOURCE_QUERY_INVALID', '集合条件须为1至50个简单值。')
        elif entry['op'] == 'is_null':
            if value is not None and type(value) is not bool:
                _fail('SOURCE_QUERY_INVALID', '空值条件只能使用布尔值。')
        elif 'value' not in entry or not _input_scalar(value):
            _fail('SOURCE_QUERY_INVALID', '筛选值须为长度不超过1000的字符串或简单标量。')
    for entry in orders:
        if not isinstance(entry, dict) or set(entry) != {'column', 'direction'} or not isinstance(entry['column'], str) or entry['column'] not in names or entry['direction'] not in ('asc', 'desc'):
            _fail('SOURCE_QUERY_INVALID', '排序字段或方向不正确。')
    _size(selection, 128 * 1024, 'SOURCE_QUERY_LIMIT')
    return {'columns': list(selected), 'filters': filters, 'order_by': orders, 'limit': limit}


def _scalar(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            _fail('SOURCE_INVALID_RESULT', '数据源返回了非有限数值。', 502)
        value = str(value)
    elif isinstance(value, (datetime, date, daytime)):
        value = value.isoformat()
    elif isinstance(value, timedelta):
        value = str(value)
    if value is not None and type(value) not in (str, int, float, bool):
        _fail('SOURCE_INVALID_RESULT', '数据源返回了非标量字段，请选择文本、数值或日期字段。', 502)
    if isinstance(value, float) and not math.isfinite(value):
        _fail('SOURCE_INVALID_RESULT', '数据源返回了非有限数值。', 502)
    _size(value, MAX_CELL_BYTES, 'SOURCE_CELL_LIMIT')
    return value


def _validate_result(result, columns, limit):
    if not isinstance(result, dict) or set(result) - {'contract_version', 'columns', 'rows', 'truncated', 'elapsed_ms'} or result.get('columns') != list(columns) or not isinstance(result.get('rows'), list) or len(result['rows']) > limit or type(result.get('truncated')) is not bool:
        _fail('SOURCE_INVALID_RESULT', '数据源查询返回的字段、记录数或结构不符合约定。', 502)
    elapsed = result.get('elapsed_ms')
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
        _fail('SOURCE_INVALID_RESULT', '数据源查询耗时格式不正确。', 502)
    rows = []
    for row in result['rows']:
        if not isinstance(row, dict) or set(row) != set(columns):
            _fail('SOURCE_INVALID_RESULT', '数据源返回了未请求的字段或缺失字段。', 502)
        rows.append({name: _scalar(row[name]) for name in columns})
    output = {'columns': list(columns), 'rows': rows, 'truncated': result['truncated'], 'elapsed_ms': elapsed}
    _size(output)
    return output


class _TLSRequiredConnection(pymysql.connections.Connection):
    def _request_authentication(self):
        # PyMySQL otherwise silently falls back to plaintext when the server
        # omits CLIENT.SSL. Reject before sending even the authentication packet.
        if not self.ssl or not self.server_capabilities & CLIENT.SSL:
            _fail('SOURCE_TLS_REQUIRED', 'MySQL服务器未提供所需TLS，连接已在认证前拒绝。', 403)
        return super()._request_authentication()


def _mysql_connection(credentials):
    options = dict(host=credentials['host'], port=credentials['port'], database=credentials['database'],
                   user=credentials['username'], password=credentials['password'], charset='utf8mb4',
                   cursorclass=SSDictCursor, connect_timeout=SOCKET_TIMEOUT, read_timeout=SOCKET_TIMEOUT,
                   write_timeout=SOCKET_TIMEOUT, autocommit=False, local_infile=False)
    if credentials.get('allow_insecure') is not True:
        ca = credentials.get('tls_ca')
        if ca is not None and (not isinstance(ca, str) or not Path(ca).is_absolute()):
            _fail('SOURCE_CREDENTIALS_INVALID', 'MySQL TLS证书须由部署管理员指定绝对路径。', 503)
        try:
            context = ssl.create_default_context(cafile=ca)
        except (OSError, ValueError, ssl.SSLError):
            _fail('SOURCE_TLS_INVALID', 'MySQL TLS证书配置不可用。', 503)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        options.update(ssl=context, ssl_verify_cert=True, ssl_verify_identity=True)
    else:
        options['ssl_disabled'] = True
    connection = (_TLSRequiredConnection if credentials.get('allow_insecure') is not True else pymysql.connect)(**options)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SET SESSION MAX_EXECUTION_TIME=5000')
            cursor.execute('START TRANSACTION READ ONLY')
        return connection
    except Exception:
        connection.close()
        raise


def _discover_mysql(config, credentials, connection=None, objects=None):
    owned = connection is None
    connection = connection or _mysql_connection(credentials)
    allowed = objects if objects is not None else config['allowed_objects']
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT TABLE_NAME object_name,TABLE_TYPE object_kind,LEFT(TABLE_COMMENT,16385) description FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME IN (' + ','.join('%s' for _ in allowed) + ') AND TABLE_TYPE IN (\'BASE TABLE\',\'VIEW\') ORDER BY TABLE_NAME LIMIT 101',
                           [config['database'], *allowed])
            objects = list(cursor.fetchall())
        if len(objects) > 100:
            _fail('SOURCE_METADATA_LIMIT', '允许访问的资源数量超过限制。', 502)
        resources = []
        for item in objects:
            with connection.cursor() as cursor:
                cursor.execute('SELECT COLUMN_NAME column_name,LEFT(COLUMN_TYPE,16385) data_type,IS_NULLABLE is_nullable,COLUMN_KEY column_key,LEFT(COLUMN_COMMENT,16385) column_comment FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION LIMIT 201',
                               (config['database'], item['object_name']))
                rows = list(cursor.fetchall())
            columns = [{'name': row['column_name'], 'type': row['data_type'], 'nullable': row['is_nullable'] == 'YES',
                        'primary_key': row['column_key'] == 'PRI', 'comment': row.get('column_comment') or ''} for row in rows]
            resource = {'key': item['object_name'], 'name': item['object_name'], 'kind': 'view' if item['object_kind'] == 'VIEW' else 'table',
                        'columns': columns, 'primary_key': [col['name'] for col in columns if col['primary_key']], 'description': item.get('description') or ''}
            if resource['kind'] == 'view':
                with connection.cursor() as cursor:
                    cursor.execute("SELECT SHA2(NULLIF(VIEW_DEFINITION,''),256) definition_version FROM information_schema.VIEWS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (config['database'], resource['key']))
                    definition = cursor.fetchone()
                if not definition or not definition.get('definition_version'):
                    _fail('SOURCE_VIEW_UNVERIFIABLE', '无法验证视图定义版本，请由源库管理员提供只读SHOW VIEW权限。', 403)
                resource['definition_version'] = definition['definition_version']
            resources.append(resource)
            _size(resources, code='SOURCE_METADATA_LIMIT')
        return _validate_resources(resources, config['allowed_objects'])
    finally:
        if owned:
            connection.close()


def _identifier(value):
    if not _text(value, max_bytes=1000) or any(ord(char) < 32 for char in value):
        _fail('SOURCE_QUERY_INVALID', '资源标识符不受支持。')
    return '`' + value.replace('`', '``') + '`'


def _query_mysql(config, credentials, resource, selection):
    if resource['kind'] not in ('table', 'view'):
        _fail('SOURCE_QUERY_INVALID', 'MySQL适配器仅查询允许访问的表或视图。')
    column_types = {col['name']: col['type'].split('(', 1)[0].split()[0].lower() for col in resource['columns']}
    used = {*selection['columns'], *(item['column'] for item in selection['filters']), *(item['column'] for item in selection['order_by'])}
    if any(column_types[name] not in _MYSQL_TYPES for name in used):
        _fail('SOURCE_COLUMN_TYPE', '查询不支持二进制、空间或未知字段类型，请选择文本、数值或日期字段。')
    columns = selection['columns']
    quoted = [_identifier(name) for name in columns]
    marker = '__ontology_source_cell_limit'
    while marker in columns:
        marker += '_'
    # Never receive an unbounded LONGTEXT/JSON cell into the Python driver.
    projections = [f'CASE WHEN OCTET_LENGTH(CAST({name} AS BINARY))>{MAX_CELL_BYTES} THEN NULL ELSE {name} END AS {name}' for name in quoted]
    lengths = ','.join(f'COALESCE(OCTET_LENGTH(CAST({name} AS BINARY)),0)' for name in quoted)
    size_expression = f'GREATEST({lengths})' if len(quoted) > 1 else lengths
    projections.append(f'({size_expression}>{MAX_CELL_BYTES}) AS {_identifier(marker)}')
    sql = 'SELECT ' + ','.join(projections) + ' FROM ' + _identifier(config['database']) + '.' + _identifier(resource['key'])
    where, arguments = [], []
    for entry in selection['filters']:
        name, op, value = _identifier(entry['column']), entry['op'], entry.get('value')
        if op == 'is_null':
            where.append(name + (' IS NOT NULL' if value is False else ' IS NULL'))
        elif op == 'in':
            where.append(name + ' IN (' + ','.join('%s' for _ in value) + ')')
            arguments.extend(value)
        elif value is None:
            if op not in ('eq', 'ne'):
                _fail('SOURCE_QUERY_INVALID', '空值仅支持等于、不等于或空值筛选。')
            where.append(name + (' IS NULL' if op == 'eq' else ' IS NOT NULL'))
        else:
            where.append(name + ' ' + _OPERATORS[op] + ' %s')
            arguments.append(value)
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    if selection['order_by']:
        sql += ' ORDER BY ' + ','.join(_identifier(item['column']) + ' ' + item['direction'].upper() for item in selection['order_by'])
    sql += ' LIMIT %s'
    arguments.append(selection['limit'] + 1)
    started = time.monotonic()
    connection = _mysql_connection(credentials)
    try:
        output, total_bytes, truncated = [], 0, False
        # Keep the table/view metadata lock through validation and SELECT. This
        # prevents a concurrent DDL from changing the meaning of approved fields.
        with connection.cursor() as cursor:
            try:
                cursor.execute('SELECT ' + ','.join(quoted) + ' FROM ' + _identifier(config['database']) + '.' + _identifier(resource['key']) + ' LIMIT 0')
            except pymysql.MySQLError as exc:
                if exc.args and exc.args[0] in (1054, 1146, 1356):
                    _fail('SOURCE_SCHEMA_DRIFT', '数据源结构已变化，请重新发现并审核映射。', 409)
                raise
        current = _discover_mysql(config, credentials, connection=connection, objects=[resource['key']])
        if not current or current[0] != resource:
            _fail('SOURCE_SCHEMA_DRIFT', '数据源结构或视图定义已变化，请重新发现并审核映射。', 409)
        cursor = connection.cursor()
        try:
            cursor.execute(sql, arguments)
            for row in cursor:
                if len(output) == selection['limit']:
                    truncated = True
                    break
                if row.pop(marker, 0):
                    _fail('SOURCE_CELL_LIMIT', '数据源单元格超过16KiB，请缩小查询字段范围。', 502)
                normalized = {name: _scalar(row[name]) for name in columns}
                total_bytes += _size(normalized)
                if total_bytes > MAX_JSON_BYTES:
                    _fail('SOURCE_RESULT_LIMIT', '数据源响应超过2MiB，请缩小查询范围。', 502)
                output.append(normalized)
        except BaseException:
            # SSCursor.close() otherwise drains all unread rows before closing.
            # On any failure terminate the socket first, then discard the cursor.
            with suppress(Exception):
                connection.close()
            with suppress(Exception):
                cursor.close()
            raise
        else:
            cursor.close()
        return _validate_result({'columns': columns, 'rows': output, 'truncated': truncated,
                                 'elapsed_ms': round((time.monotonic() - started) * 1000, 3)}, columns, selection['limit'])
    finally:
        with suppress(Exception):
            connection.close()


def _schema_node(node, root):
    seen = set()
    while isinstance(node, dict) and '$ref' in node:
        ref = node['$ref']
        if not isinstance(ref, str) or not ref.startswith('#/') or ref in seen:
            _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema引用不符合本地JSON Schema约定。', 502)
        seen.add(ref)
        node = root
        try:
            for part in ref[2:].split('/'):
                node = node[part.replace('~1', '/').replace('~0', '~')]
        except (KeyError, TypeError):
            _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema引用无法解析。', 502)
    return node


def _validate_structural_schema(schema):
    """Only bounded structural JSON Schema, never executable regex/combinators.

    SDK call_tool validates output synchronously. Restrict the peer's schema
    before that step, since an asyncio deadline cannot interrupt regex CPU work.
    """
    allowed = {'$defs', 'definitions', '$ref', '$schema', 'type', 'properties', 'required', 'items',
               'additionalProperties', 'title', 'description', 'default', 'enum', 'const',
               'minItems', 'maxItems', 'minLength', 'maxLength', 'minimum', 'maximum',
               'exclusiveMinimum', 'exclusiveMaximum', 'minProperties', 'maxProperties', 'anyOf', 'format'}
    visited = 0

    def walk(node, depth=0, trail=()):
        nonlocal visited
        visited += 1
        if visited > 2048 or depth > 20 or not isinstance(node, (dict, bool)):
            _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema层级、节点数或格式超过支持范围。', 502)
        if isinstance(node, bool):
            return
        if set(node) - allowed:
            _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema含不支持的正则或复杂验证规则。', 502)
        if '$ref' in node:
            ref = node['$ref']
            if not isinstance(ref, str) or not ref.startswith('#/') or ref in trail:
                _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema不允许外部引用或递归引用。', 502)
            walk(_schema_node({'$ref': ref}, schema), depth + 1, (*trail, ref))
        if 'type' in node:
            types = node['type'] if isinstance(node['type'], list) else [node['type']]
            if not 1 <= len(types) <= 7 or any(kind not in ('object', 'array', 'string', 'integer', 'number', 'boolean', 'null') for kind in types):
                _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema类型声明不受支持。', 502)
        for key in ('properties', '$defs', 'definitions'):
            if key in node:
                if not isinstance(node[key], dict) or len(node[key]) > 200:
                    _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema属性数量或格式不正确。', 502)
                for child in node[key].values():
                    walk(child, depth + 1, trail)
        for key in ('items', 'additionalProperties'):
            if key in node:
                walk(node[key], depth + 1, trail)
        if 'anyOf' in node:
            choices = node['anyOf']
            scalar_keys = {'type', 'enum', 'const', 'title', 'description'}
            if not isinstance(choices, list) or not 1 <= len(choices) <= 6 or any(not isinstance(child, dict) or set(child) - scalar_keys or child.get('type') not in ('string', 'integer', 'number', 'boolean', 'null') for child in choices):
                _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema仅支持简单标量联合类型。', 502)
            for child in choices:
                walk(child, depth + 1, trail)
        if 'required' in node and (not isinstance(node['required'], list) or len(node['required']) > 200 or any(not isinstance(item, str) for item in node['required'])):
            _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema必需字段格式不正确。', 502)
        if 'enum' in node and (not isinstance(node['enum'], list) or len(node['enum']) > 100 or any(type(item) not in (str, int, float, bool, type(None)) for item in node['enum'])):
            _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema枚举格式不受支持。', 502)
        if 'const' in node and type(node['const']) not in (str, int, float, bool, type(None)):
            _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema常量须为标量。', 502)
        if 'format' in node and node['format'] not in ('date', 'date-time', 'time', 'uuid', 'uri'):
            _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema格式校验不受支持。', 502)

    walk(schema)


def _validate_tool_schema(tool, operation):
    if hasattr(tool, 'model_dump'):
        tool = tool.model_dump(by_alias=True, exclude_none=True)
    if not isinstance(tool, dict) or tool.get('name') != 'ontology_source_' + operation:
        _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP服务须实现ontology-source/1约定的发现和查询工具。', 502)
    expected = {'inputSchema': {'contract_version': 'string'},
                'outputSchema': {'contract_version': 'string', 'resources': 'array'}}
    if operation == 'query':
        expected = {'inputSchema': {'contract_version': 'string', 'resource': 'string', 'selection': 'object'},
                    'outputSchema': {'contract_version': 'string', 'columns': 'array', 'rows': 'array', 'truncated': 'boolean', 'elapsed_ms': 'number'}}
    elif operation != 'discover':
        _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP操作不受支持。', 502)
    for key, fields in expected.items():
        schema = tool.get(key)
        if not isinstance(schema, dict):
            _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具须提供完整的输入和结构化输出schema。', 502)
        _size(schema, MAX_JSON_BYTES, 'SOURCE_METADATA_LIMIT')
        _validate_structural_schema(schema)
        node = _schema_node(schema, schema)
        if not isinstance(node, dict) or node.get('type') != 'object' or not isinstance(node.get('properties'), dict) or not isinstance(node.get('required'), list):
            _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema须声明对象、属性和必需字段。', 502)
        for field, kind in fields.items():
            prop = _schema_node(node['properties'].get(field), schema)
            kinds = (kind, 'integer') if kind == 'number' else (kind,)
            if field not in node['required'] or not isinstance(prop, dict) or prop.get('type') not in kinds:
                _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具schema缺少约定字段或字段类型不兼容。', 502)
            if field == 'contract_version' and ('const' in prop and prop['const'] != '1.0' or 'enum' in prop and '1.0' not in prop['enum']):
                _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具不支持约定版本1.0。', 502)
    return True


class _LimitedStream(httpx2.AsyncByteStream):
    def __init__(self, stream, budget):
        self.stream, self.budget = stream, budget

    async def __aiter__(self):
        count = 0
        async for chunk in self.stream:
            count += len(chunk)
            self.budget['bytes'] += len(chunk)
            if count > MAX_WIRE_BYTES or self.budget['bytes'] > 4 * MAX_WIRE_BYTES:
                error = SourceFailure('SOURCE_RESULT_LIMIT', 'MCP响应超过传输大小限制。', 502)
                self.budget['failure'] = error
                raise error
            yield chunk

    async def aclose(self):
        await self.stream.aclose()


class _LimitedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, budget):
        self.budget = budget
        self.transport = httpx2.AsyncHTTPTransport(verify=True, trust_env=False, retries=0)

    async def handle_async_request(self, request):
        response = await self.transport.handle_async_request(request)
        code, message, status = None, None, 502
        if response.status_code in (401, 403):
            code, message, status = 'SOURCE_UPSTREAM_AUTH', '源系统拒绝当前凭据或读取授权。', 403
        elif response.status_code == 429 or response.status_code >= 500:
            code, message, status = 'SOURCE_UPSTREAM_UNAVAILABLE', '源系统暂时不可用，请稍后重试。', 503
        elif 300 <= response.status_code < 400:
            code, message = 'MCP_REDIRECT_REJECTED', 'MCP端点不得重定向，请由管理员配置最终地址。'
        elif response.headers.get('content-encoding', 'identity').lower() not in ('', 'identity'):
            code, message = 'MCP_ENCODING_REJECTED', 'MCP端点须返回未压缩的受限响应。'
        else:
            length = response.headers.get('content-length')
            if length and (not length.isdigit() or int(length) > MAX_WIRE_BYTES):
                code, message = 'SOURCE_RESULT_LIMIT', 'MCP响应超过传输大小限制。'
        if code:
            await response.aclose()
            error = SourceFailure(code, message, status)
            self.budget['failure'] = error
            raise error
        return httpx2.Response(response.status_code, headers=response.headers,
                               stream=_LimitedStream(response.stream, self.budget), extensions=response.extensions)

    async def aclose(self):
        await self.transport.aclose()


async def _mcp_call(config, credentials, operation, arguments, budget, expected_resource=None):
    headers = {'Accept-Encoding': 'identity'}
    if credentials.get('token'):
        headers['Authorization'] = 'Bearer ' + credentials['token']
    async with asyncio.timeout(SESSION_TIMEOUT):
        async with httpx2.AsyncClient(headers=headers, transport=_LimitedTransport(budget),
                                     timeout=httpx2.Timeout(SOCKET_TIMEOUT), follow_redirects=False, trust_env=False) as client:
            async with streamable_http_client(config['endpoint'], http_client=client, max_sse_event_size=MAX_WIRE_BYTES) as streams:
                async with ClientSession(*streams, read_timeout_seconds=SOCKET_TIMEOUT) as session:
                    await session.initialize()
                    tools, cursor = {}, None
                    for _ in range(5):
                        from mcp import types
                        listing = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor) if cursor else None)
                        for tool in listing.tools:
                            if tool.name in tools:
                                _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具目录包含重复名称。', 502)
                            tools[tool.name] = tool
                        if len(tools) > 100:
                            _fail('SOURCE_METADATA_LIMIT', 'MCP工具目录超过100项。', 502)
                        cursor = listing.next_cursor
                        if not cursor:
                            break
                    else:
                        _fail('SOURCE_METADATA_LIMIT', 'MCP工具目录分页超过限制。', 502)
                    for op in ('discover', 'query'):
                        _validate_tool_schema(tools.get('ontology_source_' + op), op)
                    async def call(op, args):
                        response = await session.call_tool('ontology_source_' + op, args, read_timeout_seconds=SOCKET_TIMEOUT)
                        if getattr(response, 'is_error', False):
                            _fail('SOURCE_UPSTREAM_ERROR', 'MCP工具调用失败；请检查上游服务与数据源授权。', 502)
                        content = getattr(response, 'structured_content', None)
                        if not isinstance(content, dict) or content.get('contract_version') != '1.0':
                            _fail('MCP_PROFILE_INCOMPATIBLE', 'MCP工具须返回版本1.0的structuredContent。', 502)
                        _size(content)
                        return content

                    async def check_schema():
                        metadata = await call('discover', {'contract_version': '1.0'})
                        if set(metadata) != {'contract_version', 'resources'}:
                            _fail('SOURCE_METADATA_INVALID', 'MCP发现结果只应包含版本与资源目录。', 502)
                        resources = _validate_resources(metadata['resources'], config['allowed_objects'])
                        current = next((item for item in resources if item['key'] == arguments['resource']), None)
                        if current != expected_resource:
                            _fail('SOURCE_SCHEMA_DRIFT', 'MCP资源结构或固定查询定义已变化，请重新发现并审核映射。', 409)

                    if operation == 'query':
                        await check_schema()
                    result = await call(operation, arguments)
                    if operation == 'query':
                        await check_schema()
                    return result


def _exception_leaves(error):
    nested = getattr(error, 'exceptions', ())
    return [leaf for item in nested for leaf in _exception_leaves(item)] if nested else [error]


def _tls_failure(error):
    """Keep certificate failures distinct from operational outages for joins."""
    pending, seen = [error], set()
    while pending and len(seen) < 32:
        item = pending.pop()
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, ssl.SSLError):
            return True
        pending.extend(getattr(item, 'exceptions', ()))
        pending.extend((getattr(item, '__cause__', None), getattr(item, '__context__', None),
                        getattr(item, 'original_exception', None)))
    return False


def _mcp_operation(config, credentials, operation, arguments, expected_resource=None):
    budget = {'bytes': 0, 'failure': None}
    try:
        return asyncio.run(_mcp_call(config, credentials, operation, arguments, budget, expected_resource))
    except Exception as exc:
        if budget['failure'] is not None:
            raise budget['failure'] from None
        leaves = _exception_leaves(exc)
        for leaf in leaves:
            if isinstance(leaf, SourceFailure):
                raise leaf from None
        if _tls_failure(exc):
            raise SourceFailure('SOURCE_UPSTREAM_AUTH', 'MCP源系统安全连接验证失败。', 403) from None
        if any(isinstance(leaf, (TimeoutError, httpx2.TimeoutException)) or
               isinstance(leaf, MCPError) and leaf.code == REQUEST_TIMEOUT or
               'timeout' in type(leaf).__name__.lower() for leaf in leaves):
            raise SourceFailure('SOURCE_TIMEOUT', 'MCP请求超时，请缩小范围或检查上游服务。', 504) from None
        if any(isinstance(leaf, httpx2.NetworkError) for leaf in leaves):
            raise SourceFailure('SOURCE_UPSTREAM_UNAVAILABLE', 'MCP源系统连接暂时不可用。', 503) from None
        raise SourceFailure('SOURCE_UPSTREAM_ERROR', 'MCP连接或协议调用失败，请核对服务是否支持ontology-source/1。', 502) from None


def _boundary(operation):
    token = _REMOTE_LOG_SCOPE.set(True)
    try:
        return operation()
    except SourceFailure:
        raise
    except Exception as exc:
        code = exc.args[0] if isinstance(exc, pymysql.MySQLError) and exc.args else None
        if code in (1044, 1045, 1142, 1143, 1227, 2026) or _tls_failure(exc):
            raise SourceFailure('SOURCE_UPSTREAM_AUTH', '源系统拒绝凭据、读取授权或安全连接。', 403) from None
        if isinstance(exc, (TimeoutError, httpx2.TimeoutException)) or code in (3024, 1969):
            raise SourceFailure('SOURCE_TIMEOUT', '数据源请求超时，请缩小查询范围。', 504) from None
        if code in (2002, 2003, 2006, 2013) or isinstance(exc, (ConnectionError, httpx2.NetworkError)):
            raise SourceFailure('SOURCE_UPSTREAM_UNAVAILABLE', '源系统连接暂时不可用。', 503) from None
        raise SourceFailure('SOURCE_UPSTREAM_ERROR', '数据源连接或查询失败，请检查上游服务和只读授权。', 502) from None
    finally:
        _REMOTE_LOG_SCOPE.reset(token)


def discover(source):
    def operation():
        config = validate_config(source.get('kind'), source.get('config'))
        credentials = _resolve_credentials(source)
        if source['kind'] == 'mysql':
            return _discover_mysql(config, credentials)
        result = _mcp_operation(config, credentials, 'discover', {'contract_version': '1.0'})
        if set(result) != {'contract_version', 'resources'}:
            _fail('SOURCE_METADATA_INVALID', 'MCP发现结果只应包含版本与资源目录。', 502)
        return _validate_resources(result['resources'], config['allowed_objects'])
    return _boundary(operation)


def query(source, resource, selection):
    def operation():
        config = validate_config(source.get('kind'), source.get('config'))
        credentials = _resolve_credentials(source)
        if not isinstance(resource, dict) or resource.get('key') not in config['allowed_objects']:
            _fail('SOURCE_RESOURCE_DENIED', '资源不在数据源允许访问范围内。', 403)
        normalized = _validate_resources([{key: value for key, value in resource.items() if key in _RESOURCE_KEYS}], config['allowed_objects'])[0]
        selected = _validate_selection(selection, normalized['columns'])
        if source['kind'] == 'mysql':
            return _query_mysql(config, credentials, normalized, selected)
        result = _mcp_operation(config, credentials, 'query', {'contract_version': '1.0', 'resource': normalized['key'], 'selection': selected}, normalized)
        return _validate_result(result, selected['columns'], selected['limit'])
    return _boundary(operation)
