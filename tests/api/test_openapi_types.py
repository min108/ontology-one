import unittest
from pathlib import Path

from scripts.gen_api_types import artifacts, merge_schema_components, openapi_schema


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

    def test_report_components_are_complete_without_exporting_agent_paths(self) -> None:
        from fastapi.encoders import jsonable_encoder

        from backend.agent_service import Report
        from backend.capabilities.artifact_models import AgentReportRun

        definitions = openapi_schema()["components"]["schemas"]
        self.assertEqual(definitions["Report"], Report.model_json_schema())
        self.assertEqual(
            definitions["AgentReportRun"], jsonable_encoder(AgentReportRun.model_json_schema(), exclude_none=True)
        )
        self.assertFalse(definitions["Report"]["additionalProperties"])
        for name in ("ArtifactResult", "ArtifactDetail", "ArtifactSpec", "ArtifactSource", "ArtifactRevision"):
            self.assertIn(name, definitions)
        generated = artifacts()["types.ts"]
        for name in ("Report", "AgentReportRun", "ArtifactResult", "ArtifactRevision"):
            self.assertIn("export type " + name + " =", generated)

    def test_conflicting_shared_components_are_rejected(self) -> None:
        definitions = {"Shared": {"type": "string"}}
        merge_schema_components(definitions, {"Shared": {"type": "string"}, "Other": {"type": "integer"}})
        self.assertEqual(definitions["Other"], {"type": "integer"})
        with self.assertRaisesRegex(ValueError, "Conflicting OpenAPI component: Shared"):
            merge_schema_components(definitions, {"Shared": {"type": "integer"}})
        self.assertEqual(definitions["Shared"], {"type": "string"})
