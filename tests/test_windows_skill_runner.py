"""Portable orchestration tests; these do not claim Windows kernel validation."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from backend import windows_skill_runner as runner


FILES = {'SKILL.md': '---\nname: test-skill\ndescription: Synthetic unit test.\n---\nRead synthetic input.\n',
         'scripts/main.py': 'print(1)\n'}
CASES = [{'name': 'one', 'input': {}, 'expected_output': 1}, {'name': 'two', 'input': {}, 'expected_output': 1}]


class WindowsSkillRunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.runtime = self.root / 'python.exe'; self.runtime.write_bytes(b'unit-test-placeholder')
        self.session = self.root / 'session'; self.session.mkdir()
        runner._STATUS = None; runner._UNHEALTHY.clear()

    def tearDown(self):
        runner._STATUS = None; runner._UNHEALTHY.clear()
        self.directory.cleanup()

    def test_invalid_non_dictionary_files_fail_before_runtime_or_spawn(self):
        with patch.object(runner, '_runtime_files') as runtime:
            for value in (None, 5, ['scripts/main.py'], 'scripts/main.py'):
                report = runner.execute_tests(value, 'scripts/main.py', CASES)
                self.assertEqual(report['report']['cases'][0]['code'], 'INVALID_TEST_REQUEST')
            runtime.assert_not_called()

    def test_each_case_gets_distinct_profile_and_cleanup(self):
        profiles = []
        def profile():
            item = Mock(); item.__enter__ = Mock(return_value=item); profiles.append(item); return item
        process = Mock(); process.communicate.return_value = (b'1', b''); process.returncode = 0
        with patch.object(runner, '_runtime_files', return_value=[self.runtime]), \
             patch.object(runner, '_session_root', return_value=self.session), \
             patch.object(runner, 'AppContainerProfile', side_effect=profile), \
             patch.object(runner, 'spawn_job_process', return_value=process) as spawn:
            report = runner.execute_tests(FILES, 'scripts/main.py', CASES)
        self.assertEqual(report['outcome'], 'passed')
        self.assertEqual(len(profiles), 2); self.assertIsNot(profiles[0], profiles[1])
        for item in profiles: item.close.assert_called_once()
        self.assertEqual(spawn.call_count, 2); self.assertEqual(process.close.call_count, 2)
        self.assertFalse(self.session.exists())

    def test_profile_cleanup_failure_marks_runner_unhealthy(self):
        profile = Mock(); profile.__enter__ = Mock(return_value=profile)
        profile.close.side_effect = OSError('safe test failure')
        process = Mock(); process.communicate.return_value = (b'1', b''); process.returncode = 0
        with patch.object(runner, '_runtime_files', return_value=[self.runtime]), \
             patch.object(runner, '_session_root', return_value=self.session), \
             patch.object(runner, 'AppContainerProfile', return_value=profile), \
             patch.object(runner, 'spawn_job_process', return_value=process):
            report = runner.execute_tests(FILES, 'scripts/main.py', CASES)
        self.assertEqual(report['outcome'], 'unavailable'); self.assertTrue(runner._UNHEALTHY.is_set())
        self.assertFalse(self.session.exists())

    def test_busy_probe_is_not_cached_and_next_idle_probe_can_pass(self):
        busy = {'outcome': 'failed', 'report': {'cases': [{'code': 'RUNNER_BUSY'}]}}
        passed = {'outcome': 'passed', 'report': {'cases': [{'code': 'PASSED'}]}}
        with patch.object(runner, '_runtime_files', return_value=[self.runtime]), \
             patch.object(runner, 'execute_tests', side_effect=[busy, passed]) as execute:
            self.assertFalse(runner.runner_status()['available']); self.assertIsNone(runner._STATUS)
            self.assertTrue(runner.runner_status()['available'])
            self.assertTrue(runner.runner_status()['available'])
        self.assertEqual(execute.call_count, 2)

    def test_busy_after_cache_expiration_retains_verified_state_without_refresh(self):
        fingerprint = ((str(self.runtime), self.runtime.stat().st_size, self.runtime.stat().st_mtime_ns),)
        runner._STATUS = (fingerprint, -1000, {'available': True, 'reason': 'verified'})
        busy = {'outcome': 'failed', 'report': {'cases': [{'code': 'RUNNER_BUSY'}]}}
        with patch.object(runner, '_runtime_files', return_value=[self.runtime]), patch.object(runner, 'execute_tests', return_value=busy):
            self.assertTrue(runner.runner_status()['available'])
        self.assertEqual(runner._STATUS[1], -1000)

    def test_actual_probe_failure_is_not_cached_as_success(self):
        failed = {'outcome': 'unavailable', 'report': {'cases': [{'code': 'RUNNER_UNAVAILABLE'}]}}
        with patch.object(runner, '_runtime_files', return_value=[self.runtime]), patch.object(runner, 'execute_tests', return_value=failed):
            self.assertFalse(runner.runner_status()['available']); self.assertIsNone(runner._STATUS)

    def test_json_comparison_preserves_boolean_numeric_boundary(self):
        self.assertFalse(runner._equal({'result': True}, {'result': 1}))
        self.assertTrue(runner._equal({'result': 1.0}, {'result': 1}))
        with self.assertRaises(ValueError): runner._strict_json(b'{"x":1,"x":2}')


if __name__ == '__main__': unittest.main()
