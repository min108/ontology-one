import unittest
from pathlib import Path

from scripts.gen_api_types import artifacts, openapi_schema


class OpenApiTests(unittest.TestCase):
    def test_only_v2_paths_are_exported(self) -> None:
        schema = openapi_schema()
        self.assertTrue(schema["paths"])
        self.assertTrue(all(path.startswith("/api/v2/") for path in schema["paths"]))

    def test_generated_files_are_current(self) -> None:
        root = Path(__file__).resolve().parents[2] / "frontend/src/api/generated"
        for name, content in artifacts().items():
            self.assertEqual((root / name).read_text(), content, name)

    def test_input_output_schemas_are_registered(self) -> None:
        schema = openapi_schema()
        self.assertIn("SearchInput", schema["components"]["schemas"])
        self.assertIn("ErrorResponse", schema["components"]["schemas"])
        self.assertIn("SearchOutput", schema["components"]["schemas"])
