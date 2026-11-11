"""Nine opt-in legacy business-HTTP checks using only synthetic fixtures.

Importing this module performs no setup, credential access or model requests.
The owning controlled-live CLI supplies real authenticated disposable context.
Return values contain fixed checks only; generated text stays in the disposable
workspace. Structural checks never claim to replace a human semantic review.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote


CASE_IDS = ('qa_boundary', 'mapping_units_roles', 'relation_direction', 'project_plan',
            'authority_branches', 'material_reading', 'material_extraction',
            'experience_roundtrip', 'special_synthesis')
CHECK_IDS = frozenset({
    'legacy_real_online_result', 'legacy_citation_scope', 'legacy_independent_mapping_oracle',
    'legacy_mapping_not_applied', 'legacy_relation_direction', 'legacy_relation_not_applied',
    'legacy_task_contract', 'legacy_task_not_created', 'legacy_authority_graph_contract',
    'legacy_authority_not_saved', 'legacy_original_text_coverage', 'legacy_reading_not_applied',
    'legacy_extraction_evidence', 'legacy_background_single_attempt', 'legacy_experience_evidence',
    'legacy_revision_not_applied', 'legacy_special_source_versions', 'legacy_original_conclusions',
    'legacy_semantic_review_required',
})
FAILURE_STAGES = frozenset({'mapping_selected_columns', 'mapping_online_completed', 'mapping_response_columns',
                            'mapping_independent_oracle', 'mapping_not_applied', 'job_queue_update',
                            'job_worker_process', 'job_attempt_count', 'job_completed'})
MAPPING_DIAGNOSTIC_IDS = frozenset(prefix + '_' + field for prefix in ('amount', 'payer', 'receiver')
    for field in ('candidate', 'standard_id', 'standard_version', 'transform', 'source_quote', 'standard_quote'))
JOB_STATUSES = frozenset({'queued', 'running', 'retry', 'failed', 'cancelled', 'completed', 'partial', 'stale', 'pending'})
JOB_REASON_CODES = {
    '模型返回的内容格式不完整': 'RESPONSE_NOT_OBJECT',
    '模型未返回可处理的经验点清单': 'CANDIDATE_LIST_INVALID',
    '经验点格式不完整': 'CANDIDATE_NOT_OBJECT',
    '经验标题或摘要缺失': 'TITLE_OR_SUMMARY_INVALID',
    '原文引用格式不完整': 'REFERENCE_NOT_OBJECT',
    '该经验点的引用尚不能在原文中定位': 'QUOTE_NOT_IN_SOURCE',
    '经验点必须有可核对的原文依据': 'REFERENCE_COUNT_INVALID',
    '章节依据未包含在有效引文中': 'SECTION_REFERENCE_NOT_IN_CANDIDATE',
    '经验正文为空': 'EMPTY_ARTICLE',
    '模型连接或返回暂未完成': 'UPSTREAM_OR_RUNTIME_FAILURE',
}


def safe_job_diagnostic(value):
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in ('queue_status', 'business_status')
              if isinstance(value.get(key), str) and value[key] in JOB_STATUSES}
    result.update({key: value[key] for key in ('attempts', 'max_attempts', 'candidate_count', 'batch_count')
                   if type(value.get(key)) is int and 0 <= value[key] <= 10000})
    result['batches'] = []
    batches = value.get('batches') if isinstance(value.get('batches'), list) else []
    for row in batches[:50]:
        if not isinstance(row, dict):
            continue
        item = {key: row[key] for key in ('status',) if isinstance(row.get(key), str) and row[key] in JOB_STATUSES}
        item.update({key: row[key] for key in ('attempts', 'pages', 'candidate_count', 'error_count')
                     if type(row.get(key)) is int and 0 <= row[key] <= 10000})
        codes = set(JOB_REASON_CODES.values()) | {'UNCLASSIFIED_FAILURE'}
        reason_codes = row.get('reason_codes') if isinstance(row.get('reason_codes'), list) else []
        item['reason_codes'] = [code for code in reason_codes[:50] if isinstance(code, str) and code in codes]
        result['batches'].append(item)
    return result


def source_versions_match(expected, coverage, refs):
    return (bool(expected) and set(coverage) == set(expected) and
            all(isinstance(version, str) and bool(version) and
                coverage[ident].get('source_version') == version for ident, version in expected.items()) and
            all(ref.get('source_id') in expected and
                ref.get('source_version') == expected[ref['source_id']] for ref in refs))


def require(value, stage=None, checks=None):
    if not value:
        error = AssertionError('Fixed legacy acceptance assertion failed')
        if stage in FAILURE_STAGES:
            error.acceptance_stage = stage
        if isinstance(checks, dict):
            error.acceptance_checks = {key: value for key, value in checks.items()
                                       if key in MAPPING_DIAGNOSTIC_IDS and type(value) is bool}
        raise error


class LegacyCases:
    case_ids = CASE_IDS
    check_ids = CHECK_IDS

    def __init__(self, context):
        self.context = context
        self.box, self.builder, self.reviewer = context.box, context.builder, context.reviewer
        self.fixture = json.loads((Path(__file__).resolve().parents[1] /
                                  'tests/fixtures/ai_acceptance_cases.json').read_text(encoding='utf-8'))
        self.assets, self.documents = {}, {}
        self.dataset = self.project = None

    def result(self, *checks):
        return {'status': 'needs_manual_review',
                'checks': [{'check': name, 'status': 'passed'} for name in checks] +
                          [{'check': 'legacy_semantic_review_required', 'status': 'needs_manual_review'}],
                'usage': None}

    def case(self, name):
        return next(copy.deepcopy(case) for case in self.fixture['cases'] if case['id'] == name)

    def seed_assets(self):
        if self.assets:
            return
        for index, row in enumerate(self.fixture['seed_assets']):
            ident = 'LIVE-LEGACY-ASSET-' + str(index + 1)
            body = {key: copy.deepcopy(row[key]) for key in ('kind', 'title', 'domain', 'summary', 'payload')}
            body.update(id=ident, org_id='sub-01')
            self.builder.ok('POST', '/entities', json=body)
            for actor, action in ((self.builder, 'submit'), (self.reviewer, 'approve'), (self.reviewer, 'publish')):
                actor.ok('POST', '/entities/' + ident + '/action',
                         json={'action': action, 'reason': '纯合成验收种子，独立审核并核对范围'})
            self.assets[row['alias']] = self.builder.ok('GET', '/entities/' + ident).json()

    def seed_document(self, alias):
        if alias not in self.documents:
            row = next(row for row in self.fixture['seed_documents'] if row['alias'] == alias)
            value = self.builder.ok('POST', '/materials/upload', data={'org_id': 'sub-01', 'domain': '合同管理'},
                files={'file': (row['file_name'], row['text'].encode('utf-8'), 'text/plain')}).json()
            self.documents[alias] = {'id': value['id'], 'text': row['text']}
        return self.documents[alias]

    def seed_dataset(self):
        self.seed_assets()
        if self.dataset is None:
            row = self.fixture['seed_dataset']
            value = self.builder.ok('POST', '/datasets/upload',
                data={'org_id': 'sub-01', 'title': row['title'], 'header_start': '1', 'header_rows': '1'},
                files={'file': (row['file_name'], row['csv'].encode('utf-8-sig'), 'text/csv')}).json()
            ident = value['entity']['id']
            detail = self.builder.ok('GET', '/datasets/' + ident).json()
            sheet = detail['sheets'][0]['name']
            self.builder.ok('PUT', '/datasets/' + ident + '/sheets/' + quote(sheet, safe='') + '/grain',
                            json={'grain': row['grain'], 'confirmed': True})
            self.dataset = {'id': ident, 'sheet': sheet}
        return self.dataset

    def count_entities(self):
        rows = self.box.sql('SELECT COUNT(*) AS count FROM entities', database=self.box.tenant_db('sub-01'))
        return rows[0]['count']

    def relation_rows(self):
        return self.box.sql('SELECT * FROM relations ORDER BY id', database=self.box.tenant_db('sub-01'))

    def entity_rows(self, kind):
        return self.box.sql('SELECT id,status,version,payload FROM entities WHERE kind=%s ORDER BY id',
                            (kind,), database=self.box.tenant_db('sub-01'))

    def run_job(self, ident):
        # The queue owns identity restoration. Do not invoke the business worker
        # function directly or fabricate a principal. This only schedules this
        # run's existing queued job and suppresses every child diagnostic.
        changed = self.box.sql('UPDATE ontology_task_queue SET max_attempts=1,available_at=UTC_TIMESTAMP(6) WHERE job_id=%s',
                               (ident,), self.box.control)
        require(changed == 1, 'job_queue_update')
        value = subprocess.run([sys.executable, '-c',
            'import asyncio; from backend.jobs import run_one; asyncio.run(run_one())'],
            cwd=self.box.root, env=self.box.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
        require(value.returncode == 0, 'job_worker_process')
        rows = self.box.sql('SELECT attempts,max_attempts,status FROM ontology_task_queue WHERE job_id=%s',
                            (ident,), self.box.control)
        require(len(rows) == 1 and rows[0]['attempts'] == 1 and rows[0]['max_attempts'] == 1, 'job_attempt_count')
        if rows[0]['status'] != 'completed':
            error = AssertionError('Fixed legacy acceptance assertion failed')
            error.acceptance_stage = 'job_completed'
            error.job_diagnostic = self.job_diagnostic(ident, rows[0])
            raise error

    def job_diagnostic(self, ident, queue):
        rows = self.box.sql('SELECT status,result FROM jobs WHERE id=%s', (ident,), self.box.tenant_db('sub-01'))
        value = {'queue_status': queue.get('status'), 'attempts': queue.get('attempts'), 'max_attempts': queue.get('max_attempts')}
        if not rows:
            return safe_job_diagnostic(value)
        state = json.loads(rows[0]['result'])
        value.update(business_status=rows[0]['status'], batch_count=len(state.get('batches', [])),
                     candidate_count=sum(len(row.get('candidate_ids', [])) for row in state.get('batches', [])))
        value['batches'] = [{'status': row.get('status'), 'attempts': row.get('attempts', 0), 'pages': row.get('pages', 0),
                            'candidate_count': len(row.get('candidate_ids', [])), 'error_count': len(row.get('errors', [])),
                            'reason_codes': [JOB_REASON_CODES.get(error.get('reason'), 'UNCLASSIFIED_FAILURE')
                                             for error in row.get('errors', []) if isinstance(error, dict)]}
                           for row in state.get('batches', [])]
        return safe_job_diagnostic(value)

    async def run_case(self, name):
        require(name in CASE_IDS)
        return await getattr(self, name)()

    async def qa_boundary(self):
        self.seed_assets()
        before = self.box.business_snapshot('sub-01')
        value = self.builder.ok('POST', '/ai/ask', json=self.case('qa_boundary')['steps'][0]['json'], timeout=110).json()
        require(value.get('source') == 'online')
        citations = value.get('citations', [])
        require(any(item.get('id') == self.assets['acceptance_rule']['id'] for item in citations))
        # The modeling role can also retrieve its own draft materials. Each
        # citation must resolve through this exact tenant's real login; the
        # required rule itself was independently published during setup.
        for item in citations:
            cited = self.builder.ok('GET', '/entities/' + item['id']).json()
            require(cited.get('owner_tenant_id') == 'sub-01')
        require(all(isinstance(item.get('excerpt'), str) and item['excerpt'].strip() for item in citations))
        for number in re.findall(r'\[(\d+)\]', value.get('answer', '')):
            require(1 <= int(number) <= len(citations))
        require(self.box.business_snapshot('sub-01') == before)
        return self.result('legacy_real_online_result', 'legacy_citation_scope')

    async def mapping_units_roles(self):
        dataset = self.seed_dataset()
        detail = self.builder.ok('GET', '/datasets/' + dataset['id']).json()
        wanted = self.fixture['seed_dataset']['mapping_columns']
        selected = [row['column_name'] for row in detail['mappings']
                    if row.get('payload', {}).get('source_column', row['column_name']) in wanted]
        require(len(selected) == 3, 'mapping_selected_columns')
        before = copy.deepcopy(detail['mappings'])
        value = self.builder.ok('POST', '/datasets/' + dataset['id'] + '/mapping-recognize',
            json={'sheet_name': dataset['sheet'], 'column_names': selected}, timeout=220).json()
        require(value.get('mode') == 'online' and value.get('state') == 'completed', 'mapping_online_completed')
        rows = value.get('items', [])
        require(len(rows) == 3 and {row['column_name'] for row in rows} == set(selected), 'mapping_response_columns')
        expected = {'合同金额（万元）': ('contract_amount_yuan', 'scale10000'),
                    '付款方账号': ('payer_account', 'identity'), '收款方账号': ('receiver_account', 'identity')}
        diagnostics = {}
        for row in rows:
            source = row['source_column']
            alias, transform = expected[source]
            standard = self.assets[alias]
            prefix = {'contract_amount_yuan': 'amount', 'payer_account': 'payer', 'receiver_account': 'receiver'}[alias]
            diagnostics.update({prefix + '_candidate': row.get('outcome') == 'candidate' and row.get('can_apply') is True,
                prefix + '_standard_id': row.get('standard_id') == standard['id'],
                prefix + '_standard_version': row.get('standard_version') == standard['version'],
                prefix + '_transform': row.get('transform') == transform,
                prefix + '_source_quote': row.get('source_quote') == source,
                prefix + '_standard_quote': bool(row.get('standard_quote')) and row['standard_quote'] in standard['payload']['definition']})
        require(all(diagnostics.values()), 'mapping_independent_oracle', diagnostics)
        after = self.builder.ok('GET', '/datasets/' + dataset['id']).json()['mappings']
        require(after == before, 'mapping_not_applied')
        return self.result('legacy_real_online_result', 'legacy_independent_mapping_oracle', 'legacy_mapping_not_applied')

    async def relation_direction(self):
        self.seed_assets()
        source, target = self.assets['contract_amount_yuan'], self.assets['amount_rule']
        before = self.relation_rows()
        body = self.case('relation_direction')['steps'][0]['json']
        body['expected_version'] = source['version']
        value = self.builder.ok('POST', '/placement/' + source['id'] + '/suggest', json=body, timeout=110).json()
        require(value.get('mode') == 'online')
        require(any(row.get('target_id') == target['id'] and row.get('direction') == 'incoming' and
                    row.get('relation_type') == 'uses_data' for row in value.get('suggestions', [])))
        candidates = {row['id']: row for row in value.get('candidates', [])}
        from backend.placement import allowed
        for row in value['suggestions']:
            candidate = candidates.get(row['target_id'])
            require(candidate is not None and row['direction'] in ('incoming', 'outgoing'))
            start, end = (source, candidate) if row['direction'] == 'outgoing' else (candidate, source)
            require(allowed(start, end, row['relation_type']))
            require(10 <= len(row['evidence']) <= 240 and row['evidence'] in candidate['excerpt'])
        require(self.relation_rows() == before)
        return self.result('legacy_real_online_result', 'legacy_relation_direction', 'legacy_relation_not_applied')

    async def project_plan(self):
        if self.project is None:
            row = self.fixture['seed_project']
            self.project = self.builder.ok('POST', '/entities', json={key: copy.deepcopy(row[key])
                for key in ('title', 'kind', 'status', 'payload')} | {'id': 'LIVE-LEGACY-PLANNING', 'org_id': 'sub-01'}).json()
            for task in row['existing_tasks']:
                self.builder.ok('POST', '/projects/' + self.project['id'] + '/tasks', json=task)
        self.project = self.builder.ok('GET', '/entities/' + self.project['id']).json()
        before = self.entity_rows('task')
        value = self.builder.ok('POST', '/projects/' + self.project['id'] + '/plan',
            json=self.case('project_plan')['steps'][0]['json'], timeout=110).json()
        require(value.get('mode') == 'online' and value.get('requires_review') is True)
        require(value.get('project_version') == self.project['version'])
        from backend.projects import TYPE_MAP
        require(1 <= len(value.get('tasks', [])) <= 6)
        require(all(row.get('task_type') in TYPE_MAP and row.get('title') and row.get('instruction') for row in value['tasks']))
        require(self.entity_rows('task') == before)
        return self.result('legacy_real_online_result', 'legacy_task_contract', 'legacy_task_not_created')

    async def authority_branches(self):
        body = self.case('authority_branches')['steps'][0]['json']
        body['org_ids'] = ['sub-01']
        before = self.count_entities()
        value = self.builder.ok('POST', '/authority/assist', json=body, timeout=110).json()
        require(value.get('mode') == 'online')
        from backend.authority import Spec, checks_for
        spec = Spec.model_validate(value['spec']).model_dump()
        require(not checks_for(spec)['errors'])
        require(spec.get('person_ids', []) == [])
        require(self.count_entities() == before)
        return self.result('legacy_real_online_result', 'legacy_authority_graph_contract', 'legacy_authority_not_saved')

    async def material_reading(self):
        document = self.seed_document('material_rules')
        path = '/materials/' + document['id'] + '/reading'
        before = self.builder.ok('GET', path).json()
        body = self.case('material_reading')['steps'][1]['json']
        body['revision'] = before['revision']
        value = self.builder.ok('POST', path + '/ai', json=body, timeout=110).json()
        # The first read is deliberately non-persistent; the subsequent POST
        # may assign fresh opaque segment IDs while preserving the same draft.
        # Compare business content and boundaries, not those transient IDs.
        def stable_segments(rows):
            return [{key: row.get(key) for key in ('start', 'end', 'heading', 'locked_after', 'content')}
                    for row in rows]
        require(value['pending_ai'].get('mode') == 'online' and
                stable_segments(value['segments']) == stable_segments(before['segments']))
        proposed = copy.deepcopy(before['segments'])
        for group in value['pending_ai'].get('groups', []):
            proposed = [part for part in proposed if not (group['start'] <= part['start'] and part['end'] <= group['end'])]
            proposed.extend(group['after'])
        proposed.sort(key=lambda part: part['start'])
        position = 0
        for part in proposed:
            require(part['start'] == position and part['end'] > position)
            position = part['end']
        require(position == len(document['text']))
        require(''.join(document['text'][part['start']:part['end']] for part in proposed) == document['text'])
        return self.result('legacy_real_online_result', 'legacy_original_text_coverage', 'legacy_reading_not_applied')

    def confirm_reading(self, document):
        path = '/materials/' + document['id'] + '/reading'
        reading = self.builder.ok('GET', path).json()
        if reading['pending_ai'].get('groups'):
            reading = self.builder.ok('POST', path + '/discard-suggestions', json={
                'revision': reading['revision'], 'proposal_id': reading['pending_ai']['id']}).json()
        self.builder.ok('POST', path + '/confirm', json={'revision': reading['revision'], 'reason': '合成验收只确认保留原文的现有分段'})

    async def material_extraction(self):
        document = self.seed_document('material_rules')
        self.confirm_reading(document)
        base = '/materials/' + document['id']
        job = self.builder.ok('POST', base + '/extract', json={'mode': 'online', 'retry': False}).json()
        self.run_job(job['id'])
        state = self.builder.ok('GET', '/jobs/' + job['id']).json()
        require(state['status'] == 'completed' and state['result'].get('failed_chunks') == 0)
        detail = self.builder.ok('GET', base).json()
        chunks = {row['id']: row for row in self.builder.ok('GET', base + '/chunks').json()['items']}
        payload = detail['entity']['payload'] if 'entity' in detail else detail['payload']
        candidates = payload.get('candidates', [])
        require(bool(candidates) and set(payload.get('coverage', {})) == set(chunks))
        for row in candidates:
            require(row.get('status') == 'pending' and set(row['source_chunk_ids']) <= set(chunks))
            quoted = set()
            for ref in row.get('source_quotes', []):
                require(ref['chunk_id'] in chunks and ref['quote'] and ref['quote'] in chunks[ref['chunk_id']]['content'])
                quoted.add(ref['chunk_id'])
            require(set(row['source_chunk_ids']) <= quoted)
        return self.result('legacy_real_online_result', 'legacy_extraction_evidence', 'legacy_background_single_attempt')

    async def experience_roundtrip(self):
        case = self.case('experience_roundtrip')
        body = case['steps'][0]['json']
        body['org_id'] = 'sub-01'
        value = self.builder.ok('POST', '/experience/sessions', json=body, timeout=110).json()
        ident = value['entity']['id']
        require(value['entity']['payload']['turns'][-1]['mode'] == 'online')
        job = self.builder.ok('POST', '/experience/' + ident + '/analyze', json={'retry_failed': False}).json()
        self.run_job(job['run_id'])
        value = self.builder.ok('GET', '/experience/' + ident).json()
        payload = value['entity']['payload']
        require(payload['analysis']['status'] == 'completed')
        candidates = payload.get('knowledge_candidates', [])
        require(len(candidates) >= 2)
        available = {row['id']: row for row in value['chunks']}
        from backend.experience import validate_candidate
        for row in candidates:
            validate_candidate(row, available)
            require(row.get('status') == 'pending' and not row.get('adopted_entity_id'))
        # Identify the amount candidate by its actual cited source, not by an
        # invented ID. Selection does not substitute for semantic review.
        amount = next((row for row in candidates if any('万元' in ref.get('quote', '') for ref in row.get('source_refs', []))), None)
        require(amount is not None)
        before = copy.deepcopy(candidates)
        body = case['steps'][3]['json']
        body['candidate_ids'] = [amount['id']]
        body['expected_revisions'] = {amount['id']: amount.get('revision', 1)}
        proposal = self.builder.ok('POST', '/experience/' + ident + '/proposals', json=body, timeout=110).json()['proposal']
        require(proposal['status'] == 'pending' and len(proposal['outputs']) == 1)
        for row in proposal['outputs']:
            validate_candidate(row, available)
        after = self.builder.ok('GET', '/experience/' + ident).json()['entity']['payload']['knowledge_candidates']
        require(after == before)
        return self.result('legacy_real_online_result', 'legacy_experience_evidence',
                           'legacy_revision_not_applied', 'legacy_background_single_attempt')

    async def special_synthesis(self):
        original = '交付晚于初始计划，延期原因及批准情况待核'
        value = self.builder.ok('POST', '/special-projects', json={
            'title': '纯合成事项A专项', 'scope': '仅两份合成原文与一个事项',
            'subject_org_ids': ['sub-01'], 'original_work_conclusion': original}).json()
        ident = value['project']['id']
        matter = self.builder.ok('POST', '/special-projects/' + ident + '/matters', json={
            'title': '纯合成事项A', 'description': '纯合成交付与验收日期核对',
            'original_conclusion': original, 'subject_org_ids': ['sub-01']}).json()['entity']
        documents = [self.seed_document(alias) for alias in ('special_report', 'special_explanation')]
        mids = [document['id'] for document in documents]
        self.builder.ok('POST', '/special-projects/' + ident + '/materials', json={
            'material_ids': mids, 'matter_ids': [matter['id']],
            'registration': {'category': 'other', 'disposition': 'pending'}})
        # Snapshot authoritative source versions before the first model call.
        # Neither coverage nor a generated candidate may define its own oracle.
        expected_versions = {}
        for document in documents:
            source = self.builder.ok('GET', '/materials/' + document['id'] + '/source').json()
            require(source['text'] == document['text'])
            expected_versions[document['id']] = source['source_version']
        job = self.builder.ok('POST', '/special-projects/' + ident + '/refinements', json={
            'matter_ids': [matter['id']], 'material_ids': mids}).json()
        self.run_job(job['id'])
        workspace = self.builder.ok('GET', '/special-projects/' + ident + '/workspace').json()
        state = next(row for row in workspace['jobs'] if row['id'] == job['id'])
        require(state['status'] == 'completed')
        coverage = state['result']['coverage']
        require(set(coverage) == set(mids) and all(row.get('status') == 'processed' for row in coverage.values()))
        require(source_versions_match(expected_versions, coverage, []))
        candidates = [row for row in workspace['candidates'] if row.get('refinement_id') == job['id']]
        require(bool(candidates))
        texts = {row['id']: row['text'] for row in documents}
        for row in candidates:
            require(row.get('status') == 'pending' and row.get('source_refs'))
            require(source_versions_match(expected_versions, coverage, row['source_refs']))
            for ref in row['source_refs']:
                require(ref['source_id'] in mids and ref.get('source_version') and ref.get('anchor_id'))
                span = ref['span']
                require(texts[ref['source_id']][span['start']:span['end']] == ref['quote'])
        require(workspace['project']['payload']['original_work_conclusion'] == original)
        require(next(row for row in workspace['matters'] if row['id'] == matter['id'])['payload']['original_conclusion'] == original)
        require(not workspace['outputs'])
        return self.result('legacy_real_online_result', 'legacy_special_source_versions',
                           'legacy_original_conclusions', 'legacy_background_single_attempt')
