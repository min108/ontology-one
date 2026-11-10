"""Real MySQL/HTTP skill-governance regression with disposable identities.

The optional model fixture simulates only Messages/SSE responses. Harness SDK,
account authorization, database state, review and Docker script tests stay real.
Run --smoke early to verify the shortest declaration-only lifecycle.
"""
from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
import zipfile

import httpx
from check_security import Sandbox, Session, expect
from check_sources import Sources, environment, private_json
from check_mcp import application_children


def manifest(*, dependencies=None, scripted=False):
    return {'schema_version': '1.0', 'applicability': '仅用于合成示例的加一规则验证，不读取外部明细。',
            'inputs': {'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value'], 'additionalProperties': False},
            'outputs': {'type': 'object', 'properties': {'result': {'type': 'integer'}}, 'required': ['result'], 'additionalProperties': False},
            'steps': [{'title': '校验输入并计算', 'instruction': '检查整数输入并返回加一后的值，交由用户核对。'}],
            'entrypoint': 'scripts/main.py' if scripted else None,
            'test_cases': [{'name': '合成正例', 'input': {'value': 2}, 'expected_output': {'result': 3}}],
            'dependencies': dependencies or [], 'tool_dependencies': []}


SCRIPT = 'import json,sys\nvalue=json.load(sys.stdin)\njson.dump({"result":value["value"]+1},sys.stdout)\n'


class Runtime:
    """Own the local model fixture and fixed Docker runner; no shared services."""
    def __init__(self, box, model_port):
        self.box, self.model_port = box, model_port
        self.api_key = 'SYNTHETIC_MODEL_' + secrets.token_hex(24)
        self.proposal_marker = 'AI_DRAFT_ONLY_' + uuid.uuid4().hex
        self.host_secret = 'HOST_PRIVATE_' + secrets.token_hex(24)
        self.namespace = 'qa-' + uuid.uuid4().hex[:10]
        self.processes, self.logs = [], []

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.model_port}'

    @property
    def sensitive(self):
        return [self.api_key, self.host_secret]

    def redact(self, value):
        for item in self.sensitive:
            value = value.replace(item, '<redacted-test-value>')
        return value

    def model_state(self, **changes):
        self.state.update(changes)
        private_json(self.state_path, self.state)

    def events(self):
        path = self.state_path.with_suffix('.events.json')
        return json.loads(path.read_text()) if path.exists() else {}

    def containers(self):
        command = ['docker', '--host=unix:///var/run/docker.sock', 'ps', '-aq', '--filter',
                   'label=ontology.skill-runner.namespace=' + self.namespace]
        return subprocess.check_output(command, text=True).split()

    def wait_clean(self, timeout=6):
        deadline = time.monotonic() + timeout
        while self.containers():
            if time.monotonic() > deadline:
                raise AssertionError('A skill-test container remained after completion/cancellation')
            time.sleep(.05)

    def __enter__(self):
        self.image_id = subprocess.check_output(['docker', '--host=unix:///var/run/docker.sock', 'image',
            'inspect', '--format', '{{.Id}}', 'ontology-skill-test:0.5.0'], text=True).strip()
        if not self.image_id.startswith('sha256:') or len(self.image_id) != 71:
            raise RuntimeError('A fixed skill-test image is required')
        self.runner_sha256 = hashlib.sha256((self.box.source / 'ops/skill_runner.py').read_bytes()).hexdigest()
        self.temp = tempfile.TemporaryDirectory(prefix='ontology-skills-fixtures-')
        self.root = Path(self.temp.name)
        self.socket_path = self.root / 'runner.sock'
        self.state_path = self.root / 'model-state.json'
        self.canary = self.root / 'host-private-canary'
        self.canary.write_text(self.host_secret)
        self.canary.chmod(0o600)
        self.state = {'api_key': self.api_key, 'proposal_marker': self.proposal_marker, 'mode': 'normal', 'forbidden': []}
        private_json(self.state_path, self.state)
        try:
            for name, command, extra_env in [
                ('model', [sys.executable, str(self.box.source / 'tests/fixtures/harness_model_server.py'), '--port',
                           str(self.model_port), '--state-file', str(self.state_path)], {}),
                ('runner', [sys.executable, str(self.box.source / 'ops/skill_runner.py'), '--socket', str(self.socket_path)],
                 {'ONTOLOGY_SKILL_RUNNER_NAMESPACE': self.namespace, 'ONTOLOGY_SKILL_TEST_IMAGE': self.image_id,
                  'QA_HOST_SECRET': self.host_secret}),
            ]:
                log = (self.root / (name + '.log')).open('w')
                self.logs.append(log)
                env = {key: value for key, value in os.environ.items() if not key.startswith(('MYSQL_', 'ONTOLOGY_', 'GITHUB_'))}
                env.update(extra_env)
                self.processes.append(subprocess.Popen(command, cwd=self.box.source, env=env, stdout=log,
                                                       stderr=subprocess.STDOUT, start_new_session=True))
            deadline = time.monotonic() + 20
            with httpx.Client(trust_env=False, timeout=1) as client:
                while time.monotonic() < deadline:
                    if any(process.poll() is not None for process in self.processes):
                        raise RuntimeError('Isolated model/runner fixture exited before readiness')
                    try:
                        if self.socket_path.exists() and client.get(self.base_url + '/health').status_code == 200:
                            return self
                    except httpx.RequestError:
                        pass
                    time.sleep(.1)
            raise RuntimeError('Isolated model/runner fixture startup timed out')
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(self, *_):
        for process in reversed(self.processes):
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
        for log in self.logs:
            log.close()
        try:
            self.wait_clean()
        finally:
            if getattr(self, 'temp', None):
                self.temp.cleanup()


def version(detail):
    return detail['entity']['version']


def ident(detail):
    return detail['entity']['id']


def skill(actor, name='qa-skill'):
    return actor.ok('POST', '/skills', json={'title': 'QA技能 · ' + name,
                                           'summary': '独立验收使用的合成技能', 'package_name': name}).json()


def save(actor, detail, *, files=None, profile=None, reason='QA明确保存草稿'):
    return actor.ok('PUT', '/skills/' + ident(detail), json={
        'expected_version': version(detail), 'title': detail['entity']['title'],
        'summary': detail['entity']['summary'], 'files': files if files is not None else detail['files'],
        'manifest': profile if profile is not None else detail['manifest'], 'reason': reason}).json()


def action(actor, detail, name):
    return actor.ok('POST', '/skills/' + ident(detail) + '/action', json={
        'expected_version': version(detail), 'action': name, 'reason': 'QA明确执行' + name}).json()


def get(actor, detail):
    return actor.ok('GET', '/skills/' + ident(detail)).json()


def validate_and_test(actor, detail, mode='contract'):
    prefix = '/skills/' + ident(detail)
    validation = actor.ok('POST', prefix + '/validate', json={'expected_version': version(detail)}).json()
    assert validation['validation']['valid'], 'Skill validation did not pass'
    result = actor.ok('POST', prefix + '/test', json={'expected_version': version(detail)}, timeout=50).json()
    assert result['outcome'] == 'passed', 'Skill test did not pass: ' + json.dumps({
        'outcome': result.get('outcome'), 'cases': [{key: case.get(key) for key in ('index', 'status', 'code', 'duration_ms')}
        for case in result.get('report', {}).get('cases', [])]}, ensure_ascii=False)
    assert result['mode'] == mode, 'Skill used an unexpected test mode'
    assert result['digest'] == detail['digest'], 'Test proof is not bound to the saved package'
    return result


def run_checks(box, *, smoke=False, runtime=None, external=None, ui_access=None, section='all'):
    checks = []

    def check(name, operation, requires=(), group='governance'):
        if not smoke and section != 'all' and group not in (section, 'always'):
            return
        began = time.monotonic()
        try:
            if any(key not in records for key in requires):
                raise AssertionError('Required fixture step failed; dependent acceptance was not executed')
            operation()
            result = {'name': name, 'passed': True}
        except Exception as exc:
            message = str(exc)
            if runtime:
                message = runtime.redact(message)
            if external:
                message = external.redact(message)
            frames = traceback.extract_tb(exc.__traceback__)
            result = {'name': name, 'passed': False, 'error': message[:1200],
                      'location': f'{Path(frames[-1].filename).name}:{frames[-1].lineno}' if frames else None}
        result['seconds'] = round(time.monotonic() - began, 3)
        checks.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    root = Session(box, box.admin_name, box.admin_password)
    root.login()
    people = {tenant: {role: box.user(root, tenant, role) for role in ('admin', 'modeler', 'reviewer')}
              for tenant in ('sub-01', 'sub-02')}
    admin, builder, reviewer = (people['sub-01'][role] for role in ('admin', 'modeler', 'reviewer'))
    foreign = people['sub-02']['modeler']
    records = {}

    def lifecycle():
        detail = skill(builder)
        records['primary'] = detail
        assert detail['entity']['status'] == 'draft'
        expect(builder.request('POST', '/skills/' + ident(detail) + '/action', json={
            'expected_version': version(detail), 'action': 'submit', 'reason': '缺少校验测试不得提交'}), (409, 422))
        detail = save(builder, detail, profile=manifest())
        validate_and_test(builder, detail)
        detail = action(builder, detail, 'submit')
        expect(builder.request('POST', '/skills/' + ident(detail) + '/action', json={
            'expected_version': version(detail), 'action': 'publish', 'reason': '建模师不可自审'}), 403)
        detail = action(reviewer, detail, 'publish')
        assert detail['entity']['status'] == 'published'
        loaded = builder.ok('GET', '/skills/' + ident(detail) + '/load').json()
        assert loaded['digest'] == detail['digest']
        assert loaded['files']['SKILL.md'] == detail['files']['SKILL.md']
        expect(foreign.request('GET', '/skills/' + ident(detail)), 404)
        records['primary'] = detail

    check('real account sessions: edit, validate, contract test, separate reviewer publish and tenant-isolated load', lifecycle)
    if not smoke:
        if runtime is None or external is None:
            raise RuntimeError('Full execution/model/source fixtures are required')

        def source_and_script():
            assets = []
            for kind in ('data', 'rule', 'knowledge'):
                aid = 'SKILL-DEP-' + kind.upper()
                builder.create(aid, 'QA已审' + kind, kind=kind, payload={'definition': '合成验收口径'})
                for actor, name in ((builder, 'submit'), (reviewer, 'approve'), (reviewer, 'publish')):
                    actor.ok('POST', '/entities/' + aid + '/action', json={'action': name, 'reason': 'QA独立审核发布'})
                entity = builder.ok('GET', '/entities/' + aid).json()
                assets.append({'entity_id': aid, 'kind': kind, 'version': entity['version']})
            source = admin.ok('POST', '/sources', json={'title': 'QA技能只读依赖', 'kind': 'mysql',
                'config': {'host': box.settings['host'], 'port': box.settings['port'], 'database': external.schemas['sub-01'],
                           'allowed_objects': ['contracts']}, 'credential_ref': 'mysql-sub-01'}).json()
            for actor in (builder, reviewer):
                admin.ok('PUT', '/sources/' + source['id'] + '/grants', json={
                    'user_id': actor.user['id'], 'discover': True, 'query': True})
            resource = builder.ok('POST', '/sources/' + source['id'] + '/discover').json()['resources'][0]
            mapping = builder.ok('POST', '/sources/' + source['id'] + '/mappings', json={
                'resource_id': resource['id'], 'title': 'QA对象标识映射', 'business_meaning': '合成业务对象标识',
                'grain': '一行代表一个对象', 'object_key': ['id'], 'relation_keys': [],
                'fields': [{'column': 'id', 'field': 'business_id', 'asset_id': 'SKILL-DEP-DATA',
                            'meaning': '对象编号', 'unit': '无量纲'}]}).json()
            for actor, name in ((builder, 'submit'), (reviewer, 'publish')):
                mapping = actor.ok('POST', '/sources/' + source['id'] + '/mappings/' + mapping['id'] + '/action',
                    json={'action': name, 'expected_version': mapping['version'], 'reason': 'QA人工审核业务对象映射'}).json()
            legacy = builder.create('A.26.01.901', 'QA历史算法说明', kind='algorithm', payload={'algorithm_type': '数据校验'})
            assert legacy['kind'] == 'skill', 'Legacy kind alias was not normalized'
            expect(builder.request('GET', '/skills/' + legacy['id'] + '/load'), 404)
            detail = builder.ok('POST', '/skills', json={'title': 'QA正式技能包', 'package_name': 'qa-scripted-skill',
                                                        'legacy_entity_id': legacy['id']}).json()
            assert ident(detail) == legacy['id']
            profile = manifest(dependencies=assets, scripted=True)
            profile['tool_dependencies'] = [{'name': 'ontology_query', 'mapping_ids': [mapping['id']], 'purpose': '发布后由授权调用方查询元数据映射'}]
            detail = save(builder, detail, files={**detail['files'], 'scripts/main.py': SCRIPT}, profile=profile)
            validate_and_test(builder, detail, 'isolated_python')
            detail = action(builder, detail, 'submit')
            detail = action(reviewer, detail, 'publish')
            loaded = builder.ok('GET', '/skills/' + ident(detail) + '/load').json()
            assert loaded['tool_policy']['credentials_included'] is False
            assert loaded['tool_policy']['execution_authorized'] is False
            entity = builder.ok('GET', '/entities/' + ident(detail)).json()
            edges = entity['relations']['outgoing']
            assert {edge['type'] for edge in edges if edge['status'] == 'confirmed'} >= {'uses_data', 'based_on', 'references'}, 'Published dependency edges are missing'
            records.update(scripted=detail, source=source, mapping=mapping, dependencies=assets)
            runtime.wait_clean()

        check('legacy adoption, real MySQL mapping, isolated Python test, dual review and confirmed ontology dependency edges', source_and_script)

        def permissions_and_bypass():
            detail = records['scripted'];sid = ident(detail)
            before = get(builder, detail)
            for method, path, body in [
                ('POST', '/entities', {'kind': 'skill', 'title': '伪造受管技能', 'payload': {'managed_skill': True}}),
                ('PATCH', '/entities/' + sid, {'title': '旁路修改', 'payload': {}}),
                ('POST', '/entities/' + sid + '/action', {'action': 'revise'}),
                ('POST', '/entities/' + sid + '/restore', {'version': 1, 'reason': '不得绕过技能治理'}),
                ('POST', '/placement/' + sid + '/confirm', {'job_id': 'nonexistent', 'expected_version': version(detail),
                    'review_note': '不得经通用关联改技能依赖', 'links': [{'suggestion_id': 'x', 'target_id': 'SKILL-DEP-DATA',
                    'target_version': records['dependencies'][0]['version'], 'direction': 'outgoing', 'relation_type': 'uses_data',
                    'reason': '不得绕过专用技能发布流程', 'reviewed': True}]}),
            ]:
                actor = admin if path.endswith('/restore') else reviewer if '/placement/' in path else builder
                expect(actor.request(method, path, json=body), (403, 409))
            edge = next(edge for edge in builder.ok('GET', '/entities/' + sid).json()['relations']['outgoing']
                        if json.loads(edge['payload'])['skill_managed'])
            blocked_relation = reviewer.request('PATCH', '/relations/' + edge['id'], json={
                'status': 'rejected', 'reason': '试图绕过技能依赖治理'})
            expect(blocked_relation, 400)
            assert '技能依赖' in str(blocked_relation.json().get('detail', ''))
            ordinary = builder.create('SKILL-GUARD-DATA', 'QA关联旁路校验', kind='data')
            candidates = builder.ok('POST', '/placement/' + ordinary['id'] + '/suggest', json={
                'expected_version': ordinary['version'], 'mode': 'local', 'query': sid}).json()
            suggestion = next(item for item in candidates['suggestions'] if item['target_id'] == sid)
            expect(reviewer.request('POST', '/placement/' + ordinary['id'] + '/confirm', json={
                'job_id': candidates['job_id'], 'expected_version': ordinary['version'],
                'review_note': '不可通过反向关联修改受管技能的依赖', 'links': [{
                    'suggestion_id': suggestion['id'], 'target_id': sid, 'target_version': version(detail),
                    'direction': 'incoming', 'relation_type': 'uses_data', 'reason': '测试反向修改技能依赖的旁路', 'reviewed': True}]}), 409)
            from openpyxl import Workbook
            headers = ['技能编码', '业务领域', '子类别', '技能类型', '技能逻辑名称', '适用场景', '数据需求', '分析方法', '判定规则']
            workbook = Workbook();sheet = workbook.active;sheet.title = '技能资产'
            sheet.append(headers);sheet.append([sid, '科技数据安全', '质量', '数据校验', '旁路覆盖', '合成场景', '整数', '加一', '严格校验'])
            content = io.BytesIO();workbook.save(content);workbook.close()
            expect(builder.request('POST', '/asset-import/skill', files={'file': ('guard.xlsx', content.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')}), (403, 409))
            assert get(builder, detail)['digest'] == before['digest'] and version(get(builder, detail)) == version(before)
            expect(reviewer.request('PUT', '/skills/' + sid, json={'expected_version': version(detail), 'title': '审核官不可编辑',
                'summary': '', 'files': detail['files'], 'manifest': detail['manifest'], 'reason': '越权测试'}), 403)
            expect(foreign.request('GET', '/skills/' + sid + '/export'), 404)
            expect(foreign.request('GET', '/skills/' + sid + '/load'), 404)
            app = admin.ok('POST', '/integrations/applications', json={'name': 'QA只读调用方', 'description': '不能进入技能管理',
                'delegate_user_id': builder.user['id'], 'tool_scopes': ['ontology_discover'], 'mapping_ids': [], 'expires_days': 1}).json()
            bearer = app['credential']['token']
            with httpx.Client(trust_env=False, timeout=5) as client:
                assert client.get(box.url + '/api/skills', headers={'Authorization': 'Bearer ' + bearer}).status_code == 401
            changed = action(builder, detail, 'revise')
            stale = builder.request('PUT', '/skills/' + sid, json={'expected_version': version(detail), 'title': detail['entity']['title'],
                'summary': '', 'files': detail['files'], 'manifest': detail['manifest'], 'reason': '旧版本覆盖应拒绝'})
            expect(stale, 409)
            changed = save(builder, changed, files={**changed['files'], 'references/revision.md': '# 版本证明需重新验证\n'})
            expect(builder.request('POST', '/skills/' + sid + '/action', json={'expected_version': version(changed), 'action': 'submit', 'reason': '旧测试不可复用'}), 409)
            validate_and_test(builder, changed, 'isolated_python')
            changed = action(builder, changed, 'submit');changed = action(reviewer, changed, 'publish')
            records['scripted'] = changed
            self_review = skill(admin, 'qa-admin-separation')
            self_review = save(admin, self_review, profile=manifest())
            validate_and_test(admin, self_review)
            self_review = action(admin, self_review, 'submit')
            expect(admin.request('POST', '/skills/' + ident(self_review) + '/action', json={
                'expected_version': version(self_review), 'action': 'publish', 'reason': '管理员不得自审'}), 403)
            action(reviewer, self_review, 'publish')

        check('managed skills resist generic entity/restore/Excel/placement/relation bypass; roles, application bearer and stale proofs are enforced', permissions_and_bypass, ('scripted',))

        def portable_packages():
            detail = records['scripted'];sid = ident(detail)
            response = builder.ok('GET', '/skills/' + sid + '/export')
            assert 'no-store' in response.headers['cache-control']
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                portable = json.loads(next(archive.read(name) for name in archive.namelist() if name.endswith('/assets/ontology-one.json')))
                assert not any(key in portable for key in ('dependencies', 'tool_dependencies', 'reviewed_by', 'status', 'last_test'))
                assert not any(value.encode() in response.content for value in runtime.sensitive)
            imported = foreign.ok('POST', '/skills/import', files={'file': ('portable.zip', response.content, 'application/zip')}).json()
            assert imported['entity']['status'] == 'draft' and imported['last_test'] is None
            assert imported['manifest']['dependencies'] == [] and imported['manifest']['tool_dependencies'] == []
            expect(foreign.request('GET', '/skills/' + ident(imported) + '/load'), 409)
            assert foreign.ok('GET', '/skills/' + ident(imported) + '/versions').json()['items']
            cases = [('../escape.py', b'bad', 0), ('qa-package/scripts/link.py', b'/etc/passwd', stat.S_IFLNK | 0o777),
                     ('qa-package/.env', b'PRIVATE_KEY=must-not-import', 0),
                     ('qa-package/references/key.txt', ('onto_app_' + 'a' * 43).encode(), 0)]
            for name, content, mode in cases:
                raw = io.BytesIO()
                with zipfile.ZipFile(raw, 'w') as archive:
                    archive.writestr('qa-package/SKILL.md', '---\nname: qa-package\ndescription: 合成导入安全验证\n---\n# QA\n')
                    entry = zipfile.ZipInfo(name)
                    if mode:entry.create_system=3;entry.external_attr=mode << 16
                    archive.writestr(entry, content)
                expect(builder.request('POST', '/skills/import', content=raw.getvalue(), headers={'Content-Type': 'application/zip'}), (413, 422))
            records['imported'] = imported

        check('portable export/import never carries tenant grants, publication or proof; traversal/symlink/env/credential packages are rejected', portable_packages, ('scripted',))

        def live_dependencies():
            detail = records['scripted'];source = records['source']
            admin.ok('PUT', '/sources/' + source['id'] + '/grants', json={'user_id': builder.user['id'], 'discover': True, 'query': False})
            expect(builder.request('GET', '/skills/' + ident(detail) + '/load'), 403)
            admin.ok('PUT', '/sources/' + source['id'] + '/grants', json={'user_id': builder.user['id'], 'discover': True, 'query': True})
            builder.ok('GET', '/skills/' + ident(detail) + '/load')
            dependency = next(item for item in records['dependencies'] if item['kind'] == 'knowledge')
            aid = dependency['entity_id']
            builder.ok('PATCH', '/entities/' + aid, json={'summary': '经过修订的新口径'})
            expect(builder.request('GET', '/skills/' + ident(detail) + '/load'), 409)
            for actor, name in ((builder, 'submit'), (reviewer, 'approve'), (reviewer, 'publish')):
                actor.ok('POST', '/entities/' + aid + '/action', json={'action': name, 'reason': 'QA重新审核依赖'})
            expect(builder.request('GET', '/skills/' + ident(detail) + '/load'), 409)
            dependency['version'] = builder.ok('GET', '/entities/' + aid).json()['version']
            detail = action(builder, detail, 'revise')
            profile = copy.deepcopy(detail['manifest']);profile['dependencies'] = records['dependencies']
            detail = save(builder, detail, profile=profile)
            validate_and_test(builder, detail, 'isolated_python')
            detail = action(builder, detail, 'submit');detail = action(reviewer, detail, 'publish')
            records['scripted'] = detail
            builder.ok('GET', '/skills/' + ident(detail) + '/load')

        check('source ACL revocation and published dependency revision invalidate loads until explicit repair, retest and independent review', live_dependencies, ('scripted',))

        def isolation_and_timeout():
            detail = skill(builder, 'qa-isolation-check')
            script = '''import json,os,socket,sys
json.load(sys.stdin)
blocked=False
try:
 socket.create_connection(("127.0.0.1",MODEL_PORT),timeout=.3).close()
except OSError: blocked=True
readonly=False
try:
 open("/rootfs-must-not-write","w").close()
except OSError: readonly=True
json.dump({"env": "QA_HOST_SECRET" not in os.environ and not any(k.startswith("MYSQL_") for k in os.environ),"file": not os.path.exists(HOST_CANARY),"network":blocked,"uid":os.geteuid()!=0,"readonly":readonly},sys.stdout)
'''.replace('MODEL_PORT', str(runtime.model_port)).replace('HOST_CANARY', repr(str(runtime.canary)))
            profile = manifest(scripted=True);profile['outputs'] = {'type': 'object'}
            profile['test_cases'][0]['expected_output'] = {'env': True, 'file': True, 'network': True, 'uid': True, 'readonly': True}
            detail = save(builder, detail, files={**detail['files'], 'scripts/main.py': script}, profile=profile)
            validate_and_test(builder, detail, 'isolated_python')
            runtime.wait_clean()
            bounded = '''import json,os,signal,sys,time
json.load(sys.stdin)
memory=False
try:
 value=bytearray(128*1024*1024)
except MemoryError: memory=True
processes=False
children=[]
try:
 for index in range(8):
  try: child=os.fork()
  except BlockingIOError:
   processes=True
   break
  if child==0:
   time.sleep(2)
   os._exit(0)
  children.append(child)
finally:
 for child in children:
  try: os.kill(child,signal.SIGTERM)
  except ProcessLookupError: pass
  os.waitpid(child,0)
json.dump({"memory":memory,"processes":processes},sys.stdout)
'''
            profile['test_cases'][0]['expected_output'] = {'memory': True, 'processes': True}
            detail = save(builder, detail, files={**detail['files'], 'scripts/main.py': bounded}, profile=profile)
            validate_and_test(builder, detail, 'isolated_python')
            runtime.wait_clean()
            detail = save(builder, detail, files={**detail['files'], 'scripts/main.py': 'while True:\n pass\n'})
            started = time.monotonic()
            outcome = builder.ok('POST', '/skills/' + ident(detail) + '/test', json={'expected_version': version(detail)}, timeout=45).json()
            assert outcome['outcome'] == 'failed' and time.monotonic() - started < 25
            runtime.wait_clean()
            records['isolation'] = detail

        check('real nonroot Docker tests deny host/env/network/rootfs access and enforce memory/process/time bounds with cleanup', isolation_and_timeout, group='execution')

        def execution_races():
            detail = records['isolation'];sid = ident(detail)
            slow = 'import time,json,sys\njson.load(sys.stdin)\ntime.sleep(3)\njson.dump({},sys.stdout)\n'
            profile = manifest(scripted=True);profile['outputs'] = {'type': 'object'};profile['test_cases'][0]['expected_output'] = {}
            detail = save(builder, detail, files={**detail['files'], 'scripts/main.py': slow}, profile=profile)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(builder.request, 'POST', '/skills/' + sid + '/test', json={'expected_version': version(detail)}, timeout=45)
                deadline = time.monotonic() + 20
                while not runtime.containers():
                    assert time.monotonic() < deadline, 'Test container did not start'
                    time.sleep(.05)
                updated = save(admin, detail, files={**detail['files'], 'references/changed.md': '# 执行期间修改\n'})
                expect(future.result(), 409)
            assert get(builder, updated)['last_test'] is None
            runtime.wait_clean()
            async def disconnect():
                async with httpx.AsyncClient(base_url=box.url, cookies=builder.client.cookies, trust_env=False, timeout=45) as client:
                    task = asyncio.create_task(client.post('/api/skills/' + sid + '/test', headers={'X-CSRF-Token': builder.csrf}, json={'expected_version': version(updated)}))
                    deadline = time.monotonic() + 20
                    while not runtime.containers():
                        assert time.monotonic() < deadline, 'Test container did not start before disconnect'
                        await asyncio.sleep(.05)
                    task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass
            asyncio.run(disconnect());runtime.wait_clean()
            assert get(builder, updated)['last_test'] is None
            records['isolation'] = updated

        check('test proof is discarded on concurrent edit or actual HTTP disconnect and each container is reaped', execution_races, ('isolation',), group='execution')

        def harness_proposal():
            detail = skill(builder, 'qa-harness-authoring')
            detail = save(builder, detail, profile=manifest())
            status = admin.ok('GET', '/skills/harness/status').json()
            assert not status['configured']
            expect(builder.request('POST', '/skills/' + ident(detail) + '/assist', json={'expected_version': version(detail), 'instruction': '根据合成目标补充参考说明'}), (409, 503))
            configuration = {'base_url': runtime.base_url, 'model': 'deepseek-v4-flash', 'api_key': runtime.api_key}
            expect(builder.request('POST', '/skills/harness/config', json=configuration), 403)
            admin.ok('POST', '/skills/harness/config', json=configuration)
            status = admin.ok('GET', '/skills/harness/status').json()
            assert status['configured'] and runtime.api_key not in json.dumps(status)
            tested = admin.ok('POST', '/skills/harness/test', timeout=110).json()
            assert tested['ok'], 'Harness connectivity test did not execute successfully'
            proposal = builder.ok('POST', '/skills/' + ident(detail) + '/assist', json={
                'expected_version': version(detail), 'instruction': '请读取现有技能并补充可人工审核的参考说明；不要变更授权。'}, timeout=110).json()
            assert proposal['retention'] == 'response_only'
            assert proposal['base_version'] == version(detail) and proposal['base_digest'] == detail['digest']
            assert runtime.proposal_marker in proposal['files']['references/ai-review-note.md']
            current = get(builder, detail)
            assert current['digest'] == detail['digest'] and 'references/ai-review-note.md' not in current['files']
            saved_packages = json.dumps(box.sql('SELECT files,manifest FROM skill_documents', database=box.tenant_db('sub-01')))
            assert runtime.proposal_marker not in saved_packages, 'An unapplied model proposal was persisted'
            applied = save(builder, detail, files=proposal['files'], profile=proposal['manifest'], reason='人工核对后采纳Harness提案')
            assert applied['entity']['status'] == 'draft' and applied['last_test'] is None
            assert runtime.proposal_marker in applied['files']['references/ai-review-note.md']
            events = runtime.events()
            assert events['read_requests'] and events['proposal_requests'] and events['completed']
            assert events['unexpected_tools'] == 0 and events['foreign_value_seen'] == 0
            assert not people['sub-02']['admin'].ok('GET', '/skills/harness/status').json()['configured']
            records['harness'] = applied

        check('real official Harness SDK reads and proposes with a deterministic model; no auto-save, privilege change or cross-tenant configuration', harness_proposal, group='harness')

        def harness_cancel_and_revoke():
            detail = records['harness'];sid = ident(detail)
            before = runtime.events().get('requests', 0)
            prior_tmpfs = {str(path) for path in Path('/dev/shm').glob('ontology-skill-*')}
            observed_pids = set()
            observed_tmpfs = set()
            runtime.model_state(mode='hold')
            async def disconnect():
                async with httpx.AsyncClient(base_url=box.url, cookies=builder.client.cookies, trust_env=False, timeout=110) as client:
                    task = asyncio.create_task(client.post('/api/skills/' + sid + '/assist', headers={'X-CSRF-Token': builder.csrf}, json={'expected_version': version(detail), 'instruction': '等待测试取消'}))
                    deadline = time.monotonic() + 15
                    while runtime.events().get('requests', 0) <= before:
                        assert time.monotonic() < deadline, 'Harness did not call model before cancellation'
                        await asyncio.sleep(.05)
                    observed_pids.update(application_children(box.process.pid))
                    observed_tmpfs.update({str(path) for path in Path('/dev/shm').glob('ontology-skill-*')} - prior_tmpfs)
                    assert observed_pids, 'No real Harness subprocess was observed'
                    task.cancel()
                    try:await task
                    except asyncio.CancelledError:pass
            try:
                asyncio.run(disconnect())
                deadline = time.monotonic() + 8
                while any(Path('/proc', str(pid)).exists() for pid in observed_pids):
                    assert time.monotonic() < deadline, 'Harness worker remained after actual disconnect'
                    time.sleep(.05)
            finally:
                runtime.model_state(mode='normal')
            assert get(builder, detail)['digest'] == detail['digest']
            assert not any(Path(path).exists() for path in observed_tmpfs), 'Cancelled Harness session workspace remained'
            before = runtime.events().get('requests', 0)
            runtime.model_state(mode='hold')
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(builder.request, 'POST', '/skills/' + sid + '/assist', json={
                        'expected_version': version(detail), 'instruction': '在生成期间撤销账号权限，结果不得返回。'}, timeout=110)
                    deadline = time.monotonic() + 15
                    while runtime.events().get('requests', 0) <= before:
                        assert time.monotonic() < deadline, 'Harness request did not reach model'
                        time.sleep(.05)
                    admin.ok('PATCH', '/admin/users/' + builder.user['id'], json={'is_active': False})
                    runtime.model_state(mode='normal')
                    response = future.result()
                    expect(response, 401)
                    assert 'files' not in response.json()
            finally:
                runtime.model_state(mode='normal')
            expect(builder.request('GET', '/skills/' + sid), 401)
            admin.ok('PATCH', '/admin/users/' + builder.user['id'], json={'is_active': True})
            builder.login()
            records['harness'] = get(builder, detail)

        check('HTTP cancellation kills observed Harness PIDs and removes tmpfs; in-flight account deactivation discards proposals', harness_cancel_and_revoke, ('harness',), group='harness')

        def retention():
            protected = [*runtime.sensitive, *external.sensitive]
            for database in (box.control, box.tenant_db('sub-01'), box.tenant_db('sub-02')):
                for table in box.sql('SHOW TABLES', database=database):
                    name = next(iter(table.values()))
                    content = json.dumps(box.sql(f'SELECT * FROM `{name}`', database=database), ensure_ascii=False, default=str)
                    assert not any(secret in content for secret in protected), 'Model/source credentials or rows persisted in platform tables'
            for path in [box.log_path, runtime.root / 'model.log', runtime.root / 'runner.log']:
                raw = path.read_bytes()
                assert not any(secret.encode() in raw for secret in protected), 'Sensitive values persisted in a runtime log'
            credential_files = [path for path in box.storage.rglob('*') if path.is_file() and runtime.api_key.encode() in path.read_bytes()]
            assert len(credential_files) == (1 if 'harness' in records else 0), 'Unexpected model credential retention'
            if credential_files:
                assert stat.S_IMODE(credential_files[0].stat().st_mode) == 0o600
            backup = admin.ok('GET', '/system/backup')
            with zipfile.ZipFile(io.BytesIO(backup.content)) as archive:
                for name in archive.namelist():
                    raw = archive.read(name)
                    assert not any(secret.encode() in raw for secret in protected), 'A credential or source row entered the workspace backup'
            assert runtime.events().get('foreign_value_seen', 0) == 0 and runtime.events().get('unexpected_tools', 0) == 0
            assert not application_children(box.process.pid)
            runtime.wait_clean()

        check('platform tables and runtime logs retain no model/source secrets or external business rows', retention, group='always')

        if ui_access and all(item['passed'] for item in checks):
            private_json(ui_access, {'url': box.url, 'tenant_id': 'sub-01', 'backend_snapshot_sha256': box.source_sha256,
                'accounts': {name: {'username': actor.username, 'password': actor.password} for name, actor in people['sub-01'].items()},
                'skill_id': ident(records['harness']), 'published_skill_id': ident(records['scripted']),
                'mapping_id': records['mapping']['id'], 'dependencies': records['dependencies']})
    return {'result': 'passed' if all(item['passed'] for item in checks) else 'failed', 'checks': checks,
            'backend_snapshot_sha256': box.source_sha256, 'platform_namespace': box.namespace,
            'runner_image_id': runtime.image_id if runtime else None,
            'runner_service_sha256': runtime.runner_sha256 if runtime else None,
            'frontend_bundles': sorted(path.name for path in (box.root / 'frontend/dist').rglob('*')
                                       if path.is_file() and path.suffix in ('.js', '.css')),
            'scope': 'smoke' if smoke else ('full' if section == 'all' else section),
            'model_validation': 'official Harness SDK with deterministic model fixture' if 'harness' in records else 'not executed',
            'cleanup': 'Disposable accounts, tenant databases, API process and private storage are removed in finally'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8908)
    parser.add_argument('--model-port', type=int, default=8909)
    parser.add_argument('--source-port', type=int, default=8910)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--section', choices=('all', 'governance', 'execution', 'harness'), default='all',
                        help='Run one focused integration group during development; final acceptance uses all')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'work/skill-report.json')
    parser.add_argument('--ui-access', type=Path)
    parser.add_argument('--ui-release', type=Path)
    args = parser.parse_args()
    if bool(args.ui_access) != bool(args.ui_release):
        parser.error('Browser access and release paths must be supplied together')
    if args.ui_access and (args.smoke or args.section != 'all'):
        parser.error('Browser handoff requires complete acceptance')
    if args.ui_access and (args.ui_access.exists() or args.ui_release.exists()):
        parser.error('Use fresh browser handoff paths')
    box = Sandbox(port=args.port, output=args.output,
                  frontend_dist=Path(__file__).resolve().parents[1] / 'frontend/dist' if args.ui_access else None)
    report = {'result': 'failed'}
    runtime, external = None, None
    try:
        if args.smoke:
            with box:
                report = run_checks(box, smoke=True)
        else:
            with Runtime(box, args.model_port) as runtime, Sources(box, args.source_port) as external:
                runtime.model_state(forbidden=external.sensitive)
                with environment(ONTOLOGY_SOURCE_CREDENTIALS_FILE=str(external.registry),
                                 ONTOLOGY_SKILL_RUNNER_SOCKET=str(runtime.socket_path), ONTOLOGY_HARNESS_ALLOW_LOOPBACK_TEST='1'):
                    with box:
                        report = run_checks(box, runtime=runtime, external=external, ui_access=args.ui_access, section=args.section)
                        if args.ui_access and report['result'] == 'passed':
                            print(json.dumps({'stage': 'ui_handoff_ready', 'access_file': str(args.ui_access),
                                              'backend_snapshot_sha256': box.source_sha256}), flush=True)
                            deadline = time.monotonic() + 900
                            while not args.ui_release.exists() and time.monotonic() < deadline:
                                time.sleep(.2)
                            report['ui_handoff'] = 'released' if args.ui_release.exists() else 'timeout; no UI acceptance claimed'
    except Exception as exc:
        report['result'] = 'failed'
        message = str(exc)
        if runtime:message = runtime.redact(message)
        if external:message = external.redact(message)
        report['fatal_error'] = message[:1200]
    finally:
        if args.ui_access:
            args.ui_access.unlink(missing_ok=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'result': report['result'], 'report': str(args.output)}, ensure_ascii=False))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
