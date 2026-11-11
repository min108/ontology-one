"""Boundary regression for foreign published content, not permission emulation."""
import unittest
from unittest.mock import patch
from backend.agent_tools import SessionToolBridge
from backend.security.context import Principal


class AgentContentBoundaryTests(unittest.TestCase):
    def test_foreign_hidden_content_is_not_returned_even_after_authorized_detail(self):
        principal = Principal('user', 'user', 'hq', 'hq', 'admin', True, 'session', 1, 'test_hq')
        full = {'id': 'knowledge', 'kind': 'knowledge', 'title': 'Published knowledge', 'summary': 'summary',
                'version': 1, 'status': 'published', 'payload': {'hide_in_graph': True, 'body': 'hidden-synthetic-body'}}
        bridge = SessionToolBridge(principal)
        with patch.object(bridge, '_current', return_value=principal), \
             patch('backend.agent_tools.append_audit'), \
             patch('backend.federation.entity_detail', return_value={'entity': full}), \
             patch('backend.federation._scope') as scope:
            result = bridge.invoke_local('ontology_read', {'entity_id': 'knowledge', 'tenant_id': 'sub-01'})
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error']['code'], 'FORBIDDEN')
        self.assertIsNone(result['data'])
        self.assertNotIn('hidden-synthetic-body', str(result))
        scope.assert_not_called()
        self.assertEqual(bridge.group_versions, {})

    def test_foreign_visible_body_is_allowlisted_and_records_real_scope_boundary(self):
        principal = Principal('user', 'user', 'hq', 'hq', 'admin', True, 'session', 1, 'test_hq')
        full = {'id': 'knowledge', 'kind': 'knowledge', 'title': 'Published knowledge', 'summary': 'summary',
                'version': 3, 'status': 'published', 'payload': {'body': 'public-synthetic-body',
                    'config': {'api_key': 'synthetic-private'}, 'rows': [{'value': 'synthetic-row'}],
                    'fields': [{'name': 'amount', 'unit': '元', 'password': 'synthetic-private'}]}}
        bridge = SessionToolBridge(principal)
        with patch.object(bridge, '_current', return_value=principal), \
             patch('backend.agent_tools.append_audit'), \
             patch('backend.federation.entity_detail', return_value={'entity': full}), \
             patch('backend.federation._scope', return_value=(principal, [{'id': 'sub-01', 'visibility': 'private'}])):
            result = bridge.invoke_local('ontology_read', {'entity_id': 'knowledge', 'tenant_id': 'sub-01'})
        self.assertEqual(result['content'], {'body': 'public-synthetic-body', 'fields': [{'name': 'amount', 'unit': '元'}]})
        self.assertNotIn('payload', result['entity'])
        self.assertNotIn('synthetic-private', str(result))
        self.assertNotIn('synthetic-row', str(result))
        self.assertEqual(bridge.group_versions, {'sub-01/': 2, 'sub-01/knowledge': 3})
