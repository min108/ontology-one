import json
from pathlib import Path
import tempfile
import unittest

from summarize_windows_acceptance import contract, log_summary, read, report_summary, summarize, ROOT


class AcceptanceSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths, cls.labels, cls.symbols, cls.tests, cls.classes = contract(ROOT)

    def test_report_keeps_only_known_checks_fixed_codes_and_numeric_counters(self):
        value = {'passed': False, 'password': 'PRIVATE_PASSWORD', 'checks': [
            {'name': 'native_units', 'status': 'failed', 'exit_code': 1, 'message': 'PRIVATE_BODY'},
            {'name': 'PRIVATE_LABEL', 'status': 'failed', 'code': 'PRIVATE_CODE'},
            {'name': 'recover_first_start_and_health', 'status': 'failed', 'code': 'PermissionError',
             'launcher_failure': {'error': 'MIGRATION_FAILED', 'message': 'PRIVATE_LAUNCH_MESSAGE',
                'diagnostic': {'winerror': 5, 'exception': 'PermissionError',
                    'process_counters': {'stderr_bytes': 42, 'PRIVATE_COUNTER': 9},
                    'mysql_codes': ['MY-010119', 'PRIVATE_MYSQL_TEXT']}}}]}
        result = report_summary(value, self.labels, self.symbols, self.classes)
        encoded = json.dumps(result)
        self.assertNotIn('PRIVATE_', encoded)
        self.assertEqual(result['checks'][1]['launcher_failure']['diagnostic']['winerror'], 5)
        self.assertEqual(result['checks'][1]['exception'], 'PermissionError')
        self.assertFalse(result['passed'])

    def test_unittest_log_discards_messages_source_lines_and_absolute_paths(self):
        raw = '''ERROR: test_running_source_lock_prevents_copy (tests.test_windows_upgrade.UpgradeTests.test_running_source_lock_prevents_copy)
Traceback (most recent call last):
  File "C:\\Users\\PRIVATE_USER\\app\\ops\\windows_upgrade.py", line 120, in upgrade
    raise ValueError('PRIVATE_SOURCE_LINE')
PermissionError: [WinError 5] PRIVATE_EXCEPTION_MESSAGE
backend.windows_process.WindowsProcessError: PROCESS_IO_FAILED
backend.windows_process.WindowsProcessError: PRIVATE_PROCESS_MESSAGE
  File "C:\\PRIVATE_FILE.py", line 4, in secret
ERROR: tests.test_agent_runtime (unittest.loader._FailedTest.tests.test_agent_runtime)
ERROR: PRIVATE_TEST (PRIVATE_CLASS)
Ran 222 tests in 12.0s
FAILED (failures=1, errors=1, skipped=10)
'''
        result = log_summary(raw, self.paths, self.tests, self.classes, self.symbols)
        encoded = json.dumps(result)
        self.assertNotIn('PRIVATE_', encoded)
        self.assertNotIn('C:', encoded)
        self.assertEqual(result['frames'], [{'source': 'ops/windows_upgrade.py', 'line': 120}])
        self.assertEqual(result['tests_run'], 222)
        self.assertEqual(result['skipped'], 10)
        self.assertIn('tests.test_agent_runtime', result['failed_tests'])
        self.assertEqual(result['exception_types'], ['PermissionError', 'WindowsProcessError'])
        self.assertEqual(result['error_codes'], ['PROCESS_IO_FAILED'])

    def test_cpu_failure_keeps_only_fixed_code_and_numeric_metadata(self):
        raw = json.dumps({'native_cpu_limit_failure': {'code': 'TIMEOUT', 'user_cpu_ms': 12,
            'stdout_bytes': 0, 'message': 'PRIVATE_NATIVE_MESSAGE', 'command': 'PRIVATE_COMMAND'}})
        result = log_summary(raw, self.paths, self.tests, self.classes, self.symbols)
        self.assertEqual(result['cpu_limit_failure'], {'code': 'TIMEOUT', 'user_cpu_ms': 12, 'stdout_bytes': 0})
        self.assertNotIn('PRIVATE_', json.dumps(result))

    def test_missing_malformed_and_oversized_reports_never_emit_raw_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'windows-ci-report.json').write_text('PRIVATE_BROKEN_JSON')
            (root/'ci-entry.stderr.log').write_text('PRIVATE_RAW_STDERR')
            encoded = json.dumps(summarize(root))
            self.assertNotIn('PRIVATE_', encoded)
            large = root/'large.log'; large.write_bytes(b'x' * (2 * 1024 * 1024 + 1))
            with self.assertRaises(ValueError):
                read(large)


if __name__ == '__main__':
    unittest.main()
