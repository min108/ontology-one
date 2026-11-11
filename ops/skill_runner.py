"""Deployment-owned local service for fixed, isolated Python skill tests.

Start on the Docker host, not inside the platform API container:
  ONTOLOGY_SKILL_TEST_IMAGE=ontology-skill-test:0.5.0 \
    python ops/skill_runner.py --socket /run/ontology-skills/runner.sock
Prepare that directory for the API's group; the socket itself is always 0660.
No uploaded command, image, mount, environment, credential or Docker option is
accepted. Requests, source files, synthetic values and process output stay in
memory. Access logs are disabled and container logging uses the none driver.
"""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler
import json
import math
import os
from pathlib import Path
import re
import select
import selectors
import socket
import socketserver
import stat
import subprocess
import threading
import time
import unicodedata
import uuid

MAX_REQUEST = 3 * 1024 * 1024
MAX_OUTPUT = 64 * 1024
MAX_PACKAGE = 2 * 1024 * 1024
PROXY_KEYS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate key')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('Nonfinite scalar')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def valid_value(value):
    nodes = 0
    def visit(item, depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 20 or nodes > 5000:
            raise ValueError('Value complexity exceeded')
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item: visit(child, depth + 1)
        elif type(item) is dict and all(type(key) is str for key in item):
            for child in item.values(): visit(child, depth + 1)
        else:
            raise ValueError('Unsupported value')
    visit(value)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > MAX_OUTPUT:
        raise ValueError('Example exceeds limit')


def validate_request(body):
    if not isinstance(body, dict) or set(body) != {'files', 'entrypoint', 'test_cases'}:
        raise ValueError('Invalid request fields')
    files, entrypoint, cases = body['files'], body['entrypoint'], body['test_cases']
    if not isinstance(files, dict) or not 1 <= len(files) <= 64:
        raise ValueError('Invalid file count')
    seen, total = set(), 0
    for name, content in files.items():
        if (not isinstance(name, str) or not name or len(name) > 240 or name.startswith('/')
                or '\\' in name or ':' in name or any(ord(c) < 32 for c in name)
                or any(part in {'.', '..', ''} for part in name.split('/')) or len(name.split('/')) > 8
                or not isinstance(content, str) or '\x00' in content):
            raise ValueError('Invalid file')
        key = unicodedata.normalize('NFKC', name).casefold()
        if key != name.casefold() or key in seen:
            raise ValueError('Ambiguous path')
        seen.add(key)
        size = len(content.encode('utf-8'))
        total += size
        if size > 256 * 1024 or total > MAX_PACKAGE:
            raise ValueError('Package exceeds limit')
    if any('/'.join(name.split('/')[:index]).casefold() in seen for name in files for index in range(1, len(name.split('/')))):
        raise ValueError('File directory collision')
    if not isinstance(entrypoint, str) or entrypoint not in files or not entrypoint.startswith('scripts/') or not entrypoint.endswith('.py'):
        raise ValueError('Unsupported entrypoint')
    if not isinstance(cases, list) or not 1 <= len(cases) <= 10:
        raise ValueError('Invalid case count')
    for case in cases:
        if not isinstance(case, dict) or set(case) != {'name', 'input', 'expected_output'} or not isinstance(case['name'], str) or not 1 <= len(case['name']) <= 100:
            raise ValueError('Invalid test case')
        valid_value(case['input']); valid_value(case['expected_output'])
    return body


def json_equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return left == right


class DockerRunner:
    def __init__(self, socket_path, image=None, namespace=None):
        self.image = image or os.getenv('ONTOLOGY_SKILL_TEST_IMAGE', 'ontology-skill-test:0.5.0')
        self.namespace = namespace or os.getenv('ONTOLOGY_SKILL_RUNNER_NAMESPACE', 'ontology-skills')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,50}', self.namespace):
            raise ValueError('Invalid deployment namespace')
        self.owner = hashlib.sha256((self.namespace + '\0' + str(Path(socket_path).absolute())).encode()).hexdigest()[:24]
        self.instance = uuid.uuid4().hex[:16]
        self.image_id = None
        self.stop = threading.Event()
        self.unhealthy = threading.Event()
        self.gate = threading.BoundedSemaphore(2)
        self.children, self.lock = set(), threading.Lock()
        self.pending_cleanup = set()
        self.environment = dict(os.environ)
        for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
            self.environment.pop(key, None)
        try:
            result = self.command(['image', 'inspect', '--format', '{{.Id}}', self.image], timeout=3)
            ident = result.stdout.decode().strip()
            if result.returncode == 0 and re.fullmatch(r'sha256:[a-f0-9]{64}', ident):
                self.image_id = ident
                self.reconcile()
        except (OSError, subprocess.TimeoutExpired):
            pass
        self.reaper = threading.Thread(target=self._retry_cleanup, name='skill-container-reaper', daemon=True)
        self.reaper.start()

    def _retry_cleanup(self):
        while not self.stop.wait(.5):
            with self.lock:
                pending = list(self.pending_cleanup)
            for name in pending:
                # An interrupted create RPC may finish after its client exits.
                # Keep its fixed name until Docker positively acknowledges rm.
                try:
                    result = self.command(['rm', '-f', name], timeout=2)
                    if result.returncode == 0:
                        with self.lock:
                            self.pending_cleanup.discard(name)
                            self.children.discard(name)
                except (OSError, subprocess.TimeoutExpired):
                    pass

    def command(self, args, *, timeout=3):
        return subprocess.run(['docker', '--host=unix:///var/run/docker.sock', *args],
            env=self.environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False)

    def reconcile(self):
        result = self.command(['ps', '-aq', '--filter', 'label=ontology.skill-runner.owner=' + self.owner])
        for ident in result.stdout.decode().split():
            if re.fullmatch(r'[0-9a-f]{12,64}', ident):
                self.command(['rm', '-f', ident])

    def remove(self, name, *, creation_confirmed=True):
        # A fixed name is registered before create, so even an interrupted create
        # RPC is reconciled. Never select or remove another deployment's objects.
        removed = False
        deadline = time.monotonic() + 3
        for _ in range(3):
            try:
                left = deadline - time.monotonic()
                if left <= 0: break
                result = self.command(['rm', '-f', name], timeout=min(1, left))
                if result.returncode == 0:
                    removed = True
                    break
                left = deadline - time.monotonic()
                if left <= 0: break
                found = self.command(['ps', '-aq', '--filter', 'name=^/' + name + '$'], timeout=min(1, left))
                if creation_confirmed and found.returncode == 0 and not found.stdout.strip():
                    removed = True
                    break
            except (OSError, subprocess.TimeoutExpired):
                continue
        if removed:
            with self.lock:
                self.children.discard(name)
        else:
            # Never treat a daemon connection failure as proof of removal.
            self.unhealthy.set()
            with self.lock:
                self.pending_cleanup.add(name)
        return removed

    def _create_args(self, name):
        args = ['create', '--pull=never', '--name', name,
            '--label', 'ontology.skill-runner.owner=' + self.owner,
            '--label', 'ontology.skill-runner.namespace=' + self.namespace,
            '--label', 'ontology.skill-runner.instance=' + self.instance,
            '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges=true', '--user', '65534:65534',
            '--pids-limit', '64', '--memory', '256m', '--memory-swap', '256m', '--cpus', '1',
            '--ulimit', 'core=0:0', '--ulimit', 'nofile=64:64', '--ipc', 'none',
            '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777',
            '--log-driver', 'none', '--hostname', 'skill-test', '-i']
        # Docker client defaults may otherwise inject deployment proxy values.
        for key in PROXY_KEYS:
            args.extend(['--env', key + '='])
        return [*args, self.image_id]

    def case(self, files, entrypoint, case, deadline, disconnected, *, capture=None):
        began = time.monotonic()
        name = 'ontology-skill-' + self.namespace + '-' + uuid.uuid4().hex[:20]
        process = None
        creation_confirmed = False
        code, output = 'RUNNER_UNAVAILABLE', bytearray()
        with self.lock:
            self.children.add(name)
        try:
            # Preparing a fresh container can take several seconds on a copy-on-
            # write-less Docker backend. It consumes the overall request budget,
            # not the script's separate five-second execution allowance.
            created = self.command(self._create_args(name), timeout=max(.1, deadline - time.monotonic()))
            creation_confirmed = True
            if created.returncode != 0:
                return code, round((time.monotonic() - began) * 1000)
            if disconnected() or self.stop.is_set():
                return 'CANCELLED', round((time.monotonic() - began) * 1000)
            deadline = min(deadline, time.monotonic() + 5)
            # expected_output is deliberately absent from the process protocol.
            wire = json.dumps({'files': files, 'entrypoint': entrypoint, 'input': case['input']},
                ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
            process = subprocess.Popen(['docker', '--host=unix:///var/run/docker.sock', 'start', '-a', '-i', name],
                env=self.environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            streams = selectors.DefaultSelector()
            for stream, role in ((process.stdin, 'in'), (process.stdout, 'out'), (process.stderr, 'err')):
                os.set_blocking(stream.fileno(), False)
                streams.register(stream, selectors.EVENT_WRITE if role == 'in' else selectors.EVENT_READ, role)
            sent, stderr_size, code = 0, 0, None
            try:
                while streams.get_map():
                    if disconnected() or self.stop.is_set():
                        code = 'CANCELLED'; break
                    if time.monotonic() >= deadline:
                        code = 'TIMEOUT'; break
                    for key, _ in streams.select(.025):
                        if key.data == 'in':
                            try:
                                sent += os.write(key.fileobj.fileno(), wire[sent:sent + 65536])
                            except BlockingIOError:
                                continue
                            except (BrokenPipeError, OSError):
                                streams.unregister(key.fileobj); key.fileobj.close(); continue
                            if sent == len(wire):
                                streams.unregister(key.fileobj); key.fileobj.close()
                        else:
                            try: chunk = os.read(key.fileobj.fileno(), 16384)
                            except BlockingIOError: continue
                            if not chunk:
                                streams.unregister(key.fileobj); continue
                            if key.data == 'out':
                                output.extend(chunk)
                            else:
                                stderr_size += len(chunk)
                            if len(output) > MAX_OUTPUT or stderr_size > MAX_OUTPUT:
                                code = 'OUTPUT_LIMIT'; break
                    if code:
                        break
                if code:
                    return code, round((time.monotonic() - began) * 1000)
                process.wait(timeout=max(.1, deadline - time.monotonic()))
                result = self.command(['inspect', '--format', '{{.State.ExitCode}} {{.State.OOMKilled}}', name], timeout=1)
                state = result.stdout.decode().strip().split()
                if result.returncode != 0 or len(state) != 2:
                    code = 'RUNNER_UNAVAILABLE'
                elif state[1] == 'true' or state[0] in {'75', '137', '152', '153'}:
                    code = 'RESOURCE_LIMIT'
                elif state[0] == '124':
                    code = 'TIMEOUT'
                elif state[0] != '0':
                    code = 'SCRIPT_ERROR'
                else:
                    try:
                        actual = strict_json(output)
                        valid_value(actual)
                        code = 'PASSED' if capture is not None or json_equal(actual, case['expected_output']) else 'ASSERTION_FAILED'
                        if capture is not None:
                            capture.append(actual)
                    except (ValueError, TypeError, RecursionError, UnicodeError):
                        code = 'STDOUT_NOT_JSON'
                return code, round((time.monotonic() - began) * 1000)
            finally:
                streams.close()
        except subprocess.TimeoutExpired:
            return 'TIMEOUT', round((time.monotonic() - began) * 1000)
        except (OSError, ValueError, TypeError):
            return 'RUNNER_UNAVAILABLE', round((time.monotonic() - began) * 1000)
        finally:
            removed = self.remove(name, creation_confirmed=creation_confirmed)
            if process:
                try: process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait()
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream and not stream.closed: stream.close()
            if not removed:
                return 'RUNNER_UNAVAILABLE', round((time.monotonic() - began) * 1000)

    def execute(self, body, disconnected):
        began, rows = time.monotonic(), []
        deadline = began + 30
        halted = None
        for case in body['test_cases']:
            if halted or disconnected() or self.stop.is_set() or time.monotonic() >= deadline:
                code, duration = halted or ('CANCELLED' if disconnected() or self.stop.is_set() else 'TIMEOUT'), 0
            else:
                code, duration = self.case(body['files'], body['entrypoint'], case, deadline, disconnected)
            rows.append({'name': case['name'], 'status': 'passed' if code == 'PASSED' else 'failed', 'code': code, 'duration_ms': duration})
            if code in {'CANCELLED', 'TIMEOUT', 'RUNNER_UNAVAILABLE'}:
                halted = code
        passed = sum(row['status'] == 'passed' for row in rows)
        return {'outcome': 'passed' if passed == len(rows) else 'failed', 'mode': 'isolated_python',
                'report': {'cases': rows, 'passed': passed, 'failed': len(rows) - passed,
                           'duration_ms': round((time.monotonic() - began) * 1000)}}

    def execute_readonly(self, body, disconnected):
        captured = []
        code, _ = self.case(body['files'], body['entrypoint'], {'input': body['input']},
            time.monotonic() + body['timeout_seconds'], disconnected, capture=captured)
        if code == 'PASSED' and len(captured) == 1 and not disconnected() and not self.stop.is_set():
            return {'outcome': 'passed', 'output': captured.pop(), 'code': 'PASSED'}
        captured.clear()
        if code == 'PASSED':
            code = 'CANCELLED'
        return {'outcome': 'unavailable' if code == 'RUNNER_UNAVAILABLE' else 'failed', 'output': None, 'code': code}


class RunnerServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = False
    block_on_close = True
    request_queue_size = 8

    def __init__(self, path, runner):
        self.runner = runner
        self.connections = threading.BoundedSemaphore(12)
        super().__init__(path, Handler)

    def process_request(self, request, client_address):
        if not self.connections.acquire(blocking=False):
            request.close(); return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.connections.release(); raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connections.release()

    def handle_error(self, request, client_address):
        pass  # Never print request bodies, generated code or process output.


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'OntologySkillRunner/1'
    sys_version = ''

    def setup(self):
        self.request.settimeout(2)
        super().setup()

    def log_message(self, *args):
        pass

    def respond(self, status, value):
        self.close_connection = True
        wire = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(wire)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers(); self.wfile.write(wire); self.wfile.flush()
        except (BrokenPipeError, ConnectionError, OSError):
            pass

    def send_error(self, code, message=None, explain=None):
        self.respond(code, {'code': 'INVALID_TEST_REQUEST'})

    def disconnected(self):
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                return False
            return not self.connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        except (OSError, ValueError):
            return True

    def do_GET(self):
        if self.path != '/status':
            self.respond(404, {'code': 'INVALID_TEST_REQUEST'}); return
        self.respond(200, {'available': bool(self.server.runner.image_id) and not self.server.runner.stop.is_set() and not self.server.runner.unhealthy.is_set()})

    def do_POST(self):
        if (self.path not in {'/test', '/run'} or self.headers.get('Transfer-Encoding') is not None
                or self.headers.get('Content-Type', '').split(';')[0] != 'application/json'
                or len(self.headers.get_all('Content-Length', [])) != 1):
            self.respond(400, {'code': 'INVALID_TEST_REQUEST'}); return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_REQUEST:
                raise ValueError()
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise ValueError()
            body = strict_json(raw)
            if self.path == '/run':
                if not isinstance(body, dict) or set(body) != {'files', 'entrypoint', 'input', 'timeout_seconds'}:
                    raise ValueError()
                if type(body['timeout_seconds']) not in (int, float) or not 1 <= body['timeout_seconds'] <= 30:
                    raise ValueError()
                validate_request({'files': body['files'], 'entrypoint': body['entrypoint'],
                    'test_cases': [{'name': 'readonly', 'input': body['input'], 'expected_output': None}]})
            else:
                body = validate_request(body)
        except (ValueError, TypeError, RecursionError, UnicodeError, OSError):
            self.respond(400, {'code': 'INVALID_TEST_REQUEST'}); return
        runner = self.server.runner
        if not runner.image_id or runner.stop.is_set() or runner.unhealthy.is_set():
            self.respond(503, {'code': 'RUNNER_UNAVAILABLE'}); return
        if not runner.gate.acquire(blocking=False):
            self.respond(429, {'code': 'RUNNER_BUSY'}); return
        try:
            result = (runner.execute_readonly(body, self.disconnected) if self.path == '/run'
                      else runner.execute(body, self.disconnected))
            self.respond(200, result)
        except Exception:
            self.respond(503, {'code': 'RUNNER_UNAVAILABLE'})
        finally:
            runner.gate.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True, type=Path)
    parser.add_argument('--socket-gid', type=int, help='Deployment-owned group for the 0660 API socket.')
    parser.add_argument('--check', action='store_true', help='Check the fixed runner health route and exit.')
    args = parser.parse_args()
    path = args.socket.absolute()
    if len(os.fsencode(str(path))) > 100:
        parser.error('Socket path is too long')
    if args.check:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(2); probe.connect(str(path))
                probe.sendall(b'GET /status HTTP/1.1\r\nHost: runner\r\nConnection: close\r\n\r\n')
                data = bytearray()
                while chunk := probe.recv(4096):
                    data.extend(chunk)
                    if len(data) > 4096: raise ValueError()
            if strict_json(bytes(data).split(b'\r\n\r\n', 1)[1]).get('available') is not True:
                raise ValueError()
        except (OSError, ValueError, IndexError, AttributeError):
            raise SystemExit(1)
        return
    path.parent.mkdir(parents=True, mode=0o750, exist_ok=True)
    if path.exists() or path.is_symlink():
        mode = path.lstat()
        if not stat.S_ISSOCK(mode.st_mode) or mode.st_uid != os.getuid():
            parser.error('Refusing to replace an existing path')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(.5)
            try: probe.connect(str(path))
            except OSError: path.unlink()
            else: parser.error('Runner socket is already active')
    runner = DockerRunner(path)
    server = RunnerServer(str(path), runner)
    os.chmod(path, 0o660)
    if args.socket_gid is not None:
        os.chown(path, -1, args.socket_gid)
        os.chown(path.parent, -1, args.socket_gid)
        os.chmod(path.parent, 0o750)
    import signal
    def stop(*_):
        runner.stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=.1)
    finally:
        runner.stop.set()
        server.server_close()
        runner.reconcile()
        path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
