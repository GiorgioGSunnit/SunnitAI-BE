import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Azure Search has been removed. These functions return empty results.
# Future: implement local vector search (e.g. FAISS or pgvector) as replacement.


def search_chunks(
    search_text: str,
    document_name: Optional[str] = None,
    chapter: Optional[str] = None,
    author: Optional[str] = None,
    use_semantic: bool = False,
    top: int = 10,
) -> Optional[Dict[str, Any]]:
    logger.warning("chunk_retriever: Azure Search removed, search not available.")
    return None


def search_cdp_chunks(search_text: str, top: int = 100) -> Optional[Dict[str, Any]]:
    logger.warning("chunk_retriever: Azure Search removed, search not available.")
    return None


def get_chunk_by_id(chunk_id: str) -> Optional[Dict[str, Any]]:
    logger.warning("chunk_retriever: Azure Search removed, search not available.")
    return None


def save_results_to_file(results: Dict[str, Any], filename: str) -> bool:
    import json
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Errore nel salvataggio: {e}")
        return False
