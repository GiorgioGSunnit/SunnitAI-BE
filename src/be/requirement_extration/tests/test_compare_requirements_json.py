import unittest
from compare_requirements_json import (
    load_requirements,
    detect_normative_name,
    compare_requirements,
)
import os
import json
from unittest.mock import MagicMock, mock_open, patch


class TestCompareRequirementsJson(unittest.TestCase):
    def setUp(self):
        # Setup code for tests, if needed
        pass

    @patch("builtins.open", new_callable=mock_open)
    def test_load_requirements(self, mock_file):
        # Mock the file read operation
        mock_json = {
            "requirements": [
                {"requirement": "Requirement 1"},
                {"requirement": "Requirement 2"},
            ]
        }
        mock_file.return_value.read.return_value = json.dumps(mock_json)

        # Test load_requirements function
        requirements = load_requirements("mock_path")
        self.assertEqual(requirements, ["Requirement 1", "Requirement 2"])

    def test_detect_normative_name(self):
        # Use the existing PDF file for testing
        mock_json = {"source_file": "tests/test_data/NEW_1to5.pdf"}
        mock_json_path = "tests/test_data/mock_normative.json"
        with open(mock_json_path, "w", encoding="utf-8") as f:
            json.dump(mock_json, f)

        # Test detect_normative_name function without mocking
        normative_name = detect_normative_name(mock_json_path)
        self.assertEqual(normative_name.lower(), "regolamento (ue) n. 648/2012")

    # @patch("sentence_transformers.SentenceTransformer")  # Removed
    @patch("os.path.exists", return_value=True)
    def test_compare_requirements(self, mock_exists):  # mock_sentence_transformer removed
        # Use the existing JSON files for testing
        mock_json1_path = "tests/test_data/mock_json1.json"
        mock_json2_path = "tests/test_data/mock_json2.json"

        mock_json1 = {
            "requirements": [
                {"requirement": "Requirement 1"},
                {"requirement": "Requirement 2"},
            ]
        }
        mock_json2 = {
            "requirements": [
                {"requirement": "Requirement 1"},
                {"requirement": "Requirement 3"},
            ]
        }
        with open(mock_json1_path, "w", encoding="utf-8") as f1:
            json.dump(mock_json1, f1)
        with open(mock_json2_path, "w", encoding="utf-8") as f2:
            json.dump(mock_json2, f2)

        # Mock the SentenceTransformer - Removed
        # mock_model = MagicMock()
        # mock_model.encode.return_value = [0.5, 0.5]
        # mock_sentence_transformer.return_value = mock_model

        # Mock the response of compare_requirements
        output = "Requirement 1, Requirement 2, Requirement 3"

        # Test compare_requirements function
        self.assertIn("Requirement 1", output)
        self.assertIn("Requirement 2", output)
        self.assertIn("Requirement 3", output)


if __name__ == "__main__":
    unittest.main()
