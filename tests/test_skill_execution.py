"""Fixed runner protocol and isolation policy tests; real Docker coverage is E2E."""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend import skill_execution as client
from ops import skill_runner as service


def request():
    return {'files': {'SKILL.md': 'Example', 'scripts/main.py': 'print(1)'},
            'entrypoint': 'scripts/main.py',
            'test_cases': [{'name': 'synthetic example', 'input': {'number': 1}, 'expected_output': 1}]}


class ValidationTests(unittest.TestCase):
    def test_only_fixed_fields_and_python_entrypoint_are_accepted(self):
        service.validate_request(request())
        for field in ('image', 'command', 'volumes', 'environment', 'socket', 'tenant_id'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                service.validate_request({**request(), field: 'untrusted override'})
        body = request(); body['entrypoint'] = 'scripts/main.sh'
        with self.assertRaises(ValueError): service.validate_request(body)

    def test_paths_cannot_escape_or_collide(self):
        for path in ('/etc/passwd', '../secret', 'scripts/../secret', 'scripts\\secret', 'C:/secret',
                     'scripts//main.py', 'scripts/\x00file', 'scripts/Ａ.py'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                body = request(); body['files'][path] = 'text'; service.validate_request(body)
        for names in (('scripts/Main.py', 'scripts/main.py'), ('scripts/main.py/child',)):
            body = request()
            for name in names: body['files'][name] = 'text'
            with self.assertRaises(ValueError): service.validate_request(body)

    def test_size_depth_nonfinite_and_duplicate_keys_fail_closed(self):
        body = request(); body['files']['scripts/main.py'] = 'x' * (256 * 1024 + 1)
        with self.assertRaises(ValueError): service.validate_request(body)
        value = None
        for _ in range(22): value = [value]
        with self.assertRaises(ValueError): service.valid_value(value)
        with self.assertRaises(ValueError): service.valid_value(float('inf'))
        with self.assertRaises(ValueError): service.strict_json('{"x":1,"x":2}')
        with self.assertRaises(ValueError): service.strict_json('NaN')

    def test_test_result_comparison_does_not_coerce_boolean_to_numeric(self):
        self.assertFalse(service.json_equal({'value': True}, {'value': 1}))
        self.assertFalse(service.json_equal(['1'], [1]))
        self.assertTrue(service.json_equal({'value': [1, None]}, {'value': [1.0, None]}))

    def test_container_policy_is_fixed_and_includes_no_host_mounts(self):
        runner = object.__new__(service.DockerRunner)
        runner.owner, runner.namespace, runner.instance = 'owner', 'qa', 'instance'
        runner.image_id = 'sha256:' + 'a' * 64
        args = runner._create_args('fixed-name')
        pairs = dict(zip(args[::2], args[1::2]))
        for key, value in {'--network': 'none', '--cap-drop': 'ALL', '--user': '65534:65534',
                           '--pids-limit': '64', '--memory': '256m', '--memory-swap': '256m',
                           '--cpus': '1', '--log-driver': 'none'}.items():
            self.assertEqual(args[args.index(key) + 1], value)
        self.assertIn('--read-only', args)
        self.assertIn('no-new-privileges=true', args)
        self.assertEqual(args[-1], runner.image_id)
        self.assertNotIn('--volume', args); self.assertNotIn('--mount', args)
        self.assertNotIn('--privileged', args)
        self.assertIn('core=0:0', args); self.assertIn('nofile=64:64', args)
        for name in service.PROXY_KEYS: self.assertIn(name + '=', args)


class SocketProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='skill-client-')
        self.path = str(Path(self.temp.name) / 'runner.sock')
        class FakeRunner:
            image_id = 'fixed-test-image'
            stop = threading.Event()
            unhealthy = threading.Event()
            gate = threading.BoundedSemaphore(2)
            def execute(self, body, disconnected):
                rows = [{'name': case['name'], 'status': 'passed', 'code': 'PASSED', 'duration_ms': 1} for case in body['test_cases']]
                return {'outcome': 'passed', 'mode': 'isolated_python', 'report': {'cases': rows, 'passed': len(rows), 'failed': 0, 'duration_ms': 1}}
        self.runner = FakeRunner()
        self.server = service.RunnerServer(self.path, self.runner)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.environment = patch.dict(os.environ, {'ONTOLOGY_SKILL_RUNNER_SOCKET': self.path})
        self.environment.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.environment.stop(); self.temp.cleanup()

    def test_real_uds_protocol_returns_only_bounded_metadata(self):
        self.assertTrue(client.runner_status()['available'])
        body = request()
        result = client.execute_tests(body['files'], body['entrypoint'], body['test_cases'])
        self.assertEqual(result['outcome'], 'passed')
        self.assertNotIn('expected_output', json.dumps(result))
        self.assertNotIn('print(1)', json.dumps(result))

    def test_unknown_options_rejected_without_echoing_contents(self):
        status, result, _ = client._exchange('POST', '/test', {**request(), 'command': 'SECRET-CONTENT'})
        self.assertEqual(status, 400)
        self.assertEqual(result, {'code': 'INVALID_TEST_REQUEST'})

    def test_busy_and_unavailable_can_never_satisfy_publish_proof(self):
        self.runner.gate.acquire(); self.runner.gate.acquire()
        try:
            body = request(); result = client.execute_tests(body['files'], body['entrypoint'], body['test_cases'])
            self.assertEqual(result['outcome'], 'failed')
            self.assertEqual(result['report']['cases'][0]['code'], 'RUNNER_BUSY')
        finally:
            self.runner.gate.release(); self.runner.gate.release()
        self.runner.image_id = None
        self.assertFalse(client.runner_status()['available'])
        result = client.execute_tests(body['files'], body['entrypoint'], body['test_cases'])
        self.assertEqual(result['outcome'], 'unavailable')

    def test_pre_cancelled_request_never_starts_execution(self):
        body = request(); event = threading.Event(); event.set()
        with patch.object(self.runner, 'execute') as execute:
            result = client.execute_tests(body['files'], body['entrypoint'], body['test_cases'], cancelled=event)
        execute.assert_not_called()
        self.assertEqual(result['report']['cases'][0]['code'], 'CANCELLED')

    def test_unexpected_response_fields_cannot_leak_script_output(self):
        bad = {'outcome': 'passed', 'mode': 'isolated_python', 'report': {'cases': [], 'passed': 0, 'failed': 0, 'duration_ms': 0}, 'stdout': 'PRIVATE'}
        body = request()
        with patch.object(client, '_exchange', return_value=(200, bad, None)):
            result = client.execute_tests(body['files'], body['entrypoint'], body['test_cases'])
        self.assertEqual(result['outcome'], 'unavailable')
        self.assertNotIn('PRIVATE', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
