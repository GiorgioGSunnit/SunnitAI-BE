import unittest
from unittest.mock import MagicMock, patch
from requirement_extraction import RequirementExtractor
import tempfile
import os


class TestRequirementExtraction(unittest.TestCase):
    def setUp(self):
        self.extractor = RequirementExtractor()

    def test_initialization_defaults(self):
        self.assertEqual(self.extractor.model_name, "dlicari/lsg16k-Italian-Legal-BERT")
        self.assertTrue(hasattr(self.extractor, "tokenizer"))
        self.assertTrue(hasattr(self.extractor, "model"))

    def test_find_requirement_candidates(self):
        text = "Il soggetto deve rispettare le regole. È vietato fumare."
        page_offsets = [0, len(text)]
        candidates = self.extractor._find_requirement_candidates(text, page_offsets)
        self.assertTrue(any("deve" in c["pattern"] for c in candidates))
        self.assertTrue(any("vietato" in c["pattern"] for c in candidates))

    def test_extract_context_simple(self):
        text = "Questa è una frase. Il soggetto deve rispettare le regole. Fine."
        start = text.find("deve")
        end = start + len("deve")
        context = self.extractor.extract_context_simple(text, start, end)
        self.assertIn("Il soggetto deve rispettare le regole", context)

    @patch("requirement_extraction.RequirementExtractor.analyze_with_bert")
    def test_extract_requirements(self, mock_analyze_with_bert):
        mock_analyze_with_bert.return_value = [0.5, 0.8]
        text = "Il soggetto deve rispettare le regole. È vietato fumare."
        page_offsets = [0, len(text)]
        reqs = self.extractor.extract_requirements(text, page_offsets, threshold=0.1)
        self.assertTrue(any("deve" in r["requirement"] for r in reqs))
        self.assertTrue(any("vietato" in r["requirement"] for r in reqs))

    def test_deduplicate_requirements(self):
        reqs = [
            {"requirement": "A", "confidence": 0.9},
            {"requirement": "A", "confidence": 0.8},
            {"requirement": "B", "confidence": 0.7},
        ]
        deduped = self.extractor._deduplicate_requirements(reqs)
        self.assertEqual(len(deduped), 2)
        self.assertTrue(any(r["requirement"] == "A" for r in deduped))
        self.assertTrue(any(r["requirement"] == "B" for r in deduped))

    @patch("requirement_extraction.extract_text")
    def test_extract_text_from_pdf(self, mock_extract_text):
        mock_extract_text.return_value = "Testo PDF"
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(b"%PDF-1.4\n%Fake PDF")
            tmp.seek(0)
            tmp.close()
            with open(tmp.name, "rb") as f:
                f.seek(0)  # Ensure file pointer is at the start
                text = RequirementExtractor.extract_text_from_pdf(f)
            self.assertEqual(text, "Testo PDF")
            os.remove(tmp.name)

    def test_error_on_missing_file(self):
        from requirement_extraction import process_pdf

        with self.assertRaises(FileNotFoundError):
            process_pdf("nonexistent.pdf")


if __name__ == "__main__":
    unittest.main()
