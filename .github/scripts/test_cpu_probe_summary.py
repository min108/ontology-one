import json
import unittest

from probe_windows_cpu import safe_summary


class CpuProbeSummaryTests(unittest.TestCase):
    def test_only_fixed_codes_booleans_and_bounded_numeric_counters_survive(self):
        report = safe_summary({'password': 'PRIVATE_PASSWORD', 'trials': [
            {'trial': 1, 'code': 'TIMEOUT', 'user_cpu_ms': 12, 'wall_ms': 5003,
             'job_empty': True, 'stderr': 'PRIVATE_STDERR', 'command': 'PRIVATE_COMMAND'},
            {'trial': 2, 'code': 'PRIVATE_CODE', 'user_cpu_ms': 'PRIVATE_NUMBER',
             'setup_ms': -1, 'nonzero_exit': True}]})
        self.assertNotIn('PRIVATE_', json.dumps(report))
        self.assertEqual(report['trials'][0], {'trial': 1, 'code': 'TIMEOUT',
            'user_cpu_ms': 12, 'wall_ms': 5003, 'job_empty': True})
        self.assertEqual(report['trials'][1], {'trial': 2, 'nonzero_exit': True})
        self.assertFalse(report['provider_credentials_used'])
        self.assertEqual(report['cpu_limit_seconds'], 0.1)
        self.assertEqual(report['wall_watchdog_seconds'], 5)

    def test_trial_count_is_bounded(self):
        self.assertEqual(safe_summary({'trials': [{'trial': n} for n in range(99)]})['trial_count'], 10)


if __name__ == '__main__':
    unittest.main()
