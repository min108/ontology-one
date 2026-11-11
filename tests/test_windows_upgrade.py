"""Upgrade policy checks; native Windows runs the real pinned directory promotion."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend import platform_io as io
from backend.version import APP_VERSION, read_version
from ops import windows_upgrade as upgrade


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='版本升级 space ! ')
        self.root = Path(self.temp.name)
        self.source, self.target = self.root/'旧0.5', self.root/'新0.6'
        for root, version in ((self.source, '0.5.0'), (self.target, APP_VERSION)):
            io.ensure_private_directory(root/'runtime')
            io.atomic_write(root/'runtime/manifest.json', json.dumps({'application_version':version,
                'platform':'win_amd64','mysql':{'source_sha256':'a'*64}}).encode())
        self.local = io.ensure_private_directory(self.source/'data/local')
        io.ensure_private_directory(self.source/'data/mysql')
        self.settings = {'installation_id':'b'*32,'root_password':'c'*64,'runtime_password':'d'*64,
                         'api_port':8765,'mysql_port':18765}
        self.marker = {'installation_id':'b'*32,'version':'0.5.0'}
        for name, data in (('installation.json',self.settings), ('initialized.json',self.marker)):
            io.atomic_write(self.local/name,json.dumps(data).encode())
        for name in ('command.lock','service.lock','runtime-state.json'):
            io.atomic_write(self.local/name,b'ephemeral fixture')
        io.atomic_write(self.source/'data/mysql/synthetic.ibd',b'fixture-only unchanged bytes')
        self.stack=ExitStack()
        if os.name != 'nt':
            # Only this Windows primitive is substituted on Linux. Windows CI
            # must exercise its actual guarded, non-overwriting implementation.
            self.stack.enter_context(patch.object(io,'promote_private_directory',side_effect=lambda a,b:os.rename(a,b)))

    def tearDown(self):
        self.stack.close()
        self.temp.cleanup()

    def test_cold_copy_preserves_credentials_and_source_without_ephemeral_control(self):
        before={p.relative_to(self.source):p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        result=upgrade.upgrade(self.source,self.target)
        self.assertTrue(result['source_preserved'])
        self.assertEqual(before,{p.relative_to(self.source):p.read_bytes() for p in self.source.rglob('*') if p.is_file()})
        copied=self.target/'data/local'
        self.assertEqual(upgrade.read_json(copied/'installation.json',True),self.settings)
        self.assertFalse((copied/'runtime-state.json').exists())
        receipt=upgrade.read_json(copied/'upgrade-receipt.json',True)
        self.assertTrue(upgrade.compatible_marker(self.marker,self.settings,receipt))
        self.assertEqual((self.target/'data/mysql/synthetic.ibd').read_bytes(),b'fixture-only unchanged bytes')
        io.assert_private(copied/'installation.json')
        with self.assertRaises(upgrade.UpgradeError) as failure:upgrade.upgrade(self.source,self.target)
        self.assertEqual(failure.exception.code,'UPGRADE_TARGET_NOT_EMPTY')

    def test_running_source_lock_prevents_copy(self):
        with io.secure_open(self.local/'service.lock','rb') as stream,io.file_lock(stream,blocking=False):
            with self.assertRaises(upgrade.UpgradeError) as failure:upgrade.upgrade(self.source,self.target)
        self.assertEqual(failure.exception.code,'UPGRADE_SOURCE_BUSY')
        self.assertFalse((self.target/'data').exists())

    def test_invalid_version_database_runtime_and_receipt_are_rejected(self):
        self.assertFalse(upgrade.compatible_marker(self.marker,self.settings,{}))
        bad={'schema_version':1,'state':'copied','from_version':'0.5.0','to_version':APP_VERSION,
             'installation_id':'foreign','source_manifest_sha256':'a'*64}
        self.assertFalse(upgrade.compatible_marker(self.marker,self.settings,bad))
        manifest=upgrade.read_json(self.source/'runtime/manifest.json')
        manifest['mysql']['source_sha256']='e'*64
        io.atomic_write(self.source/'runtime/manifest.json',json.dumps(manifest).encode())
        with self.assertRaises(upgrade.UpgradeError) as failure:upgrade.upgrade(self.source,self.target)
        self.assertEqual(failure.exception.code,'UPGRADE_UNSUPPORTED')

    @unittest.skipUnless(os.name=='nt','Windows inherited DACL validation')
    def test_native_mysql_inherited_private_files_copy_but_foreign_ace_is_rejected(self):
        # Use native ordinary file creation, exactly as MySQL does, instead of
        # platform atomic_write's protected descriptor.
        nested=self.source/'data/mysql/vendor-created'
        nested.mkdir()
        inherited=nested/'row.ibd'
        inherited.write_bytes(b'inherited vendor fixture')
        with self.assertRaises(PermissionError):io.assert_private(inherited)
        io.assert_private(inherited,require_protected=False)
        from tests.windows_filesystem_fixture import make_world_accessible
        make_world_accessible(inherited)
        with self.assertRaises(PermissionError):upgrade.upgrade(self.source,self.target)
        self.assertFalse((self.target/'data').exists())
        inherited.unlink()
        inherited.write_bytes(b'inherited vendor fixture')
        result=upgrade.upgrade(self.source,self.target)
        self.assertTrue(result['source_preserved'])
        io.assert_private(self.target/'data/mysql/vendor-created/row.ibd')

    def test_reparse_source_is_refused_and_partial_copy_never_promoted(self):
        outside=io.ensure_private_directory(self.root/'outside')
        io.atomic_write(outside/'private.txt',b'external sentinel')
        link=self.source/'data/foreign'
        if os.name=='nt':
            from tests.windows_filesystem_fixture import make_junction
            make_junction(link,outside)
            try:
                with self.assertRaises((OSError,upgrade.UpgradeError)):upgrade.upgrade(self.source,self.target)
            finally:
                os.rmdir(link)
        else:
            link.symlink_to(outside,target_is_directory=True)
            with self.assertRaises((OSError,upgrade.UpgradeError)):upgrade.upgrade(self.source,self.target)
            link.unlink()
        self.assertFalse((self.target/'data').exists())
        self.assertEqual((outside/'private.txt').read_bytes(),b'external sentinel')
        self.assertFalse(list(self.target.glob('upgrade-copy-*')))


class ApplicationVersionTests(unittest.TestCase):
    def test_version_is_strict_and_missing_version_is_not_silently_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with self.assertRaises(FileNotFoundError):read_version(root)
            for value in ('../0.6.0','0.06.0','v0.6.0','0.6.0\n0.5.0'):
                (root/'VERSION').write_text(value)
                with self.assertRaises(ValueError):read_version(root)
            (root/'VERSION').write_text('0.6.0\n')
            self.assertEqual(read_version(root),'0.6.0')


if __name__=='__main__':unittest.main()
