import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from call_fast_api import app

client = TestClient(app)


class TestCallFastApi(unittest.TestCase):
    def setUp(self):
        # Setup code for tests, if needed
        pass

    @patch("call_fast_api.RequirementExtractor.extract_text_from_pdf")
    @patch("call_fast_api.upload_to_blob")
    def test_extract_requirements(
        self, mock_upload_to_blob, mock_extract_text_from_pdf
    ):
        # Simulate successful extraction
        mock_extract_text_from_pdf.return_value = "Extracted text example"
        mock_upload_to_blob.return_value = None

        with open("tests/test_data/NEW_1to5.pdf", "rb") as pdf_file:
            response = client.post(
                "/extract-requirements/",
                files={"file": ("NEW_1to5.pdf", pdf_file, "application/pdf")},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("requirements", data)
        self.assertIsInstance(data["requirements"], list)
        self.assertGreater(len(data["requirements"]), 0)

    @patch("call_fast_api.upload_to_blob")
    def test_compare_requirements(self, mock_upload_to_blob):
        with (
            open("tests/test_data/NEW_1to5.pdf", "rb") as pdf_file1,
            open("tests/test_data/OLD_1to5.pdf", "rb") as pdf_file2,
        ):
            response = client.post(
                "/compare-requirements/",
                files={
                    "file1": ("NEW_1to5.pdf", pdf_file1, "application/pdf"),
                    "file2": ("OLD_1to5.pdf", pdf_file2, "application/pdf"),
                },
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("output", data)
        self.assertIn("Analisi Comparativa Documenti Normativi", data["output"])

    @patch("call_fast_api.pdf_json_mapping.mapping", {"NEW_1to5": "NEW_1to5.json"})
    def test_get_extracted_requirements(self):
        # Simulate successful retrieval
        response = client.get("/api/v0/documents/NEW_1to5/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("source_file", response.json())


if __name__ == "__main__":
    unittest.main()
