import base64
import threading
import time
import uuid
from pathlib import Path
from fastapi import FastAPI, Response, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from requirement_extraction import RequirementExtractor
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
import logging
from dotenv import load_dotenv
import shutil
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
import subprocess
import json
import pandas as pd
import re
import requests
import sys
import platform
import fitz
from typing import Dict, Any, Optional, Union, Tuple, Callable, List
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel
import markdown2
import tiktoken
from extration_utils import (
    compute_file_hash,
    get_python_path,
    PDFToJsonMapping,
    print_mapping,
    save_pdf_mapping,
    load_pdf_mapping,
    upload_to_blob,
    check_file_exists,
    blob_exists,
    download_from_blob,
    get_blob_bytes,
    cleanup_local_json_files,
)

# Import the analyzer and comparison modules directly
from requirement_analyzer import RequirementAnalyzer, str2bool
import compare_requirements_json

# Import the new analyzer
from lex_package.analisi import analisi
from lex_package.utils.flatten import flatten_analisi_invertito as flatten_analisi
from lex_package.utils.to_xlsx import write_records_to_xlsx
from lex_package.utils.confronto_xlsx_vista import write_confronto_vista_copy
from lex_package.utils.flatten import (
    flatten_confronto_emendativo,
    add_articoli_non_attuati,
    flatten_schema_attuativo,
    flat_confronto_attuativo_seconda_meta,
    flatten_confronto_versioning,
)
from lex_package.emendativa_confronto import confronto_emendativo
from lex_package.schema_attuativo import confronto_attuativo
from lex_package.utils.integrazione_confronto_attuativo import (
    integrazione_confronto_attuativo_confronto_titoli,
    integrazione_confronto_attuativo_confronto_commi,
    select_best_matches,
)
from lex_package.utils.retry_progress import stop_llm_progress
from lex_package.versioning_confronto import confronto_versioning
from lex_package.utils.normalize_articoli_tree import (
    ensure_identificativo_fields_for_confronto,
    normalizza_gerarchia_articoli,
)

# Import the search module (now with proper naming)
from searchAI_fulltext import EnhancedSearchService

load_dotenv()

# --- Constants for sum.json data handling (ported from function_app.py) ---
BLOB_CONFIG_CONTAINER = "conf"  # Container for sum.json
SUM_BLOB_FILENAME = "sum.json"
MAX_HISTORY_ENTRIES = 10  # For operation history within sum.json

# Configure logging centrally
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s:%(lineno)d - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logging.getLogger("requirement_analyzer").setLevel(logging.DEBUG)
logging.getLogger("rich").setLevel(logging.INFO)
logging.getLogger("fastapi").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# Add logging for lex_package modules
logging.getLogger("lex_package").setLevel(logging.DEBUG)
logging.getLogger("lex_package.schema_attuativo").setLevel(logging.DEBUG)
logging.getLogger("lex_package.emendativa_confronto").setLevel(logging.DEBUG)
logging.getLogger("lex_package.versioning_confronto").setLevel(logging.DEBUG)
logging.getLogger("lex_package.utils").setLevel(logging.DEBUG)
logging.getLogger("lex_package.analisi").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)
logger.info("FastAPI application starting with centralized logging.")

os.environ["SEARCH_KEY"] = os.getenv("SEARCH_KEY")
os.environ["TRANSLATOR_KEY"] = os.getenv("TRANSLATOR_KEY")
os.environ["TRANSLATOR_LOCATION"] = os.getenv("TRANSLATOR_LOCATION")

blob_service = BlobServiceClient.from_connection_string(os.getenv("CONNECTION_STRING"))
container_name = os.getenv("CONTAINER_NAME")
container_client = blob_service.get_container_client(container_name)

app = FastAPI()

# Global store for analysis progress
analysis_progress_store: Dict[str, Dict[str, Any]] = {}
LATEST_ANALYSIS_RUN_ID: Optional[str] = (
    None  # Tracks the run_id of the most recently initiated analysis by /extract-requirements/
)
PROGRESS_CLEANUP_TIMEOUT_SECONDS = 300  # 5 minutes


# --- Progress Cleanup Function ---
def cleanup_old_progress_entries():
    """Remove progress entries older than PROGRESS_CLEANUP_TIMEOUT_SECONDS."""
    current_time = datetime.now(timezone.utc)
    entries_to_remove = []

    for run_id, progress_data in analysis_progress_store.items():
        # Check if removal is scheduled
        removal_scheduled_str = progress_data.get("removal_scheduled_at")
        if removal_scheduled_str:
            try:
                removal_time = datetime.fromisoformat(
                    removal_scheduled_str.replace("Z", "+00:00")
                )
                if current_time >= removal_time:
                    entries_to_remove.append(run_id)
                    continue
            except Exception as e:
                logger.warning(
                    f"Error parsing removal_scheduled_at for run_id {run_id}: {e}"
                )

        # Check if entry is too old
        last_update_str = progress_data.get("last_update_timestamp")
        if last_update_str:
            try:
                last_update = datetime.fromisoformat(
                    last_update_str.replace("Z", "+00:00")
                )
                if (
                    current_time - last_update
                ).total_seconds() > PROGRESS_CLEANUP_TIMEOUT_SECONDS:
                    entries_to_remove.append(run_id)
            except Exception as e:
                logger.warning(f"Error parsing timestamp for run_id {run_id}: {e}")
                entries_to_remove.append(run_id)

    for run_id in entries_to_remove:
        logger.info(f"Removing old progress entry for run_id: {run_id}")
        analysis_progress_store.pop(run_id, None)

    # Also reset LATEST_ANALYSIS_RUN_ID if it's being removed
    global LATEST_ANALYSIS_RUN_ID
    if LATEST_ANALYSIS_RUN_ID in entries_to_remove:
        LATEST_ANALYSIS_RUN_ID = None


# --- Progress Update Callback ---
def update_analysis_progress(run_id: str, progress_data: Dict[str, Any]):
    if run_id in analysis_progress_store:
        analysis_progress_store[run_id].update(progress_data)
        analysis_progress_store[run_id]["last_update_timestamp"] = datetime.now(
            timezone.utc
        ).isoformat()
    else:
        # This case should ideally be handled by initializing the run_id entry first
        analysis_progress_store[run_id] = {
            **progress_data,
            "last_update_timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "processing",  # Default status if not set by initial call
        }

    # Clean up old entries periodically
    cleanup_old_progress_entries()

    # If the status is a terminal state (completed, error), schedule removal after a delay
    terminal_statuses = [
        "completed_analysis",
        "completed_finalized",
        "completed_from_cache",
        "error_validation",
        "error_file_save",
        "error_reading_cache",
        "error_output_format",
        "error_in_processing",
    ]

    if progress_data.get("status") in terminal_statuses:
        # Mark for removal after a short delay (30 seconds)
        analysis_progress_store[run_id]["removal_scheduled_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=30)
        ).isoformat()

    # logger.debug(f"[Run ID: {run_id}] Progress updated: {analysis_progress_store[run_id]}")


origins = [
    "*",
    "https://cdpwebapp.z28.web.core.windows.net/",
    "https://cdpbedurable.azurewebsites.net/api",
    "https://www.sunnitai.it",
    "https://www.aipercompliance.it",
    "https://sasunnitdemowebapp.z6.web.core.windows.net/",
    "https://sunnitbe.azurewebsites.net/api",
    "https://sunnitbedurable.azurewebsites.net/api",
    "https://sunnitaidemo.sunnit.it/"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

extractor = RequirementExtractor()
task_store = {}
pdf_json_mapping = PDFToJsonMapping.get_instance()
MAPPING_FILE_PATH = "pdf_mapping.json"
logger.info("Starting FastAPI application with updated logging configuration")
if not Path(MAPPING_FILE_PATH).exists():
    save_pdf_mapping({}, MAPPING_FILE_PATH)
    print_mapping(pdf_json_mapping.mapping)


def normalize_filename(filename: str) -> str:
    normalized = filename.replace(" ", "_")
    logger.debug(f"Normalizzato '{filename}' in '{normalized}'")
    return normalized


def upload_confronto_vista_excel(
    source_xlsx: Path,
    pdf_name_1: str,
    pdf_name_2: str,
    blob_container: str,
) -> None:
    """Dopo l'XLSX principale di confronto, genera e carica la versione vista (layout + nome leggibile)."""
    try:
        vista_path = write_confronto_vista_copy(source_xlsx, pdf_name_1, pdf_name_2)
        upload_to_blob(
            blob_container,
            file_path=vista_path,
            blob_name=vista_path.name,
        )
        logger.info(f"Caricato XLSX confronto vista: {vista_path.name}")
    except Exception as e:
        logger.warning(f"Impossibile generare/caricare confronto_vista xlsx: {e}")


async def check_and_retrieve_resource(
    category: str, filename: str, destination_path: Path
) -> Tuple[bool, str]:
    logger.info(f"Checking for resource {category}/{filename}")
    if check_file_exists(str(destination_path)):
        logger.info(f"Resource {filename} found in filesystem at {destination_path}")
        return True, "filesystem"
    if category == "amendments" and "_vs_" in filename:
        amending_path = Path("./amending_results") / filename
        if check_file_exists(str(amending_path)):
            logger.info(
                f"Resource {filename} found in amending_results at {amending_path}"
            )
            try:
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(amending_path, destination_path)
                logger.info(
                    f"Copied {filename} from amending_results to {destination_path}"
                )
                return True, "amending_directory"
            except Exception as e:
                logger.warning(f"Failed to copy from amending_results: {e}")
    if blob_exists(category, filename):
        logger.info(f"Resource {filename} found in blob storage")
        success = download_from_blob(category, filename, destination_path)
        if success:
            logger.info(f"Downloaded {filename} from blob to {destination_path}")
            return True, "blob"
        else:
            logger.warning(f"Failed to download {filename} from blob")
    logger.warning(f"Resource {category}/{filename} not found")
    return False, "not_found"


# --- New Progress Endpoint ---
@app.get(
    "/analysis-progress/", response_model=Dict[str, Any]
)  # Changed route, removed path param
async def get_analysis_progress(
    run_id_query: Optional[str] = Query(None, alias="run_id")
):
    """Retrieve analysis progress.
    If 'run_id' query parameter is provided, fetches for that ID.
    Otherwise, attempts to fetch progress for the most recently initiated run.
    """
    # Clean up old entries before processing request
    cleanup_old_progress_entries()

    global LATEST_ANALYSIS_RUN_ID
    target_run_id = run_id_query or LATEST_ANALYSIS_RUN_ID

    log_prefix_inquiry = (
        f"[Progress Inquiry for {target_run_id if target_run_id else 'latest active'}] "
    )

    if not target_run_id:
        logger.warning(
            f"{log_prefix_inquiry}No specific run_id provided and no LATEST_ANALYSIS_RUN_ID is set."
        )
        raise HTTPException(
            status_code=404,
            detail="No analysis run specified and no default run available to track.",
        )

    if target_run_id in analysis_progress_store:
        logger.info(
            f"{log_prefix_inquiry}Progress requested. Data: {analysis_progress_store[target_run_id]}"
        )
        return analysis_progress_store[target_run_id]
    else:
        logger.warning(
            f"{log_prefix_inquiry}Progress requested but run_id '{target_run_id}' not found in store."
        )
        raise HTTPException(
            status_code=404,
            detail=f"Analysis progress for run_id '{target_run_id}' not found.",
        )


@app.post("/compare-requirements/", response_model=Dict[str, Any])
async def compare_requirements(
    file1: UploadFile = File(...),
    file2: UploadFile = File(...),
    comparisonMode: Optional[str] = Query(
        None,
        description='Modalità di confronto: "emendativa", "attuativa" o "versioning"',
    ),
):
    logger.info(f"[COMPARISON MODE]: {comparisonMode}")
    logger.info(
        f"Ricevuti due file per comparazione: {file1.filename} e {file2.filename}"
    )

    global LATEST_ANALYSIS_RUN_ID
    run_id = str(uuid.uuid4())
    if LATEST_ANALYSIS_RUN_ID is None:
        LATEST_ANALYSIS_RUN_ID = run_id
    log_prefix = f"[Run ID: {run_id}] [Endpoint] "

    analysis_progress_store[run_id] = {
        "run_id": run_id,
        "status": "starting",
        "file1": file1.filename,
        "file2": file2.filename,
        "start_time_utc": get_current_timestamp(),
        "last_update_timestamp": get_current_timestamp(),
        "message": "Processing initiated.",
    }

    logger.info(
        f"{log_prefix}Set as LATEST_ANALYSIS_RUN_ID. Initialized progress store. Received files: {file1.filename} and {file2.filename}"
    )

    # parsing del parametro mode
    valid_modes = ["emendativa", "attuativa", "versioning"]
    comparisonMode = comparisonMode.lower()
    if comparisonMode not in valid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Modalità di confronto non valida. Valori validi: {', '.join(valid_modes)}",
        )

    selected_mode = comparisonMode

    # supporto versione vecchia
    os.environ["AMENDING_MODE"] = "true" if selected_mode == "emendativa" else "false"

    update_analysis_progress(run_id, {"status": "checking_cache"})

    if not file1.filename.endswith(".pdf") or not file2.filename.endswith(".pdf"):
        logger.error("Uno dei due file non è un PDF.")
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    normalized_filename1 = normalize_filename(file1.filename)
    normalized_filename2 = normalize_filename(file2.filename)

    # Definizione dei percorsi temporanei per salvare i PDF
    tmp_dir = Path("./tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    temp_file1_path = tmp_dir / normalized_filename1
    temp_file2_path = tmp_dir / normalized_filename2

    # Salvataggio dei file PDF sul filesystem
    try:
        with open(temp_file1_path, "wb") as f1:
            shutil.copyfileobj(file1.file, f1)
        with open(temp_file2_path, "wb") as f2:
            shutil.copyfileobj(file2.file, f2)
        logger.info(f"File temporanei salvati: {temp_file1_path}, {temp_file2_path}")
    except Exception as e:
        logger.exception("Errore durante il salvataggio dei file temporanei:")
        raise HTTPException(
            status_code=500, detail="Errore nel salvataggio dei file temporanei"
        )

    # Calcolo degli hash dei PDF e aggiornamento mapping
    try:
        hash1 = compute_file_hash(str(temp_file1_path))
        hash2 = compute_file_hash(str(temp_file2_path))
        logger.info(f"Hash file: {hash1} e {hash2}")

        base_name1 = normalized_filename1.replace(".pdf", "")
        pdf_json_mapping.mapping[base_name1] = f"{hash1}.json"

        base_name2 = normalized_filename2.replace(".pdf", "")
        pdf_json_mapping.mapping[base_name2] = f"{hash2}.json"

        save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)
        print_mapping(pdf_json_mapping.mapping)
        logger.info(f"[COMPARE-POST] Mappa aggiornata: {pdf_json_mapping.mapping}")
        logger.info(
            f"[COMPARE-POST] comparisonMapping: {pdf_json_mapping.comparisonMapping}"
        )

    except Exception as e:
        logger.exception(f"Errore durante il calcolo degli hash:")
        raise HTTPException(status_code=500, detail="Errore nel calcolo dell'hash")

    # Definizione dei percorsi dei file JSON di estrazione
    output_dir = Path("./output")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path1 = output_dir / f"{hash1}.json"
    json_path2 = output_dir / f"{hash2}.json"

    # Genera il nome del file di confronto in modo consistente ordinando i due hash
    sorted_hashes = sorted([hash1, hash2])
    comparison_filename = f"{sorted_hashes[0]}_vs_{sorted_hashes[1]}.json"
    comparison_json_path = output_dir / comparison_filename
    logger.info(f"File di confronto previsto: {comparison_json_path}")

    # Check if the comparison file exists in filesystem or blob storage
    exists, location = await check_and_retrieve_resource(
        "comparisons", comparison_filename, comparison_json_path
    )

    if exists:
        logger.info(f"Comparison file found in {location}: {comparison_filename}")
        try:
            with open(comparison_json_path, "r", encoding="utf-8") as f:
                result = json.load(f)

            # Update mapping for the comparison
            comparison_key = f"{normalized_filename1.replace('.pdf','')}_{normalized_filename2.replace('.pdf','')}"
            pdf_json_mapping.comparisonMapping[comparison_key] = comparison_filename
            save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)

            update_analysis_progress(
                run_id,
                {
                    "status": "completed_comparison",
                    "percent_done": 100.0,
                    "message": "Comparison retrieved from cache",
                },
            )

            logger.info(
                f"[COMPARE-POST] Mapping confronto aggiornato: {pdf_json_mapping.comparisonMapping}"
            )

            # If the cached result doesn't have the new structure, wrap it
            if isinstance(result, list) or (
                isinstance(result, dict) and "comparison_data" not in result
            ):
                result = {
                    "file1_name": normalized_filename1,
                    "file2_name": normalized_filename2,
                    "comparison_mode": selected_mode,
                    "comparison_data": result,
                    "timestamp": get_current_timestamp(),
                }

            # Remove local JSON artefacts
            cleanup_local_json_files()

            return result
        except Exception as e:
            logger.error(f"Error reading existing comparison file: {str(e)}")
            # Continue with comparison if we can't read the existing file

    try:
        # Initialize analyzer for direct calls
        azure_config = {
            "api_key": os.getenv("AZURE_OPENAI_API_KEY"),
            "azure_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT"),
            "api_version": os.getenv("AZURE_API_VERSION", "2024-08-01-preview"),
        }
        analyzer = RequirementAnalyzer(
            backend="azure_openai", azure_config=azure_config
        )

        # Check both JSON files exist, if not extract them
        file1_exists, location1 = await check_and_retrieve_resource(
            "requirements", f"{hash1}.json", json_path1
        )
        file2_exists, location2 = await check_and_retrieve_resource(
            "requirements", f"{hash2}.json", json_path2
        )

        logger.info(f"Checking JSON files for comparison:")
        logger.info(
            f"  File 1 ({base_name1}): {json_path1} - {'EXISTS in ' + location1 if file1_exists else 'MISSING'}"
        )
        logger.info(
            f"  File 2 ({base_name2}): {json_path2} - {'EXISTS in ' + location2 if file2_exists else 'MISSING'}"
        )

        extraction_needed = not (file1_exists and file2_exists)
        if extraction_needed:
            update_analysis_progress(
                run_id,
                {"status": "extraction_needed", "message": "Extraction needed"},
            )
            logger.info(
                "=== Some JSON files are missing, extraction will be performed ==="
            )

        # Set amending mode environment variable
        if selected_mode == "emendativa":
            os.environ["AMENDING_MODE"] = "true"
        else:
            os.environ["AMENDING_MODE"] = "false"

        USE_NEW_COMPARISON = True
        if USE_NEW_COMPARISON:
            logger.info(f"{log_prefix}Using new lex_package for comparison.")

            # Verifica se i file flattened esistono nel blob, altrimenti li genera
            if selected_mode == "attuativa":
                analisi_dir = Path("./out_analisi")
                analisi_dir.mkdir(parents=True, exist_ok=True)
            elif selected_mode == "emendativa":
                analisi_dir = Path("./out_flat/out_analisi")
                analisi_dir.mkdir(parents=True, exist_ok=True)
            elif selected_mode == "versioning":
                analisi_dir = Path("./out_flat/out_analisi")
                analisi_dir.mkdir(parents=True, exist_ok=True)

            json_1 = analisi_dir / f"{hash1}.json"
            json_2 = analisi_dir / f"{hash2}.json"

            logger.info(f"[FLAT JSON 1]: {json_1}")
            logger.info(f"[FLAT JSON 2]: {json_2}")

            # Funzione helper per scaricare o generare i file flattened
            async def ensure_flattened_analysis(
                pdf_path, pdf_name, hash_value, flat_json_path, is_flattened=False
            ):
                # Prima cerca nel blob storage
                if is_flattened:
                    blob_name = f"{hash_value}_flattened.json"
                else:
                    blob_name = f"{hash_value}.json"

                logger.info(
                    f"Checking blob storage for {'flattened' if is_flattened else 'original'} analysis: {blob_name}"
                )

                if blob_exists("requirements", blob_name):
                    logger.info(f"Found {blob_name} in blob storage, downloading...")
                    success = download_from_blob(
                        "requirements", blob_name, flat_json_path
                    )
                    if success and flat_json_path.exists():
                        logger.info(
                            f"Successfully downloaded {blob_name} from blob storage"
                        )

                        # Per modalità attuativa, non serve verificare la struttura
                        # perché usiamo direttamente i dati originali (non flattened)
                        if not is_flattened and selected_mode == "attuativa":
                            return

                        # Per altre modalità, verifica se il file scaricato è già flattened o ha la struttura completa
                        try:
                            with open(flat_json_path, "r", encoding="utf-8") as f:
                                data = json.load(f)

                            # Se il file ha la struttura {"articoli": [...]}, estrai solo gli articoli
                            if isinstance(data, dict) and "articoli" in data:
                                logger.info(
                                    f"Downloaded file has full structure, extracting 'articoli' field"
                                )
                                flattened_data = data["articoli"]
                                # Riscrivi il file con solo i dati flattened
                                with open(flat_json_path, "w", encoding="utf-8") as f:
                                    json.dump(
                                        flattened_data, f, ensure_ascii=False, indent=4
                                    )
                                logger.info(
                                    f"Extracted and saved flattened data for {blob_name}"
                                )
                            elif isinstance(data, list):
                                logger.info(
                                    f"Downloaded file is already flattened (list format)"
                                )
                            else:
                                logger.warning(
                                    f"Unexpected JSON structure in {blob_name}: {type(data)}"
                                )
                                # Se la struttura non è riconosciuta, segnala errore
                                raise HTTPException(
                                    status_code=500,
                                    detail=f"Invalid JSON structure in {blob_name}. Please re-analyze the file.",
                                )

                        except json.JSONDecodeError as e:
                            logger.error(
                                f"Error decoding JSON file {blob_name}: {str(e)}"
                            )
                            raise HTTPException(
                                status_code=500,
                                detail=f"Corrupted JSON file {blob_name}. Please re-analyze the file.",
                            )
                        except HTTPException:
                            raise  # Re-raise HTTP exceptions
                        except Exception as e:
                            logger.error(
                                f"Error processing downloaded file {blob_name}: {str(e)}"
                            )
                            raise HTTPException(
                                status_code=500,
                                detail=f"Error processing file {blob_name}: {str(e)}",
                            )

                        # Se tutto è andato bene, ritorna
                        return
                    else:
                        logger.warning(
                            f"Failed to download {blob_name} from blob storage"
                        )

                # Se non trovato nel blob, NON generare l'analisi
                logger.error(
                    f"Analysis file {blob_name} not found in blob storage for {pdf_name}"
                )
                raise HTTPException(
                    status_code=404,
                    detail=f"Analysis not found for '{pdf_name}'. Please first analyze this file using the /extract-requirements/ endpoint.",
                )

            # Assicurati che entrambi i file flattened esistano
            try:
                # Per modalità attuativa, usa i file originali (non flattened)
                # Per altre modalità, usa i file flattened
                is_flattened = selected_mode != "attuativa"

                await ensure_flattened_analysis(
                    temp_file1_path,
                    normalized_filename1,
                    hash1,
                    json_1,
                    is_flattened,
                )
                await ensure_flattened_analysis(
                    temp_file2_path,
                    normalized_filename2,
                    hash2,
                    json_2,
                    is_flattened,
                )
            except HTTPException:
                raise  # Re-raise HTTP exceptions as-is
            except Exception as e:
                logger.error(f"Unexpected error in ensure_flattened_analysis: {str(e)}")
                raise HTTPException(
                    status_code=500, detail=f"Error preparing analysis files: {str(e)}"
                )

            if selected_mode == "emendativa":
                logger.info("============= MODALITA' EMENDATIVA =============")
                update_analysis_progress(
                    run_id,
                    {
                        "status": "llm_comparison_starting",
                        "message": "Starting emendativa comparison",
                    },
                )

                try:
                    logger.info(f"json_1: {json_1}")
                    with open(json_1, "r", encoding="utf-8") as f:
                        articoli_emendare = json.load(f)
                    logger.info(
                        f"Loaded articoli_emendare: type={type(articoli_emendare)}, length={len(articoli_emendare) if isinstance(articoli_emendare, list) else 'N/A'}"
                    )

                    logger.info(f"json_2: {json_2}")
                    with open(json_2, "r", encoding="utf-8") as f:
                        articoli_emendativa = json.load(f)
                    logger.info(
                        f"Loaded articoli_emendativa: type={type(articoli_emendativa)}, length={len(articoli_emendativa) if isinstance(articoli_emendativa, list) else 'N/A'}"
                    )

                    # Verifica che siano liste
                    if not isinstance(articoli_emendare, list):
                        raise ValueError(
                            f"articoli_emendare is not a list, it's {type(articoli_emendare)}"
                        )
                    if not isinstance(articoli_emendativa, list):
                        raise ValueError(
                            f"articoli_emendativa is not a list, it's {type(articoli_emendativa)}"
                        )

                except Exception as e:
                    logger.error(
                        f"Error loading JSON files for emendativa comparison: {str(e)}"
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Error loading analysis files: {str(e)}",
                    )

                start_ts = get_current_timestamp()
                update_analysis_progress(
                    run_id,
                    {"status": "llm_comparison_in_progress", "percent_done": 30.0},
                )

                comparison_raw = await confronto_emendativo(
                    articoli_emendare, articoli_emendativa
                )
                flat_comparison = flatten_confronto_emendativo(comparison_raw)
                end_ts = get_current_timestamp()
                flat_out_dir = Path("./out_flat/out_confronto_emendativo")
                flat_out_dir.mkdir(parents=True, exist_ok=True)

                # Create the result structure with file names and comparison data
                result_data = {
                    "file1_name": normalized_filename1,
                    "file2_name": normalized_filename2,
                    "comparison_mode": "emendativa",
                    "comparison_data": flat_comparison,
                    "timestamp": end_ts,
                }

                with open(
                    flat_out_dir / comparison_filename,
                    "w",
                    encoding="utf-8",
                ) as f:
                    json.dump(result_data, f, ensure_ascii=False, indent=4)

                upload_to_blob(
                    # "comparisons",
                    "amendments",
                    file_path=flat_out_dir / comparison_filename,
                    blob_name=comparison_filename,
                )

                comparison_spreasheet = comparison_filename.replace(".json", ".xlsx")
                write_records_to_xlsx(
                    flat_comparison,
                    flat_out_dir / comparison_spreasheet,
                )

                upload_to_blob(
                    # "comparisons",
                    "amendments",
                    file_path=flat_out_dir / comparison_spreasheet,
                    blob_name=comparison_spreasheet,
                )
                upload_confronto_vista_excel(
                    flat_out_dir / comparison_spreasheet,
                    normalized_filename1,
                    normalized_filename2,
                    "amendments",
                )

                token_total = len(get_tokens(json.dumps(articoli_emendare))) + len(
                    get_tokens(json.dumps(articoli_emendativa))
                )

                update_sum_data(
                    elapsed_time=calculate_seconds_between(start_ts, end_ts),
                    token_count=token_total,
                    mode="comparison",
                    connection_string=os.getenv("CONNECTION_STRING"),
                    start_timestamp=start_ts,
                    end_timestamp=end_ts,
                )

                update_analysis_progress(
                    run_id,
                    {
                        "status": "completed_comparison",
                        "percent_done": 100.0,
                        "message": "Confronto emendativo completato",
                    },
                )

                comparison_key = f"{normalized_filename1.replace('.pdf','')}_{normalized_filename2.replace('.pdf','')}"
                pdf_json_mapping.comparisonMapping[comparison_key] = comparison_filename
                save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)

                stop_llm_progress()
                return result_data

            elif selected_mode == "attuativa":
                logger.info("============= MODALITA' ATTUATIVA =============")
                update_analysis_progress(
                    run_id,
                    {
                        "status": "llm_comparison_starting",
                        "message": "Starting attuativa comparison",
                    },
                )

                try:
                    # file1 = norma esterna (EXT / Documento 285); file2 = regolamento interno (INT da attuare)
                    logger.info(f"json_1 (attuativo / esterno): {json_1}")
                    with open(json_1, "r", encoding="utf-8") as f:
                        articoli_attuativo = json.load(f)
                    logger.info(
                        f"Loaded articoli_attuativo: name={json_1} type={type(articoli_attuativo)}, length={len(articoli_attuativo) if isinstance(articoli_attuativo, list) else 'N/A'}"
                    )

                    logger.info(f"json_2 (da attuare / interno): {json_2}")
                    with open(json_2, "r", encoding="utf-8") as f:
                        articoli_attuare = json.load(f)
                    logger.info(
                        f"Loaded articoli_attuare: name={json_2} type={type(articoli_attuare)}, length={len(articoli_attuare) if isinstance(articoli_attuare, list) else 'N/A'}"
                    )

                    # --- normalizza struttura --------------------------------------------------
                    # Se il JSON ha struttura { "articoli": [...] } estrai la lista
                    if (
                        isinstance(articoli_attuare, dict)
                        and "articoli" in articoli_attuare
                    ):
                        articoli_attuare = articoli_attuare["articoli"]
                    if (
                        isinstance(articoli_attuativo, dict)
                        and "articoli" in articoli_attuativo
                    ):
                        articoli_attuativo = articoli_attuativo["articoli"]

                    # Assicura che ora siano liste
                    if not isinstance(articoli_attuare, list):
                        raise ValueError(
                            f"articoli_attuare is not a list, it's {type(articoli_attuare)}"
                        )
                    if not isinstance(articoli_attuativo, list):
                        raise ValueError(
                            f"articoli_attuativo is not a list, it's {type(articoli_attuativo)}"
                        )

                    normalizza_gerarchia_articoli(articoli_attuare)
                    normalizza_gerarchia_articoli(articoli_attuativo)
                    ensure_identificativo_fields_for_confronto(articoli_attuare)
                    ensure_identificativo_fields_for_confronto(articoli_attuativo)

                    # Converte ogni identificativo numerico in stringa
                    def stringify_ident(list_articoli):
                        for art in list_articoli:
                            if not isinstance(art, dict):
                                continue
                            if isinstance(art.get("identificativo"), int):
                                art["identificativo"] = str(art["identificativo"])
                            if isinstance(art.get("contenuto_parsato"), list):
                                for comma in art["contenuto_parsato"]:
                                    if not isinstance(comma, dict):
                                        continue
                                    if isinstance(comma.get("identificativo"), int):
                                        comma["identificativo"] = str(
                                            comma["identificativo"]
                                        )
                                    if isinstance(
                                        comma.get("contenuto_parsato_2"), list
                                    ):
                                        for sub in comma["contenuto_parsato_2"]:
                                            if not isinstance(sub, dict):
                                                continue
                                            if isinstance(
                                                sub.get("identificativo"), int
                                            ):
                                                sub["identificativo"] = str(
                                                    sub["identificativo"]
                                                )

                    stringify_ident(articoli_attuare)
                    stringify_ident(articoli_attuativo)
                    # ---------------------------------------------------------------------------

                    # Log sample data structure for debugging
                    if (
                        isinstance(articoli_attuare, dict)
                        and "articoli" in articoli_attuare
                    ):
                        articoli_attuare = articoli_attuare["articoli"]
                    if (
                        isinstance(articoli_attuativo, dict)
                        and "articoli" in articoli_attuativo
                    ):
                        articoli_attuativo = articoli_attuativo["articoli"]

                    if not isinstance(articoli_attuare, list) or not isinstance(
                        articoli_attuativo, list
                    ):
                        raise ValueError(
                            "Parsed 'articoli' is not a list after extraction"
                        )

                    # if articoli_attuare:
                    #     logger.debug(
                    #         f"Sample articoli_attuare[0] keys: {list(articoli_attuare[0].keys()) if isinstance(articoli_attuare[0], dict) else 'Not a dict'}"
                    #     )
                    #     logger.debug(
                    #         f"Sample articoli_attuare[0]: {json.dumps(articoli_attuare[0], ensure_ascii=False)[:500]}..."
                    #     )

                    # if articoli_attuativo:
                    #     logger.debug(
                    #         f"Sample articoli_attuativo[0] keys: {list(articoli_attuativo[0].keys()) if isinstance(articoli_attuativo[0], dict) else 'Not a dict'}"
                    #     )
                    #     logger.debug(
                    #         f"Sample articoli_attuativo[0]: {json.dumps(articoli_attuativo[0], ensure_ascii=False)[:500]}..."
                    #     )

                except Exception as e:
                    logger.error(
                        f"Error loading JSON files for attuativa comparison: {str(e)}"
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Error loading analysis files: {str(e)}",
                    )

                start_ts = get_current_timestamp()
                update_analysis_progress(
                    run_id,
                    {"status": "llm_comparison_in_progress", "percent_done": 30.0},
                )

                confronto_output_dir = Path("./out_schema_attuativo/confronti")
                confronto_output_dir.mkdir(parents=True, exist_ok=True)
                confronto_result_path = confronto_output_dir / f"{comparison_filename}"

                try:
                    logger.info("Starting confronto_attuativo...")
                    confronti = await confronto_attuativo(
                        articoli_attuativo, articoli_attuare
                    )
                    logger.info(
                        f"confronto_attuativo completed, result type: {type(confronti)}"
                    )
                except Exception as e:
                    logger.error(
                        f"Error in confronto_attuativo: {str(e)}", exc_info=True
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Error during attuativa comparison: {str(e)}",
                    )

                confronti_con_correlazione_titoli = (
                    await integrazione_confronto_attuativo_confronto_titoli(
                        confronti, articoli_attuare
                    )
                )

                confronto_commi = (
                    await integrazione_confronto_attuativo_confronto_commi(
                        confronti_con_correlazione_titoli, articoli_attuare
                    )
                )

                confronto_cleaned = select_best_matches(confronto_commi)

                confronti_2 = flat_confronto_attuativo_seconda_meta(confronto_cleaned)

                confronti_result = confronti + confronti_2

                flat_comparison = add_articoli_non_attuati(
                    confronti_result, articoli_attuare
                )

                end_ts = get_current_timestamp()

                flat_out_dir = Path("./out_flat/out_confronto_attuativo")
                flat_out_dir.mkdir(parents=True, exist_ok=True)

                # Create the result structure with file names and comparison data
                result_data = {
                    "file1_name": normalized_filename1,
                    "file2_name": normalized_filename2,
                    "comparison_mode": "attuativa",
                    "comparison_data": flat_comparison,
                    "timestamp": end_ts,
                }

                logger.info(f"WILL BE UPLOAD TO BLOB: {container_name}")

                # implementation_dir = Path("./implementation_results")
                # implementation_dir.mkdir(parents=True, exist_ok=True)
                # implementation_path = implementation_dir / comparison_filename

                with open(
                    flat_out_dir / f"{comparison_filename}", "w", encoding="utf-8"
                ) as f:
                    json.dump(result_data, f, ensure_ascii=False, indent=2)

                upload_to_blob(
                    # "comparisons",
                    "implementations",
                    file_path=flat_out_dir / comparison_filename,
                    blob_name=comparison_filename,
                )

                comparison_spreasheet = comparison_filename.replace(".json", ".xlsx")
                write_records_to_xlsx(
                    flat_comparison,
                    flat_out_dir / comparison_spreasheet,
                )

                upload_to_blob(
                    # "comparisons",
                    "implementations",
                    file_path=flat_out_dir / comparison_spreasheet,
                    blob_name=comparison_spreasheet,
                )
                upload_confronto_vista_excel(
                    flat_out_dir / comparison_spreasheet,
                    normalized_filename1,
                    normalized_filename2,
                    "implementations",
                )

                token_total = len(get_tokens(json.dumps(articoli_attuare))) + len(
                    get_tokens(json.dumps(articoli_attuativo))
                )

                update_sum_data(
                    elapsed_time=calculate_seconds_between(start_ts, end_ts),
                    token_count=token_total,
                    mode="comparison",
                    connection_string=os.getenv("CONNECTION_STRING"),
                    start_timestamp=start_ts,
                    end_timestamp=end_ts,
                )

                update_analysis_progress(
                    run_id,
                    {
                        "status": "completed_comparison",
                        "percent_done": 100.0,
                        "message": "Confronto attuativo completato",
                    },
                )

                comparison_key = f"{normalized_filename1.replace('.pdf','')}_{normalized_filename2.replace('.pdf','')}"
                pdf_json_mapping.comparisonMapping[comparison_key] = comparison_filename
                save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)

                stop_llm_progress()
                return result_data

            elif selected_mode == "versioning":
                logger.info("============= MODALITA' VERSIONING =============")
                update_analysis_progress(
                    run_id,
                    {
                        "status": "llm_comparison_starting",
                        "message": "Starting versioning comparison",
                    },
                )

                try:
                    logger.info(f"json_1: {json_1}")
                    with open(json_1, "r", encoding="utf-8") as f:
                        articoli_1 = json.load(f)
                    logger.info(
                        f"Loaded articoli_1: type={type(articoli_1)}, length={len(articoli_1) if isinstance(articoli_1, list) else 'N/A'}"
                    )

                    logger.info(f"json_2: {json_2}")
                    with open(json_2, "r", encoding="utf-8") as f:
                        articoli_2 = json.load(f)
                    logger.info(
                        f"Loaded articoli_2: type={type(articoli_2)}, length={len(articoli_2) if isinstance(articoli_2, list) else 'N/A'}"
                    )

                    # Verifica che siano liste
                    if not isinstance(articoli_1, list):
                        raise ValueError(
                            f"articoli_1 is not a list, it's {type(articoli_1)}"
                        )
                    if not isinstance(articoli_2, list):
                        raise ValueError(
                            f"articoli_2 is not a list, it's {type(articoli_2)}"
                        )

                except Exception as e:
                    logger.error(
                        f"Error loading JSON files for versioning comparison: {str(e)}"
                    )
                    raise HTTPException(
                        status_code=500,
                        detail=f"Error loading analysis files: {str(e)}",
                    )

                start_ts = get_current_timestamp()
                update_analysis_progress(
                    run_id,
                    {"status": "llm_comparison_in_progress", "percent_done": 30.0},
                )

                try:
                    comparison_raw = await confronto_versioning(articoli_1, articoli_2)
                    logger.info(
                        f"[COMPARISON RAW]: Type={type(comparison_raw)}, Length={len(comparison_raw) if isinstance(comparison_raw, list) else 'N/A'}"
                    )
                except Exception as e:
                    logger.error(f"Error in confronto_versioning: {str(e)}")
                    logger.error(f"Error type: {type(e).__name__}")
                    # Log some sample data to understand the structure
                    if isinstance(articoli_1, list) and len(articoli_1) > 0:
                        logger.error(f"Sample articoli_1[0]: {articoli_1[0]}")
                    if isinstance(articoli_2, list) and len(articoli_2) > 0:
                        logger.error(f"Sample articoli_2[0]: {articoli_2[0]}")

                    # Save error result to blob so the comparison is marked as attempted
                    error_result = {
                        "file1_name": normalized_filename1,
                        "file2_name": normalized_filename2,
                        "comparison_mode": "versioning",
                        "comparison_data": {
                            "error": str(e),
                            "error_type": type(e).__name__,
                            "timestamp": get_current_timestamp(),
                            "message": "Comparison failed due to an error in versioning analysis",
                        },
                        "timestamp": get_current_timestamp(),
                    }

                    flat_out_dir = Path("./out_flat/out_confronto_versioning")
                    flat_out_dir.mkdir(parents=True, exist_ok=True)
                    error_json_path = flat_out_dir / comparison_filename

                    try:
                        with open(error_json_path, "w", encoding="utf-8") as f:
                            json.dump(error_result, f, ensure_ascii=False, indent=4)

                        upload_to_blob(
                            "versionings",
                            file_path=error_json_path,
                            blob_name=comparison_filename,
                        )
                        logger.info(f"[VERSIONING] Uploaded error result to blob")
                    except Exception as upload_err:
                        logger.error(f"Failed to upload error result: {upload_err}")

                    raise HTTPException(
                        status_code=500,
                        detail=f"Error in versioning comparison: {str(e)}",
                    )

                # Sanitize comparison_raw to handle None values
                def sanitize_for_flatten(obj):
                    """Recursively replace None values with empty strings"""
                    if isinstance(obj, dict):
                        # Ensure specific fields that cause problems are never None
                        sanitized = {}
                        for k, v in obj.items():
                            if k in [
                                "motivo",
                                "relazione_contenuto",
                                "Contenuto",
                                "Contenuto Comma",
                                "Tipo",
                                "Articolo",
                                "Identificativo Comma",
                                "Pagina",
                            ]:
                                # These fields must be strings
                                if v is None:
                                    sanitized[k] = ""
                                elif not isinstance(v, str):
                                    sanitized[k] = str(v)
                                else:
                                    sanitized[k] = v
                            else:
                                sanitized[k] = sanitize_for_flatten(v)
                        return sanitized
                    elif isinstance(obj, list):
                        return [sanitize_for_flatten(item) for item in obj]
                    elif obj is None:
                        return ""
                    else:
                        return obj

                try:
                    comparison_raw = sanitize_for_flatten(comparison_raw)
                    logger.info(
                        f"[COMPARISON RAW SANITIZED]: Data sanitized for None values"
                    )

                    # Additional validation to ensure critical fields exist
                    if isinstance(comparison_raw, list):
                        for idx, item in enumerate(comparison_raw):
                            if isinstance(item, dict):
                                # Ensure all required fields exist with default values
                                if "motivo" not in item:
                                    item["motivo"] = "Sconosciuto"
                                if "relazione_contenuto" not in item:
                                    item["relazione_contenuto"] = ""
                                if (
                                    "Contenuto" not in item
                                    and "Contenuto Comma" not in item
                                ):
                                    item["Contenuto"] = ""
                                logger.debug(
                                    f"Item {idx} after validation: motivo={item.get('motivo')}, has relazione_contenuto={('relazione_contenuto' in item)}"
                                )

                except Exception as e:
                    logger.error(f"Error sanitizing comparison data: {str(e)}")
                    raise

                end_ts = get_current_timestamp()
                flat_out_dir = Path("./out_flat/out_confronto_versioning")
                flat_out_dir.mkdir(parents=True, exist_ok=True)

                # Save raw comparison first (before flattening) to ensure we have data
                raw_result_data = {
                    "file1_name": normalized_filename1,
                    "file2_name": normalized_filename2,
                    "comparison_mode": "versioning",
                    "comparison_data": comparison_raw,  # Save raw data first
                    "timestamp": end_ts,
                }

                # Save to temporary location first
                temp_json_path = flat_out_dir / f"{comparison_filename}.tmp"
                with open(temp_json_path, "w", encoding="utf-8") as f:
                    json.dump(raw_result_data, f, ensure_ascii=False, indent=4)
                logger.info(
                    f"[VERSIONING] Saved raw comparison to temp file: {temp_json_path}"
                )

                try:
                    flat_comparison = flatten_confronto_versioning(comparison_raw)
                    logger.info(f"[COMPARISON FLAT]: {flat_comparison}")
                except Exception as e:
                    logger.error(f"Error in flatten_confronto_versioning: {str(e)}")
                    logger.error(f"Type of comparison_raw: {type(comparison_raw)}")
                    if isinstance(comparison_raw, list) and len(comparison_raw) > 0:
                        logger.error(f"First element type: {type(comparison_raw[0])}")
                        logger.error(
                            f"First element keys: {comparison_raw[0].keys() if isinstance(comparison_raw[0], dict) else 'Not a dict'}"
                        )
                    # Upload the raw comparison even if flattening fails
                    try:
                        upload_to_blob(
                            "versionings",
                            file_path=temp_json_path,
                            blob_name=comparison_filename,
                        )
                        logger.info(
                            f"[VERSIONING] Uploaded raw comparison despite flattening error"
                        )
                    except Exception as upload_err:
                        logger.error(f"Failed to upload raw comparison: {upload_err}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Error flattening versioning comparison: {str(e)}",
                    )

                # Create the result structure with file names and comparison data
                result_data = {
                    "file1_name": normalized_filename1,
                    "file2_name": normalized_filename2,
                    "comparison_mode": "versioning",
                    "comparison_data": flat_comparison,
                    "timestamp": end_ts,
                }

                with open(
                    flat_out_dir / comparison_filename,
                    "w",
                    encoding="utf-8",
                ) as f:
                    json.dump(result_data, f, ensure_ascii=False, indent=4)

                upload_to_blob(
                    "versionings",
                    file_path=flat_out_dir / comparison_filename,
                    blob_name=comparison_filename,
                )
                comparison_spreasheet = comparison_filename.replace(".json", ".xlsx")
                logger.info(f"[COMPARISON SPREASHEET]: {comparison_spreasheet}")
                write_records_to_xlsx(
                    flat_comparison,
                    flat_out_dir / comparison_spreasheet,
                )

                upload_to_blob(
                    "versionings",
                    file_path=flat_out_dir / comparison_spreasheet,
                    blob_name=comparison_spreasheet,
                )
                upload_confronto_vista_excel(
                    flat_out_dir / comparison_spreasheet,
                    normalized_filename1,
                    normalized_filename2,
                    "versionings",
                )

                token_total = len(get_tokens(json.dumps(articoli_1))) + len(
                    get_tokens(json.dumps(articoli_2))
                )
                logger.info(f"[TOKEN TOTAL]: {token_total}")

                update_sum_data(
                    elapsed_time=calculate_seconds_between(start_ts, end_ts),
                    token_count=token_total,
                    mode="comparison",
                    connection_string=os.getenv("CONNECTION_STRING"),
                    start_timestamp=start_ts,
                    end_timestamp=end_ts,
                )

                update_analysis_progress(
                    run_id,
                    {
                        "status": "completed_comparison",
                        "percent_done": 100.0,
                        "message": "Confronto attuativo completato",
                    },
                )

                comparison_key = f"{normalized_filename1.replace('.pdf','')}_{normalized_filename2.replace('.pdf','')}"
                pdf_json_mapping.comparisonMapping[comparison_key] = comparison_filename
                save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)

                stop_llm_progress()
                cleanup_local_json_files()
                return result_data

        logger.info("NON DOVREMMO ESSERE QUI")

        # Direct call to compare_requirements_json function
        # try:
        #     # Load the prompt message
        #     with open("prompt_message.txt", "r", encoding="utf-8") as f:
        #         prompt_message = f.read()

        #     logger.info(f"Avvio confronto diretto tra {json_path1} e {json_path2}")
        #     result = compare_requirements_json.compare_requirements(
        #         str(json_path1),
        #         str(json_path2),
        #         prompt_message,
        #         str(comparison_json_path),
        #     )
        #     logger.info("Confronto completato con successo.")
        # except Exception as e:
        #     logger.error(f"Errore durante il confronto dei JSON: {str(e)}")
        #     raise HTTPException(
        #         status_code=500,
        #         detail=f"Errore durante la comparazione dei requisiti: {str(e)}",
        #     )

        # # Gestione post-confronto: modalità emendativa o standard
        # amending_mode_active = os.getenv("AMENDING_MODE", "false").lower() == "true"

        # if amending_mode_active:
        #     # Gestione specifica per modalità emendativa
        #     amending_dir = Path("./amending_results")
        #     amending_comparison_path = amending_dir / comparison_json_path.name

        #     if (
        #         amending_comparison_path.exists()
        #         and os.stat(amending_comparison_path).st_size > 0
        #     ):
        #         logger.info(
        #             f"File di confronto trovato in amending_results: {amending_comparison_path}"
        #         )
        #         try:
        #             # Leggi e restituisci il risultato emendativo
        #             with open(amending_comparison_path, "r", encoding="utf-8") as f:
        #                 comparison_results = json.load(f)
        #             logger.info(
        #                 f"Caricati risultati dalla modalità emendativa con successo."
        #             )

        #             # Aggiorna mapping se necessario (potrebbe essere gestito altrove)
        #             comparison_key = f"{normalized_filename1.replace('.pdf','')}_{normalized_filename2.replace('.pdf','')}"
        #             pdf_json_mapping.comparisonMapping[comparison_key] = (
        #                 comparison_filename
        #             )
        #             save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)
        #             logger.info(
        #                 f"[COMPARE-POST] Mapping confronto emendativo aggiornato (path: {amending_comparison_path})"
        #             )

        #             return comparison_results  # Ritorna immediatamente
        #         except Exception as e:
        #             logger.error(
        #                 f"[COMPARE-POST] Errore critico nella lettura del file emendativo ({amending_comparison_path}): {e}"
        #             )
        #             raise HTTPException(
        #                 status_code=500,
        #                 detail=f"[COMPARE-POST] Errore durante la lettura del risultato emendativo generato: {e}",
        #             )
        #     else:
        #         # Se il file non esiste in amending_results, è un errore perché doveva essere creato
        #         logger.error(
        #             f"[COMPARE-POST] File emendativo atteso ma non trovato o vuoto in {amending_comparison_path}"
        #         )
        #         raise HTTPException(
        #             status_code=500,
        #             detail="[COMPARE-POST] File di risultato emendativo non trovato o vuoto dopo la generazione.",
        #         )
        # else:
        #     # Gestione specifica per modalità standard
        #     logger.info(f"Modalità standard: verifico file in {comparison_json_path}")
        #     if not (
        #         comparison_json_path.exists()
        #         and os.stat(comparison_json_path).st_size > 0
        #     ):
        #         # Se il file non esiste nel percorso standard, è un errore perché doveva essere creato
        #         logger.error(
        #             f"[COMPARE-POST] Il file di confronto standard ({comparison_json_path}) è vuoto o non è stato creato."
        #         )
        #         raise HTTPException(
        #             status_code=500,
        #             detail="[COMPARE-POST] Il file di confronto standard è vuoto o non è stato creato dopo la generazione.",
        #         )

        #     # Upload standard comparison to blob
        #     upload_to_blob(
        #         "comparisons",
        #         file_path=comparison_json_path,
        #         blob_name=comparison_filename,
        #     )

        #     # Read standard result from ./output/
        #     try:
        #         with open(comparison_json_path, "r", encoding="utf-8") as f:
        #             comparison_results = json.load(f)

        #         # Aggiorna mapping per il confronto standard
        #         comparison_key = f"{normalized_filename1.replace('.pdf','')}_{normalized_filename2.replace('.pdf','')}"
        #         pdf_json_mapping.comparisonMapping[comparison_key] = comparison_filename
        #         save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)
        #         logger.info(
        #             f"[COMPARE-POST] Mapping confronto standard aggiornato (path: {comparison_json_path})"
        #         )

        #         return comparison_results
        #     except Exception as e:
        #         logger.error(
        #             f"[COMPARE-POST] Errore nella lettura del file di confronto standard ({comparison_json_path}): {e}"
        #         )
        #         raise HTTPException(
        #             status_code=500,
        #             detail=f"[COMPARE-POST] Errore durante la lettura del risultato standard generato: {e}",
        #         )

    except Exception as e:
        logger.error(f"Errore durante il confronto: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Errore durante la comparazione dei requisiti: {str(e)}",
        )


# Endpoint per ottenere i risultati di estrazione
@app.get("/api/v0/documents/{name}/", response_model=Dict[str, Any])
async def get_extracted_requirements(name: str):
    normalized_name = normalize_filename(name).replace(".pdf", "")
    json_filename = pdf_json_mapping.mapping.get(normalized_name)
    logger.info(
        f"Richiesta documento {name} (normalized: {normalized_name}) con mapping: {json_filename}"
    )
    if not json_filename:
        logger.error("Mapping non trovato per il file richiesto.")
        raise HTTPException(status_code=404, detail="File not found")

    output_file_path = Path(f"./output/{json_filename}")

    # Check if file exists locally or in blob storage
    exists, location = await check_and_retrieve_resource(
        "requirements", json_filename, output_file_path
    )

    if not exists:
        logger.warning(
            f"Il file JSON {json_filename} non esiste né localmente né nel blob storage."
        )
        pdf_json_mapping.mapping.pop(normalized_name, None)
        save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)
        raise HTTPException(status_code=404, detail="File not found (mapping updated)")

    try:
        with output_file_path.open("r", encoding="utf-8") as output_file:
            data = json.load(output_file)
            if isinstance(data, list):
                data = {"requirements": data}
            if not isinstance(data, dict):
                raise ValueError("Output JSON is not a dictionary")
            return data
        # with output_file_path.open("r", encoding="utf-8") as output_file:
        #     requirements = json.load(output_file)
        #     if "source_file" in requirements:
        #         requirements["source_file"] = Path(requirements["source_file"]).name
        #     logger.info(f"Documento {name} caricato con successo.")
        #     if not isinstance(requirements, dict):
        #         raise ValueError("Output JSON is not a dictionary")
        #     return requirements

    except json.JSONDecodeError as e:
        logger.error(f"Errore nel decodificare il JSON di output: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Errore durante la decodifica del file JSON di output",
        )
    except Exception as e:
        logger.error(f"Errore generico nella lettura del file JSON: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Errore generico nella lettura del file JSON di output",
        )


@app.post(
    "/api/v0/documents/{name}/compare/{compareToName}", response_model=Dict[str, Any]
)
async def compare_requirements_v0(name: str, compareToName: str):
    # Recupera i nomi JSON associati usando la mappa
    normalized_name1 = normalize_filename(name).replace(".pdf", "")
    normalized_name2 = normalize_filename(compareToName).replace(".pdf", "")
    logger.info(f"Richiesta confronto per: {name} e {compareToName}")

    json_filename1 = pdf_json_mapping.mapping.get(normalized_name1)
    json_filename2 = pdf_json_mapping.mapping.get(normalized_name2)
    logger.info(f"[COMPARE-V0] Mapping trovato: {json_filename1} e {json_filename2}")

    if not json_filename1 or not json_filename2:
        logger.error("Mapping mancante per uno dei documenti richiesti.")
        raise HTTPException(
            status_code=404, detail="One or both document JSON files not found"
        )

    # Usa i file JSON già estratti per il confronto
    output_dir = Path("./output")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file1_path = Path(f"./output/{json_filename1}")
    output_file2_path = Path(f"./output/{json_filename2}")

    # Check if both JSON files exist locally or in blob storage
    exists1, location1 = await check_and_retrieve_resource(
        "requirements", json_filename1, output_file1_path
    )
    exists2, location2 = await check_and_retrieve_resource(
        "requirements", json_filename2, output_file2_path
    )

    # If one of the files doesn't exist, remove it from the mapping
    if not exists1:
        logger.warning(
            f"Il file {output_file1_path} non esiste; rimuovo voce dal mapping."
        )
        pdf_json_mapping.mapping.pop(normalized_name1, None)
        raise HTTPException(
            status_code=404,
            detail=f"File {name} not found on filesystem or blob storage (mapping updated)",
        )
    if not exists2:
        logger.warning(
            f"Il file {output_file2_path} non esiste; rimuovo voce dal mapping."
        )
        pdf_json_mapping.mapping.pop(normalized_name2, None)
        raise HTTPException(
            status_code=404,
            detail=f"File {compareToName} not found on filesystem or blob storage (mapping updated)",
        )

    logger.info(f"Requirements files found - File 1: {location1}, File 2: {location2}")

    # Costruisci il nome del file di confronto utilizzando gli hash (ossia lo stem dei file JSON)
    hash1 = Path(json_filename1).stem
    hash2 = Path(json_filename2).stem
    sorted_hashes = sorted([hash1, hash2])
    comparison_filename = f"{sorted_hashes[0]}_vs_{sorted_hashes[1]}_comparison.json"
    output_file_path = Path(f"./output/{comparison_filename}")

    # Check if the comparison file already exists
    exists, location = await check_and_retrieve_resource(
        "comparisons", comparison_filename, output_file_path
    )

    if exists:
        logger.info(f"Comparison file found in {location}: {comparison_filename}")
        try:
            with open(output_file_path, "r", encoding="utf-8") as f:
                comparison_results = json.load(f)

            # Update mapping for the comparison
            comparison_key = f"{normalized_name1}_{normalized_name2}"
            pdf_json_mapping.comparisonMapping[comparison_key] = comparison_filename
            save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)
            logger.info(
                f"[COMPARE-V0] Mapping confronto aggiornato: {pdf_json_mapping.comparisonMapping}"
            )

            return comparison_results
        except Exception as e:
            logger.error(f"Error reading existing comparison file: {str(e)}")
            # Continue with comparison if we can't read the existing file

    try:
        # Load the prompt message
        with open("prompt_message.txt", "r", encoding="utf-8") as f:
            prompt_message = f.read()

        logger.info(
            f"Avvio confronto diretto tra {output_file1_path} e {output_file2_path}"
        )
        # Direct call to compare_requirements function
        compare_requirements_json.compare_requirements(
            str(output_file1_path),
            str(output_file2_path),
            prompt_message,
            str(output_file_path),
        )
        logger.info("Confronto completato.")

        # Upload to blob storage
        upload_to_blob(
            "comparisons", file_path=output_file_path, blob_name=comparison_filename
        )
        logger.info(f"Caricato confronto su blob storage: {comparison_filename}")

        if not (output_file_path.exists() and os.stat(output_file_path).st_size > 0):
            logger.error("Il file di confronto è vuoto o non creato.")
            raise HTTPException(
                status_code=500,
                detail="Il file di confronto è vuoto o non è stato creato",
            )

        try:
            with open(output_file_path, "r", encoding="utf-8") as f:
                comparison_results = json.load(f)
                if not isinstance(comparison_results, dict):
                    raise ValueError("Output JSON is not a dictionary")
        except json.JSONDecodeError as e:
            logger.error(f"Errore decodifica JSON confronto: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Errore nella decodifica del file JSON di output",
            )
        except Exception as e:
            logger.error(f"Errore generico nella lettura del confronto: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail="Errore generico nella lettura del file di output",
            )

        comparison_key = f"{normalized_name1}_{normalized_name2}"
        pdf_json_mapping.comparisonMapping[comparison_key] = comparison_filename
        save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)
        print_mapping(pdf_json_mapping.mapping)
        logger.info(
            f"[COMPARE-V0] Mapping confronto finale aggiornato: {pdf_json_mapping.comparisonMapping}"
        )

        return comparison_results
    except Exception as e:
        logger.error(f"Errore durante il confronto: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Errore durante la comparazione dei requisiti: {str(e)}",
        )


@app.get(
    "/api/v0/documents/{name}/compare/{compareToName}", response_model=Dict[str, Any]
)
async def get_comparison_result(name: str, compareToName: str):
    normalized_name1 = normalize_filename(name).replace(".pdf", "")
    normalized_name2 = normalize_filename(compareToName).replace(".pdf", "")
    logger.info(f"Richiesta GET confronto per: {name} e {compareToName}")

    # Recupera dalla mappa i nomi (basati sull'hash) dei file JSON associati
    json_filename1 = pdf_json_mapping.mapping.get(normalized_name1)
    json_filename2 = pdf_json_mapping.mapping.get(normalized_name2)
    logger.info(f"[GET COMPARE] Mapping trovato: {json_filename1}, {json_filename2}")
    if not json_filename1 or not json_filename2:
        logger.error("Mapping per uno o entrambi i file non trovato.")
        raise HTTPException(status_code=404, detail="One or both documents not found")

    # Estrai i soli nomi (senza estensione) per ottenere gli hash
    hash1 = Path(json_filename1).stem
    hash2 = Path(json_filename2).stem

    # Ordina gli hash per formare il nome del file di confronto
    sorted_hashes = sorted([hash1, hash2])
    comparison_filename = f"{sorted_hashes[0]}_vs_{sorted_hashes[1]}_comparison.json"
    output_file_path = Path(f"./output/{comparison_filename}")

    if not output_file_path.exists():
        logger.error("File di confronto non trovato sul filesystem.")
        raise HTTPException(status_code=404, detail="Comparison file not found")

    try:
        with output_file_path.open("r", encoding="utf-8") as output_file:
            comparison_results = json.load(output_file)
            if not isinstance(comparison_results, dict):
                raise ValueError("Output JSON is not a dictionary")
            logger.info("Confronto caricato correttamente.")
            return comparison_results

    except json.JSONDecodeError as e:
        logger.error(f"Errore decodifica JSON confronto: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Errore durante la decodifica del file JSON di output",
        )
    except Exception as e:
        logger.error(f"Errore generico lettura file confronto: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Errore generico nella lettura del file di output"
        )


class TextInput(BaseModel):
    text: str


@app.post("/test")
async def process_text(input_text: TextInput):
    logger.info(f"Test endpoint chiamato con input: {input_text.text}")
    try:
        # Instead of using subprocess, directly process the text
        processed_text = input_text.text
        logger.info(f"Test endpoint elaborato, output: {processed_text}")
    except Exception as e:
        logger.error(f"Errore durante il test: {e}")
        return {"error": "Errore di processo", "details": str(e)}

    return {"processed_text": processed_text}


class TranslationInput(BaseModel):
    text: str
    target_language: str


async def translate_chunk(
    text_chunk: str, constructed_url: str, params: dict, headers: dict
) -> str:
    body = [{"text": text_chunk}]
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        try:
            request = await loop.run_in_executor(
                pool,
                lambda: requests.post(
                    constructed_url, params=params, headers=headers, json=body
                ),
            )
            request.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"Errore richiesta traduzione: {str(e)}")
            raise HTTPException(
                status_code=500, detail=f"Translation API request failed: {str(e)}"
            )
        if request.status_code != 200:
            logger.error(f"Errore traduzione API, codice {request.status_code}")
            raise HTTPException(
                status_code=request.status_code,
                detail=f"Translation API error: {request.text}",
            )
        response = request.json()
        return response[0]["translations"][0]["text"]


@app.post("/translate", response_model=Dict[str, Any])
async def translate_text(file: UploadFile = File(...)):
    logger.info(f"Richiesta traduzione per file: {file.filename}")
    try:
        if not file.filename.endswith(".pdf"):
            logger.error("File non PDF per traduzione")
            raise HTTPException(status_code=400, detail="Only PDF files are accepted")

        extracted_text = RequirementExtractor.extract_text_from_pdf(file.file)

        if not extracted_text:
            logger.error("Testo estratto vuoto per traduzione")
            raise HTTPException(
                status_code=400, detail="No text could be extracted from the PDF file"
            )

        # TODO: migliorare chunking per frasi
        text_chunks = [
            extracted_text[i : i + 40000] for i in range(0, len(extracted_text), 40000)
        ]
        logger.info(f"Testo diviso in {len(text_chunks)} chunks per traduzione")

        path = "/translate"
        constructed_url = "https://api.cognitive.microsofttranslator.com" + path

        params = {"api-version": "3.0", "from": "en", "to": "it"}

        headers = {
            "Ocp-Apim-Subscription-Key": os.getenv("TRANSLATOR_KEY"),
            "Ocp-Apim-Subscription-Region": os.getenv("TRANSLATOR_LOCATION"),
            "Content-type": "application/json",
            "X-ClientTraceId": str(uuid.uuid4()),
        }

        translated_chunks = await asyncio.gather(
            *[
                translate_chunk(chunk, constructed_url, params, headers)
                for chunk in text_chunks
            ]
        )
        translated_text = " ".join(translated_chunks)
        logger.info("Traduzione completata con successo.")

        response_content = {
            "metadata": {
                "source_language": "en",
                "target_language": "it",
                "characters_translated": len(extracted_text),
            },
            "translation": translated_text,
        }
        return JSONResponse(content=response_content)

    except Exception as e:
        logger.exception("Errore durante la traduzione:")
        raise HTTPException(status_code=500, detail=f"Translation error: {e}")


class SearchRequest(BaseModel):
    search_text: str
    top: int = 10
    from_analysis: str
    # facets: list[str] = ["metadata_author", "organizations"]
    # search_fields: list[str] = ["organizations", "people", "content", "keyphrases"] #"text_vector"


@app.post("/search", response_model=dict)
async def search_documents(request: SearchRequest):
    logger.info(
        f"Richiesta ricerca: {request.search_text} con from_analysis = {request.from_analysis}"
    )
    try:
        output_dir = "./output_internal"
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
            logger.info(f"Creato output_internal in {output_dir}")

        output_filename = "output_search.json"
        output_file_path = Path(os.path.join(output_dir, output_filename))
        logger.info(f"Output search file: {output_file_path}")

        # Direct call to the EnhancedSearchService class
        search_service = EnhancedSearchService(
            endpoint="https://cdpaisearch.search.windows.net",
            index_name="azureblob-index-cdpai2",
            api_key=os.getenv("SEARCH_KEY"),
        )

        # Determine if from_analysis should be used
        from_analysis = (
            hasattr(request, "from_analysis") and request.from_analysis == "1"
        )
        if from_analysis:
            logger.info("Entro in modalità from_analysis 1")

        # Call the search function directly
        results = search_service.search_documents(
            search_text=request.search_text,
            top=request.top,
            from_analysis=from_analysis,
        )

        # Save the results to a file for persistence
        with open(output_file_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=4)

        logger.info("Ricerca completata correttamente.")
        return results

    except Exception as e:
        logger.exception("Errore durante la ricerca:")
        raise HTTPException(
            status_code=500, detail=f"An unexpected error occurred: {str(e)}"
        )


@app.get("/download-excel")
def download_excel():
    logger.info("Richiesta download excel")
    directory = Path("output_internal")
    try:
        # Find the most recent JSON file in the directory
        latest_file = max(directory.glob("*.json"), key=os.path.getmtime)
        logger.info(f"File excel trovato: {latest_file}")
    except Exception as e:
        logger.error(f"Errore nel trovare il file excel: {e}")
        raise HTTPException(status_code=500, detail="Errore nel trovare il file excel")

    with open(latest_file, "r") as file:
        data = json.load(file)

    document_data = []
    for doc in data.get("documents", []):
        doc_info = {
            "Filename": doc.get("filename", "N/A"),
            "Score": doc.get("score", "N/A"),
            "Author": doc.get("content", {}).get("metadata_author", "N/A"),
            "Creation Date": doc.get("content", {}).get(
                "metadata_creation_date", "N/A"
            ),
            "Storage Size": doc.get("content", {}).get("metadata_storage_size", "N/A"),
            "Relevant Excerpts": " | ".join(doc.get("relevant_excerpts", [])),
        }
        document_data.append(doc_info)

    df = pd.DataFrame(document_data)

    # Save DataFrame to Excel
    excel_path = "analisi_presidi.xlsx"
    df.to_excel(excel_path, index=False)
    logger.info("Excel creato con successo.")
    # Return the Excel file as a download
    return FileResponse(
        excel_path,
        filename="analisi_presidi.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get(path="/download-requirements-result")
def download_requirements_result():
    logger.info("Richiesta download risultati estrazione requisiti")
    directory = Path("output")

    try:
        latest_file = max(directory.glob("*.json"), key=os.path.getmtime)
        logger.info(f"File requisiti trovato: {latest_file}")
    except Exception as e:
        logger.error(f"Errore nel trovare il file requisiti: {e}")
        raise HTTPException(
            status_code=500, detail="Errore nel trovare il file requisiti"
        )

    with open(latest_file, "r") as file:
        data = json.load(file)

    # Extract meta-information
    source_file = data.get("source_file", "N/A")
    analysis_date_raw = data.get("analysis_date", "N/A")

    # Convert analysis date to the desired format (e.g., 21 Settembre 2024, 09:44)
    if analysis_date_raw != "N/A":
        analysis_date = (
            datetime.fromisoformat(analysis_date_raw)
            .strftime("%d %B %Y, %H:%M")
            .capitalize()
        )
    else:
        analysis_date = "N/A"

    # Extract requirements information
    requirements_data = []
    for req in data.get("requirements", []):
        req_info = [
            req.get("requirement", "N/A"),
            req.get("core_text", "N/A"),
            req.get("page", "N/A"),
            req.get("pattern_type", "N/A"),
        ]
        requirements_data.append(req_info)

    # Create DataFrame
    df = pd.DataFrame(
        requirements_data,
        columns=["Requisito", "Etichetta", "Pagina del PDF", "Riferimenti menzionati"],
    )

    # Create an Excel writer and add meta-information
    excel_path = "analisi_preliminare_requisiti.xlsx"
    with pd.ExcelWriter(excel_path, engine="xlsxwriter") as writer:
        # Write meta-information
        workbook = writer.book
        worksheet = workbook.add_worksheet("Analisi Requisiti")
        writer.sheets["Analisi Requisiti"] = worksheet

        # Write header information
        worksheet.write("A1", "Nome file:")
        worksheet.write("B1", source_file)
        worksheet.write("A2", "Data di estrazione")
        worksheet.write("B2", analysis_date)

        # Write the DataFrame starting from cell A4
        for idx, col in enumerate(df.columns):
            worksheet.write(4, idx, col)
        for row_idx, row in enumerate(df.values):
            for col_idx, value in enumerate(row):
                worksheet.write(row_idx + 5, col_idx, value)

    logger.info("Excel di requisiti creato con successo.")
    # Return the Excel file as a download
    return FileResponse(
        excel_path,
        filename="analisi_preliminare_requisiti.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/download-comparison-result")
def download_html():
    logger.info("Richiesta download file HTML confronto")
    # Directory dove si trovano i file JSON
    directory = Path("output")
    logger.info(f"Current working directory: {os.getcwd()}")
    logger.info(f"Directory output: {directory}")

    # Controlla se la directory esiste
    if not directory.exists():
        logger.error("La directory 'output' non esiste.")
        return Response(content="La directory 'output' non esiste.", status_code=400)

    # Trova il file JSON più recente che termina con '_comparison.json'
    try:
        latest_file = max(directory.glob("*_comparison.json"), key=os.path.getmtime)
        logger.info(f"File di confronto trovato: {latest_file}")
    except ValueError:
        logger.error("Nessun file '_comparison.json' trovato nella directory.")
        return Response(
            content="Nessun file '_comparison.json' trovato nella directory.",
            status_code=400,
        )

    # Carica i dati dal file JSON
    try:
        with open(latest_file, "r", encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError:
        logger.error(
            "Errore nella lettura del file JSON di confronto (formato non valido)."
        )
        return Response(
            content="Errore durante la lettura del file JSON. Formato non valido.",
            status_code=400,
        )

    # Estrarre il testo Markdown dalla chiave 'output'
    markdown_text = data.get("output", "")
    if not markdown_text:
        logger.error("Il file JSON non contiene la chiave 'output' o è vuota.")
        return Response(
            content="Il file JSON non contiene la chiave 'output' o è vuota.",
            status_code=400,
        )

    # Converti Markdown in HTML con supporto per tabelle
    try:
        html_content = markdown2.markdown(markdown_text, extras=["tables"])
    except Exception as e:
        logger.error(f"Errore nella conversione del Markdown: {str(e)}")
        return Response(
            content=f"Errore nella conversione del Markdown: {str(e)}", status_code=500
        )

    # Aggiungi stile CSS inline per le tabelle e il font
    styled_html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Risultati Confronto</title>
        <style>
            body {{
                font-family: Arial, Helvetica, sans-serif;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
            }}
            table, th, td {{
                border: 1px solid black;
            }}
            th, td {{
                padding: 8px;
                text-align: left;
            }}
        </style>
    </head>
    <body>
        {html_content}
    </body>
    </html>
    """

    # Crea il file HTML
    try:
        html_path = "risultati_confronto.html"
        with open(html_path, "w", encoding="utf-8") as html_file:
            html_file.write(styled_html_content)
        logger.info("File HTML di confronto creato con successo.")
    except Exception as e:
        logger.error(f"Errore durante la creazione del file HTML: {str(e)}")
        return Response(
            content=f"Errore durante la creazione del file HTML: {str(e)}",
            status_code=500,
        )

    # Restituisci il file HTML come download
    return FileResponse(
        html_path, filename="risultati_confronto.html", media_type="text/html"
    )


def get_blob_content(blob_name: str) -> bytes:
    connection_string = os.getenv("CONNECTION_STRING")
    if not connection_string:
        raise Exception("La connection string non è definita")
    container_name = "cdp-ext"
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob=blob_name)
    return blob_client.download_blob().readall()


@app.get("/api/v0/results/{name}", response_model=dict)
async def get_result_by_name(
    name: str,
    comparisonMode: Optional[str] = Query(
        None, description="Comparison mode: 'emendativa', 'attuativa', or 'versioning'"
    ),
):
    """
    Retrieve existing results from blob storage based on name and comparison mode.
    Does NOT generate new comparisons - only retrieves existing ones.
    """
    logger.info(
        f"[GET RESULT] REQUEST RECEIVED for {name} with comparisonMode={comparisonMode}"
    )

    # Strip any leading path or extension normalization
    stem = Path(name).name
    output_dir = Path("./output")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / stem

    logger.info(f"[GET RESULT] Looking for file at: {out_path}")

    # Determine the category based on the file type and comparison mode
    if "_vs_" in stem:
        # It's a comparison file
        if comparisonMode == "emendativa":
            category = "amendments"
        elif comparisonMode == "versioning":
            category = "versionings"
        elif comparisonMode == "attuativa":
            category = "implementations"
        else:
            category = "comparisons"
    else:
        # It's a single file requirement
        category = "requirements"

    logger.info(f"[GET RESULT] Searching in category: {category} for file: {stem}")

    # Check if the file exists in blob storage
    exists, location = await check_and_retrieve_resource(category, stem, out_path)

    if exists:
        logger.info(
            f"[GET RESULT] File found in {location}: {stem} (category: {category})"
        )
        try:
            # Read and parse the JSON file
            data = json.loads(out_path.read_text(encoding="utf-8"))
            logger.info(
                f"[GET RESULT] Successfully loaded and parsed JSON ({len(str(data))} bytes)"
            )
            return data
        except json.JSONDecodeError as e:
            logger.error(f"[GET RESULT] Invalid JSON in {out_path}: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"File exists but contains invalid JSON: {name}",
            )
        except Exception as e:
            logger.error(f"[GET RESULT] Error reading JSON from {out_path}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error reading file: {name}")
    else:
        # File not found
        logger.error(f"[GET RESULT] File not found: {stem} in category {category}")

        if "_vs_" in stem:
            # It's a comparison that doesn't exist
            error_msg = f"Comparison file not found: {name}. "
            if comparisonMode:
                error_msg += f"Searched in '{category}' container. "
            error_msg += "Please ensure the comparison has been generated first."
        else:
            # It's a requirements file that doesn't exist
            error_msg = f"Requirements file not found: {name}. Please ensure the file has been processed."

        raise HTTPException(status_code=404, detail=error_msg)


@app.get("/api/v0/hashed-names", response_model=dict)
async def get_hashed_names(
    name: Optional[str] = None, name1: Optional[str] = None, name2: Optional[str] = None
):
    """
    Translate one or two plain PDF names into their hashed .json filenames.
    If the JSON files don't exist yet, extract them from the PDF files.
    """
    logger.info(f"GET HASHED!!! name={name}, name1={name1}, name2={name2}")

    # Ensure the output directory exists
    output_dir = Path("./output")
    output_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path("./tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Helper function to find and load PDF, extract requirements if JSON not found
    async def ensure_json_exists(pdf_name: str) -> str:
        # norm = normalize_filename(pdf_name).rsplit(".", 1)[0]
        clean_name = normalize_filename(pdf_name)
        norm = clean_name[:-4] if clean_name.lower().endswith(".pdf") else clean_name
        ensure_run_id = str(
            uuid.uuid4()
        )  # Keep for logging context, though analysis is removed
        log_prefix_ensure = (
            f"[Run ID: {ensure_run_id}] [ensure_json_exists for {norm}] "
        )

        logger.info(f"{log_prefix_ensure}Attempting to find existing JSON for {norm}")

        hashed_json_filename = pdf_json_mapping.mapping.get(norm)
        output_json_path = None

        if hashed_json_filename:
            logger.info(
                f"{log_prefix_ensure}Mapping found: {norm} -> {hashed_json_filename}"
            )
            output_json_path = output_dir / hashed_json_filename
            exists, location = await check_and_retrieve_resource(
                "requirements", hashed_json_filename, output_json_path
            )
            if exists:
                logger.info(
                    f"{log_prefix_ensure}Found existing JSON via mapping: {hashed_json_filename} in {location}"
                )
                return hashed_json_filename  # Return the mapped and verified filename
            else:
                logger.warning(
                    f"{log_prefix_ensure}Mapped JSON {hashed_json_filename} not found in {location}. Will clear mapping."
                )
                pdf_json_mapping.mapping.pop(norm, None)  # Clear stale mapping
                save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)
                hashed_json_filename = None  # Ensure we don't try to use it later
        else:
            logger.info(
                f"{log_prefix_ensure}No direct mapping found for {norm}. Attempting hash-based lookup of PDF."
            )
            # If no mapping, try to find PDF to get its hash, then check if JSON for that hash exists
            # This does NOT trigger new analysis, only checks if a pre-existing JSON for that PDF's hash can be found.
            pdf_filename_for_hash_lookup = f"{norm}.pdf"
            pdf_locations_for_hash_lookup = [
                tmp_dir / pdf_filename_for_hash_lookup,
                Path(f"./pdfs/{pdf_filename_for_hash_lookup}"),
                Path(f"./{pdf_filename_for_hash_lookup}"),
            ]
            pdf_path_for_hash_lookup = None
            for loc in pdf_locations_for_hash_lookup:
                if loc.exists():
                    pdf_path_for_hash_lookup = loc
                    logger.info(
                        f"{log_prefix_ensure}Found PDF for hash lookup at {pdf_path_for_hash_lookup}"
                    )
                    break

            if pdf_path_for_hash_lookup:
                prospective_file_hash = compute_file_hash(str(pdf_path_for_hash_lookup))
                hashed_json_filename = f"{prospective_file_hash}.json"
                logger.info(
                    f"{log_prefix_ensure}Prospective hash for {norm} is {prospective_file_hash}. Checking for {hashed_json_filename}."
                )
                output_json_path = output_dir / hashed_json_filename
                exists, location = await check_and_retrieve_resource(
                    "requirements", hashed_json_filename, output_json_path
                )
                if exists:
                    logger.info(
                        f"{log_prefix_ensure}Found existing JSON via PDF hash: {hashed_json_filename} in {location}"
                    )
                    # Optionally, update mapping here if desired, as we've found a match
                    pdf_json_mapping.mapping[norm] = hashed_json_filename
                    save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)
                    logger.info(
                        f"{log_prefix_ensure}Updated mapping: {norm} -> {hashed_json_filename}"
                    )
                    return hashed_json_filename
                else:
                    logger.info(
                        f"{log_prefix_ensure}JSON {hashed_json_filename} for PDF hash not found."
                    )
            else:
                logger.info(
                    f"{log_prefix_ensure}PDF {pdf_filename_for_hash_lookup} not found for hash lookup. Cannot determine prospective JSON."
                )

        # If we reach here, the JSON was not found by any means (mapping or direct hash lookup)
        logger.error(
            f"{log_prefix_ensure}JSON for {norm} could not be found or retrieved. Analysis via /extract-requirements/ may be needed."
        )
        raise HTTPException(
            status_code=404,
            detail=f"Processed JSON for '{norm}' not found. Please ensure it has been extracted via /extract-requirements/.",
        )

    # Single file case
    if name:
        hashed = await ensure_json_exists(name)
        return {"hashed": hashed}

    # Comparison case
    if name1 and name2:
        try:
            h1 = await ensure_json_exists(name1)
            h2 = await ensure_json_exists(name2)

            # Sort the two by stem to match compare_requirements_json naming
            s1, s2 = sorted([Path(h1).stem, Path(h2).stem])

            # Return full filenames
            return {"hash1": f"{s1}.json", "hash2": f"{s2}.json"}

        except HTTPException as e:
            if e.status_code == 404:
                # We couldn't find one or both PDFs
                missing = []
                if name1 not in pdf_json_mapping.mapping:
                    missing.append(name1)
                if name2 not in pdf_json_mapping.mapping:
                    missing.append(name2)
                raise HTTPException(
                    status_code=404,
                    detail=f"Could not find PDFs for: {missing}. Please upload them first.",
                )
            raise e

    raise HTTPException(
        status_code=400, detail="Must pass either 'name' or both 'name1' and 'name2'"
    )


# --- Helper functions for sum.json data handling (ported/adapted from function_app.py) ---


def get_current_timestamp() -> str:
    """Return current UTC time as ISO string for consistent time tracking"""
    return datetime.now(timezone.utc).isoformat()


def calculate_seconds_between(
    start_iso: Optional[str], end_iso: Optional[str]
) -> float:
    """Calculate seconds between two ISO timestamp strings"""
    if not start_iso or not end_iso:
        return 0.0
    try:
        start_dt = datetime.fromisoformat(start_iso)
        end_dt = datetime.fromisoformat(end_iso)
        return (end_dt - start_dt).total_seconds()
    except Exception as e:
        logger.error(f"Error calculating time difference: {e}")
        return 0.0


def get_blob_client_for_sum_data(
    connection_string: str, container: str, blob_name: str
) -> BlobClient:
    """Helper to get a BlobClient instance specifically for sum_data."""
    if not connection_string:
        logger.error(
            "Connection string is not available for get_blob_client_for_sum_data"
        )
        raise ValueError("Azure Storage connection string is not configured.")
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)
    return blob_service_client.get_blob_client(container=container, blob=blob_name)


def ensure_sum_blob(connection_string: str) -> BlobClient:
    """
    Returns the BlobClient for sum.json in the configured container.
    If the file does not exist, it creates it with default values.
    """
    blob_client = get_blob_client_for_sum_data(
        connection_string, BLOB_CONFIG_CONTAINER, SUM_BLOB_FILENAME
    )
    try:
        blob_client.get_blob_properties()  # Check if blob exists
    except ResourceNotFoundError:
        logger.info(
            f"{SUM_BLOB_FILENAME} not found in '{BLOB_CONFIG_CONTAINER}': creating with initial values."
        )
        default_body = {
            "sum_time_extraction": 0.0,
            "sum_tokens_extraction": 0,
            "time_data_extraction": {"operations": [], "total_operations": 0},
            "sum_time_comparison": 0.0,
            "sum_tokens_comparison": 0,
            "time_data_comparison": {"operations": [], "total_operations": 0},
            "last_updated": get_current_timestamp(),
        }
        try:
            blob_client.upload_blob(json.dumps(default_body), overwrite=True)
        except Exception as e_upload:
            logger.error(f"Failed to upload initial {SUM_BLOB_FILENAME}: {e_upload}")
            raise  # Re-raise after logging if initial creation fails
    except Exception as e_props:
        logger.error(f"Error checking properties for {SUM_BLOB_FILENAME}: {e_props}")
        raise  # Re-raise if checking properties fails for other reasons
    return blob_client


def get_tokens(
    text: str,
) -> List[int]:  # Matches function_app.py signature, returns list of tokens
    """Encodes text to tokens using gpt-4-32k tokenizer."""
    try:
        tokenizer = tiktoken.encoding_for_model(
            "gpt-4-32k"
        )  # As per function_app, though gpt-4o was mentioned elsewhere
        return tokenizer.encode(text)
    except Exception as e:
        logger.error(f"Error getting tokens: {e}")
        return []  # Return empty list on error


def update_sum_data(
    elapsed_time: float,
    token_count: int,
    mode: str,  # "extraction" or "comparison"
    connection_string: str,  # Added connection_string parameter
    start_timestamp: Optional[str] = None,
    end_timestamp: Optional[str] = None,
) -> Optional[Dict[str, Any]]:  # Return type changed to Optional[Dict]
    """
    Updates sum data with operation metrics and optional timestamp-based duration.
    Only updates sums and history if the operation's elapsed time is valid (>= 10 seconds).
    Args:
        elapsed_time: Reported elapsed time (may be used as fallback or initial value)
        token_count: Number of tokens processed
        mode: Either "extraction" or "comparison"
        connection_string: Azure Storage connection string.
        start_timestamp: ISO timestamp when operation started
        end_timestamp: ISO timestamp when operation completed
    Returns:
        Updated sum data dictionary, or None if update failed.
    """
    if mode not in ("extraction", "comparison"):
        logger.error(
            f"update_sum_data: Invalid mode '{mode}'. Must be 'extraction' or 'comparison'."
        )
        return None

    if not connection_string:
        logger.error(
            "update_sum_data: Connection string not provided. Cannot update sum data."
        )
        return None

    final_elapsed_time = elapsed_time
    if start_timestamp and end_timestamp:
        calculated_elapsed = calculate_seconds_between(start_timestamp, end_timestamp)
        if calculated_elapsed > 0:
            logger.info(
                f"update_sum_data: Using timestamp-based duration: {calculated_elapsed:.2f}s (reported was {elapsed_time:.2f}s)"
            )
            final_elapsed_time = calculated_elapsed
        else:
            logger.warning(
                f"update_sum_data: Timestamp-based duration was invalid ({calculated_elapsed:.2f}s). Using reported: {elapsed_time:.2f}s."
            )

    try:
        blob = ensure_sum_blob(connection_string)
        data = json.loads(blob.download_blob().readall().decode("utf-8"))
    except Exception as e_load:
        logger.error(
            f"update_sum_data: Failed to load or ensure {SUM_BLOB_FILENAME}: {e_load}"
        )
        return None

    MIN_VALID_ELAPSED_TIME = 10.0
    if final_elapsed_time < MIN_VALID_ELAPSED_TIME or token_count <= 0:
        logger.warning(
            f"update_sum_data: Operation for mode '{mode}' with elapsed_time {final_elapsed_time:.2f}s and token_count {token_count} "
            f"does not meet criteria (elapsed_time >= {MIN_VALID_ELAPSED_TIME}s and token_count > 0). "
            "Skipping update to sums and history."
        )
        data["last_updated"] = get_current_timestamp()  # Still update last_updated
        try:
            blob.upload_blob(json.dumps(data), overwrite=True)
        except Exception as e_save_skipped:
            logger.error(
                f"update_sum_data: Failed to save {SUM_BLOB_FILENAME} after skipping sum update: {e_save_skipped}"
            )
        return data  # Return current data

    logger.info(
        f"update_sum_data: Updating sums for mode '{mode}' with valid elapsed_time {final_elapsed_time:.2f}s and token_count {token_count}."
    )

    time_data_key = f"time_data_{mode}"
    if time_data_key not in data or not isinstance(data[time_data_key], dict):
        data[time_data_key] = {"operations": [], "total_operations": 0}
    if not isinstance(data[time_data_key].get("operations"), list):
        data[time_data_key]["operations"] = []

    operation_data = {
        "token_count": token_count,
        "elapsed_time": final_elapsed_time,
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "timestamp": get_current_timestamp(),
    }

    data[time_data_key]["operations"].append(operation_data)
    if len(data[time_data_key]["operations"]) > MAX_HISTORY_ENTRIES:
        data[time_data_key]["operations"] = data[time_data_key]["operations"][
            -MAX_HISTORY_ENTRIES:
        ]

    data[time_data_key]["total_operations"] = (
        data[time_data_key].get("total_operations", 0) + 1
    )

    data[f"sum_time_{mode}"] = data.get(f"sum_time_{mode}", 0.0) + final_elapsed_time
    data[f"sum_tokens_{mode}"] = data.get(f"sum_tokens_{mode}", 0) + token_count
    data["last_updated"] = get_current_timestamp()

    try:
        blob.upload_blob(json.dumps(data), overwrite=True)
        logger.info(
            f"update_sum_data: Successfully updated {SUM_BLOB_FILENAME} for mode '{mode}'."
        )
        return data
    except Exception as e_save:
        logger.error(
            f"update_sum_data: Failed to save updated {SUM_BLOB_FILENAME}: {e_save}"
        )
        return None  # Indicate failure to save

def save_results_to_storage(result_dict: dict,
                            pdf_path: str,
                            json_path: str,
                            text_content: str):
    """
    Salva il JSON su disco, carica il file (e una copia "plain") su Blob
    e aggiorna pdf_json_mapping come faceva il vecchio save_results.
    """
    from pathlib import Path
    from extration_utils import upload_to_blob

    # json principale è già stato scritto in json_path dal chiamante
    # 1️⃣ copia "plain":  <hash>.json → <nomefile>.json
    pdf_name = Path(pdf_path).name
    base_name = pdf_name.replace(".pdf", "")
    plain_json_path = Path(json_path).parent / f"{base_name}.json"
    shutil.copy(json_path, plain_json_path)

    # 2️⃣ upload
    upload_to_blob("requirements", file_path=json_path,      blob_name=Path(json_path).name)
    upload_to_blob("requirements", file_path=plain_json_path, blob_name=plain_json_path.name)

    # 3️⃣ mapping
    pdf_json_mapping.mapping[base_name] = Path(json_path).name
    save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)

    # (facoltativo) eventuale logica di versione / history ecc.


@app.get("/api/v0/documents/{name}/excel", response_model=None)
async def get_extracted_requirements_excel(name: str):
    """
    Recupera l'Excel dell'analisi requisiti per un documento.
    Blob-first: legge da Blob Storage senza usare filesystem del pod; fallback a check_and_retrieve_resource.
    """
    name = (name or "").strip()
    logger.info(f"Request for Excel file: {name}")

    try:
        file_hash = get_hash_for_name(name)
    except HTTPException as e:
        if e.status_code != 404:
            raise
        normalized = normalize_filename(name).replace(".pdf", "").strip()
        if len(normalized) == 64 and all(
            c in "0123456789abcdef" for c in normalized.lower()
        ):
            file_hash = normalized
            logger.info(f"Name treated as hash: {file_hash}")
        else:
            logger.error(f"Hash non trovato per documento: {name}")
            raise

    excel_filename = f"{file_hash}.xlsx"
    download_name = normalize_filename(name).replace(".pdf", "") + ".xlsx"
    if download_name == ".xlsx":
        download_name = excel_filename

    content = get_blob_bytes("requirements", excel_filename)
    if content is not None:
        logger.info(f"Excel analisi servito da blob: {excel_filename}")
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
        )

    excel_dir = Path("./out_flat/out_analisi")
    excel_dir.mkdir(parents=True, exist_ok=True)
    excel_path = excel_dir / excel_filename
    exists, _ = await check_and_retrieve_resource(
        "requirements", excel_filename, excel_path
    )
    if not exists:
        logger.error(f"Excel non trovato in blob per hash: {file_hash} (documento: {name})")
        raise HTTPException(
            status_code=404,
            detail=f"Excel analisi non trovato per il documento (hash={file_hash}). Verificare che l'analisi sia stata eseguita.",
        )
    try:
        return FileResponse(
            path=str(excel_path),
            filename=download_name,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except Exception as e:
        logger.error(f"Error returning Excel file: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error retrieving Excel file: {str(e)}"
        )


@app.get("/download-requirements-excel")
async def download_requirements_excel_by_query(
    filename: str = Query(..., description="The PDF filename or hash to get Excel for")
):
    """
    Download Excel file for requirements analysis.
    This endpoint uses query parameters which makes it easier to use with direct links.

    Example: /download-requirements-excel?filename=document.pdf
    """
    logger.info(f"Request for Excel download via query param: {filename}")

    # Reuse the logic from the path-based endpoint
    return await get_extracted_requirements_excel(filename)


# --- Progress Clear Endpoint ---
@app.delete("/analysis-progress/{run_id}")
async def clear_analysis_progress(run_id: str):
    """Clear analysis progress for a specific run_id."""
    if run_id in analysis_progress_store:
        logger.info(f"Clearing progress for run_id: {run_id}")
        analysis_progress_store.pop(run_id, None)

        # Also clear LATEST_ANALYSIS_RUN_ID if it matches
        global LATEST_ANALYSIS_RUN_ID
        if LATEST_ANALYSIS_RUN_ID == run_id:
            LATEST_ANALYSIS_RUN_ID = None

        return {"message": f"Progress cleared for run_id: {run_id}"}
    else:
        raise HTTPException(
            status_code=404, detail=f"No progress found for run_id: {run_id}"
        )


@app.post(
    "/extract-requirements/", response_model=Dict[str, Any]
)  # Assuming this is still synchronous as per user context
async def extract_requirements(file: UploadFile = File(...)):
    global LATEST_ANALYSIS_RUN_ID  # Declare intention to modify global
    run_id = str(uuid.uuid4())
    LATEST_ANALYSIS_RUN_ID = run_id  # Set this as the latest run_id
    log_prefix = f"[Run ID: {run_id}] [Endpoint] "

    # Initialize progress store
    analysis_progress_store[run_id] = {
        "run_id": run_id,
        "status": "starting",
        "filename": file.filename,
        "start_time_utc": get_current_timestamp(),
        "last_update_timestamp": get_current_timestamp(),
        "message": "Processing initiated.",
    }
    logger.info(
        f"{log_prefix}Set as LATEST_ANALYSIS_RUN_ID. Initialized progress store. Received file: {file.filename}"
    )

    # ------------ START OF EXISTING CORE LOGIC (abbreviated for this edit focus) -----------
    if not file.filename.endswith(".pdf"):
        logger.error(f"{log_prefix}File not PDF received. Rejecting.")
        analysis_progress_store[run_id]["status"] = "error_validation"
        analysis_progress_store[run_id]["error_message"] = "Only PDF files are accepted"
        analysis_progress_store[run_id][
            "last_update_timestamp"
        ] = get_current_timestamp()
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    normalized_filename = normalize_filename(file.filename)
    temp_file_path = Path(f"./tmp/{normalized_filename}")
    output_dir = Path("./output")
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_file_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        logger.info(f"{log_prefix}File saved temporarily to: {temp_file_path}")
    except Exception as e_save:
        logger.error(f"{log_prefix}Failed to save uploaded file: {e_save}")
        analysis_progress_store[run_id]["status"] = "error_file_save"
        analysis_progress_store[run_id]["error_message"] = str(e_save)
        analysis_progress_store[run_id][
            "last_update_timestamp"
        ] = get_current_timestamp()
        raise HTTPException(
            status_code=500, detail=f"Failed to save uploaded file: {str(e_save)}"
        )

    file_hash = compute_file_hash(str(temp_file_path))
    output_filename = f"{file_hash}.json"
    output_file_path = output_dir / output_filename
    logger.info(
        f"{log_prefix}Temporary file: {temp_file_path}, Output JSON: {output_file_path}"
    )

    update_analysis_progress(run_id, {"status": "checking_cache"})
    exists, location = await check_and_retrieve_resource(
        "requirements", output_filename, output_file_path
    )

    if exists:
        logger.info(f"{log_prefix}Cached file found: {output_filename}")
        try:
            with open(output_file_path, "r", encoding="utf-8") as f_cache:
                requirements_data = json.load(f_cache)
            base_name = normalized_filename.replace(".pdf", "")
            pdf_json_mapping.mapping[base_name] = output_filename
            save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)
            update_analysis_progress(
                run_id,
                {
                    "status": "completed_from_cache",
                    "percent_done": 100.0,
                    "message": "Retrieved from cache.",
                },
            )
            if isinstance(requirements_data, dict):
                requirements_data["run_id"] = run_id
            # Clean up local JSON cache as blob storage is the single source of truth
            cleanup_local_json_files()
            return requirements_data
        except Exception as e_cache_read:
            logger.error(f"{log_prefix}Error reading cached file: {str(e_cache_read)}")
            update_analysis_progress(
                run_id,
                {"status": "error_reading_cache", "error_message": str(e_cache_read)},
            )
            # Fall through to re-analysis

    text_content_for_sum_data = ""
    start_timestamp_analysis = None
    end_timestamp_analysis = None
    final_requirements_data = {}
    try:
        update_analysis_progress(run_id, {"status": "preprocessing"})

        USE_NEW_ANALYZER = True  # Hardcoded parameter, True to use new lex_package

        if USE_NEW_ANALYZER:
            logger.info(f"{log_prefix}Using new lex_package for analysis.")
            update_analysis_progress(run_id, {"status": "llm_analysis_starting"})
            start_timestamp_analysis = get_current_timestamp()

            with fitz.open(str(temp_file_path)) as doc:
                text_content_for_sum_data = "\n".join(page.get_text() for page in doc)

            # TODO: CAPIRE WRAPPING FUNZIONE PER STATI INTERMEDI
            update_analysis_progress(
                run_id, {"status": "llm_analysis_in_progress", "percent_done": 20.0}
            )
            raw_results = await analisi(
                pdf_path=str(temp_file_path), pdf_name=normalized_filename
            )

            out_analisi_dir = Path("./out_analisi")
            out_analisi_dir.mkdir(parents=True, exist_ok=True)

            update_analysis_progress(
                run_id, {"status": "llm_analysis_formatting", "percent_done": 80.0}
            )
            logger.info(f"Raw results: {raw_results}\n\n\n\n\n\n\n")
            flattened_data = flatten_analisi(raw_results)

            # Return the original structure (not flattened) for Azure Durable Function
            # This ensures compatibility with attuativa comparison mode
            requirements_result_dict = {"run_id": run_id, "articoli": raw_results}

            logger.info(f"Flattened data: {flattened_data}\n\n\n\n\n\n\n")
            out_flat_analisi_dir = Path("./out_flat/out_analisi")
            out_flat_analisi_dir.mkdir(parents=True, exist_ok=True)

            # Save Excel file
            write_records_to_xlsx(
                flattened_data, out_flat_analisi_dir / f"{file_hash}.xlsx"
            )

            # Upload Excel file to blob storage
            excel_file_path = out_flat_analisi_dir / f"{file_hash}.xlsx"
            upload_to_blob(
                "requirements", file_path=excel_file_path, blob_name=f"{file_hash}.xlsx"
            )
            logger.info(
                f"{log_prefix}Uploaded Excel file to blob storage: {file_hash}.xlsx"
            )

            # Create and upload plain-named Excel file
            plain_excel_filename = normalized_filename.replace(".pdf", ".xlsx")
            plain_excel_path = out_flat_analisi_dir / plain_excel_filename
            shutil.copy(excel_file_path, plain_excel_path)
            upload_to_blob(
                "requirements",
                file_path=plain_excel_path,
                blob_name=plain_excel_filename,
            )
            logger.info(
                f"{log_prefix}Uploaded plain-named Excel file to blob storage: {plain_excel_filename}"
            )

            # Save original (non-flattened) JSON locally
            with open(
                out_analisi_dir / f"{file_hash}.json", "w", encoding="utf-8"
            ) as file:
                json.dump(raw_results, file, ensure_ascii=False, indent=2)

            # Save flattened JSON locally
            with open(
                out_flat_analisi_dir / f"{file_hash}_flattened.json",
                "w",
                encoding="utf-8",
            ) as file:
                json.dump(flattened_data, file, ensure_ascii=False, indent=2)

            logger.info(
                f"{log_prefix}New analyzer processing complete. Saving results to: {output_file_path}"
            )

            # Save the main output file (with full structure including run_id)
            with open(output_file_path, "w", encoding="utf-8") as f_out:
                json.dump(requirements_result_dict, f_out, ensure_ascii=False, indent=4)

            logger.info(f"[OUT_ANALISI] {out_analisi_dir}")

            # Verify files exist before uploading
            original_file_path = out_analisi_dir / f"{file_hash}.json"
            flattened_file_path = out_flat_analisi_dir / f"{file_hash}_flattened.json"

            if not original_file_path.exists():
                logger.error(f"Original file not found: {original_file_path}")
                raise FileNotFoundError(
                    f"Original file not found: {original_file_path}"
                )
            else:
                logger.info(
                    f"Original file exists: {original_file_path} (size: {original_file_path.stat().st_size} bytes)"
                )

            if not flattened_file_path.exists():
                logger.error(f"Flattened file not found: {flattened_file_path}")
                raise FileNotFoundError(
                    f"Flattened file not found: {flattened_file_path}"
                )
            else:
                logger.info(
                    f"Flattened file exists: {flattened_file_path} (size: {flattened_file_path.stat().st_size} bytes)"
                )

            # Upload original (non-flattened) version to blob as {hash}.json
            upload_to_blob(
                "requirements",
                file_path=original_file_path,
                blob_name=f"{file_hash}.json",
            )
            logger.info(
                f"{log_prefix}Uploaded original JSON to blob storage: {file_hash}.json"
            )

            # Upload flattened version to blob as {hash}_flattened.json
            upload_to_blob(
                "requirements",
                file_path=flattened_file_path,
                blob_name=f"{file_hash}_flattened.json",
            )
            logger.info(
                f"{log_prefix}Uploaded flattened JSON to blob storage: {file_hash}_flattened.json"
            )

            # Also save plain-named versions
            base_name = normalized_filename.replace(".pdf", "")

            # Copy and upload plain-named original JSON
            plain_original_path = out_analisi_dir / f"{base_name}.json"
            shutil.copy(out_analisi_dir / f"{file_hash}.json", plain_original_path)
            upload_to_blob(
                "requirements",
                file_path=plain_original_path,
                blob_name=f"{base_name}.json",
            )

            # Copy and upload plain-named flattened JSON
            plain_flattened_path = out_flat_analisi_dir / f"{base_name}_flattened.json"
            shutil.copy(
                out_flat_analisi_dir / f"{file_hash}_flattened.json",
                plain_flattened_path,
            )
            upload_to_blob(
                "requirements",
                file_path=plain_flattened_path,
                blob_name=f"{base_name}_flattened.json",
            )

            # Update mapping
            pdf_json_mapping.mapping[base_name] = output_filename
            save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)

            end_timestamp_analysis = get_current_timestamp()
            update_analysis_progress(
                run_id,
                {
                    "status": "completed_analysis",  # Simplified status for new analyzer
                    "percent_done": 100.0,
                    "message": "Analysis successful using new analyzer.",
                },
            )
            end_timestamp_analysis = get_current_timestamp()

            #  Ensure the cached JSON is also present on Azure Blob Storage
            try:
                if not blob_exists("requirements", output_filename):
                    upload_to_blob(
                        "requirements",
                        file_path=output_file_path,
                        blob_name=output_filename,
                    )
                # Also upload the plain-named version (e.g. "7._Direttiva_...json")
                plain_json_path = output_file_path.parent / f"{base_name}.json"
                if not plain_json_path.exists():
                    shutil.copy(output_file_path, plain_json_path)
                if not blob_exists("requirements", plain_json_path.name):
                    upload_to_blob(
                        "requirements",
                        file_path=plain_json_path,
                        blob_name=plain_json_path.name,
                    )
            except Exception as e_blob_sync:
                logger.warning(
                    f"{log_prefix}Unable to sync cached JSON to blob storage: {e_blob_sync}"
                )

        else:  # Fallback to old RequirementAnalyzer
            logger.info(f"{log_prefix}Using old RequirementAnalyzer.")
            azure_config = {
                "api_key": os.getenv("AZURE_OPENAI_API_KEY"),
                "azure_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT"),
                "api_version": os.getenv("AZURE_API_VERSION", "2024-08-01-preview"),
            }
            analyzer = RequirementAnalyzer(
                backend="azure_openai", azure_config=azure_config
            )
            text_content_for_sum_data, offsets = (
                analyzer.extract_text_with_page_mapping(str(temp_file_path))
            )
            update_analysis_progress(
                run_id, {"status": "llm_analysis_starting"}
            )  # Old analyzer has its own progress
            start_timestamp_analysis = get_current_timestamp()
            requirements_result_dict = await analyzer.analyze_text_async(
                text_content_for_sum_data,
                offsets,
                run_id=run_id,
                progress_callback=update_analysis_progress,
            )
            analyzer.save_results(
                requirements_result_dict,
                str(temp_file_path),
                str(output_file_path),
                text_content_for_sum_data,
            )
            end_timestamp_analysis = get_current_timestamp()
            # Progress for 'completed_analysis' is typically set by the callback in old analyzer path.
            # Explicitly ensuring a final status if not already set to completed by callback.
            if analysis_progress_store[run_id].get("status") != "completed_analysis":
                update_analysis_progress(
                    run_id,
                    {
                        "status": "completed_analysis",
                        "percent_done": 100.0,
                        "message": "Analysis successful using old analyzer.",
                    },
                )

        # sum.json update (common to both paths)
        try:
            app_connection_string = os.getenv("CONNECTION_STRING")
            if (
                app_connection_string
                and text_content_for_sum_data
                and start_timestamp_analysis
                and end_timestamp_analysis
            ):
                token_list = get_tokens(text_content_for_sum_data)
                token_count = len(token_list)
                elapsed_time_analysis = calculate_seconds_between(
                    start_timestamp_analysis, end_timestamp_analysis
                )
                update_sum_data(
                    elapsed_time=elapsed_time_analysis,
                    token_count=token_count,
                    mode="extraction",
                    connection_string=app_connection_string,
                    start_timestamp=start_timestamp_analysis,
                    end_timestamp=end_timestamp_analysis,
                )
                update_analysis_progress(
                    run_id, {"sum_data_updated": True}
                )  # Merge this status
            elif not app_connection_string:
                logger.warning(
                    f"{log_prefix}CONNECTION_STRING not set. Skipping sum.json update."
                )
            else:
                logger.warning(
                    f"{log_prefix}Could not update sum.json due to missing text, timestamps, or token count."
                )
        except Exception as e_sum_data:
            logger.error(f"{log_prefix}Error during sum.json update: {e_sum_data}")
            update_analysis_progress(run_id, {"sum_data_error": str(e_sum_data)})

        with open(output_file_path, "r", encoding="utf-8") as f_final:
            final_requirements_data = json.load(f_final)
        if isinstance(final_requirements_data, list):
            final_requirements_data = {"articoli": final_requirements_data}

        if not isinstance(final_requirements_data, dict):
            update_analysis_progress(
                run_id,
                {
                    "status": "error_output_format",
                    "error_message": f"Unexpected type: {type(final_requirements_data)}",
                },
            )
            raise ValueError("Output JSON neither list nor dict")

        # Post-analysis ops
        base_name = normalized_filename.replace(".pdf", "")
        plain_json_path = output_file_path.parent / f"{base_name}.json"
        shutil.copy(output_file_path, plain_json_path)
        upload_to_blob(
            "requirements", file_path=output_file_path, blob_name=output_filename
        )
        upload_to_blob(
            "requirements", file_path=plain_json_path, blob_name=f"{base_name}.json"
        )
        pdf_json_mapping.mapping[base_name] = output_filename
        save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)
        print_mapping(pdf_json_mapping.mapping)
        logger.info(
            f"{log_prefix}[EXTRACT] Mapping aggiornato: {pdf_json_mapping.mapping}"
        )
        update_analysis_progress(
            run_id,
            {"status": "completed_finalized", "message": "All operations complete."},
        )
        final_requirements_data["run_id"] = run_id
        cleanup_local_json_files()
        return final_requirements_data

    except Exception as e_main_analysis:
        logger.exception(
            f"{log_prefix}Overall error in requirement extraction: {e_main_analysis}"
        )
        update_analysis_progress(
            run_id,
            {"status": "error_in_processing", "error_message": str(e_main_analysis)},
        )
        raise HTTPException(
            status_code=500,
            detail=f"Error during requirement extraction: {str(e_main_analysis)}",
        )


@app.get("/download-comparison-excel")
async def download_comparison_excel(
    fileName1: str = Query(..., description="Primo PDF originale"),
    fileName2: str = Query(..., description="Secondo PDF originale"),
    comparisonMode: str = Query(
        "versioning", description="'versioning'|'attuativa'|'emendativa'"
    ),
):
    """
    Restituisce l'Excel già creato nel blob. Blob-first: legge da Blob Storage
    senza usare filesystem del pod; fallback a check_and_retrieve_resource.
    """
    h1 = get_hash_for_name(fileName1)
    h2 = get_hash_for_name(fileName2)
    h1, h2 = sorted([h1, h2])

    plain1 = normalize_filename(fileName1).replace(".pdf", "")
    plain2 = normalize_filename(fileName2).replace(".pdf", "")
    download_name = f"{plain1}_vs_{plain2}.xlsx"

    folder = {
        "versioning": "versionings",
        "attuativa": "implementations",
        "emendativa": "amendments",
    }.get(comparisonMode, "comparisons")

    excel_name = (
        f"{h1}_vs_{h2}.xlsx"
        if comparisonMode in ("versioning", "attuativa", "emendativa")
        else f"{h1}_vs_{h2}_comparison.xlsx"
    )

    content = get_blob_bytes(folder, excel_name)
    if content is not None:
        logger.info(f"Excel confronto servito da blob: {folder}/{excel_name}")
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
        )

    if pdf_json_mapping.comparisonMapping:
        for comp_key in (f"{plain1}_{plain2}", f"{plain2}_{plain1}"):
            comp_file = pdf_json_mapping.comparisonMapping.get(comp_key)
            if not comp_file:
                continue
            excel_blob_name = comp_file.replace(".json", ".xlsx") if comp_file.endswith(".json") else comp_file
            if not excel_blob_name.endswith(".xlsx"):
                continue
            content = get_blob_bytes(folder, excel_blob_name)
            if content is not None:
                logger.info(f"Excel confronto servito da blob (mapping): {excel_blob_name}")
                return Response(
                    content=content,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
                )

    local_dir = Path("./output")
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / excel_name
    exists, _ = await check_and_retrieve_resource(folder, excel_name, local_path)
    if not exists and pdf_json_mapping.comparisonMapping:
        for comp_key in (f"{plain1}_{plain2}", f"{plain2}_{plain1}"):
            comp_file = pdf_json_mapping.comparisonMapping.get(comp_key)
            if not comp_file:
                continue
            excel_blob_name = comp_file.replace(".json", ".xlsx") if comp_file.endswith(".json") else comp_file
            if not excel_blob_name.endswith(".xlsx"):
                continue
            fallback_path = local_dir / excel_blob_name
            exists, _ = await check_and_retrieve_resource(folder, excel_blob_name, fallback_path)
            if exists:
                local_path = fallback_path
                break
    if not exists:
        raise HTTPException(
            status_code=404,
            detail=f"Excel confronto non trovato nel blob (folder={folder}, name={excel_name}). Verificare che il confronto sia stato eseguito e che i file siano in mapping.",
        )
    return FileResponse(
        path=str(local_path),
        filename=download_name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

def get_hash_for_name(pdf_name: str) -> str:
    """Restituisce l'hash (stem del JSON) per il nome PDF. Prova più varianti di nome per compatibilità con path."""
    if not pdf_name or not isinstance(pdf_name, str):
        raise HTTPException(400, "Nome file non valido")
    raw = normalize_filename(pdf_name).strip()
    if raw.lower().endswith(".pdf"):
        raw = raw[:-4].strip()
    normalized = raw
    def canonical(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    keys_to_try = [
        normalized,
        normalized.split("/")[-1].split("\\")[-1],
    ]
    for k in keys_to_try:
        if not k:
            continue
        mapped = pdf_json_mapping.mapping.get(k)
        if mapped:
            return Path(mapped).stem
    for map_key, mapped in pdf_json_mapping.mapping.items():
        if map_key == normalized or map_key.endswith(normalized) or normalized.endswith(map_key):
            return Path(mapped).stem
    norm_canonical = canonical(normalized)
    if norm_canonical:
        for map_key, mapped in pdf_json_mapping.mapping.items():
            if canonical(map_key) == norm_canonical:
                logger.info("get_hash_for_name: match canonico '%s' -> '%s'", pdf_name, map_key)
                return Path(mapped).stem
        for map_key, mapped in pdf_json_mapping.mapping.items():
            ck = canonical(map_key)
            if len(ck) < 10:
                continue
            if norm_canonical in ck or ck in norm_canonical:
                logger.info("get_hash_for_name: match canonico substring '%s' -> '%s'", pdf_name, map_key)
                return Path(mapped).stem
    for k in keys_to_try:
        if not k:
            continue
        local_pdf = Path("./tmp") / f"{k}.pdf"
        if local_pdf.exists():
            return compute_file_hash(str(local_pdf))
    raise HTTPException(404, f"Hash non trovato per '{pdf_name}' (provate: {keys_to_try})")


class DeleteFilesRequest(BaseModel):
    files: List[str]


@app.delete("/delete-files", response_model=dict)
async def delete_files(request: DeleteFilesRequest):
    """
    Delete files from blob storage including all associated files.
    For PDFs, this includes their JSON, flattened JSON, and Excel files.
    """
    deleted_files = []
    failed_files = []

    logger.info(f"[DELETE] Request to delete files: {request.files}")

    # Log current state of mappings
    logger.info("=" * 80)
    logger.info("[DELETE] Current PDF to JSON mapping:")
    logger.info("=" * 80)
    for pdf_name, json_path in sorted(pdf_json_mapping.mapping.items()):
        logger.info(f"  {pdf_name:<50} -> {json_path}")
    logger.info(f"Total PDFs in mapping: {len(pdf_json_mapping.mapping)}")
    logger.info("=" * 80)

    if pdf_json_mapping.comparisonMapping:
        logger.info("[DELETE] Current comparison mappings:")
        logger.info("-" * 80)
        for comp_key, comp_file in sorted(pdf_json_mapping.comparisonMapping.items()):
            logger.info(f"  {comp_key:<50} -> {comp_file}")
        logger.info(f"Total comparisons: {len(pdf_json_mapping.comparisonMapping)}")
        logger.info("=" * 80)

    for filename in request.files:
        try:
            files_to_delete = []

            logger.info(f"\n[DELETE] Processing file: {filename}")

            # Normalize the filename
            normalized_name = normalize_filename(filename)
            base_name = (
                normalized_name.replace(".pdf", "")
                .replace(".json", "")
                .replace(".xlsx", "")
            )

            logger.info(f"[DELETE] Normalized name: {normalized_name}")
            logger.info(f"[DELETE] Base name: {base_name}")

            # Check if it's a PDF
            if filename.endswith(".pdf"):
                # Get the hash from mapping - use base_name (without .pdf extension)
                json_filename = pdf_json_mapping.mapping.get(base_name)
                logger.info(
                    f"[DELETE] Mapping lookup for '{base_name}': {json_filename}"
                )

                # Always add the PDF itself first
                files_to_delete.append(f"requirements/{normalized_name}")

                # Also scan blob storage for any files that start with the base_name
                # This catches files that might not be in the mapping
                logger.info(
                    f"[DELETE] Scanning blob storage for files starting with '{base_name}'"
                )
                try:
                    # List all blobs in requirements folder that start with base_name
                    blobs_with_prefix = list(
                        container_client.list_blobs(
                            name_starts_with=f"requirements/{base_name}"
                        )
                    )
                    for blob in blobs_with_prefix:
                        blob_name = blob.name
                        # Skip comparison files - they should be deleted separately
                        if "_vs_" in blob_name:
                            logger.info(
                                f"[DELETE] Skipping comparison file: {blob_name}"
                            )
                            continue
                        if blob_name not in files_to_delete:
                            files_to_delete.append(blob_name)
                            logger.info(
                                f"[DELETE] Found additional file via blob scan: {blob_name}"
                            )
                except Exception as e:
                    logger.warning(
                        f"[DELETE] Error scanning blobs with prefix '{base_name}': {str(e)}"
                    )

                if json_filename:
                    file_hash = Path(json_filename).stem

                    # Also scan for files with the hash prefix
                    logger.info(
                        f"[DELETE] Scanning blob storage for files starting with hash '{file_hash}'"
                    )
                    try:
                        hash_blobs = list(
                            container_client.list_blobs(
                                name_starts_with=f"requirements/{file_hash}"
                            )
                        )
                        for blob in hash_blobs:
                            blob_name = blob.name
                            # Skip comparison files - they should be deleted separately
                            if "_vs_" in blob_name:
                                logger.info(
                                    f"[DELETE] Skipping comparison file: {blob_name}"
                                )
                                continue
                            if blob_name not in files_to_delete:
                                files_to_delete.append(blob_name)
                                logger.info(
                                    f"[DELETE] Found additional file via hash scan: {blob_name}"
                                )
                    except Exception as e:
                        logger.warning(
                            f"[DELETE] Error scanning blobs with hash '{file_hash}': {str(e)}"
                        )

                    # Add all known associated files (for redundancy)
                    files_to_delete.extend(
                        [
                            f"requirements/{file_hash}.json",
                            f"requirements/{file_hash}_flattened.json",
                            f"requirements/{file_hash}.xlsx",
                            f"requirements/{base_name}_flattened.json",  # Plain named flattened
                            f"requirements/{base_name}.xlsx",  # Plain named Excel
                        ]
                    )

                    # Check for comparison files
                    for (
                        comp_key,
                        comp_file,
                    ) in pdf_json_mapping.comparisonMapping.items():
                        if base_name in comp_key or file_hash in comp_key:
                            # Add comparison files
                            comp_hash = Path(comp_file).stem
                            files_to_delete.extend(
                                [
                                    f"comparisons/{comp_file}",
                                    f"comparisons/{comp_hash}.xlsx",
                                    f"amendments/{comp_file}",
                                    f"amendments/{comp_hash}.xlsx",
                                    f"implementations/{comp_file}",
                                    f"implementations/{comp_hash}.xlsx",
                                ]
                            )
                else:
                    # No mapping found, but we already added the PDF and scanned for base_name files
                    logger.info(
                        f"[DELETE] No mapping found for '{normalized_name}', but already scanned for related files"
                    )

            elif "_vs_" in filename:
                # It's a comparison file
                # Add files from all comparison folders
                for folder in ["comparisons", "amendments", "implementations"]:
                    files_to_delete.extend(
                        [
                            f"{folder}/{filename}",
                            f"{folder}/{filename.replace('.json', '.xlsx')}",
                        ]
                    )
            else:
                # Regular file - try all folders
                for folder in [
                    "requirements",
                    "comparisons",
                    "amendments",
                    "implementations",
                ]:
                    files_to_delete.append(f"{folder}/{filename}")

            # Log files to be deleted
            if files_to_delete:
                logger.info(f"[DELETE] Files to delete for '{filename}':")
                for f in files_to_delete:
                    logger.info(f"  - {f}")
            else:
                logger.info(
                    f"[DELETE] No files identified for deletion for '{filename}'"
                )

            # Delete files from blob storage
            for blob_name in files_to_delete:
                try:
                    blob_client = container_client.get_blob_client(blob_name)
                    blob_client.delete_blob()
                    deleted_files.append(blob_name)
                    logger.info(f"[DELETE] Deleted blob: {blob_name}")
                except Exception as e:
                    # Blob might not exist, which is okay
                    logger.debug(
                        f"[DELETE] Could not delete blob {blob_name}: {str(e)}"
                    )

            # Update mappings if it's a PDF
            if filename.endswith(".pdf") and base_name in pdf_json_mapping.mapping:
                del pdf_json_mapping.mapping[base_name]
                save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)
                logger.info(f"[DELETE] Removed {base_name} from mapping")

            # Remove from comparison mapping
            keys_to_remove = []
            for comp_key in pdf_json_mapping.comparisonMapping:
                if base_name in comp_key or (
                    json_filename and Path(json_filename).stem in comp_key
                ):
                    keys_to_remove.append(comp_key)

            for key in keys_to_remove:
                del pdf_json_mapping.comparisonMapping[key]
                logger.info(f"[DELETE] Removed comparison mapping: {key}")

            if keys_to_remove:
                save_pdf_mapping(pdf_json_mapping.mapping, MAPPING_FILE_PATH)

        except Exception as e:
            logger.error(f"[DELETE] Error deleting {filename}: {str(e)}")
            failed_files.append(filename)

    return {
        "success": len(failed_files) == 0 and len(deleted_files) > 0,
        "deletedFiles": list(set(deleted_files)),  # Remove duplicates
        "failedFiles": failed_files,
        "message": f"Deleted {len(set(deleted_files))} files",
    }
