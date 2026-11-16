import unittest
from unittest.mock import patch

from backend.core.ids import new_id


class IdTests(unittest.TestCase):
    def test_uppercase_prefix(self) -> None:
        self.assertRegex(new_id("CS-"), r"^CS-[0-9A-F]{8}$")

    def test_organization_lowercase(self) -> None:
        self.assertRegex(new_id("org-"), r"^org-[0-9a-f]{8}$")

    def test_receipt_lowercase(self) -> None:
        self.assertRegex(new_id("rcp_"), r"^rcp_[0-9a-f]{8}$")

    def test_unknown_prefix(self) -> None:
        with self.assertRaises(ValueError):
            new_id("UNKNOWN-")

    def test_collision_retry(self) -> None:
        with patch("backend.core.ids.secrets.token_hex", side_effect=["aaaaaaaa", "bbbbbbbb"]):
            self.assertEqual(new_id("CS-", exists=lambda value: value == "CS-AAAAAAAA"), "CS-BBBBBBBB")

    def test_collision_exhaustion(self) -> None:
        with self.assertRaises(RuntimeError):
            new_id("CS-", exists=lambda value: True)
