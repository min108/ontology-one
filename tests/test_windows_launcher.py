"""Portable launcher policy tests; native process/MySQL checks run on Windows."""
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from ops import windows_launcher as launcher


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='平台 space! ')
        self.root = Path(self.directory.name)
        self.local = self.root / 'data' / 'local'
        self.patches = [patch.object(launcher, name, value) for name, value in {
            'ROOT': self.root, 'LOCAL': self.local, 'SETTINGS': self.local / 'installation.json',
            'STATE': self.local / 'runtime-state.json', 'READY': self.local / 'initialized.json'}.items()]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        self.directory.cleanup()

    def test_credentials_are_private_unique_and_stable(self):
        first = launcher.get_installation()
        self.assertEqual(first, launcher.get_installation())
        self.assertNotEqual(first['root_password'], first['runtime_password'])
        self.assertEqual(len(first['root_password']), 64)
        if os.name != 'nt':
            self.assertEqual((self.local / 'installation.json').stat().st_mode & 0o777, 0o600)

    def test_missing_credentials_never_reinitialize_existing_database(self):
        (self.root / 'data' / 'mysql').mkdir(parents=True)
        with self.assertRaises(launcher.LaunchError) as error:
            launcher.get_installation()
        self.assertEqual(error.exception.code, 'LOCAL_STATE_INVALID')

    def test_foreign_credentials_and_shell_injection_environment_are_not_inherited(self):
        with patch.dict(os.environ, {'MYSQL_PASSWORD': 'foreign', 'DEEPSEEK_API_KEY': 'foreign',
            'ONTOLOGY_BOOTSTRAP_PASSWORD': 'foreign', 'PYTHONPATH': 'foreign', 'NODE_OPTIONS': 'foreign'}, clear=False):
            env = launcher.base_environment()
        for name in ('MYSQL_PASSWORD', 'DEEPSEEK_API_KEY', 'ONTOLOGY_BOOTSTRAP_PASSWORD', 'PYTHONPATH', 'NODE_OPTIONS'):
            self.assertNotIn(name, env)
        self.assertIn(str(self.root / 'runtime' / 'python'), env['PATH'])

    def test_explicit_deployment_proxy_and_ca_are_preserved_without_tls_bypass(self):
        configured = {'HTTP_PROXY': 'http://proxy.example:8080', 'https_proxy': 'http://proxy.example:8080',
            'NO_PROXY': 'localhost,127.0.0.1', 'NODE_EXTRA_CA_CERTS': 'C:/trusted/company.pem',
            'SSL_CERT_FILE': 'C:/trusted/company.pem', 'REQUESTS_CA_BUNDLE': 'C:/trusted/company.pem',
            'ONTOLOGY_SOURCE_CREDENTIALS_FILE': 'C:/private/source-credentials.json',
            'NODE_TLS_REJECT_UNAUTHORIZED': '0', 'NODE_OPTIONS': '--require untrusted.js'}
        with patch.dict(os.environ, configured, clear=True):
            env = launcher.base_environment()
        for key in configured:
            if key in ('NODE_TLS_REJECT_UNAUTHORIZED', 'NODE_OPTIONS'):
                self.assertNotIn(key, env)
            else:
                self.assertEqual(env[key], configured[key])

    def test_application_uses_the_packaged_skill_python_without_root_database_secret(self):
        settings = launcher.get_installation()
        env = launcher.application_environment(settings)
        self.assertEqual(env['ONTOLOGY_SKILL_PYTHON_ROOT'], str(self.root / 'runtime' / 'skill-python'))
        self.assertEqual(env['MYSQL_PASSWORD'], settings['runtime_password'])
        self.assertNotIn('ONTOLOGY_RUNTIME_DB_PASSWORD', env)
        self.assertNotIn(settings['root_password'], env.values())

    def test_ports_and_runtime_paths_cannot_escape_policy(self):
        for value in (0, 80, 65536, '8765', True):
            with self.assertRaises(launcher.LaunchError): launcher.valid_port(value)
        for value in ('../python.exe', 'runtime/../python.exe', 'C:/python.exe', '/runtime/python.exe', 'runtime\\python.exe'):
            with self.assertRaises(launcher.LaunchError): launcher.runtime_path(value)
        with self.assertRaises(launcher.LaunchError): launcher.get_installation(8765, 8765)

    def test_busy_port_is_reported_without_touching_listener(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen(1)
            with self.assertRaises(launcher.LaunchError) as error:
                launcher.assert_port_free(listener.getsockname()[1])
            self.assertEqual(error.exception.code, 'PORT_BUSY')
            self.assertNotEqual(listener.fileno(), -1)

    def test_control_channel_authenticates_token_and_installation(self):
        settings = launcher.get_installation()
        supervisor = object.__new__(launcher.Supervisor)
        supervisor.settings, supervisor.token = settings, 'a' * 64
        supervisor.state_lock, supervisor.children = threading.Lock(), {}
        supervisor.phase, supervisor.error = 'ready', None
        supervisor.finished, supervisor.stop_event = threading.Event(), threading.Event()
        supervisor.channel = socket.socket()
        supervisor.channel.bind(('127.0.0.1', 0)); supervisor.channel.listen(4); supervisor.channel.settimeout(.1)
        thread = threading.Thread(target=supervisor.serve_control)
        thread.start()
        launcher.write_json(launcher.STATE, {'root': str(self.root), 'installation_id': settings['installation_id'],
            'token': supervisor.token, 'control_port': supervisor.channel.getsockname()[1]})
        try:
            for token, identity in (('wrong', settings['installation_id']), (supervisor.token, 'foreign')):
                with socket.create_connection(supervisor.channel.getsockname(), timeout=2) as client:
                    client.sendall(json.dumps({'token': token, 'installation_id': identity, 'operation': 'stop'}).encode() + b'\n')
                    self.assertEqual(client.recv(1024), b'')
                self.assertFalse(supervisor.stop_event.is_set())
            status = launcher.control('status')
            self.assertTrue(status['ready'])
            self.assertNotIn('token', status)
            self.assertNotIn('root_password', status)
            self.assertTrue(launcher.control('stop')['ready'])
            self.assertTrue(supervisor.stop_event.is_set())
        finally:
            supervisor.finished.set(); supervisor.channel.close(); thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_copied_state_cannot_control_another_root(self):
        settings = launcher.get_installation()
        launcher.write_json(launcher.STATE, {'root': str(self.root / 'another'), 'installation_id': settings['installation_id'],
            'token': 'a' * 64, 'control_port': 12345})
        with patch.object(socket, 'create_connection') as connect:
            self.assertIsNone(launcher.control('stop'))
            connect.assert_not_called()


class WindowsEntryEncodingTests(unittest.TestCase):
    def test_windows_entries_preserve_unicode_paths_without_cmd_reexpansion(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('启动平台.bat', '关闭平台.bat'):
            raw = (root / name).read_bytes()
            raw.decode('ascii')
            self.assertIn(b'DisableDelayedExpansion', raw)
            self.assertNotIn(b'\n', raw.replace(b'\r\n', b''))
            self.assertIn(b'"%~dp0ops\\windows_entry.ps1"', raw)
        raw = (root / 'ops' / 'windows_entry.ps1').read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        self.assertIn('Set-Location -LiteralPath', raw.decode('utf-8-sig'))


if __name__ == '__main__':
    unittest.main()
