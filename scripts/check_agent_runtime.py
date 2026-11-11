"""Real account/source/skill authorization with the actual Harness/tool workers.

Use a dedicated disposable MySQL server (MYSQL_*); only the model replies are
simulated. Every schema, account, source fixture, process and private file is
owned by this run and removed. Reports contain fixed case names and outcomes,
never cookies, model/source credentials, model messages or source business rows.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
import json
import os
import re
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from check_security import Sandbox, Session, expect
from check_sources import Sources, environment, private_json
from check_mcp import prepare, application_children
from check_skills import skill, save, manifest, validate_and_test, action, ident as skill_id


class Model:
    def __init__(self, box, port):
        self.box, self.port = box, port
        self.key = 'SYNTHETIC_AGENT_' + secrets.token_hex(24)
        self.process = self.log = None

    @property
    def url(self):
        return 'http://127.0.0.1:' + str(self.port)

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ontology-agent-model-')
        self.root = Path(self.temp.name)
        self.state_path = self.root / 'model-state.json'
        self.state = {'api_key': self.key, 'plan': [], 'forbidden': []}
        self.update()
        self.log_path = self.root / 'model.log'
        self.log = self.log_path.open('w')
        self.process = subprocess.Popen([sys.executable, str(self.box.source / 'tests/fixtures/agent_model_server.py'),
                         '--port', str(self.port), '--state-file', str(self.state_path)],
                         stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 15
        with httpx.Client(trust_env=False, timeout=1) as probe:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('Synthetic model fixture did not start')
                try:
                    if probe.get(self.url+'/health').status_code == 200:
                        return self
                except httpx.RequestError:
                    pass
                time.sleep(.05)
        raise RuntimeError('Synthetic model fixture readiness timeout')

    def update(self, **values):
        self.state.update(values)
        private_json(self.state_path, self.state)

    def events(self):
        path = self.state_path.with_suffix('.events.json')
        return json.loads(path.read_text()) if path.exists() else {}

    def __exit__(self, *_):
        self.update(mode='normal')
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=5)
        if self.log:
            self.log.close()
        self.temp.cleanup()


class Broker:
    def __init__(self, box, port):
        self.box, self.port = box, port

    def __enter__(self):
        self.log_path = self.box.root / 'agent-service.log'
        self.log = self.log_path.open('w')
        self.process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.agent_service:app',
                  '--host', '127.0.0.1', '--port', str(self.port), '--workers', '1', '--no-access-log'],
                  cwd=self.box.root, env=self.box.env, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic()+20
        with httpx.Client(trust_env=False, timeout=1) as probe:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('Agent broker did not start')
                try:
                    value = probe.get('http://127.0.0.1:'+str(self.port)+'/internal/health')
                    if value.status_code == 200 and value.json().get('ready') is True:
                        return self
                except httpx.RequestError:
                    pass
                time.sleep(.05)
        raise RuntimeError('Agent broker readiness timeout')

    def __exit__(self, *_):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL);self.process.wait(timeout=5)
        self.log.close()


def query(mapping):
    return {'mapping_id': mapping['id'], 'query': {'fields': ['business_id', 'contract_amount', 'business_text'], 'limit': 50}}


def join(left, right, *, partial=False):
    return {'left': {'mapping_id': left['id'], 'query': {'limit': 50}},
            'right': {'mapping_id': right['id'], 'fields': ['business_id', 'contract_amount', 'business_text'], 'filters': []},
            'left_key': 'business_id', 'right_key': 'business_id', 'join_type': 'left',
            'max_rows': 100, 'allow_partial': partial}


async def core_checks(box, external, model):
    """The UI login produces every principal; no trusted identity is fabricated."""
    from backend.agent_tools import SessionToolBridge
    from backend.agent_tool_executor import AgentToolExecutor
    from backend.agent_tasks import complete_task
    from backend.security.store import authenticate_session
    from backend.security.context import use_context
    from backend.mcp_contracts import ToolFailure

    people, records = prepare(box, external)
    admin, builder, reviewer = (people['sub-01'][role] for role in ('admin', 'modeler', 'reviewer'))
    foreign = people['sub-02']['modeler']
    left, right = (records['sub-01'][kind] for kind in ('mysql', 'mcp'))
    protected = external.sensitive + [builder.client.cookies.get('ontology_session'), foreign.client.cookies.get('ontology_session')]
    checks = []

    def principal(actor):
        result = authenticate_session(actor.client.cookies.get('ontology_session'), 'agent-qa-'+uuid.uuid4().hex)
        assert result is not None
        return result[0]

    def bridge(actor=builder, mappings=None, skills=()):
        return SessionToolBridge(principal(actor), mappings if mappings is not None else [left['mapping']['id'], right['mapping']['id']], skills)

    async def check(name, operation):
        began = time.monotonic()
        try:
            if inspect.iscoroutinefunction(operation):
                await operation()
            else:
                await asyncio.to_thread(operation)
            row = {'name': name, 'passed': True}
        except Exception as failure:
            # Raw exception strings can contain source values, SQL or headers.
            row = {'name': name, 'passed': False, 'failure_class': type(failure).__name__}
            frame = traceback.extract_tb(failure.__traceback__)[-1]
            row['location'] = Path(frame.filename).name+':'+str(frame.lineno)
            # Only fixed HTTP metadata from our own synthetic callers, never
            # rejected bodies or an upstream exception's detail/message.
            match = re.match(r'^(GET|POST|PUT|PATCH|DELETE) ([^ ]+): ([0-9]{3}), expected', str(failure))
            if match:
                row.update(http_method=match[1], http_path=match[2], http_status=int(match[3]))
        row['seconds'] = round(time.monotonic()-began, 3)
        checks.append(row)
        print(json.dumps(row), flush=True)

    def allowed(value):
        assert value['status'] in ('ok', 'partial')
        return value['data']

    def denied(value):
        assert value['status'] == 'error' and value['data'] is None
        assert value['error']['code']
        assert not any(item in json.dumps(value, ensure_ascii=False) for item in protected)

    def live_scope():
        direct = bridge()
        discovered = allowed(direct.invoke_local('ontology_discover', {'limit': 100}))
        assert discovered['entities'] and all(item['owner_tenant_id'] == 'sub-01' for item in discovered['entities'])
        rows = allowed(direct.invoke_local('ontology_query', query(left['mapping'])))['rows']
        assert rows and all(item['business_text'] == external.markers['sub-01'] for item in rows)
        content = direct.invoke_local('ontology_read', {'entity_id': 'MCP-AMOUNT'})
        assert content['read_only'] and content['owner_tenant_id'] == 'sub-01'
        assert content['content']['definition'] == '已审核的业务数据标准'
        denied(direct.invoke_local('ontology_read', {'entity_id': 'MCP-HIDDEN'}))
        denied(direct.invoke_local('ontology_read', {'entity_id': 'MCP-AMOUNT', 'tenant_id': 'sub-02'}))
        denied(bridge(foreign, [left['mapping']['id']]).invoke_local('ontology_query', query(left['mapping'])))
        denied(bridge(mappings=[]).invoke_local('ontology_query', query(left['mapping'])))
        for tool, arguments in [('bash', {'command': 'forbidden'}), ('arbitrary_sql', {'sql': 'DELETE FROM contracts'}),
                                ('ontology_query', {**query(left['mapping']), 'tenant_id': 'sub-02'}),
                                ('ontology_query', {**query(left['mapping']), 'sql': 'SELECT * FROM contracts'})]:
            denied(direct.invoke_local(tool, arguments))
    await check('real password sessions intersect tenant, explicit selections and fixed readonly tool contracts', live_scope)

    def live_updates_and_join():
        direct = bridge()
        external.sql('UPDATE contracts SET amount=88.25 WHERE id=1')
        rows = allowed(direct.invoke_local('ontology_query', query(left['mapping'])))['rows']
        assert any(str(row['contract_amount']) == '88.25' for row in rows)
        joined = allowed(direct.invoke_local('ontology_join', join(left['mapping'], right['mapping'])))
        assert joined['complete'] and len(joined['rows']) == 2
        assert all(row['left']['business_id'] == row['right']['business_id'] for row in joined['rows'])
        direct.validate()
        assert {receipt['source_id'] for receipt in direct.receipts} == {left['source']['id'], right['source']['id']}
    await check('source updates and controlled two-source joins use current source rows with ownership receipts', live_updates_and_join)

    def partial_failures():
        try:
            external.write_state(query_http_status=503)
            denied(bridge().invoke_local('ontology_join', join(left['mapping'], right['mapping'])))
            value = bridge().invoke_local('ontology_join', join(left['mapping'], right['mapping'], partial=True))
            assert value['status'] == 'partial' and value['data']['failed_step'] == 'right'
            assert value['data']['rows'] == [] and len(value['data']['unjoined_left']) == 2
            external.write_state(query_http_status=403)
            denied(bridge().invoke_local('ontology_join', join(left['mapping'], right['mapping'], partial=True)))
        finally:
            external.write_state(query_http_status=0)
    await check('explicit source partial failure never converts revoked authorization into partial business rows', partial_failures)

    def source_revocation():
        direct = bridge()
        allowed(direct.invoke_local('ontology_query', query(left['mapping'])))
        admin.ok('PUT', '/sources/'+left['source']['id']+'/grants', json={'user_id': builder.user['id'], 'discover': True, 'query': False})
        try:
            denied(direct.invoke_local('ontology_query', query(left['mapping'])))
            try:
                direct.validate()
            except Exception:
                pass
            else:
                raise AssertionError('Prior receipt survived source revocation')
        finally:
            admin.ok('PUT', '/sources/'+left['source']['id']+'/grants', json={'user_id': builder.user['id'], 'discover': True, 'query': True})
    await check('source grant revocation invalidates next tool call and previously retained receipts', source_revocation)

    published = {}
    def published_skills():
        detail = skill(builder, 'agent-qa-'+uuid.uuid4().hex[:8])
        detail = save(builder, detail, profile=manifest())
        validate_and_test(builder, detail)
        detail = action(builder, detail, 'submit')
        detail = action(reviewer, detail, 'publish')
        published.update(detail)
        direct = bridge(skills=[skill_id(detail)])
        loaded = direct.invoke_local('skill_load', {'skill_id': skill_id(detail)})
        assert loaded['digest'] == detail['digest'] and loaded['instruction_authority'] == 'untrusted_guidance_never_permission'
        denied(bridge(skills=[]).invoke_local('skill_load', {'skill_id': skill_id(detail)}))
        denied(bridge(foreign, mappings=[], skills=[skill_id(detail)]).invoke_local('skill_load', {'skill_id': skill_id(detail)}))
        action(builder, detail, 'revise')
        try:
            direct.validate()
        except Exception:
            pass
        else:
            raise AssertionError('Withdrawn publication survived skill validation')
    await check('published skill load uses real digest-bound governance and immediately rejects withdrawal or foreign tenant', published_skills)

    async def real_task():
        admin.ok('POST', '/skills/harness/config', json={'base_url': model.url, 'api_key': model.key, 'model': 'deepseek-flash'})
        model.update(plan=[{'type': 'tool_use', 'id': 'task-submit', 'name': 'submit_task_result', 'input': {'text': '{"合成结果":700}'}},
                           {'type': 'text', 'text': '完成'}], allowed_tools=['submit_task_result'], forbidden=protected)
        p = principal(builder)
        with use_context(p):
            text = await complete_task(p, [{'role': 'user', 'content': '合成整数7元乘100换算为分，返回JSON。'}], json_mode=True)
        assert json.loads(text) == {'合成结果': 700}
        assert model.events()['authenticated'] >= 2
        assert model.events()['foreign_value_seen'] == 0
        assert model.events()['unexpected_tools'] == 0
    await check('real authenticated tenant model configuration invokes actual pinned SDK bounded structured task', real_task)

    async def killable_source_worker():
        executor = AgentToolExecutor(max_concurrent=1)
        p = principal(builder)
        arguments = query(right['mapping'])
        try:
            external.write_state(query_hold=True)
            began = time.monotonic()
            try:
                await executor.execute(p, [right['mapping']['id']], [], 'ontology_query', arguments,
                                       'deadline-test', deadline=asyncio.get_running_loop().time()+1)
            except ToolFailure as failure:
                assert failure.code == 'TIMEOUT'
            else:
                raise AssertionError('Held source survived deadline')
            assert time.monotonic()-began < 5
            assert not executor.children and executor.active == 0
            observed_queries = external.events().get('query_started', 0)
            task = asyncio.create_task(executor.execute(p, [right['mapping']['id']], [], 'ontology_query', arguments,
                                'cancel-test', deadline=asyncio.get_running_loop().time()+20))
            async with asyncio.timeout(5):
                while not executor.children or external.events().get('query_started', 0) <= observed_queries:
                    if task.done(): await task
                    await asyncio.sleep(.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError('Cancellation did not propagate')
            assert not executor.children and executor.active == 0
        finally:
            external.write_state(query_hold=False)
            await executor.close()
    await check('held real MCP source is terminated and reaped on overall deadline and caller cancellation', killable_source_worker)

    def no_persistence():
        for database in (box.control, box.tenant_db('sub-01'), box.tenant_db('sub-02')):
            for table in box.sql('SHOW TABLES', database=database):
                name = next(iter(table.values()))
                raw = json.dumps(box.sql('SELECT * FROM `'+name+'`', database=database), ensure_ascii=False, default=str)
                assert not any(value in raw for value in protected)
        for path in [*[item for item in box.root.rglob('*') if item.is_file()], box.log_path, external.log_path]:
            assert not any(value.encode() in path.read_bytes() for value in protected)
        assert not application_children(box.process.pid)
    await check('source rows, source secrets and original session cookies never persist in platform data or logs', no_persistence)
    return checks, people, records


async def http_checks(box, external, model, broker, people, records, *, only_hq=False):
    admin, builder, reviewer = (people['sub-01'][role] for role in ('admin', 'modeler', 'reviewer'))
    foreign = people['sub-02']['modeler']
    left, right = (records['sub-01'][kind] for kind in ('mysql', 'mcp'))
    base = '/agent/sessions/'
    checks, owned = [], []

    def session(actor=builder):
        value = actor.ok('POST', '/agent/sessions', json={}).json()
        owned.append((actor, value['id']))
        return value['id']

    def start(ident, *, mappings=(), skills=(), message='按当前授权进行合成只读分析。', actor=builder):
        value = actor.ok('POST', base+ident+'/turns', json={'message': message, 'mapping_ids': list(mappings),
                      'skill_ids': list(skills), 'entity_ids': []}).json()
        return value['run_id']

    async def finish(ident, run, *, expected='completed', timeout=35, actor=builder):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            response = actor.ok('GET', base+ident+'/runs/'+run)
            assert 'no-store' in response.headers.get('cache-control', '')
            value = response.json()
            if value['status'] not in ('queued', 'running', 'cancelling'):
                if expected is not None:
                    assert value['status'] == expected, 'Run ended with safe code '+str((value.get('error') or {}).get('code'))
                return value
            await asyncio.sleep(.05)
        raise AssertionError('Agent run exceeded test deadline')

    async def held(previous):
        async with asyncio.timeout(15):
            while model.events().get('held', 0) <= previous:
                await asyncio.sleep(.05)

    async def check(name, operation):
        if only_hq and name != 'headquarters own-provider agent traverses authorized subsidiary results with ownership and rejects grant-revoked replay':
            return
        began = time.monotonic()
        try:
            if inspect.iscoroutinefunction(operation):
                await operation()
            else:
                await asyncio.to_thread(operation)
            row = {'name': name, 'passed': True}
        except Exception as failure:
            row = {'name': name, 'passed': False, 'failure_class': type(failure).__name__}
            frame = traceback.extract_tb(failure.__traceback__)[-1]
            row['location'] = Path(frame.filename).name+':'+str(frame.lineno)
            match = re.match(r'^(GET|POST|PUT|PATCH|DELETE) ([^ ]+): ([0-9]{3}), expected', str(failure))
            if match:
                row.update(http_method=match[1], http_path=match[2], http_status=int(match[3]))
        row['seconds'] = round(time.monotonic()-began, 3)
        checks.append(row);print(json.dumps(row), flush=True)

    def ownership():
        anon = Session(box)
        expect(anon.request('GET', '/agent/capabilities'), 401)
        capabilities = builder.ok('GET', '/agent/capabilities').json()
        assert capabilities['configured'] and capabilities['available']
        assert capabilities['retention']['session'] == 'memory_only'
        ident = session()
        expect(foreign.request('GET', base+ident), 404)
        expect(reviewer.request('GET', base+ident), 404)
        other_login = Session(box, builder.username, builder.password);other_login.login()
        expect(other_login.request('GET', base+ident), 404)
        assert builder.ok('GET', base+ident).json()['messages'] == []
        for extra in ({'tenant_id': 'sub-02'}, {'principal': {'role': 'admin'}}, {'tools': ['bash']}):
            expect(builder.request('POST', base+ident+'/turns', json={'message': '合成问题', **extra}), 422)
        assert not any(item['id'] == ident for item in foreign.ok('GET', '/agent/sessions').json()['items'])
    await check('broker requires real cookie and CSRF; sessions bind user, login and workspace with no client identity overrides', ownership)

    def foreign_model_credential():
        headquarters = next(actor for actor in box.clients if actor.user.get('is_superadmin'))
        before = model.events().get('requests', 0)
        headquarters.switch('sub-01')
        try:
            value = headquarters.ok('GET', '/agent/capabilities').json()
            assert value['requires_home_workspace'] and not value['configured'] and not value['available']
            expect(headquarters.request('POST', '/agent/sessions', json={}), 403)
            assert model.events().get('requests', 0) == before
        finally:
            headquarters.switch('hq')
    await check('headquarters workspace operation does not consume another institutions provider credential', foreign_model_credential)

    async def headquarters_group():
        root = next(actor for actor in box.clients if actor.user.get('is_superadmin'))
        hq = box.user(root, 'hq', 'modeler')
        hq_key = 'SYNTHETIC_HEADQUARTERS_' + secrets.token_hex(24)
        root.ok('POST', '/skills/harness/config', json={'base_url': model.url, 'api_key': hq_key, 'model': 'deepseek-flash'})
        model.update(api_key=hq_key, mode='normal', expected_values=[],
                     allowed_tools=['ontology_read','ontology_discover','ontology_traverse','ontology_explain','group_search','group_graph'],
                     plan=[{'type': 'tool_use', 'id': 'hq-search', 'name': 'group_search', 'input': {'tenant_id': 'sub-01', 'query': 'MCP'}},
                           {'type': 'tool_use', 'id': 'hq-graph', 'name': 'group_graph', 'input': {'tenant_id': 'sub-02', 'query': 'MCP'}},
                           {'type': 'tool_use', 'id': 'hq-read', 'name': 'ontology_read', 'input': {'tenant_id': 'sub-02', 'entity_id': 'MCP-AMOUNT'}},
                           {'type': 'text', 'text': '总部使用自身模型凭据，按集团授权查阅两个机构的已发布成果。'}])
        try:
            session_id = session(hq)
            run = start(session_id, actor=hq)
            value = await finish(session_id, run, actor=hq)
            assert {tool['name'] for tool in value['tools']} == {'group_search', 'group_graph', 'ontology_read'}
            assert all(tool['status'] == 'ok' for tool in value['tools'])
            assert {'sub-01', 'sub-02'} <= {item['tenant_id'] for item in value['provenance']}
            grants = root.ok('GET', '/admin/grants').json()['items']
            grant = next(item for item in grants if item['user_id'] == hq.user['id'] and item['tenant_id'] == 'sub-02')
            root.ok('DELETE', '/admin/grants/'+grant['id'])
            rejected = hq.request('GET', base+session_id+'/runs/'+run)
            assert rejected.status_code in (401, 403, 409)
            assert 'answer' not in rejected.json()
        finally:
            model.update(api_key=model.key)
    await check('headquarters own-provider agent traverses authorized subsidiary results with ownership and rejects grant-revoked replay', headquarters_group)

    async def complete_agent():
        sensitive = [item for item in external.sensitive if item not in (external.markers['sub-01'], external.mcp_marker)]
        model.update(mode='normal', allowed_tools=['ontology_read','ontology_discover','ontology_query','ontology_traverse','ontology_explain','ontology_join','group_search','group_graph'],
                     forbidden=sensitive, expected_values=[external.markers['sub-01'], external.mcp_marker],
                     plan=[{'type': 'tool_use', 'id': 'query', 'name': 'ontology_query', 'input': query(left['mapping'])},
                           {'type': 'tool_use', 'id': 'join', 'name': 'ontology_join', 'input': join(left['mapping'], right['mapping'])},
                           {'type': 'text', 'text': '已根据两个授权来源完成合成只读分析；结果仅表示本次有界查询。'}])
        ident = session()
        run = start(ident, mappings=[left['mapping']['id'], right['mapping']['id']])
        value = await finish(ident, run)
        assert value['answer'] and not value['partial']
        assert {item['name'] for item in value['tools']} == {'ontology_query', 'ontology_join'}
        assert {item['source_id'] for item in value['provenance']} == {left['source']['id'], right['source']['id']}
        assert all(item['tenant_id'] == 'sub-01' for item in value['provenance'])
        detail = builder.ok('GET', base+ident).json()
        assert [item['role'] for item in detail['messages']] == ['user', 'assistant']
        assert model.events()['selected_rows_seen'] > 0 and model.events()['foreign_value_seen'] == 0
        # Narrowing selection must not replay a prior source-backed answer.
        model.update(plan=[{'type': 'text', 'text': '当前未选择外部映射，仅处理当前本体语义问题。'}], expected_values=[], allowed_tools=['ontology_read','ontology_discover','ontology_traverse','ontology_explain','group_search','group_graph'])
        next_run = start(ident)
        await finish(ident, next_run)
        assert 'answer' not in builder.ok('GET', base+ident+'/runs/'+run).json()
        assert len(builder.ok('GET', base+ident).json()['messages']) == 2
    await check('main API -> dedicated broker -> real Harness -> killable two-source workers returns provenance and clears narrowed history', complete_agent)

    async def source_revoke_inflight():
        model.update(mode='hold', hold_at=1, allowed_tools=['ontology_read','ontology_discover','ontology_query','ontology_traverse','ontology_explain','ontology_join','group_search','group_graph'],
                     expected_values=[], plan=[{'type': 'tool_use', 'id': 'query', 'name': 'ontology_query', 'input': query(left['mapping'])},
                          {'type': 'text', 'text': '不应在撤权后返回的合成结果。'}])
        before = model.events().get('held', 0)
        ident = session();run = start(ident, mappings=[left['mapping']['id']])
        await held(before)
        admin.ok('PUT', '/sources/'+left['source']['id']+'/grants', json={'user_id': builder.user['id'], 'discover': True, 'query': False})
        try:
            model.update(mode='normal')
            # Reading an active source-backed run must also invalidate/scrub.
            response = builder.request('GET', base+ident+'/runs/'+run)
            assert response.status_code in (200, 409)
            if response.status_code == 200:
                assert 'answer' not in response.json()
            await asyncio.sleep(.5)
            detail = builder.ok('GET', base+ident).json()
            assert detail['messages'] == []
            assert all('answer' not in value for value in detail['turns'])
        finally:
            model.update(mode='normal')
            admin.ok('PUT', '/sources/'+left['source']['id']+'/grants', json={'user_id': builder.user['id'], 'discover': True, 'query': True})
    await check('revocation after source rows enter model memory cancels output and scrubs retrieval/history', source_revoke_inflight)

    async def published_skill_replay():
        detail = skill(builder, 'agent-runtime-'+uuid.uuid4().hex[:8])
        detail = save(builder, detail, profile=manifest())
        validate_and_test(builder, detail)
        detail = action(builder, detail, 'submit')
        detail = action(reviewer, detail, 'publish')
        model.update(mode='normal', expected_values=[],
                     allowed_tools=['ontology_read','ontology_discover','ontology_traverse','ontology_explain','group_search','group_graph','skill_load','skill_execute'],
                     plan=[{'type': 'tool_use', 'id': 'load-skill', 'name': 'skill_load', 'input': {'skill_id': skill_id(detail)}},
                           {'type': 'text', 'text': '已参考当前审核发布的合成技能，未修改或发布任何成果。'}])
        session_id = session();run = start(session_id, skills=[skill_id(detail)])
        result = await finish(session_id, run)
        assert any(item['name'] == 'skill_load' and item['status'] == 'ok' for item in result['tools'])
        action(builder, detail, 'revise')
        expect(builder.request('GET', base+session_id+'/runs/'+run), 409)
        value = builder.ok('GET', base+session_id).json()
        assert value['messages'] == [] and all('answer' not in turn for turn in value['turns'])
    await check('completed agent skill guidance cannot be replayed after the publication is withdrawn', published_skill_replay)

    async def parallel_admission():
        mappings = [left['mapping']['id']]
        model.update(mode='normal', expected_values=[],
                     allowed_tools=['ontology_read','ontology_discover','ontology_query','ontology_traverse','ontology_explain','ontology_join','group_search','group_graph'],
                     plan=[{'type': 'tool_use', 'id': 'seed-query', 'name': 'ontology_query', 'input': query(left['mapping'])},
                           {'type': 'text', 'text': '已建立用于授权重核的合成来源上下文。'}])
        session_id = session()
        await finish(session_id, start(session_id, mappings=mappings))
        # A retained receipt forces the start path to await real authorization
        # work. An empty new session would not reliably expose this race.
        model.update(mode='hold', hold_at=0, plan=[{'type': 'text', 'text': '并发准入测试'}])
        before = model.events().get('held', 0)
        accepted = []
        admission_gate = threading.Barrier(2)
        def post():
            admission_gate.wait(timeout=5)
            return builder.request('POST', base+session_id+'/turns', json={'message': '并行提问仅允许一个运行',
                        'mapping_ids': mappings, 'skill_ids': [], 'entity_ids': []})
        def concurrent_posts():
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(post) for _ in range(2)]
                return [future.result(timeout=25) for future in futures]
        try:
            replies = await asyncio.to_thread(concurrent_posts)
            accepted.extend(response.json()['run_id'] for response in replies if response.status_code == 200)
            assert sorted(response.status_code for response in replies) == [200, 409]
            assert len(accepted) == 1
            await held(before)
            assert model.events().get('held', 0) == before + 1
        finally:
            for run_id in accepted:
                builder.request('POST', base+session_id+'/runs/'+run_id+'/cancel')
            model.update(mode='normal')
        assert not application_children(broker.process.pid)
    await check('parallel starts sharing a source-backed session admit one run across asynchronous authorization checks', parallel_admission)

    async def cancel_and_serialization():
        model.update(mode='hold', hold_at=0, expected_values=[], plan=[{'type': 'text', 'text': '取消后不应返回'}],
                     allowed_tools=['ontology_read','ontology_discover','ontology_traverse','ontology_explain','group_search','group_graph'])
        before = model.events().get('held', 0)
        ident = session();run = start(ident)
        await held(before)
        expect(builder.request('POST', base+ident+'/turns', json={'message': '并发第二问'}), 409)
        began = time.monotonic()
        value = builder.ok('POST', base+ident+'/runs/'+run+'/cancel').json()
        assert time.monotonic()-began < 8 and value['status'] == 'cancelled' and 'answer' not in value
        model.update(mode='normal')
        await asyncio.sleep(.1)
        assert not application_children(broker.process.pid)
        builder.ok('DELETE', base+ident)
        expect(builder.request('GET', base+ident), 404)
    await check('active turn concurrency is rejected; real provider-wait cancellation reaps processes and session deletion is final', cancel_and_serialization)

    def old_hardcoded_removed():
        before = box.business_snapshot('sub-01')
        expect(builder.request('POST', '/applications/run', json={'scenario_id': 'legacy'}), 410)
        after = box.business_snapshot('sub-01')
        assert before == after
    await check('legacy hardcoded application execution is retired without creating findings or entities', old_hardcoded_removed)

    def retention():
        protected = external.sensitive + [builder.client.cookies.get('ontology_session'), foreign.client.cookies.get('ontology_session')]
        for database in (box.control, box.tenant_db('sub-01'), box.tenant_db('sub-02')):
            for table in box.sql('SHOW TABLES', database=database):
                name = next(iter(table.values()))
                raw = json.dumps(box.sql('SELECT * FROM `'+name+'`', database=database), ensure_ascii=False, default=str)
                assert not any(item in raw for item in protected)
        for path in [*[item for item in box.root.rglob('*') if item.is_file()], box.log_path, broker.log_path, external.log_path]:
            raw = path.read_bytes()
            assert not any(item.encode() in raw for item in protected)
    await check('completed model-backed source analyses do not persist business rows or raw caller/source credentials', retention)
    for actor, session_id in owned:
        actor.request('DELETE', base+session_id)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8920)
    parser.add_argument('--source-port', type=int, default=8921)
    parser.add_argument('--model-port', type=int, default=8922)
    parser.add_argument('--broker-port', type=int, default=8923)
    parser.add_argument('--ui-access', type=Path)
    parser.add_argument('--ui-release', type=Path)
    parser.add_argument('--only-hq', action='store_true', help='Focused reproduction of the headquarters adapter acceptance case')
    parser.add_argument('--ui-only', action='store_true', help='Prepare a private synthetic browser fixture without repeating acceptance cases')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'work/agent-runtime-report.json')
    args = parser.parse_args()
    if bool(args.ui_access) != bool(args.ui_release):
        parser.error('Both private UI handoff paths are required')
    if args.ui_access and (args.ui_access.exists() or args.ui_release.exists()):
        parser.error('UI handoff paths must be fresh')
    if args.ui_only and (not args.ui_access or args.only_hq):
        parser.error('UI-only requires UI handoff paths and cannot combine with headquarters-only')
    box = Sandbox(port=args.port, output=args.output, frontend_dist=ROOT/'frontend/dist' if args.ui_access else None)
    external = Sources(box, args.source_port)
    report = {'result': 'failed', 'model': 'synthetic Replies via real pinned Harness SDK; no real provider calls'}
    report['selection'] = 'ui-fixture-only' if args.ui_only else 'headquarters-only' if args.only_hq else 'full'
    try:
        with external, Model(box, args.model_port) as model, environment(
                ONTOLOGY_SOURCE_CREDENTIALS_FILE=str(external.registry), ONTOLOGY_HARNESS_ALLOW_LOOPBACK_TEST='1',
                ONTOLOGY_AGENT_SERVICE_URL='http://127.0.0.1:'+str(args.broker_port)):
            with box:
                with Broker(box, args.broker_port) as broker:
                    settings = {'MYSQL_'+key.upper(): str(value) for key, value in box.settings.items()}
                    with environment(**settings, MYSQL_DATABASE=box.namespace, ONTOLOGY_STORAGE_ROOT=str(box.storage)):
                        if args.only_hq or args.ui_only:
                            people, records = prepare(box, external)
                            report['checks'] = []
                        else:
                            report['checks'], people, records = asyncio.run(core_checks(box, external, model))
                        if args.ui_only:
                            people['sub-01']['admin'].ok('POST', '/skills/harness/config', json={
                                'base_url': model.url, 'api_key': model.key, 'model': 'deepseek-flash'})
                        else:
                            report['checks'] += asyncio.run(http_checks(box, external, model, broker, people, records, only_hq=args.only_hq))
                    report['result'] = ('fixture_ready' if args.ui_only else 'passed') if all(item['passed'] for item in report['checks']) else 'failed'
                    if args.ui_access and report['result'] in ('passed', 'fixture_ready'):
                        model.update(mode='normal', plan=[{'type': 'text', 'text': '这是当前机构的合成只读回答；可以继续提问，也可取消正在运行的任务。'}],
                                     allowed_tools=['ontology_read','ontology_discover','ontology_traverse','ontology_explain','group_search','group_graph'], expected_values=[])
                        private_json(args.ui_access, {'url': box.url, 'tenant_id': 'sub-01',
                            'accounts': {role: {'username': actor.username, 'password': actor.password}
                                         for role, actor in people['sub-01'].items()},
                            'model_state_file': str(model.state_path), 'model_events_file': str(model.state_path.with_suffix('.events.json')),
                            'backend_snapshot_sha256': box.source_sha256})
                        print(json.dumps({'stage': 'ui_handoff_ready', 'access_file': str(args.ui_access)}), flush=True)
                        deadline = time.monotonic()+300
                        while not args.ui_release.exists() and time.monotonic() < deadline:
                            time.sleep(.1)
                        report['ui_handoff'] = 'released' if args.ui_release.exists() else 'expired_after_5min; UI not claimed'
                report['result'] = ('fixture_ready' if args.ui_only else 'passed') if all(item['passed'] for item in report['checks']) else 'failed'
                report['backend_snapshot_sha256'] = box.source_sha256
    except Exception as failure:
        report['failure_class'] = type(failure).__name__
        frame = traceback.extract_tb(failure.__traceback__)[-1]
        report['location'] = Path(frame.filename).name+':'+str(frame.lineno)
    finally:
        if args.ui_access:
            args.ui_access.unlink(missing_ok=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'result': report['result'], 'report': str(args.output)}), flush=True)
    return 0 if report['result'] in ('passed', 'fixture_ready') else 1


if __name__ == '__main__':
    raise SystemExit(main())
