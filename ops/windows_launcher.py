"""Native Windows portable launcher. Secrets never appear in process arguments.

The detached supervisor owns every server through a kill-on-close Windows Job.
Only its authenticated loopback control channel may stop that installation;
stored PIDs are diagnostic metadata and are never used to terminate processes.
"""
from __future__ import annotations

import argparse
from contextlib import suppress
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.platform_io import (atomic_write, ensure_private_directory, file_lock,
                                 reject_reparse_points, secure_open)

LOCAL = ROOT / 'data' / 'local'
STATE = LOCAL / 'runtime-state.json'
SETTINGS = LOCAL / 'installation.json'
READY = LOCAL / 'initialized.json'
MAX_CONTROL = 8192
START_TIMEOUT = 360
_ERRORS = {
    'WINDOWS_REQUIRED': '需要 Windows 10/11 64位系统和交付包内的64位Python。',
    'RUNTIME_MISSING': '运行包不完整，请重新解压完整的Windows运行包。',
    'RUNTIME_INVALID': '运行时清单或文件校验不一致，请重新获取完整运行包。',
    'LOCAL_NTFS_REQUIRED': '请将完整运行包解压到本机固定NTFS磁盘，再双击启动。网络共享、FAT/exFAT和重解析目录不满足本平台的凭据权限要求。',
    'PORT_BUSY': '端口已被占用；请关闭占用程序，或使用 --port / --mysql-port 指定其他端口。平台不会终止其他程序。',
    'PASSWORD_REQUIRED': '首次运行需要设置总部超管密码，至少12位且包含字母和数字。',
    'DATABASE_INIT_FAILED': '本地数据库初始化失败。已有数据不会被删除，请检查可写空间和运行包完整性。',
    'DATABASE_START_FAILED': '本地数据库未能启动。请检查端口和 data/mysql 下的数据库错误日志。',
    'MIGRATION_FAILED': '数据库迁移或管理员初始化失败；已停止本次启动的进程，保留数据供诊断。如首次初始化曾中断，请使用当时设置的管理员账号和密码重试，平台不会重置已有账号。',
    'API_START_FAILED': '平台接口未就绪；已停止本次启动的进程。',
    'CHILD_EXITED': '平台服务意外退出；已回收本安装实例的其他进程，数据予以保留。',
    'CONTROL_UNAVAILABLE': '无法连接本安装实例的控制通道；不会依据旧PID终止任何进程。',
    'START_TIMEOUT': '启动超过等待时间；已请求本安装实例停止，请检查运行状态。',
    'START_CANCELLED': '本次启动已取消，正在回收本安装实例的进程。',
    'INSTANCE_BUSY': '本安装实例正在执行另一项启动或关闭操作，请稍后重试。',
    'LOCAL_STATE_INVALID': '本安装实例的私有配置不完整或无效；为保护现有数据，未重置数据库凭据。',
    'INTERNAL_ERROR': '启动器未能完成操作；未终止其他程序，也未删除已有数据。',
}


class LaunchError(Exception):
    def __init__(self, code, *, diagnostic=None):
        self.code = code
        self.diagnostic = diagnostic
        super().__init__(_ERRORS.get(code, _ERRORS['INTERNAL_ERROR']))


def safe_diagnostic(exception, phase):
    """Fixed operation metadata only: never exception text, paths or output."""
    result = {'phase': phase, 'exception': type(exception).__name__}
    for name in ('code', 'operation'):
        value = getattr(exception, name, None)
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,63}', value):
            result[name] = value
    for name in ('native_error', 'winerror', 'errno'):
        value = getattr(exception, name, None)
        if type(value) is int and 0 <= value < 2**32:
            result[name] = value
    return result


def mysql_failure(child, phase, *, code='DATABASE_INIT_FAILED', output=b''):
    """Report only the owned process exit and MySQL's stable numeric IDs."""
    diagnostic = {'phase': phase}
    status = child.poll()
    if type(status) is int:
        diagnostic['process_exit'] = status & 0xffffffff
    identifiers = sorted(set(re.findall(rb'\bMY-[0-9]{6}\b', output)))[:12]
    if identifiers:
        diagnostic['mysql_codes'] = [item.decode('ascii') for item in identifiers]
    return LaunchError(code, diagnostic=diagnostic)


def read_json(path):
    with secure_open(path, 'rb', require_private=True) as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise LaunchError('LOCAL_STATE_INVALID')
    return json.loads(raw)


def write_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def runtime_path(relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise LaunchError('RUNTIME_INVALID')
    parts = relative.split('/')
    if parts[0] != 'runtime' or any(part in ('', '.', '..') for part in parts):
        raise LaunchError('RUNTIME_INVALID')
    path = ROOT.joinpath(*parts)
    reject_reparse_points(path)
    if not path.is_file():
        raise LaunchError('RUNTIME_MISSING')
    return path


def check_runtime():
    if os.name != 'nt' or sys.maxsize <= 2**32 or sys.getwindowsversion().major < 10:
        raise LaunchError('WINDOWS_REQUIRED')
    try:
        manifest = json.loads((ROOT / 'runtime' / 'manifest.json').read_text(encoding='utf-8-sig'))
        if manifest['schema_version'] != 1 or manifest['platform'] != 'win_amd64':
            raise LaunchError('RUNTIME_INVALID')
        python = runtime_path(manifest['python']['executable'])
        if not python.samefile(sys.executable):
            raise LaunchError('WINDOWS_REQUIRED')
        result = {'python': python, 'mysqld': runtime_path(manifest['mysql']['server']),
                  'mysql': runtime_path(manifest['mysql']['client']),
                  'mysqladmin': runtime_path(manifest['mysql']['admin'])}
        harness = runtime_path(manifest['harness']['executable'])
        skill_python = runtime_path(manifest.get('skill_python', {}).get('executable', 'runtime/skill-python/python.exe'))
        file_hashes = {row['path']: row['sha256'] for row in manifest.get('files', []) if isinstance(row, dict)}
        checks = ((python, manifest['python'].get('sha256')), (harness, manifest['harness'].get('sha256')),
                  (result['mysqld'], manifest['mysql'].get('server_sha256')),
                  (result['mysql'], manifest['mysql'].get('client_sha256')),
                  (result['mysqladmin'], manifest['mysql'].get('admin_sha256')),
                  (skill_python, manifest.get('skill_python', {}).get('sha256')))
        for path, configured in checks:
            expected = configured or file_hashes.get(path.relative_to(ROOT).as_posix(), '')
            if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
                raise LaunchError('RUNTIME_INVALID')
            with path.open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise LaunchError('RUNTIME_INVALID')
        if not (ROOT / 'frontend' / 'dist' / 'index.html').is_file():
            raise LaunchError('RUNTIME_MISSING')
        return result
    except LaunchError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise LaunchError('RUNTIME_MISSING') from None


def check_local_volume():
    """Credentials never silently fall back to an ACL-less or remote volume."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    volume = ctypes.create_unicode_buffer(32768)
    filesystem = ctypes.create_unicode_buffer(128)
    kernel.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumePathNameW.restype = wintypes.BOOL
    kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    kernel.GetDriveTypeW.restype = wintypes.UINT
    kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumeInformationW.restype = wintypes.BOOL
    try:
        reject_reparse_points(ROOT)
        if (str(ROOT).startswith('\\\\') or not kernel.GetVolumePathNameW(str(ROOT), volume, len(volume))
                or kernel.GetDriveTypeW(volume.value) != 3
                or not kernel.GetVolumeInformationW(volume.value, None, 0, None, None, None, filesystem, len(filesystem))
                or filesystem.value.upper() != 'NTFS'):
            raise LaunchError('LOCAL_NTFS_REQUIRED')
    except OSError:
        raise LaunchError('LOCAL_NTFS_REQUIRED') from None


def valid_port(value):
    if type(value) is not int or not 1024 <= value <= 65535:
        raise LaunchError('LOCAL_STATE_INVALID')
    return value


def get_installation(port=None, mysql_port=None):
    ensure_private_directory(LOCAL)
    if SETTINGS.exists():
        data = read_json(SETTINGS)
        if (not isinstance(data, dict) or set(data) != {'installation_id', 'root_password', 'runtime_password', 'api_port', 'mysql_port'}
                or not re.fullmatch(r'[0-9a-f]{32}', data.get('installation_id', ''))
                or any(not re.fullmatch(r'[0-9a-f]{64}', data.get(key, '')) for key in ('root_password', 'runtime_password'))):
            raise LaunchError('LOCAL_STATE_INVALID')
    else:
        # Missing credentials beside an existing database are never regenerated.
        if (ROOT / 'data' / 'mysql').exists():
            raise LaunchError('LOCAL_STATE_INVALID')
        data = {'installation_id': uuid.uuid4().hex, 'root_password': secrets.token_hex(32),
                'runtime_password': secrets.token_hex(32), 'api_port': 8765, 'mysql_port': 18765}
    if port is not None:
        data['api_port'] = valid_port(port)
    if mysql_port is not None:
        data['mysql_port'] = valid_port(mysql_port)
    valid_port(data['api_port']); valid_port(data['mysql_port'])
    if data['api_port'] == data['mysql_port']:
        raise LaunchError('PORT_BUSY')
    write_json(SETTINGS, data)
    return data


def base_environment():
    # Do not inherit shell MYSQL_*, provider keys, PYTHONPATH, NODE_OPTIONS or
    # caller/source bearer credentials into platform processes.
    keep = {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA',
            'COMSPEC', 'PROCESSOR_ARCHITECTURE', 'PROCESSOR_ARCHITEW6432', 'NUMBER_OF_PROCESSORS',
            'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
            'NODE_EXTRA_CA_CERTS', 'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'ONTOLOGY_SOURCE_CREDENTIALS_FILE'}
    result = {key: value for key, value in os.environ.items() if key.upper() in keep}
    system = Path(result.get('SYSTEMROOT', result.get('SystemRoot', r'C:\Windows')))
    result['PATH'] = os.pathsep.join(map(str, (ROOT / 'runtime' / 'python', ROOT / 'runtime' / 'mysql' / 'bin', system / 'System32', system)))
    result.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    return result


def application_environment(settings, *, root=False):
    result = base_environment()
    port = settings['api_port']
    result.update(MYSQL_HOST='127.0.0.1', MYSQL_PORT=str(settings['mysql_port']),
                  MYSQL_DATABASE='ontology_one_v020', MYSQL_USER='root' if root else 'ontology_runtime',
                  MYSQL_PASSWORD=settings['root_password'] if root else settings['runtime_password'],
                  ONTOLOGY_RUNTIME_DB_PASSWORD=settings['runtime_password'],
                  ONTOLOGY_STORAGE_ROOT=str(ROOT / 'data' / 'tenants'), ONTOLOGY_WORKER_ENABLED='0',
                  ONTOLOGY_SKILL_PYTHON_ROOT=str(ROOT / 'runtime' / 'skill-python'),
                  ONTOLOGY_TRUSTED_ORIGINS=f'http://127.0.0.1:{port},http://localhost:{port}',
                  ONTOLOGY_COOKIE_SECURE='0', ONTOLOGY_MCP_ALLOWED_HOSTS='localhost:*,127.0.0.1:*',
                  ONTOLOGY_DB_POOL_SIZE='8', ONTOLOGY_HARNESS_MAX_CONCURRENT='2')
    if not root:
        result.pop('ONTOLOGY_RUNTIME_DB_PASSWORD')
    return result


def assert_port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(('127.0.0.1', port))
        except OSError:
            raise LaunchError('PORT_BUSY') from None


def control(operation, *, timeout=2):
    try:
        state = read_json(STATE)
        if not isinstance(state, dict) or state.get('root') != str(ROOT) or not re.fullmatch(r'[0-9a-f]{64}', state.get('token', '')):
            return None
        settings = read_json(SETTINGS)
        if state.get('installation_id') != settings.get('installation_id'):
            return None
        with socket.create_connection(('127.0.0.1', valid_port(state['control_port'])), timeout=timeout) as channel:
            channel.sendall(json.dumps({'token': state['token'], 'installation_id': state['installation_id'],
                                       'root': str(ROOT), 'operation': operation}).encode() + b'\n')
            data = bytearray()
            while b'\n' not in data and len(data) <= MAX_CONTROL:
                chunk = channel.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)
            value = json.loads(data)
            if isinstance(value, dict) and value.get('installation_id') == settings['installation_id']:
                return value
    except (OSError, ValueError, TypeError, KeyError, LaunchError):
        pass
    return None


def drain(stream):
    # No process stdout/stderr is retained. Platform audits are stored through
    # their normal structured channels; startup errors use fixed safe codes.
    try:
        while stream.read(16384):
            pass
    except (OSError, ValueError):
        pass


class Supervisor:
    def __init__(self, runtime, settings):
        self.runtime, self.settings = runtime, settings
        self.root = str(ROOT)
        self.children, self.state_lock = {}, threading.Lock()
        self.drain_threads = []
        self.stop_event, self.finished = threading.Event(), threading.Event()
        self.phase, self.error = 'starting', None
        self.failure_diagnostic = None
        self.token = secrets.token_hex(32)
        self.channel = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.channel.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self.channel.bind(('127.0.0.1', 0)); self.channel.listen(4); self.channel.settimeout(.5)

    def status(self):
        with self.state_lock:
            return {'installation_id': self.settings['installation_id'], 'running': not self.finished.is_set(),
                    'ready': self.phase == 'ready', 'phase': self.phase, 'error': self.error,
                    'api_port': self.settings['api_port'],
                    'processes': {name: {'pid': child.pid, 'alive': child.poll() is None} for name, child in self.children.items()}}

    def phase_to(self, phase):
        with self.state_lock:
            self.phase = phase

    def serve_control(self):
        while not self.finished.is_set():
            try:
                connection, _ = self.channel.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with connection:
                connection.settimeout(1)
                try:
                    raw = bytearray()
                    while b'\n' not in raw and len(raw) <= MAX_CONTROL:
                        chunk = connection.recv(4096)
                        if not chunk:
                            break
                        raw.extend(chunk)
                    value = json.loads(raw)
                    if (len(raw) > MAX_CONTROL or not isinstance(value, dict)
                            or set(value) != {'token', 'installation_id', 'root', 'operation'}
                            or not isinstance(value['token'], str) or not hmac.compare_digest(value['token'], self.token)
                            or value['installation_id'] != self.settings['installation_id']
                            or value['root'] != self.root
                            or value['operation'] not in ('status', 'stop')):
                        continue
                    if value['operation'] == 'stop':
                        self.stop_event.set()
                    connection.sendall(json.dumps(self.status()).encode() + b'\n')
                except (OSError, ValueError, TypeError, KeyError):
                    continue

    def spawn(self, name, args, environment):
        from backend.windows_process import spawn
        child = spawn(list(map(str, args)), cwd=str(ROOT), env=environment)
        with self.state_lock:
            self.children[name] = child
        return child

    def persistent(self, name, args, environment):
        child = self.spawn(name, args, environment)
        # PIPEs must never fill while long-running services wait for shutdown.
        for stream in (child.stdout, child.stderr):
            thread = threading.Thread(target=drain, args=(stream,), daemon=True)
            thread.start()
            self.drain_threads.append(thread)
        with suppress(OSError):
            child.stdin.close()
        return child

    def finish_child(self, name):
        with self.state_lock:
            child = self.children.pop(name, None)
        if child:
            child.close()

    def secure_new_database(self, mysql_root, staging, environment):
        """Set and verify root credentials while TCP networking is disabled."""
        init, defaults = LOCAL / 'mysql-init.sql', LOCAL / 'mysql-client.cnf'
        shared_name = 'OntologyBootstrap' + self.settings['installation_id']
        sql = "ALTER USER 'root'@'localhost' IDENTIFIED BY '" + self.settings['root_password'] + "';\n"
        atomic_write(init, sql.encode('ascii'))
        atomic_write(defaults, ('[client]\nuser=root\npassword=' + self.settings['root_password'] + '\n').encode('ascii'))
        server = None
        try:
            server = self.persistent('mysql_secure', [self.runtime['mysqld'], '--no-defaults', '--console',
                '--basedir=' + str(mysql_root), '--datadir=' + str(staging), '--skip-networking',
                '--mysqlx=OFF', '--shared-memory', '--shared-memory-base-name=' + shared_name,
                '--local-infile=OFF', '--secure-file-priv=NULL', '--skip-log-bin', '--init-file=' + str(init)], environment)
            def authenticated():
                if server.poll() is not None:
                    raise mysql_failure(server, 'securing_database')
                probe = self.spawn('mysql_bootstrap_probe', [self.runtime['mysql'], '--defaults-file=' + str(defaults),
                    '--protocol=MEMORY', '--shared-memory-base-name=' + shared_name, '--connect-timeout=1',
                    '--batch', '--skip-column-names', '--execute=SELECT 1'], environment)
                try:
                    output, _ = probe.communicate(timeout=3, cancelled=self.stop_event, max_stdout=1024, max_stderr=4096)
                    return probe.wait(timeout=1) == 0 and output.strip() == b'1'
                finally:
                    self.finish_child('mysql_bootstrap_probe')
            self.wait_until(authenticated, 60, 'DATABASE_INIT_FAILED')
            shutdown = self.spawn('mysql_bootstrap_shutdown', [self.runtime['mysqladmin'], '--defaults-file=' + str(defaults),
                '--protocol=MEMORY', '--shared-memory-base-name=' + shared_name, '--connect-timeout=2', 'shutdown'], environment)
            try:
                shutdown.communicate(timeout=20, cancelled=self.stop_event, max_stdout=1024, max_stderr=4096)
                if shutdown.wait(timeout=1) != 0 or server.wait(timeout=20) != 0:
                    raise LaunchError('DATABASE_INIT_FAILED')
            finally:
                self.finish_child('mysql_bootstrap_shutdown')
        finally:
            if server:
                self.finish_child('mysql_secure')
            for path in (init, defaults):
                with suppress(OSError): path.unlink()

    def wait_until(self, predicate, timeout, error):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.stop_event.is_set():
                raise LaunchError('START_CANCELLED')
            if predicate():
                return
            self.stop_event.wait(.2)
        raise LaunchError(error)

    def database_connection(self):
        import pymysql
        return pymysql.connect(host='127.0.0.1', port=self.settings['mysql_port'], user='root',
                               password=self.settings['root_password'], connect_timeout=1, read_timeout=2, write_timeout=2)

    def database_ready(self):
        if self.children['mysql'].poll() is not None:
            raise mysql_failure(self.children['mysql'], self.phase, code='DATABASE_START_FAILED')
        try:
            with self.database_connection() as connection, connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                return cursor.fetchone() == (1,)
        except Exception:
            return False

    def start_services(self, bootstrap):
        for port in (self.settings['api_port'], self.settings['mysql_port']):
            assert_port_free(port)
        mysql_root = self.runtime['mysqld'].parent.parent
        mysql_data = ROOT / 'data' / 'mysql'
        environment = base_environment()
        if not mysql_data.exists():
            self.phase_to('initializing_database')
            # Initialize in a new private staging directory. A failed first run
            # cannot leave a partial database at the durable data/mysql path.
            staging = Path(tempfile.mkdtemp(prefix='.mysql-initialize-', dir=ROOT / 'data'))
            ensure_private_directory(staging)
            child = self.spawn('mysql_initialize', [self.runtime['mysqld'], '--no-defaults', '--initialize-insecure',
                '--basedir=' + str(mysql_root), '--datadir=' + str(staging)], environment)
            try:
                output, errors = child.communicate(timeout=120, cancelled=self.stop_event, max_stdout=65536, max_stderr=65536)
                if child.wait(timeout=1) != 0:
                    raise mysql_failure(child, self.phase, output=output + errors)
                self.finish_child('mysql_initialize')
                self.secure_new_database(mysql_root, staging, environment)
                staging.rename(mysql_data)
            finally:
                if staging.exists():
                    # Only this call's newly created staging directory is removed.
                    # Existing data/mysql and previous failed runs are untouched.
                    with suppress(Exception): child.terminate_tree()
                    with suppress(Exception): self.finish_child('mysql_initialize')
                    import shutil
                    with suppress(OSError): shutil.rmtree(staging)
        self.phase_to('starting_database')
        self.persistent('mysql', [self.runtime['mysqld'], '--no-defaults', '--basedir=' + str(mysql_root),
            '--datadir=' + str(mysql_data), '--bind-address=127.0.0.1', '--port=' + str(self.settings['mysql_port']),
            '--mysqlx=OFF', '--local-infile=OFF', '--secure-file-priv=NULL', '--skip-log-bin',
            '--character-set-server=utf8mb4', '--collation-server=utf8mb4_unicode_ci',
            '--max-connections=128', '--log-error=' + str(mysql_data / 'server.err'),
            '--pid-file=' + str(mysql_data / 'server.pid')], environment)
        self.wait_until(self.database_ready, 90, 'DATABASE_START_FAILED')
        self.phase_to('migrating')
        migration = self.spawn('migration', [self.runtime['python'], '-I', '-X', 'utf8', '-B', ROOT / 'ops' / 'windows_initialize.py'],
                               application_environment(self.settings, root=True))
        migration.communicate(json.dumps(bootstrap).encode('utf-8'), timeout=180,
                              cancelled=self.stop_event, max_stdout=65536, max_stderr=65536)
        if migration.wait(timeout=1) != 0:
            raise LaunchError('MIGRATION_FAILED')
        self.finish_child('migration'); bootstrap.clear()
        write_json(READY, {'installation_id': self.settings['installation_id'], 'version': '0.5.0'})
        self.phase_to('starting_api')
        environment = application_environment(self.settings)
        api = self.persistent('api', [self.runtime['python'], '-I', '-X', 'utf8', '-B', '-m', 'uvicorn', 'backend.main:app',
            '--app-dir', str(ROOT), '--host', '127.0.0.1', '--port', str(self.settings['api_port']),
            '--workers', '2', '--no-access-log'], environment)
        self.persistent('worker', [self.runtime['python'], '-I', '-X', 'utf8', '-B', ROOT / 'scripts' / 'run_worker.py'], environment)
        def ready():
            if api.poll() is not None or self.children['worker'].poll() is not None:
                raise LaunchError('API_START_FAILED')
            try:
                direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with direct.open(f"http://127.0.0.1:{self.settings['api_port']}/api/health", timeout=1) as response:
                    return response.status == 200
            except OSError:
                return False
        self.wait_until(ready, 60, 'API_START_FAILED')
        self.phase_to('ready')
        while not self.stop_event.wait(.5):
            if any(self.children[name].poll() is not None for name in ('mysql', 'api', 'worker')):
                raise LaunchError('CHILD_EXITED')

    def cleanup(self):
        self.phase_to('stopping')
        for name in ('api', 'worker', 'migration', 'mysql_bootstrap_probe', 'mysql_bootstrap_shutdown', 'mysql_secure', 'mysql_initialize'):
            child = self.children.get(name)
            if child:
                with suppress(Exception): child.terminate_tree()
                with suppress(Exception): child.close()
        mysql = self.children.get('mysql')
        if mysql:
            if mysql.poll() is None:
                try:
                    with self.database_connection() as connection, connection.cursor() as cursor:
                        cursor.execute('SHUTDOWN')
                except Exception:
                    pass
                with suppress(Exception): mysql.wait(timeout=15)
            with suppress(Exception): mysql.terminate_tree()
            with suppress(Exception): mysql.close()
        for thread in self.drain_threads:
            thread.join(timeout=3)
            if thread.is_alive():
                self.error = self.error or 'INTERNAL_ERROR'
        self.phase_to('failed' if self.error else 'stopped')
        # Leave a non-secret failure receipt. The live control token is removed.
        with suppress(Exception):
            write_json(LOCAL / 'last-result.json', {'installation_id': self.settings['installation_id'],
                       'phase': self.phase, 'error': self.error, 'diagnostic': self.failure_diagnostic, 'finished_at': time.time()})
        with suppress(OSError): STATE.unlink()
        self.finished.set(); self.channel.close()

    def run(self, bootstrap):
        write_json(STATE, {'root': str(ROOT), 'installation_id': self.settings['installation_id'],
                   'token': self.token, 'control_port': self.channel.getsockname()[1]})
        threading.Thread(target=self.serve_control, daemon=True).start()
        try:
            self.start_services(bootstrap)
        except LaunchError as exc:
            self.error = exc.code
            self.failure_diagnostic = exc.diagnostic or safe_diagnostic(exc, self.phase)
        except BaseException as exc:
            self.error = 'START_CANCELLED' if self.stop_event.is_set() else 'INTERNAL_ERROR'
            self.failure_diagnostic = safe_diagnostic(exc, self.phase)
        finally:
            bootstrap.clear()
            self.cleanup()


def bootstrap_input(non_interactive):
    if os.path.lexists(READY):
        try:
            marker, settings = read_json(READY), read_json(SETTINGS)
            if (not isinstance(marker, dict) or set(marker) != {'installation_id', 'version'}
                    or marker['installation_id'] != settings['installation_id'] or marker['version'] != '0.5.0'):
                raise ValueError('Invalid initialization marker')
            return {}
        except (OSError, ValueError, KeyError, TypeError):
            raise LaunchError('LOCAL_STATE_INVALID') from None
    username = os.environ.get('ONTOLOGY_BOOTSTRAP_USERNAME', 'group.admin')
    password = os.environ.get('ONTOLOGY_BOOTSTRAP_PASSWORD', '')
    if not password and not non_interactive:
        print('首次运行，请设置总部超管的初始密码（输入不回显）。如初始化曾中断，请沿用当时的账号和密码。')
        password = getpass.getpass('初始密码：')
        if password != getpass.getpass('再次输入：'):
            raise LaunchError('PASSWORD_REQUIRED')
    if (not isinstance(password, str) or not 12 <= len(password) <= 128
            or not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password)):
        raise LaunchError('PASSWORD_REQUIRED')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{2,79}', username):
        raise LaunchError('PASSWORD_REQUIRED')
    return {'username': username, 'password': password}


def start(args, runtime):
    active = control('status')
    if active:
        if not active['ready']:
            raise LaunchError('INSTANCE_BUSY')
        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{active['api_port']}")
        return active
    try:
        with secure_open(LOCAL / 'service.lock', 'a+b', create_parents=True) as stream, file_lock(stream, blocking=False):
            pass
    except (BlockingIOError, OSError):
        raise LaunchError('CONTROL_UNAVAILABLE') from None
    settings = get_installation(args.port, args.mysql_port)
    for port in (settings['api_port'], settings['mysql_port']):
        assert_port_free(port)
    bootstrap = bootstrap_input(args.non_interactive)
    with suppress(OSError): (LOCAL / 'last-result.json').unlink()
    supervisor = subprocess.Popen([str(runtime['python']), '-I', '-X', 'utf8', '-B', str(Path(__file__).resolve()), 'supervise'],
        cwd=str(ROOT), env=base_environment(), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    supervisor.stdin.write(json.dumps(bootstrap).encode('utf-8')); supervisor.stdin.close(); bootstrap.clear()
    began, phase = time.monotonic(), None
    while time.monotonic() - began < START_TIMEOUT:
        active = control('status')
        if active:
            if active['ready']:
                if not args.no_browser:
                    webbrowser.open(f"http://127.0.0.1:{settings['api_port']}")
                return active
            if active['phase'] != phase and not args.json:
                phase = active['phase']
                print({'starting': '准备启动', 'initializing_database': '首次初始化本地数据库',
                       'starting_database': '启动本地数据库', 'migrating': '迁移机构工作区并初始化账户',
                       'starting_api': '启动平台服务', 'stopping': '正在回收本次启动进程'}.get(phase, phase), flush=True)
        if supervisor.poll() is not None:
            if (LOCAL / 'last-result.json').exists():
                receipt = read_json(LOCAL / 'last-result.json')
                raise LaunchError(receipt.get('error') or 'INTERNAL_ERROR', diagnostic=receipt.get('diagnostic'))
            raise LaunchError('INTERNAL_ERROR')
        time.sleep(.3)
    with suppress(Exception): stop()
    raise LaunchError('START_TIMEOUT')


def stop():
    active = control('stop')
    if active is None:
        # Only call a missing supervisor stopped when its lifetime lock is free.
        try:
            with secure_open(LOCAL / 'service.lock', 'a+b', create_parents=True) as stream, file_lock(stream, blocking=False):
                return {'running': False, 'ready': False, 'phase': 'stopped'}
        except (BlockingIOError, OSError):
            raise LaunchError('CONTROL_UNAVAILABLE') from None
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if control('status') is None:
            try:
                with secure_open(LOCAL / 'service.lock', 'a+b') as stream, file_lock(stream, blocking=False):
                    return {'installation_id': active['installation_id'], 'running': False, 'ready': False, 'phase': 'stopped'}
            except (BlockingIOError, OSError):
                pass
        time.sleep(.2)
    raise LaunchError('CONTROL_UNAVAILABLE')


def current_status():
    active = control('status')
    if active is not None:
        return active
    try:
        with secure_open(LOCAL / 'service.lock', 'a+b', create_parents=True) as stream, file_lock(stream, blocking=False):
            return {'running': False, 'ready': False, 'phase': 'stopped'}
    except BlockingIOError:
        raise LaunchError('CONTROL_UNAVAILABLE') from None


def main():
    # -I deliberately ignores PYTHONIOENCODING. Set stream encodings explicitly
    # so redirected Windows CI output and the Chinese console use the same text.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Ontology One 原生Windows启动器')
    parser.add_argument('command', nargs='?', choices=('start', 'stop', 'status', 'supervise'), default='start')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--non-interactive', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--port', type=int)
    parser.add_argument('--mysql-port', type=int)
    args = parser.parse_args()
    try:
        if os.name != 'nt' or sys.maxsize <= 2**32:
            raise LaunchError('WINDOWS_REQUIRED')
        check_local_volume()
        runtime = check_runtime() if args.check or args.command in ('start', 'supervise') else None
        if args.check:
            result = {'ready': True, 'platform': 'win_amd64', 'runtime_valid': True}
        elif args.command == 'supervise':
            ensure_private_directory(LOCAL)
            with secure_open(LOCAL / 'service.lock', 'a+b') as stream, file_lock(stream, blocking=False):
                bootstrap = json.loads(sys.stdin.buffer.read(4096))
                Supervisor(runtime, get_installation()).run(bootstrap)
            return 0
        elif args.command == 'status':
            result = current_status()
        else:
            ensure_private_directory(LOCAL)
            try:
                with secure_open(LOCAL / 'command.lock', 'a+b') as stream, file_lock(stream, blocking=False):
                    result = start(args, runtime) if args.command == 'start' else stop()
            except BlockingIOError:
                raise LaunchError('INSTANCE_BUSY') from None
        if args.json or args.check or args.command == 'status':
            print(json.dumps(result, ensure_ascii=False))
        elif result.get('ready'):
            print(f"平台已就绪：http://127.0.0.1:{result['api_port']}\n关闭时请双击“关闭平台.bat”；关闭本窗口不会停止平台。")
        else:
            print('本安装实例已关闭，数据库和文件均已保留。')
        return 0
    except KeyboardInterrupt:
        with suppress(Exception): stop()
        print(_ERRORS['START_CANCELLED'])
        return 130
    except Exception as exc:
        code = exc.code if isinstance(exc, LaunchError) else 'INTERNAL_ERROR'
        diagnostic = getattr(exc, 'diagnostic', None) or safe_diagnostic(exc, 'launcher')
        print(json.dumps({'error': code, 'message': _ERRORS.get(code, _ERRORS['INTERNAL_ERROR']), 'diagnostic': diagnostic}, ensure_ascii=False)
              if args.json else _ERRORS.get(code, _ERRORS['INTERNAL_ERROR']), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
