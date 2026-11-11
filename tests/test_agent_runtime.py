"""Private agent tool-channel validation; actual identity checks run over HTTP QA.

The callback is deliberately a recorder here: these tests establish that malformed
or unauthenticated requests never reach the authorization/execution boundary.
"""
from __future__ import annotations

import http.client
import json
import unittest
from unittest.mock import Mock

from backend.agent_tools import SessionToolBridge


class AgentMemoryChannelTests(unittest.TestCase):
    def setUp(self):
        self.bridge = SessionToolBridge(None)
        self.bridge.invoke = Mock(return_value={'status': 'ok', 'synthetic': True})
        self.bridge.__enter__()

    def tearDown(self):
        owned_thread = self.bridge.thread
        self.bridge.close()
        self.assertFalse(owned_thread.is_alive())
        self.assertIsNone(self.bridge.server)

    def request(self, *, path='/tools', method='POST', token=None, raw=None, extra_headers=()):
        body = raw if raw is not None else b'{"name":"ontology_discover","arguments":{}}'
        connection = http.client.HTTPConnection('127.0.0.1', self.bridge.server.server_port, timeout=4)
        connection.putrequest(method, path)
        connection.putheader('Authorization', 'Bearer '+(self.bridge.token if token is None else token))
        connection.putheader('Content-Length', str(len(body)))
        for name, value in extra_headers:
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        result = response.status, response.read(), response.getheader('Cache-Control')
        connection.close()
        return result

    def test_valid_fixed_channel_dispatches_once_without_caching(self):
        status, body, cache = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {'status': 'ok', 'synthetic': True})
        self.assertEqual(cache, 'no-store')
        self.bridge.invoke.assert_called_once_with('ontology_discover', {})

    def test_authentication_paths_methods_and_transfer_framing_fail_before_callback(self):
        invalid = [dict(token='incorrect'), dict(path='/tools?forward=https://example.invalid'),
                   dict(path='http://example.invalid/tools'), dict(method='GET'), dict(method='CONNECT'),
                   dict(extra_headers=(('Authorization', 'Bearer '+self.bridge.token),)),
                   dict(extra_headers=(('Transfer-Encoding', 'chunked'),)),
                   dict(extra_headers=(('Proxy-Authorization', 'Bearer synthetic-upstream'),)),
                   dict(extra_headers=(('Content-Length', '2'),))]
        for options in invalid:
            with self.subTest(options=options):
                self.assertIn(self.request(**options)[0], (400, 403, 405))
        self.bridge.invoke.assert_not_called()

    def test_duplicate_json_extra_fields_and_oversize_body_fail_before_callback(self):
        invalid = [b'{"name":"ontology_discover","name":"bash","arguments":{}}',
                   b'{"name":"ontology_discover","arguments":{},"tenant_id":"sub-02"}',
                   b'{"name":"ontology_discover","arguments":{},"token":"synthetic"}',
                   b'[]', b'NaN', b'\xff\xff', b'{}'+b' '*65536]
        for body in invalid:
            with self.subTest(size=len(body)):
                status, raw, _ = self.request(raw=body)
                self.assertEqual(status, 400)
                self.assertNotIn(self.bridge.token.encode(), raw)
                self.assertNotIn(b'synthetic', raw)
        self.bridge.invoke.assert_not_called()


if __name__ == '__main__':
    unittest.main()
