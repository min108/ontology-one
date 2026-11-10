"""Reproducible resource-only MySQL adaptation for native Windows Unicode paths.

The vendor archive remains separately SHA256-pinned. Only RT_MANIFEST/ID1 is
changed, with existing languages/settings preserved. An existing Authenticode
certificate is removed because it cannot authenticate the derived executable.
VC redistributables are never passed to this transformer. This source recipe is
distributed alongside the unmodified corresponding MySQL upstream source.

https://learn.microsoft.com/windows/apps/design/globalizing/use-utf8-code-page
https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-beginupdateresourcew
"""
from __future__ import annotations

import ctypes as C
import hashlib
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET

RECIPE_VERSION = 1
ASSEMBLY = 'urn:schemas-microsoft-com:asm.v1'
APPLICATION = 'urn:schemas-microsoft-com:asm.v3'
SETTINGS = 'http://schemas.microsoft.com/SMI/2019/WindowsSettings'
ET.register_namespace('', ASSEMBLY)
ET.register_namespace('asmv3', APPLICATION)
ET.register_namespace('ws2019', SETTINGS)


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def utf8_manifest(original: bytes | None) -> bytes:
    if original is not None and (len(original) > 65536 or b'<!DOCTYPE' in original.upper()):
        raise ValueError('Invalid vendor manifest')
    root = ET.fromstring(original) if original else ET.Element('{'+ASSEMBLY+'}assembly', {'manifestVersion':'1.0'})
    if root.tag != '{'+ASSEMBLY+'}assembly':
        raise ValueError('Unexpected manifest root')
    applications = root.findall('{'+APPLICATION+'}application')
    if len(applications) > 1:
        raise ValueError('Ambiguous application manifest')
    application = applications[0] if applications else ET.SubElement(root, '{'+APPLICATION+'}application')
    groups = application.findall('{'+APPLICATION+'}windowsSettings')
    if len(groups) > 1:
        raise ValueError('Ambiguous Windows settings')
    settings = groups[0] if groups else ET.SubElement(application, '{'+APPLICATION+'}windowsSettings')
    active = settings.findall('{'+SETTINGS+'}activeCodePage')
    if len(active) > 1:
        raise ValueError('Ambiguous active code page')
    element = active[0] if active else ET.SubElement(settings, '{'+SETTINGS+'}activeCodePage')
    element.text = 'UTF-8'
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def pe_layout(data):
    if len(data) < 256 or data[:2] != b'MZ': raise ValueError('Invalid PE image')
    pe = struct.unpack_from('<I',data,0x3c)[0]
    if pe+24 > len(data) or data[pe:pe+4] != b'PE\0\0': raise ValueError('Invalid PE header')
    machine, sections = struct.unpack_from('<HH',data,pe+4)
    optional_size = struct.unpack_from('<H',data,pe+20)[0]
    optional = pe+24
    if machine != 0x8664 or optional_size < 152 or optional+optional_size > len(data):
        raise ValueError('Only complete x64 PE images are accepted')
    if struct.unpack_from('<H',data,optional)[0] != 0x20b:
        raise ValueError('Only PE32+ images are accepted')
    count = struct.unpack_from('<I',data,optional+108)[0]
    if count < 5: raise ValueError('PE data directories are missing')
    table = optional+optional_size
    if not 1 <= sections <= 96 or table+40*sections > len(data): raise ValueError('Invalid PE section table')
    ranges=[];section_records=[]
    for index in range(sections):
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from('<IIII',data,table+40*index+8)
        if raw_offset+raw_size > len(data): raise ValueError('PE section is outside image')
        ranges.append((virtual_address,max(virtual_size,raw_size),raw_offset,raw_size))
        name=bytes(data[table+40*index:table+40*index+8]).rstrip(b'\0')
        section_records.append((name,virtual_address,max(virtual_size,raw_size),raw_offset,raw_size))
    return {'pe':pe,'optional':optional,'checksum':optional+64,'resource':optional+112+16,
            'security':optional+112+32,'ranges':ranges,'sections':section_records}


def non_resource_sections(data):
    layout=pe_layout(data);rva,_=struct.unpack_from('<II',data,layout['resource'])
    return {name:hashlib.sha256(data[offset:offset+size]).hexdigest()
            for name,address,span,offset,size in layout['sections'] if not address<=rva<address+span}


def unsigned_image(data):
    """Remove the certificate, without moving sections or unrelated overlays."""
    result=bytearray(data);layout=pe_layout(result)
    offset,size=struct.unpack_from('<II',result,layout['security'])
    present=bool(offset or size)
    if present:
        if not offset or size<8 or offset+size>len(result): raise ValueError('Invalid PE certificate range')
        if offset<max(start+length for _a,_s,start,length in layout['ranges']):
            raise ValueError('PE certificate overlaps executable sections')
        length,revision,kind=struct.unpack_from('<IHH',result,offset)
        if not 8 <= length <= size or revision not in (0x100,0x200) or kind != 2:
            raise ValueError('Unexpected PE certificate format')
        struct.pack_into('<II',result,layout['security'],0,0)
        if offset+size==len(result): del result[offset:]
        else: result[offset:offset+size]=bytes(size)
    struct.pack_into('<I',result,layout['checksum'],0)
    return result,present


def normalize_resource_times(data, vendor_timestamp):
    result=bytearray(data);layout=pe_layout(result)
    struct.pack_into('<I',result,layout['pe']+8,vendor_timestamp)
    struct.pack_into('<I',result,layout['checksum'],0)
    rva,size=struct.unpack_from('<II',result,layout['resource'])
    if not rva or size<16: raise ValueError('Updated resource directory is missing')
    base=None
    for address,span,offset,raw_size in layout['ranges']:
        if address<=rva<address+span and rva-address+size<=raw_size:
            base=offset+rva-address;break
    if base is None or base+size>len(result): raise ValueError('Resource directory is outside image')
    pending=[0];seen=set()
    while pending:
        relative=pending.pop()
        if relative in seen: raise ValueError('Cyclic resource directories')
        seen.add(relative)
        if len(seen)>4096 or relative+16>size: raise ValueError('Invalid resource tree')
        position=base+relative
        struct.pack_into('<I',result,position+4,0)
        named,numbered=struct.unpack_from('<HH',result,position+12)
        count=named+numbered
        if relative+16+count*8>size: raise ValueError('Invalid resource entries')
        for index in range(count):
            target=struct.unpack_from('<I',result,position+16+index*8+4)[0]
            if target & 0x80000000: pending.append(target & 0x7fffffff)
    return result


def _api():
    if sys.platform != 'win32': raise ValueError('Resource transformation requires native Windows')
    kernel=C.WinDLL('kernel32',use_last_error=True)
    signatures={
        'LoadLibraryExW':(C.c_void_p,[C.c_wchar_p,C.c_void_p,C.c_uint32]),
        'FreeLibrary':(C.c_int,[C.c_void_p]),
        'FindResourceExW':(C.c_void_p,[C.c_void_p,C.c_void_p,C.c_void_p,C.c_uint16]),
        'SizeofResource':(C.c_uint32,[C.c_void_p,C.c_void_p]),
        'LoadResource':(C.c_void_p,[C.c_void_p,C.c_void_p]),
        'LockResource':(C.c_void_p,[C.c_void_p]),
        'BeginUpdateResourceW':(C.c_void_p,[C.c_wchar_p,C.c_int]),
        'UpdateResourceW':(C.c_int,[C.c_void_p,C.c_void_p,C.c_void_p,C.c_uint16,C.c_void_p,C.c_uint32]),
        'EndUpdateResourceW':(C.c_int,[C.c_void_p,C.c_int]),
    }
    for name,(restype,argtypes) in signatures.items():
        function=getattr(kernel,name);function.restype=restype;function.argtypes=argtypes
    return kernel


def _check(value, operation):
    if not value: raise OSError(C.get_last_error(),operation)
    return value


def read_manifests(path):
    kernel=_api();module=_check(kernel.LoadLibraryExW(str(path),None,0x2|0x20),'LoadLibraryExW')
    callback_type=C.WINFUNCTYPE(C.c_int,C.c_void_p,C.c_void_p,C.c_void_p,C.c_uint16,C.c_ssize_t)
    languages=[]
    callback=callback_type(lambda _m,_t,_n,language,_p: languages.append(language) or 1)
    enum=kernel.EnumResourceLanguagesW;enum.restype=C.c_int
    enum.argtypes=[C.c_void_p,C.c_void_p,C.c_void_p,callback_type,C.c_ssize_t]
    try:
        if not enum(module,24,1,callback,0):
            error=C.get_last_error()
            if error not in (1813,1814,1815): raise OSError(error,'EnumResourceLanguagesW')
        result={}
        for language in languages:
            resource=_check(kernel.FindResourceExW(module,24,1,language),'FindResourceExW')
            size=kernel.SizeofResource(module,resource)
            if not 0<size<=65536: raise ValueError('Invalid manifest resource size')
            loaded=_check(kernel.LoadResource(module,resource),'LoadResource')
            result[language]=C.string_at(_check(kernel.LockResource(loaded),'LockResource'),size)
        return result
    finally: kernel.FreeLibrary(module)


def _transform_copy(path, manifests, timestamp):
    raw,removed=unsigned_image(path.read_bytes());path.write_bytes(raw)
    kernel=_api();update=_check(kernel.BeginUpdateResourceW(str(path),False),'BeginUpdateResourceW')
    buffers=[]
    try:
        for language,original in sorted((manifests or {0:None}).items()):
            blob=utf8_manifest(original);buffer=C.create_string_buffer(blob);buffers.append(buffer)
            _check(kernel.UpdateResourceW(update,24,1,language,buffer,len(blob)),'UpdateResourceW')
        handle,update=update,None
        _check(kernel.EndUpdateResourceW(handle,False),'EndUpdateResourceW')
    finally:
        if update: kernel.EndUpdateResourceW(update,True)
    raw=normalize_resource_times(path.read_bytes(),timestamp)
    layout=pe_layout(raw);buffer=(C.c_char*len(raw)).from_buffer(raw)
    imagehlp=C.WinDLL('imagehlp',use_last_error=True)
    checksum=imagehlp.CheckSumMappedFile;checksum.restype=C.c_void_p
    checksum.argtypes=[C.c_void_p,C.c_uint32,C.POINTER(C.c_uint32),C.POINTER(C.c_uint32)]
    previous,current=C.c_uint32(),C.c_uint32()
    _check(checksum(buffer,len(raw),C.byref(previous),C.byref(current)),'CheckSumMappedFile')
    struct.pack_into('<I',raw,layout['checksum'],current.value)
    path.write_bytes(raw)
    observed=read_manifests(path)
    if set(observed)!=set(manifests or {0:None}): raise ValueError('Manifest language resources changed')
    for language,blob in observed.items():
        expected=utf8_manifest((manifests or {0:None})[language])
        if ET.tostring(ET.fromstring(blob))!=ET.tostring(ET.fromstring(expected)):
            raise ValueError('Updated manifest differs from the precise transformation')
    return removed


def transform_mysql_executable(path):
    path=Path(path).resolve()
    if path.name.lower() not in {'mysqld.exe','mysql.exe','mysqladmin.exe'}:
        raise ValueError('Only the three declared MySQL executable resources may be changed')
    vendor=path.read_bytes();layout=pe_layout(vendor)
    timestamp=struct.unpack_from('<I',vendor,layout['pe']+8)[0]
    manifests=read_manifests(path)
    with tempfile.TemporaryDirectory(prefix='ontology-mysql-manifest-') as directory:
        first=Path(directory)/'first.exe';second=Path(directory)/'second.exe'
        first.write_bytes(vendor);second.write_bytes(vendor)
        removed=_transform_copy(first,manifests,timestamp)
        _transform_copy(second,manifests,timestamp)
        if sha256(first)!=sha256(second): raise ValueError('Resource transformation is not reproducible')
        if non_resource_sections(vendor)!=non_resource_sections(first.read_bytes()):
            raise ValueError('A non-resource executable section was modified')
        derived=sha256(first);shutil.copyfile(first,path)
    return {'recipe_version':RECIPE_VERSION,'vendor_sha256':hashlib.sha256(vendor).hexdigest(),
        'derived_sha256':derived,'resource_type':24,'resource_id':1,'languages':sorted(manifests or {0:None}),
        'active_code_page':'UTF-8','minimum_windows_build':18362,'deterministic_pair_verified':True,
        'non_resource_sections_unchanged':True,
        'vendor_authenticode_certificate_removed':removed,
        'signature_status':'unsigned derived executable; vendor signature is not claimed'}
