"""
Client Azure Blob Storage con DefaultAzureCredential (Managed Identity).
Usa sempre lo storage reale (sacdpdev001) per i dati app.
AzureWebJobsStorage (Azurite) e' gestito separatamente dal runtime Functions.

Convenzione path nel container ai-audit-poc-sa:
- *.pdf (root)     : PDF caricati
- out/requirements/ : output estrazione (json, xlsx, flattened)
- out/comparisons/  : output comparazioni
- conf/             : sum.json, tokens_per_second.json, locks/
- debug/            : log di debug da lex_package
"""
import logging
import os

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient

# Path prefixes
PREFIX_OUT = "out"
PREFIX_CONF = "conf"


def _get_account_name() -> str:
    return (
        os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
        or os.getenv("AzureWebJobsStorage__accountName")
        or "sacdpdev001"
    )


def _get_container_name() -> str:
    return os.getenv("BLOB_CONTAINER_NAME") or "ai-audit-poc-sa"


def get_blob_service_client() -> BlobServiceClient:
    """BlobServiceClient via DefaultAzureCredential (Managed Identity)."""
    account = _get_account_name()
    url = f"https://{account}.blob.core.windows.net"
    return BlobServiceClient(account_url=url, credential=DefaultAzureCredential())


def get_container_client() -> ContainerClient:
    """Container client per il single container."""
    return get_blob_service_client().get_container_client(_get_container_name())


def get_blob_client(blob_path: str) -> BlobClient:
    """BlobClient per path nel container (es: out/requirements/doc.json)."""
    return get_container_client().get_blob_client(blob_path)


# --- Path helpers ---

def path_pdf(filename: str) -> str:
    """PDF in root del container."""
    return filename


def path_out_requirements(blob_name: str) -> str:
    """Output estrazione: out/requirements/{blob_name}"""
    return f"{PREFIX_OUT}/requirements/{blob_name}"


def path_out_comparisons(blob_path: str) -> str:
    """Output comparazioni: out/comparisons/{blob_path}"""
    return f"{PREFIX_OUT}/comparisons/{blob_path}"


def path_conf(blob_name: str) -> str:
    """Config/stato: conf/{blob_name}"""
    return f"{PREFIX_CONF}/{blob_name}"


def path_locks(blob_name: str) -> str:
    """Lock distribuiti: conf/locks/{blob_name}"""
    return f"{PREFIX_CONF}/locks/{blob_name}"


def path_job(job_id: str) -> str:
    """Stato job condiviso (singolo servizio / mono-utenza): conf/jobs/{job_id}.json"""
    return f"{PREFIX_CONF}/jobs/{job_id}.json"


# --- Retrocompatibilita' (alias per codice esistente) ---

def path_cdp(filename: str) -> str:
    """Alias: PDF interni -> root."""
    return path_pdf(filename)


def path_cdp_ext(filename: str) -> str:
    """Alias retrocompatibilita': output/dati esterni -> out/{filename}."""
    return f"{PREFIX_OUT}/{filename}"


def path_requirements(blob_name: str) -> str:
    """Alias: requirements -> out/requirements/"""
    return path_out_requirements(blob_name)


def path_comparisons(blob_path: str) -> str:
    """Alias: comparisons -> out/comparisons/"""
    return path_out_comparisons(blob_path)


def is_available() -> bool:
    """True se storage e' configurato."""
    return bool(_get_account_name() and _get_container_name())


# --- Debug log upload ---

PREFIX_DEBUG = "debug"

_log = logging.getLogger(__name__)


def upload_debug_log(filename: str, content: str) -> bool:
    """Carica un debug log su blob storage in debug/{filename}.
    Ritorna True se upload riuscito, False altrimenti (non blocca il flusso).
    """
    try:
        blob_path = f"{PREFIX_DEBUG}/{filename}"
        get_blob_client(blob_path).upload_blob(
            content.encode("utf-8"), overwrite=True
        )
        _log.info("Debug log uploaded: %s", blob_path)
        return True
    except Exception as exc:
        _log.warning("Failed to upload debug log %s: %s", filename, exc)
        return False
