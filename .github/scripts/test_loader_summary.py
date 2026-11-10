import csv
import json
from pathlib import Path
import tempfile
import unittest

from summarize_windows_loader import summarize


class LoaderSummaryTests(unittest.TestCase):
    def test_only_owned_pid_and_canonical_system_objects_survive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); raw = root/'raw.csv'; receipt = root/'receipt.json'
            receipt.write_text(json.dumps({'schema_version': 1, 'child_pids': [42], 'control_pids': [43], 'probe_pid': 44}))
            with raw.open('w', newline='', encoding='utf-8-sig') as file:
                writer = csv.DictWriter(file, ['PID', 'Operation', 'Path', 'Result', 'Detail'])
                writer.writeheader()
                writer.writerows([
                    dict(PID=42, Operation='CreateFile', Path=r'C:\Users\PRIVATE_USER\PRIVATE_KEY', Result='ACCESS DENIED', Detail='SECRET_DETAIL'),
                    dict(PID=99, Operation='Load Image', Path=r'C:\secret\other.exe', Result='SUCCESS', Detail='OTHER_PROCESS'),
                    dict(PID=42, Operation='Load Image', Path=r'C:\Windows\System32\kernel32.dll', Result='SUCCESS', Detail='NO_EXPORT'),
                    dict(PID=43, Operation='Load Image', Path=r'C:\Windows\System32\ntdll.dll', Result='SUCCESS', Detail='CONTROL_DETAIL'),
                    dict(PID=44, Operation='CreateFile', Path=r'C:\private\python.exe', Result='SUCCESS', Detail='PARENT_SECRET'),
                    dict(PID=42, Operation='RegQueryValue', Path=r'HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\KnownDLLs', Result='NAME NOT FOUND', Detail='REGISTRY_VALUE')])
            result = summarize(raw, receipt); encoded = json.dumps(result)
            self.assertEqual(result['owned_event_count'], 3)
            self.assertEqual(result['control_event_count'], 1)
            self.assertEqual(result['parent_event_count'], 1)
            self.assertEqual(result['csv_event_count'], 6)
            self.assertTrue(all(event['first_sequence'] <= event['last_sequence'] for event in result['events']))
            self.assertEqual(result['events'][0]['ntstatus'], 0xC0000022)
            for secret in ('PRIVATE_USER', 'PRIVATE_KEY', 'SECRET_DETAIL', 'other.exe', 'OTHER_PROCESS', 'REGISTRY_VALUE', 'CONTROL_DETAIL', 'PARENT_SECRET', 'C:\\'):
                self.assertNotIn(secret, encoded)
            self.assertIn('kernel32.dll', encoded)
            self.assertIn('session manager', encoded)
            self.assertFalse(result['raw_content_retained'])

    def test_invalid_receipt_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); receipt = root/'receipt.json'
            receipt.write_text(json.dumps({'schema_version': 1, 'child_pids': ['42']}))
            with self.assertRaises(ValueError): summarize(root/'missing.csv', receipt)


if __name__ == '__main__': unittest.main()
