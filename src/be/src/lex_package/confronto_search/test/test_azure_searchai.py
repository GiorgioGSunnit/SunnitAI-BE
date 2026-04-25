"""
Azure Search has been removed.
This test file is kept as a placeholder.
Future: implement local vector search tests (FAISS, pgvector, etc.) here.
"""
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from chunk_retriever import search_chunks, search_cdp_chunks


def test_search_returns_none():
    """search_chunks is a no-op stub — must return None without raising."""
    result = search_chunks("credito", top=5)
    assert result is None, f"Expected None, got {result}"


def test_cdp_search_returns_none():
    result = search_cdp_chunks("regolamento", top=5)
    assert result is None, f"Expected None, got {result}"


if __name__ == "__main__":
    print("Azure Search removed — running stub verification only.")
    test_search_returns_none()
    test_cdp_search_returns_none()
    print("OK — stubs behave correctly.")
