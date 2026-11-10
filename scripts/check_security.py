"""Real HTTP identity/tenant regression. Never points at an existing workspace.

MYSQL_* selects a disposable test server (CREATE/DROP DATABASE privileges needed).
A unique namespace, copied backend, private storage and uvicorn port isolate the
test. Passwords/cookies are random and are never written to reports. This module
also supplies the harness for load_test.py; importing it does not start a server.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlparse
import uuid

import httpx
import pymysql
from pymysql.cursors import DictCursor


def expect(response, status=200):
    allowed = {status} if isinstance(status, int) else set(status)
    if response.status_code not in allowed:
        try:
            detail = response.json().get('detail', '(no detail)')
        except ValueError:
            detail = '(non-JSON response)'
        raise AssertionError(f'{response.request.method} {response.request.url.path}: {response.status_code}, expected {sorted(allowed)}; {str(detail)[:500]}')
    return response


class Session:
    def __init__(self, sandbox, username='', password=''):
        self.sandbox = sandbox
        self.client = httpx.Client(base_url=sandbox.url, timeout=30, trust_env=False)
        self.username, self.password, self.csrf, self.user, self.workspace = username, password, '', {}, ''
        sandbox.clients.append(self)

    def request(self, method, path, **kwargs):
        headers = dict(kwargs.pop('headers', {}))
        if method.upper() not in {'GET', 'HEAD', 'OPTIONS'} and self.csrf:
            headers.setdefault('X-CSRF-Token', self.csrf)
        return self.client.request(method, '/api' + path, headers=headers, **kwargs)

    def ok(self, method, path, **kwargs):
        return expect(self.request(method, path, **kwargs))

    def consume(self, response):
        data = expect(response).json()
        self.csrf, self.user = data['csrf_token'], data['user']
        self.workspace = data['workspace']['tenant_id']
        return data

    def login(self, change=True):
        response = self.request('POST', '/auth/login', json={'username': self.username, 'password': self.password})
        data = self.consume(response)
        if data['must_change_password'] and change:
            self.change_password()
        return response

    def change_password(self):
        updated = 'Changed9-' + secrets.token_hex(16)
        self.consume(self.request('POST', '/auth/password', json={'current_password': self.password, 'new_password': updated}))
        self.password = updated

    def create(self, ident, title, **extra):
        return self.ok('POST', '/entities', json={'id': ident, 'kind': 'data', 'title': title,
                       'summary': title + ' 业务定义', 'domain': '测试领域', **extra}).json()

    def publish(self, ident):
        for action in ('submit', 'approve', 'publish'):
            self.ok('POST', f'/entities/{ident}/action', json={'action': action})

    def switch(self, tenant):
        return self.consume(self.request('POST', '/auth/workspace', json={'tenant_id': tenant}))


class Sandbox:
    def __init__(self, port=8877, output=None, workers=1, frontend_dist=None):
        self.source = Path(__file__).resolve().parents[1]
        self.namespace = 'ontology_security_qa_' + uuid.uuid4().hex[:10]
        self.control = self.namespace + '_control'
        self.port, self.url = port, f'http://127.0.0.1:{port}'
        self.workers = workers
        self.frontend_dist = Path(frontend_dist) if frontend_dist else None
        self.output = Path(output or self.source / 'work' / 'security-report.json')
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.clients, self.process = [], None
        self.admin_name = 'qa-super-' + uuid.uuid4().hex[:8]
        self.admin_password = 'Initial9-' + secrets.token_hex(16)
        self.settings = {'host': os.getenv('MYSQL_HOST', '127.0.0.1'), 'port': int(os.getenv('MYSQL_PORT', '3306')),
                         'user': os.getenv('MYSQL_USER', 'root'), 'password': os.getenv('MYSQL_PASSWORD', '')}
        if os.getenv('ONTOLOGY_MYSQL_URL'):
            parsed = urlparse(os.environ['ONTOLOGY_MYSQL_URL'])
            self.settings.update(host=parsed.hostname, port=parsed.port or 3306,
                                 user=unquote(parsed.username or ''), password=unquote(parsed.password or ''))

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ontology-security-')
        self.root = Path(self.temp.name)
        self.storage = self.root / 'private-storage'
        shutil.copytree(self.source / 'backend', self.root / 'backend', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        if self.frontend_dist:
            shutil.copytree(self.frontend_dist, self.root / 'frontend' / 'dist')
        digest = hashlib.sha256()
        for path in sorted((self.root / 'backend').rglob('*')):
            if path.is_file():
                digest.update(path.relative_to(self.root).as_posix().encode() + b'\0' + path.read_bytes())
        self.source_sha256 = digest.hexdigest()
        self.env = os.environ.copy()
        self.env.pop('ONTOLOGY_MYSQL_URL', None)
        for key, value in self.settings.items():
            self.env['MYSQL_' + key.upper()] = str(value)
        self.env.update(MYSQL_DATABASE=self.namespace, ONTOLOGY_STORAGE_ROOT=str(self.storage),
                        ONTOLOGY_WORKER_ENABLED='0', ONTOLOGY_TRUSTED_ORIGINS=self.url,
                        ONTOLOGY_BOOTSTRAP_USERNAME=self.admin_name, ONTOLOGY_BOOTSTRAP_PASSWORD=self.admin_password)
        try:
            self.log_path = self.output.with_suffix('.server.log')
            self.log = self.log_path.open('w')
            launcher = [sys.executable]
            application = 'backend.main:app'
            if os.getenv('ONTOLOGY_QA_PROFILE'):
                profiler = self.root / '_qa_profile.py'
                profiler.write_text('''import collections, json, os, resource, sys, threading, time
from backend.main import app as real_app
stop = threading.Event()
active = threading.Event()
stacks = collections.Counter()
requests = collections.defaultdict(list)
started = None
cpu_started = None
def sample():
    while not stop.wait(0.01):
        if not active.is_set():
            continue
        for ident, frame in sys._current_frames().items():
            if ident == threading.get_ident():
                continue
            parts = []
            while frame is not None:
                filename = frame.f_code.co_filename
                parts.append(filename.rsplit('/', 1)[-1] + ':' + frame.f_code.co_name + ':' + str(frame.f_lineno))
                frame = frame.f_back
            stacks[' > '.join(reversed(parts))] += 1
thread = threading.Thread(target=sample, daemon=True)
thread.start()
def finish():
    stop.set()
    thread.join(2)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    with open(os.environ['ONTOLOGY_QA_PROFILE'] + '.' + str(os.getpid()) + '.json', 'w') as output:
        json.dump({'method':'stdlib thread-stack sampling, not CPU-time attribution; instrumented diagnostic only',
                   'pid':os.getpid(),'sample_interval_ms':10,'wall_seconds':time.monotonic()-started if started else 0,
                   'cpu_seconds':usage.ru_utime + usage.ru_stime - cpu_started if cpu_started is not None else 0,
                   'max_rss_kib':usage.ru_maxrss,'stacks':stacks.most_common(),'request_seconds':dict(requests)},output,indent=2)
async def app(scope, receive, send):
    global started, cpu_started
    if scope['type'] == 'lifespan':
        try:
            return await real_app(scope, receive, send)
        finally:
            finish()
    if not active.is_set() and scope.get('path') == '/api/entities' and b'kind=data' in scope.get('query_string', b''):
        started = time.monotonic()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu_started = usage.ru_utime + usage.ru_stime
        active.set()
    began = time.monotonic()
    try:
        return await real_app(scope, receive, send)
    finally:
        if active.is_set() and scope['type'] == 'http':
            requests[scope['method'] + ' ' + scope['path']].append(time.monotonic()-began)
''')
                self.env['ONTOLOGY_QA_PROFILE'] = str(Path(self.env['ONTOLOGY_QA_PROFILE']).resolve())
                application = '_qa_profile:app'
            command = launcher + ['-m', 'uvicorn', application, '--host', '127.0.0.1',
                                  '--port', str(self.port), '--workers', str(self.workers), '--no-access-log']
            self.process = subprocess.Popen(command, cwd=self.root, env=self.env,
                                            stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 55
            with httpx.Client(trust_env=False, timeout=1) as probe:
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        raise RuntimeError(f'Test server exited; see {self.log_path}')
                    try:
                        response = probe.get(self.url + '/api/health')
                        if response.status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    time.sleep(.2)
                else:
                    raise RuntimeError(f'Test server startup timed out; see {self.log_path}')
            self.python('from backend.security.store import initialize_control,bootstrap_superadmin; initialize_control(); bootstrap_superadmin()')
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def python(self, code):
        result = subprocess.run([sys.executable, '-c', code], cwd=self.root, env=self.env, capture_output=True, text=True, timeout=50)
        if result.returncode:
            raise RuntimeError('Isolated command failed: ' + result.stderr[-2500:])
        return result.stdout

    def sql(self, statement, args=(), database=None):
        assert database is None or database.startswith(self.namespace), 'Only this run may be accessed'
        with pymysql.connect(**self.settings, database=database, cursorclass=DictCursor, charset='utf8mb4', autocommit=True) as conn:
            with conn.cursor() as cursor:
                cursor.execute(statement, args)
                return list(cursor.fetchall()) if cursor.description else cursor.rowcount

    def tenant_db(self, tenant):
        rows = self.sql('SELECT db_name FROM sec_tenants WHERE id=%s', (tenant,), self.control)
        assert len(rows) == 1
        return rows[0]['db_name']

    def user(self, admin, tenant, role, suffix=None, change=True):
        username = 'qa-' + (suffix or uuid.uuid4().hex[:10])
        password = 'Temporary9-' + secrets.token_hex(16)
        response = admin.ok('POST', '/admin/users', json={'username': username, 'display_name': username,
                            'tenant_id': tenant, 'role': role, 'password': password, 'must_change_password': change}).json()
        actor = Session(self, username, password)
        actor.login(change=True)
        assert actor.user['id'] == response['id']
        return actor

    def business_snapshot(self, tenant):
        database = self.tenant_db(tenant)
        tables = self.sql('SHOW TABLES', database=database)
        result = {}
        for row in tables:
            table = next(iter(row.values()))
            records = self.sql(f'SELECT * FROM `{table}`', database=database)
            encoded = sorted(json.dumps(item, sort_keys=True, default=str, ensure_ascii=False) for item in records)
            result[table] = hashlib.sha256('\n'.join(encoded).encode()).hexdigest()
        return result

    def __exit__(self, *exc):
        for client in self.clients:
            client.client.close()
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        if getattr(self, 'log', None):
            self.log.close()
        try:
            with pymysql.connect(**self.settings, autocommit=True) as conn, conn.cursor() as cursor:
                cursor.execute('SHOW DATABASES')
                owned = [row[0] for row in cursor.fetchall() if row[0].startswith(self.namespace)]
                for name in owned:
                    cursor.execute(f'DROP DATABASE `{name}`')
        finally:
            if getattr(self, 'temp', None):
                self.temp.cleanup()


def run_checks(box):
    checks = []
    def check(name, operation):
        started = time.monotonic()
        try:
            operation()
            result = {'name': name, 'passed': True}
        except Exception as exc:
            result = {'name': name, 'passed': False, 'error': str(exc)[:1500]}
        result['seconds'] = round(time.monotonic() - started, 3)
        checks.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    anon = Session(box)
    expect(anon.request('GET', '/entities'), 401)
    admin = Session(box, box.admin_name, box.admin_password)
    login_response = admin.login(change=False)
    assert admin.user['must_change_password']
    expect(admin.request('GET', '/entities'), 403)
    old_cookie = admin.client.cookies.get('ontology_session')
    admin.change_password()
    assert admin.client.cookies.get('ontology_session') != old_cookie
    expect(anon.request('GET', '/auth/session', headers={'Cookie': 'ontology_session=' + old_cookie}), 401)
    checks.append({'name': 'password login, forced change and old session invalidation', 'passed': True})
    assert 'httponly' in login_response.headers['set-cookie'].lower() and 'samesite=' in login_response.headers['set-cookie'].lower()
    a = box.user(admin, 'sub-01', 'modeler', 'company-a')
    b = box.user(admin, 'sub-02', 'modeler', 'company-b')
    reviewer = box.user(admin, 'sub-01', 'reviewer', 'review-a')
    local_admin = box.user(admin, 'sub-01', 'admin', 'admin-a')
    department = box.user(admin, 'dept-01', 'admin', 'department-a')
    other_dept = box.user(admin, 'dept-02', 'modeler', 'department-b')
    headquarters = box.user(admin, 'hq', 'modeler', 'hq-modeler')
    hq_reviewer = box.user(admin, 'hq', 'reviewer', 'hq-reviewer')
    actors = {'sub-01': a, 'sub-02': b}
    for tenant, actor in actors.items():
        actor.create('SAME-ID', f'UniqueSecret-{tenant}', payload={'definition': f'UniqueSecret-{tenant}数据含义'})
    docs = {}
    for tenant, actor in actors.items():
        data = f'# 同名文档\n\nOnlyFile-{tenant}原始数据内容。项目到期应核对交付记录。'.encode()
        uploaded = actor.ok('POST', '/materials/upload', data={'org_id': tenant, 'upload_token': 'qa-same-upload-token-0001'},
                            files={'file': ('same.txt', data, 'text/plain')}).json()
        docs[tenant] = (uploaded['id'], data)
    assert docs['sub-01'][0] == docs['sub-02'][0]
    def tenant_isolation():
        for tenant, actor in actors.items():
            foreign = 'sub-02' if tenant == 'sub-01' else 'sub-01'
            assert actor.ok('GET', '/entities/SAME-ID').json()['title'] == f'UniqueSecret-{tenant}'
            assert actor.ok('GET', '/search', params={'q': 'UniqueSecret-' + foreign}).json()['total'] == 0
            assert actor.ok('GET', f'/materials/{docs[tenant][0]}/download').content == docs[tenant][1]
            expect(actor.request('GET', f'/federation/entities/{foreign}/SAME-ID'), 404)
            assert all(x['tenant_id'] == tenant for x in actor.ok('GET', '/federation/search').json()['items'])
    check('same IDs: entity, search, source-file and federation isolation', tenant_isolation)

    def cache_isolation():
        for _ in range(2):
            for tenant, actor in actors.items():
                foreign = 'sub-02' if tenant == 'sub-01' else 'sub-01'
                graph = actor.ok('GET', '/graph', params={'view': 'focus', 'node': 'SAME-ID'}).json()
                assert 'UniqueSecret-' + foreign not in json.dumps(graph, ensure_ascii=False)
                search = actor.ok('POST', '/ai/ask', json={'question': 'UniqueSecret-' + foreign, 'mode': 'retrieval'}).json()
                assert 'UniqueSecret-' + foreign not in json.dumps(search, ensure_ascii=False)
    check('warm graph and retrieval caches remain tenant-scoped', cache_isolation)

    def spoofing():
        expect(a.request('GET', '/entities/SAME-ID', headers={'X-Tenant-Id': 'sub-02'}), 403)
        expect(a.request('GET', '/entities/SAME-ID', headers={'X-Workspace-Id': 'sub-02'}), 409)
        response = a.ok('GET', '/entities/SAME-ID', params={'tenant_id': 'sub-02'})
        assert response.json()['title'] == 'UniqueSecret-sub-01'
        expect(a.request('POST', '/entities', json={'id': 'FORGED-ORG', 'kind': 'data', 'title': 'fake', 'org_id': 'sub-02'}), 403)
        a.create('AUDIT-ACTOR', '真实操作者测试', actor='forged-superadmin', payload={'tenant_id': 'sub-02'})
        history = a.ok('GET', '/activity', params={'entity_id': 'AUDIT-ACTOR'}).json()['items']
        assert history and {x['actor'] for x in history} == {a.username}
        assert a.ok('GET', '/entities/AUDIT-ACTOR').json()['owner_tenant_id'] == 'sub-01'
        expect(a.request('POST', '/auth/workspace', json={'tenant_id': 'sub-02'}), 403)
    check('forged tenant, workspace, org and actor cannot select another owner', spoofing)

    def csrf_role_boundary():
        expect(a.request('POST', '/entities', headers={'X-CSRF-Token': ''}, json={'kind': 'data', 'title': 'bad'}), 403)
        expect(a.request('POST', '/entities', headers={'Origin': 'https://untrusted.example'}, json={'kind': 'data', 'title': 'bad'}), 403)
        expect(a.request('POST', '/unreviewed-new-operation', json={}), 403)
        expect(reviewer.request('POST', '/entities', json={'kind': 'data', 'title': 'bad'}), 403)
        expect(a.request('POST', '/entities/SAME-ID/action', json={'action': 'approve'}), 403)
        expect(a.request('PATCH', '/entities/SAME-ID', json={'status': 'published'}), 403)
        expect(a.request('POST', '/admin/users', json={'username': 'forged-user', 'tenant_id': 'sub-02', 'role': 'admin', 'password': 'Temporary9-random123'}), 403)
        expect(local_admin.request('POST', '/admin/users', json={'username': 'forged-local', 'tenant_id': 'sub-02', 'role': 'admin', 'password': 'Temporary9-random123'}), 403)
    check('CSRF/origin, default deny, modeling/review and local-admin role boundaries', csrf_role_boundary)

    def file_path_boundary():
        mid = docs['sub-01'][0]
        entity = a.ok('GET', '/entities/' + mid).json()
        original = entity['payload']
        try:
            for malicious in ('../../sub-02/data/uploads/other.txt', '/etc/passwd', 'C:\\Windows\\system.ini'):
                bad = {**original, 'stored_name': '', 'source_path': malicious}
                a.ok('PATCH', '/entities/' + mid, json={'payload': bad, 'reason': '隔离边界测试'})
                expect(a.request('GET', '/materials/' + mid + '/download'), (403, 404))
        finally:
            a.ok('PATCH', '/entities/' + mid, json={'payload': original, 'reason': '恢复测试原件引用'})
        assert a.ok('GET', '/materials/' + mid + '/download').content == docs['sub-01'][1]
    check('stored-file traversal, absolute paths and Windows paths are rejected', file_path_boundary)

    for ident in ('DEPT-PUBLIC', 'DEPT-PRIVATE', 'DEPT-DRAFT'):
        department.create(ident, ident)
    department.publish('DEPT-PUBLIC')
    department.publish('DEPT-PRIVATE')
    for ident in ('DEPT-PUBLIC', 'DEPT-DRAFT'):
        department.ok('POST', f'/federation/entities/dept-01/{ident}/visibility', json={'visibility': 'headquarters'})
    def federation_scope():
        directory = admin.ok('GET', '/admin/tenants').json()['items']
        assert len(directory) == 51 and len({x['id'] for x in directory}) == 51
        assert Counter(x['category'] for x in directory) == {'headquarters': 1, 'resident': 1, 'headquarters_department': 12, 'subsidiary': 37}
        found = headquarters.ok('GET', '/federation/search', params={'q': 'UniqueSecret'}).json()['items']
        assert {x['tenant_id'] for x in found} == {'sub-01', 'sub-02'}
        assert headquarters.ok('GET', '/federation/entities/sub-01/SAME-ID').json()['entity']['status'] == 'draft'
        expect(other_dept.request('GET', '/federation/entities/dept-01/DEPT-PRIVATE'), 404)
        expect(other_dept.request('GET', '/federation/entities/dept-01/DEPT-DRAFT'), 404)
        assert other_dept.ok('GET', '/federation/entities/dept-01/DEPT-PUBLIC').json()['entity']['id'] == 'DEPT-PUBLIC'
        expect(a.request('GET', '/federation/entities/dept-01/DEPT-PUBLIC'), 404)
        expect(headquarters.request('POST', '/auth/workspace', json={'tenant_id': 'sub-01'}), 403)
    check('51-organizations directory; HQ private read, department published sharing, subsidiary own-only', federation_scope)

    def crosslinks_and_revocation():
        created = headquarters.ok('POST', '/federation/links', json={'source_tenant_id': 'sub-01', 'source_entity_id': 'SAME-ID',
                                 'target_tenant_id': 'sub-02', 'target_entity_id': 'SAME-ID', 'label': '跨机构字段映射', 'reason': 'QA明确授权边界'}).json()
        expect(headquarters.request('PATCH', '/federation/links/' + created['id'], json={'status': 'confirmed', 'reason': '建模人不可审核'}), 403)
        hq_reviewer.ok('PATCH', '/federation/links/' + created['id'], json={'status': 'confirmed', 'reason': '独立审核完成'})
        assert any(x.get('link_id') == created['id'] for x in headquarters.ok('GET', '/federation/links').json()['items'])
        grants = admin.ok('GET', '/admin/grants').json()['items']
        grant = next(x for x in grants if x['user_id'] == headquarters.user['id'] and x['tenant_id'] == 'sub-02')
        admin.ok('DELETE', '/admin/grants/' + grant['id'])
        expect(headquarters.request('GET', '/federation/entities/sub-02/SAME-ID'), 404)
        assert not any(x.get('link_id') == created['id'] for x in headquarters.ok('GET', '/federation/links').json()['items'])
        detail = headquarters.ok('GET', '/federation/entities/sub-01/SAME-ID').json()
        assert not any(x.get('link_id') == created['id'] for group in detail['relations'].values() for x in group)
    check('cross-link two-endpoint authorization and next-request grant revocation', crosslinks_and_revocation)

    def get_readonly():
        before = box.business_snapshot('sub-01')
        for path in ('/entities', '/search?q=UniqueSecret', '/graph', '/catalog', '/overview', '/materials',
                     f'/materials/{docs["sub-01"][0]}/reading', '/datasets', '/authority/catalog', '/experience/sessions',
                     '/impact/sources', '/work/catalog', '/ai/status'):
            a.ok('GET', path)
        assert box.business_snapshot('sub-01') == before, 'GET changed tenant business tables'
    check('GET routes leave every tenant business table unchanged', get_readonly)

    def jobs_and_revocation():
        for tenant in actors:
            box.sql('INSERT INTO jobs VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    ('SAME-JOB', 'material_extract', docs[tenant][0], 'completed', 100, tenant, '{}', '2026-11-10', '2026-11-10'), box.tenant_db(tenant))
        for tenant, actor in actors.items():
            assert actor.ok('GET', '/jobs/SAME-JOB').json()['message'] == tenant
        mid = docs['sub-01'][0]
        preview = a.ok('POST', f'/materials/{mid}/segment-preview', json={'mode': 'default'}).json()
        a.ok('POST', f'/materials/{mid}/segment-confirm', json={'preview_id': preview['id']})
        job = a.ok('POST', f'/materials/{mid}/extract', json={'mode': 'local', 'retry': False}).json()
        expect(b.request('GET', '/jobs/' + job['id']), 404)
        admin.ok('PATCH', '/admin/users/' + a.user['id'], json={'is_active': False})
        expect(a.request('GET', '/entities'), 401)
        before = box.business_snapshot('sub-01')
        box.sql('UPDATE ontology_task_queue SET available_at=UTC_TIMESTAMP(6) WHERE job_id=%s', (job['id'],), box.control)
        box.python('import asyncio; from backend.jobs import run_one; asyncio.run(run_one())')
        row = box.sql('SELECT status FROM ontology_task_queue WHERE job_id=%s', (job['id'],), box.control)
        assert row[0]['status'] == 'cancelled', row
        assert box.business_snapshot('sub-01') == before, 'Revoked worker modified tenant data'
    check('same-ID job isolation, account disable and queued-worker pre-execution revocation', jobs_and_revocation)

    def downgrade():
        admin.ok('PATCH', '/admin/users/' + b.user['id'], json={'role': 'reviewer'})
        expect(b.request('GET', '/entities'), 401)
        b.login()
        expect(b.request('POST', '/entities', json={'kind': 'data', 'title': 'denied'}), 403)
        admin.ok('PATCH', '/admin/users/' + a.user['id'], json={'is_active': True})
        a.login()
        a.ok('GET', '/entities')
        expect(admin.request('POST', '/admin/grants', json={'user_id': a.user['id'], 'tenant_id': 'sub-02', 'access': 'write', 'reason': '子公司不可获得跨机构操作'}), 403)
        expect(admin.request('POST', '/admin/grants', json={'user_id': headquarters.user['id'], 'tenant_id': 'sub-02', 'access': 'write', 'reason': '普通总部也不可跨机构写'}), 403)
        grant = admin.ok('POST', '/admin/grants', json={'user_id': headquarters.user['id'], 'tenant_id': 'sub-02', 'access': 'read_private', 'reason': '显式临时总部读取'}).json()
        assert headquarters.ok('GET', '/federation/entities/sub-02/SAME-ID').json()['entity']['title'] == 'UniqueSecret-sub-02'
        admin.ok('DELETE', '/admin/grants/' + grant['id'])
        expect(headquarters.request('GET', '/federation/entities/sub-02/SAME-ID'), 404)
        expect(headquarters.request('POST', '/auth/workspace', json={'tenant_id': 'sub-02'}), 403)
    check('role downgrade, re-enable, HQ write denial and read-grant revocation affect next request', downgrade)

    def audit_integrity():
        rows = admin.ok('GET', '/admin/audit', params={'page_size': 100}).json()
        assert rows
        serialized = json.dumps(box.sql('SELECT details FROM sec_audit', database=box.control))
        assert box.admin_password not in serialized and old_cookie not in serialized and admin.csrf not in serialized
        try:
            box.sql("UPDATE sec_audit SET outcome='tampered' LIMIT 1", database=box.control)
        except pymysql.MySQLError:
            pass
        else:
            raise AssertionError('Audit UPDATE was accepted')
    check('audit contains no raw auth secrets and SQL updates are rejected', audit_integrity)

    def login_limit():
        username = 'unknown-' + uuid.uuid4().hex[:12]
        for _ in range(5):
            expect(anon.request('POST', '/auth/login', json={'username': username, 'password': 'Invalid9-credential'}), 401)
        response = expect(anon.request('POST', '/auth/login', json={'username': username, 'password': 'Invalid9-credential'}), 429)
        assert int(response.headers['retry-after']) > 0
    check('password guessing is persistently throttled', login_limit)
    return {'result': 'passed' if all(x['passed'] for x in checks) else 'failed', 'checks': checks,
            'transport': 'real loopback HTTP to uvicorn; real password sessions and CSRF',
            'fixture_notes': 'Business entity/file creation uses HTTP; same-ID job rows and worker availability use SQL only in disposable databases. No identity or authorization mocks.',
            'database_namespace': box.namespace, 'backend_snapshot_sha256': box.source_sha256}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8877)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'work' / 'security-report.json')
    args = parser.parse_args()
    report = {'result': 'failed'}
    try:
        with Sandbox(args.port, args.output) as box:
            report = run_checks(box)
    except Exception as exc:
        report['result'] = 'failed'
        report['fatal_error'] = str(exc)
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'result': report['result'], 'report': str(args.output)}, ensure_ascii=False))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
