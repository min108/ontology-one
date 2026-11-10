"""Shared-volume maintenance exclusion for API/worker processes and offline tools.

All instances must use the same storage root and honor this lock. This is a
single-host/shared-filesystem guard, not a distributed availability protocol.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path


_mode = ContextVar('ontology_maintenance_lock_mode', default=None)


def require_maintenance():
    if _mode.get() != 'exclusive':
        raise RuntimeError('恢复操作必须持有独占离线维护锁。')


@contextmanager
def runtime_guard(shared=True):
    try:
        import fcntl
    except ImportError as exc:
        raise RuntimeError('本版本维护锁需要支持 flock 的 Linux/Unix 运行环境。') from exc
    root = Path(os.environ.get('ONTOLOGY_STORAGE_ROOT', str(Path(__file__).resolve().parents[1] / 'data' / 'tenants'))).absolute()
    parent = root.parent
    for path in (parent, *parent.parents):
        if path.is_symlink():
            raise RuntimeError('维护锁目录不能通过符号链接访问。')
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(parent / '.ontology-maintenance.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            message = '工作区正在维护，服务暂不能启动。' if shared else '平台或任务进程仍在运行；请停止共享此存储卷的所有 API 与 worker 后维护。'
            raise RuntimeError(message) from exc
        token = _mode.set('shared' if shared else 'exclusive')
        try:
            yield
        finally:
            _mode.reset(token)
    finally:
        os.close(descriptor)
