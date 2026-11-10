"""Skill governance boundaries: authority, stale proofs, schemas and retention."""
import asyncio
import copy
import json
from contextlib import ExitStack
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from backend import skills as s
from backend.security.context import Principal, use_context

P = Principal(user_id='editor', username='editor', home_tenant_id='sub-01', tenant_id='sub-01', role='admin',
              is_superadmin=False, session_id='session', auth_version=1, database_name='unit_test')
FILES = {'SKILL.md': '---\nname: test-skill\ndescription: Unit test skill\n---\n\n# Test skill\n\nUse the declared steps.\n'}
MANIFEST = {'schema_version': '1.0', 'applicability': '合成测试场景', 'inputs': {'type': 'object'}, 'outputs': {'type': 'object'},
            'steps': [{'title': '核对', 'instruction': '按声明口径核对合成输入。'}], 'entrypoint': None,
            'test_cases': [{'name': '合成样例', 'input': {}, 'expected_output': {}}], 'dependencies': [], 'tool_dependencies': []}


class Connection:
    def __init__(self):
        self.statements = []
        self.result = Mock(fetchone=Mock(return_value=None), fetchall=Mock(return_value=[]))
    def execute(self, statement, values=()):
        self.statements.append((statement, values))
        return self.result
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): pass


class Governance(unittest.TestCase):
    def test_strict_identity_and_control_fields(self):
        for model, body in [(s.CreateBody, {'title': 'test', 'package_name': 'test', 'tenant_id': 'other'}),
                            (s.ActionBody, {'expected_version': 1, 'action': 'publish', 'reason': 'reason', 'reviewed_by': 'fake'}),
                            (s.VersionBody, {'expected_version': True}),
                            (s.AssistBody, {'expected_version': 1, 'instruction': 'test', 'plugin_path': '/tmp/evil'})]:
            with self.subTest(model=model.__name__), self.assertRaises(ValidationError): model.model_validate(body)

    def test_schema_profile_blocks_resolvers_regex_and_complexity(self):
        for value in [{'type': 'string', 'pattern': '(a+)+$'}, {'$ref': 'https://remote/schema'},
                      {'type': 'object', '$defs': {}}, {'type': 'object', 'additionalProperties': {'type': 'string'}},
                      {'type': 'array'}, {'type': 'string', 'maxLength': 1000000},
                      {'type': 'object', 'required': ['missing']}, {'type': 'number', 'minimum': float('nan')},
                      {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 20, 'maxItems': 10}]:
            with self.subTest(schema=value), self.assertRaises(ValueError): s.validate_schema(value)
        nested = {'type': 'string'}
        for _ in range(15): nested = {'type': 'array', 'items': nested}
        with self.assertRaises(ValueError): s.validate_schema(nested)
        s.validate_schema({'type': 'object', 'properties': {'count': {'type': 'integer', 'minimum': 0, 'maximum': 10}}, 'required': ['count'], 'additionalProperties': False})

    def test_manifest_caps_and_execution_profile(self):
        for patch_value in [{'entrypoint': '../main.py'}, {'entrypoint': 'scripts/a/../../main.py'},
                            {'entrypoint': 'scripts/main.sh'}, {'test_cases': MANIFEST['test_cases'] * 11},
                            {'dependencies': [{'entity_id': 'D1', 'kind': 'data', 'version': True}]},
                            {'tool_dependencies': [{'name': 'shell', 'mapping_ids': [], 'purpose': 'run'}]},
                            {'tool_dependencies': [{'name': 'ontology_query', 'mapping_ids': [], 'purpose': 'query'}]},
                            {'test_cases': [{'name': 'x', 'input': float('inf'), 'expected_output': None}]}]:
            with self.subTest(patch=patch_value), self.assertRaises(ValidationError): s.Manifest.model_validate({**MANIFEST, **patch_value})

    def test_compatible_manifest_does_not_export_authority(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest['dependencies'] = [{'entity_id': 'private-id', 'version': 2, 'kind': 'data'}]
        manifest['tool_dependencies'] = [{'name': 'ontology_query', 'mapping_ids': ['private-map'], 'purpose': 'query'}]
        files, package = s._coherent_files(FILES, manifest)
        self.assertTrue(package['valid'])
        self.assertNotIn('private-id', files[s.MANIFEST_PATH])
        self.assertNotIn('private-map', files[s.MANIFEST_PATH])
        self.assertNotIn('dependencies', json.loads(files[s.MANIFEST_PATH]))
        self.assertIn('ontology-one.manifest', files['SKILL.md'])

    def test_draft_can_save_incomplete_script_but_cannot_validate(self):
        manifest = {**MANIFEST, 'entrypoint': 'scripts/main.py'}
        files, package = s._coherent_files({**FILES, 'scripts/main.py': 'def invalid(\n'}, manifest)
        self.assertFalse(package['valid'])
        report = s._validation(files, manifest)
        self.assertFalse(report['valid'])
        self.assertIn('PYTHON_SYNTAX', [error['code'] for error in report['errors']])

    def test_examples_are_schema_checked_without_echo(self):
        manifest = {**MANIFEST, 'inputs': {'type': 'integer'}, 'test_cases': [{'name': 'synthetic', 'input': 'NEVER_ECHO_VALUE', 'expected_output': {}}]}
        files, _ = s._coherent_files(FILES, manifest)
        report = s._validation(files, manifest)
        self.assertFalse(report['valid'])
        self.assertNotIn('NEVER_ECHO_VALUE', json.dumps(report))
        self.assertIn('example_schema_mismatch', [item['code'] for item in report['errors']])

    def test_manifest_completion_required(self):
        files, _ = s._coherent_files(FILES, s.Manifest().model_dump())
        report = s._validation(files, s.Manifest().model_dump())
        self.assertEqual({item['code'] for item in report['errors']}, {'applicability_required', 'steps_required', 'test_cases_required'})

    def test_application_readonly_and_reviewer_write_denied(self):
        from dataclasses import replace
        for principal, roles in [(replace(P, identity_kind='application', session_id=''), s.ROLES),
                                 (replace(P, readonly=True), s.ROLES), (replace(P, must_change_password=True), s.ROLES),
                                 (replace(P, role='reviewer'), s.BUILD)]:
            with use_context(principal), patch.object(s, 'assert_principal_current', side_effect=lambda value: value), self.assertRaises(HTTPException) as error:
                s._role(roles)
            self.assertEqual(error.exception.status_code, 403)

    def test_generic_guard_uses_document_not_just_user_marker(self):
        connection = Connection(); connection.result.fetchone.return_value = {'entity_id': 'S1'}
        with self.assertRaises(HTTPException): s.guard_generic_entity(connection, {'id': 'S1', 'payload': {}})
        connection.result.fetchone.return_value = None
        with self.assertRaises(HTTPException): s.guard_generic_entity(connection, None, {'payload': {'managed_skill': False}})
        s.guard_generic_entity(connection, {'id': 'legacy', 'payload': {'legacy_skill': True}})

    def test_each_skill_transaction_begins_read_committed(self):
        connection = Connection()
        with patch.object(s, 'connect', return_value=connection):
            self.assertIs(s._connect(), connection)
        self.assertEqual(connection.statements, [('SET TRANSACTION ISOLATION LEVEL READ COMMITTED', ())])

    def test_self_review_rejected_for_admin_editor_and_submitter(self):
        for field in ('last_editor_id', 'submitted_by'):
            document = {'last_editor_id': 'other-editor', 'submitted_by': 'other-submitter', field: P.user_id}
            with use_context(P), ExitStack() as stack:
                stack.enter_context(patch.object(s, 'assert_principal_current', side_effect=lambda value: value))
                stack.enter_context(patch.object(s, '_connect', return_value=Connection()))
                stack.enter_context(patch.object(s, '_document', return_value=({'id': 'S1', 'version': 3, 'status': 'review'}, document)))
                with self.assertRaises(HTTPException) as error:
                    s._action('S1', s.ActionBody(expected_version=3, action='publish', reason='review'))
                self.assertEqual(error.exception.status_code, 403)

    def test_stale_public_version_rejected_before_changes(self):
        with self.assertRaises(HTTPException) as error: s._expected({'version': 9}, 8)
        self.assertEqual(error.exception.status_code, 409)

    def test_proof_fingerprint_covers_manifest_and_source_versions(self):
        document = {'digest': 'a' * 64, 'manifest': copy.deepcopy(MANIFEST)}
        check = [{'kind': 'mapping', 'id': 'M1', 'mapping_version': 1, 'source_config_version': 1, 'schema_hash': 'b' * 64, 'current': True}]
        original = s._proof_fingerprint(document, check)
        for key in ('mapping_version', 'source_config_version', 'schema_hash'):
            changed = copy.deepcopy(check); changed[0][key] = 2 if key != 'schema_hash' else 'c' * 64
            self.assertNotEqual(original, s._proof_fingerprint(document, changed))
        document['manifest']['dependencies'] = [{'entity_id': 'D1', 'version': 1, 'kind': 'data'}]
        self.assertNotEqual(original, s._proof_fingerprint(document, check))

    def test_old_proof_cannot_publish_after_mapping_change(self):
        document = {'digest': 'a' * 64, 'manifest': MANIFEST, 'files': FILES}
        checks = [{'id': 'M1', 'mapping_version': 2, 'current': True}]
        with patch.object(s, '_validation', return_value={'valid': True}), patch.object(s, '_dependency_state', return_value=(checks, [])), patch.object(s, '_last_check', return_value={'outcome': 'passed', 'report': {'mode': 'contract', 'proof_fingerprint': 'old'}}):
            with self.assertRaises(HTTPException) as error: s._validate_current(Connection(), {'id': 'S1'}, document, proof=True)
            self.assertEqual(error.exception.detail['code'], 'skill_test_required')

    def test_execution_report_does_not_persist_values_or_arbitrary_error(self):
        raw = {'outcome': 'passed', 'stdout': 'SECRET_RESULT', 'inputs': 'SECRET_INPUT', 'report': {'cases': [{'status': 'passed', 'code': 'SECRET_ERROR', 'actual': 'SECRET_RESULT', 'duration_ms': 3}], 'result': 'SECRET_RESULT', 'stderr': 'SECRET_ERROR'}}
        result = s._safe_test_result(raw, MANIFEST, 'isolated_python')
        encoded = json.dumps(result)
        for secret in ('SECRET_RESULT', 'SECRET_INPUT', 'SECRET_ERROR'): self.assertNotIn(secret, encoded)
        self.assertEqual(result['report']['passed'], 1)
        self.assertEqual(result['report']['cases'][0]['code'], 'SCRIPT_ERROR')
        self.assertEqual(s._safe_test_result({'outcome': 'passed'}, MANIFEST, 'isolated_python')['outcome'], 'failed')

    def test_restore_demotes_and_discards_proof(self):
        connection = Connection(); connection.result.fetchall.return_value = [{'entity_id': 'S1'}]
        entity = {'id': 'S1', 'status': 'published', 'version': 4}
        document = {'current_revision': 2}
        with use_context(P), patch.object(s, '_document', return_value=(entity, document)), patch.object(s, '_snapshot_entity'), patch.object(s, '_sync_relations') as sync, patch.object(s, '_history_event'):
            s.demote_restored_skills(connection)
        self.assertEqual(connection.statements[0][0], 'DELETE FROM skill_checks')
        self.assertTrue(any("SET status='draft'" in sql for sql, _ in connection.statements))
        self.assertTrue(any('submitted_by=NULL' in sql for sql, _ in connection.statements))
        sync.assert_called_once()

    def test_load_holds_published_entity_and_document_shared(self):
        document = {'current_revision': 2, 'digest': 'a' * 64, 'files': FILES, 'manifest': MANIFEST}
        with use_context(P), ExitStack() as stack:
            stack.enter_context(patch.object(s, 'assert_principal_current', side_effect=lambda value: value))
            stack.enter_context(patch.object(s, '_connect', return_value=Connection()))
            document_call = stack.enter_context(patch.object(s, '_document', return_value=({'id': 'S1', 'status': 'published', 'version': 3}, document)))
            stack.enter_context(patch.object(s, '_validate_current'))
            stack.enter_context(patch.object(s, '_audit'))
            s.load_skill('S1')
        self.assertEqual(document_call.call_args.kwargs, {'shared': True})

    def test_skill_schema_is_in_restore_allowlist(self):
        from scripts.restore_backup import schema_columns
        tables = schema_columns()
        self.assertEqual(tables['skill_documents'], frozenset({'entity_id', 'current_revision', 'files', 'manifest', 'digest', 'last_editor_id', 'submitted_by', 'updated_at'}))
        self.assertTrue({'skill_revisions', 'skill_checks'} <= tables.keys())

    def test_request_failure_never_echoes_rejected_content(self):
        raw = b'{"expected_version":1,"instruction":"PRIVATE_PROMPT","tenant_id":"other"}'
        sent = False
        async def receive():
            nonlocal sent
            if sent: return {'type': 'http.disconnect'}
            sent = True
            return {'type': 'http.request', 'body': raw, 'more_body': False}
        request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': []}, receive)
        with self.assertRaises(HTTPException) as error: asyncio.run(s._body(request, s.AssistBody))
        self.assertNotIn('PRIVATE_PROMPT', str(error.exception.detail))

    def test_disconnect_waits_for_harness_cleanup(self):
        cleaned = []
        class Disconnected:
            async def is_disconnected(self): return True
        async def operation():
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.01)
                cleaned.append(True)
        async def run():
            with self.assertRaises(HTTPException) as error:
                await s._await_connected(Disconnected(), operation())
            self.assertEqual(error.exception.detail['code'], 'skill_assist_cancelled')
        asyncio.run(run())
        self.assertEqual(cleaned, [True])

    def test_duplicate_request_keys_rejected(self):
        raw = b'{"expected_version":1,"expected_version":2}'
        async def receive(): return {'type': 'http.request', 'body': raw, 'more_body': False}
        request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': []}, receive)
        with self.assertRaises(HTTPException): asyncio.run(s._body(request, s.VersionBody))


if __name__ == '__main__':
    unittest.main()
