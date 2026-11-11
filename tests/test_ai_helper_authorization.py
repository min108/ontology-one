"""AI-assisted writes must preserve revocation and avoid stale partial results."""
from contextlib import ExitStack
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from backend import ai, datasets, placement
from tests.test_ai_runtime import Fixture


class HelperAuthorizationTests(Fixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.configured()
        self.entity = {'id': 'asset', 'kind': 'data', 'title': '合成资产', 'summary': '合成测试',
                       'payload': {}, 'version': 1, 'domain': '测试', 'org_id': 'test-ai'}

    def setup_placement(self):
        conn = MagicMock()
        self.stack.enter_context(patch.object(placement, 'connect', return_value=conn))
        self.stack.enter_context(patch.object(placement, 'source_asset', return_value=self.entity))
        self.stack.enter_context(patch.object(placement, 'candidates_for', return_value=[
            {'id': 'candidate', 'title': '合成口径', 'kind': 'rule', 'version': 1, 'excerpt': '只用于合成验证的业务口径'}]))
        return conn

    def setup_dataset(self):
        conn = MagicMock(); conn.execute.return_value.fetchone.return_value = None
        context = MagicMock(); context.__enter__.return_value = conn
        self.stack.enter_context(patch.object(datasets, 'db', return_value=context))
        self.stack.enter_context(patch.object(datasets, 'get_entity', return_value=self.entity))
        sheet = {'name': '合成表', 'columns': [{'name': 'amount'}], 'grain': '合成记录', 'grain_confirmed': True}
        self.stack.enter_context(patch.object(datasets, 'sheets_for', return_value=[sheet]))
        self.stack.enter_context(patch.object(datasets, '_sheet_context', return_value=sheet))
        self.stack.enter_context(patch.object(datasets, 'mappings_for', return_value=[{'column_name': 'amount', 'status': 'unmapped'}]))
        self.stack.enter_context(patch.object(datasets, '_current_standards', return_value=[]))
        self.stack.enter_context(patch.object(datasets, '_field_profile', return_value={}))
        self.stack.enter_context(patch.object(datasets, 'rank_candidates', return_value={
            'items': [], 'context': {'scenarios': [], 'object_kind': ''},
            'dictionary_count': 0, 'total': 0, 'truncated': False}))
        return conn

    def assert_no_insert(self, conn):
        self.assertFalse(any('INSERT' in str(call.args[0]).upper() for call in conn.execute.call_args_list))

    async def test_placement_preserves_authorization_errors_instead_of_wrapping_502(self):
        conn = self.setup_placement()
        for code in (401, 403, 409):
            with self.subTest(status=code), patch.object(ai, 'complete', new=AsyncMock(side_effect=HTTPException(code, '授权已失效'))), self.assertRaises(HTTPException) as caught:
                await placement.suggest('asset', placement.SuggestRequest(expected_version=1))
            self.assertEqual(caught.exception.status_code, code)
            self.assert_no_insert(conn)

    async def test_placement_rechecks_identity_after_model_result_before_write(self):
        conn = self.setup_placement()
        async def revoke(*args, **kwargs):
            self.disabled = True
            return json.dumps({'suggestions': []})
        with patch.object(ai, 'complete', side_effect=revoke), self.assertRaises(HTTPException) as caught:
            await placement.suggest('asset', placement.SuggestRequest(expected_version=1))
        self.assertEqual(caught.exception.status_code, 401)
        self.assert_no_insert(conn)

    async def test_dataset_does_not_store_partial_results_after_authorization_failure(self):
        conn = self.setup_dataset()
        for code in (401, 403, 409):
            with self.subTest(status=code), patch.object(ai, 'complete', new=AsyncMock(side_effect=HTTPException(code, '授权已失效'))), self.assertRaises(HTTPException) as caught:
                await datasets.recognize_mappings('asset', datasets.RecognitionBody(sheet_name='合成表'))
            self.assertEqual(caught.exception.status_code, code)
            self.assert_no_insert(conn)

    async def test_dataset_supplemental_model_call_preserves_revocation(self):
        conn = self.setup_dataset()
        first = json.dumps({'items': [{'column_name': 'amount', 'decision': 'no_match', 'search_terms': ['合成金额']} ]})
        with patch.object(ai, 'complete', new=AsyncMock(side_effect=[first, HTTPException(403, '授权已失效')])), self.assertRaises(HTTPException) as caught:
            await datasets.recognize_mappings('asset', datasets.RecognitionBody(sheet_name='合成表'))
        self.assertEqual(caught.exception.status_code, 403)
        self.assert_no_insert(conn)

    async def test_dataset_rechecks_current_identity_before_review_write(self):
        conn = self.setup_dataset()
        async def revoke(*args, **kwargs):
            self.disabled = True
            return json.dumps({'items': [{'column_name': 'amount', 'decision': 'matched', 'standard_id': None}]})
        with patch.object(ai, 'complete', side_effect=revoke), self.assertRaises(HTTPException) as caught:
            await datasets.recognize_mappings('asset', datasets.RecognitionBody(sheet_name='合成表'))
        self.assertEqual(caught.exception.status_code, 401)
        self.assert_no_insert(conn)


if __name__ == '__main__':
    unittest.main()
