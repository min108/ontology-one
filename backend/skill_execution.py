"""Platform entry point for isolated, synthetic skill tests.

Linux uses a deployment-owned Unix-socket runner; native Windows uses LPAC Jobs.
The API has no Docker access. Source and synthetic examples travel in memory;
only fixed case-status metadata is returned. Closing/half-closing the socket
cancels the runner's current container rather than abandoning a Python thread.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import socket
import stat
import time

MAX_REQUEST = 3 * 1024 * 1024
MAX_RESPONSE = 128 * 1024
_CODES = frozenset({'PASSED', 'ASSERTION_FAILED', 'STDOUT_NOT_JSON', 'SCRIPT_ERROR', 'TIMEOUT',
                    'CANCELLED', 'OUTPUT_LIMIT', 'RESOURCE_LIMIT', 'RUNNER_BUSY', 'RUNNER_UNAVAILABLE',
                    'INVALID_TEST_REQUEST', 'NOT_RUN'})


def _cancelled(value):
    if value is None:
        return False
    try:
        return bool(value() if callable(value) else value.is_set())
    except Exception:
        return True


def _socket_path():
    value = os.getenv('ONTOLOGY_SKILL_RUNNER_SOCKET', '')
    if not value or len(os.fsencode(value)) > 100 or not Path(value).is_absolute():
        raise OSError('Runner not configured')
    mode = Path(value).lstat().st_mode
    if not stat.S_ISSOCK(mode):
        raise OSError('Runner unavailable')
    return value


def _exchange(method, path, body=None, *, timeout=30, cancelled=None):
    encoded = b'' if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    if len(encoded) > MAX_REQUEST:
        raise ValueError('Request exceeds runner limit')
    request = (f'{method} {path} HTTP/1.1\r\nHost: ontology-skill-runner\r\nConnection: close\r\n'
               f'Content-Type: application/json\r\nContent-Length: {len(encoded)}\r\n\r\n').encode() + encoded
    deadline = time.monotonic() + timeout
    interrupted = None
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(min(1, timeout))
        channel.connect(_socket_path())
        channel.setblocking(False)
        offset, received = 0, bytearray()
        while True:
            now = time.monotonic()
            if interrupted is None and (_cancelled(cancelled) or now >= deadline):
                interrupted = 'CANCELLED' if _cancelled(cancelled) else 'TIMEOUT'
                # Keep the response half open so the fixed runner can acknowledge
                # container removal. Real disconnect follows the same EOF path.
                try:
                    channel.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                deadline = now + 4
            elif interrupted is not None and now >= deadline:
                return 503, {}, interrupted
            readable, writable, _ = select.select([channel], [channel] if offset < len(request) and interrupted is None else [], [], .05)
            if writable:
                try:
                    sent = channel.send(request[offset:offset + 65536])
                    if not sent:
                        raise OSError('Runner disconnected')
                    offset += sent
                except BlockingIOError:
                    pass
            if readable:
                try:
                    chunk = channel.recv(65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                received.extend(chunk)
                if len(received) > MAX_RESPONSE:
                    raise ValueError('Runner response exceeds limit')
        header, separator, content = bytes(received).partition(b'\r\n\r\n')
        if not separator:
            raise OSError('Runner response unavailable')
        first = header.split(b'\r\n', 1)[0].split()
        if len(first) < 2 or not first[1].isdigit():
            raise ValueError('Invalid runner response')
        return int(first[1]), json.loads(content), interrupted


def runner_status():
    if os.name == 'nt':
        from .windows_skill_runner import runner_status as windows_status
        return windows_status()
    try:
        status, body, _ = _exchange('GET', '/status', timeout=1)
        if status == 200 and isinstance(body, dict) and body.get('available') is True:
            return {'available': True, 'reason': '隔离脚本测试服务可用。'}
    except (OSError, ValueError, TypeError):
        pass
    return {'available': False, 'reason': '隔离脚本测试服务未配置或不可用；脚本测试不能通过发布验收。'}


def _failure(cases, code, began, *, unavailable=False):
    names = [str(item.get('name', ''))[:100] if isinstance(item, dict) else '' for item in cases[:10]] if isinstance(cases, list) else []
    return {'outcome': 'unavailable' if unavailable else 'failed', 'mode': 'isolated_python',
            'report': {'cases': [{'name': name, 'status': 'failed', 'code': code, 'duration_ms': 0} for name in names],
                       'passed': 0, 'failed': len(names), 'duration_ms': round((time.monotonic() - began) * 1000)}}


def _safe_report(body, cases):
    if not isinstance(body, dict) or set(body) != {'outcome', 'mode', 'report'} or body['mode'] != 'isolated_python':
        raise ValueError('Invalid test report')
    report = body['report']
    if not isinstance(report, dict) or set(report) != {'cases', 'passed', 'failed', 'duration_ms'}:
        raise ValueError('Invalid test report')
    if not isinstance(report['cases'], list) or len(report['cases']) != len(cases):
        raise ValueError('Invalid case count')
    for case, expected in zip(report['cases'], cases):
        if (not isinstance(case, dict) or set(case) != {'name', 'status', 'code', 'duration_ms'}
                or case['name'] != expected['name'] or case['status'] not in {'passed', 'failed'}
                or case['code'] not in _CODES or type(case['duration_ms']) is not int or case['duration_ms'] < 0
                or (case['status'] == 'passed') != (case['code'] == 'PASSED')):
            raise ValueError('Invalid case metadata')
    passed = sum(case['status'] == 'passed' for case in report['cases'])
    if (type(report['passed']) is not int or report['passed'] != passed
            or type(report['failed']) is not int or report['failed'] != len(cases) - passed
            or type(report['duration_ms']) is not int or report['duration_ms'] < 0
            or body['outcome'] != ('passed' if passed == len(cases) else 'failed')):
        raise ValueError('Invalid test outcome')
    return body


def execute_tests(files, entrypoint, test_cases, timeout_seconds=30, cancelled=None):
    if os.name == 'nt':
        from .windows_skill_runner import execute_tests as windows_execute
        return windows_execute(files, entrypoint, test_cases, timeout_seconds, cancelled)
    began = time.monotonic()
    if _cancelled(cancelled):
        return _failure(test_cases, 'CANCELLED', began)
    if (not isinstance(files, dict) or not isinstance(entrypoint, str) or not isinstance(test_cases, list)
            or not 1 <= len(test_cases) <= 10 or type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 30):
        return _failure(test_cases, 'INVALID_TEST_REQUEST', began)
    try:
        status, body, interrupted = _exchange('POST', '/test',
            {'files': files, 'entrypoint': entrypoint, 'test_cases': test_cases}, timeout=timeout_seconds, cancelled=cancelled)
        if interrupted:
            return _failure(test_cases, interrupted, began)
        if status == 429:
            return _failure(test_cases, 'RUNNER_BUSY', began)
        if status == 503:
            return _failure(test_cases, 'RUNNER_UNAVAILABLE', began, unavailable=True)
        if status != 200:
            return _failure(test_cases, 'INVALID_TEST_REQUEST', began)
        return _safe_report(body, test_cases)
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError, UnicodeError):
        return _failure(test_cases, 'RUNNER_UNAVAILABLE', began, unavailable=True)


def execute_readonly_skill(files, entrypoint, input, *, timeout=30, cancelled=None):
    """Return one bounded JSON value in memory after sandbox cleanup completes.

    Authorization, publication proofs and JSON schemas are enforced by the
    caller before/after execution. No code runs outside the existing sandbox.
    Test reports keep their existing metadata-only contract.
    """
    failed = lambda code: {'outcome': 'unavailable' if code == 'RUNNER_UNAVAILABLE' else 'failed',
                           'output': None, 'code': code}
    if _cancelled(cancelled):
        return failed('CANCELLED')
    if type(timeout) not in (int, float) or not 1 <= timeout <= 30:
        return failed('INVALID_TEST_REQUEST')
    try:
        if os.name == 'nt':
            from .windows_skill_runner import execute_tests as windows_execute
            captured = []
            result = windows_execute(files, entrypoint, [{'name': 'readonly', 'input': input,
                'expected_output': None}], timeout, cancelled, _capture=captured)
            if result['outcome'] == 'passed' and len(captured) == 1 and not _cancelled(cancelled):
                return {'outcome': 'passed', 'output': captured.pop(), 'code': 'PASSED'}
            captured.clear()
            codes = result.get('report', {}).get('cases', [])
            return failed('CANCELLED' if _cancelled(cancelled) else codes[0]['code'] if codes else 'RUNNER_UNAVAILABLE')
        status, body, interrupted = _exchange('POST', '/run',
            {'files': files, 'entrypoint': entrypoint, 'input': input, 'timeout_seconds': timeout},
            timeout=timeout, cancelled=cancelled)
        if interrupted or _cancelled(cancelled):
            return failed(interrupted or 'CANCELLED')
        if status != 200:
            return failed({429: 'RUNNER_BUSY', 400: 'INVALID_TEST_REQUEST'}.get(status, 'RUNNER_UNAVAILABLE'))
        if (not isinstance(body, dict) or set(body) != {'outcome', 'output', 'code'} or
                body['code'] not in _CODES or body['outcome'] not in {'passed', 'failed', 'unavailable'} or
                body['outcome'] != ('passed' if body['code'] == 'PASSED' else
                                    'unavailable' if body['code'] == 'RUNNER_UNAVAILABLE' else 'failed')):
            raise ValueError()
        from .windows_skill_runner import _value
        _value(body['output'])
        if body['outcome'] != 'passed' and body['output'] is not None:
            raise ValueError()
        return body
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError, UnicodeError):
        return failed('RUNNER_UNAVAILABLE')
