"""Real upper MCP -> ontology service -> MySQL/MCP source integration checks.

Creates disposable app credentials through authenticated management REST. No
caller token, result value or filter value is written to reports or fixtures.
MYSQL_* selects the dedicated test server; all namespaces/users are cleaned up.
"""
from __future__ import annotations
import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
import copy
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import time

import httpx
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from check_security import Sandbox, Session, expect
from check_sources import Sources, environment, private_json

TOOLS = ['ontology_discover', 'ontology_query', 'ontology_traverse', 'ontology_explain', 'ontology_join']
APPS = '/integrations/applications'
RETENTION = {'result': 'response_only', 'cache': 'disabled', 'audit': 'metadata_only'}
# The test caller never prints third-party exception payloads or HTTP headers.
logging.getLogger('mcp').setLevel(logging.CRITICAL)
logging.getLogger('httpx2').setLevel(logging.ERROR)


def application_children(pid):
    parents = {}
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
            parents[int(path.name)] = int(fields[1])
        except (OSError, ValueError, IndexError):
            continue
    found, frontier = set(), {pid}
    while frontier:
        children = {child for child, parent in parents.items() if parent in frontier} - found
        found.update(children)
        frontier = children
    return found


async def wait_reaped(box, timeout=4):
    deadline = time.monotonic() + timeout
    while application_children(box.process.pid):
        if time.monotonic() > deadline:
            raise AssertionError('An MCP worker remained after cancellation/deadline')
        await asyncio.sleep(.05)


class Caller:
    def __init__(self, box, protected):
        self.url, self.protected = box.url + '/mcp/', protected
        self.responses = []

    async def response(self, response):
        self.responses.append({'status': response.status_code,
                               'no_store': 'no-store' in response.headers.get('cache-control', ''),
                               'request_id': bool(response.headers.get('x-request-id'))})

    @asynccontextmanager
    async def open(self, token):
        async with httpx2.AsyncClient(headers={'Authorization': 'Bearer ' + token}, timeout=40,
                                     trust_env=False, follow_redirects=False,
                                     event_hooks={'response': [self.response]}) as client:
            async with streamable_http_client(self.url, http_client=client, max_sse_event_size=2 * 1024 * 1024 + 65536) as streams:
                async with ClientSession(*streams, read_timeout_seconds=40) as session:
                    await session.initialize()
                    yield session

    def unpack(self, result):
        value = result.structured_content
        assert isinstance(value, dict) and value['contract_version'] == '1.0'
        assert value['retention'] == RETENTION and value['request_id']
        assert value['status'] in ('ok', 'partial', 'error')
        for block in result.content:
            assert getattr(block, 'type', '') == 'text'
            text = getattr(block, 'text', '')
            assert len(text) < 1000 and not any(secret in text for secret in self.protected())
        if value['status'] == 'error':
            assert value['data'] is None and value['error']['code']
        return value

    async def call(self, token, tool, arguments):
        async with self.open(token) as session:
            return self.unpack(await session.call_tool(tool, arguments, read_timeout_seconds=40))


def success(value, *, partial=False):
    assert value['status'] in (('ok', 'partial') if partial else ('ok',)), 'Tool did not succeed: ' + str((value.get('error') or {}).get('code'))
    return value['data']


def failure(value, codes=None):
    assert value['status'] == 'error' and value['data'] is None, 'Unauthorized/incomplete call exposed data'
    if codes:
        assert value['error']['code'] in set(codes), 'Unexpected safe error code: ' + value['error']['code']
    return value


def prepare(box, external):
    root = Session(box, box.admin_name, box.admin_password)
    root.login()
    people, records = {}, {}
    for tenant, prefix in [('sub-01', 'QA-A'), ('sub-02', 'QA-B')]:
        admin = box.user(root, tenant, 'admin')
        builder = box.user(root, tenant, 'modeler')
        reviewer = box.user(root, tenant, 'reviewer')
        people[tenant] = {'admin': admin, 'modeler': builder, 'reviewer': reviewer}
        for ident, title in [('MCP-KEY', '业务编号'), ('MCP-AMOUNT', '金额'), ('MCP-TEXT', '业务文本')]:
            builder.create(ident, prefix + '-' + title, payload={'definition': '已审核的业务数据标准', 'unit': '无量纲'})
            for actor, action in ((builder, 'submit'), (reviewer, 'approve'), (reviewer, 'publish')):
                actor.ok('POST', '/entities/' + ident + '/action', json={'action': action, 'reason': 'QA异人审核标准'})
        builder.create('MCP-HIDDEN', prefix + '-未发布内容', payload={'definition': '不能穿透未发布节点'})
        database = box.tenant_db(tenant)
        for ident, left, right, status in [('EDGE-1', 'MCP-KEY', 'MCP-AMOUNT', 'confirmed'),
                                           ('EDGE-2', 'MCP-AMOUNT', 'MCP-TEXT', 'confirmed'),
                                           ('EDGE-HIDDEN', 'MCP-KEY', 'MCP-HIDDEN', 'confirmed'),
                                           ('EDGE-PENDING', 'MCP-KEY', 'MCP-TEXT', 'candidate')]:
            box.sql('INSERT INTO relations(id,source,target,type,label,status,payload) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                    (ident, left, right, 'references_data', 'QA审核关系', status, '{}'), database)
        kinds = ['mysql', 'mcp'] if tenant == 'sub-01' else ['mysql']
        records[tenant] = {}
        for kind in kinds:
            config = {'host': box.settings['host'], 'port': box.settings['port'], 'database': external.schemas[tenant],
                      'allowed_objects': ['contracts', 'active_contracts']} if kind == 'mysql' else {
                          'endpoint': external.endpoint, 'allowed_objects': [item['key'] for item in external.state['resources']]}
            source = admin.ok('POST', '/sources', json={'title': prefix + ' ' + kind, 'kind': kind, 'config': config,
                                                       'credential_ref': kind + '-' + tenant}).json()
            for actor in (builder, reviewer):
                admin.ok('PUT', f"/sources/{source['id']}/grants", json={'user_id': actor.user['id'], 'discover': True, 'query': True})
            resources = builder.ok('POST', '/sources/' + source['id'] + '/discover').json()['resources']
            resource = next(item for item in resources if item['kind'] == 'table')
            item = {'source': source, 'resource': resource, 'admin': admin, 'builder': builder, 'reviewer': reviewer}
            item['mapping'] = publish_mapping(item)
            records[tenant][kind] = item
            if kind == 'mcp':
                saved = {**item, 'resource': next(value for value in resources if value['kind'] == 'saved_query')}
                saved['mapping'] = publish_mapping(saved)
                records[tenant]['saved'] = saved
    return people, records


def mapping_body(resource):
    return {'resource_id': resource['id'], 'title': 'QA已审核的跨源绑定', 'business_meaning': '相同业务编号对应的合同信息',
            'grain': '每条记录代表一个业务对象', 'object_key': ['id'],
            'relation_keys': [{'column': 'id', 'target_asset_id': 'MCP-KEY'}],
            'fields': [{'column': col, 'field': field, 'asset_id': asset, 'meaning': meaning, 'unit': unit}
                       for col, field, asset, meaning, unit in [('id', 'business_id', 'MCP-KEY', '业务对象编号', '无量纲'),
                           ('amount', 'contract_amount', 'MCP-AMOUNT', '合同金额', '元'),
                           ('secret', 'business_text', 'MCP-TEXT', '业务说明', '无量纲')]]}


def publish_mapping(item, existing=False):
    source, builder, reviewer = item['source'], item['builder'], item['reviewer']
    base = f"/sources/{source['id']}/mappings"
    if existing:
        detail = builder.ok('GET', '/sources/' + source['id']).json()
        mapping = next(value for value in detail['mappings'] if value['id'] == item['mapping']['id'])
        if mapping['status'] != 'draft':
            mapping = builder.ok('POST', base + '/' + mapping['id'] + '/action', json={
                'action': 'revise', 'expected_version': mapping['version'], 'reason': 'QA人工修订'}).json()
        mapping = builder.ok('PUT', base + '/' + mapping['id'], json={**mapping_body(item['resource']), 'expected_version': mapping['version']}).json()
    else:
        mapping = builder.ok('POST', base, json=mapping_body(item['resource'])).json()
    for actor, action in ((builder, 'submit'), (reviewer, 'publish')):
        mapping = actor.ok('POST', base + '/' + mapping['id'] + '/action', json={
            'action': action, 'expected_version': mapping['version'], 'reason': 'QA明确核对业务键、同版标准和两个来源'}).json()
    return mapping


async def run_checks(box, external, ui_access=None):
    checks, tokens = [], []
    people, records = prepare(box, external)
    admin, builder, reviewer = (people['sub-01'][name] for name in ('admin', 'modeler', 'reviewer'))
    left, right, saved = (records['sub-01'][name] for name in ('mysql', 'mcp', 'saved'))
    other = records['sub-02']['mysql']
    all_ids = [item['mapping']['id'] for item in (left, right, saved)]
    protected = lambda: external.sensitive + tokens
    caller = Caller(box, protected)

    def redact(value):
        for item in protected():
            value = value.replace(item, '<redacted-test-value>')
        return value

    async def check(name, operation):
        began = time.monotonic()
        try:
            value = operation()
            if inspect.isawaitable(value):
                await value
            result = {'name': name, 'passed': True}
        except Exception as exc:
            result = {'name': name, 'passed': False, 'error': redact(str(exc))[:1000]}
            trace = exc.__traceback__
            while trace and trace.tb_next:
                trace = trace.tb_next
            if trace:
                result['failure_location'] = Path(trace.tb_frame.f_code.co_filename).name + ':' + str(trace.tb_lineno)
        result['seconds'] = round(time.monotonic() - began, 3)
        checks.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    def app_create(actor, delegate, ids, tools=TOOLS, name='QA应用'):
        value = actor.ok('POST', APPS, json={'name': name, 'description': 'QA真实调用授权',
            'delegate_user_id': delegate.user['id'], 'tool_scopes': tools, 'mapping_ids': ids, 'expires_days': 1}).json()
        tokens.append(value['credential']['token'])
        return value

    def app_current(value, actor=admin):
        return next(item for item in actor.ok('GET', APPS).json()['items'] if item['id'] == value['application']['id'])

    def app_patch(value, changes, actor=admin):
        current = app_current(value, actor)
        value['application'] = actor.ok('PATCH', APPS + '/' + current['id'], json={'expected_version': current['version'], **changes}).json()
        return value

    def rotate(value, actor=admin):
        current = app_current(value, actor)
        updated = actor.ok('POST', APPS + '/' + current['id'] + '/credentials', json={
            'expected_version': current['version'], 'expires_days': 1, 'reason': 'QA明确重新签发'}).json()
        value.update(updated)
        tokens.append(updated['credential']['token'])
        return value

    first = app_create(admin, builder, all_ids)
    second = app_create(people['sub-02']['admin'], people['sub-02']['modeler'], [other['mapping']['id']], name='QA机构B应用')
    token = lambda value=first: value['credential']['token']

    def q(item, **extra):
        return {'mapping_id': item['mapping']['id'], 'query': {'limit': 20}, **extra}

    def join(**extra):
        return {'left': {'mapping_id': left['mapping']['id'], 'query': {'limit': 50}},
                'right': {'mapping_id': right['mapping']['id'], 'fields': ['business_id', 'contract_amount', 'business_text'], 'filters': []},
                'left_key': 'business_id', 'right_key': 'business_id', 'join_type': 'left', 'max_rows': 200,
                'allow_partial': False, **extra}

    def grant(item, enabled):
        item['admin'].ok('PUT', f"/sources/{item['source']['id']}/grants", json={'user_id': item['builder'].user['id'], 'discover': True, 'query': enabled})

    def business_snapshot():
        result = {}
        for tenant in ('sub-01', 'sub-02'):
            database = box.tenant_db(tenant)
            for table in box.sql('SHOW TABLES', database=database):
                name = next(iter(table.values()))
                if name.startswith('source_'):
                    continue
                data = box.sql(f'SELECT * FROM `{name}`', database=database)
                result[(tenant, name)] = hashlib.sha256(json.dumps(sorted(json.dumps(row, sort_keys=True, default=str) for row in data)).encode()).hexdigest()
        return result
    original_business = business_snapshot()

    async def all_tools():
        async with caller.open(token()) as session:
            listing = await session.list_tools()
            assert {tool.name for tool in listing.tools} == set(TOOLS)
        data = success(await caller.call(token(), 'ontology_discover', {'query': 'QA-A', 'kind': 'data'}))
        assert {item['id'] for item in data['entities']} == {'MCP-KEY', 'MCP-AMOUNT', 'MCP-TEXT'}
        assert all(item['owner_tenant_id'] == 'sub-01' and 'payload' not in item for item in data['entities'])
        data = success(await caller.call(token(), 'ontology_traverse', {'entity_id': 'MCP-KEY', 'depth': 3}))
        assert {item['id'] for item in data['nodes']} == {'MCP-KEY', 'MCP-AMOUNT', 'MCP-TEXT'}
        assert {item['id'] for item in data['edges']} == {'EDGE-1', 'EDGE-2'}
        limited = success(await caller.call(token(), 'ontology_traverse', {'entity_id': 'MCP-KEY', 'max_nodes': 1}), partial=True)
        assert limited['truncated'] and len(limited['nodes']) == 1
        before = external.events().get('query_started', 0)
        explanation = success(await caller.call(token(), 'ontology_explain', {'mapping_id': saved['mapping']['id']}))
        encoded = json.dumps(explanation)
        assert not any(value in encoded for value in [external.endpoint, external.schemas['sub-01'], 'credential_ref', 'SELECT id,amount'])
        assert external.events().get('query_started', 0) == before
        for item, marker in ((left, external.markers['sub-01']), (right, external.mcp_marker)):
            value = await caller.call(token(), 'ontology_query', q(item))
            data = success(value)
            assert len(data['rows']) == 2 and all(row['business_text'] == marker for row in data['rows'])
            assert value['provenance'] and all(receipt['tenant_id'] == 'sub-01' for receipt in value['provenance'])
        data = success(await caller.call(token(), 'ontology_join', join()))
        assert data['complete'] and len(data['rows']) == 2
        assert all(row['left']['business_id'] == row['right']['business_id'] for row in data['rows'])

    await check('official upper SDK initialize/list_tools and all five tools traverse real MySQL plus MCP sources', all_tools)

    async def input_and_identity():
        for name, args in [('ontology_query', {**q(left), 'tenant_id': 'sub-02'}),
                           ('ontology_query', {'mapping_id': left['mapping']['id'], 'query': {'sql': external.filter_marker}}),
                           ('ontology_join', join(left={'mapping_id': left['mapping']['id'], 'query': {'limit': 51}})),
                           ('ontology_traverse', {'entity_id': 'MCP-KEY', 'depth': 4})]:
            failure(await caller.call(token(), name, args), {'INVALID_ARGUMENT'})
        failure(await caller.call(token(), 'ontology_traverse', {'entity_id': 'MCP-HIDDEN'}), {'FORBIDDEN'})
        filtered = success(await caller.call(token(), 'ontology_query', {'mapping_id': left['mapping']['id'],
            'query': {'filters': [{'field': 'business_text', 'op': 'eq', 'value': external.filter_marker}]}}))
        assert filtered['rows'] == []
        with httpx.Client(trust_env=False, timeout=5) as raw:
            response = raw.get(box.url + '/mcp/')
            assert response.status_code == 401 and 'www-authenticate' in response.headers
            assert raw.get(box.url + '/mcp/', cookies=admin.client.cookies).status_code in (401, 403)
            assert raw.get(box.url + '/mcp/', headers={'Authorization': 'Bearer ' + external.token}).status_code == 401
            assert raw.get(box.url + '/api/entities', headers={'Authorization': 'Bearer ' + token()}).status_code == 401
            assert raw.post(box.url + '/api/entities', headers={'Authorization': 'Bearer ' + token()}, json={'kind': 'data', 'title': 'forbidden'}).status_code == 401
            for header in ('X-Tenant-Id', 'X-Workspace-Id', 'X-Actor-Id'):
                assert raw.get(box.url + '/mcp/', headers={'Authorization': 'Bearer ' + token(), header: 'sub-02'}).status_code in (400, 401, 403)
            assert raw.get(box.url + '/mcp/?tenant_id=sub-02', headers={'Authorization': 'Bearer ' + token()}).status_code in (400, 401, 403)
        public = admin.ok('GET', APPS).text
        assert not any(secret in public for secret in tokens)
        expect(builder.request('GET', APPS), 403)
        expect(admin.request('POST', APPS, json={'name': 'QA拒绝跨机构委托', 'delegate_user_id': people['sub-02']['modeler'].user['id'],
            'tool_scopes': TOOLS, 'mapping_ids': []}), {403, 422})

    await check('unknown SQL/tenant arguments, browser-cookie substitution, app-to-browser API and cross-tenant delegation are denied', input_and_identity)

    async def scopes_and_tenants():
        failure(await caller.call(token(), 'ontology_query', q(other)), {'FORBIDDEN'})
        failure(await caller.call(token(second), 'ontology_query', q(left)), {'FORBIDDEN'})
        values = await asyncio.gather(caller.call(token(), 'ontology_query', q(left)),
                                      caller.call(token(second), 'ontology_query', q(other)))
        assert success(values[0])['rows'][0]['business_text'] == external.markers['sub-01']
        assert success(values[1])['rows'][0]['business_text'] == external.markers['sub-02']
        data = success(await caller.call(token(second), 'ontology_discover', {'kind': 'data'}))
        assert all(item['owner_tenant_id'] == 'sub-02' and item['title'].startswith('QA-B') for item in data['entities'])
        app_patch(first, {'tool_scopes': ['ontology_discover'], 'mapping_ids': [left['mapping']['id']]})
        failure(await caller.call(token(), 'ontology_query', q(left)), {'FORBIDDEN'})
        app_patch(first, {'tool_scopes': TOOLS, 'mapping_ids': [left['mapping']['id']]})
        failure(await caller.call(token(), 'ontology_query', q(right)), {'FORBIDDEN'})
        success(await caller.call(token(), 'ontology_query', q(left)))
        app_patch(first, {'tool_scopes': TOOLS, 'mapping_ids': all_ids})

    await check('mixed real app clients retain tenant isolation; live tool and mapping scope reductions apply to the same credential', scopes_and_tenants)

    async def join_bounds():
        original = copy.deepcopy(external.state['rows'])
        try:
            external.write_state(rows=[*original, dict(original[0])])
            assert len(success(await caller.call(token(), 'ontology_join', join()))['rows']) == 3
            limited = success(await caller.call(token(), 'ontology_join', join(max_rows=1)), partial=True)
            assert len(limited['rows']) == 1 and limited['result_truncated'] and not limited['complete']
            external.write_state(rows=[{**row, 'id': str(row['id'])} for row in original], coerce_key_filters=True)
            data = success(await caller.call(token(), 'ontology_join', join(join_type='inner')))
            assert data['rows'] == [], 'Join silently coerced string and numeric keys'
            external.write_state(rows=[dict(original[0]) for _ in range(201)], coerce_key_filters=False)
            value = await caller.call(token(), 'ontology_join', join())
            data = success(value, partial=True)
            assert data['right_truncated'] and not data['complete'] and data['unknown_unmatched_count'] == 1
            assert all(row['right'] is not None for row in data['rows']), 'Truncated right side falsely asserted no match'
        finally:
            external.write_state(rows=original, coerce_key_filters=False)

    await check('real two-source join preserves duplicates, enforces typed keys and reports capped/unknown matches', join_bounds)

    async def partial_rules():
        try:
            external.write_state(query_http_status=503)
            failure(await caller.call(token(), 'ontology_join', join()), {'SOURCE_UNAVAILABLE'})
            value = await caller.call(token(), 'ontology_join', join(allow_partial=True))
            assert value['status'] == 'partial'
            assert value['data']['rows'] == [] and len(value['data']['unjoined_left']) == 2
            assert value['data']['failed_step'] == 'right' and not value['data']['complete']
            for status in (401, 403):
                external.write_state(query_http_status=status)
                failure(await caller.call(token(), 'ontology_join', join(allow_partial=True)), {'FORBIDDEN', 'UNAUTHENTICATED'})
            external.write_state(query_http_status=0, mode='query_error')
            failure(await caller.call(token(), 'ontology_join', join(allow_partial=True)), {'SOURCE_UNAVAILABLE'})
        finally:
            external.write_state(query_http_status=0, mode='')
        reverse = join(left={'mapping_id': right['mapping']['id'], 'query': {'limit': 50}},
                       right={'mapping_id': left['mapping']['id'], 'fields': ['business_id', 'business_text'], 'filters': []}, allow_partial=True)
        external.sql(f"REVOKE SELECT ON `{external.schemas['sub-01']}`.* FROM %s@'%%'", (external.users['sub-01'],), server=True)
        try:
            failure(await caller.call(token(), 'ontology_join', reverse), {'FORBIDDEN', 'STALE_MAPPING'})
        finally:
            external.sql(f"GRANT SELECT ON `{external.schemas['sub-01']}`.* TO %s@'%%'", (external.users['sub-01'],), server=True)

    await check('real right HTTP503 permits explicit partial; source HTTP401/403, tool rejection and revoked MySQL SELECT never expose left rows', partial_rules)

    async def held_call(arguments, mutate):
        started = external.events().get('query_started', 0)
        external.write_state(query_hold=True)
        task = asyncio.create_task(caller.call(token(), 'ontology_join', arguments))
        try:
            deadline = time.monotonic() + 8
            while external.events().get('query_started', 0) <= started:
                if task.done():
                    return await task
                if time.monotonic() > deadline:
                    raise AssertionError('Right source did not enter its held real request')
                await asyncio.sleep(.025)
            mutate()
            external.write_state(query_hold=False)
            return await task
        finally:
            external.write_state(query_hold=False)
            if not task.done():
                task.cancel()
                with suppress(BaseException):
                    await task

    async def inflight_authority():
        try:
            failure(await held_call(join(allow_partial=True), lambda: grant(left, False)), {'FORBIDDEN'})
        finally:
            grant(left, True)
        try:
            failure(await held_call(join(allow_partial=True), lambda: app_patch(first, {'mapping_ids': [left['mapping']['id']]})),
                    {'FORBIDDEN', 'UNAUTHENTICATED'})
        finally:
            app_patch(first, {'mapping_ids': all_ids})
        success(await caller.call(token(), 'ontology_join', join()))

    await check('left-source authorization and app mapping scope revoked while right IO runs discard all intermediate rows', inflight_authority)

    async def drift():
        resources = copy.deepcopy(external.state['resources'])
        resources[0]['columns'] = [*resources[0]['columns'], {'name': 'extra_note', 'type': 'varchar(30)', 'nullable': True, 'primary_key': False}]
        external.write_state(resources=resources)
        failure(await caller.call(token(), 'ontology_join', join(allow_partial=True)), {'STALE_MAPPING'})
        diagnostic = success(await caller.call(token(), 'ontology_explain', {'mapping_id': right['mapping']['id']}))
        assert diagnostic['mapping']['locally_current'] is False
        current = builder.ok('POST', '/sources/' + right['source']['id'] + '/discover').json()['resources']
        right['resource'] = next(item for item in current if item['id'] == right['resource']['id'])
        right['mapping'] = publish_mapping(right, existing=True)
        success(await caller.call(token(), 'ontology_join', join()))

    await check('source schema drift is never partial; stale explanation stays metadata-only and manual review restores the same allowed mapping', drift)

    async def process_limits():
        seen = set()
        external.write_state(query_hold=True)
        task = asyncio.create_task(caller.call(token(), 'ontology_query', q(right, timeout_ms=2000)))
        try:
            while not task.done():
                seen.update(application_children(box.process.pid))
                await asyncio.sleep(.025)
            failure(await task, {'TIMEOUT'})
            assert seen, 'No actual subprocess was observed'
            await wait_reaped(box)
        finally:
            external.write_state(query_hold=False)
        count = external.events().get('query_started', 0)
        external.write_state(query_hold=True)
        task = asyncio.create_task(caller.call(token(), 'ontology_query', q(right)))
        try:
            deadline = time.monotonic() + 8
            while external.events().get('query_started', 0) <= count:
                if task.done():
                    failure(await task)
                    raise AssertionError('Disconnected request never entered the source')
                assert time.monotonic() < deadline, 'Source request did not start'
                await asyncio.sleep(.025)
            assert application_children(box.process.pid)
            task.cancel()
            with suppress(BaseException):
                await task
            await wait_reaped(box)
        finally:
            external.write_state(query_hold=False)
            if not task.done():
                task.cancel()
                with suppress(BaseException): await task
        assert external.events().get('caller_token_seen', 0) == 0
        assert external.events().get('authenticated_requests', 0) > 0

    await check('overall deadline and actual HTTP disconnect terminate and reap worker subprocesses; caller bearer never reaches source', process_limits)

    async def credentials():
        life = app_create(admin, builder, [left['mapping']['id']], tools=['ontology_query'])
        with httpx.Client(trust_env=False, timeout=5) as raw:
            def denied(raw_token):
                assert raw.get(box.url + '/mcp/', headers={'Authorization': 'Bearer ' + raw_token}).status_code == 401
            old = token(life)
            rotate(life)
            denied(old)
            success(await caller.call(token(life), 'ontology_query', q(left)))
            current = app_current(life)
            admin.ok('POST', APPS + '/' + current['id'] + '/revoke', json={'expected_version': current['version'], 'reason': 'QA撤销'})
            denied(token(life))
            rotate(life)
            box.sql('UPDATE sec_application_credentials SET expires_at=0 WHERE id=%s', (life['credential']['id'],), box.control)
            denied(token(life))
            rotate(life)
            app_patch(life, {'enabled': False})
            denied(token(life))
            app_patch(life, {'enabled': True})
            rotate(life)
            admin.ok('PATCH', '/admin/users/' + builder.user['id'], json={'role': 'reviewer'})
            denied(token(life))
            rotate(life)
            success(await caller.call(token(life), 'ontology_query', q(left)))
            admin.ok('PATCH', '/admin/users/' + builder.user['id'], json={'is_active': False})
            denied(token(life))
            admin.ok('PATCH', '/admin/users/' + builder.user['id'], json={'is_active': True, 'role': 'modeler'})
            builder.login()
            rotate(first)
            success(await caller.call(token(), 'ontology_query', q(left)))
        listed = admin.ok('GET', APPS).text
        assert not any(value in listed for value in tokens)
        assert 'token_hash' not in listed

    await check('one-time credentials rotate/revoke/expire; app disable and delegated role/account changes invalidate old authority', credentials)

    def no_persistence():
        assert original_business == business_snapshot(), 'MCP mutated generic business tables'
        for database in (box.control, box.tenant_db('sub-01'), box.tenant_db('sub-02')):
            for table in box.sql('SHOW TABLES', database=database):
                name = next(iter(table.values()))
                data = json.dumps(box.sql(f'SELECT * FROM `{name}`', database=database), ensure_ascii=False, default=str)
                assert not any(secret in data for secret in protected()), 'Protected source/filter/caller value persisted in database'
        for path in [*[item for item in box.root.rglob('*') if item.is_file()], box.log_path, external.log_path]:
            data = path.read_bytes()
            assert not any(secret.encode() in data for secret in protected()), 'Protected source/filter/caller value persisted in files/logs'
        assert caller.responses and all(value['no_store'] and value['request_id'] for value in caller.responses)
        assert not application_children(box.process.pid)

    await check('raw rows, filters and caller credentials never persist; generic data is unchanged and MCP responses forbid caching', no_persistence)
    if ui_access and all(item['passed'] for item in checks):
        private_json(ui_access, {'url': box.url, 'tenant_id': 'sub-01',
            'accounts': {name: {'username': actor.username, 'password': actor.password} for name, actor in people['sub-01'].items()},
            'mapping_ids': [left['mapping']['id'], right['mapping']['id']], 'delegate_user_id': builder.user['id'],
            'application_id': first['application']['id'], 'frontend_dist': str(box.root / 'frontend/dist'), 'backend_snapshot_sha256': box.source_sha256})
    return {'result': 'passed' if all(item['passed'] for item in checks) else 'failed', 'checks': checks,
            'backend_snapshot_sha256': box.source_sha256, 'platform_namespace': box.namespace,
            'transport': 'official MCP 2.3 Streamable HTTP caller -> real app bearer -> bounded subprocess -> MySQL and official MCP source',
            'identity': 'Apps created by real password-session administrator; no identity mocks and no caller credential files',
            'retention': 'Reports contain case metadata only; no result/filter/token values',
            'ui_handoff': 'waiting for separate browser verification' if ui_access else 'not requested; UI not verified',
            'cleanup': 'finally removes API/MCP fixture, private registry, platform/source databases and source users'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8898)
    parser.add_argument('--mcp-port', type=int, default=8899)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'work/mcp-report.json')
    parser.add_argument('--ui-access', type=Path)
    parser.add_argument('--ui-release', type=Path)
    args = parser.parse_args()
    if bool(args.ui_access) != bool(args.ui_release):
        parser.error('Browser access and release paths must be supplied together')
    if args.ui_access and (args.ui_access.exists() or args.ui_release.exists()):
        parser.error('Use fresh handoff and release paths')
    box = Sandbox(port=args.port, output=args.output, frontend_dist=Path(__file__).resolve().parents[1] / 'frontend/dist' if args.ui_access else None)
    external = Sources(box, args.mcp_port)
    report = {'result': 'failed'}
    try:
        with external, environment(ONTOLOGY_SOURCE_CREDENTIALS_FILE=str(external.registry), ONTOLOGY_MCP_MAX_CONCURRENT='2'):
            with box:
                report = asyncio.run(run_checks(box, external, args.ui_access))
                if args.ui_access and report['result'] == 'passed':
                    print(json.dumps({'stage': 'ui_handoff_ready', 'access_file': str(args.ui_access),
                                      'backend_snapshot_sha256': box.source_sha256}), flush=True)
                    deadline = time.monotonic() + 900
                    while not args.ui_release.exists():
                        if time.monotonic() > deadline:
                            report['ui_handoff'] = 'timed_out; UI not claimed'
                            break
                        time.sleep(.2)
                    else:
                        report['ui_handoff'] = 'browser released fixture; see separate browser report'
    except Exception as exc:
        report['result'] = 'failed'
        report['fatal_error'] = external.redact(str(exc))[:1000]
    finally:
        if args.ui_access:
            args.ui_access.unlink(missing_ok=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'result': report['result'], 'report': str(args.output)}, ensure_ascii=False))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
