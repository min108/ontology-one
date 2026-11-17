import unittest

from e2e.r3_runner_metadata import model_metadata


class RunnerMetadataTests(unittest.TestCase):
    def test_default_names_match_environment_configuration(self) -> None:
        from backend.skill_harness import DEFAULT_BASE_URL, DEFAULT_MODEL

        self.assertEqual(model_metadata({}), {"model_name": DEFAULT_MODEL, "base_url": DEFAULT_BASE_URL})

    def test_safe_metadata_never_retains_userinfo_query_or_fragment(self) -> None:
        result = model_metadata(
            {
                "DEEPSEEK_MODEL": "deepseek-chat",
                "DEEPSEEK_BASE_URL": "https://person:secret@example.test:8443/anthropic?token=secret#secret",
                "DEEPSEEK_API_KEY": "secret",
            }
        )
        self.assertEqual(result, {"model_name": "deepseek-chat", "base_url": "https://example.test:8443/anthropic"})
        self.assertNotIn("secret", str(result))
        self.assertNotIn("person", str(result))

    def test_secret_in_path_or_model_and_invalid_urls_are_not_recorded(self) -> None:
        self.assertEqual(
            model_metadata(
                {
                    "DEEPSEEK_MODEL": "key-secret",
                    "DEEPSEEK_BASE_URL": "https://example.test/key-secret",
                    "DEEPSEEK_API_KEY": "key-secret",
                }
            ),
            {"model_name": None, "base_url": None},
        )
        for endpoint in ("private text", "file:///secret", "https://example.test:bad", "https://["):
            with self.subTest(endpoint=endpoint):
                self.assertIsNone(model_metadata({"DEEPSEEK_BASE_URL": endpoint})["base_url"])
        self.assertIsNone(model_metadata({"DEEPSEEK_MODEL": "model\ncredential"})["model_name"])
        self.assertIsNone(
            model_metadata({"DEEPSEEK_BASE_URL": "https://example.test/%73ecret", "DEEPSEEK_API_KEY": " secret "})[
                "base_url"
            ]
        )

    def test_ipv6_endpoint_retains_safe_address(self) -> None:
        self.assertEqual(
            model_metadata({"DEEPSEEK_BASE_URL": "http://[::1]:8123/v1?private=yes"})["base_url"],
            "http://[::1]:8123/v1",
        )
