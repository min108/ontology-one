"""Visible-mount decision table; shadowed mount records cannot select runtime storage."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend import skill_harness as harness


@unittest.skipUnless(sys.platform == "linux", "Linux tmpfs mount identity contract")
class RuntimeMountTests(unittest.TestCase):
    def test_active_mount_identity_allows_only_writable_tmpfs(self) -> None:
        # mountinfo also lists covered mounts in restricted/containerized namespaces.
        positives = [
            "7 1 0:7 / / rw - tmpfs tmpfs rw\n",
            "8 1 0:8 / /dev/shm ro - tmpfs tmpfs ro\n7 1 0:7 / / rw - tmpfs tmpfs rw\n",
            "8 1 0:8 / / ro - tmpfs tmpfs ro\n7 1 0:7 / / rw - tmpfs tmpfs rw\n",
            "7 1 0:7 / / rw,nosuid,nodev - tmpfs tmpfs rw\n9 1 0:9 / /unrelated ro - ext4 disk ro\n",
            "7 1 0:7 / / rw,relatime shared:1 - tmpfs tmpfs rw,size=1024k\n",
        ]
        negatives = [
            "7 1 0:7 / / rw - ext4 disk rw\n",
            "7 1 0:7 / / ro - tmpfs tmpfs ro\n",
            "8 1 0:8 / / rw - tmpfs tmpfs rw\n",
            "7 1 0:7 / / rw - tmpfs tmpfs rw\n7 1 0:7 / / rw - tmpfs tmpfs rw\n",
            "7 malformed\n",
        ]
        with tempfile.TemporaryDirectory() as temporary:

            def evidence(path: Path, *args: object, **kwargs: object) -> str:
                if str(path).startswith("/proc/self/fdinfo/"):
                    return "pos:\t0\nflags:\t0300000\nmnt_id:\t7\n"
                self.assertEqual(str(path), "/proc/self/mountinfo")
                return mountinfo

            for accepted, mountinfo in [(True, value) for value in positives] + [(False, value) for value in negatives]:
                mountinfo = mountinfo.replace("/dev/shm", temporary)
                with (
                    self.subTest(accepted=accepted, mountinfo=mountinfo),
                    patch.dict(os.environ, {"ONTOLOGY_HARNESS_TMP_ROOT": temporary}),
                    patch.object(Path, "read_text", evidence),
                ):
                    if accepted:
                        self.assertEqual(harness._tmpfs_root(), Path(temporary))
                    else:
                        with self.assertRaises(HTTPException) as failure:
                            harness._tmpfs_root()
                        self.assertEqual(failure.exception.detail["code"], "HARNESS_UNAVAILABLE")
