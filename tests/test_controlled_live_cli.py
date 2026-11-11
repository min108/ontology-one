"""Pure-local safety checks for the opt-in controlled live acceptance CLI.

No provider, database, container, model credential, or SDK process is used.
"""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    'controlled_live_cli', Path(__file__).resolve().parents[1] / 'scripts/check_controlled_live.py')
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

KEY = 'SYNTHETIC_CONTROLLED_LOCAL_ONLY_NOT_A_PROVIDER_KEY'
MODEL_TEXT = 'SYNTHETIC_MODEL_OR_SOURCE_TEXT_MUST_NOT_REACH_REPORT'


class CliBoundaryTests(unittest.TestCase):
    def test_boolean_oracle_rejects_numeric_false(self):
        expected = {'rule_applies': False, 'missing_attachment_proves_failure': False}
        self.assertTrue(cli.strict_booleans(dict(expected), expected))
        self.assertFalse(cli.strict_booleans({'rule_applies': 0, 'missing_attachment_proves_failure': False}, expected))
        self.assertFalse(cli.strict_booleans({'rule_applies': False, 'missing_attachment_proves_failure': 'false'}, expected))

    def test_source_version_oracle_rejects_stale_coverage_and_references(self):
        from scripts.controlled_live_legacy import source_versions_match
        expected = {'synthetic-material': 'actual-version-before-model'}
        coverage = {'synthetic-material': {'source_version': 'actual-version-before-model'}}
        current = [{'source_id': 'synthetic-material', 'source_version': 'actual-version-before-model'}]
        self.assertTrue(source_versions_match(expected, coverage, current))
        stale = [{'source_id': 'synthetic-material', 'source_version': 'older-valid-version'}]
        self.assertFalse(source_versions_match(expected, coverage, stale))
        self.assertFalse(source_versions_match(expected, {'synthetic-material': {'source_version': 'older-valid-version'}}, current))

    def test_listing_is_offline_and_does_not_read_credentials_or_prepare_services(self):
        output = io.StringIO()
        with (patch.object(cli, 'read_key', side_effect=AssertionError('must not read credentials')),
              patch.object(cli, 'setup_context', side_effect=AssertionError('must not prepare services')),
              redirect_stdout(output)):
            self.assertEqual(cli.main(['--list-cases']), 0)
        listed = json.loads(output.getvalue())
        serialized = json.dumps(listed)
        for name in cli.CASE_IDS:
            self.assertIn(name, serialized)

    def test_key_cli_argument_is_rejected_without_echo(self):
        output = io.StringIO()
        with redirect_stderr(output), redirect_stdout(output), self.assertRaises(SystemExit):
            cli.main(['--api-key', KEY])
        self.assertNotIn(KEY, output.getvalue())

    def test_credential_is_removed_from_environment_immediately(self):
        environment = {'DEEPSEEK_API_KEY': KEY, 'UNRELATED': 'preserved'}
        with patch.object(cli.os, 'environ', environment):
            self.assertEqual(cli.read_key(), KEY)
        self.assertNotIn('DEEPSEEK_API_KEY', environment)
        self.assertEqual(environment['UNRELATED'], 'preserved')

    def test_missing_noninteractive_credential_does_not_prompt_or_echo(self):
        with (patch.object(cli.os, 'environ', {}),
              patch.object(cli.sys.stdin, 'isatty', return_value=False),
              patch.object(cli.getpass, 'getpass', side_effect=AssertionError('must not prompt'))):
            with self.assertRaises(ValueError):
                cli.read_key()

    def test_invalid_environment_credential_is_also_removed(self):
        environment = {'DEEPSEEK_API_KEY': 'invalid\ncredential'}
        with patch.object(cli.os, 'environ', environment), self.assertRaises(ValueError):
            cli.read_key()
        self.assertNotIn('DEEPSEEK_API_KEY', environment)

    def test_usage_copies_only_numeric_allowlist(self):
        value = cli.safe_usage({'model_responses': 2, 'complete': True,
            'input_tokens': 9, 'output_tokens': 4, 'cache_read_tokens': 0,
            'cache_write_tokens': 0, 'api_key': KEY, 'answer': MODEL_TEXT})
        raw = json.dumps(value)
        self.assertNotIn(KEY, raw)
        self.assertNotIn(MODEL_TEXT, raw)
        self.assertEqual(value['input_tokens'], 9)
        self.assertEqual(value['output_tokens'], 4)
        invalid = cli.safe_usage({'model_responses': 1, 'complete': True,
            'input_tokens': True, 'output_tokens': -4,
            'cache_read_tokens': 0, 'cache_write_tokens': 0})
        self.assertNotEqual(invalid.get('status'), 'reported')

    def test_diagnostic_only_accepts_fixed_stage_and_boolean_mapping_checks(self):
        try:
            error = RuntimeError(KEY + MODEL_TEXT)
            error.acceptance_stage = KEY
            error.acceptance_checks = {'amount_candidate': True, 'payer_transform': KEY, MODEL_TEXT: True}
            raise error
        except RuntimeError as failure:
            value = cli.safe_diagnostic(failure)
        self.assertNotIn(KEY, json.dumps(value))
        self.assertNotIn(MODEL_TEXT, json.dumps(value))
        self.assertEqual(value['acceptance_checks'], {'amount_candidate': True})
        self.assertNotIn('acceptance_stage', value)

    def test_job_diagnostic_discards_raw_batch_errors_and_source_values(self):
        try:
            error = RuntimeError(KEY)
            error.acceptance_stage = 'job_completed'
            error.job_diagnostic = {'queue_status': 'failed', 'business_status': 'partial',
                'attempts': 1, 'max_attempts': 1, 'source_snapshot': MODEL_TEXT, 'exception': KEY,
                'batches': [{'status': 'failed', 'pages': 1, 'error_count': 2,
                    'reason_codes': ['QUOTE_NOT_IN_SOURCE', KEY, {'message': MODEL_TEXT}],
                    'errors': [{'candidate': MODEL_TEXT, 'reason': KEY}]}]}
            raise error
        except RuntimeError as failure:
            value = cli.safe_diagnostic(failure)
        raw = json.dumps(value)
        self.assertNotIn(KEY, raw)
        self.assertNotIn(MODEL_TEXT, raw)
        self.assertEqual(value['job']['batches'][0]['reason_codes'], ['QUOTE_NOT_IN_SOURCE'])
        self.assertEqual(value['job']['attempts'], 1)


class RunBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_exception_text_is_discarded_and_first_failure_stops_without_retry(self):
        class Context:
            runner_available = True
            calls = []

            async def run_case(self, name):
                self.calls.append(name)
                raise RuntimeError(KEY + MODEL_TEXT)

        context = Context()
        rows = await cli.run_selected(list(cli.CASE_IDS), context)
        self.assertEqual(context.calls, [cli.CASE_IDS[0]])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'failed')
        self.assertEqual(rows[0]['error_code'], 'LIVE_CASE_FAILED')
        raw = json.dumps(rows)
        self.assertNotIn(KEY, raw)
        self.assertNotIn(MODEL_TEXT, raw)

    async def test_missing_runner_is_blocked_before_model_call(self):
        class Context:
            runner_available = False

            async def run_case(self, name):
                raise AssertionError('must not call the model without an isolated runner')

        rows = await cli.run_selected(['readonly_skill', 'legacy_ai'], Context())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['case'], 'readonly_skill')
        self.assertEqual(rows[0]['status'], 'blocked')

    async def test_case_result_extras_and_unknown_check_text_never_reach_report(self):
        check = next(iter(cli.CHECK_IDS))

        class Context:
            runner_available = True

            async def run_case(self, name):
                return {'status': 'passed',
                    'checks': [{'check': check, 'status': 'passed', 'detail': KEY},
                               {'check': MODEL_TEXT, 'status': 'passed'}],
                    'usage': {'model_responses': 1, 'complete': True,
                        'input_tokens': 1, 'output_tokens': 2,
                        'cache_read_tokens': 0, 'cache_write_tokens': 0, 'secret': KEY},
                    'answer': MODEL_TEXT, 'api_key': KEY, 'error_code': MODEL_TEXT}

        rows = await cli.run_selected(['legacy_ai'], Context())
        raw = json.dumps(rows)
        self.assertNotIn(KEY, raw)
        self.assertNotIn(MODEL_TEXT, raw)
        self.assertTrue(all(row['check'] in cli.CHECK_IDS for row in rows[0]['checks']))

    async def test_ordered_cases_execute_once_each_and_append_to_supplied_results(self):
        check = next(iter(cli.CHECK_IDS))

        class Context:
            runner_available = True

            def __init__(self):
                self.calls = []

            async def run_case(self, name):
                self.calls.append(name)
                return {'status': 'passed', 'checks': [{'check': check, 'status': 'passed'}],
                        'usage': None}

        context = Context()
        results = []
        returned = await cli.run_selected(['legacy_ai', 'agent_multiturn'], context, results)
        self.assertIs(returned, results)
        self.assertEqual(context.calls, ['legacy_ai', 'agent_multiturn'])
        self.assertEqual([row['case'] for row in results], context.calls)
        self.assertTrue(all(row['status'] == 'passed' for row in results))

    async def test_blocked_case_stops_the_suite(self):
        class Context:
            runner_available = True

            def __init__(self):
                self.calls = []

            async def run_case(self, name):
                self.calls.append(name)
                return {'status': 'blocked', 'checks': [], 'usage': None}

        context = Context()
        rows = await cli.run_selected(['authorized_mcp', 'legacy_ai'], context)
        self.assertEqual(context.calls, ['authorized_mcp'])
        self.assertEqual(rows[0]['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
