"""Harness boundaries and lifecycle; opt-in tests use the real official SDK.

RUN_HARNESS_SDK_TESTS=1 requires the pinned runtime and writable /dev/shm.
Only the model service is simulated; no real provider key is required.
"""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from backend import skill_harness as client
from backend import skill_harness_worker as worker
from backend.skills import Manifest, SCHEMA_KEYS, SCHEMA_TYPES


def windows_pid_alive(pid):
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return ctypes.get_last_error() != 87
    try:
        return kernel.WaitForSingleObject(handle, 0) != 0
    finally:
        kernel.CloseHandle(handle)


FILES = {'SKILL.md': '---\nname: example\ndescription: Example.\n---\nOriginal.'}
MANIFEST = {'dependencies': [{'kind': 'data', 'id': 'retained'}], 'tool_dependencies': ['retained-tool']}
AUTHORING_SCHEMA = json.loads((Path(__file__).resolve().parents[1] / 'backend/harness_plugin/authoring.schema.json').read_text())


def proposal(**values):
    return {'ontology_skill_proposal': '1.0', 'summary': 'Changed',
            'changes': [{'path': 'SKILL.md', 'content': 'Revised'}],
            'delete_paths': [], 'manifest_patch': {'applicability': 'New condition'}, **values}


def events(value=None, tool='propose_skill_files', error=False):
    return [{'type': 'tool/call', 'seq': 4, 'data': {'callId': 'call-1', 'name': tool}},
            {'type': 'tool/result', 'sourceEventSeqs': [4],
             'data': {'message': {'toolCallId': 'call-1', 'isError': error,
                      'content': [{'type': 'text', 'text': json.dumps(value or proposal())}]}}}]


class ProposalTests(unittest.TestCase):
    def test_only_accepted_proposal_changes_files_and_preserves_grants(self):
        result = worker._proposal(events(), FILES, MANIFEST)
        self.assertEqual(result['files']['SKILL.md'], 'Revised')
        self.assertEqual(result['manifest']['dependencies'], MANIFEST['dependencies'])
        self.assertEqual(result['manifest']['tool_dependencies'], MANIFEST['tool_dependencies'])
        self.assertEqual(FILES['SKILL.md'].splitlines()[-1], 'Original.')

    def test_file_text_cannot_forge_a_proposal_or_another_tool_call(self):
        for value in (events(tool='read_skill_file'), events(error=True),
                      [{'type': 'tool/result', **events()[-1]}]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                worker._proposal(value, FILES, MANIFEST)
        value = events(); value[-1]['data']['message']['toolCallId'] = 'unrelated'
        with self.assertRaises(ValueError): worker._proposal(value, FILES, MANIFEST)

    def test_author_cannot_grant_privileges_delete_manifest_or_escape_paths(self):
        invalid = [proposal(manifest_patch={'dependencies': []}),
                   proposal(delete_paths=['SKILL.md']),
                   proposal(changes=[{'path': '../escape', 'content': 'x'}]),
                   proposal(changes=[{'path': 'SKILL.md', 'content': 'x'}] * 2)]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                worker._proposal(events(value), FILES, MANIFEST)

    def test_open_package_format_supports_readme_license_refs_and_safe_extensions(self):
        files = {**FILES, 'README.md': 'Readme', 'LICENSE': 'License',
                 'refs/example.md': 'Reference', 'examples/example.txt': 'Example'}
        self.assertEqual(worker._files(files), files)
        result = worker._proposal(events(proposal(changes=[{'path': 'refs/new.md', 'content': 'new'}])), files, MANIFEST)
        self.assertEqual(result['files']['LICENSE'], 'License')
        self.assertEqual(result['files']['refs/new.md'], 'new')

    def test_bounds_duplicate_json_and_nonfinite_values_fail(self):
        for path in ('/etc/passwd', 'a/../b', 'a//b', 'a\\b', 'C:/b'):
            with self.subTest(path=path), self.assertRaises(ValueError): worker._files({**FILES, path: 'x'})
        with self.assertRaises(ValueError): worker._files({**FILES, 'x': 'x' * 262145})
        with self.assertRaises(ValueError): worker._decode('{"x":1,"x":2}')
        with self.assertRaises(ValueError): worker._decode('NaN')


class AuthoringContractTests(unittest.TestCase):
    def test_exposed_manifest_contract_accepts_valid_platform_shapes(self):
        Draft202012Validator.check_schema(AUTHORING_SCHEMA)
        cases = [{}, {'applicability': 'Synthetic CNY amounts in cents only.',
                  'inputs': {'type': 'object', 'properties': {'amount': {'type': 'integer', 'minimum': 0}},
                             'required': ['amount'], 'additionalProperties': False},
                  'outputs': {'type': 'integer'},
                  'steps': [{'title': 'Validate amount', 'instruction': 'Reject negative input; preserve the unit in cents.'}],
                  'entrypoint': 'scripts/main.py',
                  'test_cases': [{'name': 'synthetic amount', 'input': {'amount': 120}, 'expected_output': 120}]},
                 {'inputs': {'type': 'array', 'items': {'type': 'string'}}, 'outputs': {'type': 'null'}, 'entrypoint': None}]
        for manifest in cases:
            with self.subTest(manifest=manifest):
                Manifest.model_validate(manifest, strict=True)
                Draft202012Validator(AUTHORING_SCHEMA).validate(
                    {'summary': 'Synthetic proposal', 'changes': [], 'delete_paths': [], 'manifest_patch': manifest})
        for name in ('inputs', 'outputs'):
            profile = AUTHORING_SCHEMA['properties']['manifest_patch']['properties'][name]
            self.assertEqual(set(profile['properties']), SCHEMA_KEYS)
            self.assertEqual(set(profile['properties']['type']['enum']), SCHEMA_TYPES)

    def test_common_model_shape_guesses_are_rejected_by_tool_and_platform(self):
        invalid = [{'steps': ['Validate the data']}, {'steps': [{'title': 'Only a title'}]},
                   {'steps': [{'title': 'x', 'instruction': 'y', 'tool': 'arbitrary_sql'}]},
                   {'test_cases': [{'name': 'example', 'input': {}, 'expected': {}}]},
                   {'test_cases': [{'name': 'example', 'input': {}}]},
                   {'inputs': {'type': ['string', 'null']}}, {'inputs': {'type': 'object', '$ref': 'https://example.invalid'}},
                   {'outputs': {'type': 'number', 'format': 'currency'}}, {'inputs': {'description': 'No declared type'}},
                   {'steps': [{'title': 'x', 'instruction': 'y'}] * 31},
                   {'test_cases': [{'name': str(i), 'input': {}, 'expected_output': {}} for i in range(11)]}]
        for manifest in invalid:
            with self.subTest(manifest=manifest):
                with self.assertRaises(ValueError): Manifest.model_validate(manifest, strict=True)
                self.assertFalse(Draft202012Validator(AUTHORING_SCHEMA).is_valid(
                    {'summary': 'Invalid proposal', 'changes': [], 'delete_paths': [], 'manifest_patch': manifest}))


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='harness-config-test-')
        self.path = Path(self.temporary.name) / 'harness.json'
        if sys.platform == 'win32':
            from backend.platform_io import ensure_private_directory
            ensure_private_directory(Path(self.temporary.name))
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(client, '_principal', return_value=object()))
        self.stack.enter_context(patch.object(client, '_config_path', side_effect=lambda **_: self.path))
        self.stack.enter_context(patch.object(client, 'read_bytes', side_effect=lambda path: path.read_bytes()))
        self.stack.enter_context(patch.object(client, 'write_bytes', side_effect=self.write))
        from backend.platform_io import secure_open
        self.stack.enter_context(patch.object(client, 'open_file', side_effect=secure_open))
        self.stack.enter_context(patch.object(client, 'append_audit'))
        self.stack.enter_context(patch.object(client, 'harness_status', return_value={'ok': True}))

    def write(self, path, raw):
        path.write_bytes(raw); path.chmod(0o600)
        if sys.platform == 'win32':
            from backend.platform_io import restrict_permissions
            restrict_permissions(path)

    def tearDown(self):
        self.stack.close(); self.temporary.cleanup()

    def test_default_https_messages_and_loopback_requires_deployment_opt_in(self):
        self.assertEqual(client.HarnessConfiguration().base_url, 'https://api.deepseek.com/anthropic')
        invalid = ['http://example.com', 'https://user:secret@example.com',
                   'https://example.com?key=x', 'https://example.com/#fragment', 'https://example.com\\evil']
        with patch.dict(os.environ, {'ONTOLOGY_HARNESS_ALLOW_LOOPBACK_TEST': '0'}):
            for value in invalid + ['http://127.0.0.1:8909']:
                with self.subTest(value=value), self.assertRaises(ValueError): client._base_url(value)
        with patch.dict(os.environ, {'ONTOLOGY_HARNESS_ALLOW_LOOPBACK_TEST': '1'}):
            self.assertEqual(client._base_url('http://127.0.0.1:8909/'), 'http://127.0.0.1:8909')
            with self.assertRaises(ValueError): client._base_url('http://example.com')

    def test_key_is_bound_to_full_endpoint_and_clear_does_not_restore_old_key(self):
        client._save_configuration(client.HarnessConfiguration(api_key='synthetic-provider-key'))
        before = client._configuration()
        with self.assertRaises(HTTPException) as caught:
            client._save_configuration(client.HarnessConfiguration(base_url='https://api.deepseek.com/other'))
        self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(client._configuration(), before)
        client._save_configuration(client.HarnessConfiguration(clear_key=True))
        cleared = client._configuration()
        with self.assertRaises(HTTPException) as caught: client._mark_verified(object(), before['revision'])
        self.assertEqual(caught.exception.detail['code'], 'HARNESS_CONFIG_CHANGED')
        self.assertEqual(client._configuration(), cleared)
        self.assertEqual(cleared['api_key'], '')

    def test_verification_and_configuration_use_same_file_lock(self):
        client._save_configuration(client.HarnessConfiguration(api_key='synthetic-provider-key'))
        revision = client._configuration()['revision']
        entered, finished = threading.Event(), threading.Event()
        def verify():
            entered.set(); client._mark_verified(object(), revision); finished.set()
        with client._config_lock():
            thread = threading.Thread(target=verify); thread.start()
            self.assertTrue(entered.wait(1)); self.assertFalse(finished.wait(.05))
        thread.join(2)
        self.assertTrue(finished.is_set()); self.assertTrue(client._configuration()['verified'])

    def test_credential_validation_errors_never_echo_rejected_body(self):
        app = FastAPI(); app.include_router(client.router)
        with TestClient(app) as api:
            for body in ({'api_key': 'sensitive-test-provider-key', 'clear_key': True},
                         {'api_key': 'sensitive-test-provider-key', 'unknown': 'value'},
                         {'api_key': {'nested': 'sensitive-test-provider-key'}}):
                response = api.post('/skills/harness/config', json=body)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn('sensitive-test-provider-key', response.text)
                self.assertNotIn('input', response.text)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux mount-policy test; Windows uses ACL and memory-only sessions')
    def test_disk_session_workspace_is_rejected_without_fallback(self):
        # /tmp may itself be tmpfs in hardened containers. Supply the mount
        # evidence explicitly and exercise the real filesystem decision code.
        for options, filesystem in (('rw', 'ext4'), ('ro', 'tmpfs')):
            evidence = f'1 0 0:1 / / {options},relatime - {filesystem} fixture {options}\n'
            with self.subTest(options=options, filesystem=filesystem), \
                    patch.dict(os.environ, {'ONTOLOGY_HARNESS_TMP_ROOT': self.temporary.name}), \
                    patch.object(Path, 'read_text', return_value=evidence):
                with self.assertRaises(HTTPException) as caught: client._tmpfs_root()
            self.assertEqual(caught.exception.detail['code'], 'HARNESS_UNAVAILABLE')


class MemoryContextTests(unittest.TestCase):
    def test_private_channel_is_loopback_authenticated_one_use_and_not_a_proxy(self):
        with worker.MemoryContextServer({'files': FILES, 'manifest': {}}) as channel:
            self.assertEqual(channel.server.server_address[0], '127.0.0.1')
            def request(path='/context', token=None, extra=None):
                connection = http.client.HTTPConnection('127.0.0.1', channel.server.server_port, timeout=2)
                headers = {'Authorization': 'Bearer ' + (token or channel.token), **(extra or {})}
                connection.request('GET', path, headers=headers)
                response = connection.getresponse(); body = response.read(); status = response.status
                connection.close(); return status, body
            self.assertEqual(request(token='invalid')[0], 403)
            connection = http.client.HTTPConnection('127.0.0.1', channel.server.server_port, timeout=2)
            connection.putrequest('GET', '/context')
            connection.putheader('Authorization', 'Bearer ' + channel.token)
            connection.putheader('Authorization', 'Bearer ' + channel.token)
            connection.endheaders(); response = connection.getresponse()
            self.assertEqual(response.status, 403); response.read(); connection.close()
            self.assertEqual(request(path='http://example.invalid/context')[0], 403)
            self.assertEqual(request(extra={'Proxy-Authorization': 'Basic forbidden'})[0], 403)
            status, raw = request()
            self.assertEqual(status, 200); self.assertEqual(json.loads(raw)['files'], FILES)
            self.assertEqual(request()[0], 403); self.assertIsNone(channel._body)
        self.assertFalse(channel.thread.is_alive())

    def test_context_size_is_bounded_before_opening_a_listener(self):
        with self.assertRaises(ValueError): worker.MemoryContextServer({'text': 'x' * (worker.MAX_INPUT + 1)})


class WindowsExchangeOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeat_cancel_and_terminate_failure_still_join_communicator(self):
        from backend.windows_process import WindowsProcessError
        started, closed, release, finished = (threading.Event() for _ in range(4))
        class Process:
            returncode = 1
            def communicate(self, *_, **__):
                started.set(); release.wait(3); finished.set(); return b'', b''
            def terminate_tree(self):
                raise WindowsProcessError('PROCESS_CLEANUP_FAILED')
            def close(self):
                closed.set()
        executor = client.HarnessExecutor()
        with patch.object(client.sys, 'platform', 'win32'):
            task = asyncio.create_task(executor._exchange(Process(), {}))
            try:
                async with asyncio.timeout(2):
                    while not started.is_set(): await asyncio.sleep(.01)
                task.cancel()
                async with asyncio.timeout(2):
                    while not closed.is_set(): await asyncio.sleep(.01)
                task.cancel(); release.set()
                with self.assertRaises(asyncio.CancelledError): await task
                self.assertTrue(finished.is_set())
            finally:
                release.set()
                if not task.done(): task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@unittest.skipUnless(sys.platform == 'linux', 'Linux process-group cases; Windows Job lifecycle is exercised separately')
class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='harness-lifecycle-test-')
        self.root = Path(self.temporary.name)
        self.root_patch = patch.object(client, '_tmpfs_root', return_value=self.root); self.root_patch.start()
        self.executor = client.HarnessExecutor(maximum=1)
        self.processes = []

    async def asyncTearDown(self):
        await self.executor.close(); self.root_patch.stop(); self.temporary.cleanup()

    async def spawn_sleep(self, directory):
        process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c',
                  'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)',
                  cwd=directory, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                  stderr=asyncio.subprocess.PIPE, start_new_session=True)
        self.processes.append(process)
        return process

    def assert_reaped(self):
        self.assertEqual(self.executor.active, 0); self.assertFalse(self.executor.children)
        self.assertFalse(self.executor.calls); self.assertEqual(list(self.root.iterdir()), [])
        for process in self.processes: self.assertIsNotNone(process.returncode)

    async def test_timeout_kills_ignoring_child_reaps_process_and_removes_session(self):
        with patch.object(self.executor, '_spawn', side_effect=self.spawn_sleep):
            with self.assertRaises(HTTPException) as caught:
                await self.executor.execute({}, timeout_seconds=.2)
        self.assertEqual(caught.exception.detail['code'], 'HARNESS_TIMEOUT'); self.assert_reaped()

    async def test_cancel_during_spawn_waits_for_ownership_then_reaps(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(directory):
            process = await self.spawn_sleep(directory); started.set(); await release.wait(); return process
        with patch.object(self.executor, '_spawn', side_effect=delayed):
            task = asyncio.create_task(self.executor.execute({}))
            await asyncio.wait_for(started.wait(), 2); task.cancel(); release.set()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assert_reaped()

    async def test_shutdown_cancels_work_and_full_pool_fails_without_queue(self):
        with patch.object(self.executor, '_spawn', side_effect=self.spawn_sleep):
            task = asyncio.create_task(self.executor.execute({}))
            while not self.processes: await asyncio.sleep(.01)
            with self.assertRaises(HTTPException) as caught: await self.executor.execute({})
            self.assertEqual(caught.exception.detail['code'], 'HARNESS_BUSY')
            await self.executor.close()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assert_reaped()

    async def test_cancel_reaps_orphaned_grandchild_in_own_process_group(self):
        async def spawn_family(directory):
            code = ('import pathlib,signal,subprocess,sys,time;'
                    'signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                    'child=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(30)"]);'
                    'pathlib.Path("grandchild.pid").write_text(str(child.pid));time.sleep(30)')
            process = await asyncio.create_subprocess_exec(sys.executable, '-I', '-c', code,
                      cwd=directory, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                      stderr=asyncio.subprocess.PIPE, start_new_session=True)
            self.processes.append(process); return process
        with patch.object(self.executor, '_spawn', side_effect=spawn_family):
            task = asyncio.create_task(self.executor.execute({}))
            async with asyncio.timeout(3):
                while not (records := list(self.root.glob('*/grandchild.pid'))): await asyncio.sleep(.01)
            grandchild = int(records[0].read_text())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assert_reaped(); self.assertFalse(Path('/proc', str(grandchild)).exists())

    async def test_worker_inherits_only_explicit_environment_and_no_key_argv(self):
        async def capture(*args, **kwargs): return args, kwargs
        with patch.dict(os.environ, {'DATABASE_PASSWORD': 'host-database-secret', 'DEEPSEEK_API_KEY': 'host-model-secret'}), \
                patch.object(asyncio, 'create_subprocess_exec', side_effect=capture):
            args, kwargs = await self.executor._spawn(self.root)
        self.assertNotIn('DATABASE_PASSWORD', kwargs['env']); self.assertNotIn('DEEPSEEK_API_KEY', kwargs['env'])
        self.assertNotIn('host-database-secret', repr((args, kwargs)))
        self.assertNotIn('host-model-secret', repr((args, kwargs)))
        self.assertTrue(kwargs['start_new_session']); self.assertIn('-I', args)


@unittest.skipUnless(sys.platform == 'win32', 'Native Windows Job lifecycle')
class WindowsLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from backend.platform_io import ensure_private_directory
        self.temporary = tempfile.TemporaryDirectory(prefix='harness-windows-test-')
        self.root = Path(self.temporary.name); ensure_private_directory(self.root)
        self.root_patch = patch.object(client, '_work_root', return_value=self.root); self.root_patch.start()
        self.executor = client.HarnessExecutor(maximum=1)
        self.processes = []

    async def asyncTearDown(self):
        await self.executor.close(); self.root_patch.stop(); self.temporary.cleanup()

    async def spawn_family(self, directory):
        from backend.windows_process import spawn_job_process, ProcessLimits
        system_root = os.environ.get('SystemRoot') or os.environ['SYSTEMROOT']
        code = ('import pathlib,subprocess,sys,time;'
                'child=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(30)"]);'
                'pathlib.Path("descendant.pid").write_text(str(child.pid));time.sleep(30)')
        process = await asyncio.to_thread(spawn_job_process, [sys.executable, '-I', '-c', code],
                    cwd=str(directory), env={'SystemRoot': system_root, 'PATH': str(Path(system_root) / 'System32')},
                    limits=ProcessLimits(memory_bytes=1024**3, active_processes=4, cpu_time_seconds=25))
        self.processes.append(process)
        return process

    def assert_reaped(self, observed):
        self.assertEqual(self.executor.active, 0); self.assertFalse(self.executor.children)
        self.assertFalse(self.executor.calls); self.assertEqual(list(self.root.iterdir()), [])
        self.assertFalse([pid for pid in observed if windows_pid_alive(pid)])

    async def test_timeout_reaps_job_and_releases_bounded_pipe_threads(self):
        with patch.object(self.executor, '_spawn', side_effect=self.spawn_family):
            task = asyncio.create_task(self.executor.execute({}, timeout_seconds=4))
            async with asyncio.timeout(3):
                while not list(self.root.glob('*/descendant.pid')):
                    if task.done(): await task
                    await asyncio.sleep(.01)
            observed = self.processes[0].process_ids(); self.assertGreaterEqual(len(observed), 2)
            with self.assertRaises(HTTPException) as caught: await task
            self.assertEqual(caught.exception.detail['code'], 'HARNESS_TIMEOUT')
        self.assert_reaped(observed)

    async def test_cancel_during_spawn_keeps_job_owned_until_close(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(directory):
            process = await self.spawn_family(directory); started.set(); await release.wait(); return process
        with patch.object(self.executor, '_spawn', side_effect=delayed):
            task = asyncio.create_task(self.executor.execute({}))
            await asyncio.wait_for(started.wait(), 5)
            observed = self.processes[0].process_ids()
            task.cancel(); release.set()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assert_reaped(observed)


@unittest.skipUnless(os.getenv('RUN_HARNESS_SDK_TESTS') == '1', 'real pinned SDK integration is opt-in')
class OfficialSDKTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests, self.mode = [], 'author'
        self.output_content = 'Revised'
        self.release = threading.Event()
        self.root = client._work_root(); self.before = set(self.root.glob('ontology-skill-*'))
        self.executor = client.HarnessExecutor()
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length']))); owner.requests.append(body)
                turn = len(owner.requests)
                if owner.mode == 'stall':
                    owner.release.wait(5); return
                if owner.mode == 'redirect':
                    self.send_response(307); self.send_header('Location', '/credential-target')
                    self.send_header('Content-Length', '0'); self.end_headers(); return
                if owner.mode == 'authentication_error':
                    raw = json.dumps({'type': 'error', 'error': {'type': 'authentication_error',
                        'message': 'SYNTHETIC_PRIVATE_PROVIDER_ERROR'}}).encode()
                    self.send_response(401); self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw); return
                if owner.mode == 'repair' and turn < 4:
                    patches = [{'steps': ['A model guessed the wrong item shape']},
                               {'inputs': {'type': 'object', 'properties': {'amount': {'type': 'number', '$ref': 'https://example.invalid/schema'}}}},
                               {'steps': [{'title': 'Validate synthetic input', 'instruction': 'Keep explicitly declared units unchanged.'}],
                                'inputs': {'type': 'object'}, 'outputs': {'type': 'object'},
                                'test_cases': [{'name': 'empty synthetic object', 'input': {}, 'expected_output': {}}]}]
                    patch_value = proposal(manifest_patch=patches[turn-1]); patch_value.pop('ontology_skill_proposal')
                    content = {'type': 'tool_use', 'id': f'proposal-{turn}', 'name': 'propose_skill_files', 'input': patch_value}
                elif owner.mode == 'repair': content = {'type': 'text', 'text': 'Done'}
                elif owner.mode == 'forbidden' and turn == 1:
                    content = {'type': 'tool_use', 'id': 'forbidden-1', 'name': 'bash', 'input': {'command': 'printf unauthorized'}}
                elif owner.mode == 'forbidden': content = {'type': 'text', 'text': 'Done'}
                elif owner.mode == 'loop' or turn == 1:
                    content = {'type': 'tool_use', 'id': f'read-{turn}', 'name': 'read_skill_file', 'input': {'path': 'README.md'}}
                elif turn == 2:
                    patch_value = proposal(changes=[{'path': 'SKILL.md', 'content': owner.output_content}]); patch_value.pop('ontology_skill_proposal')
                    content = {'type': 'tool_use', 'id': 'propose-1', 'name': 'propose_skill_files', 'input': patch_value}
                else: content = {'type': 'text', 'text': 'Done'}
                data = [{'type': 'message_start', 'message': {'id': 'test', 'model': 'deepseek-v4-flash', 'usage': {'input_tokens': 3, 'output_tokens': 0}}},
                        {'type': 'content_block_start', 'index': 0, 'content_block': content}, {'type': 'content_block_stop', 'index': 0},
                        {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use' if content['type'] == 'tool_use' else 'end_turn'}, 'usage': {'output_tokens': 1}},
                        {'type': 'message_stop'}]
                raw = ''.join('event: '+item['type']+'\ndata: '+json.dumps(item)+'\n\n' for item in data).encode()
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()

    async def asyncTearDown(self):
        await self.executor.close()
        self.release.set()
        await asyncio.to_thread(self.server.shutdown); self.server.server_close(); self.thread.join(2)
        self.assertFalse(self.executor.children); self.assertEqual(self.executor.active, 0)
        self.assertEqual(set(self.root.glob('ontology-skill-*')) - self.before, set())

    def payload(self):
        return {'instruction': 'Update the skill.', 'files': {**FILES, 'README.md': 'Safe reference'},
                'manifest': deepcopy(MANIFEST), 'metadata': {}, 'request_id': 'real-sdk-unit-test',
                'configuration': {'api_key': 'synthetic-local-test-only', 'base_url': 'http://127.0.0.1:'+str(self.server.server_port), 'model': 'deepseek-v4-flash'},
                'runtime_executable': str(client._runtime()), 'mode': 'author'}

    async def test_real_sdk_multi_step_authoring_only_exposes_two_tools_and_disables_upload(self):
        result = await self.executor.execute(self.payload())
        self.assertEqual(result['proposal']['files']['SKILL.md'], 'Revised')
        self.assertEqual(result['proposal']['files']['README.md'], 'Safe reference')
        self.assertEqual(result['proposal']['manifest']['dependencies'], MANIFEST['dependencies'])
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(result['usage'], {'model_responses': 3, 'complete': True,
            'input_tokens': 9, 'output_tokens': 3, 'cache_read_tokens': 0, 'cache_write_tokens': 0})
        for body in self.requests:
            self.assertEqual({tool['name'] for tool in body['tools']}, {'read_skill_file', 'propose_skill_files'})
            self.assertFalse([key for key in body if key.startswith('dsh_')])
            exposed = next(tool for tool in body['tools'] if tool['name'] == 'propose_skill_files')
            self.assertEqual(exposed['input_schema'], AUTHORING_SCHEMA)

    async def test_model_can_correct_rejected_manifest_shapes_in_the_same_run(self):
        self.mode = 'repair'
        result = await self.executor.execute(self.payload())
        self.assertEqual(len(self.requests), 4)
        returned = result['proposal']['manifest']
        portable = {key: returned[key] for key in worker.PORTABLE if key in returned}
        Manifest.model_validate(portable, strict=True)
        self.assertEqual(portable['inputs'], {'type': 'object'})
        self.assertEqual(portable['steps'][0]['title'], 'Validate synthetic input')
        for request_index in (1, 2):
            results = [part for message in self.requests[request_index]['messages']
                       if isinstance(message.get('content'), list) for part in message['content']
                       if part.get('type') == 'tool_result']
            self.assertTrue(results[-1]['is_error'])

    async def test_provider_authentication_failure_is_classified_without_provider_text(self):
        self.mode = 'authentication_error'
        with self.assertRaises(HTTPException) as caught:
            await self.executor.execute(self.payload())
        self.assertEqual(caught.exception.detail['code'], 'HARNESS_PROVIDER_AUTH')
        self.assertNotIn('SYNTHETIC_PRIVATE_PROVIDER_ERROR', repr(caught.exception.detail))
        self.assertEqual(len(self.requests), 1)

    async def test_model_loop_stops_before_ninth_model_request(self):
        self.mode = 'loop'
        with self.assertRaises(HTTPException): await self.executor.execute(self.payload())
        self.assertEqual(len(self.requests), 8)

    async def test_provider_redirect_is_not_followed_or_retried(self):
        self.mode = 'redirect'
        with self.assertRaises(HTTPException): await self.executor.execute(self.payload())
        self.assertEqual(len(self.requests), 1)

    async def test_model_cannot_invoke_builtin_shell_tool(self):
        self.mode = 'forbidden'
        with self.assertRaises(HTTPException): await self.executor.execute(self.payload())
        self.assertEqual(len(self.requests), 2)
        self.assertEqual({tool['name'] for tool in self.requests[0]['tools']}, {'read_skill_file', 'propose_skill_files'})
        messages = json.dumps(self.requests[-1]['messages'])
        self.assertIn('tool_result', messages)
        self.assertIn('is_error', messages)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux /proc assertion; Windows has a Job PID assertion below')
    async def test_cancel_real_runtime_reaps_worker_and_runtime_pids(self):
        self.mode = 'stall'
        task = asyncio.create_task(self.executor.execute(self.payload()))
        try:
            async with asyncio.timeout(10):
                while not self.requests:
                    if task.done():
                        await task  # Surface runtime startup failures promptly.
                        self.fail('Harness ended before reaching the test model')
                    await asyncio.sleep(.01)
            direct = [process.pid for process in self.executor.children]
            observed = set(direct)
            for entry in Path('/proc').iterdir():
                if not entry.name.isdigit(): continue
                try:
                    fields = (entry / 'stat').read_text().rsplit(') ', 1)[1].split()
                    if int(fields[2]) in direct: observed.add(int(entry.name))
                except (OSError, ValueError): pass
            self.assertGreaterEqual(len(observed), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertFalse([pid for pid in observed if Path('/proc', str(pid)).exists()])
        finally:
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @unittest.skipUnless(sys.platform == 'win32', 'Requires native Windows Job Objects')
    async def test_windows_cancel_real_runtime_reaps_all_job_pids(self):
        self.mode = 'stall'
        task = asyncio.create_task(self.executor.execute(self.payload()))
        try:
            async with asyncio.timeout(20):
                while not self.requests:
                    if task.done():
                        await task
                        self.fail('Harness ended before reaching the test model')
                    await asyncio.sleep(.01)
            observed = {pid for process in self.executor.children for pid in process.process_ids()}
            self.assertGreaterEqual(len(observed), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertFalse([pid for pid in observed if windows_pid_alive(pid)])
        finally:
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @unittest.skipUnless(sys.platform == 'win32', 'Native Windows memory-only session retention assertion')
    async def test_windows_does_not_persist_draft_prompt_output_or_key(self):
        markers = ['WINDOWS_DRAFT_PRIVATE_MARKER_701', 'WINDOWS_PROMPT_PRIVATE_MARKER_702',
                   'WINDOWS_MODEL_KEY_PRIVATE_MARKER_703', 'WINDOWS_OUTPUT_PRIVATE_MARKER_704']
        self.output_content = markers[3]
        payload = self.payload()
        payload['files']['SKILL.md'] = markers[0]
        payload['instruction'] = markers[1]
        payload['configuration']['api_key'] = markers[2]
        directories, inspected = [], []
        spawn, reap = self.executor._spawn, self.executor._reap
        async def capture(directory):
            directories.append(directory)
            return await spawn(directory)
        async def inspect(process):
            await reap(process)
            found, journals = [], []
            for path in directories[-1].rglob('*'):
                if not path.is_file(): continue
                if path.name == 'context.json' or path.name.endswith(('.jsonl', '.jsonl.zstd')):
                    journals.append(path.name)
                raw = path.read_bytes()
                if any(marker.encode() in raw for marker in markers): found.append(path.name)
            inspected.append(True)
            self.assertEqual(journals, []); self.assertEqual(found, [])
        with patch.object(self.executor, '_spawn', side_effect=capture), patch.object(self.executor, '_reap', side_effect=inspect):
            result = await self.executor.execute(payload)
        self.assertTrue(result['ok']); self.assertEqual(inspected, [True])
        self.assertFalse(directories[0].exists())


if __name__ == '__main__':
    unittest.main()
