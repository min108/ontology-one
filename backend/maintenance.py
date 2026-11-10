"""Shared-volume maintenance exclusion for API/worker processes and offline tools.

All instances must use the same storage root and honor this lock. This is a
single-host/shared-filesystem guard, not a distributed availability protocol.
"""
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
import os
from pathlib import Path
from .platform_io import secure_open, file_lock


_mode = ContextVar('ontology_maintenance_lock_mode', default=None)


def require_maintenance():
    if _mode.get() != 'exclusive':
        raise RuntimeError('恢复操作必须持有独占离线维护锁。')


@contextmanager
def runtime_guard(shared=True):
    root = Path(os.environ.get('ONTOLOGY_STORAGE_ROOT', str(Path(__file__).resolve().parents[1] / 'data' / 'tenants'))).absolute()
    with ExitStack() as stack:
        try:
            stream = stack.enter_context(secure_open(root.parent / '.ontology-maintenance.lock', 'a+b', create_parents=True))
            stack.enter_context(file_lock(stream, shared=shared, blocking=False))
        except BlockingIOError as exc:
            message = '工作区正在维护，服务暂不能启动。' if shared else '平台或任务进程仍在运行；请停止共享此存储卷的所有 API 与 worker 后维护。'
            raise RuntimeError(message) from exc
        except OSError as exc:
            raise RuntimeError('无法安全打开维护锁；请使用当前用户拥有的本地安全存储目录。') from exc
        token = _mode.set('shared' if shared else 'exclusive')
        try:
            yield
        finally:
            _mode.reset(token)
