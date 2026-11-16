import json
import logging
import unittest

from backend.core.logging import MetadataFormatter


class LoggingTests(unittest.TestCase):
    def test_only_safe_metadata_is_logged(self) -> None:
        record = logging.LogRecord("test", logging.INFO, "", 1, "secret body %s", ("password",), None)
        record.request_id = "r"
        record.actor_id = "u"
        record.tenant_id = "t"
        record.password = "never"
        data = json.loads(MetadataFormatter().format(record))
        self.assertEqual(data["request_id"], "r")
        self.assertTrue({"timestamp", "level", "event", "actor_id", "tenant_id"} <= set(data))
        self.assertNotIn("secret", json.dumps(data))
        self.assertNotIn("password", json.dumps(data))

    def test_exception_text_not_logged(self) -> None:
        record = logging.LogRecord("test", logging.ERROR, "", 1, "body", (), (ValueError, ValueError("secret"), None))
        self.assertNotIn("secret", MetadataFormatter().format(record))
