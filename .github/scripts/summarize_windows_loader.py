"""Reduce CI-only ProcMon CSV to bounded, credential-free owned-PID evidence.

Never copy raw paths, registry values, command lines, details, or CSV records.
The capture and this parser run as runneradmin; the raw files never enter an
artifact directory. A standard-user fixed probe supplies its own child PIDs.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re

OPERATIONS = frozenset(('CreateFile', 'QueryOpen', 'QueryInformationFile', 'ReadFile',
    'CreateFileMapping', 'Load Image', 'Process Start', 'Process Exit', 'Thread Create',
    'Thread Exit', 'RegOpenKey', 'RegQueryKey', 'RegQueryValue', 'RegEnumKey',
    'RegEnumValue', 'CloseFile', 'RegCloseKey', 'QuerySecurityFile'))
RESULTS = {'SUCCESS': 0, 'ACCESS DENIED': 0xC0000022,
    'NAME NOT FOUND': 0xC0000034, 'PATH NOT FOUND': 0xC000003A,
    'REPARSE': 0x104, 'BUFFER OVERFLOW': 0x80000005,
    'BUFFER TOO SMALL': 0xC0000023, 'NO MORE ENTRIES': 0x8000001A,
    'NO MORE FILES': 0x80000006, 'END OF FILE': 0xC0000011,
    'INVALID PARAMETER': 0xC000000D, 'SHARING VIOLATION': 0xC0000043,
    'PRIVILEGE NOT HELD': 0xC0000061, 'INVALID IMAGE FORMAT': 0xC000007B,
    'INVALID IMAGE HASH': 0xC0000428, 'DLL NOT FOUND': 0xC0000135,
    'NOT FOUND': 0xC0000225, 'FILE NOT FOUND': 0xC000000F,
    'FILE LOCKED WITH ONLY READERS': 0x12A, 'FILE LOCKED WITH WRITERS': 0x12B,
    'IS DIRECTORY': 0xC00000BA, 'NOT A DIRECTORY': 0xC0000103}
MODULE = re.compile(r'[a-z0-9_.-]{1,80}\.(?:dll|exe|pyd)\Z')


def object_label(raw: str) -> tuple[str, str | None]:
    """Categories are fixed labels; only syntactically safe module basenames survive."""
    value = raw.lower().replace('/', '\\')
    if value.startswith(('hklm\\', 'hkcu\\', 'hku\\', 'hkcr\\')):
        for needle, label in (('image file execution options', 'registry_ifeo'),
            ('known dlls', 'registry_known_dlls'), ('knowndlls', 'registry_known_dlls'),
            ('session manager', 'registry_session_manager'),
            ('appcompatflags', 'registry_appcompat'), ('appmodel', 'registry_appmodel'),
            ('\\console', 'registry_console')):
            if needle in value: return label, None
        return ('registry_machine' if value.startswith('hklm\\') else 'registry_user'), None
    basename = PureWindowsPath(value).name
    module = basename if MODULE.fullmatch(basename) else None
    if '\\windows\\system32\\' in value: return 'system32_module' if module else 'system32_object', module
    if '\\windows\\winsxs\\' in value: return 'windows_sxs', module
    if '\\windows\\' in value: return 'windows_object', module
    if value.startswith(('\\device\\', '\\basenamedobjects\\', '\\sessions\\')):
        return 'native_object', module
    return ('runtime_module' if module else 'other_object'), module


def canonical_system_object(raw: str) -> str | None:
    """Only canonical OS locations survive; user/package/unrecognized paths do not."""
    value = raw.lower().replace('/', '\\')
    for marker in ('\\windows\\system32\\', '\\windows\\winsxs\\'):
        if marker in value:
            tail = value.split(marker, 1)[1]
            if len(tail) < 240 and re.fullmatch(r'[a-z0-9_\\ .{}!()\-]+', tail):
                return '<windows>' + marker[len('\\windows'):] + tail
    prefixes = ('hklm\\system\\currentcontrolset\\control\\session manager',
        'hklm\\software\\microsoft\\windows nt\\currentversion\\image file execution options',
        'hklm\\software\\microsoft\\windows\\currentversion\\sidebyside',
        '\\knowndlls\\', '\\knowndlls32\\', '\\windows\\windowstations\\')
    if len(value) <= 360 and any(value.startswith(prefix) for prefix in prefixes):
        value = re.sub(r's-1-\d+(?:-\d+)+', '<sid>', value)
        return re.sub(r'ontology[a-z0-9_.-]*', '<qa-id>', value)
    return None


def summarize(csv_path: Path, receipt_path: Path) -> dict:
    receipt = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
    pids = receipt.get('child_pids')
    if receipt.get('schema_version') != 1 or not isinstance(pids, list) or not 1 <= len(pids) <= 4:
        raise ValueError('INVALID_PROBE_RECEIPT')
    if any(type(pid) is not int or not 0 < pid < 2**32 for pid in pids):
        raise ValueError('INVALID_PROBE_PID')
    control_pids = receipt.get('control_pids', [])
    if (not isinstance(control_pids, list) or len(control_pids) > 2 or
            any(type(pid) is not int or not 0 < pid < 2**32 for pid in control_pids)):
        raise ValueError('INVALID_CONTROL_PID')
    owned, control = set(pids), set(control_pids)
    if owned & control: raise ValueError('OVERLAPPING_PROBE_PIDS')
    probe_pid = receipt.get('probe_pid')
    if probe_pid is not None and (type(probe_pid) is not int or not 0 < probe_pid < 2**32 or probe_pid in owned | control):
        raise ValueError('INVALID_PARENT_PID')
    parent = {probe_pid} if probe_pid is not None else set()
    counts = Counter(); sequence = {}; matched = controlled = parented = rows = malformed_pids = 0
    with csv_path.open('rb') as file:
        prefix = file.read(4)
    encoding = 'utf-16' if prefix.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    with csv_path.open(encoding=encoding, errors='strict', newline='') as file:
        reader = csv.DictReader(file)
        if not {'PID', 'Operation', 'Path', 'Result'}.issubset(reader.fieldnames or ()):
            raise ValueError('UNEXPECTED_PROCMON_COLUMNS')
        for row in reader:
            rows += 1
            if rows > 2_000_000: raise ValueError('CAPTURE_ROW_LIMIT')
            try: pid = int(row['PID'])
            except (ValueError, TypeError):
                malformed_pids += 1
                continue
            if pid not in owned | control | parent: continue
            role = 'lpac' if pid in owned else ('normal_control' if pid in control else 'probe_parent')
            if pid in owned: matched += 1
            elif pid in control: controlled += 1
            else: parented += 1
            raw_operation = row['Operation']
            operation = raw_operation if raw_operation in OPERATIONS else 'other_operation'
            category, module = object_label(row['Path'])
            status = RESULTS.get(row['Result'].upper())
            # The hash permits correlating otherwise undisclosed objects without
            # leaking directory names, registry values, or package/user paths.
            object_id = hashlib.sha256(row['Path'].encode('utf-8')).hexdigest()[:16]
            key = (pid, operation, category, module, status, object_id,
                   canonical_system_object(row['Path']), role)
            counts[key] += 1
            sequence.setdefault(key, [rows, rows])[1] = rows
    entries = [{'pid': k[0], 'operation': k[1], 'object': k[2], 'module': k[3],
                'ntstatus': k[4], 'object_id': k[5], 'system_object': k[6],
                'role': k[7], 'count': n, 'first_sequence': sequence[k][0],
                'last_sequence': sequence[k][1]} for k, n in counts.items()]
    entries.sort(key=lambda item: (item['role'] != 'lpac', item['ntstatus'] != 0xC0000022,
        item['ntstatus'] in (0, 0x104), item['operation'] != 'Load Image', item['object_id']))
    return {'schema_version': 1, 'status': 'captured' if matched else ('control_only' if controlled else ('parent_only' if parented else 'no_owned_events')),
        'owned_pid_count': len(owned), 'owned_event_count': matched,
        'control_pid_count': len(control), 'control_event_count': controlled,
        'parent_pid_count': len(parent), 'parent_event_count': parented,
        'csv_event_count': rows, 'unparseable_pid_count': malformed_pids,
        'distinct_event_count': len(entries), 'truncated': len(entries) > 256,
        'events': entries[:256], 'raw_content_retained': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    try: report = summarize(args.csv, args.receipt)
    except Exception:
        report = {'schema_version': 1, 'status': 'summary_unavailable', 'raw_content_retained': False}
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__': main()
