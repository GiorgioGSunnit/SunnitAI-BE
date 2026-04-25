# === Setup path per moduli condivisi ===
import sys
from pathlib import Path

# Aggiungi /app/src al path per trovare core/ e utils/
_app_src = Path("/app/src")
if _app_src.exists() and str(_app_src) not in sys.path:
    sys.path.insert(0, str(_app_src))

# Bootstrap: carica credenziali da Azure Key Vault PRIMA di qualsiasi os.getenv()
try:
    import core.bootstrap  # noqa: F401 - side effect import
except ImportError:
    pass  # In ambiente locale senza core/, usa .env

# from WrapperFunction import app as fastapi_app

# app = func.AsgiFunctionApp(app=fastapi_app, http_auth_level=func.AuthLevel.ANONYMOUS)

from datetime import datetime, timedelta
import random
import time
import azure_func_compat as func  # replaces: import azure.functions as func
import logging
from concurrent.futures import ThreadPoolExecutor

from job_store import (
    create_job,
    get_job,
    set_completed,
    set_failed,
    set_running,
)

# Storage handled by local filesystem via blob_storage_client.py

from utils import blob_storage_client as bsc
from dotenv import load_dotenv
import os
import json
from pathlib import Path
from typing import List, Dict
import requests
import base64
from PyPDF2 import PdfReader
from io import BytesIO
ResourceNotFoundError = FileNotFoundError
import threading
import tempfile
import glob
import urllib.parse
import statistics
import hashlib  
import re
import numpy as np
from sklearn.linear_model import LinearRegression
from urllib.parse import urlencode

# Monitoring: standard Python logging is used. Azure Monitor removed.

DEBUG = False

QUEUE_NAME = "extract-requirements-queue-async"

logging.basicConfig(
    level=logging.ERROR, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

load_dotenv()

# Azure Function App
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Executor per job in background
_job_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="job_")

# Configurazioni (single container con path prefix)
CONTAINER_NAME = ""  # PDF interni: root del container
CONTAINER_NAME_EXT = "out"  # output analisi: out/
MODE = os.getenv("MODE")
IPVMAI = os.getenv("IPVMAI")
IPVMNER = os.getenv("IPVMNER")

HISTORY_FILENAME = "tokens_per_second_history.json"
MAX_HISTORY_ENTRIES = 10
DEFAULT_TOKENS_PER_SECOND = 63
BLOB_CONFIG_CONTAINER = "conf"
BLOB_FILENAME = "tokens_per_second.json"
SUM_BLOB_FILENAME = "sum.json"

# Global lock objects
extraction_lock = threading.Lock()
comparison_lock = threading.Lock()
lock_file_path = os.path.join(tempfile.gettempdir(), "vmai_processing")


# Coefficienti del modello log-transform stabilizzato, calcolati dall'analisi dei dati storici
LOG_MODEL_COEFFICIENTS = {
    "intercept": 4.6741,
    "server": -0.5726,
    "size_mb": 0.8864,
    "chunks": 1.1992,
}


@app.route(route="warmup", methods=["GET"])
def warmup(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Function App instance is warm")
    return func.HttpResponse(status_code=200)


@app.route(route="job/{job_id}", methods=["GET"])
def get_job_status(req: func.HttpRequest) -> func.HttpResponse:
    """Polling endpoint per stato job. Compatibile con formato Azure Durable."""
    job_id = req.route_params.get("job_id")
    if not job_id:
        return func.HttpResponse(
            json.dumps({"error": "Missing job_id"}), status_code=400, mimetype="application/json"
        )
    job = get_job(job_id)
    if not job:
        return func.HttpResponse(
            json.dumps({"error": "Job not found", "id": job_id}), status_code=404, mimetype="application/json"
        )
    # Formato compatibile con FE che fa polling (runtimeStatus, output)
    body = {
        "id": job["id"],
        "runtimeStatus": job["runtimeStatus"],
        "created_at": job["created_at"],
    }
    if job.get("custom_status"):
        body["customStatus"] = job["custom_status"]
    if job.get("result") is not None:
        body["output"] = job["result"]
    if job.get("error") is not None:
        body["output"] = {"error": job["error"], "status": "error"}
    return func.HttpResponse(
        json.dumps(body), status_code=200, mimetype="application/json"
    )


def predict_time_log_model_complete(file_content: bytes, estimated_server_time: float, chunker_fn, extract_text_fn) -> float:
    """
    Stima il tempo di elaborazione usando un modello di regressione log-transform.
    """

    text = extract_text_fn(BytesIO(file_content))
    chunks_count = len(chunker_fn(text))
    file_size_mb = len(file_content) / (1024 * 1024)

    log_server = np.log1p(estimated_server_time)
    log_size = np.log1p(file_size_mb)
    log_chunks = np.log1p(chunks_count)

    pred_log = (
        LOG_MODEL_COEFFICIENTS["intercept"]
        + LOG_MODEL_COEFFICIENTS["server"] * log_server
        + LOG_MODEL_COEFFICIENTS["size_mb"] * log_size
        + LOG_MODEL_COEFFICIENTS["chunks"] * log_chunks
    )

    return float(np.expm1(pred_log))

def cleanup_stale_locks():
    """Remove any stale lock files and blobs from previous runs that may have crashed"""
    try:
        # 1. Clean up local lock files
        lock_pattern = f"{lock_file_path}*.lock"
        stale_locks = glob.glob(lock_pattern)

        if stale_locks:
            logger.info(
                f"Found {len(stale_locks)} stale local lock files. Cleaning up..."
            )
            for lock_file in stale_locks:
                try:
                    os.remove(lock_file)
                    logger.info(f"Removed stale lock file: {lock_file}")
                except Exception as e:
                    logger.warning(
                        f"Failed to remove stale lock file {lock_file}: {str(e)}"
                    )
        else:
            logger.info("No stale local lock files found.")

        # 2. Clean up Azure blob storage locks
        try:
            if not bsc.is_available():
                logger.warning("Blob Storage non configurato, skipping blob lock cleanup")
                return

            try:
                container_client = bsc.get_container_client()
                blobs = list(container_client.list_blobs(name_starts_with=bsc.path_conf("locks/")))
            except Exception as e:
                logger.error(f"Failed to list blobs: {str(e)}")
                return

            lock_count = 0
            for blob in blobs:
                try:
                    blob_client = container_client.get_blob_client(blob.name)

                    # Optional: Check if lock is expired before deleting
                    try:
                        lock_data = json.loads(blob_client.download_blob().readall())
                        locked_at = lock_data.get("locked_at", "")
                        operation_type = lock_data.get("operation_type", "unknown")
                        logger.info(
                            f"Found lock from: {locked_at}, type: {operation_type}"
                        )
                    except:
                        logger.info(
                            f"Found lock blob without readable metadata: {blob.name}"
                        )

                    # Delete the lock blob
                    blob_client.delete_blob()
                    logger.info(f"Removed stale blob lock: {blob.name}")
                    lock_count += 1
                except Exception as e:
                    logger.warning(
                        f"Failed to remove stale blob lock {blob.name}: {str(e)}"
                    )

            if lock_count > 0:
                logger.info(f"Cleaned up {lock_count} stale blob locks")
            else:
                logger.info("No stale blob locks found")

        except Exception as e:
            logger.error(f"Error cleaning up blob locks: {str(e)}")

    except Exception as e:
        logger.error(f"Error cleaning up stale locks: {str(e)}")


# Clean up stale locks at startup
cleanup_stale_locks()


# Process lock status helper functions using Azure Blob Storage for distributed locking
def is_any_process_locked(operation_type=None):
    """
    Check if any process is already running using blob existence

    Args:
        operation_type: Optional. Se specificato, controlla solo se c'è un lock
                       per un'operazione incompatibile con questa
    """
    try:
        if not bsc.is_available():
            logger.warning("Blob Storage non configurato, assuming no active locks")
            return False

        try:
            container_client = bsc.get_container_client()
            blobs = list(container_client.list_blobs(name_starts_with=bsc.path_conf("locks/")))
        except Exception as e:
            logger.error(f"Error listing lock blobs: {str(e)}")
            return False

        if not blobs:
            return False  # No locks found

        # Definisci i gruppi di operazioni incompatibili
        # Le operazioni nello stesso gruppo non possono essere eseguite in parallelo
        incompatible_groups = {
            "extraction": ["extraction", "comparison"],
            "comparison": ["extraction", "comparison", "subjects", "sanctions"],
            "subjects": ["subjects", "comparison"],
            "sanctions": ["sanctions", "comparison"],
            "translation": ["translation"],
        }

        # Se non è specificato un tipo di operazione, controlla se esiste qualsiasi lock
        if operation_type is None:
            return len(blobs) > 0

        # Altrimenti, controlla solo se ci sono lock per operazioni incompatibili
        incompatible_ops = incompatible_groups.get(operation_type, [])

        for blob in blobs:
            blob_client = container_client.get_blob_client(blob.name)
            try:
                lock_data = json.loads(blob_client.download_blob().readall())
                existing_op_type = lock_data.get("operation_type", "unknown")

                # Check if lock has expired
                expires_at = lock_data.get("expires_at")
                if expires_at:
                    try:
                        expiry_time = datetime.fromisoformat(expires_at)
                        if expiry_time < datetime.utcnow():
                            logger.info(
                                f"Found expired lock for {existing_op_type}, cleaning up"
                            )
                            try:
                                blob_client.delete_blob()
                                continue  # Skip this expired lock
                            except:
                                pass  # If we can't delete, treat as valid lock
                    except:
                        pass  # If we can't parse the date, treat as unexpired

                # Check if this operation is incompatible with requested operation
                if existing_op_type in incompatible_ops:
                    logger.info(
                        f"Found incompatible operation lock: {existing_op_type}"
                    )
                return True
            except:
                # If we can't read the lock data, assume it's incompatible to be safe
                logger.warning(f"Found lock blob but couldn't read data: {blob.name}")
                return True

            # No incompatible operations found
            return False
    except Exception as e:
        logger.error(f"Error checking lock status: {str(e)}")
        # If we can't check, assume unlocked to allow operations to proceed
        return False


def acquire_global_lock(operation_type="processing", timeout_minutes=40):
    """Try to acquire the global lock for any operation type using blob creation"""
    try:
        if not bsc.is_available():
            logger.warning("Blob Storage non configurato, skipping lock acquisition")
            return "dummy_lock"

        try:
            container_client = bsc.get_container_client()
        except Exception as e:
            logger.error(f"Failed to get container client: {str(e)}")
            return "dummy_lock"

        blob_name = bsc.path_locks("global_operation.lock")
        blob_client = container_client.get_blob_client(blob_name)

        try:
            # Check if blob already exists
            blob_client.get_blob_properties()
            # If we get here, the blob exists and we can't acquire the lock

            # Optional: Check if existing lock has expired
            try:
                lock_data = json.loads(blob_client.download_blob().readall())
                expires_at = lock_data.get("expires_at")
                if expires_at:
                    try:
                        expiry_time = datetime.fromisoformat(expires_at)
                        if expiry_time < datetime.utcnow():
                            logger.info("Found expired lock, deleting it")
                            try:
                                blob_client.delete_blob()
                                # Continue to create a new lock
                            except Exception as delete_error:
                                logger.error(
                                    f"Failed to delete expired lock: {str(delete_error)}"
                                )
                                return None
                        else:
                            # Lock exists and is not expired
                            return None
                    except Exception as parse_error:
                        logger.error(f"Failed to parse expiry time: {str(parse_error)}")
                        return None
                else:
                    # Lock exists without expiry time
                    return None
            except Exception as e:
                logger.error(f"Error checking lock expiry: {str(e)}")
                return None
        except:
            # Blob doesn't exist, we can create it
            pass

        # Create new lock with expiration time
        try:
            expiration_time = datetime.utcnow() + timedelta(minutes=timeout_minutes)
            lock_data = {
                "locked_at": get_current_timestamp(),
                "expires_at": expiration_time.isoformat(),
                "operation_type": operation_type,
            }

            blob_client.upload_blob(json.dumps(lock_data), overwrite=True)
            logger.info(
                f"Created lock for {operation_type} operation with {timeout_minutes} minute timeout"
            )
            return blob_name
        except Exception as create_error:
            logger.error(f"Failed to create lock blob: {str(create_error)}")
            return None
    except Exception as e:
        logger.error(f"Error acquiring lock: {str(e)}")
        return None


def release_lock(blob_name):
    """Release a previously acquired lock by deleting the blob"""
    if not blob_name:
        return

    if blob_name == "dummy_lock":
        logger.info("Skipping release of dummy lock")
        return

    try:
        if not bsc.is_available():
            logger.warning("Blob Storage non configurato, skipping lock release")
            return

        try:
            blob_client = bsc.get_blob_client(blob_name)
        except Exception as e:
            logger.error(f"Failed to get blob client: {str(e)}")
            return

        # Check if the blob exists before trying to delete it
        try:
            blob_client.get_blob_properties()
        except:
            logger.warning(f"Lock {blob_name} does not exist, nothing to release")
            return

        # Delete the lock blob
        try:
            blob_client.delete_blob()
            logger.info(f"Released lock: {blob_name}")
        except Exception as delete_error:
            logger.error(f"Failed to delete lock blob: {str(delete_error)}")

    except Exception as e:
        logger.error(f"Error releasing lock: {str(e)}")


def get_current_timestamp():
    """Return current UTC time as ISO string for consistent time tracking"""
    return datetime.utcnow().isoformat()


def calculate_seconds_between(start_iso, end_iso):
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


# Add Application Insights custom tracking methods
def track_operation_start(operation_name, properties=None):
    """
    Track the start of an operation in Application Insights.
    Returns a timestamp that can be used with track_operation_end.
    """
    try:
        from opentelemetry import trace

        properties = properties or {}
        properties["operation_start"] = get_current_timestamp()

        # Get tracer and create span
        tracer = trace.get_tracer(__name__)
        span = tracer.start_span(operation_name)

        # Add custom properties to span
        for key, value in properties.items():
            span.set_attribute(key, value)

        # Store span in context
        trace.use_span(span, end_on_exit=False)

        logger.info(f"Started operation tracking: {operation_name}")
        return properties["operation_start"], span
    except ImportError:
        logger.warning("OpenTelemetry not available for operation tracking")
        return get_current_timestamp(), None
    except Exception as e:
        logger.warning(f"Failed to track operation start: {str(e)}")
        return get_current_timestamp(), None


def track_operation_end(
    operation_name, start_time, span=None, properties=None, success=True, metrics=None
):
    """
    Track the end of an operation in Application Insights.
    Requires the timestamp returned from track_operation_start.
    """
    try:
        end_time = get_current_timestamp()
        duration = calculate_seconds_between(start_time, end_time)

        properties = properties or {}
        properties["operation_end"] = end_time
        properties["duration_seconds"] = duration
        properties["success"] = success

        metrics = metrics or {}
        metrics["duration_seconds"] = duration

        if span:
            # Add end properties to span
            for key, value in properties.items():
                span.set_attribute(key, value)

            # Add metrics to span
            for key, value in metrics.items():
                if isinstance(value, (int, float)):
                    span.set_attribute(key, value)

            # End the span
            span.end()

        logger.info(
            f"Completed operation tracking: {operation_name}, duration: {duration:.2f}s, success: {success}"
        )
        return duration
    except Exception as e:
        logger.warning(f"Failed to track operation end: {str(e)}")
        return calculate_seconds_between(start_time, get_current_timestamp())


def ensure_sum_blob() -> BlobClient:
    """
    Torna il BlobClient per conf/sum.json.
    Se il file non esiste, lo crea con valori di default.
    """
    if not bsc.is_available():
        raise ValueError("Blob Storage non configurato (account/container)")
    logger.info("ensure_sum_blob: Inizio controllo blob conf/sum.json.")
    blob_client = _blob_client(bsc.path_conf(SUM_BLOB_FILENAME))
    try:
        logger.debug("ensure_sum_blob: Provo a scaricare il blob per verificarne l'esistenza.")
        blob_client.download_blob().readall()
        logger.info("ensure_sum_blob: sum.json esiste già. Nessuna creazione necessaria.")
    except ResourceNotFoundError:
        logger.warning(
            f"ensure_sum_blob: {SUM_BLOB_FILENAME} non trovato in conf/. Creo con valori iniziali."
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
        logger.debug(f"ensure_sum_blob: Corpo di default generato: {default_body}")
        blob_client.upload_blob(json.dumps(default_body), overwrite=True)
        logger.info("ensure_sum_blob: Blob sum.json creato con valori di default.")
    return blob_client


def load_sum_data() -> Dict[str, float]:
    """
    Legge sum.json da 'conf' restituendo dizionario completo dei dati.
    Se CONNECTION_STRING manca, ritorna default per evitare rstrip su None.
    """
    if not bsc.is_available():
        logger.warning("load_sum_data: Blob Storage non configurato, uso default")
        return {
            "sum_time_extraction": 0.0,
            "sum_tokens_extraction": 0,
            "time_data_extraction": {"operations": [], "total_operations": 0},
            "sum_time_comparison": 0.0,
            "sum_tokens_comparison": 0,
            "time_data_comparison": {"operations": [], "total_operations": 0},
        }
    logger.info("load_sum_data: Lettura dei dati da sum.json.")
    blob = ensure_sum_blob()
    raw = blob.download_blob().readall()
    logger.debug(f"load_sum_data: Contenuto raw del blob: {raw}")
    data = json.loads(raw)
    logger.info("load_sum_data: Dati caricati con successo.")
    return data


def update_sum_data(
    elapsed_time: float,
    token_count: int,
    mode: str,
    start_timestamp: str = None,
    end_timestamp: str = None,
) -> Dict[str, float]:
    """
    Updates sum data with operation metrics and optional timestamp-based duration.
    """
    logger.info(f"update_sum_data: Inizio aggiornamento per mode='{mode}' con elapsed_time={elapsed_time}, token_count={token_count}.")
    assert mode in (
        "extraction",
        "comparison",
    ), "mode deve essere 'extraction' o 'comparison'"

    final_elapsed_time = elapsed_time
    if start_timestamp and end_timestamp:
        logger.debug(f"update_sum_data: Calcolo durata da timestamp '{start_timestamp}' -> '{end_timestamp}'.")
        calculated_elapsed = calculate_seconds_between(start_timestamp, end_timestamp)
        if calculated_elapsed > 0:
            logger.info(
                f"update_sum_data: Uso durata calcolata {calculated_elapsed:.2f}s invece del reporter {elapsed_time:.2f}s."
            )
            final_elapsed_time = calculated_elapsed
        else:
            logger.warning(
                f"update_sum_data: Durata calcolata non valida ({calculated_elapsed:.2f}s). Mantengo reported {elapsed_time:.2f}s."
            )

    if not bsc.is_available():
        logger.warning("update_sum_data: Blob Storage non configurato, skip aggiornamento blob")
        return {}
    blob = ensure_sum_blob()
    data = json.loads(blob.download_blob().readall())
    logger.debug(f"update_sum_data: Dati correnti caricati: {data}")

    MIN_VALID_ELAPSED_TIME = 10.0
    if final_elapsed_time < MIN_VALID_ELAPSED_TIME or token_count <= 0:
        logger.warning(
            f"update_sum_data: Operazione SKIPPED per mode='{mode}' (elapsed_time={final_elapsed_time:.2f}s < {MIN_VALID_ELAPSED_TIME}s o token_count={token_count} <=0)."
        )
        data["last_updated"] = get_current_timestamp()
        logger.debug(f"update_sum_data: Aggiornato solo 'last_updated': {data['last_updated']}")
        return data

    logger.info(
        f"update_sum_data: Operazione valida, procedo con aggiornamento sums e storico per mode='{mode}'."
    )
    time_data_key = f"time_data_{mode}"
    if time_data_key not in data:
        data[time_data_key] = {"operations": [], "total_operations": 0}
        logger.debug(f"update_sum_data: Creata nuova chiave '{time_data_key}' nel dizionario.")

    operation_data = {
        "token_count": token_count,
        "elapsed_time": final_elapsed_time,
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "timestamp": get_current_timestamp(),
    }
    data[time_data_key]["operations"].append(operation_data)
    logger.debug(f"update_sum_data: Aggiunta operation_data nello storico: {operation_data}")

    if len(data[time_data_key]["operations"]) > MAX_HISTORY_ENTRIES:
        logger.info(
            f"update_sum_data: Superato MAX_HISTORY_ENTRIES ({MAX_HISTORY_ENTRIES}), rimuovo voci più vecchie."
        )
        data[time_data_key]["operations"] = data[time_data_key]["operations"][
            -MAX_HISTORY_ENTRIES:
        ]

    data[time_data_key]["total_operations"] += 1
    logger.debug(f"update_sum_data: total_operations incrementato a {data[time_data_key]['total_operations']}")

    data[f"sum_time_{mode}"] += final_elapsed_time
    data[f"sum_tokens_{mode}"] += token_count
    data["last_updated"] = get_current_timestamp()
    logger.debug(
        f"update_sum_data: Nuovi totali sum_time_{mode}={data[f'sum_time_{mode}']}, sum_tokens_{mode}={data[f'sum_tokens_{mode}']}"
    )

    blob.upload_blob(json.dumps(data), overwrite=True)
    logger.info("update_sum_data: Blob aggiornato con nuovi dati.")
    return data


# Login endpoint (rimane HTTP triggered perché è una operazione veloce)
@app.route(route="login", methods=["POST"])
async def login(req: func.HttpRequest) -> func.HttpResponse:
    try:
        data = req.get_json()
        username = data.get("username")
        password = data.get("password")

        if username == "username" and password == "password2":
            response_body = {"message": "Login successful", "status": "success"}
            return func.HttpResponse(
                body=json.dumps(response_body),
                status_code=200,
                mimetype="application/json",
            )
        else:
            response_body = {
                "message": "Invalid username or password",
                "status": "fail",
            }
            return func.HttpResponse(
                body=json.dumps(response_body),
                status_code=401,
                mimetype="application/json",
            )
    except Exception as e:
        error_body = {"error": f"An error occurred: {str(e)}"}
        return func.HttpResponse(
            body=json.dumps(error_body), status_code=500, mimetype="application/json"
        )


def _run_upload_job(job_id: str, input_data: dict):
    try:
        set_running(job_id)
        res = upload_to_blob(input_data)
        set_completed(job_id, res)
    except Exception as e:
        logger.error(f"Upload job error: {str(e)}")
        set_failed(job_id, str(e))


# Upload Client Function
@app.route(route="upload", methods=["POST"])
def upload_client(req: func.HttpRequest) -> func.HttpResponse:
    try:
        file = req.files.get("file")
        external = req.form.get("external")

        if not external:
            return func.HttpResponse(
                "No external param provided in the request.", status_code=400
            )
        if not file:
            return func.HttpResponse(
                "No file was provided in the request.", status_code=400
            )

        content = file.stream.read()
        input_data = {
            "filename": file.filename,
            "file_content": base64.b64encode(content).decode("utf-8"),
            "is_external": external in ["1", "true", "True"],
        }

        job_id = create_job("upload", "Pending")
        _job_executor.submit(_run_upload_job, job_id, input_data)

        body = {
            "id": job_id,
            "statusQueryGetUri": f"/api/job/{job_id}",
            "status": "success",
            "data": {"filename": file.filename},
        }
        return func.HttpResponse(
            body=json.dumps(body),
            status_code=202,
            mimetype="application/json",
        )

    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )

# @app.route(route="upload", methods=['POST'])
# def upload(req: func.HttpRequest) -> func.HttpResponse:
#     logging.info('Python HTTP trigger function processed a request.')

#     try:
#         # Extract file from request
#         # print("req files", dir(req.form), req.form.get("external"))
#         file = req.files.get('file')
#         external = req.form.get("external")
#         logging.info(f"req.files: {req.files}")
#         logging.info(f"req.form: {req.form}")
#         is_external = True if external == "1" or external == "true" or external == "True" else False
#         if not external:
#             return func.HttpResponse(
#                 "No external param provided in the request.",
#                 status_code=400
#             )
#         if not file:
#             return func.HttpResponse(
#                 "No file was provided in the request.",
#                 status_code=400
#             )

#         logging.info("[DURABLE] Sto per eseguire fileread")
#         fileread = file.stream.read()
#         logging.info(f"{fileread}")

#         logging.info("[DURABLE] Sto per eseguire la gestione del blob")
#         blob_service_client = BlobServiceClient.from_connection_string(CONNECTION_STRING)
#         logging.info(f"{blob_service_client}")
#         if is_external:
#             logging.info("is external")
#             blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME_EXT, blob=f"{file.filename}")#UI/2024-10-31_165333_UTC/DocumentiStrategici/{file.filename}")
#         else:
#             logging.info("is internal")
#             blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=f"{file.filename}")#UI/2024-10-31_165333_UTC/DocumentiStrategici/{file.filename}")
#         logging.info(f"{blob_client}")
#         blob_client.upload_blob(fileread, overwrite=True)

#         logging.info(msg = 'Returning OK 200')
#         return func.HttpResponse(
#             json.dumps({"status": "success"
#                         , "data": [
#                             {
#                              "filename": file.filename
#                              }
#                              ]
#                         }),
#             status_code=200,
#             mimetype="application/json"
#         )

#     except Exception as e:
#         logging.error(f"An error occurred: {str(e)}")
#         print("ERRORE:", e)
#         return func.HttpResponse(
#             json.dumps({"error": str(e)}),
#             status_code=500,
#             mimetype="application/json"
#         )


# Extract Requirements Client Function
@app.route(route="extract_requirements", methods=["POST"])
def extract_requirements_client(req: func.HttpRequest) -> func.HttpResponse:
    mode = "extraction"
    operation_properties = {
        "operation_mode": mode,
        "client_request_id": req.headers.get("x-ms-client-request-id", ""),
        "function_name": "extract_requirements_client",
    }
    start_time_iso, span = track_operation_start(
        "extract_requirements", properties=operation_properties
        )
    start_time_ts = time.time() 

    try:
        logger.info("====================extract_requirements====================")
        file = req.files.get("file")
        external = req.form.get("external")

        if not external:
            track_operation_end("extract_requirements", start_time_iso, span, success=False, properties={"error": "No external param provided"})
            return func.HttpResponse("No external param provided in the request.", status_code=400)

        if not file:
            track_operation_end("extract_requirements", start_time_iso, span, success=False, properties={"error": "No file provided"})
            return func.HttpResponse("No file was provided in the request.", status_code=400)

        content = file.stream.read()
        file_size = len(content) # aggiunta grandezza del file
        text = extract_text_from_pdf(BytesIO(content)) or ""
        token_count = len(get_tokens(text))
        logger.info(f"Token count: {token_count}, File size: {file_size} bytes")

        # calcolo complessità del contenuto in base ad una regex
        def calculate_content_complexity(text):
            num_sentences = text.count('.') + text.count('\n')
            complex_terms = len(re.findall(r'\b[A-Z]{2,}\b|\d{4,}', text))
            return complex_terms / max(num_sentences, 1)

        complexity_score = calculate_content_complexity(text)
        logger.info(f"Content complexity score: {complexity_score:.4f}")

        if span:
            span.set_attribute("token_count", token_count)
            span.set_attribute("filename", file.filename)
            span.set_attribute("is_external", external in ["1", "true", "True"])
            # aggiunta grandezza del file e complessità del contenuto
            span.set_attribute("file_size", file_size)
            span.set_attribute("content_complexity", complexity_score)

        sums = load_sum_data()
        time_data_key = f"time_data_{mode}"
        avg_time_per_token = 1 / DEFAULT_TOKENS_PER_SECOND
        valid_operations = []
        has_timestamp_data = False

        if time_data_key in sums and sums[time_data_key]["operations"]:
            valid_operations = [
                op for op in sums[time_data_key]["operations"]
                if op["token_count"] > 0
                and 10.0 < calculate_seconds_between(op["start_timestamp"], op["end_timestamp"]) < 1800
                and (calculate_seconds_between(op["start_timestamp"], op["end_timestamp"]) / op["token_count"]) < 1.0
            ]
            logger.info(f"{len(valid_operations)} operazioni valide per stima basata su timestamp.")

            if len(valid_operations) >= 10:
                X = np.array([[op["token_count"]] for op in valid_operations])
                y = np.array([
                    calculate_seconds_between(op["start_timestamp"], op["end_timestamp"])
                    for op in valid_operations
                ])
                model = LinearRegression().fit(X, y)
                base_estimate = float(model.predict([[token_count]])[0])
                avg_time_per_token = base_estimate / token_count
                has_timestamp_data = True
                logger.info("Using linear regression for base estimate.")
                logger.info(f"Base estimate (regressione lineare): {base_estimate:.2f}s, avg_time_per_token: {avg_time_per_token:.4f}")
            else:
                times_per_token = [
                    calculate_seconds_between(op["start_timestamp"], op["end_timestamp"]) / op["token_count"]
                    for op in valid_operations
                ]
                truncated_mean = calculate_truncated_mean(times_per_token, 0.1)
                if truncated_mean:
                    avg_time_per_token = truncated_mean
                    has_timestamp_data = True
                    logger.info(f"Using truncated mean (10%): {avg_time_per_token:.4f}s per token from {len(valid_operations)} operations")
                else:
                    total_tokens = sum(op["token_count"] for op in valid_operations)
                    total_time = sum(
                        calculate_seconds_between(op["start_timestamp"], op["end_timestamp"])
                        for op in valid_operations
                    )
                    if total_tokens > 0 and total_time > 0:
                        avg_time_per_token = total_time / total_tokens
                        has_timestamp_data = True
                        logger.info(f"Using timestamp-based average: {avg_time_per_token:.4f}s per token")

        if avg_time_per_token <= 0 and sums.get(f"sum_tokens_{mode}", 0) > 0:
            avg_time_per_token = sums[f"sum_time_{mode}"] / sums[f"sum_tokens_{mode}"]
            logger.info(f"Using sum-based average: {avg_time_per_token:.4f}s per token")

        # Penalità per PDF sbilanciati
        size_penalty = 0
        if token_count > 0 and file_size / token_count > 200:
            size_penalty = 30
            logger.info("High size/token ratio detected, applying size penalty of 30s.")

        # base_estimate = avg_time_per_token * token_count
        # estimated_time = max(
        #     random.randint(60, 100),
        #     round(base_estimate * multiplier + size_penalty, 2)
        # )

        # logger.info(f"Final estimated time: {estimated_time:.2f}s (base: {base_estimate:.2f}, penalty: {size_penalty}, multiplier: {multiplier})")


        regression_estimate = 0  # fallback default, anche in caso di errore
        
        # Calcolo stima tramite modello log-transform
        try:
            base_estimate = avg_time_per_token * token_count
            regression_estimate = predict_time_log_model_complete(
                file_content=content,
                estimated_server_time=base_estimate,
                chunker_fn=split_into_chunks,
                extract_text_fn=extract_text_from_pdf,
            )

            # Calcola il moltiplicatore solo se il modello regressivo non viene usato
            if regression_estimate <= 0:
                if token_count > 15000:
                    multiplier = 1.5
                elif token_count > 10000:
                    multiplier = 1.3
                elif token_count < 2000:
                    multiplier = 1.1
                else:
                    multiplier = 1.2
                logger.info(f"Dynamic multiplier based on token count: {multiplier}")
            else:
                multiplier = 1.0  # inutile se stiamo usando il modello regressivo

            logger.info(f"[Stima modello log] Tempo stimato: {regression_estimate:.2f}s (base_estimate: {base_estimate:.2f})")
        except Exception as model_error:
            regression_estimate = 0
            logger.warning(f"[Stima modello log] Errore nella previsione: {str(model_error)}")

        # Penalità dimensione e moltiplicatore
        size_penalty = 30 if token_count > 0 and file_size / token_count > 200 else 0
        if token_count > 15000:
            multiplier = 1.5
        elif token_count > 10000:
            multiplier = 1.3
        elif token_count < 2000:
            multiplier = 1.1
        else:
            multiplier = 1.2

        # Tempo stimato finale (uso fallback se il modello fallisce)
        base_estimate = avg_time_per_token * token_count
        estimated_time = round(
            (regression_estimate if regression_estimate > 0 else base_estimate * multiplier) + size_penalty,
            2
        )

        logger.info(f"[Stima finale] Stimato: {estimated_time:.2f}s | Metodo: {'modello log' if regression_estimate > 0 else 'euristico'} | Penalità: {size_penalty}")

        if span:
            span.set_attribute("estimated_time", estimated_time)
            span.set_attribute("avg_time_per_token", avg_time_per_token)
            span.set_attribute("calculation_method", "timestamp_based" if has_timestamp_data else "sum_based")
            span.set_attribute("size_penalty", size_penalty)
            span.set_attribute("dynamic_multiplier", multiplier)

        orchestrator_input = {
            "filename": file.filename,
            "file_content": base64.b64encode(content).decode("utf-8"),
            "is_external": external in ["1", "true", "True"],
        }

        job_id = create_job("extract_requirements", "Pending")
        _job_executor.submit(_run_extract_requirements_job, job_id, orchestrator_input)
        logger.info(f"Started job with ID = '{job_id}'.")

        recent_operations = sums.get(time_data_key, {}).get("operations", [])[-5:]

        body = {
            "id": job_id,
            "statusQueryGetUri": f"/api/job/{job_id}",
            "token_count": token_count,
            "sum_time": round(sums.get(f"sum_time_{mode}", 0), 2),
            "sum_tokens": sums.get(f"sum_tokens_{mode}", 0),
            "estimated_time": estimated_time,
            "status": "running",
            "start_time": start_time_iso,
            "avg_time_per_token": avg_time_per_token,
            "time_estimation_method": "timestamp_based" if has_timestamp_data else "sum_based",
            "total_operations": sums.get(time_data_key, {}).get("total_operations", 0),
            "recent_operations": recent_operations,
            "complexity_score": complexity_score,
            "size_penalty": size_penalty,
            "file_size": file_size,
        }

        track_operation_end(
            "extract_requirements",
            start_time_iso,
            span,
            success=True,
            properties={"job_id": job_id, "filename": file.filename},
            metrics={"token_count": token_count, "estimated_time": estimated_time},
        )

        return func.HttpResponse(
            body=json.dumps(body),
            status_code=202,
            mimetype="application/json",
        )

    except Exception as e:
        logger.error(f"Error in extract_requirements_client: {str(e)}")
        error_properties = {"error": str(e), "error_type": type(e).__name__}
        track_operation_end("extract_requirements", start_time_iso, span, success=False, properties=error_properties)

        error_response = {
            "error": str(e),
            "status": "error",
            "statusCode": 500,
            "timestamp": get_current_timestamp(),
        }
        return func.HttpResponse(json.dumps(error_response), status_code=500, mimetype="application/json")


# Compare Requirements Client Function
@app.route(route="compare_requirements", methods=["POST"])
def compare_requirements_client(req: func.HttpRequest) -> func.HttpResponse:
    mode = "comparison"
    operation_properties = {
        "operation_mode": mode,
        "client_request_id": req.headers.get("x-ms-client-request-id", ""),
        "function_name": "compare_requirements_client",
        "content_type": req.headers.get("content-type", ""),
    }
    start_time, span = track_operation_start(
        "compare_requirements", properties=operation_properties
    )

    try:
        # Log request details for debugging
        content_type = req.headers.get("content-type", "")
        logger.info(f"Request content type: {content_type}")
        logger.info(f"Request body exists: {req.get_body() is not None}")

        # Check if filenames are provided in the request
        filenames_provided = False
        file1_name = None
        file2_name = None
        file1IsExternal = None
        file2IsExternal = None
        comparisonMode = None

        # First, try to parse as form data (multipart/form-data)
        if "multipart/form-data" in content_type:
            logger.info("Processing request as multipart/form-data")
            logger.info(f"[PAYLOAD]: {req.form}")
            # Try to get file names from form data
            form_file1_name = req.form.get("file1Name")
            form_file2_name = req.form.get("file2Name")
            comparisonMode = req.form.get("comparisonMode")

            # logger.info(f"[FOTTUTO FORM]: {comparisonMode}")

            if form_file1_name and form_file2_name:
                filenames_provided = True
                file1_name = form_file1_name
                file2_name = form_file2_name
                file1IsExternal = req.form.get("file1IsExternal", "1")
                file2IsExternal = req.form.get("file2IsExternal", "1")
                comparisonMode = req.form.get("comparisonMode")
                logger.info(
                    f"Found filenames in form data: {file1_name} and {file2_name}. Comparison mode: {comparisonMode}"
                )
            # Check if files are being uploaded directly
            elif req.files and "file1" in req.files and "file2" in req.files:
                logger.info("Using uploaded files from form data")
                file1 = req.files.get("file1")
                file2 = req.files.get("file2")
                file1IsExternal = req.form.get("file1IsExternal", "1")
                file2IsExternal = req.form.get("file2IsExternal", "1")
                comparisonMode = req.form.get("comparisonMode")

                if file1 and file2:
                    file1_content = file1.stream.read()
                    file2_content = file2.stream.read()
                    file1_name = file1.filename
                    file2_name = file2.filename
        # If not form data, try JSON body
        elif req.get_body():
            logger.info("Trying to parse as JSON body")
            try:
                req_body = req.get_json()
                logger.info(f"Parsed JSON body: {req_body}")

                if req_body and "file1Name" in req_body and "file2Name" in req_body:
                    filenames_provided = True
                    file1_name = req_body.get("file1Name")
                    file2_name = req_body.get("file2Name")
                    file1IsExternal = req_body.get("file1IsExternal", True)
                    file2IsExternal = req_body.get("file2IsExternal", True)
                    comparisonMode = req_body.get("comparisonMode")
                    logger.info(
                        f"Found filenames in JSON: {file1_name} and {file2_name}"
                    )
            except Exception as json_err:
                logger.error(f"Error parsing JSON: {str(json_err)}")
                # Try to read raw body for debugging
                try:
                    raw_body = req.get_body().decode("utf-8")
                    logger.info(f"Raw request body: {raw_body}")
                except:
                    pass
                # Try with form data as a fallback
                logger.info("JSON parsing failed, trying form data fallback")
                if req.form:
                    form_file1_name = req.form.get("file1Name")
                    form_file2_name = req.form.get("file2Name")
                    if form_file1_name and form_file2_name:
                        filenames_provided = True
                        file1_name = form_file1_name
                        file2_name = form_file2_name
                        file1IsExternal = req.form.get("file1IsExternal", "1")
                        file2IsExternal = req.form.get("file2IsExternal", "1")
                        comparisonMode = req.form.get("comparisonMode")
                        logger.info(
                            f"Found filenames in form data (fallback): {file1_name} and {file2_name}"
                        )

        # Validation
        if not filenames_provided and not (
            "file1_content" in locals() and "file2_content" in locals()
        ):
            error_msg = "Missing required parameters. Please provide either filenames or files in the request."
            if span:
                span.set_attribute("error", error_msg)
            track_operation_end(
                "compare_requirements",
                start_time,
                span,
                success=False,
                properties={"error": error_msg},
            )

            logger.error("No valid input data found in request")
            return func.HttpResponse(
                error_msg,
                status_code=400,
            )

        # Update span with request details
        if span:
            span.set_attribute("file1_name", file1_name)
            span.set_attribute("file2_name", file2_name)
            span.set_attribute("filenames_provided", filenames_provided)
            span.set_attribute("comparisonMode", str(comparisonMode))

        # Normalize boolean values
        if isinstance(file1IsExternal, str):
            file1IsExternal = file1IsExternal.lower() in ["1", "true", "yes"]
        elif not isinstance(file1IsExternal, bool):
            file1IsExternal = True  # Default to external

        if isinstance(file2IsExternal, str):
            file2IsExternal = file2IsExternal.lower() in ["1", "true", "yes"]
        elif not isinstance(file2IsExternal, bool):
            file2IsExternal = True  # Default to external

        logger.info(
            f"Processing request. Filenames: {file1_name}, {file2_name}. External: {file1IsExternal}, {file2IsExternal}. Comparison mode: {comparisonMode}"
        )

        # If we have filenames but no content yet, retrieve from blob storage
        if filenames_provided and not (
            "file1_content" in locals() and "file2_content" in locals()
        ):
            if not bsc.is_available():
                error_msg = "Blob Storage non configurato"
                return func.HttpResponse(
                    json.dumps({"error": error_msg}),
                    status_code=500,
                    mimetype="application/json",
                )

            # Get the first file
            path1 = bsc.path_pdf(file1_name)
            try:
                logger.info(f"Attempting to download file {file1_name} from {path1}")
                blob_client1 = bsc.get_blob_client(path1)
                # Check if blob exists
                if not blob_client1.exists():
                    error_msg = f"File {file1_name} not found in blob storage."
                    if span:
                        span.set_attribute("error", error_msg)
                    track_operation_end(
                        "compare_requirements",
                        start_time,
                        span,
                        success=False,
                        properties={"error": error_msg, "error_type": "BlobNotFound"},
                    )

                    logger.error(f"Blob {file1_name} does not exist at {path1}")
                    return func.HttpResponse(
                        json.dumps({"error": error_msg}),
                        status_code=404,
                        mimetype="application/json",
                    )

                # Download the file
                file1_download = blob_client1.download_blob()
                file1_content = file1_download.readall()
                logger.info(
                    f"Successfully downloaded {len(file1_content)} bytes for file {file1_name}"
                )
            except ResourceNotFoundError:
                error_msg = f"File {file1_name} not found in blob storage"
                if span:
                    span.set_attribute("error", error_msg)
                track_operation_end(
                    "compare_requirements",
                    start_time,
                    span,
                    success=False,
                    properties={
                        "error": error_msg,
                        "error_type": "ResourceNotFoundError",
                    },
                )

                logger.error(f"ResourceNotFoundError: {error_msg}")
                return func.HttpResponse(
                    json.dumps({"error": error_msg}),
                    status_code=404,
                    mimetype="application/json",
                )

            # Get the second file
            path2 = bsc.path_pdf(file2_name)
            try:
                logger.info(f"Attempting to download file {file2_name} from {path2}")
                blob_client2 = bsc.get_blob_client(path2)
                # Check if blob exists
                if not blob_client2.exists():
                    error_msg = f"File {file2_name} not found in blob storage."
                    if span:
                        span.set_attribute("error", error_msg)
                    track_operation_end(
                        "compare_requirements",
                        start_time,
                        span,
                        success=False,
                        properties={"error": error_msg, "error_type": "BlobNotFound"},
                    )

                    logger.error(f"Blob {file2_name} does not exist at {path2}")
                    return func.HttpResponse(
                        json.dumps({"error": error_msg}),
                        status_code=404,
                        mimetype="application/json",
                    )

                # Download the file
                file2_download = blob_client2.download_blob()
                file2_content = file2_download.readall()
                logger.info(
                    f"Successfully downloaded {len(file2_content)} bytes for file {file2_name}"
                )
            except ResourceNotFoundError:
                error_msg = f"File {file2_name} not found in blob storage"
                if span:
                    span.set_attribute("error", error_msg)
                logger.error(f"ResourceNotFoundError: {error_msg}")
                return func.HttpResponse(
                    json.dumps(
                        {"error": f"File {file2_name} not found in blob storage."}
                    ),
                    status_code=404,
                    mimetype="application/json",
                )
            except Exception as e:
                logger.error(f"Error downloading file {file2_name}: {str(e)}")
                return func.HttpResponse(
                    json.dumps(
                        {"error": f"Error downloading file {file2_name}: {str(e)}"}
                    ),
                    status_code=500,
                    mimetype="application/json",
                )

        # Check file content for both flows
        if not file1_content or not file2_content:
            return func.HttpResponse("One or both files are empty.", status_code=400)

        # Extract text and count tokens for both flows
        try:
            # Make sure we have proper PDF files before attempting extraction
            # Validate file1_content
            if len(file1_content) > 0:
                # Check if it's a valid PDF (has %PDF signature)
                if not file1_content.startswith(b"%PDF"):
                    logger.warning(
                        f"File {file1_name} doesn't start with PDF signature - may be base64 encoded"
                    )
                    # Try to decode if it might be base64 encoded
                    try:
                        maybe_decoded = base64.b64decode(file1_content)
                        if maybe_decoded.startswith(b"%PDF"):
                            logger.info("Successfully decoded file1 from base64")
                            file1_content = maybe_decoded
                    except Exception as decode_err:
                        logger.warning(
                            f"Failed to decode potential base64 content: {decode_err}"
                        )

            # Validate file2_content
            if len(file2_content) > 0:
                # Check if it's a valid PDF (has %PDF signature)
                if not file2_content.startswith(b"%PDF"):
                    logger.warning(
                        f"File {file2_name} doesn't start with PDF signature - may be base64 encoded"
                    )
                    # Try to decode if it might be base64 encoded
                    try:
                        maybe_decoded = base64.b64decode(file2_content)
                        if maybe_decoded.startswith(b"%PDF"):
                            logger.info("Successfully decoded file2 from base64")
                            file2_content = maybe_decoded
                    except Exception as decode_err:
                        logger.warning(
                            f"Failed to decode potential base64 content: {decode_err}"
                        )

            # Now extract text from the validated PDFs
            text1 = extract_text_from_pdf(BytesIO(file1_content))
            text2 = extract_text_from_pdf(BytesIO(file2_content))

            # If either extraction returned empty string, log error
            if not text1:
                logger.error(f"Failed to extract text from file1: {file1_name}")
            if not text2:
                logger.error(f"Failed to extract text from file2: {file2_name}")

            # Continue with token counting even if extraction might have had issues
            token_count = len(get_tokens(text1 or "")) + len(get_tokens(text2 or ""))
            logger.info(f"Token count calcolato in client: {token_count}")

            # Track token count in span
            if span:
                span.set_attribute("token_count", token_count)
                span.set_attribute("text1_length", len(text1 or ""))
                span.set_attribute("text2_length", len(text2 or ""))
        except Exception as e:
            error_msg = f"Error processing PDF files: {str(e)}"
            if span:
                span.set_attribute("error", error_msg)
            track_operation_end(
                "compare_requirements",
                start_time,
                span,
                success=False,
                properties={"error": error_msg, "error_type": type(e).__name__},
            )

            logger.error(error_msg)
            return func.HttpResponse(
                json.dumps(
                    {
                        "error": error_msg,
                        "status": "error",
                        "statusCode": 400,
                        "timestamp": get_current_timestamp(),
                    }
                ),
                status_code=400,
                mimetype="application/json",
            )

        # carica i totali correnti e calcola la stima
        sums = load_sum_data()

        # Calculate average time per token using timestamp-based history if available
        time_data_key = f"time_data_{mode}"
        avg_time_per_token = 1 / DEFAULT_TOKENS_PER_SECOND  # Default fallback
        valid_operations = []  # Initialize valid_operations to empty list by default
        has_timestamp_data = False

        if time_data_key in sums and sums[time_data_key]["operations"]:
            # Filtrare i record con elapsed time inferiore a 10 secondi
            operations_before = len(sums[time_data_key]["operations"])

            # Filtriamo le operazioni conservando solo quelle con un tempo valido (> 10 secondi)
            filtered_operations = []
            for op in sums[time_data_key]["operations"]:
                elapsed = calculate_seconds_between(
                    op["start_timestamp"], op["end_timestamp"]
                )
                if (
                    op["token_count"] > 0 and elapsed >= 10.0
                ):  # Aumentata la soglia da 1.0 a 10.0 secondi
                    filtered_operations.append(op)
                else:
                    logger.warning(
                        f"Removed invalid operation record with elapsed time: {elapsed:.2f}s, tokens: {op['token_count']}"
                    )

            # Sostituisci le operazioni con quelle filtrate e SALVA I CAMBIAMENTI
            if len(filtered_operations) < operations_before:
                # Aggiorna lo storico in sums
                sums[time_data_key]["operations"] = filtered_operations
                sums[time_data_key]["total_operations"] = len(filtered_operations)

                # Salva direttamente lo storico aggiornato nel blob storage
                blob = ensure_sum_blob()
                blob.upload_blob(json.dumps(sums), overwrite=True)

                logger.info(
                    f"Removed {operations_before - len(filtered_operations)} operations with elapsed time < 10s and saved updated history"
                )

            valid_operations = filtered_operations

            if valid_operations:
                has_timestamp_data = True

                # Calcola i tempi per token per ogni operazione
                times_per_token = [
                    calculate_seconds_between(
                        op["start_timestamp"], op["end_timestamp"]
                    )
                    / op["token_count"]
                    for op in valid_operations
                ]

                # Calcola la media troncata (rimuove il 10% dei valori più alti e più bassi)
                truncated_mean = calculate_truncated_mean(times_per_token, 0.1)

                if truncated_mean is not None:
                    # Usa la media troncata
                    avg_time_per_token = truncated_mean
                    logger.info(
                        f"Using truncated mean (10%): {avg_time_per_token:.4f}s per token from {len(valid_operations)} operations"
                    )
                else:
                    # Fallback alla media normale se non abbiamo abbastanza dati per la media troncata
                    total_tokens = sum(op["token_count"] for op in valid_operations)
                    total_time = sum(
                        calculate_seconds_between(
                            op["start_timestamp"], op["end_timestamp"]
                        )
                        for op in valid_operations
                    )
                    if total_tokens > 0 and total_time > 0:
                        avg_time_per_token = total_time / total_tokens
                        logger.info(
                            f"Using timestamp-based average: {avg_time_per_token:.4f}s per token"
                        )

        # If we don't have enough timestamp data, fall back to sum-based calculation
        if avg_time_per_token <= 0 and sums[f"sum_tokens_{mode}"] > 0:
            avg_time_per_token = sums[f"sum_time_{mode}"] / sums[f"sum_tokens_{mode}"]
            logger.info(f"Using sum-based average: {avg_time_per_token:.4f}s per token")

        # Calcola la stima applicando un moltiplicatore del 20%
        base_estimate = avg_time_per_token * token_count
        multiplier = 1.2  # 20% in più rispetto alla stima base
        min_estimate_seconds = random.randint(
            60, 100
        )  # Set minimum estimate to 60 seconds
        estimated_time = max(min_estimate_seconds, round(base_estimate * multiplier, 2))

        logger.info(
            f"Estimated time for {token_count} tokens: {estimated_time:.2f}s (base: {base_estimate:.2f}s, multiplier: {multiplier})"
        )

        # Track estimation details in span
        if span:
            span.set_attribute("estimated_time", estimated_time)
            span.set_attribute("avg_time_per_token", avg_time_per_token)
            span.set_attribute(
                "calculation_method",
                "timestamp_based" if has_timestamp_data else "sum_based",
            )
            span.set_attribute("base_estimate", base_estimate)
            span.set_attribute("multiplier", multiplier)

        # Create the orchestrator input (same for both flows)
        orchestrator_input = {
            "file1": {
                "filename": file1_name,
                "file_content": base64.b64encode(file1_content).decode("utf-8"),
                "is_external": file1IsExternal,
            },
            "file2": {
                "filename": file2_name,
                "file_content": base64.b64encode(file2_content).decode("utf-8"),
                "is_external": file2IsExternal,
            },
            "comparisonMode": comparisonMode,
        }

        job_id = create_job("compare_requirements", "Pending")
        _job_executor.submit(_run_compare_requirements_job, job_id, orchestrator_input)
        logger.info(f"Started job with ID = '{job_id}'.")

        recent_operations = []
        if time_data_key in sums and sums[time_data_key]["operations"]:
            recent_operations = sums[time_data_key]["operations"][-5:]

        body = {
            "id": job_id,
            "statusQueryGetUri": f"/api/job/{job_id}",
            "token_count": token_count,
            "sum_time": round(sums[f"sum_time_{mode}"], 2),
            "sum_tokens": sums[f"sum_tokens_{mode}"],
            "estimated_time": estimated_time,
            "comparisonMode": comparisonMode,
            "status": "running",
            "start_time": start_time,
            "avg_time_per_token": avg_time_per_token,
            "time_estimation_method": "timestamp_based" if has_timestamp_data else "sum_based",
            "total_operations": sums[time_data_key]["total_operations"] if time_data_key in sums else 0,
            "recent_operations": recent_operations,
            "using_blob_filenames": filenames_provided,
        }

        track_operation_end(
            "compare_requirements",
            start_time,
            span,
            success=True,
            properties={
                "job_id": job_id,
                "file1_name": file1_name,
                "file2_name": file2_name,
                "comparisonMode": str(comparisonMode),
            },
            metrics={"token_count": token_count, "estimated_time": estimated_time},
        )

        return func.HttpResponse(
            body=json.dumps(body),
            status_code=202,
            mimetype="application/json",
        )

    except Exception as e:
        logger.error(f"An error occurred in compare_requirements_client: {str(e)}")
        error_properties = {"error": str(e), "error_type": type(e).__name__}
        track_operation_end(
            "compare_requirements",
            start_time,
            span,
            success=False,
            properties=error_properties,
        )

        error_response = {
            "error": str(e),
            "status": "error",
            "statusCode": 500,
            "timestamp": get_current_timestamp(),
        }
        return func.HttpResponse(
            json.dumps(error_response), status_code=500, mimetype="application/json"
        )


def _run_extract_requirements_job(job_id: str, input_data: dict):
    inputData = input_data
    mode = "extraction"
    lock_file = None
    total_elapsed = 0.0

    try:
        set_running(job_id, {"status": "extract_requirements", "message": "Starting"})
        logger.info("============extract_requirements_job===========")

        file_bytes = base64.b64decode(inputData["file_content"])
        reader = PdfReader(BytesIO(file_bytes))
        text = "".join(p.extract_text() or "" for p in reader.pages)
        token_count = len(get_tokens(text))

        lock_file = create_process_lock(job_id, mode, token_count)
        sums = load_sum_data()
        time_data_key = f"time_data_{mode}"
        avg_time_per_token = 1 / DEFAULT_TOKENS_PER_SECOND

        if time_data_key in sums and sums[time_data_key]["operations"]:
            filtered_operations = [
                op for op in sums[time_data_key]["operations"]
                if op["token_count"] > 0 and calculate_seconds_between(op["start_timestamp"], op["end_timestamp"]) >= 10.0
            ]
            if len(filtered_operations) < len(sums[time_data_key]["operations"]):
                sums[time_data_key]["operations"] = filtered_operations
                sums[time_data_key]["total_operations"] = len(filtered_operations)
                blob = ensure_sum_blob()
                blob.upload_blob(json.dumps(sums), overwrite=True)
            valid_operations = filtered_operations
            if valid_operations:
                total_tokens = sum(op["token_count"] for op in valid_operations)
                total_time = sum(calculate_seconds_between(op["start_timestamp"], op["end_timestamp"]) for op in valid_operations)
                if total_tokens > 0 and total_time > 0:
                    avg_time_per_token = total_time / total_tokens
        if avg_time_per_token <= 0 and sums.get(f"sum_tokens_{mode}", 0) > 0:
            avg_time_per_token = sums[f"sum_time_{mode}"] / sums[f"sum_tokens_{mode}"]

        operation_start_time = get_current_timestamp()
        est_time = round(avg_time_per_token * token_count * 1.2, 2)
        set_running(job_id, {"token_count": token_count, "estimated_time": est_time, "status": "uploading"})

        upload_res = upload_to_blob(inputData)
        upload_time = upload_res.get("upload_elapsed", 0.0)
        set_running(job_id, {"status": "processing", "upload_complete": True, "upload_time": upload_time})

        proc_res = process_requirements(inputData)
        if not proc_res or proc_res.get("status") == "error":
            error_msg = proc_res.get("error", "Unknown error") if proc_res else "Empty response"
            error_details = proc_res.get("details", {}) if proc_res else {}
            if isinstance(error_details, dict) and "timestamp" in error_details:
                set_failed(job_id, json.dumps({"status": "error", "error": error_msg, "status_code": 418}))
            else:
                set_failed(job_id, json.dumps({"status": "error", "error": error_msg}))
            return

        vm_data = proc_res.get("result")
        if vm_data is None:
            set_failed(job_id, "Missing result from VMAI")
            return

        file_name_without_ext = Path(inputData["filename"]).stem
        md5_hash = hashlib.md5(file_name_without_ext.encode("utf-8")).hexdigest()
        result_blob_name = f"requirements/{md5_hash}.json"
        container_name_for_json = CONTAINER_NAME_EXT

        save_res = save_json_to_blob({
            "json_content": json.dumps(vm_data),
            "container_name": container_name_for_json,
            "blob_name": result_blob_name,
        })
        if not save_res or save_res.get("status") == "error":
            set_failed(job_id, save_res.get("error", "Save failed") if save_res else "Save failed")
            return

        processing_time = proc_res.get("process_elapsed", 0.0)
        operation_end_time = get_current_timestamp()
        total_elapsed = calculate_seconds_between(operation_start_time, operation_end_time)

        if lock_file:
            processing_info = get_processing_time_from_lock(lock_file)
            real_elapsed_time = processing_info.get("elapsed_time", 0)
            if real_elapsed_time > 10:
                total_elapsed = real_elapsed_time
            try:
                os.remove(lock_file)
            except Exception:
                pass

        updated = update_sum_data(total_elapsed, token_count, mode=mode, start_timestamp=operation_start_time, end_timestamp=operation_end_time)

        orchestrator_output = {
            "status": "success",
            "vm_result": vm_data,
            "result_blob_name": result_blob_name,
            "container_name": container_name_for_json,
            "timing_info": {
                "total_processing_time_sec": total_elapsed,
                "start_timestamp": operation_start_time,
                "end_timestamp": operation_end_time,
                "upload_time_sec": upload_time,
                "vm_processing_time_sec": processing_time,
                "reported_vm_processing_time_sec": proc_res.get("process_elapsed", 0.0),
            },
            "statistics_info": {
                "updated_sum_time": updated.get(f"sum_time_{mode}"),
                "updated_sum_tokens": updated.get(f"sum_tokens_{mode}"),
            },
        }
        set_completed(job_id, orchestrator_output)

    except Exception as e:
        if lock_file:
            try:
                os.remove(lock_file)
            except Exception:
                pass
        logger.error(f"extract_requirements_job error: {str(e)}")
        set_failed(job_id, str(e))


def _run_compare_requirements_job(job_id: str, files_data: dict):
    mode = "comparison"
    lock_file = None

    try:
        set_running(job_id, {"status": "compare_requirements", "message": "Starting"})
        logger.info("============compare_requirements_job===========")

        try:
            file1_bytes = base64.b64decode(files_data["file1"]["file_content"])
            if not file1_bytes.startswith(b"%PDF"):
                try:
                    maybe_decoded = base64.b64decode(file1_bytes)
                    if maybe_decoded.startswith(b"%PDF"):
                        file1_bytes = maybe_decoded
                except Exception:
                    pass
            file2_bytes = base64.b64decode(files_data["file2"]["file_content"])
            if not file2_bytes.startswith(b"%PDF"):
                try:
                    maybe_decoded = base64.b64decode(file2_bytes)
                    if maybe_decoded.startswith(b"%PDF"):
                        file2_bytes = maybe_decoded
                except Exception:
                    pass
            reader1 = PdfReader(BytesIO(file1_bytes))
            reader2 = PdfReader(BytesIO(file2_bytes))
            text1 = "".join(p.extract_text() or "" for p in reader1.pages)
            text2 = "".join(p.extract_text() or "" for p in reader2.pages)
            token_count = len(get_tokens(text1)) + len(get_tokens(text2))
        except Exception as e:
            set_failed(job_id, f"Error processing PDFs: {str(e)}")
            return

        operation_start_time = get_current_timestamp()
        lock_file = create_process_lock(job_id, mode, token_count)
        sums = load_sum_data()
        time_data_key = f"time_data_{mode}"
        avg_time_per_token = 1 / DEFAULT_TOKENS_PER_SECOND
        if time_data_key in sums and sums[time_data_key]["operations"]:
            valid_operations = [
                op for op in sums[time_data_key]["operations"]
                if op["token_count"] > 0 and calculate_seconds_between(op["start_timestamp"], op["end_timestamp"]) > 10.0
            ]
            if valid_operations:
                total_tokens = sum(op["token_count"] for op in valid_operations)
                total_time = sum(calculate_seconds_between(op["start_timestamp"], op["end_timestamp"]) for op in valid_operations)
                if total_tokens > 0 and total_time > 0:
                    avg_time_per_token = total_time / total_tokens
        if avg_time_per_token <= 0 and sums.get(f"sum_tokens_{mode}", 0) > 0:
            avg_time_per_token = sums[f"sum_time_{mode}"] / sums[f"sum_tokens_{mode}"]

        est_time = round(avg_time_per_token * token_count * 1.2, 2)
        set_running(job_id, {"token_count": token_count, "estimated_time": est_time, "status": "uploading"})

        upload_res1 = upload_to_blob(files_data["file1"])
        upload_time1 = upload_res1.get("upload_elapsed", 0.0)
        upload_res2 = upload_to_blob(files_data["file2"])
        upload_time2 = upload_res2.get("upload_elapsed", 0.0)
        set_running(job_id, {"status": "comparing", "upload_time1": upload_time1, "upload_time2": upload_time2})

        compare_result = compare_requirements(files_data)
        if compare_result.get("status") == "error":
            error_msg = compare_result.get("error", "Unknown error")
            error_details = compare_result.get("details", {})
            if lock_file:
                try:
                    os.remove(lock_file)
                except Exception:
                    pass
            if isinstance(error_details, dict) and "timestamp" in error_details:
                set_failed(job_id, json.dumps({"error": error_msg, "status_code": 418}))
            else:
                set_failed(job_id, error_msg)
            return

        compare_elapsed = compare_result.get("compare_elapsed", 0.0)
        operation_end_time = get_current_timestamp()
        total_elapsed = calculate_seconds_between(operation_start_time, operation_end_time)

        if lock_file:
            processing_info = get_processing_time_from_lock(lock_file)
            real_elapsed_time = processing_info.get("elapsed_time", 0)
            if real_elapsed_time > 10:
                total_elapsed = real_elapsed_time
            try:
                os.remove(lock_file)
            except Exception:
                pass

        result = compare_result.get("result") if isinstance(compare_result, dict) else compare_result
        updated = update_sum_data(total_elapsed, token_count, mode=mode, start_timestamp=operation_start_time, end_timestamp=operation_end_time)

        if isinstance(result, dict):
            result.update({
                "total_processing_time_sec": total_elapsed,
                "updated_sum_time": updated[f"sum_time_{mode}"],
                "updated_sum_tokens": updated[f"sum_tokens_{mode}"],
                "start_timestamp": operation_start_time,
                "end_timestamp": operation_end_time,
            })

        set_completed(job_id, result)

    except Exception as e:
        if lock_file:
            try:
                os.remove(lock_file)
            except Exception:
                pass
        logger.error(f"compare_requirements_job error: {str(e)}")
        set_failed(job_id, str(e))


# Activity functions (now plain functions, no durable trigger)
def upload_to_blob(inputData: dict) -> dict:
    try:
        if not bsc.is_available():
            raise ValueError("Blob Storage non configurato, impossibile upload")
        start = time.time()
        blob_path = bsc.path_pdf(inputData["filename"])
        blob_client = bsc.get_blob_client(blob_path)
        blob_client.upload_blob(inputData["file_content"], overwrite=True)
        elapsed = round(time.time() - start, 2)
        return {
            "status": "success",
            "filename": inputData["filename"],
            "upload_elapsed": elapsed,
        }
    except Exception as e:
        logger.error(f"Blob upload error: {str(e)}")
        raise


def process_requirements(inputData: dict) -> dict:
    try:
        logger.info("============process_requirements===========")

        start = time.time()

        # Updated URL to match the new endpoint
        url = f"http://{IPVMAI}:2025/extract-requirements"

        files = {
            "file": (
                inputData["filename"],
                base64.b64decode(inputData["file_content"]),
                "application/pdf",
            )
        }

        logger.info(
            f"Sending request to {url} with filename: {inputData['filename']} and file size: {len(files['file'][1])} bytes"
        )

        # Use a longer timeout to accommodate the potentially longer processing time
        # First number is connection timeout, second is read timeout (in seconds)
        try:
            response = requests.post(url, files=files, timeout=(60, 2400))
            logger.info(f"Response status: {response.status_code}")
        except requests.exceptions.ConnectTimeout:
            logger.error("VMAI connect timeout – cannot reach host")
            return {
                "status": "error",
                "error": "VMAI connect timeout – cannot reach host",
            }
        except requests.exceptions.ReadTimeout:
            logger.error("VMAI read timeout – processing exceeded 40 min")
            return {
                "status": "error",
                "error": "VMAI read timeout – processing exceeded 40 min",
            }

        response.raise_for_status()
        data = response.json()
        logger.info(f"Response from VMAI: {str(data)[:500]}...")

        elapsed = round(time.time() - start, 2)

        return {"status": "success", "result": data, "process_elapsed": elapsed}
    except requests.exceptions.Timeout:
        logger.error("Request to VMAI timed out")
        return {
            "status": "error",
            "error": "Request timed out when calling VMAI service",
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error: {str(e)}")
        return {
            "status": "error",
            "error": f"Error communicating with VMAI service: {str(e)}",
        }
    except Exception as e:
        logger.error(f"Requirements processing error: {str(e)}")
        return {"status": "error", "error": str(e)}


def compare_requirements(filesData: dict) -> dict:
    try:

        start = time.time()
        url = f"http://{IPVMAI}:2025/compare-requirements"

        params = {}
        if filesData.get("comparisonMode"):
            params["comparisonMode"] = filesData["comparisonMode"]
        # logger.info(f"[FOTTUTI PARAMS]: {params}")
        # logger.info(f"[FOTTUTI FILEDATA]: {filesData}")
        logger.info(f"Comparison mode: {params.get('comparisonMode', 'not set')}")

        # Get file contents and decode base64 if needed
        try:
            file1_content = base64.b64decode(filesData["file1"]["file_content"])
            # Validate PDF content
            if not file1_content.startswith(b"%PDF"):
                logger.warning(
                    "File1 content doesn't start with PDF signature - may be double encoded"
                )
                # Try to decode again in case of double encoding
                try:
                    maybe_decoded = base64.b64decode(file1_content)
                    if maybe_decoded.startswith(b"%PDF"):
                        logger.info(
                            "Successfully decoded file1 from double base64 encoding"
                        )
                        file1_content = maybe_decoded
                except Exception as decode_err:
                    logger.warning(
                        f"Failed to decode potential double encoded content: {decode_err}"
                    )

            file2_content = base64.b64decode(filesData["file2"]["file_content"])
            # Validate PDF content
            if not file2_content.startswith(b"%PDF"):
                logger.warning(
                    "File2 content doesn't start with PDF signature - may be double encoded"
                )
                # Try to decode again in case of double encoding
                try:
                    maybe_decoded = base64.b64decode(file2_content)
                    if maybe_decoded.startswith(b"%PDF"):
                        logger.info(
                            "Successfully decoded file2 from double base64 encoding"
                        )
                        file2_content = maybe_decoded
                except Exception as decode_err:
                    logger.warning(
                        f"Failed to decode potential double encoded content: {decode_err}"
                    )
        except Exception as e:
            logger.error(f"Error decoding base64 file content: {str(e)}")
            return {
                "status": "error",
                "error": f"Error decoding file content: {str(e)}",
            }

        files = {
            "file1": (
                filesData["file1"]["filename"],
                file1_content,
                "application/pdf",
            ),
            "file2": (
                filesData["file2"]["filename"],
                file2_content,
                "application/pdf",
            ),
        }

        logger.info(f"Sending comparison request to {url} with params {params}")
        logger.info(
            f"File sizes: file1={len(files['file1'][1])}, file2={len(files['file2'][1])} bytes"
        )

        # Use a longer timeout to accommodate the potentially longer processing time
        # First number is connection timeout, second is read timeout (in seconds)
        response = requests.post(url, files=files, params=params, timeout=(60, 2400))
        logger.info(f"Response status: {response.status_code}")

        response.raise_for_status()
        data = response.json()
        logger.info(f"Response from VMAI: {str(data)[:500]}...")

        elapsed = round(time.time() - start, 2)

        return {"status": "success", "result": data, "compare_elapsed": elapsed}
    except requests.exceptions.Timeout:
        logger.error("Request to VMAI timed out")
        return {
            "status": "error",
            "error": "Request timed out when calling VMAI service",
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error: {str(e)}")
        return {
            "status": "error",
            "error": f"Error communicating with VMAI service: {str(e)}",
        }
    except Exception as e:
        logger.error(f"Comparison processing error: {str(e)}")
        return {"status": "error", "error": str(e)}


def save_json_to_blob(saveData: dict) -> dict:
    """
    Saves a JSON string to Azure Blob Storage.

    Args:
        saveData (dict): A dictionary containing:
            - json_content (str): The JSON string to save.
            - container_name (str): The name of the blob container.
            - blob_name (str): The name of the blob (including any prefixes).
    """
    try:
        logger.info(
            f"Attempting to save JSON to {saveData['container_name']}/{saveData['blob_name']}"
        )

        json_content = saveData.get("json_content")
        container_name = saveData.get("container_name")
        blob_name = saveData.get("blob_name")

        if not all([json_content, container_name, blob_name]):
            missing_params = [
                p
                for p, v in {
                    "json_content": json_content,
                    "container_name": container_name,
                    "blob_name": blob_name,
                }.items()
                if not v
            ]
            error_msg = f"Missing required parameters for save_json_to_blob: {', '.join(missing_params)}"
            logger.error(error_msg)
            return {"status": "error", "error": error_msg}

        if not bsc.is_available():
            return {"status": "error", "error": "Blob Storage non configurato"}

        blob_path = f"{container_name}/{blob_name}"
        blob_client = bsc.get_blob_client(blob_path)

        blob_client.upload_blob(json_content, overwrite=True)

        logger.info(f"Successfully saved JSON to {container_name}/{blob_name}")
        return {
            "status": "success",
            "container_name": container_name,
            "blob_name": blob_name,
        }
    except Exception as e:
        logger.error(
            f"Error saving JSON to blob {saveData.get('container_name', 'N/A')}/{saveData.get('blob_name', 'N/A')}: {str(e)}"
        )
        return {
            "status": "error",
            "error": str(e),
            "container_name": saveData.get("container_name"),
            "blob_name": saveData.get("blob_name"),
        }


# Mock data endpoints
@app.route(route="getAllScopes", methods=["GET"])
def get_all_scopes(req: func.HttpRequest) -> func.HttpResponse:
    logger.info("Get all scopes request received.")
    try:
        if MODE == "test":
            with open(os.path.join("mock/", "GetAllScopes.json"), "r") as f:
                scopes_data = json.load(f)
            return func.HttpResponse(
                json.dumps(scopes_data), status_code=200, mimetype="application/json"
            )
        else:
            print("API error: prod mode not added yet")
    except Exception as e:
        logger.error(f"An error occurred while fetching scopes: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )


@app.route(route="getAllTopics", methods=["GET"])
def get_all_topics(req: func.HttpRequest) -> func.HttpResponse:
    logger.info("Get all topics request received.")
    try:
        if MODE == "test":
            with open(os.path.join("mock/", "GetAllTopics.json"), "r") as f:
                topics_data = json.load(f)
            return func.HttpResponse(
                json.dumps(topics_data), status_code=200, mimetype="application/json"
            )
        else:
            print("API error: prod mode not added yet")
    except Exception as e:
        logger.error(f"An error occurred while fetching topics: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )


@app.route(route="getLastDocInfo", methods=["GET"])
def get_last_doc_info(req: func.HttpRequest) -> func.HttpResponse:
    logger.info("Get last document info request received.")
    try:
        if MODE == "test":
            with open(os.path.join("mock/", "GetLastDocInfo.json"), "r") as f:
                last_doc_info = json.load(f)
            return func.HttpResponse(
                json.dumps(last_doc_info), status_code=200, mimetype="application/json"
            )
        else:
            print("API error: prod mode not added yet")
    except Exception as e:
        logger.error(f"An error occurred while fetching last document info: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )


@app.route(route="getAllStatistics", methods=["GET"])
def get_all_statistics(req: func.HttpRequest) -> func.HttpResponse:
    logger.info("Get all statistics request received.")
    try:
        if MODE == "test":
            with open(os.path.join("mock/", "GetAllStatistics.json"), "r") as f:
                statistics_data = json.load(f)
            return func.HttpResponse(
                json.dumps(statistics_data),
                status_code=200,
                mimetype="application/json",
            )
        else:
            print("API error: prod mode not added yet")
    except Exception as e:
        logger.error(f"An error occurred while fetching statistics: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )


# API Routes
@app.route(route="getDocuments/{name}", methods=["GET"])
def get_documents(req: func.HttpRequest) -> func.HttpResponse:
    logger.info("Get documents")
    try:
        name = req.route_params.get("name")
        if not name:
            return func.HttpResponse(
                "Il parametro 'name' non è stato fornito.", status_code=400
            )

        url = f"http://{IPVMAI}:2025/api/v0/documents/{name}/"
        logger.info(f"Calling VMAI API at {url}")

        try:
            res = requests.get(url, timeout=(10, 120))  # Add timeout

            res.raise_for_status()
            logger.info(f"VMAI returned status {res.status_code}")

            return func.HttpResponse(
                json.dumps(res.json()), status_code=200, mimetype="application/json"
            )

        except requests.exceptions.Timeout:
            logger.error(f"Timeout while calling VMAI API for document {name}")
            return func.HttpResponse(
                json.dumps(
                    {"error": "Timeout while calling VMAI API", "status_code": 504}
                ),
                status_code=200,
                mimetype="application/json",
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"Error calling VMAI API for document {name}: {str(e)}")
            return func.HttpResponse(
                json.dumps(
                    {
                        "error": f"Error communicating with VMAI service: {str(e)}",
                        "status_code": 500,
                    }
                ),
                status_code=200,
                mimetype="application/json",
            )

    except Exception as e:
        logger.error(f"An error occurred while fetching documents: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e), "status_code": 500}),
            status_code=200,
            mimetype="application/json",
        )


@app.route(route="sendCompare", methods=["POST"])
def send_compare(req: func.HttpRequest) -> func.HttpResponse:
    try:
        data = req.get_json()
        name = data.get("name")
        compareToName = data.get("compareToName")
        url = f"http://{IPVMAI}:2025/api/v0/documents/{name}/compare/{compareToName}"

        logger.info("Sto per chiamare l'elaborazione su FastAPI")

        res = requests.post(url)

        if not name:
            return func.HttpResponse(
                "Il parametro 'name' non è stato fornito.", status_code=400
            )

        logger.info("Returning OK 200")

        return func.HttpResponse(
            json.dumps(res.json()), status_code=200, mimetype="application/json"
        )
    except Exception as e:
        logger.error(f"An error occurred while sending comparison: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )


# Add this function before the get_results function
def add_cache_headers(response, max_age_seconds=1800):
    """Add cache-control headers to a response with the specified max age"""
    headers = response.headers
    headers["Cache-Control"] = f"public, max-age={max_age_seconds}"
    return response


@app.route(route="getCompare/{name}/{compareToName}", methods=["GET"])
def get_compare(req: func.HttpRequest) -> func.HttpResponse:
    name = req.route_params.get("name")
    compareToName = req.route_params.get("compareToName")
    if not name:
        return func.HttpResponse(
            json.dumps(
                {
                    "status": "error",
                    "detail": "Il parametro 'name' non è stato fornito.",
                    "status_code": 400,
                }
            ),
            status_code=200,  # la DF lo interpreta come "fine", ma con errore nel body
            mimetype="application/json",
        )
    try:
        url = f"http://{IPVMAI}:2025/api/v0/documents/{name}/compare/{compareToName}"
        logger.info(f"Calling VMAI API at {url}")

        try:
            res = requests.get(url, timeout=(10, 300))  # Add timeout for long operation
            logger.info(f"VMAI returned status {res.status_code}")

            if res.status_code == 404:
                # "fine con errore" (DF smette di pollare)
                return func.HttpResponse(
                    json.dumps(
                        {
                            "status": "error",
                            "detail": "One or both documents not found.",
                            "status_code": 404,
                        }
                    ),
                    status_code=200,
                    mimetype="application/json",
                )

            if res.status_code != 200:
                return func.HttpResponse(
                    json.dumps(
                        {
                            "status": "error",
                            "detail": f"API responded with code {res.status_code}",
                            "status_code": res.status_code,
                        }
                    ),
                    status_code=200,
                    mimetype="application/json",
                )

            logger.info("Returning OK 200")
            response = func.HttpResponse(
                json.dumps(res.json()), status_code=200, mimetype="application/json"
            )
            # Add cache headers for successful responses - 30 minutes (1800 seconds)
            return add_cache_headers(response, 1800)

        except requests.exceptions.Timeout:
            logger.error(
                f"Timeout while calling VMAI API for comparison {name} vs {compareToName}"
            )
            return func.HttpResponse(
                json.dumps(
                    {
                        "status": "error",
                        "detail": "Timeout while calling VMAI API",
                        "status_code": 504,
                    }
                ),
                status_code=200,
                mimetype="application/json",
            )
        except requests.exceptions.RequestException as e:
            logger.error(
                f"Error calling VMAI API for comparison {name} vs {compareToName}: {str(e)}"
            )
            return func.HttpResponse(
                json.dumps(
                    {
                        "status": "error",
                        "detail": f"Error communicating with VMAI service: {str(e)}",
                        "status_code": 500,
                    }
                ),
                status_code=200,
                mimetype="application/json",
            )
    except Exception as e:
        logger.error(f"An error occurred while fetching comparison: {str(e)}")
        return func.HttpResponse(
            json.dumps({"status": "error", "detail": str(e), "status_code": 500}),
            status_code=200,
            mimetype="application/json",
        )


def _run_extract_subjects_job(job_id: str, input_data: dict):
    try:
        set_running(job_id)
        result = process_subjects(input_data)
        set_completed(job_id, result)
    except Exception as e:
        logger.error(f"extract_subjects error: {str(e)}")
        set_failed(job_id, str(e))


@app.route(route="extract_subjects", methods=["POST"])
def extract_subjects_client(req: func.HttpRequest) -> func.HttpResponse:
    try:
        file = req.files.get("file")
        if not file:
            return func.HttpResponse(
                "No file was provided in the request.", status_code=400
            )
        file_content = file.stream.read()
        if not file_content:
            return func.HttpResponse(
                "The file content is empty or invalid.", status_code=400
            )

        orchestrator_input = {
            "filename": file.filename,
            "file_content": base64.b64encode(file_content).decode("utf-8"),
        }

        job_id = create_job("extract_subjects", "Pending")
        _job_executor.submit(_run_extract_subjects_job, job_id, orchestrator_input)
        logger.info(f"Started job with ID = '{job_id}'.")

        body = {
            "id": job_id,
            "statusQueryGetUri": f"/api/job/{job_id}",
            "status": "running",
        }
        return func.HttpResponse(
            json.dumps(body), status_code=202, mimetype="application/json"
        )

    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )


def process_subjects(inputData: dict) -> dict:
    try:
        logger.info("Processing subjects activity started")

        file_content = base64.b64decode(inputData["file_content"])
        if not file_content:
            raise ValueError("File content is empty after base64 decoding.")

        url = f"http://{IPVMNER}:2025/extract-ner-subjects"

        files = {"file": (inputData["filename"], file_content, "application/pdf")}

        response = requests.post(url, files=files)
        response.raise_for_status()
        logger.info("Subjects extracted successfully")
        return response.json()

    except Exception as e:
        logger.error(f"Subject extraction error: {str(e)}")
        raise


def _run_extract_sanctions_job(job_id: str, input_data: dict):
    try:
        set_running(job_id)
        result = process_sanctions(input_data)
        set_completed(job_id, result)
    except Exception as e:
        logger.error(f"extract_sanctions error: {str(e)}")
        set_failed(job_id, str(e))


@app.route(route="extract_sanctions", methods=["POST"])
def extract_sanctions_client(req: func.HttpRequest) -> func.HttpResponse:
    try:
        file = req.files.get("file")
        external = req.form.get("external")
        if not external:
            return func.HttpResponse(
                "No external param provided in the request.", status_code=400
            )
        if not file:
            return func.HttpResponse(
                "No file was provided in the request.", status_code=400
            )

        orchestrator_input = {
            "filename": file.filename,
            "file_content": base64.b64encode(file.stream.read()).decode("utf-8"),
            "is_external": external in ["1", "true", "True"],
        }

        job_id = create_job("extract_sanctions", "Pending")
        _job_executor.submit(_run_extract_sanctions_job, job_id, orchestrator_input)
        logger.info(f"Started job with ID = '{job_id}'.")

        body = {
            "id": job_id,
            "statusQueryGetUri": f"/api/job/{job_id}",
            "status": "running",
        }
        return func.HttpResponse(
            json.dumps(body), status_code=202, mimetype="application/json"
        )

    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )


def process_sanctions(inputData: dict) -> dict:
    try:
        logger.info("Processing sanctions activity started")

        url = f"http://{IPVMNER}:2026/extract-ner-sanctions"
        file_content = base64.b64decode(inputData["file_content"])
        logger.info(f"Decoded file size: {len(file_content)} bytes")

        files = {"file": (inputData["filename"], file_content, "application/pdf")}

        logger.info(
            f"Sending request to {url} with filename: {inputData['filename']} and file size: {len(files['file'][1])} bytes"
        )

        response = requests.post(url, files=files)
        response.raise_for_status()
        print("RISULTATO process_sanctions", response.json())
        return response.json()
    except Exception as e:
        logger.error(f"Sanctions extraction error: {str(e)}")
        return {"status": "error", "error": str(e)}


@app.route(route="search", methods=["POST"])
def search(req: func.HttpRequest) -> func.HttpResponse:
    try:
        data = req.get_json()
        logger.info(f"Contenuto data: {data}")

        if not data:
            return func.HttpResponse(
                "I parametri in formato JSON non sono stati forniti.", status_code=400
            )

        url = f"http://{IPVMAI}:2025/search"
        res = requests.post(url, json=data)

        logger.info("Returning OK 200")

        return func.HttpResponse(
            json.dumps(res.json()), status_code=200, mimetype="application/json"
        )
    except Exception as e:
        logger.error(f"An error occurred while sending comparison: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )


@app.route(route="download-excel", methods=["GET"])
def download_excel(req: func.HttpRequest) -> func.HttpResponse:
    """
    Download Excel file. First tries blob storage, then falls back to VMAI API.
    """
    logger.info("Azure Function triggered to download Excel.")

    try:
        # Get parameters to determine which Excel file to look for
        file_name = req.params.get("fileName", "")
        comparison_type = req.params.get(
            "type", "analysis"
        )  # Default to analysis if not specified

        # Try to find the Excel file in blob storage first
        if file_name and bsc.is_available():
            try:
                container_client = bsc.get_container_client()

                # Determine the blob path based on the type
                if comparison_type == "analysis":
                    # For single file analysis, look in requirements folder
                    # Get hashed name first
                    try:
                        r = requests.get(
                            f"http://{IPVMAI}:2025/api/v0/hashed-names",
                            params={"name": file_name},
                            timeout=(10, 60),
                        )
                        if r.status_code == 200:
                            hashed = r.json()["hashed"]
                            excel_filename = f"{Path(hashed).stem}.xlsx"
                            blob_path = f"requirements/{excel_filename}"
                        else:
                            blob_path = None
                    except:
                        blob_path = None
                else:
                    # For comparisons, construct the comparison Excel filename
                    file_name1 = req.params.get("fileName1", "")
                    file_name2 = req.params.get("fileName2", "")

                    if file_name1 and file_name2:
                        try:
                            r = requests.get(
                                f"http://{IPVMAI}:2025/api/v0/hashed-names",
                                params={"name1": file_name1, "name2": file_name2},
                                timeout=(10, 60),
                            )
                            if r.status_code == 200:
                                j = r.json()
                                h1 = Path(j["hash1"]).stem
                                h2 = Path(j["hash2"]).stem

                                # Determine container and filename based on comparison type
                                container_map = {
                                    "comparison": "comparisons",
                                    "versioning": "versionings",
                                    "emendativa": "amendings",
                                    "attuativa": "implementations",
                                }
                                folder = container_map.get(
                                    comparison_type, "comparisons"
                                )

                                # Only add _comparison suffix for standard comparisons
                                if comparison_type == "comparison":
                                    excel_filename = f"{h1}_vs_{h2}_comparison.xlsx"
                                else:
                                    # For versioning, emendativa, attuativa: no _comparison suffix
                                    excel_filename = f"{h1}_vs_{h2}.xlsx"

                                blob_path = f"{folder}/{excel_filename}"
                                logger.info(f"Looking for Excel file: {blob_path}")
                            else:
                                blob_path = None
                        except:
                            blob_path = None
                    else:
                        blob_path = None

                # Try to download from blob storage
                if blob_path:
                    full_blob_path = bsc.path_cdp_ext(blob_path)
                    logger.info(f"Attempting to download Excel from blob: {full_blob_path}")
                    try:
                        blob_client = container_client.get_blob_client(full_blob_path)
                        excel_data = blob_client.download_blob().readall()

                        logger.info(
                            f"Successfully downloaded Excel from blob storage: {blob_path}"
                        )

                        # Determine filename for download
                        if comparison_type == "requirement":
                            download_filename = file_name
                        else:
                            download_filename = (
                                excel_filename
                                if "excel_filename" in locals()
                                else "comparison_result.xlsx"
                            )

                        return func.HttpResponse(
                            excel_data,
                            status_code=200,
                            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={
                                "Content-Disposition": f"attachment; filename={download_filename}",
                                "Cache-Control": "public, max-age=3600",  # Cache for 1 hour
                            },
                        )
                    except ResourceNotFoundError:
                        logger.info(
                            f"Excel file not found in blob storage: {blob_path}, falling back to VMAI"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Error downloading from blob storage: {str(e)}, falling back to VMAI"
                        )

            except Exception as e:
                logger.warning(
                    f"Error accessing blob storage: {str(e)}, falling back to VMAI"
                )

        # Fallback to VMAI API
        logger.info("Falling back to VMAI API for Excel generation")
        endpoint_url = f"http://{IPVMAI}:2025/download-excel"

        # Forward any query parameters to VMAI
        params = dict(req.params)
        response = requests.get(endpoint_url, params=params, timeout=(10, 300))

        if response.status_code == 200:
            return func.HttpResponse(
                response.content,
                status_code=200,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": response.headers.get(
                        "Content-Disposition",
                        "attachment; filename=analisi_presidi.xlsx",
                    ),
                    "Cache-Control": "no-cache",  # Don't cache VMAI-generated files
                },
            )
        else:
            logger.error(f"Errore chiamando l'endpoint VMAI: {response.status_code}")
            return func.HttpResponse(
                f"Errore chiamando l'endpoint: {response.status_code}",
                status_code=response.status_code,
            )

    except Exception as e:
        logger.error(f"Errore durante la gestione del download Excel: {str(e)}")
        return func.HttpResponse(
            f"Errore durante la gestione del download Excel: {str(e)}", status_code=500
        )


@app.route(route="download-requirements-result", methods=["GET"])
def download_requirements_result(req: func.HttpRequest) -> func.HttpResponse:
    logger.info("Azure Function triggered to download Excel.")

    endpoint_url = f"http://{IPVMAI}:2025/download-requirements-result"

    try:
        response = requests.get(endpoint_url)

        if response.status_code == 200:
            return func.HttpResponse(
                response.content,
                status_code=200,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={
                    "Content-Disposition": "attachment; filename=analisi_preliminare_requisiti.xlsx"
                },
            )
        else:
            logger.error(f"Errore chiamando l'endpoint: {response.status_code}")
            return func.HttpResponse(
                f"Errore chiamando l'endpoint: {response.status_code}",
                status_code=response.status_code,
            )

    except Exception as e:
        logger.error(f"Errore durante la chiamata all'endpoint: {str(e)}")
        return func.HttpResponse(
            f"Errore durante la chiamata all'endpoint: {str(e)}", status_code=500
        )


@app.route(route="download-requirements-excel", methods=["GET"])
def download_requirements_excel(req: func.HttpRequest) -> func.HttpResponse:
    """
    Proxy per download Excel requirements per documento.
    Prima tenta di servire da blob (se hashed-names restituisce l'hash), poi fallback a VMAI.
    Query: ?filename=<nome file PDF o base name>
    """
    filename = req.params.get("filename", "").strip()
    if not filename:
        return func.HttpResponse(
            "Parametro filename è obbligatorio", status_code=400
        )

    # Try blob first (same logic as download-excel) so we can serve when VMAI mapping is missing
    if bsc.is_available():
        try:
            r = requests.get(
                f"http://{IPVMAI}:2025/api/v0/hashed-names",
                params={"name": filename},
                timeout=(10, 60),
            )
            if r.status_code == 200:
                hashed = r.json().get("hashed")
                if hashed:
                    stem = Path(hashed).stem
                    excel_filename = f"{stem}.xlsx"
                    blob_path = f"requirements/{excel_filename}"
                    full_blob_path = bsc.path_cdp_ext(blob_path)
                    container_client = bsc.get_container_client()
                    try:
                        blob_client = container_client.get_blob_client(full_blob_path)
                        excel_data = blob_client.download_blob().readall()
                        logger.info("Download requirements Excel from blob: %s", full_blob_path)
                        download_name = Path(filename).stem + ".xlsx" if filename else excel_filename
                        return func.HttpResponse(
                            excel_data,
                            status_code=200,
                            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={
                                "Content-Disposition": f'attachment; filename="{download_name}"',
                                "Cache-Control": "public, max-age=3600",
                            },
                        )
                    except ResourceNotFoundError:
                        logger.info("Excel not found in blob %s, falling back to VMAI", full_blob_path)
        except Exception as e:
            logger.warning("Blob path for download-requirements-excel failed: %s, falling back to VMAI", e)

    qs = urlencode({"filename": filename})
    vm_url = f"http://{IPVMAI}:2025/download-requirements-excel?{qs}"
    logger.info("Proxy download-requirements-excel: %s", vm_url)
    try:
        vm_resp = requests.get(vm_url, timeout=60, stream=True)
        if vm_resp.status_code != 200:
            logger.error("VMAI download-requirements-excel returned %s", vm_resp.status_code)
            return func.HttpResponse(
                vm_resp.text or f"Errore VMAI: {vm_resp.status_code}",
                status_code=vm_resp.status_code,
            )
        content_disp = vm_resp.headers.get(
            "Content-Disposition",
            'attachment; filename="requirements.xlsx"',
        )
        return func.HttpResponse(
            body=vm_resp.content,
            status_code=200,
            headers={
                "Content-Disposition": content_disp,
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            },
        )
    except Exception as e:
        logger.error("Errore proxy download-requirements-excel: %s", e)
        return func.HttpResponse(
            f"Errore durante il download: {str(e)}", status_code=500
        )


@app.route(route="download-comparison-result", methods=["GET"])
def download_comparison_result(req: func.HttpRequest) -> func.HttpResponse:
    logger.info("Azure Function triggered to download HTML showing comparison results.")

    endpoint_url = f"http://{IPVMAI}:2025/download-comparison-result"

    try:
        response = requests.get(endpoint_url)

        if response.status_code == 200:
            return func.HttpResponse(
                response.content,
                status_code=200,
                mimetype="text/html",
                headers={
                    "Content-Disposition": "attachment; filename=risultati_confronto.html"
                },
            )
        else:
            logger.error(f"Errore chiamando l'endpoint: {response.status_code}")
            return func.HttpResponse(
                f"Errore chiamando l'endpoint: {response.status_code}",
                status_code=response.status_code,
            )

    except Exception as e:
        logger.error(f"Errore durante la chiamata all'endpoint: {str(e)}")
        return func.HttpResponse(
            f"Errore durante la chiamata all'endpoint: {str(e)}", status_code=500
        )

@app.route(route="download-comparison-excel", methods=["GET"])
def download_comparison_excel(req: func.HttpRequest) -> func.HttpResponse:
    """
    Proxy HTTPS → HTTP.
    Pass-through dell’Excel generato dalla VM in modo che il frontend non
    venga bloccato per mixed-content.
    Query string richiesta:
      ?fileName1=<...>&fileName2=<...>&comparisonMode=<versioning|attuativa|emendativa>
    """
    file1 = req.params.get("fileName1")
    file2 = req.params.get("fileName2")
    mode  = req.params.get("comparisonMode", "versioning")

    if not (file1 and file2):
        return func.HttpResponse(
            "fileName1 e fileName2 sono obbligatori", status_code=400
        )

    # Try blob first (same folder convention as download-excel)
    if bsc.is_available():
        try:
            r = requests.get(
                f"http://{IPVMAI}:2025/api/v0/hashed-names",
                params={"name1": file1, "name2": file2},
                timeout=(10, 60),
            )
            if r.status_code == 200:
                j = r.json()
                h1 = Path(j.get("hash1", "")).stem
                h2 = Path(j.get("hash2", "")).stem
                if h1 and h2:
                    h1, h2 = sorted([h1, h2])
                    folder_map = {
                        "versioning": "versionings",
                        "attuativa": "implementations",
                        "emendativa": "amendments",
                    }
                    folder = folder_map.get(mode, "versionings")
                    excel_filename = f"{h1}_vs_{h2}.xlsx"
                    blob_path = f"{folder}/{excel_filename}"
                    full_blob_path = bsc.path_cdp_ext(blob_path)
                    container_client = bsc.get_container_client()
                    try:
                        blob_client = container_client.get_blob_client(full_blob_path)
                        excel_data = blob_client.download_blob().readall()
                        logger.info("Download comparison Excel from blob: %s", full_blob_path)
                        plain1 = Path(file1).stem
                        plain2 = Path(file2).stem
                        download_name = f"{plain1}_vs_{plain2}.xlsx"
                        return func.HttpResponse(
                            excel_data,
                            status_code=200,
                            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={
                                "Content-Disposition": f'attachment; filename="{download_name}"',
                                "Cache-Control": "public, max-age=3600",
                            },
                        )
                    except ResourceNotFoundError:
                        logger.info("Comparison Excel not found in blob %s, falling back to VMAI", full_blob_path)
        except Exception as e:
            logger.warning("Blob path for download-comparison-excel failed: %s, falling back to VMAI", e)

    # Costruisci la URL da chiamare sulla VM
    qs = urlencode({"fileName1": file1, "fileName2": file2, "comparisonMode": mode})
    vm_url = f"http://{IPVMAI}:2025/download-comparison-excel?{qs}"
    logger.info("Proxy download: %s", vm_url)

    try:
        vm_resp = requests.get(vm_url, timeout=120, stream=True)

        if vm_resp.status_code != 200:
            logger.error("VM returned %s", vm_resp.status_code)
            return func.HttpResponse(
                f"Errore VM: {vm_resp.status_code}", status_code=vm_resp.status_code
            )

        # Usa headers originali se presenti, altrimenti imposta noi
        headers = {
            "Content-Disposition": vm_resp.headers.get(
                "Content-Disposition",
                f'attachment; filename="comparison.xlsx"'
            ),
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }

        return func.HttpResponse(
            body=vm_resp.content,
            status_code=200,
            headers=headers,
        )

    except requests.exceptions.RequestException as exc:
        logger.exception("Errore chiamando la VM")
        return func.HttpResponse(
            f"Errore durante la chiamata alla VM: {exc}", status_code=502
        )

def _run_translate_job(job_id: str, input_data: dict):
    try:
        set_running(job_id)
        result = process_translation(input_data)
        set_completed(job_id, result)
    except Exception as e:
        logger.error(f"translate error: {str(e)}")
        set_failed(job_id, str(e))


@app.route(route="translate", methods=["POST"])
def translate_client(req: func.HttpRequest) -> func.HttpResponse:
    try:
        file = req.files.get("file")
        if not file:
            return func.HttpResponse(
                "No file was provided in the request.", status_code=400
            )

        # process_translation expects file_content as bytes (not base64)
        input_data = {"filename": file.filename, "file_content": file.stream.read()}

        job_id = create_job("translate", "Pending")
        _job_executor.submit(_run_translate_job, job_id, input_data)

        body = {
            "id": job_id,
            "statusQueryGetUri": f"/api/job/{job_id}",
            "status": "running",
        }
        return func.HttpResponse(
            json.dumps(body), status_code=202, mimetype="application/json"
        )
    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )


def process_translation(inputData: dict) -> dict:
    try:
        # Check if any process is already running
        url = f"http://{IPVMAI}:2025/translate"

        files = {
            "file": (
                inputData["filename"],
                inputData["file_content"],
                "application/pdf",
            )
        }

        res = requests.post(url, files=files)
        return res.json()
    except Exception as e:
        logger.error(f"Translation error: {str(e)}")
        raise


@app.route(route="get-results", methods=["GET"])
def get_results(req: func.HttpRequest) -> func.HttpResponse:
    try:
        t = (req.params.get("type") or "").strip().lower()
        if not t:
            return func.HttpResponse(
                json.dumps({"error": "Missing parameter 'type'", "status_code": 400}),
                status_code=200,
                mimetype="application/json",
            )
        if t not in (
            "requirement",
            "subject",
            "sanction",
            "comparison",
            "versioning",
            "emendativa",
            "attuativa",
            "implementation",
            "amendment",
        ):
            return func.HttpResponse(
                json.dumps({"error": f"Invalid type '{t}'", "status_code": 400}),
                status_code=200,
                mimetype="application/json",
            )

        folder = {
            "requirement": "requirements",
            "subject": "subjects",
            "sanction": "sanctions",
            "comparison": "comparisons",
            "versioning": "versionings",
            "emendativa": "amendments",
            "attuativa": "implementations",
            "implementation": "implementations",
            "amendment": "amendments",
        }[t]

        blob_name = None

        comparisonMode = req.params.get("comparisonMode", "false")

        if t not in (
            "comparison",
            "versioning",
            "emendativa",
            "attuativa",
            "implementation",
            "amendment",
        ):
            plain_name = req.params.get("name", "")
            if not plain_name:
                return func.HttpResponse(
                    json.dumps(
                        {"error": "Missing parameter 'name'", "status_code": 400}
                    ),
                    status_code=200,
                    mimetype="application/json",
                )
            logger.info(f"Getting hashed name for '{plain_name}'")

            try:
                r = requests.get(
                    f"http://{IPVMAI}:2025/api/v0/hashed-names",
                    params={"name": plain_name},
                    timeout=(30, 120),  # Add timeout (connection_timeout, read_timeout)
                )
                r.raise_for_status()
                hashed = r.json()["hashed"]
                blob_name = hashed
                logger.info(f"Hashed name received: {hashed}")

                # Add more logging for debugging
                logger.info(f"Calling VMAI results endpoint for {hashed}")

                r2 = requests.get(
                    f"http://{IPVMAI}:2025/api/v0/results/{hashed}",
                    params={"comparisonMode": comparisonMode},
                    timeout=(30, 300),  # Add timeout (connection_timeout, read_timeout)
                )

            except requests.exceptions.Timeout:
                logger.error(f"Timeout while calling VMAI API for {plain_name}")
                return func.HttpResponse(
                    json.dumps(
                        {"error": "Timeout while calling VMAI API", "status_code": 504}
                    ),
                    status_code=200,
                    mimetype="application/json",
                )
            except requests.exceptions.RequestException as e:
                logger.error(f"Error calling VMAI API for {plain_name}: {str(e)}")
                # Continue to blob fallback
                r2 = None

        else:
            n1 = req.params.get("fileName1", "")
            n2 = req.params.get("fileName2", "")
            if not n1 or not n2:
                return func.HttpResponse(
                    json.dumps(
                        {
                            "error": "Comparison needs fileName1 & fileName2",
                            "status_code": 400,
                        }
                    ),
                    status_code=200,
                    mimetype="application/json",
                )
            logger.info(f"Getting hashed names for comparison '{n1}' vs '{n2}'")

            try:
                r = requests.get(
                    f"http://{IPVMAI}:2025/api/v0/hashed-names",
                    params={"name1": n1, "name2": n2},
                    timeout=(10, 60),  # Add timeout
                )
                r.raise_for_status()
                j = r.json()
                h1 = Path(j["hash1"]).stem
                h2 = Path(j["hash2"]).stem
                # Build blob name depending on comparison type
                if t == "comparison":
                    cmp_name = f"{h1}_vs_{h2}_comparison.json"
                else:
                    # For versioning, emendativa, attuativa we don't add the _comparison suffix
                    cmp_name = f"{h1}_vs_{h2}.json"
                blob_name = cmp_name
                logger.info(f"Comparison blob name: {cmp_name}")

                logger.info(f"Calling VMAI results endpoint for comparison {cmp_name}")

                r2 = requests.get(
                    f"http://{IPVMAI}:2025/api/v0/results/{cmp_name}",
                    params={"comparisonMode": comparisonMode},
                    timeout=(10, 300),  # Add timeout
                )

            except requests.exceptions.Timeout:
                logger.error(
                    f"Timeout while calling VMAI API for comparison {n1} vs {n2}"
                )
                return func.HttpResponse(
                    json.dumps(
                        {"error": "Timeout while calling VMAI API", "status_code": 504}
                    ),
                    status_code=200,
                    mimetype="application/json",
                )
            except requests.exceptions.RequestException as e:
                logger.error(
                    f"Error calling VMAI API for comparison {n1} vs {n2}: {str(e)}"
                )
                # Continue to blob fallback
                r2 = None

        # if VMAI returned OK, just pass it through:
        if r2 is not None and r2.status_code == 200 and r2.content:
            logger.info("VMAI returned valid response, passing through")
            response = func.HttpResponse(
                r2.content, status_code=200, mimetype="application/json"
            )
            # Add cache headers for successful responses - 30 minutes (1800 seconds)
            return add_cache_headers(response, 1800)

        if not blob_name:
            raise ValueError("Internal error: no blob_name computed")

        # otherwise fall back to blob storage:
        blob_path = f"{folder}/{blob_name}"
        logger.info(f"VMAI didn't return valid response, falling back to blob storage")
        logger.info(
            f"Attempting to access blob at path: {blob_path} in container: {CONTAINER_NAME_EXT}"
        )

        if not bsc.is_available():
            return func.HttpResponse(
                json.dumps({"error": "Blob Storage non configurato", "status_code": 500}),
                status_code=200,
                mimetype="application/json",
            )
        full_path = bsc.path_cdp_ext(blob_path)
        container_client = bsc.get_container_client()
        prefix = bsc.path_cdp_ext(folder)
        folder_exists = False
        blobs = list(container_client.list_blobs(name_starts_with=prefix))
        if not blobs:
            placeholder_blob = container_client.get_blob_client(f"{prefix}/.placeholder")
            try:
                placeholder_blob.upload_blob("", overwrite=True)
            except Exception as folder_ex:
                logger.error(f"Failed to create folder placeholder: {folder_ex}")
        else:
            folder_exists = True

        blobs = [blob.name for blob in container_client.list_blobs(name_starts_with=prefix)]

        if full_path not in blobs:
            logger.error(
                f"Blob {blob_path} not found in container {CONTAINER_NAME_EXT}"
            )
            return func.HttpResponse(
                json.dumps(
                    {
                        "error": f"Blob not found. Available: {blobs}",
                        "folder_created": not folder_exists,
                        "status_code": 404,
                    }
                ),
                status_code=200,
                mimetype="application/json",
            )

        blob = container_client.get_blob_client(full_path)
        data = blob.download_blob().readall()
        response = func.HttpResponse(data, status_code=200, mimetype="application/json")
        # Add cache headers for blob storage responses - 30 minutes (1800 seconds)
        return add_cache_headers(response, 1800)

    except (requests.HTTPError, ValueError) as e:
        logging.error(e)
        return func.HttpResponse(
            json.dumps({"error": str(e), "status_code": 400}),
            status_code=200,
            mimetype="application/json",
        )
    except ResourceNotFoundError as e:
        logger.error(f"ResourceNotFoundError: {e}")
        return func.HttpResponse(
            json.dumps({"error": f"Blob not found: {e}", "status_code": 404}),
            status_code=200,
            mimetype="application/json",
        )
    except Exception as e:
        logger.error(f"Errore in get_results: {e}")
        return func.HttpResponse(
            json.dumps({"error": str(e), "status_code": 500}),
            status_code=200,
            mimetype="application/json",
        )


def extract_text_from_pdf(file_obj):
    """Estrae il testo dal PDF mantenendo la struttura del documento"""
    try:
        # Verify we have a BytesIO object
        if not isinstance(file_obj, BytesIO):
            logger.warning("File object is not BytesIO, converting")
            file_content = file_obj.read() if hasattr(file_obj, "read") else file_obj
            file_obj = BytesIO(file_content)

        # Reset position to start of file
        file_obj.seek(0)

        # Check if file has PDF signature
        pdf_signature = file_obj.read(4)
        file_obj.seek(0)  # Reset position again

        if pdf_signature != b"%PDF":
            logger.warning("File does not have PDF signature, may not be a valid PDF")

        reader = PdfReader(file_obj)
        text = ""

        if not reader.pages:
            logger.warning("PDF has no pages")
            return ""

        for page in reader.pages:
            extracted = page.extract_text() or ""
            text += extracted + "\n"

        return text
    except Exception as e:
        logger.error(f"Error in PDF extraction: {str(e)}")
        # Return empty string instead of letting the exception propagate
        return ""


def _blob_client(blob_path: str) -> BlobClient:
    """BlobClient per path nel single container."""
    return bsc.get_blob_client(blob_path)


def get_tokens(text: str) -> range:
    """Approximate token count — 1 token ≈ 4 characters."""
    return range(len(text) // 4)


def load_tokens_per_second() -> float:
    logging.info("====================load_tokens_per_second====================")
    try:
        blob_client = _blob_client(bsc.path_conf(BLOB_FILENAME))
        blob_data = blob_client.download_blob().readall()
        data = json.loads(blob_data.decode("utf-8"))
        return data.get("tokens_per_second", DEFAULT_TOKENS_PER_SECOND)
    except Exception:
        return DEFAULT_TOKENS_PER_SECOND


def save_execution_informations(execution_time_sum, token_sum):
    logging.info("====================save_execution_informations====================")
    blob_client = ensure_sum_blob()
    data = {
        "token_sum": token_sum,
        "execution_time_sum": execution_time_sum,
        "last_updated": datetime.utcnow().isoformat(),
    }
    blob_client.upload_blob(json.dumps(data), overwrite=True)


def get_execution_informations():
    """
    Ritorna il dict contenente le somme.
    Se il blob non esiste viene creato con default e restituito.
    """
    blob_client = ensure_sum_blob()
    downloaded = blob_client.download_blob().readall()
    return json.loads(downloaded.decode("utf-8"))


def save_tokens_per_second(new_value: float):
    logging.info("====================save_tokens_per_second====================")
    blob_client = _blob_client(bsc.path_conf(BLOB_FILENAME))
    data = {
        "tokens_per_second": new_value,
        "last_updated": datetime.utcnow().isoformat(),
    }
    blob_client.upload_blob(json.dumps(data), overwrite=True)


def load_history() -> List[Dict]:
    logging.info("====================load_history====================")
    try:
        blob_client = _blob_client(bsc.path_conf(HISTORY_FILENAME))
        blob_data = blob_client.download_blob().readall()
        return json.loads(blob_data.decode("utf-8"))
    except:
        return []


def save_history(history: List[Dict]):
    logging.info("====================save_history====================")
    history = history[-MAX_HISTORY_ENTRIES:]
    blob_client = _blob_client(bsc.path_conf(HISTORY_FILENAME))
    blob_client.upload_blob(json.dumps(history), overwrite=True)


def compute_average_tokens_per_second(history: List[Dict]) -> float:
    logging.info(
        "====================compute_average_tokens_per_second===================="
    )
    valid_rates = [
        h["token_count"] / h["duration"]
        for h in history
        if h["duration"] > 5 and h["token_count"] > 0
    ]
    if not valid_rates:
        return DEFAULT_TOKENS_PER_SECOND
    return round(sum(valid_rates) / len(valid_rates), 2)


def update_and_compute_tokens_per_second(token_count: int, actual_time: float) -> float:
    logging.info(
        "====================update_and_compute_tokens_per_second===================="
    )
    history = load_history()
    history.append(
        {
            "token_count": token_count,
            "duration": actual_time,
            "timestamp": datetime.utcnow().isoformat(),
        }
    )
    save_history(history)
    new_value = compute_average_tokens_per_second(history)
    save_tokens_per_second(new_value)
    return new_value


def estimate_processing_time(text: str, tokens_per_second: float) -> dict:
    logging.info("====================estimate_processing_time====================")
    token_count = len(text) // 4  # approximation: 1 token ≈ 4 characters
    estimated_time = round((token_count / tokens_per_second) * 1.2, 2)
    return {"token_count": token_count, "estimated_time_sec": estimated_time}


@app.route(route="get-document-list", methods=["GET"])
def get_document_list(req: func.HttpRequest) -> func.HttpResponse:
    """
    Scans blob and local for PDFs. For each PDF, attempts to find a matching JSON
    by:
    1. Direct name match (pdf_base.json).
    2. Calling VMAI hashing service to get expected JSON name, then matching that.
    Uploads local PDFs and their matched JSONs if not in blob.
    Handles URL-encoded blob names.
    """
    import urllib.parse
    import requests
    import os

    # Logging via logger Python standard.

    log_messages_aggregator = (
        []
    )  # Lista per accumulare i messaggi di log specifici di questa run

    def add_log(message, level="info"):
        timestamped_message = f"[{datetime.utcnow().isoformat()}] {message}"
        log_messages_aggregator.append(timestamped_message)
        if level == "info":
            logger.info(message)
        elif level == "warning":
            logger.warning(message)
        elif level == "error":
            logger.error(message)
        else:
            logger.info(message)  # Default to info

    add_log(
        "get_document_list: Initiating scan for PDFs, attempting direct and API-hashed JSON matches in blob and filesystem"
    )
    try:
        # region Boilerplate: Check Blob Storage and Initialize
        if not bsc.is_available():
            add_log("Blob Storage non configurato", level="error")
            return func.HttpResponse(
                json.dumps(
                    {
                        "error": "Azure Storage non configurato",
                        "detailed_logs": log_messages_aggregator,
                    }
                ),
                status_code=500,
                mimetype="application/json",
            )

        if not IPVMAI:
            add_log("IPVMAI is not available", level="error")
            return func.HttpResponse(
                json.dumps(
                    {
                        "error": "IPVMAI (VMAI service IP/hostname) is not configured",
                        "detailed_logs": log_messages_aggregator,
                    }
                ),
                status_code=500,
                mimetype="application/json",
            )

        try:
            container_client = bsc.get_container_client()
            add_log(f"Successfully connected to container {bsc._get_container_name()}")
        except Exception as e:
            add_log(f"Error connecting to blob storage: {str(e)}", level="error")
            return func.HttpResponse(
                json.dumps(
                    {
                        "error": f"Error connecting to blob storage: {str(e)}",
                        "detailed_logs": log_messages_aggregator,
                    }
                ),
                status_code=500,
                mimetype="application/json",
            )
        # endregion

        # region Step 1: Get All Files from Blob and Local Filesystem
        blob_decoded_pdf_map = {}
        blob_json_base_to_full_map = {}
        comparison_files = []  # New list to collect comparison files
        hash_to_pdf_map = {}  # New mapping from hash to PDF name

        try:
            all_blobs = list(container_client.list_blobs())
            for blob in all_blobs:
                decoded_name = urllib.parse.unquote(blob.name)

                # Estrai solo il nome del file senza percorso
                file_name_only = os.path.basename(decoded_name)
                base_name, ext = os.path.splitext(file_name_only)
                ext_lower = ext.lower()

                if ext_lower == ".pdf":
                    blob_decoded_pdf_map[decoded_name] = blob.name
                elif ext_lower == ".json":
                    # Check if this is a comparison file
                    if "_vs_" in file_name_only:
                        comparison_files.append(file_name_only)
                        add_log(f"Found comparison file: {file_name_only}")
                    else:
                        # Salva il nome base (senza path) in minuscolo come chiave
                        # e il nome completo decodificato come valore
                        blob_json_base_to_full_map[base_name.lower()] = decoded_name

                    # Salva anche i file JSON senza l'estensione per catturare più match possibili
                    # Questo è utile se il servizio di hashing restituisce nomi senza estensione
                    path_base_name = os.path.splitext(file_name_only)[0].lower()
                    if path_base_name not in blob_json_base_to_full_map:
                        blob_json_base_to_full_map[path_base_name] = decoded_name

            add_log(
                f"Blob: Found {len(blob_decoded_pdf_map)} PDFs, {len(blob_json_base_to_full_map)} base JSON names"
            )
            add_log(f"Blob PDF Names (decoded): {list(blob_decoded_pdf_map.keys())}")
            add_log(
                f"Blob JSON Base Names (lower): {list(blob_json_base_to_full_map.keys())}"
            )
            add_log(
                f"Blob JSON Full Names (decoded): {list(blob_json_base_to_full_map.values())}"
            )

        except Exception as e:
            add_log(f"Error listing blobs: {str(e)}", level="error")

        local_pdf_files = set()
        local_json_base_to_full_map = {}
        local_json_full_to_path_map = {}

        local_data_dir = "./data"
        if os.path.exists(local_data_dir):
            for item in os.listdir(local_data_dir):
                full_path = os.path.join(local_data_dir, item)
                if os.path.isfile(full_path):
                    base_name, ext = os.path.splitext(item)
                    ext_lower = ext.lower()
                    if ext_lower == ".pdf":
                        local_pdf_files.add(item)
                    elif ext_lower == ".json":
                        local_json_base_to_full_map[base_name.lower()] = item
                        local_json_full_to_path_map[item] = full_path
            add_log(
                f"Local: Found {len(local_pdf_files)} PDFs, {len(local_json_base_to_full_map)} JSONs (by base name)"
            )
            add_log(f"Local PDF Names: {list(local_pdf_files)}")
            add_log(
                f"Local JSON Base Names (lower): {list(local_json_base_to_full_map.keys())}"
            )
            add_log(
                f"Local JSON Full Names: {list(local_json_base_to_full_map.values())}"
            )
        else:
            add_log(
                f"Local data directory {local_data_dir} not found.", level="warning"
            )
        # endregion

        all_unique_decoded_pdf_names = set(blob_decoded_pdf_map.keys()).union(
            local_pdf_files
        )
        add_log(
            f"Total unique PDF names to check: {len(all_unique_decoded_pdf_names)} -> {list(all_unique_decoded_pdf_names)}"
        )

        matched_decoded_pdfs = []
        pdf_to_json_map_decoded = {}
        files_to_upload = {}

        for decoded_pdf_name in all_unique_decoded_pdf_names:
            pdf_base_name_lower = os.path.splitext(decoded_pdf_name)[0].lower()
            matched_json_full_name = None
            match_method = "None"
            add_log(
                f"Processing PDF: '{decoded_pdf_name}' (base_lower: '{pdf_base_name_lower}')"
            )

            # Attempt 1: Direct match (pdf_base_name.json) in local files
            if pdf_base_name_lower in local_json_base_to_full_map:
                matched_json_full_name = local_json_base_to_full_map[
                    pdf_base_name_lower
                ]
                match_method = "Local Direct"
                add_log(
                    f"  Attempt 1 (Local Direct) for '{pdf_base_name_lower}': FOUND in local_json_base_to_full_map. Matched JSON: '{matched_json_full_name}'"
                )
            else:
                add_log(
                    f"  Attempt 1 (Local Direct) for '{pdf_base_name_lower}': NOT FOUND in local_json_base_to_full_map (Keys: {list(local_json_base_to_full_map.keys())})"
                )

            # Attempt 2: Direct match in blob files (if not found locally)
            if (
                not matched_json_full_name
                and pdf_base_name_lower in blob_json_base_to_full_map
            ):
                matched_json_full_name = blob_json_base_to_full_map[pdf_base_name_lower]
                match_method = "Blob Direct"
                add_log(
                    f"  Attempt 2 (Blob Direct) for '{pdf_base_name_lower}': FOUND in blob_json_base_to_full_map. Matched JSON: '{matched_json_full_name}'"
                )
            elif not matched_json_full_name:
                add_log(
                    f"  Attempt 2 (Blob Direct) for '{pdf_base_name_lower}': NOT FOUND in blob_json_base_to_full_map (Keys: {list(blob_json_base_to_full_map.keys())})"
                )

            # Attempt 3: Fallback to Hashing API if no direct match
            if not matched_json_full_name:
                match_method = "API Hash"
                add_log(
                    f"  Attempt 3 (API Hash) for PDF '{decoded_pdf_name}': No direct match found yet, trying Hashing API."
                )
                try:
                    pdf_filename_for_api = os.path.basename(
                        decoded_pdf_name
                    )  # Pass only filename to API
                    hash_api_url = f"http://{IPVMAI}:2025/api/v0/hashed-names"
                    add_log(
                        f"    Calling Hashing API: {hash_api_url} with name='{pdf_filename_for_api}'"
                    )
                    response = requests.get(
                        hash_api_url,
                        params={
                            "name": pdf_filename_for_api
                        },  # Usa pdf_filename_for_api
                        timeout=(10, 60),
                    )
                    add_log(f"    Hashing API Status Code: {response.status_code}")
                    response.raise_for_status()
                    api_response_json = response.json()
                    add_log(f"    Hashing API Response JSON: {api_response_json}")
                    raw_hashed_name_from_api = api_response_json.get("hashed")

                    if raw_hashed_name_from_api:
                        api_hashed_base_lower = os.path.splitext(
                            raw_hashed_name_from_api
                        )[0].lower()
                        add_log(
                            f"    API Raw Hashed Name: '{raw_hashed_name_from_api}', Derived API Hashed Base (lower): '{api_hashed_base_lower}'"
                        )

                        if api_hashed_base_lower in local_json_base_to_full_map:
                            matched_json_full_name = local_json_base_to_full_map[
                                api_hashed_base_lower
                            ]
                            add_log(
                                f"    Found API-hashed JSON ('{api_hashed_base_lower}') match in LOCAL files: '{matched_json_full_name}'"
                            )
                        elif api_hashed_base_lower in blob_json_base_to_full_map:
                            matched_json_full_name = blob_json_base_to_full_map[
                                api_hashed_base_lower
                            ]
                            add_log(
                                f"    Found API-hashed JSON ('{api_hashed_base_lower}') match in BLOB files: '{matched_json_full_name}'"
                            )
                        else:
                            add_log(
                                f"    API-hashed base '{api_hashed_base_lower}' NOT FOUND in local_json_base_to_full_map (Keys: {list(local_json_base_to_full_map.keys())}) or blob_json_base_to_full_map (Keys: {list(blob_json_base_to_full_map.keys())})"
                            )
                    else:
                        add_log(
                            f"    Hashing API for '{decoded_pdf_name}' (sent as '{pdf_filename_for_api}') returned no 'hashed' field. Response: {api_response_json}",
                            level="warning",
                        )
                except Exception as e:
                    add_log(
                        f"    Error during Hashing API call for '{decoded_pdf_name}' (sent as '{pdf_filename_for_api}'): {str(e)}",
                        level="error",
                    )

            if matched_json_full_name:
                add_log(
                    f"  ✓ FINAL MATCH for PDF '{decoded_pdf_name}' is JSON '{matched_json_full_name}' (Method: {match_method})"
                )
                matched_decoded_pdfs.append(decoded_pdf_name)
                pdf_to_json_map_decoded[decoded_pdf_name] = matched_json_full_name

                pdf_in_blob = decoded_pdf_name in blob_decoded_pdf_map
                pdf_is_local = decoded_pdf_name in local_pdf_files

                json_base_lower_of_matched = os.path.splitext(matched_json_full_name)[
                    0
                ].lower()
                json_in_blob_via_base = (
                    json_base_lower_of_matched in blob_json_base_to_full_map
                )
                json_is_local = matched_json_full_name in local_json_full_to_path_map

                if pdf_is_local and not pdf_in_blob:
                    files_to_upload[decoded_pdf_name] = os.path.join(
                        local_data_dir, decoded_pdf_name
                    )
                    add_log(f"    Marked local PDF '{decoded_pdf_name}' for upload.")

                if json_is_local and not json_in_blob_via_base:
                    files_to_upload[matched_json_full_name] = (
                        local_json_full_to_path_map[matched_json_full_name]
                    )
                    add_log(
                        f"    Marked local JSON '{matched_json_full_name}' for upload."
                    )
            else:
                add_log(
                    f"  ✗ NO FINAL JSON match found for PDF '{decoded_pdf_name}' after all attempts."
                )

        # region Step 4: Upload Files
        files_uploaded_count = 0
        if files_to_upload:
            add_log(
                f"Attempting to upload {len(files_to_upload)} files to blob storage: {list(files_to_upload.keys())}"
            )
            for filename_to_upload_as, local_filepath in files_to_upload.items():
                try:
                    blob_client = container_client.get_blob_client(
                        filename_to_upload_as
                    )
                    with open(local_filepath, "rb") as data:
                        blob_client.upload_blob(data, overwrite=True)
                    files_uploaded_count += 1
                    add_log(
                        f"  Uploaded '{filename_to_upload_as}' from '{local_filepath}' to blob."
                    )
                except Exception as e:
                    add_log(
                        f"  Error uploading '{filename_to_upload_as}' to blob: {str(e)}",
                        level="error",
                    )
        # endregion

        total_pdfs_considered = len(all_unique_decoded_pdf_names)
        all_json_bases_considered = set(blob_json_base_to_full_map.keys()).union(
            set(local_json_base_to_full_map.keys())
        )
        total_jsons_considered_count = len(all_json_bases_considered)

        add_log(
            f"Final counts: Matched PDFs: {len(matched_decoded_pdfs)}, Total PDFs considered: {total_pdfs_considered}, Total unique JSON base names considered: {total_jsons_considered_count}, Files Uploaded: {files_uploaded_count}"
        )

        # Build hash to PDF mapping from the pdf_to_json_map_decoded
        for pdf_name, json_path in pdf_to_json_map_decoded.items():
            # Extract hash from json path (e.g., "requirements/hash.json" -> "hash")
            json_filename = os.path.basename(json_path)
            hash_value = os.path.splitext(json_filename)[0]
            hash_to_pdf_map[hash_value] = pdf_name
            add_log(f"Hash mapping: {hash_value} -> {pdf_name}")

        # Also try to extract PDF names from all JSON files in blob storage
        # This helps when one of the PDFs in a comparison is no longer in the main list
        for json_name in blob_json_base_to_full_map.keys():
            if (
                json_name not in hash_to_pdf_map and len(json_name) == 32
            ):  # Likely a hash
                # Try to find a PDF with a similar base name
                for pdf_name in all_unique_decoded_pdf_names:
                    pdf_base = os.path.splitext(pdf_name)[0].lower()
                    if pdf_base not in hash_to_pdf_map.values():
                        # This might be an orphaned hash, skip it
                        continue
                add_log(f"Found additional hash in blob storage: {json_name}")

        # Create user-friendly names for comparison files
        comparison_files_with_names = []
        add_log(f"Processing {len(comparison_files)} comparison files")
        add_log(f"Hash to PDF map contains: {hash_to_pdf_map}")

        for comp_file in comparison_files:
            if "_vs_" in comp_file:
                # Extract the two hashes from the filename
                base_name = comp_file.replace(".json", "").replace(".xlsx", "")
                parts = base_name.split("_vs_")
                if len(parts) == 2:
                    hash1, hash2 = parts
                    add_log(
                        f"Processing comparison: {comp_file}, hash1={hash1}, hash2={hash2}"
                    )

                    pdf1 = hash_to_pdf_map.get(hash1, f"Unknown ({hash1})")
                    pdf2 = hash_to_pdf_map.get(hash2, f"Unknown ({hash2})")

                    add_log(f"Mapped to: pdf1={pdf1}, pdf2={pdf2}")

                    # Create user-friendly name
                    friendly_name = f"{pdf1} c/ {pdf2}"
                    comparison_files_with_names.append(
                        {"filename": comp_file, "displayName": friendly_name}
                    )
                else:
                    comparison_files_with_names.append(
                        {"filename": comp_file, "displayName": comp_file}
                    )
            else:
                comparison_files_with_names.append(
                    {"filename": comp_file, "displayName": comp_file}
                )


        return func.HttpResponse(
            json.dumps(
                {
                    "files": matched_decoded_pdfs,
                    "totalPdfs": total_pdfs_considered,
                    "totalJsons": total_jsons_considered_count,
                    "totalMatches": len(matched_decoded_pdfs),
                    "filesUploaded": files_uploaded_count,
                    "mappings": pdf_to_json_map_decoded,
                    "comparisonFiles": comparison_files_with_names,
                    # "detailed_log_summary": log_messages_aggregator,  # Aggiungiamo un riassunto dei log alla risposta per ora
                }
            ),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as e:
        add_log(f"General error in get_document_list: {str(e)}", level="error")
        import traceback

        add_log(f"Traceback: {traceback.format_exc()}", level="error")
        # Anche qui, potremmo provare a inviare i log accumulati all'entity
        return func.HttpResponse(
            json.dumps(
                {
                    "error": f"Error scanning for documents: {str(e)}",
                    "detailed_logs": log_messages_aggregator,
                }
            ),
            status_code=500,
            mimetype="application/json",
        )


def create_process_lock(operation_id, mode, token_count=0):
    """
    Create a local process lock file with timestamp to track real processing time

    Args:
        operation_id: ID univoco dell'operazione (instance_id)
        mode: 'extraction' o 'comparison'
        token_count: numero di token da processare

    Returns:
        path del file di lock creato
    """
    lock_id = f"{operation_id}_{mode}"
    lock_file_name = f"{lock_file_path}_{lock_id}.lock"

    lock_data = {
        "operation_id": operation_id,
        "mode": mode,
        "token_count": token_count,
        "created_at": get_current_timestamp(),
        "status": "running",
    }

    try:
        with open(lock_file_name, "w") as f:
            json.dump(lock_data, f)
        logger.info(f"Created process lock file at {lock_file_name}")
        return lock_file_name
    except Exception as e:
        logger.error(f"Failed to create process lock file: {str(e)}")
        return None


def get_processing_time_from_lock(lock_file_path):
    """
    Calculate real processing time using the lock file's timestamp

    Args:
        lock_file_path: Percorso del file di lock da leggere

    Returns:
        dict con informazioni sul tempo di elaborazione
    """
    try:
        if not os.path.exists(lock_file_path):
            logger.warning(f"Lock file {lock_file_path} does not exist")
            return {"elapsed_time": 0, "error": "Lock file not found"}

        with open(lock_file_path, "r") as f:
            lock_data = json.load(f)

        created_at = lock_data.get("created_at")
        if not created_at:
            return {"elapsed_time": 0, "error": "Missing creation timestamp"}

        current_time = get_current_timestamp()
        elapsed_time = calculate_seconds_between(created_at, current_time)

        # Aggiorna il file di lock con il nuovo stato e tempo di elaborazione
        lock_data["last_checked_at"] = current_time
        lock_data["elapsed_time"] = elapsed_time

        with open(lock_file_path, "w") as f:
            json.dump(lock_data, f)

        return {
            "elapsed_time": elapsed_time,
            "created_at": created_at,
            "current_time": current_time,
            "token_count": lock_data.get("token_count", 0),
            "mode": lock_data.get("mode", "unknown"),
        }
    except Exception as e:
        logger.error(f"Error reading lock file {lock_file_path}: {str(e)}")
        return {"elapsed_time": 0, "error": str(e)}


def calculate_truncated_mean(values, truncate_percent=0.1):
    """
    Calcola la media troncata, rimuovendo una percentuale specificata dei valori più bassi e più alti.

    Args:
        values: Lista di valori numerici
        truncate_percent: Percentuale (0-0.5) dei valori da rimuovere da ciascuna estremità

    Returns:
        Media troncata o None se non ci sono valori sufficienti
    """
    if (
        not values or len(values) < 3
    ):  # Serve un minimo di dati per una media troncata sensata
        return None

    # Ordina i valori
    sorted_values = sorted(values)

    # Calcola quanti elementi rimuovere da ciascuna estremità
    cut_count = int(len(values) * truncate_percent)

    # Seleziona il sottoinsieme troncato
    truncated_values = sorted_values[cut_count : len(sorted_values) - cut_count]

    # Calcola e restituisci la media
    if truncated_values:
        return sum(truncated_values) / len(truncated_values)
    else:
        return None


def track_request_with_appinsights(name, success=True, properties=None, metrics=None):
    """Traccia una richiesta in Application Insights"""
    try:
        from opentelemetry import trace
        from opentelemetry.trace.status import Status, StatusCode

        # Ottieni il tracer
        tracer = trace.get_tracer(__name__)

        # Prepara gli attributi
        attributes = {}
        if properties:
            for k, v in properties.items():
                attributes[k] = str(v) if v is not None else "null"

        if metrics:
            for k, v in metrics.items():
                if isinstance(v, (int, float)):
                    attributes[f"metric_{k}"] = v

        # Crea e completa lo span
        with tracer.start_as_current_span(name, attributes=attributes) as span:
            if success:
                span.set_status(Status(StatusCode.OK))
            else:
                span.set_status(Status(StatusCode.ERROR))

        logger.info(f"AppInsights: Tracked {name} - Success: {success}")
    except Exception as e:
        logger.warning(f"AppInsights: Errore durante il tracking di {name}: {str(e)}")


def track_dependency_with_appinsights(
    name, target, success=True, properties=None, duration_ms=None
):
    """Traccia una dipendenza in Application Insights"""
    try:
        from opentelemetry import trace

        # Ottieni il tracer
        tracer = trace.get_tracer(__name__)

        # Prepara gli attributi
        attributes = {"target": target}

        if properties:
            for k, v in properties.items():
                attributes[k] = str(v) if v is not None else "null"

        if duration_ms is not None:
            attributes["duration_ms"] = duration_ms

        # Crea e completa lo span
        with tracer.start_as_current_span(
            f"dependency:{name}", attributes=attributes
        ) as span:
            if not success:
                span.record_exception(Exception(f"Dependency {name} failed"))

        logger.info(f"AppInsights: Tracked dependency {name} - Success: {success}")
    except Exception as e:
        logger.warning(
            f"AppInsights: Errore durante il tracking di dipendenza {name}: {str(e)}"
        )


def track_exception_with_appinsights(exception, properties=None):
    """Traccia un'eccezione in Application Insights"""
    try:
        from opentelemetry import trace
        from opentelemetry.trace.status import Status, StatusCode

        # Ottieni il tracer corrente
        tracer = trace.get_tracer(__name__)
        current_span = trace.get_current_span()

        # Registra l'eccezione nello span corrente
        if current_span:
            attributes = {}
            if properties:
                for k, v in properties.items():
                    attributes[k] = str(v) if v is not None else "null"

            # Registra l'eccezione
            current_span.record_exception(exception, attributes=attributes)
            current_span.set_status(Status(StatusCode.ERROR), str(exception))

        logger.info(
            f"AppInsights: Tracked exception {type(exception).__name__}: {str(exception)}"
        )
    except Exception as e:
        logger.warning(
            f"AppInsights: Errore durante il tracking dell'eccezione: {str(e)}"
        )


def track_metric_with_appinsights(name, value, properties=None):
    """Traccia una metrica in Application Insights"""
    try:
        from opentelemetry import metrics

        # Ottieni il meter
        meter = metrics.get_meter(__name__)

        # Crea o ottieni il contatore
        counter = meter.create_counter(name)

        # Registra il valore
        attributes = {}
        if properties:
            for k, v in properties.items():
                attributes[k] = str(v) if v is not None else "null"

        counter.add(value, attributes=attributes)

        logger.info(f"AppInsights: Tracked metric {name} = {value}")
    except Exception as e:
        logger.warning(
            f"AppInsights: Errore durante il tracking della metrica {name}: {str(e)}"
        )


# Funzione per dividere il testo in chunks presa da requirements_analyzer
def split_into_chunks(
    text: str,
    chunk_size: int = 6000,
    overlap: int = 1000,
    model: str = "",  # unused — kept for backward compatibility
) -> List[str]:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", " ", ""],
    )
    return splitter.split_text(text)


@app.route(route="delete-files", methods=["DELETE"])
def delete_files(req: func.HttpRequest) -> func.HttpResponse:
    """
    Proxies the delete-files request to the backend VM service.
    """
    logger.info("Proxying request for file deletion.")

    # Backend VM endpoint for deleting files
    target_url = f"http://{IPVMAI}:2025/delete-files"

    try:
        # Get the original request body to forward it
        body = req.get_body()
    except Exception as e:
        logger.warning(f"Could not get request body for proxying: {str(e)}")
        body = None

    try:
        # Forward the DELETE request with its body to the VM
        resp = requests.delete(
            url=target_url, data=body, headers={"Content-Type": "application/json"}
        )

        # Return the response from the VM back to the client
        return func.HttpResponse(
            body=resp.content,
            status_code=resp.status_code,
            mimetype=resp.headers.get("content-type", "application/json"),
        )

    except requests.exceptions.RequestException as e:
        logger.error(f"Error proxying DELETE request to {target_url}: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": f"Error forwarding request to backend: {str(e)}"}),
            status_code=502,  # Bad Gateway
            mimetype="application/json",
        )
