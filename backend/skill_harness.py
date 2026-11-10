"""Tenant-governed authoring through the pinned official DeepSeek Harness SDK.

No model can choose plugins, executables, identity, source credentials or SQL.
The only authoring tools read a bounded draft and propose changes in memory.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress, contextmanager
import ctypes
from functools import lru_cache
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import sys
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit
import uuid

import anyio
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from .security.context import require_context
from .security.store import assert_principal_current, append_audit
from .storage import scoped_path, read_bytes, write_bytes, open_file

router = APIRouter(prefix='/skills/harness', tags=['技能开发 Harness'])
SDK_VERSION = '0.2.1rc2'
DEFAULT_BASE_URL = 'https://api.deepseek.com/anthropic'
DEFAULT_MODEL = 'deepseek-v4-flash'
MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 2 * 1024 * 1024 + 256 * 1024
MAX_DRAFT_BYTES = 512 * 1024
DEFAULT_TIMEOUT = 90
SDK_HASHES = {
    '__init__.py': '0511648a19b7f0e639f2dd4fe2cb972039c8e794be57546422ac519004992e4b',
    'api.py': '7ff562d094f04390c5c6d05977c74282416d9bbf3604031ffc8fcdc69897aa61',
    'client.py': 'bc9eef92d7a2da4bd3175db4b99b23f8430f225efca8e0f6176573e396702a66',
    'errors.py': '0d380a8947c7cc64dc1811660816b89fe676fac46b3cfcf18a51ecafc6bf28da',
    'models.py': 'c8cba1cdf77017506ab5a01e53c3c8e5b36bfe941cc3e51d40580cd9407a35be',
    'office.py': '300d51a882f321d4f417b0c1b9d3ab8e04455e6c8533da858d1d57e8ed30018a',
}
RUNTIME_SHA256 = 'b6f70327db1392b4ff879b8be26c87349b14933c1b4f7a90b391446bb005aae8'
WINDOWS_RUNTIME_SHA256 = 'f9ed01f241df37c50c702d81b6c226d576210b8f3947699735ca90285ea13365'
SAFE_ERRORS = {
    'HARNESS_NOT_CONFIGURED': (409, '请先由本机构管理员配置技能开发的 Harness 模型接入。'),
    'HARNESS_UNAVAILABLE': (503, '受控 Harness 运行时或内存工作区暂不可用，请检查部署配置。'),
    'HARNESS_BUSY': (429, '技能开发正在处理其他请求，请稍后重试。'),
    'HARNESS_TIMEOUT': (504, '技能生成超过时限，运行已终止；请缩小任务后重试。'),
    'HARNESS_LIMIT': (413, '技能上下文或模型结果超过限制，请缩小文件或任务范围。'),
    'HARNESS_FAILED': (502, 'Harness 未完成技能生成，请核对模型配置、连接和任务范围。'),
    'HARNESS_INCOMPLETE': (502, 'Harness 未完成有效提案，请缩小任务或调整说明后重试。'),
    'HARNESS_CONFIG_CHANGED': (409, '模型配置在运行期间已变化，本次结果未返回，请重新发起。'),
    'HARNESS_NO_PROPOSAL': (422, '模型尚未形成可采纳的技能提案，请补充业务条件或调整任务要求。'),
    'HARNESS_OUTPUT_LIMIT': (502, '模型输出达到上限，提案未完成；请缩小本次修改范围。'),
    'HARNESS_CANCELLED': (409, '技能生成已取消，提案未保存。'),
    'HARNESS_PROVIDER_AUTH': (502, '模型服务拒绝认证，请由管理员核对密钥及模型使用权限。'),
    'HARNESS_PROVIDER_QUOTA': (502, '模型账户额度不足，请由管理员核对账户余额或配额。'),
    'HARNESS_PROVIDER_RATE_LIMIT': (429, '模型服务触发调用限流，请稍后重试。'),
    'HARNESS_PROVIDER_REQUEST': (502, '模型服务不接受当前请求，请核对模型名称、接口协议与工具支持。'),
    'HARNESS_PROVIDER_UNAVAILABLE': (503, '模型服务暂不可用或连接中断，本次提案未保存。'),
    'HARNESS_PROVIDER_RESPONSE': (502, '模型服务响应不符合受控调用协议，本次结果未采纳。'),
}


def _error(code):
    status, message = SAFE_ERRORS.get(code, SAFE_ERRORS['HARNESS_FAILED'])
    return HTTPException(status, {'code': code if code in SAFE_ERRORS else 'HARNESS_FAILED', 'message': message})


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def _loads(raw):
    def invalid(_):
        raise ValueError()
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError()
            out[key] = value
        return out
    return json.loads(raw, parse_constant=invalid, object_pairs_hook=unique)


async def _finish_owned_cleanup(task):
    """Never abandon owned cleanup on a second cancellation; report it after."""
    cancelled = False
    with anyio.CancelScope(shield=True):
        while True:
            try:
                await asyncio.shield(task)
                return cancelled
            except asyncio.CancelledError:
                cancelled = True
                if task.done():
                    task.result()
                    return cancelled


def _base_url(value):
    try:
        parts = urlsplit(value.strip())
        # Allow only explicit deployment opt-in to loopback mock endpoints.
        testing = os.getenv('ONTOLOGY_HARNESS_ALLOW_LOOPBACK_TEST') == '1'
        test_http = testing and parts.scheme == 'http' and parts.hostname in {'127.0.0.1', '::1'}
        if (not parts.hostname or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or (parts.scheme != 'https' and not test_http)
                or any(ord(char) < 33 for char in value) or '\\' in value):
            raise ValueError()
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError()
        return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/'), '', ''))
    except (TypeError, ValueError):
        raise ValueError('请配置有效的 HTTPS Messages 服务地址。') from None


class HarnessConfiguration(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    base_url: str = Field(default=DEFAULT_BASE_URL, min_length=1, max_length=1000)
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_key: StrictBool = False

    @field_validator('base_url')
    @classmethod
    def endpoint(cls, value):
        return _base_url(value)

    @field_validator('model')
    @classmethod
    def model_identifier(cls, value):
        value = value.strip()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}', value):
            raise ValueError('请填写有效模型标识。')
        return value

    @field_validator('api_key')
    @classmethod
    def key(cls, value):
        if value is None or value == '':
            return None
        if len(value) < 8 or value != value.strip() or any(ord(char) < 33 for char in value):
            raise ValueError('模型密钥格式无效。')
        return value

    @model_validator(mode='after')
    def unambiguous_key(self):
        if self.clear_key and self.api_key:
            raise ValueError('清除与设置密钥不能同时操作。')
        return self


def _principal(principal=None, *, admin=False, build=False):
    principal = principal or require_context()
    if (principal.identity_kind != 'session' or principal.must_change_password
            or (admin and principal.role != 'admin')
            or (build and principal.role not in {'admin', 'modeler'})
            or ((admin or build) and principal.readonly)):
        raise HTTPException(403, '当前身份无权管理或使用技能开发模型。')
    return assert_principal_current(principal, require_write=admin or build)


def _config_path(*, create=False):
    return scoped_path('data/runtime/harness.json', allowed=('data/runtime',), create_parent=create)


def _configuration():
    path = _config_path()
    if not path.exists():
        return {'base_url': DEFAULT_BASE_URL, 'model': DEFAULT_MODEL, 'api_key': '', 'revision': '', 'verified': False}
    try:
        if sys.platform == 'win32':
            with open_file(path, 'rb', require_private=True) as stream:
                raw = stream.read(16385)
        else:
            if path.stat().st_mode & 0o077:
                raise ValueError()
            raw = read_bytes(path)
        if len(raw) > 16384:
            raise ValueError()
        value = _loads(raw)
        checked = HarnessConfiguration.model_validate({key: value[key] for key in ('base_url', 'model', 'api_key')})
        return {'base_url': checked.base_url, 'model': checked.model, 'api_key': checked.api_key or '',
                'revision': str(value['revision']), 'verified': value.get('verified') is True}
    except (ValueError, KeyError, TypeError, OSError):
        raise _error('HARNESS_UNAVAILABLE') from None


@contextmanager
def _config_lock():
    from .platform_io import file_lock
    path = _config_path(create=True).with_name('.harness.lock')
    with open_file(path, 'a+b') as stream, file_lock(stream):
        yield


def _unescape_mount(value):
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), value)


def _tmpfs_root():
    """Require writable Linux tmpfs; never silently journal sessions on disk."""
    path = Path(os.getenv('ONTOLOGY_HARNESS_TMP_ROOT', '/dev/shm')).absolute()
    try:
        if sys.platform != 'linux' or any(p.is_symlink() for p in (path, *path.parents)) or not path.is_dir():
            raise ValueError()
        matches = []
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            left, right = line.split(' - ', 1)
            fields = left.split(); mount = Path(_unescape_mount(fields[4]))
            if path == mount or path.is_relative_to(mount):
                matches.append((len(str(mount)), fields[5].split(','), right.split()[0]))
        _, options, filesystem = max(matches)
        if filesystem != 'tmpfs' or 'rw' not in options:
            raise ValueError()
        return path
    except (OSError, ValueError, IndexError):
        raise _error('HARNESS_UNAVAILABLE') from None


def _work_root():
    if sys.platform != 'win32':
        return _tmpfs_root()
    from .platform_io import ensure_private_directory, assert_private
    path = Path(os.getenv('ONTOLOGY_HARNESS_TMP_ROOT', str(Path(tempfile.gettempdir()) / 'ontology-one-harness'))).absolute()
    try:
        ensure_private_directory(path)
        assert_private(path)
        return path
    except (OSError, ValueError):
        raise _error('HARNESS_UNAVAILABLE') from None


@lru_cache(maxsize=16)
def _verified_file(path, device, inode, size, modified, changed, expected):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() == expected


def _verify_file(path, expected):
    if path.is_symlink() or not path.is_file():
        raise ValueError()
    item = path.stat()
    if not _verified_file(str(path), item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns, expected):
        raise ValueError()


def _runtime():
    """Check the exact official SDK and platform-specific x64 runtime artifact."""
    try:
        if sys.platform not in {'linux', 'win32'} or sys.maxsize <= 2**32 or platform.machine().lower() not in {'x86_64', 'amd64'}:
            raise ValueError()
        sdk = importlib.metadata.distribution('deepseek-harness-sdk')
        runtime = importlib.metadata.distribution('deepseek-harness-runtime-bin')
        if sdk.version != SDK_VERSION or runtime.version != SDK_VERSION:
            raise ValueError()
        sdk_root = Path(sdk.locate_file('deepseek_harness')).resolve()
        filename = 'deepseek-harness-sdk-runtime-win-x64.exe' if sys.platform == 'win32' else 'deepseek-harness-sdk-runtime-linux-x64'
        executable = Path(runtime.locate_file('deepseek_harness_runtime/runtime/' + filename)).resolve()
        if not sdk_root.is_relative_to(Path(sys.prefix).resolve()) or not executable.is_relative_to(Path(sys.prefix).resolve()):
            raise ValueError()
        for name, expected in SDK_HASHES.items():
            _verify_file(sdk_root / name, expected)
        _verify_file(executable, WINDOWS_RUNTIME_SHA256 if sys.platform == 'win32' else RUNTIME_SHA256)
        if not os.access(executable, os.X_OK):
            raise ValueError()
        return executable
    except (ValueError, OSError, importlib.metadata.PackageNotFoundError):
        raise _error('HARNESS_UNAVAILABLE') from None


def harness_status():
    _principal()
    config = _configuration()
    reason = ''
    try:
        _runtime(); _work_root()
        available = True
    except HTTPException:
        available, reason = False, '需要固定的 x64 Harness 0.2.1rc2 运行时及可用的平台隔离工作区。'
    configured = bool(config['api_key'])
    return {'available': available, 'configured': configured, 'verified': config['verified'],
            'version': SDK_VERSION, 'protocol': 'deepseek-messages', 'base_url': config['base_url'],
            'model': config['model'], 'reason': reason or ('' if configured else '尚未配置本机构的技能开发模型。'),
            'limits': {'timeout_seconds': DEFAULT_TIMEOUT, 'max_model_steps': 8, 'max_output_tokens': 8192,
                       'max_context_bytes': MAX_DRAFT_BYTES, 'max_concurrent_per_process': EXECUTOR.maximum},
            'retention': {'proposal': 'response_only', 'session': 'temporary_memory', 'audit': 'metadata_only'}}


class HarnessExecutor:
    """Own every process until its full process tree and private workspace close."""
    def __init__(self, maximum=None):
        self.maximum = int(maximum if maximum is not None else os.getenv('ONTOLOGY_HARNESS_MAX_CONCURRENT', '2'))
        if not 1 <= self.maximum <= 4:
            raise ValueError('Harness concurrency must be between 1 and 4')
        self.active, self.closing = 0, False
        self.calls, self.children = set(), set()

    async def _spawn(self, directory):
        from .skill_harness_worker import deployment_network_environment
        env = {**deployment_network_environment(os.environ),
               'PATH': os.defpath, 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
               'HOME': str(directory / 'home'), 'TMPDIR': str(directory / 'tmp'),
               'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'}
        if sys.platform == 'win32':
            from .windows_process import spawn_job_process, ProcessLimits
            system_root = os.environ.get('SystemRoot') or os.environ.get('SYSTEMROOT')
            if not system_root or not Path(system_root).is_dir():
                raise _error('HARNESS_UNAVAILABLE')
            env.update(SystemRoot=system_root, PATH=str(Path(system_root) / 'System32'),
                       USERPROFILE=str(directory / 'home'), LOCALAPPDATA=str(directory / 'home'),
                       TEMP=str(directory / 'tmp'), TMP=str(directory / 'tmp'))
            return await asyncio.to_thread(
                spawn_job_process, [sys.executable, '-I', '-B', str(Path(__file__).with_name('skill_harness_worker.py'))],
                cwd=str(directory), env=env,
                limits=ProcessLimits(memory_bytes=1024**3, active_processes=4, cpu_time_seconds=25),
            )
        return await asyncio.create_subprocess_exec(
            sys.executable, '-I', '-B', str(Path(__file__).with_name('skill_harness_worker.py')),
            cwd=str(directory), env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True, limit=MAX_RESPONSE_BYTES + 4096,
        )

    def _own_orphaned_descendants(self):
        # A container's PID 1 may not reap orphans. Adopt runtime descendants
        # when a cancelled worker exits so this owner can reap its whole group.
        # This does not change process privileges or grant access to any files.
        if sys.platform == 'win32':
            return  # The suspended Windows child is assigned to its Job before resume.
        if sys.platform != 'linux':
            raise _error('HARNESS_UNAVAILABLE')
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise _error('HARNESS_UNAVAILABLE')

    async def _read_stderr(self, stream):
        seen = 0
        while chunk := await stream.read(4096):
            seen += len(chunk)
            if seen > 16384:
                raise _error('HARNESS_LIMIT')

    async def _exchange(self, process, payload):
        if sys.platform == 'win32':
            from .windows_process import WindowsProcessError
            exchange = asyncio.create_task(asyncio.to_thread(
                process.communicate, _json(payload) + b'\n',
                max_stdout=MAX_RESPONSE_BYTES + 1, max_stderr=16384,
            ))
            try:
                raw, _ = await asyncio.shield(exchange)
            except asyncio.CancelledError:
                async def stop_exchange():
                    try:
                        try:
                            await asyncio.to_thread(process.terminate_tree)
                        except Exception:
                            # Closing the owned Job is the kernel kill-on-close
                            # fallback; the communication task is still ours.
                            with suppress(Exception):
                                await asyncio.to_thread(process.close)
                    finally:
                        await asyncio.gather(exchange, return_exceptions=True)
                await _finish_owned_cleanup(asyncio.create_task(stop_exchange()))
                raise
            except WindowsProcessError as exc:
                raise _error('HARNESS_LIMIT' if exc.code == 'OUTPUT_LIMIT' else 'HARNESS_FAILED') from None
            if process.returncode != 0:
                raise _error('HARNESS_FAILED')
            if not raw.endswith(b'\n') or raw.count(b'\n') != 1 or len(raw) > MAX_RESPONSE_BYTES + 1:
                raise _error('HARNESS_LIMIT')
            return _loads(raw)
        process.stdin.write(_json(payload) + b'\n')
        await process.stdin.drain(); process.stdin.close()
        try:
            raw = await process.stdout.readline()
        except ValueError:
            raise _error('HARNESS_LIMIT') from None
        if len(raw) > MAX_RESPONSE_BYTES + 1 or not raw.endswith(b'\n'):
            raise _error('HARNESS_LIMIT')
        if await process.stdout.read(1):
            raise _error('HARNESS_LIMIT')
        await process.wait()
        if process.returncode != 0:
            raise _error('HARNESS_FAILED')
        return _loads(raw)

    async def _reap(self, process):
        if sys.platform == 'win32':
            try:
                await asyncio.to_thread(process.terminate_tree)
                await asyncio.to_thread(process.wait, 5)
            finally:
                await asyncio.to_thread(process.close)
                self.children.discard(process)
            return
        async def discard(stream):
            while await stream.read(65536):
                pass
        drain = [asyncio.create_task(discard(stream)) for stream in (process.stdout, process.stderr)]
        if process.returncode is None:
            with suppress(ProcessLookupError): os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), .4)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError): os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
        with suppress(ProcessLookupError): os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        await asyncio.gather(*drain, return_exceptions=True)
        # asyncio owns and has reaped the direct worker above. Only adopted
        # descendants from this worker's group are waited here; never wait(-1).
        while True:
            try:
                reaped, _ = os.waitpid(-process.pid, os.WNOHANG)
            except ChildProcessError:
                break
            if reaped == 0:
                await asyncio.sleep(.01)
        if process.stdin: process.stdin.close()
        self.children.discard(process)

    async def execute(self, payload, *, timeout_seconds=DEFAULT_TIMEOUT):
        if self.closing or self.active >= self.maximum:
            raise _error('HARNESS_BUSY')
        if len(_json(payload)) > MAX_REQUEST_BYTES:
            raise _error('HARNESS_LIMIT')
        root = _work_root()
        self.active += 1
        call = asyncio.current_task(); self.calls.add(call)
        directory = process = spawn = None
        tasks = []
        try:
            directory = Path(tempfile.mkdtemp(prefix='ontology-skill-', dir=root))
            if sys.platform == 'win32':
                from .platform_io import ensure_private_directory
                for path in (directory, directory / 'home', directory / 'tmp'):
                    ensure_private_directory(path)
            else:
                directory.chmod(0o700)
                for part in ('home', 'tmp'):
                    (directory / part).mkdir(mode=0o700)
            self._own_orphaned_descendants()
            async with asyncio.timeout(timeout_seconds):
                spawn = asyncio.create_task(self._spawn(directory))
                process = await asyncio.shield(spawn)
                self.children.add(process)
                if self.closing:
                    raise asyncio.CancelledError()
                exchange = asyncio.create_task(self._exchange(process, payload))
                tasks = [exchange]
                if sys.platform != 'win32':
                    tasks.append(asyncio.create_task(self._read_stderr(process.stderr)))
                result = (await asyncio.gather(*tasks))[0]
                if not isinstance(result, dict) or result.get('ok') is not True:
                    raise _error(result.get('code', 'HARNESS_FAILED') if isinstance(result, dict) else 'HARNESS_FAILED')
                return result
        except TimeoutError:
            raise _error('HARNESS_TIMEOUT') from None
        except HTTPException:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise _error('HARNESS_FAILED') from None
        finally:
            async def cleanup():
                nonlocal process
                try:
                    if process is None and spawn is not None:
                        with suppress(Exception):
                            process = await spawn; self.children.add(process)
                    for task in tasks: task.cancel()
                    if tasks: await asyncio.gather(*tasks, return_exceptions=True)
                    if process is not None: await self._reap(process)
                finally:
                    try:
                        if directory is not None:
                            shutil.rmtree(directory, ignore_errors=False)
                    finally:
                        self.active -= 1; self.calls.discard(call)
            if await _finish_owned_cleanup(asyncio.create_task(cleanup())):
                raise asyncio.CancelledError()

    async def close(self):
        self.closing = True
        with anyio.CancelScope(shield=True):
            calls = [call for call in self.calls if call is not asyncio.current_task()]
            for call in calls: call.cancel()
            if calls: await asyncio.gather(*calls, return_exceptions=True)


EXECUTOR = HarnessExecutor()


def _context_payload(instruction, files, manifest, metadata, request_id):
    from .skill_harness_worker import _files
    if not isinstance(instruction, str) or not 1 <= len(instruction.strip()) <= 8000 or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id):
        raise HTTPException(422, '请提供有效的技能开发说明。')
    try:
        files = _files(files)
        if (sum(len(content.encode()) for content in files.values()) > MAX_DRAFT_BYTES
                or not isinstance(manifest, dict) or len(_json(manifest)) > 131072
                or len(_json(metadata)) > 131072):
            raise _error('HARNESS_LIMIT')
        return {'instruction': instruction, 'files': files, 'manifest': manifest, 'metadata': metadata, 'request_id': request_id}
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(422, '技能开发上下文不符合允许的格式。') from None


async def _invoke(principal, instruction, files, manifest, metadata, request_id, *, mode='author'):
    await asyncio.to_thread(_principal, principal, build=True)
    config = await asyncio.to_thread(_configuration)
    if not config['api_key']:
        raise _error('HARNESS_NOT_CONFIGURED')
    executable = await asyncio.to_thread(_runtime)
    payload = _context_payload(instruction, files, manifest, metadata, request_id)
    payload.update(configuration={key: config[key] for key in ('api_key','base_url','model')}, runtime_executable=str(executable), mode=mode)
    started = time.monotonic()
    await asyncio.to_thread(append_audit, principal, 'skill_harness_started', '', 'started', {'runtime_version': SDK_VERSION})
    try:
        response = await EXECUTOR.execute(payload)
        await asyncio.to_thread(_principal, principal, build=True)
        current = await asyncio.to_thread(_configuration)
        if current['revision'] != config['revision']:
            raise _error('HARNESS_CONFIG_CHANGED')
        if mode == 'test':
            result = {'tested': True, 'revision': config['revision']}
        else:
            result = response.get('proposal')
            if not isinstance(result, dict) or set(result) != {'summary','files','manifest'}:
                raise _error('HARNESS_INCOMPLETE')
            from .skill_harness_worker import _files
            _files(result['files'])
            if (not isinstance(result['summary'], str) or len(result['summary']) > 2000
                    or not isinstance(result['manifest'], dict) or len(_json(result['manifest'])) > 131072
                    or result['manifest'].get('dependencies') != manifest.get('dependencies')
                    or result['manifest'].get('tool_dependencies') != manifest.get('tool_dependencies')
                    or config['api_key'] in _json(result).decode()):
                raise _error('HARNESS_INCOMPLETE')
        await asyncio.to_thread(append_audit, principal, 'skill_harness_finished', '', 'success',
                                {'runtime_version': SDK_VERSION, 'duration_ms': round((time.monotonic()-started)*1000),
                                 'file_count': len(result.get('files', {})), 'usage': _safe_usage(response.get('usage'))})
        return result
    except BaseException as exc:
        code = 'cancelled' if isinstance(exc, asyncio.CancelledError) else (
            exc.detail.get('code', 'denied') if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else 'failed')
        with anyio.CancelScope(shield=True):
            with suppress(Exception):
                await asyncio.to_thread(append_audit, principal, 'skill_harness_failed', '', 'failed',
                                        {'code': code, 'duration_ms': round((time.monotonic()-started)*1000)})
        if isinstance(exc, (HTTPException, asyncio.CancelledError)):
            raise
        raise _error('HARNESS_FAILED') from None


def _safe_usage(value):
    keys = {'model_responses', 'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens'}
    if not isinstance(value, dict) or type(value.get('complete')) is not bool:
        return {'complete': False}
    if set(value) != keys | {'complete'}:
        return {'complete': False}
    if any(item is not None and (type(item) is not int or not 0 <= item <= 80_000_000)
           for key, item in value.items() if key in keys):
        return {'complete': False}
    if value['complete'] and any(value[key] is None for key in keys):
        return {'complete': False}
    return dict(value)


async def propose_skill(principal, instruction, files, manifest, metadata, request_id):
    return await _invoke(principal, instruction, files, manifest, metadata, request_id)


@router.get('/status')
def status():
    return harness_status()


def _save_configuration(body: HarnessConfiguration):
    principal = _principal(admin=True)
    with _config_lock():
        current = _configuration()
        if current['api_key'] and body.base_url != current['base_url'] and not (body.api_key or body.clear_key):
            raise HTTPException(422, '更换模型服务地址时须重新输入对应密钥，或先明确清除旧密钥。')
        current.update(base_url=body.base_url, model=body.model, revision=uuid.uuid4().hex, verified=False)
        if body.clear_key: current['api_key'] = ''
        elif body.api_key: current['api_key'] = body.api_key
        write_bytes(_config_path(create=True), _json(current))
    append_audit(principal, 'skill_harness_configured', '', 'success', {'configured': bool(current['api_key']), 'verified': False})
    return harness_status()


@router.post('/config')
async def save_configuration(request: Request):
    # FastAPI's generic validation detail can echo a rejected secret input.
    # Parse this sensitive payload with fixed safe errors instead.
    await asyncio.to_thread(_principal, admin=True)
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(415, '模型配置须使用 JSON。')
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 16384:
            raise HTTPException(413, '模型配置超过允许大小。')
    try:
        body = HarnessConfiguration.model_validate(_loads(raw), strict=True)
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, '模型配置格式无效，请核对 HTTPS Messages 地址、模型标识及密钥。') from None
    return await asyncio.to_thread(_save_configuration, body)


def _mark_verified(principal, revision):
    _principal(principal, admin=True)
    with _config_lock():
        current = _configuration()
        if current['revision'] != revision:
            raise _error('HARNESS_CONFIG_CHANGED')
        current['verified'] = True
        write_bytes(_config_path(create=True), _json(current))
    return harness_status()


async def _disconnect_guard(request, task):
    while not task.done():
        if await request.is_disconnected():
            task.cancel()
            return
        await asyncio.sleep(.1)


@router.post('/test')
async def test_configuration(request: Request):
    principal = await asyncio.to_thread(_principal, admin=True)
    task = asyncio.create_task(_invoke(principal, 'Reply with a short confirmation only; do not call tools.',
                                      {'SKILL.md': '---\nname: connection-test\ndescription: Connection test.\n---\nConnection test.'},
                                      {}, {}, principal.request_id or uuid.uuid4().hex, mode='test'))
    watcher = asyncio.create_task(_disconnect_guard(request, task))
    try:
        result = await task
        status_value = await asyncio.to_thread(_mark_verified, principal, result['revision'])
        return {'ok': True, 'message': '已验证受控 Harness SDK 与模型连接。', 'status': status_value}
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
