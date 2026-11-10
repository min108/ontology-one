"""Offline same-institution restore into an empty, directory-owned workspace.

Stop every API and worker sharing the deployment's storage volume first.
  python scripts/restore_backup.py backup.zip --tenant-id sub-01 --maintenance \
      --reason '灾后恢复'
Credentials are prompted interactively. A database name or password cannot be
provided as a command-line argument. Control-plane data is never restored.
"""
from __future__ import annotations
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import db
from backend.maintenance import runtime_guard, require_maintenance
from backend.security import store
from backend.security.context import use_context
from backend.storage import workspace_root, scoped_path

SNAPSHOT = 'data/mysql-workspace.json'
MAX_ARCHIVE_BYTES = 2 * 1024**3
MAX_SNAPSHOT_BYTES = 128 * 1024**2
MAX_MEMBER_BYTES = 512 * 1024**2
MAX_MEMBERS = 20_000


class RestoreError(RuntimeError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RestoreError('JSON 包含重复键。')
        result[key] = value
    return result


def _load_json(stream):
    return json.load(stream, object_pairs_hook=_unique_object,
                     parse_constant=lambda _: (_ for _ in ()).throw(RestoreError('JSON 包含非法数值。')))


def _parts(body):
    """Split trusted CREATE TABLE column definitions outside nested parentheses."""
    depth, quote, start = 0, None, 0
    for i, char in enumerate(body):
        if quote:
            if char == quote and (i == 0 or body[i - 1] != '\\'):
                quote = None
        elif char in "'\"`":
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif char == ',' and depth == 0:
            yield body[start:i].strip()
            start = i + 1
    yield body[start:].strip()


def schema_columns():
    """Build the allowlist from executable application schema, never the archive."""
    class Collector:
        def __init__(self): self.statements = list(db.SCHEMA)
        def execute(self, statement, params=()): self.statements.append(statement)
        def executescript(self, script): self.statements.extend(db.split_script(script))
    collector = Collector()
    from backend.datasets import ensure_schema
    from backend.provenance import initialize_provenance
    from backend.special_projects import init_special_schema
    from backend.sources import ensure_schema as ensure_source_schema
    ensure_schema(collector)
    initialize_provenance(collector)
    init_special_schema(collector)
    ensure_source_schema(collector)
    result = {}
    for sql in collector.statements:
        match = re.search(r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([a-z_]+)\s*\((.*)\)\s*ENGINE', sql, re.I | re.S)
        if not match:
            continue
        columns = []
        for definition in _parts(match[2]):
            name = definition.split(None, 1)[0].strip('`')
            if name.upper() in {'PRIMARY', 'UNIQUE', 'KEY', 'INDEX', 'CONSTRAINT', 'FOREIGN', 'CHECK'} or '(' in name:
                continue
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
                raise RestoreError('应用表结构无法识别，恢复已停止。')
            columns.append(name)
        result[match[1]] = frozenset(columns)
    if not {'entities', 'relations', 'entity_ownership'} <= result.keys():
        raise RestoreError('应用表结构不完整。')
    return result


def validate_archive(archive):
    names, total = set(), 0
    if len(archive.infolist()) > MAX_MEMBERS:
        raise RestoreError('备份成员数超过恢复上限。')
    for info in archive.infolist():
        raw = info.filename
        relative = PurePosixPath(raw)
        if '\x00' in raw or '\\' in raw or relative.is_absolute() or PureWindowsPath(raw).drive or any(p in ('.', '..', '') for p in raw.rstrip('/').split('/')):
            raise RestoreError('备份包含不安全的文件路径。')
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
            raise RestoreError('备份不允许符号链接或特殊文件。')
        if raw in names:
            raise RestoreError('备份包含重复文件路径。')
        names.add(raw)
        total += info.file_size
        if info.file_size > MAX_MEMBER_BYTES or total > MAX_ARCHIVE_BYTES:
            raise RestoreError('备份解压大小超过恢复上限。')
        if info.is_dir():
            if raw not in ('data/', 'data/uploads/') and not raw.startswith('data/uploads/'):
                raise RestoreError('备份包含不允许的目录。')
        elif raw not in (SNAPSHOT, 'backup-info.json') and not raw.startswith('data/uploads/'):
            raise RestoreError('备份仅可包含机构业务快照和上传原件。')
        if raw == SNAPSHOT and info.file_size > MAX_SNAPSHOT_BYTES:
            raise RestoreError('业务快照超过128 MiB恢复上限。')
    if SNAPSHOT not in names:
        raise RestoreError('备份缺少机构业务快照。')


def _validate_embedded(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in {'source_path', 'file_path', 'original_path'} and isinstance(item, str) and item:
                path = PurePosixPath(item)
                if '\\' in item or path.is_absolute() or PureWindowsPath(item).drive or '..' in path.parts or not item.startswith('data/uploads/'):
                    raise RestoreError('业务快照包含工作区外部文件引用。')
            _validate_embedded(item)
    elif isinstance(value, list):
        for item in value: _validate_embedded(item)


def validate_snapshot(snapshot, tenant_id, allowed=None):
    if not isinstance(snapshot, dict) or snapshot.get('format') != 'ontology-mysql-workspace-v1' or snapshot.get('database') != 'MySQL':
        raise RestoreError('不支持的 MySQL 工作区快照。')
    if snapshot.get('source_tenant') != tenant_id:
        raise RestoreError('备份来源机构与目标机构不一致；不允许跨机构恢复或导入无机构标识的旧备份。')
    tables = snapshot.get('tables')
    allowed = allowed or schema_columns()
    if not isinstance(tables, dict) or 'entities' not in tables:
        raise RestoreError('备份缺少业务实体表。')
    if set(tables) - allowed.keys():
        raise RestoreError('备份包含非业务白名单数据表，控制库和未知表不可导入。')
    for table, rows in tables.items():
        if not isinstance(rows, list):
            raise RestoreError('备份数据表须为记录列表。')
        for row in rows:
            if not isinstance(row, dict) or set(row) != allowed[table]:
                raise RestoreError(f'表 {table} 的字段与当前应用结构不一致。')
            if any(not isinstance(v, (str, int, float, bool, type(None))) for v in row.values()):
                raise RestoreError('表字段包含非标量值。')
            if table == 'entity_ownership' and row['tenant_id'] != tenant_id:
                raise RestoreError('实体所有权记录指向其他机构。')
            if table in ('entities', 'dataset_rows', 'dataset_import_receipts') and row.get('org_id') not in (None, '', tenant_id):
                raise RestoreError('业务记录的归属机构与目标机构不一致。')
            if table == 'entities' and row['id'] == tenant_id and (row['kind'] != 'organization' or row['source_kind'] != 'directory'):
                raise RestoreError('备份不得冒充或覆盖受管理的机构根节点。')
            for field in ('payload', 'snapshot', 'metadata', 'result', 'values_json'):
                if isinstance(row.get(field), str) and row[field].lstrip().startswith(('{', '[')):
                    try: _validate_embedded(json.loads(row[field]))
                    except json.JSONDecodeError as exc: raise RestoreError('快照内嵌JSON无效。') from exc
    ids = {r['id'] for r in tables['entities']}
    if len(ids) != len(tables['entities']):
        raise RestoreError('备份包含重复实体编号。')
    for row in tables.get('relations', []):
        if row['source'] not in ids | {tenant_id} or row['target'] not in ids | {tenant_id}:
            raise RestoreError('备份关系包含不存在的端点。')
    return tables


def _empty_workspace(connection, tenant_id, allowed, *, lock=False):
    existing = {row[0] for row in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE() AND table_type='BASE TABLE'")}
    if existing - allowed.keys():
        raise RestoreError('目标库包含当前应用无法识别的表，拒绝恢复。')
    for table in existing:
        rows = connection.execute(f'SELECT * FROM `{table}`' + (' FOR UPDATE' if lock else '')).fetchall()
        if table == 'entities':
            if any(row['id'] != tenant_id or row['kind'] != 'organization' or row['source_kind'] != 'directory' for row in rows):
                raise RestoreError('目标机构已有业务实体，禁止覆盖。')
        elif table == 'workspace_meta':
            if any(row['meta_key'] != 'schema_version' for row in rows):
                raise RestoreError('目标机构已有工作区状态，禁止覆盖。')
        elif rows:
            raise RestoreError(f'目标机构已有业务记录（{table}），禁止覆盖。')


def restore_snapshot(snapshot, archive, principal, reason):
    """Caller must hold runtime_guard(shared=False) for the entire operation."""
    require_maintenance()
    principal = store.require_admin(principal)
    tenant_id = principal.tenant_id
    if principal.home_tenant_id != tenant_id and not principal.is_superadmin:
        raise RestoreError('仅本机构管理员或总部平台管理员可恢复此机构。')
    allowed = schema_columns()
    tables = validate_snapshot(snapshot, tenant_id, allowed)
    validate_archive(archive)
    tenant = store.get_tenant(tenant_id)
    if not tenant or tenant['db_name'] != principal.database_name or tenant['status'] != 'active':
        raise RestoreError('服务端机构目录或工作区授权已变化。')
    # Refuse occupied workspaces before any schema initialization.
    try:
        with closing(db._raw_connection(tenant['db_name'])) as raw:
            _empty_workspace(db.Connection(raw), tenant_id, allowed)
    except Exception as exc:
        if not getattr(exc, 'args', None) or exc.args[0] != 1049:
            raise
    db.ensure_tenant_schema(tenant_id)
    with use_context(principal):
        root = workspace_root(create=True)
        uploads = scoped_path('data/uploads', create_parent=True)
        if uploads.exists() and (not uploads.is_dir() or any(uploads.iterdir())):
            raise RestoreError('目标机构已保存上传原件，禁止覆盖。')
        installed = False
        committed = False
        with tempfile.TemporaryDirectory(prefix='.restore-stage-', dir=root) as temp:
            staging = Path(temp) / 'uploads'
            staging.mkdir(mode=0o700)
            files = 0
            for info in archive.infolist():
                if info.is_dir() or not info.filename.startswith('data/uploads/'):
                    continue
                relative = PurePosixPath(info.filename).relative_to('data/uploads')
                target = staging.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with archive.open(info) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst, 256 * 1024)
                target.chmod(0o600)
                files += 1
            store.append_audit(principal, 'workspace_restore', tenant_id, 'started', {'reason': reason, 'tables': len(tables), 'files': files})
            try:
                with closing(db.connect()) as connection:
                    connection.begin()
                    _empty_workspace(connection, tenant_id, allowed, lock=True)
                    # Compare against the actual provisioned schema as well as the source registry.
                    actual = {}
                    for row in connection.execute('SELECT table_name,column_name FROM information_schema.columns WHERE table_schema=DATABASE()'):
                        actual.setdefault(row[0], set()).add(row[1])
                    for table in tables:
                        if actual.get(table) != set(allowed[table]):
                            raise RestoreError(f'目标表 {table} 结构与应用定义不一致。')
                    count = 0
                    # Sources have dependencies on their registry/resources and
                    # on ontology assets. Restore metadata in dependency order.
                    source_order = ['source_connections', 'source_resources', 'source_schema_versions',
                                    'source_mappings', 'source_mapping_versions', 'source_grants']
                    ordered_tables = sorted(tables, key=lambda name: (1, source_order.index(name))
                                            if name in source_order else (0, name))
                    for table in ordered_tables:
                        rows = tables[table]
                        if table == 'workspace_meta':
                            # Deployment schema markers belong to the current program.
                            continue
                        selected = [row for row in rows if not (table == 'entities' and row['id'] == tenant_id)]
                        columns = sorted(allowed[table])
                        names = ','.join('`' + name + '`' for name in columns)
                        marks = ','.join('?' for _ in columns)
                        for offset in range(0, len(selected), 500):
                            connection.executemany(f'INSERT INTO `{table}`({names}) VALUES({marks})',
                                                   [[row[name] for name in columns] for row in selected[offset:offset + 500]])
                        count += len(selected)
                    # Restoring a catalog never silently reopens an external
                    # system. An administrator must re-enable and rediscover it.
                    if 'source_connections' in tables:
                        connection.execute('UPDATE source_connections SET enabled=0,version=version+1')
                    if 'source_grants' in tables:
                        connection.execute('DELETE FROM source_grants')
                    store.assert_principal_current(principal, require_write=True)
                    if uploads.exists(): uploads.rmdir()
                    os.rename(staging, uploads)
                    installed = True
                    try:
                        connection.commit()
                        committed = True
                    except Exception:
                        connection.rollback()
                        raise
                store.append_audit(principal, 'workspace_restore', tenant_id, 'success', {'reason': reason, 'rows': count, 'files': files})
                return {'tenant_id': tenant_id, 'rows': count, 'files': files}
            except Exception as exc:
                if committed:
                    raise RestoreError('业务数据及原件已恢复，但完成审计记录写入失败；请检查控制库，勿重复恢复。') from exc
                if installed:
                    # The restore owns this newly installed directory; existing uploads
                    # were rejected before staging and cannot be removed here.
                    shutil.rmtree(uploads)
                store.append_audit(principal, 'workspace_restore', tenant_id, 'failed', {'reason': reason})
                raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='离线恢复同一机构工作区备份；禁止覆盖已有业务。')
    parser.add_argument('backup', type=Path)
    parser.add_argument('--tenant-id', required=True, help='服务端机构目录ID，必须等于备份source_tenant。')
    parser.add_argument('--username', help='平台管理员账号；省略时交互输入。')
    parser.add_argument('--maintenance', action='store_true', help='确认所有 API 与后台 worker 已停止。')
    parser.add_argument('--reason', required=True, help='写入只追加审计的恢复原因。')
    args = parser.parse_args(argv)
    if not args.maintenance:
        parser.error('请停止所有 API/worker，并使用 --maintenance 确认维护模式。')
    if not 2 <= len(args.reason.strip()) <= 500:
        parser.error('恢复原因须为2至500字符。')
    principal = None
    try:
        with runtime_guard(shared=False), zipfile.ZipFile(args.backup) as archive:
            validate_archive(archive)
            with archive.open(SNAPSHOT) as stream:
                snapshot = _load_json(stream)
            validate_snapshot(snapshot, args.tenant_id)
            username = args.username or input('平台用户名: ').strip()
            password = getpass.getpass('平台密码（不回显）: ')
            raw = store.login(username, password, 'offline-local', 'ontology-offline-restore', 'offline-restore-' + os.urandom(8).hex())
            del password
            principal = store.authenticate_session(raw, 'offline-restore-' + os.urandom(8).hex())[0]
            store.require_admin(principal)
            if args.tenant_id != principal.home_tenant_id and not principal.is_superadmin:
                raise RestoreError('不能恢复其他机构。')
            if args.tenant_id != principal.tenant_id:
                store.switch_workspace(principal, args.tenant_id)
                principal = store.authenticate_session(raw, principal.request_id)[0]
            result = restore_snapshot(snapshot, archive, principal, args.reason.strip())
            print(f"恢复完成：机构 {result['tenant_id']}；业务记录 {result['rows']:,} 行；上传原件 {result['files']:,} 个。")
            print('控制库账号、授权、审计、异步队列及模型密钥未从备份导入。可以重新启动平台。')
    except Exception as exc:
        detail = getattr(exc, 'detail', None) or str(exc)
        raise SystemExit('恢复未完成：' + detail) from exc
    finally:
        if principal:
            try: store.revoke_session(principal, principal.session_id)
            except Exception: pass


if __name__ == '__main__':
    main()
