"""Tenant-scoped runtime storage. Repository files are never data sources."""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import RLock

from fastapi import HTTPException
from .security.context import require_context
from . import platform_io

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _tenant_id():
    ident = str(require_context().tenant_id)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ident):
        raise HTTPException(403, '无效的机构存储上下文。')
    return ident


def _reject_symlinks(path):
    try:
        platform_io.reject_reparse_points(path)
    except OSError as exc:
        raise HTTPException(403, '不允许通过符号链接、目录联接或重解析点访问工作区文件。') from exc


def workspace_root(*, create=False):
    """Derive the workspace from the authenticated context on every call."""
    base = Path(os.environ.get('ONTOLOGY_STORAGE_ROOT', str(REPOSITORY_ROOT / 'data' / 'tenants'))).absolute()
    path = base / _tenant_id()
    _reject_symlinks(path)
    if create:
        try:
            platform_io.ensure_private_directory(path)
        except OSError as exc:
            raise HTTPException(403, '无法建立当前用户专用的安全工作区目录。') from exc
    return path


def _relative(value):
    raw = str(value)
    if not raw or '\x00' in raw or '\\' in raw or PureWindowsPath(raw).drive:
        raise HTTPException(403, '无效的工作区文件路径。')
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in ('..', '.') for part in raw.split('/')):
        raise HTTPException(403, '文件路径必须位于当前机构工作区内。')
    return path


def scoped_path(relative, *, allowed=('data/uploads',), create_parent=False, must_exist=False):
    part = _relative(relative)
    if not any(part == PurePosixPath(prefix) or PurePosixPath(prefix) in part.parents for prefix in allowed):
        raise HTTPException(403, '文件不属于可访问的工作区目录。')
    root = workspace_root(create=create_parent)
    path = root.joinpath(*part.parts)
    _reject_symlinks(path)
    if not path.is_relative_to(root):
        raise HTTPException(403, '禁止跨机构访问文件。')
    if create_parent:
        try:
            platform_io.ensure_private_directory(path.parent)
        except OSError as exc:
            raise HTTPException(403, '无法建立安全资料目录。') from exc
    if must_exist and not path.is_file():
        raise HTTPException(404, '当前机构资料库中没有该原件。')
    return path


def upload_path(name, category='', *, create_parent=False, must_exist=False):
    if not name or Path(name).name != name or '/' in name or '\\' in name:
        raise HTTPException(403, '无效的原件名称。')
    suffix = (category.strip('/') + '/') if category else ''
    return scoped_path('data/uploads/' + suffix + name, create_parent=create_parent, must_exist=must_exist)


def uploads_dir(category=''):
    suffix = '/' + category.strip('/') if category else ''
    path = scoped_path('data/uploads' + suffix, create_parent=True)
    try:
        platform_io.ensure_private_directory(path)
    except OSError as exc:
        raise HTTPException(403, '无法建立安全上传目录。') from exc
    return path


def stored_path(path):
    root = workspace_root()
    _reject_symlinks(Path(path))
    try:
        relative = Path(path).absolute().relative_to(root).as_posix()
    except ValueError as exc:
        raise HTTPException(403, '不能记录其他机构或服务器文件路径。') from exc
    scoped_path(relative, allowed=('data/uploads', 'data/runtime'))
    return relative


def assert_storage_write():
    from .jobs import assert_execution_principal_current
    return assert_execution_principal_current(require_context(), require_write=True)


@contextmanager
def open_file(path, mode='rb', *, require_private=False):
    """Authorize tenant scope, then pin directories and open the verified object."""
    stored_path(path)
    write = any(c in mode for c in 'wax+')
    if write:
        assert_storage_write()
    try:
        with platform_io.secure_open(path, mode, require_private=require_private) as stream:
            yield stream
            if write:
                assert_storage_write()
    except OSError as exc:
        raise HTTPException(403, '无法安全访问当前机构文件。') from exc


def write_bytes(path, data, *, overwrite=True):
    stored_path(path)
    assert_storage_write()
    try:
        return platform_io.atomic_write(path, data, overwrite=overwrite, before_commit=assert_storage_write)
    except FileExistsError:
        raise
    except OSError as exc:
        raise HTTPException(403, '无法安全写入当前机构文件。') from exc


def read_bytes(path):
    with open_file(path, 'rb') as stream:
        return stream.read()


class ScopedActivitySet:
    """Process-local interactive locks never collide across institutions."""
    def __init__(self):
        self._values = set()
        self._lock = RLock()

    def _key(self, value):
        return (_tenant_id(), value)

    def __contains__(self, value):
        with self._lock:
            return self._key(value) in self._values

    def add(self, value):
        with self._lock:
            self._values.add(self._key(value))

    def discard(self, value):
        with self._lock:
            self._values.discard(self._key(value))


def download_response(path, filename, media_type='application/octet-stream'):
    """Open the authorized file now; stream that descriptor, never re-open a path."""
    from fastapi.responses import StreamingResponse
    from urllib.parse import quote
    handle = open_file(path, 'rb')
    stream = handle.__enter__()
    def chunks():
        try:
            while data := stream.read(256 * 1024):
                yield data
        finally:
            handle.__exit__(None, None, None)
    return StreamingResponse(chunks(), media_type=media_type,
        headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(str(filename)),
                 'X-Content-Type-Options': 'nosniff'})
