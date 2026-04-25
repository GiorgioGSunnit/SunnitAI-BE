import json
import asyncio
import logging
from typing import List, Dict, Any
from pathlib import Path
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from src.lex_package.neo_confronto.chunk_generator import convert_r_analysis_to_chunks, save_chunks_to_file

# Azure Search has been removed. Indexing is a no-op.
# Future: implement local vector indexing (e.g. FAISS or pgvector) as replacement.

logger = logging.getLogger(__name__)


def load_chunks() -> List[Dict[str, Any]]:
    chunks_file = Path(__file__).parent / "r_chunks.json"
    try:
        with open(chunks_file, "r", encoding="utf-8") as f:
            chunks = json.load(f)
        logger.info(f"Loaded {len(chunks)} chunks from {chunks_file}")
        return chunks
    except FileNotFoundError:
        logger.error(f"File {chunks_file} not found")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON from {chunks_file}: {e}")
        return []


async def send_chunks_parallel(chunks: List[Dict[str, Any]], max_concurrent: int = 10) -> None:
    logger.warning("chunk_indexer: Azure Search removed — indexing skipped.")


async def main():
    logger.info("Converting R analysis to chunks...")
    chunks_data = convert_r_analysis_to_chunks()
    save_chunks_to_file(chunks_data, "r_chunks.json")
    logger.info(f"Generated {len(chunks_data)} chunks (indexing to remote search is disabled).")


if __name__ == "__main__":
    asyncio.run(main())
