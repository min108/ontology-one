"""Numeric usage and fixed error categories must never retain model/provider text."""
import unittest

from backend import skill_harness as client
from backend import skill_harness_worker as worker


def message(seq, usage):
    return {'type': 'assistant/message', 'seq': seq,
            'data': {'message': {'content': 'private prompt and model output'}, 'usage': usage}}


def failure(code, status=None):
    return [{'type': 'turn/end', 'data': {'reason': {'kind': 'error', 'error': {
        'code': code, 'status': status, 'message': 'private provider body',
        'cause': {'Authorization': 'private-header'}, 'stack': 'private stack'}}}}]


class HarnessDiagnosticsTests(unittest.TestCase):
    def test_only_deployment_proxy_and_ca_variables_survive(self):
        source = {'HTTPS_PROXY': 'http://synthetic-proxy.invalid:8080',
                  'https_proxy': 'http://synthetic-lowercase.invalid:8080',
                  'NO_PROXY': '127.0.0.1,localhost', 'SSL_CERT_FILE': '/etc/synthetic-ca.pem',
                  'DATABASE_PASSWORD': 'private', 'DEEPSEEK_API_KEY': 'private',
                  'NODE_OPTIONS': '--require /private/injection.js',
                  'NODE_TLS_REJECT_UNAUTHORIZED': '0', 'PYTHONPATH': '/private/injection',
                  'LD_PRELOAD': '/private/injection.so'}
        self.assertEqual(worker.deployment_network_environment(source), {
            'HTTPS_PROXY': source['HTTPS_PROXY'], 'https_proxy': source['https_proxy'],
            'NO_PROXY': source['NO_PROXY'], 'NODE_EXTRA_CA_CERTS': '/etc/synthetic-ca.pem'})
        source['NODE_EXTRA_CA_CERTS'] = '/etc/synthetic-explicit-ca.pem'
        self.assertEqual(worker.deployment_network_environment(source)['NODE_EXTRA_CA_CERTS'],
                         '/etc/synthetic-explicit-ca.pem')

    def test_usage_deduplicates_native_events_and_retains_only_numeric_metadata(self):
        first = message(1, {'inputTokens': 10, 'outputTokens': 3, 'cacheReadTokens': 7})
        result = worker._usage([first, first, message(2, {'inputTokens': 5, 'outputTokens': 2,
                                                       'cacheWriteTokens': 4})])
        self.assertEqual(result, {'model_responses': 2, 'complete': True, 'input_tokens': 15,
                                 'output_tokens': 5, 'cache_read_tokens': 7, 'cache_write_tokens': 4})
        self.assertEqual(client._safe_usage(result), result)
        self.assertNotIn('private', repr(result))

    def test_missing_or_invalid_usage_is_unknown_instead_of_false_zero(self):
        for usage in (None, {}, {'inputTokens': True, 'outputTokens': 1},
                      {'inputTokens': -1, 'outputTokens': 1},
                      {'inputTokens': 5, 'outputTokens': 'secret'},
                      {'inputTokens': 5, 'outputTokens': 10_000_001}):
            with self.subTest(usage=usage):
                result = worker._usage([message(1, usage)])
                self.assertFalse(result['complete'])
                self.assertIsNone(result['input_tokens'])
                self.assertIsNone(result['output_tokens'])
        self.assertFalse(worker._usage([])['complete'])

    def test_audit_rejects_extra_fields_and_non_numeric_values(self):
        result = worker._usage([message(1, {'inputTokens': 1, 'outputTokens': 2})])
        for invalid in ({**result, 'message': 'private'}, {**result, 'output_tokens': True},
                        {**result, 'input_tokens': 'private'}, {**result, 'input_tokens': None}):
            self.assertEqual(client._safe_usage(invalid), {'complete': False})

    def test_provider_failures_have_distinct_fixed_safe_categories(self):
        for source, expected in {'AUTH': 'HARNESS_PROVIDER_AUTH', 'QUOTA': 'HARNESS_PROVIDER_QUOTA',
                                 'RATE_LIMIT': 'HARNESS_PROVIDER_RATE_LIMIT',
                                 'INVALID_REQUEST': 'HARNESS_PROVIDER_REQUEST',
                                 'TRANSPORT': 'HARNESS_PROVIDER_UNAVAILABLE',
                                 'TIMEOUT': 'HARNESS_TIMEOUT',
                                 'MALFORMED_RESPONSE': 'HARNESS_PROVIDER_RESPONSE'}.items():
            with self.subTest(code=source):
                result = worker._failure_code(failure(source), 'error')
                self.assertEqual(result, expected)
                self.assertNotIn('private', repr(client._error(result).detail))
        self.assertEqual(worker._failure_code(failure('HTTP_401', 401), 'error'), 'HARNESS_PROVIDER_AUTH')
        self.assertEqual(worker._failure_code(failure({'arbitrary': 'private'}), 'error'), 'HARNESS_INCOMPLETE')

    def test_output_limit_and_no_proposal_are_not_provider_auth_errors(self):
        self.assertEqual(worker._failure_code([], 'max-tokens'), 'HARNESS_OUTPUT_LIMIT')
        self.assertEqual(worker._failure_code([], 'aborted'), 'HARNESS_CANCELLED')
        with self.assertRaises(worker.NoProposalError):
            worker._proposal([], {'SKILL.md': 'example'}, {})
        self.assertEqual(client._error('HARNESS_NO_PROPOSAL').status_code, 422)


if __name__ == '__main__':
    unittest.main()
