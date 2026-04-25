import unittest
from requirement_analyzer import (
    extract_article_references,
    process_single_requirement,
    RequirementAnalyzer,
)
import os


class TestRequirementAnalyzer(unittest.TestCase):
    def test_extract_article_references(self):
        self.assertEqual(
            extract_article_references("Il soggetto deve rispettare le regole."),
            "Obbligo",
        )
        self.assertEqual(extract_article_references("È vietato fumare."), "Divieto")
        self.assertEqual(
            extract_article_references("Qualora si verifichi un evento..."),
            "Condizione",
        )
        self.assertEqual(
            extract_article_references("Il termine è di 30 giorni."),
            "Termine temporale",
        )
        self.assertEqual(
            extract_article_references("Testo generico senza pattern."), "altro"
        )

    def test_process_single_requirement(self):
        req = {"requirement": "Il soggetto deve rispettare le regole."}
        pages = ["Questa è una pagina.", "Il soggetto deve rispettare le regole."]
        threshold = 50
        result = process_single_requirement((req, pages, threshold))
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["pattern_type"], "Obbligo")

    def test_split_into_chunks(self):
        analyzer = RequirementAnalyzer()
        text = "word " * 1000  # 1000 words
        chunks = analyzer._split_into_chunks(
            text, chunk_size=100, overlap=10, model="cl100k_base"
        )
        self.assertTrue(len(chunks) > 1)
        self.assertIsInstance(chunks[0], str)

    def test_parse_gpt_response(self):
        analyzer = RequirementAnalyzer()
        raw_response = '["Requisito 1 Etichetta: Etichetta1"]\n["Requisito 2 Etichetta: Etichetta2"]'
        parsed = analyzer._parse_gpt_response(raw_response)
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed[0]["requirement"], "Requisito 1")
        self.assertEqual(parsed[0]["core_text"], "Etichetta1")

    # You can add more tests for PDF extraction and error handling as needed


if __name__ == "__main__":
    unittest.main()
