"""Windows LPAC runner for synthetic, read-only Python skill tests.

No Docker, elevation, shell or installed Python is required: a pinned embedded
Python distribution ships in the native bundle. Files and profile storage are
read-only inside the sandbox; the sole result channel is bounded JSON stdout.
Capabilities are empty, and WindowsProcess verifies the resulting LPAC token
before its initial thread is resumed. Runtime copies and profiles are unique
per request, so concurrent tenants never share an AppContainer identity.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time

from .skill_package import validate_files
from .windows_process import AppContainerProfile, ProcessLimits, WindowsProcessError, spawn_job_process, _plain_path

MAX_OUTPUT = 64 * 1024
LIMITS = ProcessLimits(memory_bytes=192 * 1024 * 1024, active_processes=1, cpu_time_seconds=5, cpu_rate_percent=50)
_GATE = threading.BoundedSemaphore(2)
_STATUS_LOCK = threading.Lock()
_STATUS = None
_UNHEALTHY = threading.Event()


def _cancelled(value):
    try:
        return bool(value() if callable(value) else value is not None and value.is_set())
    except Exception:
        return True


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def _strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate key')
            result[key] = value
        return result
    def invalid(_): raise ValueError('Nonfinite value')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def _value(value):
    nodes = 0
    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if depth > 20 or nodes > 5000: raise ValueError('Value complexity exceeded')
        if item is None or type(item) in (str, int, bool): return
        if type(item) is float and math.isfinite(item): return
        if type(item) is list:
            for child in item: visit(child, depth + 1)
        elif type(item) is dict and all(type(key) is str for key in item):
            for child in item.values(): visit(child, depth + 1)
        else: raise ValueError('Invalid synthetic value')
    visit(value, 0)
    if len(_json(value)) > MAX_OUTPUT: raise ValueError('Synthetic value exceeds limit')


def _equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool): return type(left) is type(right) and left == right
    if type(left) in (int, float) and type(right) in (int, float): return left == right
    if type(left) is not type(right): return False
    if isinstance(left, dict): return left.keys() == right.keys() and all(_equal(left[key], right[key]) for key in left)
    if isinstance(left, list): return len(left) == len(right) and all(_equal(a, b) for a, b in zip(left, right))
    return left == right


def _validate(files, entrypoint, cases, timeout):
    if (not isinstance(files, dict) or type(timeout) not in (int, float) or not 1 <= timeout <= 30
            or not isinstance(cases, list) or not 1 <= len(cases) <= 10
            or not isinstance(entrypoint, str) or entrypoint not in files
            or not entrypoint.startswith('scripts/') or not entrypoint.endswith('.py')
            or not validate_files(files)['valid']):
        raise ValueError('Invalid test request')
    for case in cases:
        if (not isinstance(case, dict) or set(case) != {'name', 'input', 'expected_output'}
                or not isinstance(case['name'], str) or not 1 <= len(case['name']) <= 100):
            raise ValueError('Invalid test case')
        _value(case['input']); _value(case['expected_output'])


def _runtime_files():
    if os.name != 'nt': raise WindowsProcessError('WINDOWS_X64_REQUIRED')
    configured = os.getenv('ONTOLOGY_SKILL_PYTHON_ROOT', '')
    if not configured: raise WindowsProcessError('RUNNER_UNAVAILABLE')
    root = _plain_path(configured)
    if not root.is_dir(): raise WindowsProcessError('RUNNER_UNAVAILABLE')
    files, total = [], 0
    allowed = re.compile(r'(?:python(?:w|3|3\d+)?\.(?:exe|zip|_pth)|[\w.-]+\.dll|[\w-]+\.pyd|LICENSE\.txt)', re.I)
    for item in root.iterdir():
        if not allowed.fullmatch(item.name): continue
        item = _plain_path(item)
        if not item.is_file(): raise WindowsProcessError('RUNNER_UNAVAILABLE')
        total += item.stat().st_size
        if len(files) >= 128 or total > 128 * 1024 * 1024: raise WindowsProcessError('RUNNER_UNAVAILABLE')
        # A _pth file is trusted configuration, but never allow it to point
        # outside the isolated runtime copy or import host site-packages.
        if item.suffix.lower() == '._pth':
            for line in item.read_text(encoding='utf-8').splitlines():
                value = line.strip()
                if value and not value.startswith('#') and value != '.' and not re.fullmatch(r'python3\d+\.zip', value, re.I):
                    if value != 'import site': raise WindowsProcessError('RUNNER_UNAVAILABLE')
        files.append(item)
    names = {item.name.lower() for item in files}
    if ('python.exe' not in names or not any(re.fullmatch(r'python3\d+\.zip', name) for name in names)
            or not any(re.fullmatch(r'python3\d+\.dll', name) for name in names)):
        raise WindowsProcessError('RUNNER_UNAVAILABLE')
    return sorted(files)


def _session_root():
    from .platform_io import ensure_private_directory
    configured = os.getenv('ONTOLOGY_SKILL_TEMP_DIR')
    root = Path(configured) if configured else Path(tempfile.gettempdir()) / 'OntologyOne' / 'skill-tests'
    root = ensure_private_directory(root)
    return ensure_private_directory(Path(tempfile.mkdtemp(prefix='request-', dir=root)))


def _result(cases, codes, began, unavailable=False):
    rows = [{'name': case['name'], 'status': 'passed' if code == 'PASSED' else 'failed', 'code': code, 'duration_ms': duration}
            for case, (code, duration) in zip(cases, codes)]
    passed = sum(row['status'] == 'passed' for row in rows)
    return {'outcome': 'unavailable' if unavailable else 'passed' if passed == len(rows) else 'failed',
            'mode': 'isolated_python', 'report': {'cases': rows, 'passed': passed, 'failed': len(rows) - passed,
            'duration_ms': round((time.monotonic() - began) * 1000)}}


def _failure(cases, code, began, unavailable=False):
    safe = [{'name': str(case.get('name', ''))[:100] if isinstance(case, dict) else ''} for case in cases[:10]] if isinstance(cases, list) else []
    return _result(safe, [(code, 0)] * len(safe), began, unavailable)


def execute_tests(files, entrypoint, test_cases, timeout_seconds=30, cancelled=None):
    began = time.monotonic()
    if _cancelled(cancelled): return _failure(test_cases, 'CANCELLED', began)
    try:
        _validate(files, entrypoint, test_cases, timeout_seconds)
        runtime_files = _runtime_files()
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        return _failure(test_cases, 'INVALID_TEST_REQUEST', began)
    except (OSError, WindowsProcessError):
        return _failure(test_cases, 'RUNNER_UNAVAILABLE', began, True)
    if _UNHEALTHY.is_set(): return _failure(test_cases, 'RUNNER_UNAVAILABLE', began, True)
    if not _GATE.acquire(blocking=False): return _failure(test_cases, 'RUNNER_BUSY', began)
    root, profile, owner = None, None, None
    rows, halted = [], None
    deadline = began + timeout_seconds
    result = None
    try:
        root = _session_root()
        runtime, work = root / 'runtime', root / 'work'
        runtime.mkdir(); work.mkdir()
        for source in runtime_files: shutil.copyfile(source, runtime / source.name)
        for name, content in files.items():
            path = work.joinpath(*name.split('/')); path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        environment = {'SystemRoot': os.environ.get('SystemRoot', r'C:\Windows'), 'PATH': str(runtime),
                       'HOME': str(work), 'USERPROFILE': str(work), 'TEMP': str(work), 'TMP': str(work),
                       'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1', 'PYTHONUTF8': '1'}
        for case in test_cases:
            start = time.monotonic()
            if halted or _cancelled(cancelled) or start >= deadline:
                code = halted or ('CANCELLED' if _cancelled(cancelled) else 'TIMEOUT')
            else:
                try:
                    # A new SID/profile for every case also isolates registry
                    # state, not only files and process lifetimes.
                    profile = AppContainerProfile().__enter__()
                    profile.grant_read_tree(root)
                    profile.grant_read_tree(runtime, executable=True)
                    owner = spawn_job_process([str(runtime / 'python.exe'), '-I', '-B', '-u', str(work / entrypoint)],
                        cwd=str(work), env=environment, limits=LIMITS, appcontainer=profile)
                    output, _ = owner.communicate(_json(case['input']), timeout=max(.01, min(5, deadline - time.monotonic())),
                        cancelled=cancelled, max_stdout=MAX_OUTPUT, max_stderr=MAX_OUTPUT)
                    if owner.returncode:
                        code = 'RESOURCE_LIMIT' if owner.returncode in {0xC0000017, 0xC0000044, 0xC000012D, 1816} else 'SCRIPT_ERROR'
                    else:
                        try:
                            actual = _strict_json(output); _value(actual)
                            code = 'PASSED' if _equal(actual, case['expected_output']) else 'ASSERTION_FAILED'
                        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
                            code = 'STDOUT_NOT_JSON'
                except WindowsProcessError as exc:
                    code = exc.code if exc.code in {'TIMEOUT', 'CANCELLED', 'OUTPUT_LIMIT'} else 'RUNNER_UNAVAILABLE'
                except (OSError, ValueError, TypeError):
                    code = 'RUNNER_UNAVAILABLE'
                finally:
                    if owner is not None:
                        owner.close(); owner = None
                    if profile is not None:
                        profile.close(); profile = None
            rows.append((code, round((time.monotonic() - start) * 1000)))
            if code in {'TIMEOUT', 'CANCELLED', 'RUNNER_UNAVAILABLE'}: halted = code
        result = _result(test_cases, rows, began)
    except (OSError, ValueError, TypeError):
        result = _failure(test_cases, 'RUNNER_UNAVAILABLE', began, True)
    finally:
        try:
            for cleanup in ((owner.close if owner is not None else None),
                            (lambda: shutil.rmtree(root)) if root is not None else None,
                            (profile.close if profile is not None else None)):
                if cleanup is None: continue
                try:
                    cleanup()
                except (OSError, ValueError):
                    _UNHEALTHY.set()
                    result = _failure(test_cases, 'RUNNER_UNAVAILABLE', began, True)
        finally:
            _GATE.release()
    return result


def runner_status():
    """Availability is a real isolated execution probe, never just an import."""
    global _STATUS
    unavailable = {'available': False, 'reason': 'Windows 隔离测试不可用；不会降级为普通进程执行。'}
    if _UNHEALTHY.is_set(): return unavailable
    try:
        runtime = _runtime_files()
        fingerprint = tuple((str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in runtime)
    except (OSError, ValueError):
        return unavailable
    with _STATUS_LOCK:
        if _STATUS is not None and _STATUS[0] == fingerprint and time.monotonic() - _STATUS[1] < 300:
            return dict(_STATUS[2])
        files = {'SKILL.md': '---\nname: isolation-probe\ndescription: Native isolated execution probe.\n---\nSynthetic test.\n',
                 'scripts/main.py': 'import json\nprint(json.dumps({"isolated": True}))\n'}
        result = execute_tests(files, 'scripts/main.py', [{'name': 'probe', 'input': {}, 'expected_output': {'isolated': True}}])
        if any(case.get('code') == 'RUNNER_BUSY' for case in result.get('report', {}).get('cases', [])):
            if _STATUS is not None and _STATUS[0] == fingerprint and _STATUS[2]['available']:
                return {'available': True, 'reason': 'Windows 隔离测试已验证，当前测试任务繁忙，请稍后重试。'}
            return {'available': False, 'reason': '隔离测试任务繁忙，首次安全验证等待空闲后重试。'}
        status = {'available': True, 'reason': 'Windows AppContainer 只读脚本测试可用；输入输出使用 JSON 管道。'} if result['outcome'] == 'passed' else unavailable
        _STATUS = (fingerprint, time.monotonic(), status) if status['available'] else None
        return dict(status)
