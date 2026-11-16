import unittest

from backend.core.errors import ERROR_STATUS, ApiError, ErrorResponse


class ErrorTests(unittest.TestCase):
    def test_error_catalog(self) -> None:
        self.assertEqual(len(ERROR_STATUS), 44)
        for code, status in [
            ("UNAUTHENTICATED", 401),
            ("PERMISSION_DENIED", 403),
            ("NOT_FOUND", 404),
            ("VERSION_CONFLICT", 409),
            ("VALIDATION_FAILED", 422),
            ("INTERNAL_ERROR", 500),
        ]:
            self.assertEqual(ApiError(code).status_code, status)

    def test_error_model(self) -> None:
        data = ApiError("NOT_FOUND").response("req").model_dump()
        self.assertEqual(ErrorResponse.model_validate(data).error.request_id, "req")

    def test_next_actions(self) -> None:
        for code in ("PERMISSION_DENIED", "MODEL_NOT_CONFIGURED", "VERSION_CONFLICT", "PRECONDITION_FAILED"):
            self.assertTrue(ApiError(code).response("r").error.next_actions)

    def test_unknown_code(self) -> None:
        with self.assertRaises(ValueError):
            ApiError("NOT_A_CODE")

    def test_upload_status_alternatives(self) -> None:
        self.assertEqual(ApiError("UPLOAD_REJECTED", status_code=415).status_code, 415)
