"""Opt-in real DeepSeek checks of controlled task and agent entry points.

Uses the pinned official Harness, real password/CSRF sessions, disposable MySQL
tenants, an actual synthetic MCP source and independently published skills. No
authorization or tool execution is mocked. This is a Linux QA orchestrator;
native Windows and upgrade acceptance remain in check_windows_ci.py.

DEEPSEEK_API_KEY is consumed and removed before any child starts, or read by
non-echoing getpass. Never supply a key as an argument. Only fixed check names,
statuses, durations and numeric provider usage enter the report. All private
fixture files and logs use writable tmpfs and are removed on exit. Each case
runs once, serially; the first failure or blocker stops the suite. Existing
90-second/8-step/8192-token limits are not a monetary spending cap.

Requires the project's pinned dependencies, a dedicated MYSQL_* server with
CREATE/DROP DATABASE/USER rights, approved api.deepseek.com access and, for the
readonly_skill case, ONTOLOGY_SKILL_RUNNER_SOCKET pointing to the fixed isolated
runner. No script is ever executed as an ordinary process on the QA host.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import getpass
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import traceback
import warnings

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.controlled_live_legacy import CASE_IDS as LEGACY_CASE_IDS, CHECK_IDS as LEGACY_CHECK_IDS, LegacyCases, FAILURE_STAGES, MAPPING_DIAGNOSTIC_IDS, safe_job_diagnostic

CORE_CASE_IDS = ('legacy_ai', 'agent_multiturn', 'authorized_mcp', 'readonly_skill')
CASE_IDS = CORE_CASE_IDS + LEGACY_CASE_IDS
CHECK_IDS = frozenset({
    'online_legacy_result', 'published_citation', 'strict_rule_boundaries',
    'no_automatic_business_write', 'ontology_tool_used', 'second_turn_memory',
    'session_owned_by_login', 'authorized_mcp_tool_used', 'independent_source_oracle',
    'current_source_requeried', 'source_receipt_present', 'revoked_source_rejected',
    'foreign_source_rejected', 'independent_skill_test_and_review',
    'published_skill_loaded_and_executed', 'independent_skill_output',
    'withdrawn_skill_rejected', 'isolated_runner_required', 'credential_log_absence',
    'source_and_session_retention',
}) | LEGACY_CHECK_IDS
CHECK_STATUSES = {'passed', 'failed', 'needs_manual_review', 'blocked'}
COUNTS = ('model_responses', 'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens')
LIMITS = {'turn_timeout_seconds': 90, 'max_model_steps': 8, 'max_tokens_per_model_request': 8192,
          'automatic_retries': 0, 'enforced_currency_budget': False}
PREFLIGHT_STAGE = 'not_started'
DIAGNOSTIC_FILES = {'check_controlled_live.py', 'controlled_live_legacy.py', 'check_security.py',
                    'check_sources.py', 'check_mcp.py', 'check_skills.py', 'check_agent_runtime.py'}


def safe_diagnostic(error):
    value = {}
    name = type(error).__name__
    value['failure_class'] = name if name in {'HTTPException', 'AssertionError', 'ValueError', 'OSError',
        'PermissionError', 'OperationalError', 'InternalError', 'RuntimeError', 'TimeoutError',
        'ModuleNotFoundError', 'JSONDecodeError', 'FileNotFoundError', 'KeyError', 'TypeError'} else 'OtherError'
    for frame in reversed(traceback.extract_tb(error.__traceback__)):
        name = Path(frame.filename).name
        if name in DIAGNOSTIC_FILES and frame.name not in {'require', 'checked'}:
            value.update(file=name, line=frame.lineno)
            break
    if getattr(error, 'acceptance_stage', None) in FAILURE_STAGES:
        value['acceptance_stage'] = error.acceptance_stage
    checks = getattr(error, 'acceptance_checks', None)
    if isinstance(checks, dict):
        value['acceptance_checks'] = {key: result for key, result in checks.items()
                                      if key in MAPPING_DIAGNOSTIC_IDS and type(result) is bool}
    if isinstance(getattr(error, 'job_diagnostic', None), dict):
        value['job'] = safe_job_diagnostic(error.job_diagnostic)
    return value


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, '参数无效；使用 --help。密钥只能通过环境注入或隐藏输入提供。\n')


def read_key():
    key = os.environ.pop('DEEPSEEK_API_KEY', None)
    if key is None:
        if not sys.stdin.isatty():
            raise ValueError()
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            key = getpass.getpass('DeepSeek API key（隐藏输入，不保存到报告）: ')
    if not isinstance(key, str) or not 8 <= len(key) <= 4096 or any(not 33 <= ord(c) <= 126 for c in key):
        raise ValueError()
    return key


def safe_usage(value):
    unknown = {'status': 'unknown', 'reason': 'not_reported_may_have_incurred_cost'}
    if not isinstance(value, dict) or value.get('complete') is not True:
        return unknown
    if any(type(value.get(k)) is not int or not 0 <= value[k] <= 10**12 for k in COUNTS):
        return unknown
    return {'status': 'reported', 'complete': True, **{k: value[k] for k in COUNTS}}


def sum_usage(values):
    safe = [safe_usage(value) for value in values]
    if not safe or any(value['status'] != 'reported' for value in safe):
        return None
    return {'complete': True, **{k: sum(value[k] for value in safe) for k in COUNTS}}


async def run_selected(selected, context, results=None):
    if results is None:
        results = []
    for name in selected:
        if name not in CASE_IDS:
            raise ValueError()
        began = time.monotonic()
        row = {'case': name, 'status': 'failed', 'checks': [], 'usage': safe_usage(None)}
        try:
            if name == 'readonly_skill' and context.runner_available is not True:
                row.update(status='blocked', checks=[{'check': 'isolated_runner_required', 'status': 'blocked'}])
            else:
                result = await context.run_case(name)
                if not isinstance(result, dict) or result.get('status') not in CHECK_STATUSES:
                    raise ValueError()
                row['status'] = result['status']
                row['checks'] = [{'check': check['check'], 'status': check['status']}
                    for check in result.get('checks', []) if isinstance(check, dict)
                    and check.get('check') in CHECK_IDS and check.get('status') in CHECK_STATUSES]
                row['usage'] = safe_usage(result.get('usage'))
        except asyncio.CancelledError:
            row.update(status='cancelled', error_code='LIVE_CANCELLED')
            raise
        except Exception as error:
            row.update(status='failed', error_code='LIVE_CASE_FAILED', usage=safe_usage(getattr(error, 'usage', None)))
            row['diagnostic'] = safe_diagnostic(error)
        finally:
            row['duration_ms'] = round((time.monotonic() - began) * 1000)
            results.append(row)
        if row['status'] in {'failed', 'blocked'}:
            break
    return results


def require(condition):
    if not condition:
        raise AssertionError('Fixed acceptance assertion failed')


def json_answer(answer):
    """The JSON must be the whole answer; prose is not a semantic oracle."""
    require(isinstance(answer, str) and len(answer.encode()) <= 128 * 1024)
    answer = answer.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', answer, re.DOTALL)
    return json.loads(fenced[1] if fenced else answer)


def strict_booleans(value, expected):
    return (isinstance(value, dict) and set(value) == set(expected) and
            all(type(value[key]) is bool and value[key] is expected[key] for key in expected))


class LiveContext:
    def __init__(self, box, external, broker, people, records, key):
        self.box, self.external, self.broker = box, external, broker
        self.people, self.records, self.key = people, records, key
        self.admin, self.builder, self.reviewer = (people['sub-01'][role] for role in ('admin', 'modeler', 'reviewer'))
        self.foreign = people['sub-02']['modeler']
        self.owned = []
        self.legacy = None
        from backend.skill_execution import runner_status
        self.runner_available = runner_status()['available'] is True

    def session(self):
        value = self.builder.ok('POST', '/agent/sessions', json={}).json()
        self.owned.append(value['id'])
        return value['id']

    async def turn(self, session, message, *, mappings=(), skills=(), entities=()):
        base = '/agent/sessions/' + session
        run = self.builder.ok('POST', base + '/turns', json={
            'message': message, 'mapping_ids': list(mappings), 'skill_ids': list(skills), 'entity_ids': list(entities)}).json()['run_id']
        deadline = time.monotonic() + 105
        while time.monotonic() < deadline:
            response = self.builder.ok('GET', base + '/runs/' + run)
            require('no-store' in response.headers.get('cache-control', ''))
            value = response.json()
            if value['status'] not in {'running', 'queued', 'cancelling'}:
                require(value['status'] == 'completed')
                require(self.key not in json.dumps(value, ensure_ascii=False))
                return value
            await asyncio.sleep(.1)
        self.builder.request('POST', base + '/runs/' + run + '/cancel')
        raise TimeoutError()

    def tools(self, value, expected):
        actual = {item['name'] for item in value['tools'] if item['status'] == 'ok'}
        require(set(expected) <= actual)

    def no_writes(self, before):
        require(self.box.business_snapshot('sub-01') == before)

    async def legacy_ai(self):
        # A real existing business HTTP endpoint, rather than invoking only the
        # low-level task adapter. Boundary meaning uses an independent oracle.
        rule_id = 'LIVE-SYNTHETIC-ACCEPTANCE-RULE'
        self.builder.create(rule_id, '合成采购验收登记规则', kind='rule', payload={
            'content': '【纯合成测试制度】仅适用于2026年1月1日起新签、人民币合同金额严格超过100000元的采购合同。'
                       '金额恰为100000元不适用。缺少验收附件仅为待核，不能据此认定未验收或违规。',
            'active_asset': True})
        for actor, action in ((self.builder, 'submit'), (self.reviewer, 'approve'), (self.reviewer, 'publish')):
            actor.ok('POST', '/entities/' + rule_id + '/action', json={'action': action, 'reason': '合成验收独立审核'})
        before = self.box.business_snapshot('sub-01')
        value = self.builder.ok('POST', '/ai/ask', json={
            'question': '依据合成采购验收登记规则，2026年2月新签、金额恰好100000元的人民币合同，未见验收附件。'
                        '请先简述依据并引用出处，并在答复末尾独立一行给出JSON对象，字段仅为 '
                        'rule_applies（本条是否适用）、missing_attachment_proves_failure（仅缺附件能否认定未验收），均为布尔值。',
            'mode': 'online'}, timeout=110).json()
        require(value.get('source') == 'online')
        citations = value.get('citations', [])
        require(any(c.get('id') == rule_id and '100000' in c.get('excerpt', '') for c in citations))
        answer = value.get('answer', '')
        require(self.key not in answer)
        # Parse just the explicitly requested, closed boolean contract. This
        # deliberately does not claim broad natural-language semantic review.
        matches = re.findall(r'\{[^{}]*"rule_applies"[^{}]*\}', answer)
        require(len(matches) == 1)
        require(strict_booleans(json.loads(matches[0]), {'rule_applies': False, 'missing_attachment_proves_failure': False}))
        numbers = re.findall(r'\[(\d+)\]', answer)
        require(numbers)
        for number in numbers:
            require(1 <= int(number) <= len(citations))
        require(any(citations[int(number) - 1].get('id') == rule_id for number in numbers))
        self.no_writes(before)
        return self.passed('online_legacy_result', 'published_citation', 'strict_rule_boundaries', 'no_automatic_business_write')

    async def agent_multiturn(self):
        before = self.box.business_snapshot('sub-01')
        session = self.session()
        response = self.foreign.request('GET', '/agent/sessions/' + session)
        require(response.status_code == 404)
        first = await self.turn(session,
            '必须先调用ontology_read工具读取已发布本体 MCP-AMOUNT 的定义。记住本轮合成标签 MULTITURN-SYNTHETIC-A 和整数37。'
            '仅返回JSON：{"asset_id":"MCP-AMOUNT","unit":"读取到的unit"}。', entities=['MCP-AMOUNT'])
        self.tools(first, ['ontology_read'])
        require(json_answer(first['answer']) == {'asset_id': 'MCP-AMOUNT', 'unit': '无量纲'})
        second = await self.turn(session,
            '仅根据上一轮用户给出的标签与整数，返回JSON，字段 previous_tag 为上一轮标签、sum 为上一轮整数加5；不要猜测业务数值。',
            entities=['MCP-AMOUNT'])
        require(json_answer(second['answer']) == {'previous_tag': 'MULTITURN-SYNTHETIC-A', 'sum': 42})
        detail = self.builder.ok('GET', '/agent/sessions/' + session).json()
        require(len(detail['messages']) == 4 and len(detail['turns']) == 2)
        self.no_writes(before)
        return self.passed('ontology_tool_used', 'second_turn_memory', 'session_owned_by_login',
                           'no_automatic_business_write', usage=sum_usage([first.get('usage'), second.get('usage')]))

    async def authorized_mcp(self):
        from backend.agent_tools import SessionToolBridge
        from backend.security.store import authenticate_session
        mapping = self.records['sub-01']['mcp']['mapping']
        source = self.records['sub-01']['mcp']['source']
        session = self.session()
        prompt = ('只查询本次明确选择的MCP映射 ' + mapping['id'] + '，使用ontology_query读取 business_id、contract_amount 两列全部记录，limit=50，'
                  '不要读取business_text。计算行数及金额合计，严格只返回JSON对象，字段 row_count 为整数、amount_sum 为保留两位小数的字符串。'
                  '不得把此小样本称为全机构全量。')
        before = self.box.business_snapshot('sub-01')
        observed = self.external.events().get('query_started', 0)
        first = await self.turn(session, prompt, mappings=[mapping['id']])
        self.tools(first, ['ontology_query'])
        require(json_answer(first['answer']) == {'row_count': 2, 'amount_sum': '32.50'})
        require(self.external.events().get('query_started', 0) > observed)
        require(any(p.get('source_id') == source['id'] for p in first.get('provenance', [])))
        changed = [dict(row) for row in self.external.state['rows']]
        changed[0]['amount'] = '99.25'
        self.external.write_state(rows=changed)
        observed = self.external.events().get('query_started', 0)
        second = await self.turn(session, '源数据已经改变，请重新查询当前源端，按上一轮相同JSON格式返回当前行数与金额合计。', mappings=[mapping['id']])
        self.tools(second, ['ontology_query'])
        require(json_answer(second['answer']) == {'row_count': 2, 'amount_sum': '119.25'})
        require(self.external.events().get('query_started', 0) > observed)
        foreign_principal = authenticate_session(self.foreign.client.cookies.get('ontology_session'), 'live-foreign')[0]
        denied = SessionToolBridge(foreign_principal, [mapping['id']], []).invoke_local('ontology_query', {
            'mapping_id': mapping['id'], 'query': {'fields': ['business_id'], 'limit': 50}})
        require(denied.get('status') == 'error' and denied.get('data') is None)
        self.no_writes(before)
        self.admin.ok('PUT', '/sources/' + source['id'] + '/grants', json={
            'user_id': self.builder.user['id'], 'discover': True, 'query': False})
        try:
            denied = self.builder.request('GET', '/agent/sessions/' + session)
            require(denied.status_code in {401, 403, 409} and 'answer' not in denied.json())
        finally:
            self.admin.ok('PUT', '/sources/' + source['id'] + '/grants', json={
                'user_id': self.builder.user['id'], 'discover': True, 'query': True})
        return self.passed('authorized_mcp_tool_used', 'independent_source_oracle', 'current_source_requeried',
                           'source_receipt_present', 'revoked_source_rejected', 'foreign_source_rejected',
                           'no_automatic_business_write', usage=sum_usage([first.get('usage'), second.get('usage')]))

    async def readonly_skill(self):
        from check_skills import skill, save, manifest, validate_and_test, action, ident, SCRIPT
        detail = skill(self.builder, 'controlled-live-plus-one')
        detail = save(self.builder, detail, files={**detail['files'], 'scripts/main.py': SCRIPT}, profile=manifest(scripted=True))
        validate_and_test(self.builder, detail, 'isolated_python')
        detail = action(self.builder, detail, 'submit')
        detail = action(self.reviewer, detail, 'publish')
        skill_id = ident(detail)
        before = self.box.business_snapshot('sub-01')
        session = self.session()
        result = await self.turn(session,
            '加载已选用的已发布技能 ' + skill_id + ' 的说明，再通过skill_execute工具以输入 {"value":37} 真实执行。'
            '不要自行心算替代执行。只返回技能的output JSON。', skills=[skill_id])
        self.tools(result, ['skill_load', 'skill_execute'])
        require(json_answer(result['answer']) == {'result': 38})
        self.no_writes(before)
        action(self.builder, detail, 'revise')
        denied = self.builder.request('GET', '/agent/sessions/' + session)
        require(denied.status_code in {401, 403, 409} and 'answer' not in denied.json())
        return self.passed('independent_skill_test_and_review', 'published_skill_loaded_and_executed',
                           'independent_skill_output', 'withdrawn_skill_rejected', 'no_automatic_business_write', usage=result.get('usage'))

    @staticmethod
    def passed(*checks, usage=None):
        return {'status': 'passed', 'checks': [{'check': name, 'status': 'passed'} for name in checks], 'usage': usage}

    async def run_case(self, name):
        start = self.box.sql('SELECT COALESCE(MAX(id),0) AS n FROM sec_audit', database=self.box.control)[0]['n']
        try:
            if name in LEGACY_CASE_IDS:
                if self.legacy is None:
                    self.legacy = LegacyCases(self)
                result = await self.legacy.run_case(name)
            else:
                result = await getattr(self, name)()
            self.assert_retention()
            result['checks'] += [{'check': name, 'status': 'passed'} for name in ('credential_log_absence', 'source_and_session_retention')]
            result['usage'] = self.usage_since(start)
            return result
        except Exception as error:
            # Numeric usage is recovered only from the platform's fixed audit
            # contract; provider exception text never crosses this boundary.
            error.usage = self.usage_since(start)
            raise

    def usage_since(self, start):
        rows = self.box.sql('SELECT action,details FROM sec_audit WHERE id>%s AND tenant_id=%s '
                            'AND action IN (%s,%s,%s,%s,%s,%s) ORDER BY id',
                            (start, 'sub-01', 'harness.task.finished', 'harness.agent.finished',
                             'harness.task.failed', 'harness.agent.failed', 'harness.task.started', 'harness.agent.started'), self.box.control)
        finished = [json.loads(row['details']).get('usage') for row in rows if row['action'].endswith('.finished')]
        starts = sum(row['action'].endswith('.started') for row in rows)
        if starts != len(finished) or any(row['action'].endswith('.failed') for row in rows):
            return None
        return sum_usage(finished)

    def assert_retention(self):
        protected = [self.key, *self.external.sensitive,
                     *[actor.client.cookies.get('ontology_session') for actor in (self.builder, self.foreign)]]
        protected = [value for value in protected if value]
        for database in (self.box.control, self.box.tenant_db('sub-01'), self.box.tenant_db('sub-02')):
            for table in self.box.sql('SHOW TABLES', database=database):
                name = next(iter(table.values()))
                value = json.dumps(self.box.sql('SELECT * FROM `' + name + '`', database=database), ensure_ascii=False, default=str)
                require(not any(secret in value for secret in protected))
        for path in [self.box.log_path, self.broker.log_path, self.external.log_path]:
            require(not any(secret.encode() in path.read_bytes() for secret in protected))
        for path in self.box.root.rglob('*'):
            if not path.is_file():
                continue
            # One deliberately private tenant configuration contains the key.
            # Source data, caller cookies and source secrets must be absent even
            # there; all other platform files must also exclude the model key.
            excluded = protected[1:] if path.name == 'harness.json' else protected
            require(not any(secret.encode() in path.read_bytes() for secret in excluded))

    def close(self):
        for ident in self.owned:
            self.builder.request('DELETE', '/agent/sessions/' + ident)
        self.key = None


@contextmanager
def setup_context(key, args):
    global PREFLIGHT_STAGE
    # All heavy imports are intentionally after CLI parsing and credential pop.
    # Existing fixture modules use POSIX process groups and /proc, so fail
    # explicitly on other hosts rather than claiming native Windows coverage.
    PREFLIGHT_STAGE = 'dependency_import'
    if sys.platform != 'linux':
        raise ValueError()
    from backend import skill_harness
    from check_security import Sandbox
    from check_sources import Sources, environment
    from check_mcp import prepare
    from check_agent_runtime import Broker
    PREFLIGHT_STAGE = 'runtime_validation'
    skill_harness._runtime()
    root = skill_harness._work_root()
    logging.getLogger('httpx').setLevel(logging.CRITICAL)
    previous_temp = tempfile.tempdir
    PREFLIGHT_STAGE = 'private_workspace'
    with tempfile.TemporaryDirectory(prefix='ontology-controlled-live-', dir=root) as temporary:
        private = Path(temporary)
        try:
            tempfile.tempdir = temporary
            with environment(TMPDIR=temporary, TEMP=temporary, TMP=temporary,
                             ONTOLOGY_AGENT_SERVICE_URL='http://127.0.0.1:' + str(args.broker_port)):
                box = Sandbox(port=args.port, output=private / 'fixture-status.json')
                external = Sources(box, args.source_port)
                PREFLIGHT_STAGE = 'source_fixture'
                with external, environment(ONTOLOGY_SOURCE_CREDENTIALS_FILE=str(external.registry)):
                    PREFLIGHT_STAGE = 'platform_start'
                    with box:
                      PREFLIGHT_STAGE = 'broker_start'
                      with Broker(box, args.broker_port) as broker:
                        settings = {'MYSQL_' + k.upper(): str(v) for k, v in box.settings.items()}
                        with environment(**settings, MYSQL_DATABASE=box.namespace, ONTOLOGY_STORAGE_ROOT=str(box.storage)):
                            PREFLIGHT_STAGE = 'identity_fixture'
                            people, records = prepare(box, external)
                            PREFLIGHT_STAGE = 'runner_probe'
                            context = LiveContext(box, external, broker, people, records, key)
                            # No probe or extra model call. The first chosen
                            # case is the first actual provider request.
                            PREFLIGHT_STAGE = 'model_configuration'
                            context.admin.ok('POST', '/skills/harness/config', json={
                                'base_url': skill_harness.DEFAULT_BASE_URL, 'model': skill_harness.DEFAULT_MODEL, 'api_key': key})
                            try:
                                PREFLIGHT_STAGE = 'ready'
                                yield context
                            finally:
                                context.close()
        finally:
            tempfile.tempdir = previous_temp


def main(argv=None):
    global PREFLIGHT_STAGE
    parser = SafeParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--list-cases', action='store_true')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--case', choices=CASE_IDS)
    selection.add_argument('--suite', choices=('small', 'legacy', 'full'), default='small')
    parser.add_argument('--port', type=int, default=8930)
    parser.add_argument('--source-port', type=int, default=8931)
    parser.add_argument('--broker-port', type=int, default=8933)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.list_cases:
        print(json.dumps({'cases': list(CASE_IDS), 'default': list(CORE_CASE_IDS), 'legacy': list(LEGACY_CASE_IDS), 'full': list(CASE_IDS),
                          'skill_authoring_cli': 'scripts/check_harness_live.py',
                          'model': 'real DeepSeek via pinned official Harness', 'limits': LIMITS}, ensure_ascii=False))
        return 0
    selected = [args.case] if args.case else list(CASE_IDS if args.suite == 'full' else LEGACY_CASE_IDS if args.suite == 'legacy' else CORE_CASE_IDS)
    output = args.output or ROOT / 'work' / ('controlled-live-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '.json')
    report = {'purpose': 'synthetic_small_sample_real_provider_acceptance', 'model': 'real DeepSeek via pinned official Harness',
              'selected_cases': selected, 'limits': LIMITS, 'cases': [],
              'scope': 'listed small-sample oracles only; not enterprise data or all natural-language semantics'}
    key = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w') as destination:
            try:
                PREFLIGHT_STAGE = 'credential_read'
                key = read_key()
                with setup_context(key, args) as context:
                    snapshot = context.box.source_sha256
                    require(isinstance(snapshot, str) and re.fullmatch(r'[0-9a-f]{64}', snapshot))
                    report['backend_snapshot_sha256'] = snapshot
                    asyncio.run(run_selected(selected, context, report['cases']))
                statuses = [row['status'] for row in report['cases']]
                report['status'] = next((status for status in ('failed', 'blocked', 'needs_manual_review') if status in statuses), 'passed')
            except KeyboardInterrupt:
                report['status'] = 'cancelled'
            except Exception as error:
                report.update(status='blocked' if not report['cases'] else 'failed', error_code='LOCAL_PREFLIGHT_OR_CREDENTIAL_ERROR')
                report.update(preflight_stage=PREFLIGHT_STAGE, diagnostic=safe_diagnostic(error))
            finally:
                key = None
                report['not_run_cases'] = [name for name in selected if name not in {row['case'] for row in report['cases']}]
                destination.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(report, ensure_ascii=False))
        return {'passed': 0, 'failed': 1, 'blocked': 2, 'needs_manual_review': 2, 'cancelled': 130}[report['status']]
    except Exception:
        print(json.dumps({'status': 'not_started', 'error_code': 'REPORT_PATH_UNAVAILABLE'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
