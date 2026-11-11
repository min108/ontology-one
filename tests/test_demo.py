"""Synthetic fixtures, no-I/O adapter and demonstration governance boundaries."""
import copy
import json
import subprocess
import sys
import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from backend import demo, demo_data, source_adapters as adapters, sources
from backend.security.context import Principal, use_context

P = Principal(user_id='admin', username='admin', home_tenant_id='sub-01', tenant_id='sub-01', role='admin',
              is_superadmin=False, session_id='session', auth_version=1, database_name='unit_test')


class DemoContracts(unittest.TestCase):
    def source(self):
        return {'id': demo_data.SOURCE_ID, 'kind': 'demo', 'config': copy.deepcopy(demo_data.SOURCE_CONFIG)}

    def test_demo_adapter_uses_no_credentials_or_network(self):
        with patch.object(adapters, '_resolve_credentials', side_effect=AssertionError('credential access')), \
                patch.object(adapters, '_mcp_operation', side_effect=AssertionError('network access')):
            resources = adapters.discover(self.source())
            self.assertEqual({r['key'] for r in resources}, {'contracts', 'payments'})
            contracts = next(r for r in resources if r['key'] == 'contracts')
            result = adapters.query(self.source(), contracts, {'columns': ['contract_id', 'amount_yuan'],
                'filters': [{'column': 'amount_yuan', 'op': 'gt', 'value': 100000}], 'order_by': [{'column': 'amount_yuan', 'direction': 'asc'}], 'limit': 2})
            self.assertEqual(result['rows'], [{'contract_id': 'DEMO-C002', 'amount_yuan': 150000}, {'contract_id': 'DEMO-C004', 'amount_yuan': 200000}])
            self.assertFalse(result['truncated'])

    def test_demo_adapter_rejects_external_address_arbitrary_id_and_schema(self):
        source = self.source()
        for config in ({**demo_data.SOURCE_CONFIG, 'endpoint': 'https://example.invalid'}, {'fixture': demo_data.PACK, 'allowed_objects': ['secret']}, {}):
            with self.assertRaises(adapters.SourceFailure):
                adapters.discover({**source, 'config': config})
        with self.assertRaises(adapters.SourceFailure):
            adapters.discover({**source, 'id': 'other-source'})
        resource = adapters.discover(source)[0]
        resource['columns'][0]['comment'] = 'changed'
        with self.assertRaises(adapters.SourceFailure):
            adapters.query(source, resource, {'columns': ['contract_id'], 'limit': 1})
        with self.assertRaises(ValidationError):
            sources.SourceCreate(title='untrusted', kind='demo', config=demo_data.SOURCE_CONFIG, credential_ref='anything')

    def test_demo_nulls_projection_limits_and_joins_are_independently_checkable(self):
        contracts = adapters.discover(self.source())[0]
        nulls = adapters.query(self.source(), contracts, {'columns': ['contract_id', 'attachment_ready'],
            'filters': [{'column': 'attachment_ready', 'op': 'is_null'}], 'limit': 2})
        self.assertEqual(nulls['rows'], [{'contract_id': 'DEMO-C003', 'attachment_ready': None}])
        limited = adapters.query(self.source(), contracts, {'columns': ['contract_id'], 'limit': 1})
        self.assertEqual(limited['rows'], [{'contract_id': 'DEMO-C001'}]); self.assertTrue(limited['truncated'])
        sums = {row['contract_id']: 0 for row in demo_data.CONTRACTS}
        for row in demo_data.PAYMENTS:
            sums[row['contract_id']] += row['payment_yuan']
        self.assertEqual(sums, {'DEMO-C001': 40000, 'DEMO-C002': 150000, 'DEMO-C003': 0, 'DEMO-C004': 80000})
        self.assertEqual([sums[row['contract_id']] for row in demo_data.CONTRACTS], [row['paid_yuan'] for row in demo_data.CONTRACTS])

    def test_fixture_skill_schema_and_independent_arithmetic_cases(self):
        from backend.skills import Manifest, _coherent_files
        Manifest.model_validate(demo_data.SKILL_MANIFEST)
        _, package = _coherent_files(copy.deepcopy(demo_data.SKILL_FILES), copy.deepcopy(demo_data.SKILL_MANIFEST))
        self.assertTrue(package['valid'])
        script = demo_data.SKILL_FILES['scripts/balance.py']
        for value, expected in [({'amount': 200000, 'paid': 80000}, {'unpaid': 120000, 'overpaid': False}),
                                ({'amount': 12.5, 'paid': 20}, {'unpaid': 0, 'overpaid': True})]:
            result = subprocess.run([sys.executable, '-I', '-c', script], input=json.dumps(value), capture_output=True, text=True, timeout=5, check=True)
            self.assertEqual(json.loads(result.stdout), expected)

    def test_loading_requires_explicit_confirmation_and_home_admin(self):
        for value in ({'confirm': 1}, {'confirm': True, 'tenant_id': 'other'}):
            with self.assertRaises(ValidationError): demo.Confirm.model_validate(value)
        for principal in (replace(P, role='modeler'), replace(P, readonly=True), replace(P, tenant_id='other'), replace(P, identity_kind='application')):
            with self.subTest(principal=principal), use_context(principal), patch.object(demo, 'assert_principal_current', side_effect=lambda p, **kw: p):
                with self.assertRaises(HTTPException) as error: demo._confirmed(demo.Confirm(confirm=True))
                self.assertEqual(error.exception.status_code, 403)
        with use_context(P), patch.object(demo, 'assert_principal_current', side_effect=lambda p, **kw: p):
            with self.assertRaises(HTTPException) as error: demo._confirmed(demo.Confirm(confirm=False))
            self.assertEqual(error.exception.status_code, 422)

    def test_changed_or_externally_referenced_content_blocks_archive(self):
        receipt = {'entity_ids': ['DEMO07-A'], 'source_ids': ['source'], 'snapshots': {'entities': {'sha256': 'old'}}, 'files': []}
        with patch.object(demo, '_snapshot', return_value={'entities': {'sha256': 'new'}}), patch.object(demo, '_external_references', return_value=False):
            self.assertEqual(demo._changes(Mock(), receipt), ['entities'])
        with patch.object(demo, '_snapshot', return_value=receipt['snapshots']), patch.object(demo, '_external_references', return_value=True):
            self.assertEqual(demo._changes(Mock(), receipt), ['external_references'])

    def test_sample_downloads_are_fixed_not_path_or_network_inputs(self):
        with use_context(P), patch.object(demo, 'assert_principal_current', side_effect=lambda p, **kw: p):
            sample = demo.sample_file('contracts.csv')
            self.assertIn(b'DEMO-C001', sample.body)
            self.assertIn('no-store', sample.headers['cache-control'])
            for name in ('../secret', 'file:///etc/passwd', 'https://example.invalid'):
                with self.assertRaises(HTTPException) as error: demo.sample_file(name)
                self.assertEqual(error.exception.status_code, 404)

    def test_existing_upload_is_never_overwritten_or_added_to_cleanup(self):
        written = []
        with patch.object(demo, 'write_bytes', side_effect=FileExistsError('/private/original.md')):
            with self.assertRaises(HTTPException) as error:
                demo._write_file('/private/original.md', b'synthetic', written)
        self.assertEqual(error.exception.status_code, 409)
        self.assertNotIn('/private', str(error.exception.detail))
        self.assertEqual(written, [])


if __name__ == '__main__':
    unittest.main()
