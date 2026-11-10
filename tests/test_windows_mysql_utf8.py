"""Offline policy checks; the Windows builder also verifies two native results."""
import struct
import unittest
import xml.etree.ElementTree as ET

from scripts import windows_mysql_utf8 as recipe


def pe_fixture(certificate=True):
    data=bytearray(0x600)
    data[:2]=b'MZ';struct.pack_into('<I',data,0x3c,0x80);data[0x80:0x84]=b'PE\0\0'
    struct.pack_into('<HHI',data,0x84,0x8664,2,12345)
    struct.pack_into('<H',data,0x94,240)
    optional=0x98
    struct.pack_into('<H',data,optional,0x20b)
    struct.pack_into('<I',data,optional+108,16)
    struct.pack_into('<II',data,optional+128,0x2000,0x200)
    table=optional+240
    for index,(name,virtual,offset) in enumerate(((b'.text',0x1000,0x200),(b'.rsrc',0x2000,0x400))):
        data[table+40*index:table+40*index+len(name)]=name
        struct.pack_into('<IIII',data,table+40*index+8,0x200,virtual,0x200,offset)
    data[0x200:0x210]=bytes(range(16))
    struct.pack_into('<I',data,0x404,777)
    if certificate:
        struct.pack_into('<II',data,optional+144,0x600,16)
        data+=struct.pack('<IHH',16,0x200,2)+b'fixture!'
    return data


class MySQLManifestPolicyTests(unittest.TestCase):
    def test_additive_manifest_preserves_execution_level_and_dependencies(self):
        original=b'''<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
        <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false"/></requestedPrivileges></security></trustInfo>
        <dependency><dependentAssembly><assemblyIdentity name="example" version="1.0.0.0"/></dependentAssembly></dependency>
        </assembly>'''
        before=ET.fromstring(original);after=ET.fromstring(recipe.utf8_manifest(original))
        for element in before:
            self.assertEqual(ET.tostring(element),ET.tostring(after.find(element.tag)))
        active=after.find('.//{'+recipe.SETTINGS+'}activeCodePage')
        self.assertEqual(active.text,'UTF-8')
        self.assertEqual(recipe.utf8_manifest(recipe.utf8_manifest(original)),recipe.utf8_manifest(original))

    def test_ambiguous_or_unexpected_manifest_fails_closed(self):
        for original in (b'<notassembly/>',b'<!DOCTYPE assembly><assembly/>'):
            with self.assertRaises(ValueError):recipe.utf8_manifest(original)

    def test_certificate_removal_and_timestamps_leave_code_sections_unchanged(self):
        vendor=pe_fixture();unsigned,removed=recipe.unsigned_image(vendor)
        self.assertTrue(removed);self.assertEqual(len(unsigned),0x600)
        self.assertEqual(struct.unpack_from('<II',unsigned,0x98+144),(0,0))
        normalized=recipe.normalize_resource_times(unsigned,12345)
        self.assertEqual(struct.unpack_from('<I',normalized,0x404)[0],0)
        self.assertEqual(recipe.non_resource_sections(vendor),recipe.non_resource_sections(normalized))
        self.assertEqual(struct.unpack_from('<I',normalized,0x88)[0],12345)

    def test_invalid_certificate_or_unrelated_executable_is_rejected(self):
        bad=pe_fixture();struct.pack_into('<II',bad,0x98+144,0x200,16)
        with self.assertRaises(ValueError):recipe.unsigned_image(bad)
        with self.assertRaises(ValueError):recipe.transform_mysql_executable('vcruntime140.dll')


if __name__=='__main__':unittest.main()
