"""Real 0.3 source lifecycle regression using disposable MySQL and MCP sources.

MYSQL_* must point to a dedicated test server with CREATE DATABASE/USER rights.
Platform data, source schemas/users, credential registry and MCP state are all
unique to this run and removed in finally. Reports contain case metadata only.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
import pymysql
from pymysql.cursors import DictCursor
from check_security import Sandbox, Session, expect


@contextmanager
def environment(**values):
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def private_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.chmod(0o600)
    temporary.replace(path)


class Sources:
    def __init__(self, box, port=8890):
        self.box, self.port = box, port
        self.ident = uuid.uuid4().hex[:10]
        self.schemas = {tenant: 'ontology_source_qa_' + self.ident + suffix for tenant, suffix in [('sub-01', '_a'), ('sub-02', '_b')]}
        self.users = {tenant: 'sourceqa_' + self.ident + suffix for tenant, suffix in [('sub-01', 'a'), ('sub-02', 'b')]}
        self.passwords = {tenant: secrets.token_urlsafe(28) for tenant in self.schemas}
        self.markers = {tenant: 'ROW_PRIVATE_' + uuid.uuid4().hex for tenant in self.schemas}
        self.mcp_marker = 'MCP_PRIVATE_' + uuid.uuid4().hex
        self.filter_marker = 'FILTER_PRIVATE_' + uuid.uuid4().hex + "' OR 1=1 --"
        self.token = secrets.token_urlsafe(32)
        self.process, self.log = None, None
        self.created = False

    def sql(self, statement, args=(), tenant='sub-01', server=False):
        settings = dict(self.box.settings)
        if not server:
            settings['database'] = self.schemas[tenant]
        with pymysql.connect(**settings, charset='utf8mb4', cursorclass=DictCursor, autocommit=True) as connection, connection.cursor() as cursor:
            cursor.execute(statement, args)
            return list(cursor.fetchall()) if cursor.description else cursor.rowcount

    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ontology-source-fixtures-')
        self.root = Path(self.temp.name)
        self.registry = self.root / 'credentials.json'
        self.state_path = self.root / 'mcp-state.json'
        self.events_path = self.state_path.with_suffix('.events.json')
        try:
            self.created = True
            for tenant, schema in self.schemas.items():
                self.sql(f'CREATE DATABASE `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci', server=True)
                self.sql('CREATE TABLE contracts(id BIGINT PRIMARY KEY,amount DECIMAL(12,2) NOT NULL,status VARCHAR(40) NOT NULL,secret TEXT NOT NULL)', tenant=tenant)
                self.sql('INSERT INTO contracts VALUES(1,12.50,\'active\',%s),(2,20.00,\'inactive\',%s)', (self.markers[tenant], self.markers[tenant]), tenant)
                self.sql("CREATE VIEW active_contracts AS SELECT id,amount,status,secret FROM contracts WHERE status='active'", tenant=tenant)
                self.sql("CREATE USER %s@'%%' IDENTIFIED BY %s", (self.users[tenant], self.passwords[tenant]), server=True)
                self.sql(f"GRANT SELECT, SHOW VIEW ON `{schema}`.* TO %s@'%%'", (self.users[tenant],), server=True)
            columns = [{'name': name, 'type': typ, 'nullable': False, 'primary_key': name == 'id'} for name, typ in
                       [('id', 'bigint'), ('amount', 'decimal(12,2)'), ('status', 'varchar(40)'), ('secret', 'text')]]
            resources = [{'key': key, 'name': key, 'kind': kind, 'columns': columns, 'primary_key': ['id'],
                          **({'definition_version': 'fixed-active-v1', 'definition': "SELECT id,amount,status,secret FROM contracts WHERE status='active'"} if kind == 'saved_query' else {})}
                         for key, kind in [('contracts_table', 'table'), ('active_contracts_view', 'view'), ('active_contracts_query', 'saved_query')]]
            self.state = {'token': self.token, 'resources': resources, 'rows': [
                {'id': 1, 'amount': '12.50', 'status': 'active', 'secret': self.mcp_marker},
                {'id': 2, 'amount': '20.00', 'status': 'inactive', 'secret': self.mcp_marker}]}
            private_json(self.state_path, self.state)
            self.registry_data = {'refs': {}}
            for tenant in self.schemas:
                self.registry_data['refs']['mysql-' + tenant] = {
                    'tenant_id': tenant, 'kind': 'mysql', 'host': self.box.settings['host'], 'port': self.box.settings['port'],
                    'database': self.schemas[tenant], 'username': self.users[tenant], 'password': self.passwords[tenant], 'allow_insecure': True}
            self.registry_data['refs']['mcp-sub-01'] = {'tenant_id': 'sub-01', 'kind': 'mcp', 'endpoint': self.endpoint,
                                                       'token': self.token, 'allow_insecure': True}
            private_json(self.registry, self.registry_data)
            self.start()
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    @property
    def endpoint(self):
        return f'http://127.0.0.1:{self.port}/mcp'

    @property
    def sensitive(self):
        return [*self.passwords.values(), *self.markers.values(), self.token, self.mcp_marker, self.filter_marker]

    def redact(self, value):
        for secret in self.sensitive:
            value = value.replace(secret, '<redacted-test-value>')
        return value

    def write_state(self, **changes):
        self.state.update(changes)
        private_json(self.state_path, self.state)

    def events(self):
        return json.loads(self.events_path.read_text()) if self.events_path.exists() else {}

    def start(self):
        self.log_path = self.root / 'mcp.log'
        self.log = self.log_path.open('a')
        self.process = subprocess.Popen([sys.executable, str(self.box.source / 'tests/fixtures/source_mcp_server.py'),
                                        '--port', str(self.port), '--state-file', str(self.state_path)],
                                       stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 15
        with httpx.Client(trust_env=False, timeout=1) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('MCP fixture failed to start; private fixture log retained until cleanup')
                try:
                    if client.get(self.endpoint).status_code == 401:
                        return
                except httpx.RequestError:
                    pass
                time.sleep(.1)
        raise RuntimeError('MCP fixture startup timed out')

    def stop(self):
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        if self.log:
            self.log.close()
        self.process = None

    def __exit__(self, *exc):
        self.stop()
        try:
            if self.created:
                for tenant, schema in self.schemas.items():
                    self.sql(f'DROP DATABASE IF EXISTS `{schema}`', server=True)
                    self.sql("DROP USER IF EXISTS %s@'%%'", (self.users[tenant],), server=True)
        finally:
            if getattr(self, 'temp', None):
                self.temp.cleanup()


def run_checks(box, external, ui_access=None):
    checks, records = [], {}

    def check(name, operation, needs_sources=True):
        if needs_sources and set(records) != {'mysql', 'mcp'}:
            result = {'name': name, 'passed': False, 'skipped': 'source setup did not finish'}
            checks.append(result)
            print(json.dumps(result), flush=True)
            return
        began = time.monotonic()
        try:
            operation()
            result = {'name': name, 'passed': True}
        except Exception as exc:
            result = {'name': name, 'passed': False, 'error': external.redact(str(exc))[:1200]}
        result['seconds'] = round(time.monotonic() - began, 3)
        checks.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    superadmin = Session(box, box.admin_name, box.admin_password)
    superadmin.login()
    admins, builders, reviewers = {}, {}, {}
    for tenant in ('sub-01', 'sub-02'):
        admins[tenant] = box.user(superadmin, tenant, 'admin')
        builders[tenant] = box.user(superadmin, tenant, 'modeler')
        reviewers[tenant] = box.user(superadmin, tenant, 'reviewer')
        for ident, title in [('SOURCE-ID', 'QA业务编号'), ('SOURCE-AMOUNT', 'QA合同金额'), ('SOURCE-TEXT', 'QA业务文本')]:
            builders[tenant].create(ident, title, payload={'definition': 'QA经过独立审核的字段口径', 'unit': '无量纲'})
            for actor, action in ((builders[tenant], 'submit'), (reviewers[tenant], 'approve'), (reviewers[tenant], 'publish')):
                actor.ok('POST', '/entities/' + ident + '/action', json={'action': action, 'reason': 'QA独立复核业务标准'})
    admin, builder, reviewer = admins['sub-01'], builders['sub-01'], reviewers['sub-01']

    def grant(source, actor, discover=True, query=True):
        return admin.ok('PUT', f"/sources/{source['id']}/grants", json={'user_id': actor.user['id'], 'discover': discover, 'query': query}).json()

    def definition(resource):
        return {'resource_id': resource['id'], 'title': 'QA显式业务映射', 'business_meaning': '合同记录及独立审核的业务口径',
                'grain': '每条记录代表一份合同', 'object_key': ['id'], 'relation_keys': [], 'fields': [
                    {'column': 'id', 'field': 'business_id', 'asset_id': 'SOURCE-ID', 'meaning': '合同唯一编号', 'unit': '无量纲'},
                    {'column': 'amount', 'field': 'contract_amount', 'asset_id': 'SOURCE-AMOUNT', 'meaning': '合同金额', 'unit': '元'},
                    {'column': 'secret', 'field': 'business_text', 'asset_id': 'SOURCE-TEXT', 'meaning': '合同业务说明', 'unit': '无量纲'}]}

    def current(source, mapping):
        detail = builder.ok('GET', '/sources/' + source['id']).json()
        return next(item for item in detail['mappings'] if item['id'] == mapping['id'])

    def action(source, mapping, name, actor):
        return actor.ok('POST', f"/sources/{source['id']}/mappings/{mapping['id']}/action", json={
            'action': name, 'expected_version': mapping['version'], 'reason': 'QA人工复核结构、粒度和目标标准版本'}).json()

    def publish(source, resource, existing=None):
        if existing:
            mapping = current(source, existing)
            if mapping['status'] != 'draft':
                mapping = action(source, mapping, 'revise', builder)
            mapping = builder.ok('PUT', f"/sources/{source['id']}/mappings/{mapping['id']}", json={**definition(resource), 'expected_version': mapping['version']}).json()
        else:
            mapping = builder.ok('POST', f"/sources/{source['id']}/mappings", json=definition(resource)).json()
        expect(builder.request('POST', f"/sources/{source['id']}/mappings/{mapping['id']}/query", json={}), 409)
        mapping = action(source, mapping, 'submit', builder)
        expect(builder.request('POST', f"/sources/{source['id']}/mappings/{mapping['id']}/action", json={
            'action': 'publish', 'expected_version': mapping['version'], 'reason': 'QA禁止建模者自审'}), 403)
        return action(source, mapping, 'publish', reviewer)

    def query(source, mapping, actor=builder, body=None, status=200):
        response = actor.request('POST', f"/sources/{source['id']}/mappings/{mapping['id']}/query", json=body or {'limit': 20})
        expect(response, status)
        result = response.json()
        if status == 200:
            assert result['retention'] == {'result': 'response_only', 'cache': 'disabled', 'audit': 'metadata_only'}
            assert result['columns'] == ['business_id', 'contract_amount', 'business_text']
        else:
            assert 'rows' not in result
        return result

    def create_and_discover():
        with pymysql.connect(host=box.settings['host'], port=box.settings['port'], user=external.users['sub-01'],
                             password=external.passwords['sub-01'], database=external.schemas['sub-01']) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM contracts')
            assert cursor.fetchone()[0] == 2
            try:
                cursor.execute('UPDATE contracts SET amount=0 WHERE id=1')
            except pymysql.MySQLError as exc:
                assert exc.args[0] == 1142
            else:
                raise AssertionError('Source account unexpectedly allows writes')
        mysql_config = {'host': box.settings['host'], 'port': box.settings['port'], 'database': external.schemas['sub-01'],
                        'allowed_objects': ['contracts', 'active_contracts']}
        configs = {'mysql': mysql_config, 'mcp': {'endpoint': external.endpoint, 'allowed_objects': [item['key'] for item in external.state['resources']]}}
        for kind, config in configs.items():
            body = {'title': 'QA外部源 ' + kind, 'kind': kind, 'config': config, 'credential_ref': kind + '-sub-01'}
            expect(builder.request('POST', '/sources', json=body), 403)
            source = admin.ok('POST', '/sources', json=body).json()
            expect(builder.request('POST', '/sources/' + source['id'] + '/discover'), 403)
            grant(source, builder)
            grant(source, reviewer)
            discovered = builder.ok('POST', '/sources/' + source['id'] + '/discover').json()
            assert {item['kind'] for item in discovered['resources']} == ({'table', 'view'} if kind == 'mysql' else {'table', 'view', 'saved_query'})
            assert all('rows' not in item for item in discovered['resources'])
            resource = next(item for item in discovered['resources'] if item['kind'] == ('table' if kind == 'mysql' else 'saved_query'))
            records[kind] = {'source': source, 'resource': resource, 'mapping': publish(source, resource)}
            result = query(source, records[kind]['mapping'])
            expected = external.markers['sub-01'] if kind == 'mysql' else external.mcp_marker
            assert result['rows'] and all(row['business_text'] == expected for row in result['rows'])
            assert len(result['rows']) == (2 if kind == 'mysql' else 1)
            projected = builder.ok('POST', f"/sources/{source['id']}/mappings/{records[kind]['mapping']['id']}/query",
                                   json={'fields': ['business_id'], 'limit': 1}).json()
            assert projected['columns'] == ['business_id'] and all(set(row) == {'business_id'} for row in projected['rows'])
            assert projected['truncated'] is (kind == 'mysql')
            for other in discovered['resources']:
                if other['id'] == resource['id']:
                    continue
                extra_mapping = publish(source, other)
                extra_result = query(source, extra_mapping)
                assert len(extra_result['rows']) == (2 if other['kind'] == 'table' else 1)
                if kind == 'mysql':
                    records[kind].update(view_resource=other, view_mapping=extra_mapping)
            public = builder.ok('GET', '/sources').json()['items']
            assert all('config' not in item and 'credential_ref' not in item for item in public)
        assert external.events().get('discover', 0) >= 2 and external.events().get('query_finished', 0) >= 1

    check('real SELECT-only MySQL table/view and SDK MCP table/view/saved-query discovery; separate review and query', create_and_discover, needs_sources=False)

    def review_and_versions():
        source, resource = records['mysql']['source'], records['mysql']['resource']
        own = admin.ok('POST', f"/sources/{source['id']}/mappings", json=definition(resource)).json()
        own = action(source, own, 'submit', admin)
        expect(admin.request('POST', f"/sources/{source['id']}/mappings/{own['id']}/action", json={
            'action': 'publish', 'expected_version': own['version'], 'reason': 'QA管理员也不能自审'}), 403)
        action(source, own, 'reject', reviewer)
        mapping = records['mysql']['mapping']
        expect(builder.request('PUT', f"/sources/{source['id']}/mappings/{mapping['id']}", json={**definition(resource), 'expected_version': 1}), 409)
        versions = builder.ok('GET', f"/sources/{source['id']}/mappings/{mapping['id']}/versions").json()['items']
        assert len(versions) >= 3 and len({item['version'] for item in versions}) == len(versions)
        lineage = builder.ok('GET', '/sources/lineage/SOURCE-ID').json()
        assert mapping['id'] in json.dumps(lineage)

    check('administrator cannot self-review; optimistic versions and metadata lineage remain explicit', review_and_versions)

    def fresh_values_and_injection():
        external.sql('UPDATE contracts SET amount=87.65 WHERE id=1')
        external.state['rows'][0]['amount'] = '87.65'
        external.write_state()
        for item in records.values():
            result = query(item['source'], item['mapping'], body={'filters': [{'field': 'business_id', 'op': 'eq', 'value': 1}], 'limit': 20})
            assert result['rows'][0]['contract_amount'] == '87.65'
            result = query(item['source'], item['mapping'], body={'filters': [{'field': 'business_text', 'op': 'eq', 'value': external.filter_marker}]})
            assert result['rows'] == []
            query(item['source'], item['mapping'], body={'sql': 'SELECT * FROM contracts'}, status=422)
            query(item['source'], item['mapping'], body={'fields': ['business_id;DROP TABLE contracts']}, status=422)
            query(item['source'], item['mapping'], body={'limit': 201}, status=422)
        assert external.sql('SELECT COUNT(*) AS n FROM contracts')[0]['n'] == 2

    check('live source row updates, parameterized filters, arbitrary SQL and unbounded result requests rejected', fresh_values_and_injection)

    def drift():
        external.sql('ALTER TABLE contracts ADD COLUMN extra_note VARCHAR(30) NULL')
        external.state['resources'][2]['definition_version'] = 'fixed-active-v2'
        external.write_state()
        for item in records.values():
            source, mapping = item['source'], item['mapping']
            query(source, mapping, status=409)
            assert current(source, mapping)['status'] == 'stale'
            discovered = builder.ok('POST', '/sources/' + source['id'] + '/discover').json()['resources']
            resource = next(value for value in discovered if value['id'] == item['resource']['id'])
            versions = builder.ok('GET', f"/sources/{source['id']}/resources/{resource['id']}/versions").json()['items']
            assert len(versions) >= 2
            item['resource'], item['mapping'] = resource, publish(source, resource, mapping)
            query(source, item['mapping'])

    check('MySQL schema and MCP fixed-query definition drift block reads until manual rediscovery and review', drift)

    def view_definition_drift():
        item = records['mysql']
        source, mapping = item['source'], item['view_mapping']
        external.sql("ALTER VIEW active_contracts AS SELECT id,amount,status,secret FROM contracts WHERE status='inactive'")
        query(source, mapping, status=409)
        assert current(source, mapping)['status'] == 'stale'
        resources = builder.ok('POST', '/sources/' + source['id'] + '/discover').json()['resources']
        resource = next(value for value in resources if value['id'] == item['view_resource']['id'])
        item['view_mapping'] = publish(source, resource, mapping)
        result = query(source, item['view_mapping'])
        assert [row['business_id'] for row in result['rows']] == [2]
        assert current(source, item['mapping'])['status'] == 'published'

    check('view definition-only changes invalidate its mapping without invalidating the unchanged table', view_definition_drift)

    def target_change():
        builder.ok('POST', '/entities/SOURCE-AMOUNT/action', json={'action': 'revise', 'reason': 'QA标准版本修订'})
        for item in records.values():
            query(item['source'], item['mapping'], status=409)
        for actor, name in ((builder, 'submit'), (reviewer, 'approve'), (reviewer, 'publish')):
            actor.ok('POST', '/entities/SOURCE-AMOUNT/action', json={'action': name, 'reason': 'QA复核新版标准'})
        for item in records.values():
            item['mapping'] = publish(item['source'], item['resource'], item['mapping'])
            query(item['source'], item['mapping'])

    check('changed target standard invalidates both published mappings until explicit new-version review', target_change)

    def tenant_and_access():
        for item in records.values():
            source, mapping = item['source'], item['mapping']
            expect(builders['sub-02'].request('GET', '/sources/' + source['id']), 404)
            expect(builders['sub-02'].request('POST', f"/sources/{source['id']}/mappings/{mapping['id']}/query", json={}), 404)
            grant(source, builder, discover=True, query=False)
            query(source, mapping, status=403)
            grant(source, builder)
        foreign = admins['sub-02'].ok('POST', '/sources', json={'title': 'QA拒绝借用其他租户凭据', 'kind': 'mysql',
             'config': records['mysql']['source']['config'], 'credential_ref': 'mysql-sub-01'}).json()
        expect(admins['sub-02'].request('POST', '/sources/' + foreign['id'] + '/discover'), {403, 422})
        assert admins['sub-02'].ok('GET', '/sources/' + foreign['id']).json()['resources'] == []
        other_admin, other_builder, other_reviewer = admins['sub-02'], builders['sub-02'], reviewers['sub-02']
        other = other_admin.ok('POST', '/sources', json={'title': 'QA机构B独立源', 'kind': 'mysql',
            'config': {**records['mysql']['source']['config'], 'database': external.schemas['sub-02']},
            'credential_ref': 'mysql-sub-02'}).json()
        for actor in (other_builder, other_reviewer):
            other_admin.ok('PUT', f"/sources/{other['id']}/grants", json={'user_id': actor.user['id'], 'discover': True, 'query': True})
        other_resources = other_builder.ok('POST', '/sources/' + other['id'] + '/discover').json()['resources']
        other_resource = next(item for item in other_resources if item['kind'] == 'table')
        other_mapping = other_builder.ok('POST', f"/sources/{other['id']}/mappings", json=definition(other_resource)).json()
        other_mapping = action(other, other_mapping, 'submit', other_builder)
        other_mapping = action(other, other_mapping, 'publish', other_reviewer)
        other_rows = query(other, other_mapping, actor=other_builder)['rows']
        assert other_rows and all(row['business_text'] == external.markers['sub-02'] for row in other_rows)
        own = records['mysql']
        assert all(row['business_text'] == external.markers['sub-01'] for row in query(own['source'], own['mapping'])['rows'])
        before = len(admin.ok('GET', '/sources').json()['items'])
        invalid = {**records['mysql']['source']['config'], 'password': external.filter_marker}
        expect(admin.request('POST', '/sources', json={'title': 'QA禁止凭据内嵌', 'kind': 'mysql', 'config': invalid, 'credential_ref': 'mysql-sub-01'}), 422)
        assert len(admin.ok('GET', '/sources').json()['items']) == before

    check('cross-tenant IDs and credential borrowing denied; grant revocation immediate; API config cannot carry secrets', tenant_and_access)

    def inflight(change, expected):
        item = records['mcp']
        count = external.events().get('query_started', 0)
        external.write_state(query_hold=True)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(builder.request, 'POST', f"/sources/{item['source']['id']}/mappings/{item['mapping']['id']}/query", json={})
                deadline = time.monotonic() + 4
                while external.events().get('query_started', 0) <= count:
                    if future.done():
                        expect(future.result())
                        raise AssertionError('Query did not enter the remote fixture')
                    if time.monotonic() > deadline:
                        raise AssertionError('Remote query start was not observed')
                    time.sleep(.025)
                change(item)
                external.write_state(query_hold=False)
                response = future.result(timeout=15)
                expect(response, expected)
                assert 'rows' not in response.json()
                assert external.mcp_marker not in response.text
        finally:
            external.write_state(query_hold=False)

    def in_flight_controls():
        item = records['mcp']
        inflight(lambda value: grant(value['source'], builder, True, False), 403)
        grant(item['source'], builder)
        def disable(value):
            source = admin.ok('GET', '/sources/' + value['source']['id']).json()['source']
            admin.ok('PATCH', '/sources/' + source['id'], json={'enabled': False, 'expected_version': source['version']})
        inflight(disable, {403, 409})
        source = admin.ok('GET', '/sources/' + item['source']['id']).json()['source']
        query(source, item['mapping'], status={403, 409})
        admin.ok('PATCH', '/sources/' + source['id'], json={'enabled': True, 'expected_version': source['version']})
        item['mapping'] = publish(source, item['resource'], item['mapping'])
        query(source, item['mapping'])
        def replace_mapping(value):
            value['mapping'] = publish(value['source'], value['resource'], value['mapping'])
        inflight(replace_mapping, 409)
        assert current(source, item['mapping'])['status'] == 'published'
        query(source, item['mapping'])

    check('remote IO cannot return rows after grant revocation, source disable or replacement by a new mapping revision', in_flight_controls)

    def credential_caps_errors():
        item = records['mcp']
        saved = external.registry_data['refs'].pop('mcp-sub-01')
        private_json(external.registry, external.registry_data)
        try:
            query(item['source'], item['mapping'], status={403, 422, 502})
        finally:
            external.registry_data['refs']['mcp-sub-01'] = saved
            private_json(external.registry, external.registry_data)
        for mode in ('wrong_contract', 'oversized_metadata', 'oversized_cell', 'tool_error'):
            external.write_state(mode=mode)
            query(item['source'], item['mapping'], status=502)
        external.write_state(mode='', query_delay=6)
        try:
            query(item['source'], item['mapping'], status=504)
        finally:
            external.write_state(query_delay=0, mode='')
        query(item['source'], item['mapping'])
        external.stop()
        external.write_state(schema_mode='incompatible')
        external.start()
        try:
            query(item['source'], item['mapping'], status=502)
        finally:
            external.stop()
            external.write_state(schema_mode='')
            external.start()
        query(item['source'], item['mapping'])

    check('revoked deployment credentials, incompatible protocol, metadata/cell caps, upstream errors and timeout fail closed', credential_caps_errors)

    def non_persistence():
        needles = [value.encode() for value in external.sensitive]
        databases = [box.control, box.tenant_db('sub-01'), box.tenant_db('sub-02')]
        for database in databases:
            for table in box.sql('SHOW TABLES', database=database):
                name = next(iter(table.values()))
                rows = box.sql(f'SELECT * FROM `{name}`', database=database)
                encoded = json.dumps(rows, ensure_ascii=False, default=str).encode()
                assert not any(needle in encoded for needle in needles), 'Protected external value was persisted in platform database'
        paths = [path for path in box.root.rglob('*') if path.is_file()]
        paths.extend([box.log_path, external.log_path])
        for path in paths:
            content = path.read_bytes()
            assert not any(needle in content for needle in needles), 'Protected external value was persisted in platform files/logs'
        assert not any((box.storage / 'tenants').glob('*/source-results/*'))
        assert len(builder.ok('GET', '/entities?kind=data').json()['items']) == 3

    check('external rows, filter values and deployment secrets never persist in platform databases, storage or logs', non_persistence)
    if ui_access and all(item['passed'] for item in checks):
        item = records['mcp']
        private_json(ui_access, {'url': box.url, 'accounts': {
            key: {'username': actor.username, 'password': actor.password}
            for key, actor in [('admin', admin), ('modeler', builder), ('reviewer', reviewer)]},
            'source_id': item['source']['id'], 'resource_id': item['resource']['id'],
            'mapping_id': item['mapping']['id'], 'mcp_source_id': item['source']['id'],
            'mysql_source_id': records['mysql']['source']['id'], 'mysql_resource_id': records['mysql']['resource']['id'],
            'mysql_mapping_id': records['mysql']['mapping']['id'],
            'backend_snapshot_sha256': box.source_sha256,
            'standard_ids': ['SOURCE-ID', 'SOURCE-AMOUNT', 'SOURCE-TEXT']})
    return {'result': 'passed' if all(item['passed'] for item in checks) else 'failed', 'checks': checks,
            'transport': 'real HTTP password sessions and CSRF; real MySQL SELECT-only users and official MCP SDK Streamable HTTP',
            'backend_snapshot_sha256': box.source_sha256, 'platform_namespace': box.namespace,
            'retention': 'Only test-case metadata is reported; source rows and secrets excluded',
            'ui_handoff': 'waiting for separate browser verification' if ui_access else 'not requested; browser UI not verified by this script',
            'cleanup': 'finally removes test API, MCP process, platform/source databases, source users and private registry/state'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8888)
    parser.add_argument('--mcp-port', type=int, default=8890)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'work' / 'source-report.json')
    parser.add_argument('--ui-access', type=Path, help='Optional private mode-600 browser handoff file')
    parser.add_argument('--ui-release', type=Path, help='Browser completion signal; required with --ui-access')
    args = parser.parse_args()
    report = {'result': 'failed'}
    if bool(args.ui_access) != bool(args.ui_release):
        parser.error('--ui-access and --ui-release must be supplied together')
    if args.ui_access and (args.ui_access.exists() or args.ui_release.exists()):
        parser.error('Use fresh browser handoff and release paths')
    box = Sandbox(port=args.port, output=args.output, frontend_dist=Path(__file__).resolve().parents[1] / 'frontend/dist' if args.ui_access else None)
    external = Sources(box, args.mcp_port)
    try:
        with external, environment(ONTOLOGY_SOURCE_CREDENTIALS_FILE=str(external.registry)):
            with box:
                report = run_checks(box, external, args.ui_access)
                if args.ui_access and report['result'] == 'passed':
                    print(json.dumps({'stage': 'ui_handoff_ready', 'access_file': str(args.ui_access), 'release_file': str(args.ui_release), 'backend_snapshot_sha256': box.source_sha256}), flush=True)
                    deadline = time.monotonic() + 900
                    while not args.ui_release.exists():
                        if time.monotonic() > deadline:
                            report['ui_handoff'] = 'timed_out; browser completion not claimed'
                            break
                        time.sleep(.2)
                    else:
                        report['ui_handoff'] = 'browser released fixture; see separate browser report'
    except Exception as exc:
        report['result'] = 'failed'
        report['fatal_error'] = external.redact(str(exc))[:1500]
    finally:
        if args.ui_access:
            args.ui_access.unlink(missing_ok=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'result': report['result'], 'report': str(args.output)}, ensure_ascii=False))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
