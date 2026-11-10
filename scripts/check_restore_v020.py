"""Offline restore boundary checks; no running user workspace is modified."""
from __future__ import annotations
import copy
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.restore_backup import RestoreError, schema_columns, validate_archive, validate_snapshot, _load_json
from backend.maintenance import runtime_guard, require_maintenance


class RestoreBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.columns = schema_columns()

    def snapshot(self):
        return {'format':'ontology-mysql-workspace-v1','database':'MySQL','source_tenant':'sub-01',
                'tables':{'entities':[], 'relations':[], 'entity_ownership':[]}}

    def archive(self, name, *, mode=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            archive.writestr('data/mysql-workspace.json',json.dumps(self.snapshot()))
            info=zipfile.ZipInfo(name)
            if mode: info.external_attr = mode << 16
            archive.writestr(info,b'original content')
        stream.seek(0)
        return zipfile.ZipFile(stream)

    def test_source_registry(self):
        self.assertIn('dataset_sheets',self.columns)
        self.assertIn('reading_versions',self.columns)
        self.assertIn('special_deliveries',self.columns)
        self.assertNotIn('sec_users',self.columns)
        self.assertNotIn('federation_links',self.columns)
        self.assertEqual(self.columns['entities'],frozenset({'id','kind','title','summary','domain','org_id','status','source_kind','payload','created_at','updated_at','version'}))

    def test_same_tenant(self):
        validate_snapshot(self.snapshot(),'sub-01',self.columns)
        for tenant in ('sub-02',None,''):
            with self.assertRaises(RestoreError): validate_snapshot(self.snapshot(),tenant,self.columns)
        value=self.snapshot();value.pop('source_tenant')
        with self.assertRaises(RestoreError): validate_snapshot(value,'sub-01',self.columns)

    def test_control_tables_and_unknown_columns(self):
        for table in ('sec_users','sec_audit','sec_grants','federation_links','ontology_task_queue','entities; DROP TABLE entities'):
            value=self.snapshot();value['tables'][table]=[]
            with self.assertRaises(RestoreError): validate_snapshot(value,'sub-01',self.columns)
        value=self.snapshot();value['tables']['entities']=[{'id':'bad','unknown':'value'}]
        with self.assertRaises(RestoreError): validate_snapshot(value,'sub-01',self.columns)

    def test_embedded_paths_and_ownership(self):
        row={key:None for key in self.columns['entities']};row.update(id='MAT-1',kind='material',org_id='sub-01',payload='{}')
        for path in ('/etc/passwd','../sub-02/secret','data/uploads/../../secret','C:\\secret'):
            value=self.snapshot();bad=dict(row,payload=json.dumps({'source_path':path}));value['tables']['entities']=[bad]
            with self.assertRaises(RestoreError): validate_snapshot(value,'sub-01',self.columns)
        value=self.snapshot();value['tables']['entities']=[dict(row,org_id='sub-02')]
        with self.assertRaises(RestoreError): validate_snapshot(value,'sub-01',self.columns)
        ownership={key:None for key in self.columns['entity_ownership']};ownership.update(entity_id='MAT-1',tenant_id='sub-02')
        value=self.snapshot();value['tables']['entity_ownership']=[ownership]
        with self.assertRaises(RestoreError): validate_snapshot(value,'sub-01',self.columns)

    def test_zip_paths_and_types(self):
        with self.archive('data/uploads/material.txt') as archive: validate_archive(archive)
        for name in ('/absolute','../escape','data/uploads/../../escape','C:/escape','data\\uploads\\escape','data/runtime/ai.json','backend/main.py','data/source/legacy.txt'):
            with self.archive(name) as archive:
                with self.assertRaises(RestoreError): validate_archive(archive)
        for mode in (stat.S_IFLNK | 0o777,stat.S_IFIFO | 0o600):
            with self.archive('data/uploads/link',mode=mode) as archive:
                with self.assertRaises(RestoreError): validate_archive(archive)

    def test_zip_duplicate_and_json_duplicates(self):
        with self.archive('data/mysql-workspace.json') as archive:
            with self.assertRaises(RestoreError): validate_archive(archive)
        with self.assertRaises(RestoreError): _load_json(io.StringIO('{"source_tenant":"sub-01","source_tenant":"sub-02"}'))
        with self.assertRaises(RestoreError): _load_json(io.StringIO('{"value":NaN}'))

    def test_maintenance_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            previous=os.environ.get('ONTOLOGY_STORAGE_ROOT');os.environ['ONTOLOGY_STORAGE_ROOT']=str(Path(temp)/'tenants')
            try:
                with self.assertRaises(RuntimeError): require_maintenance()
                with runtime_guard(shared=True):
                    with runtime_guard(shared=True): pass
                    with self.assertRaises(RuntimeError):
                        with runtime_guard(shared=False): pass
                with runtime_guard(shared=False):
                    require_maintenance()
                    with self.assertRaises(RuntimeError):
                        with runtime_guard(shared=True): pass
            finally:
                if previous is None:os.environ.pop('ONTOLOGY_STORAGE_ROOT',None)
                else:os.environ['ONTOLOGY_STORAGE_ROOT']=previous


if __name__=='__main__': unittest.main(verbosity=2)
