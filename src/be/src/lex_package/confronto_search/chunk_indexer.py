import json
import asyncio
import aiohttp
import logging
from typing import List, Dict, Any
from pathlib import Path
import sys
import os

# Aggiungi il path del progetto al sys.path per permettere gli import assoluti
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))

from src.lex_package.neo_confronto.chunk_generator import convert_r_analysis_to_chunks, save_chunks_to_file


# Azure Search AI configuration
ENDPOINT = "https://cdpaisearch.search.windows.net"
INDEX_NAME = "azureblob-data-index"
API_KEY = os.getenv("AZURE_SEARCH_ADMIN_KEY", "")

# Headers for the request
HEADERS = {"Content-Type": "application/json", "api-key": API_KEY}

# API parameters - using newer version that supports semantic search
PARAMS = {"api-version": "2023-11-01"}

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def load_chunks() -> List[Dict[str, Any]]:
    """Load chunks from r_chunks.json file"""
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


def prepare_document_for_indexing(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """Convert chunk format to Azure Search document format"""
    # Adatta i campi ai nomi disponibili nell'indice Azure Search
    # Il campo chiave può contenere solo lettere, numeri, underscore, dash e uguale
    safe_key = f"CDP-R-{chunk['id']}"  # Chiave sicura senza caratteri speciali
    
    return {
        "@search.action": "upload",
        "metadata_storage_path": safe_key,  # Campo chiave
        "metadata_storage_name": chunk["fileName"],
        "content": f"Chapter: {chunk['chapter']}\nSection: {chunk['section']}\nPage: {chunk['page']}\n\n{chunk['content']}",
        "metadata_author": "CDP System",
    }


async def send_chunk_to_azure(
    session: aiohttp.ClientSession, chunk: Dict[str, Any]
) -> bool:
    """Send a single chunk to Azure Search AI for indexing"""
    url = f"{ENDPOINT}/indexes/{INDEX_NAME}/docs/index"

    # Prepare document for Azure Search
    document = prepare_document_for_indexing(chunk)
    payload = {"value": [document]}

    try:
        async with session.post(
            url, headers=HEADERS, params=PARAMS, json=payload
        ) as response:
            if response.status == 200 or response.status == 201:
                logger.info(f"Successfully indexed chunk {chunk['id']}")
                return True
            else:
                error_text = await response.text()
                logger.error(
                    f"Failed to index chunk {chunk['id']}: {response.status} - {error_text}"
                )
                return False

    except Exception as e:
        logger.error(f"Error sending chunk {chunk['id']} to Azure: {str(e)}")
        return False


async def send_chunks_parallel(
    chunks: List[Dict[str, Any]], max_concurrent: int = 10
) -> None:
    """Send all chunks to Azure Search AI in parallel with concurrency control"""
    if not chunks:
        logger.warning("No chunks to send")
        return

    logger.info(
        f"Starting to index {len(chunks)} chunks with max {max_concurrent} concurrent requests"
    )

    # Create semaphore to limit concurrent requests
    semaphore = asyncio.Semaphore(max_concurrent)

    async def send_with_semaphore(
        session: aiohttp.ClientSession, chunk: Dict[str, Any]
    ) -> bool:
        async with semaphore:
            return await send_chunk_to_azure(session, chunk)

    # Create HTTP session with timeout
    timeout = aiohttp.ClientTimeout(total=60)  # 60 seconds timeout

    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Create tasks for all chunks
        tasks = [send_with_semaphore(session, chunk) for chunk in chunks]

        # Execute all tasks and gather results
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Count successful indexing operations
        successful = sum(1 for result in results if result is True)
        failed = len(chunks) - successful

        logger.info(f"Indexing completed: {successful} successful, {failed} failed")

        if failed > 0:
            logger.warning(
                f"{failed} chunks failed to be indexed. Check logs for details."
            )


async def get_index_schema() -> Dict[str, Any]:
    """Get the schema of the Azure Search index to understand available fields"""
    url = f"{ENDPOINT}/indexes/{INDEX_NAME}"
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=HEADERS, params=PARAMS) as response:
                if response.status == 200:
                    schema = await response.json()
                    logger.info("Successfully retrieved index schema")
                    return schema
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to get index schema: {response.status} - {error_text}")
                    return {}
        except Exception as e:
            logger.error(f"Error retrieving index schema: {str(e)}")
            return {}


async def main():
    """Main function to orchestrate the indexing process"""
    # First, let's check the index schema
    logger.info("Retrieving Azure Search index schema...")
    schema = await get_index_schema()
    
    if schema:
        fields = schema.get("fields", [])
        logger.info("Available fields in the index:")
        for field in fields:
            logger.info(f"  - {field.get('name', 'unknown')} (type: {field.get('type', 'unknown')}, key: {field.get('key', False)})")
    
    # Generate R chunks first
    logger.info("Converting R analysis to chunks...")
    chunks_data = convert_r_analysis_to_chunks()

    # Save chunks to file
    save_chunks_to_file(chunks_data, "r_chunks.json")
    logger.info(f"Generated and saved {len(chunks_data)} R chunks")
    logger.info("Starting Azure Search AI indexing process")

    # Load chunks from JSON file
    chunks = load_chunks()

    if not chunks:
        logger.error("No chunks to process. Exiting.")
        return

    # Send chunks to Azure Search AI in parallel
    await send_chunks_parallel(chunks, max_concurrent=10)

    logger.info("Indexing process completed")


if __name__ == "__main__":
    asyncio.run(main())
