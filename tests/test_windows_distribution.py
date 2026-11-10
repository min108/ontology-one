"""Offline checks for the native distribution's provenance and archive gates."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import build_windows_release as package


class DistributionPolicyTests(unittest.TestCase):
    def test_archive_members_reject_escape_links_and_drive_paths(self):
        for name in ('../escape', '/root/file', 'C:/file', 'root/../file', r'root\file'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                package.safe_member(zipfile.ZipInfo(name))
        link=zipfile.ZipInfo('root/link');link.external_attr=0o120777 << 16
        with self.assertRaises(ValueError):package.safe_member(link)
        self.assertEqual(package.safe_member(zipfile.ZipInfo('root/valid.dll')).as_posix(),'root/valid.dll')

    def test_release_requires_hash_and_rejects_wrong_or_malformed_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=Path(directory);(cache/'runtime.zip').write_bytes(b'fixed official fixture')
            item={'url':'https://www.python.org/runtime.zip','filename':'runtime.zip','sha256':None}
            with self.assertRaises(ValueError):package.download(dict(item),cache)
            for value in ('f'*63,'f'*64):
                with self.assertRaises(ValueError):package.download(dict(item,sha256=value),cache)
            with self.assertRaises(ValueError):package.download(dict(item,upstream_md5='f'*31),cache,bootstrap=True)
            candidate=dict(item)
            package.download(candidate,cache,bootstrap=True)
            self.assertEqual(candidate['sha256'],hashlib.sha256(b'fixed official fixture').hexdigest())

    def test_official_origins_reject_plaintext_userinfo_and_unlisted_hosts(self):
        for url in ('http://www.python.org/a','https://www.python.org.evil/a','https://user:pass@www.python.org/a','https://www.python.org:8443/a'):
            with self.subTest(url=url),self.assertRaises(ValueError):package.approved_url(url)

    def test_build_environment_does_not_forward_provider_or_repository_secrets(self):
        with patch.dict(package.os.environ,{'DEEPSEEK_API_KEY':'synthetic-private','GH_TOKEN':'synthetic-github'},clear=True):
            environment=package.build_environment()
        self.assertNotIn('DEEPSEEK_API_KEY',environment)
        self.assertNotIn('GH_TOKEN',environment)


if __name__=='__main__':unittest.main()
