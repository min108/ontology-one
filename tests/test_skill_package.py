"""Agent Skills compatibility and hostile archive boundaries; no scripts execute."""
from __future__ import annotations

import io
import json
from pathlib import Path
import stat
import struct
import tempfile
import unittest
import warnings
import zipfile

from backend import skill_package as packages


def skill(name='sample-skill', extra='', body='# Steps\nRead authorized metadata.'):
    return f'---\nname: {name}\ndescription: Inspect business metadata when reviewing a source.\n{extra}---\n{body}\n'


def files(**additional):
    return {'SKILL.md': skill(), **additional}


def archive(entries, compression=zipfile.ZIP_STORED):
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(output, 'w', compression=compression) as stream:
            for path, content in entries:
                stream.writestr(path, content.encode() if isinstance(content, str) else content)
    return output.getvalue()


class SkillPackageTests(unittest.TestCase):
    def test_windows_illegal_characters_and_device_aliases_are_portably_rejected(self):
        for name in ['bad<name.txt', 'bad>name.txt', 'bad"name.txt', 'bad|name.txt', 'bad?name.txt', 'bad*name.txt',
                     'COM¹.txt', 'LPT².md', 'com³.py', 'NUL .txt', 'ＡＵＸ.txt']:
            with self.subTest(name=name):
                self.assertFalse(packages.validate_files(files(**{'assets/' + name: 'sample'}))['valid'])

    def assertInvalid(self, package, code):
        result = packages.validate_files(package)
        self.assertFalse(result['valid'])
        self.assertIn(code, [issue['code'] for issue in result['errors']])
        return result

    def assertZipInvalid(self, data, codes):
        with self.assertRaises(packages.SkillPackageError) as caught:
            packages.read_zip(data)
        self.assertIn(caught.exception.code, {codes} if isinstance(codes, str) else codes)
        return caught.exception

    def test_open_format_metadata_and_unicode_name_keep_string_scalars(self):
        package = {'SKILL.md': skill('财务-analysis', 'metadata:\n  version: 1.0\n  enabled: yes\nlicense: Apache-2.0\nallowed-tools: Read Bash(git:*)\n')}
        result = packages.validate_files(package, expected_name='财务-analysis')
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(result['frontmatter']['metadata'], {'version': '1.0', 'enabled': 'yes'})
        self.assertIn('TOOLS_DECLARATION_ONLY', [issue['code'] for issue in result['warnings']])
        self.assertEqual(len(result['digest']), 64)

    def test_frontmatter_boundaries_and_original_body_separators(self):
        package = {'SKILL.md': skill(body='# Instruction\n---\nKeep body separator.')}
        result = packages.validate_files(package)
        self.assertTrue(result['valid'])
        self.assertIn('\n---\n', result['body'])
        self.assertInvalid({'SKILL.md': 'name: absent'}, 'FRONTMATTER_MISSING')
        self.assertInvalid({'SKILL.md': '---\nname: open'}, 'FRONTMATTER_UNCLOSED')
        self.assertInvalid({'SKILL.md': '---\n- item\n---\n'}, 'FRONTMATTER_TYPE')
        self.assertInvalid({'SKILL.md': skill(extra='metadata: [unfinished\n')}, 'FRONTMATTER_SYNTAX')

    def test_name_description_and_optional_types_are_explicit(self):
        for name in ['UPPER', '-lead', 'tail-', 'a--b', 'a_b', 'x' * 65, 'ａｂｃ']:
            with self.subTest(name=name):
                self.assertInvalid({'SKILL.md': skill(name)}, 'SKILL_NAME')
        result = packages.validate_files(files(), expected_name='different')
        self.assertEqual(result['errors'][0]['code'], 'SKILL_DIRECTORY')
        self.assertInvalid({'SKILL.md': skill(extra='unknown: value\n')}, 'FRONTMATTER_FIELD')
        self.assertInvalid({'SKILL.md': skill(extra='compatibility: ' + 'x' * 501 + '\n')}, 'FRONTMATTER_VALUE')
        self.assertInvalid({'SKILL.md': skill(extra='metadata:\n  nested:\n    key: value\n')}, 'FRONTMATTER_METADATA')
        self.assertInvalid({'SKILL.md': skill().replace('Inspect business metadata when reviewing a source.', ' ')}, 'SKILL_DESCRIPTION')

    def test_yaml_duplicates_aliases_tags_and_depth_are_never_accepted(self):
        for extra, code in [('name: other\n', 'FRONTMATTER_DUPLICATE'),
                            ('metadata:\n  version: one\n  version: two\n', 'FRONTMATTER_DUPLICATE'),
                            ('metadata: &values\n  version: one\n', 'FRONTMATTER_UNSAFE'),
                            ('metadata: *values\n', 'FRONTMATTER_UNSAFE'),
                            ('license: !!python/object/apply:os.system [echo unsafe]\n', 'FRONTMATTER_UNSAFE'),
                            ('metadata: ' + '[' * 9 + 'x' + ']' * 9 + '\n', 'FRONTMATTER_COMPLEXITY')]:
            with self.subTest(code=code):
                self.assertInvalid({'SKILL.md': skill(extra=extra)}, code)
        result = self.assertInvalid({'SKILL.md': skill(extra='metadata:\n  <<: value\n')}, 'FRONTMATTER_DUPLICATE')
        self.assertNotIn('value', str(result['errors']))

    def test_json_duplicate_keys_nonfinite_and_deep_structures_rejected(self):
        for value, code in [('{"x":1,"x":2}', 'JSON_DUPLICATE'), ('{"x":NaN}', 'JSON_VALUE'),
                            ('{"x":1e10000}', 'JSON_VALUE'), ('[', 'JSON_SYNTAX'),
                            ('[' * 17 + '0' + ']' * 17, 'JSON_COMPLEXITY')]:
            with self.subTest(code=code):
                self.assertInvalid(files(**{'assets/example.json': value}), code)
        self.assertTrue(packages.validate_files(files(**{'assets/example.json': '{"x":[1,true,null]}'}))['valid'])

    def test_python_syntax_checked_without_executing_and_other_languages_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / 'must-not-exist'
            script = f'from pathlib import Path\nPath({str(marker)!r}).write_text("forbidden")\n'
            package = files(**{'scripts/check.py': script, 'scripts/check.sh': 'echo sample\n', 'scripts/check.js': 'console.log("sample");\n'})
            self.assertTrue(packages.validate_files(package)['valid'])
            self.assertFalse(marker.exists())
        for script in ['def invalid(:\n', 'return 1\n', 'from __future__ import nonexistent_feature\n']:
            self.assertInvalid(files(**{'scripts/invalid.py': script}), 'PYTHON_SYNTAX')

    def test_optional_manifest_is_not_required_but_declared_reference_is_checked(self):
        self.assertTrue(packages.validate_files(files())['valid'])
        package = {'SKILL.md': skill(extra='metadata:\n  ontology-one.manifest: assets/ontology-one.json\n'),
                   'assets/ontology-one.json': '{"custom":"the domain validates governance"}'}
        self.assertTrue(packages.validate_files(package)['valid'])
        del package['assets/ontology-one.json']
        self.assertInvalid(package, 'MANIFEST_REFERENCE')
        self.assertTrue(packages.validate_files(files(**{'other-notes/extra.txt': 'additional open-format text'}))['valid'])

    def test_known_credentials_and_private_files_do_not_echo_secrets(self):
        token = 'onto_app_' + 'A' * 43
        for path, body, code in [('references/example.md', token, 'CREDENTIAL_DETECTED'),
                                 ('references/private.md', '-----BEGIN RSA PRIVATE KEY-----\nabc', 'CREDENTIAL_DETECTED'),
                                 ('assets/id_rsa', 'credential', 'SECRET_FILE'),
                                 ('assets/server.pem', 'credential', 'SECRET_FILE'),
                                 ('assets/' + token + '.txt', 'x', 'INVALID_PATH')]:
            with self.subTest(code=code):
                result = self.assertInvalid(files(**{path: body}), code)
                self.assertNotIn(token, str(result))
                self.assertNotIn('BEGIN RSA', str(result))

    def test_escaped_credentials_in_parsed_metadata_are_also_rejected(self):
        token = 'onto_app_' + 'A' * 43
        escaped = ''.join('\\u%04x' % ord(char) for char in token)
        package = {'SKILL.md': skill(extra='metadata:\n  author: "' + escaped + '"\n')}
        result = self.assertInvalid(package, 'CREDENTIAL_DETECTED')
        self.assertNotIn(token, str(result))
        for value in ['{"value":"' + escaped + '"}', '{"' + escaped + '":"value"}']:
            result = self.assertInvalid(files(**{'assets/example.json': value}), 'CREDENTIAL_DETECTED')
            self.assertNotIn(token, str(result))

    def test_paths_collision_hidden_traversal_controls_and_file_directory_conflicts(self):
        for path in ['../bad', '/absolute', r'scripts\bad.py', 'C:drive', 'refs//a', '.env', '.git/config',
                     'scripts/%2e%2e/a.py', 'refs/../bad', 'refs/ bad', 'refs/bad.', 'refs/CON.txt',
                     'refs/a\x00b', 'refs/／bad.txt', 'refs/\u202ebad.txt']:
            with self.subTest(path=path):
                self.assertInvalid(files(**{path: 'text'}), 'INVALID_PATH')
        self.assertInvalid(files(**{'references/One.md': 'x', 'references/one.md': 'y'}), 'DUPLICATE_PATH')
        self.assertInvalid(files(**{'references/Ａ.md': 'x', 'references/A.md': 'y'}), 'DUPLICATE_PATH')
        self.assertInvalid(files(**{'scripts/a.py': 'pass', 'scripts/a.py/inside.py': 'pass'}), 'PATH_CONFLICT')

    def test_text_and_resource_limits_apply_before_parsing(self):
        self.assertInvalid(files(**{'assets/data.bin': b'not-text'}), 'TEXT_REQUIRED')
        self.assertInvalid(files(**{'assets/data.txt': 'bad\x00content'}), 'TEXT_REQUIRED')
        self.assertInvalid(files(**{'assets/data.txt': '\ud800'}), 'TEXT_REQUIRED')
        self.assertInvalid(files(**{'assets/data.txt': 'x' * (packages.MAX_FILE_BYTES + 1)}), 'FILE_SIZE')
        self.assertInvalid(files(**{f'assets/{i}.txt': 'x' for i in range(64)}), 'FILE_COUNT')
        self.assertInvalid(files(**{f'assets/{i}.txt': 'x' * packages.MAX_FILE_BYTES for i in range(8)}), 'PACKAGE_SIZE')
        self.assertInvalid(files(**{'/'.join(['dir'] * 8) + '/file.txt': 'x'}), 'PATH_DEPTH')

    def test_digest_is_content_addressed_and_not_zip_metadata(self):
        package = files(**{'references/a.md': 'text\n'})
        reversed_package = dict(reversed(list(package.items())))
        self.assertEqual(packages.package_digest(package), packages.package_digest(reversed_package))
        changed = {**package, 'references/a.md': 'text\r\n'}
        self.assertNotEqual(packages.package_digest(package), packages.package_digest(changed))
        self.assertEqual(packages.write_zip(package), packages.write_zip(reversed_package))

    def test_export_roundtrip_preserves_files_and_normalizes_single_lowercase_entry(self):
        package = files(**{'scripts/main.py': 'print("hello")\n', 'references/中文.md': '来源说明\n', 'assets/template.txt': 'x' * 20000})
        raw = packages.write_zip(package)
        self.assertEqual(packages.read_zip(raw), package)
        with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
            self.assertTrue(all(item.filename.startswith('sample-skill/') for item in zipped.infolist()))
            self.assertTrue(all(stat.S_IFMT(item.external_attr >> 16) == stat.S_IFREG for item in zipped.infolist()))
        self.assertEqual(packages.read_zip(archive([('skill.md', skill())])), files())
        self.assertEqual(packages.read_zip(archive([('SKILL.md', skill())])), files())

    def test_maximum_relative_path_depth_remains_exportable_with_root_wrapper(self):
        deep = '/'.join(['dir'] * 7) + '/notes.txt'
        package = files(**{deep: 'text'})
        self.assertEqual(packages.read_zip(packages.write_zip(package)), package)

    def test_manifest_sync_preserves_body_resources_and_unrelated_frontmatter(self):
        text = skill(extra='license: Apache-2.0\ncompatibility: Python 3.12\nallowed-tools: Read\nmetadata:\n  author: example\n  version: 1.0\n', body='\n# Original body\n\nKeep trailing whitespace.  \n')
        package = {'SKILL.md': text, 'references/original.md': '原始内容\r\n', 'scripts/main.py': 'print(1)\n'}
        portable = {'schema_version': '1.0', 'steps': ['inspect'], 'inputs': []}
        updated = packages.sync_manifest(package, portable)
        self.assertEqual(updated['references/original.md'], package['references/original.md'])
        self.assertEqual(updated['scripts/main.py'], package['scripts/main.py'])
        self.assertEqual(updated['SKILL.md'].split('\n---\n', 1)[1], text.split('\n---\n', 1)[1])
        result = packages.validate_files(updated)
        self.assertTrue(result['valid'], result['errors'])
        self.assertEqual(result['frontmatter']['metadata'], {'author': 'example', 'version': '1.0', 'ontology-one.manifest': 'assets/ontology-one.json'})
        self.assertEqual(result['frontmatter']['license'], 'Apache-2.0')
        self.assertEqual(result['frontmatter']['compatibility'], 'Python 3.12')
        self.assertEqual(result['frontmatter']['allowed-tools'], 'Read')
        self.assertEqual(json.loads(updated['assets/ontology-one.json']), portable)
        self.assertEqual(packages.sync_manifest(updated, portable), updated)
        self.assertNotIn('assets/ontology-one.json', package)
        self.assertEqual(packages.read_zip(packages.write_zip(updated)), updated)

    def test_zip_parent_directory_must_match_declared_name(self):
        self.assertZipInvalid(archive([('other/SKILL.md', skill())]), 'SKILL_DIRECTORY')
        self.assertZipInvalid(archive([('a/SKILL.md', skill()), ('b/file.txt', 'x')]), 'SKILL_FILE_MISSING')
        self.assertZipInvalid(archive([('sample-skill/SKILL.md', skill()), ('other/', '')]), 'SKILL_DIRECTORY')
        self.assertZipInvalid(archive([('sample-skill/README.md', 'missing')]), 'SKILL_FILE_MISSING')

    def test_zip_duplicate_names_traversal_symlinks_and_device_modes_rejected(self):
        for extra, code in [('SKILL.md', 'DUPLICATE_PATH'), ('skill.md', 'DUPLICATE_PATH'),
                            ('../escape.txt', 'INVALID_PATH'), ('/absolute.txt', 'INVALID_PATH')]:
            with self.subTest(extra=extra):
                self.assertZipInvalid(archive([('SKILL.md', skill()), (extra, 'text')]), code)
        for mode in [stat.S_IFLNK | 0o777, stat.S_IFCHR | 0o644, stat.S_IFDIR | 0o755]:
            info = zipfile.ZipInfo('references/link')
            info.create_system = 3
            info.external_attr = mode << 16
            self.assertZipInvalid(archive([('SKILL.md', skill()), (info, 'target')]), 'ZIP_LINK')

    def test_zip_limits_compression_and_file_directory_conflicts(self):
        self.assertZipInvalid(archive([('SKILL.md', skill()), ('assets/repeat.txt', 'a' * 20000)], zipfile.ZIP_DEFLATED), 'ZIP_RATIO')
        self.assertZipInvalid(archive([('SKILL.md', skill()), ('assets/large.txt', 'a' * (packages.MAX_FILE_BYTES + 1))]), 'FILE_SIZE')
        self.assertZipInvalid(archive([('SKILL.md', skill()), ('assets/x', 'x'), ('assets/x/file.txt', 'x')]), 'PATH_CONFLICT')
        self.assertZipInvalid(archive([('SKILL.md', skill()), ('assets/a.bin', b'\xff')]), 'TEXT_REQUIRED')
        self.assertZipInvalid(archive([('SKILL.md', skill())], zipfile.ZIP_BZIP2), 'ZIP_ENCODING')
        self.assertZipInvalid(archive([(f'dir-{i}/', '') for i in range(129)]), 'ZIP_FORMAT')

    def test_zip_trailing_data_multi_disk_encryption_and_corruption_rejected(self):
        raw = archive([('SKILL.md', skill())])
        self.assertZipInvalid(raw + b'extra', 'ZIP_FORMAT')
        self.assertZipInvalid(b'prefix' + raw, 'ZIP_FORMAT')
        self.assertZipInvalid(raw[:-5], 'ZIP_FORMAT')
        split = bytearray(raw)
        eocd = split.rfind(b'PK\x05\x06')
        struct.pack_into('<H', split, eocd + 4, 1)
        self.assertZipInvalid(bytes(split), 'ZIP_FORMAT')
        encrypted = bytearray(raw)
        central = encrypted.index(b'PK\x01\x02')
        struct.pack_into('<H', encrypted, central + 8, 1)
        self.assertZipInvalid(bytes(encrypted), 'ZIP_ENCODING')
        corrupt = bytearray(raw)
        location = corrupt.index(b'name:')
        corrupt[location] ^= 1
        self.assertZipInvalid(bytes(corrupt), 'ZIP_FORMAT')


if __name__ == '__main__':
    unittest.main()
