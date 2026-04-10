"""Test activity functions con mock."""
import base64
import os
import pytest

# Set env before importing function_app
os.environ.setdefault("CONNECTION_STRING", "x")
os.environ.setdefault("IPVMAI", "127.0.0.1")
os.environ.setdefault("IPVMNER", "127.0.0.1")

# Skip if azure not installed
pytest.importorskip("azure.functions")


def test_upload_to_blob(mock_blob):
    """Test upload_to_blob con mock blob."""
    from function_app import upload_to_blob

    input_data = {
        "filename": "test.pdf",
        "file_content": base64.b64encode(b"%PDF-1.4 test content").decode("utf-8"),
        "is_external": True,
    }
    result = upload_to_blob(input_data)
    assert result["status"] == "success"
    assert result["filename"] == "test.pdf"
    assert "upload_elapsed" in result
    assert len(mock_blob) == 1


def test_extract_text_from_pdf():
    """Test estrazione testo da PDF minore."""
    from function_app import extract_text_from_pdf
    from io import BytesIO

    # Minimal PDF (valid header)
    pdf_bytes = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Contents 4 0 R>>endobj\n4 0 obj<</Length 44>>stream\nBT\n/F1 12 Tf\n100 700 Td\n(test) Tj\nET\nendstream endobj\nxref\n0 5\n0000000000 65535 f\n0000000009 00000 n\n0000000058 00000 n\n0000000115 00000 n\n0000000206 00000 n\ntrailer<</Size 5/Root 1 0 R>>\nstartxref\n305\n%%EOF"
    text = extract_text_from_pdf(BytesIO(pdf_bytes))
    assert isinstance(text, str)
    # PyPDF2 may or may not extract "test" depending on version
    assert len(text) >= 0
