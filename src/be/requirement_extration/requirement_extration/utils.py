import hashlib
import json
import os
from pathlib import Path
import logging
from threading import Lock
from rich.console import Console
from rich.table import Table
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError

console = Console()

logger = logging.getLogger(__name__)


def compute_file_hash(file_path: str) -> str:
    hash_md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def get_python_path() -> str:
    import sys

    # Percorso dell'interprete Python del virtual environment corrente
    return sys.executable


class PDFToJsonMapping:
    _instance = None
    _lock = Lock()

    def __init__(self):
        self.mapping = {}
        self.comparisonMapping = {}
        self.build_mapping()

    def build_mapping(self):
        """
        Costruisce una mappa che associa il nome normalizzato del PDF (senza estensione)
        al nome del file JSON (basato sull'hash del PDF) presente nella cartella output.
        La mappa viene ricostruita esaminando tutti i file PDF in ./tmp/.
        """
        tmp_dir = Path("./tmp")
        output_dir = Path("./output")
        self.mapping.clear()
        logger.info("Costruzione della mappa PDF -> JSON")
        for pdf_file in tmp_dir.glob("*.pdf"):
            normalized_name = pdf_file.name
            base_name = normalized_name.replace(".pdf", "")
            file_hash = compute_file_hash(str(pdf_file))
            json_filename = f"{file_hash}.json"
            json_path = output_dir / json_filename
            if json_path.exists():
                logger.info(
                    f"Per {normalized_name} è presente il file JSON {json_filename}"
                )
                self.mapping[base_name] = json_filename
            else:
                logger.warning(
                    f"Attenzione: per {normalized_name} il file JSON {json_filename} non esiste ancora."
                )

    @classmethod
    def get_instance(cls):
        logger.info("Richiesta di istanza di PDFToJsonMapping")
        with cls._lock:
            if cls._instance is None:
                cls._instance = PDFToJsonMapping()
        return cls._instance


# Helper per salvare il mapping su file (persistenza)
def save_pdf_mapping(mapping: dict, filename: str = "pdf_mapping.json") -> None:
    """Persist the current PDF→JSON mapping and comparison mapping to disk.

    The file will be saved as a JSON object with the following structure:
    {
        "mapping": { ... },            # PDF base name  → hashed json filename
        "comparisonMapping": { ... }   # <pdf1_pdf2> key → comparison filename
    }

    This function is backward-compatible with older callers that only supply
    the plain *mapping* dictionary.  It retrieves the *comparisonMapping*
    from the singleton instance if available so that information is not lost
    across saves.
    """
    try:
        # Recupera anche la comparisonMapping attuale, se l'istanza esiste già
        try:
            comparison_mapping = PDFToJsonMapping.get_instance().comparisonMapping
        except Exception:
            # If the singleton is not yet initialised or any other issue arises
            comparison_mapping = {}

        data = {
            "mapping": mapping,
            "comparisonMapping": comparison_mapping,
        }

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Mapping salvato su {filename} (v2 schema)")
    except Exception as e:
        logger.error(f"Errore nel salvataggio del mapping: {str(e)}")


# Helper per caricare il mapping da file
def load_pdf_mapping(filename: str = "pdf_mapping.json") -> dict:
    """Load mapping from *filename*.

    Returns the *mapping* part (plain dict pdf→hash) regardless of whether the
    file is using the new structured schema or the legacy one.
    """
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Handle both new structured schema and legacy flat dict
        if isinstance(data, dict) and "mapping" in data:
            mapping = data.get("mapping", {})
        else:
            mapping = data  # legacy format was already the mapping itself

        logger.info(f"Mapping caricato da {filename} (trovate {len(mapping)} voci)")
        return mapping
    except Exception as e:
        logger.warning(f"Mapping non caricato: {str(e)}")
        return {}


# def print_mapping(mapping: dict):
#     table = Table(title="PDF → JSON Mapping")
#     table.add_column("PDF Name", style="cyan", no_wrap=True)
#     table.add_column("JSON File", style="magenta")
#     if mapping:
#         for pdf_name, json_file in mapping.items():
#             table.add_row(pdf_name, json_file)
#     else:
#         table.add_row("(vuoto)", "(nessuna voce)")
#     console.print(table)


def print_mapping(mapping):
    for pdf_name, json_name in mapping.items():
        print(f"{pdf_name} -> {json_name}")


def upload_to_blob(category: str, file_path: Path, blob_name: str = None):
    blob_service = BlobServiceClient.from_connection_string(
        os.getenv("CONNECTION_STRING"), logging_enable=False
    )
    container_name = os.getenv("CONTAINER_NAME")
    container_client = blob_service.get_container_client(container_name)
    BLOB_PREFIXES = {
        "requirements": "requirements",
        "sanctions": "sanctions",
        "subjects": "subjects",
        "comparisons": "comparisons",
        "amendments": "amendments",
        "versionings": "versionings",
        "implementations": "implementations",
    }
    prefix = BLOB_PREFIXES.get(category)
    name = blob_name or file_path.name
    if not prefix:
        raise ValueError(f"Categoria blob sconosciuta: {category}")
    with open(file_path, "rb") as data:
        container_client.upload_blob(f"{prefix}/{name}", data, overwrite=True)
    logger.info(f"[BLOB] Caricato {file_path} → {prefix}/{name}")


def check_file_exists(file_path: str) -> bool:
    """Check if a file exists on the filesystem."""
    return Path(file_path).exists() and Path(file_path).stat().st_size > 0


def blob_exists(category: str, blob_name: str) -> bool:
    """Check if a blob exists in Azure Blob Storage."""
    try:
        blob_service = BlobServiceClient.from_connection_string(
            os.getenv("CONNECTION_STRING"), logging_enable=False
        )
        container_name = os.getenv("CONTAINER_NAME")
        container_client = blob_service.get_container_client(container_name)

        BLOB_PREFIXES = {
            "versionings": "versionings",
            "implementations": "implementations",
            "requirements": "requirements",
            "sanctions": "sanctions",
            "subjects": "subjects",
            "comparisons": "comparisons",
            "amendments": "amendments",
        }
        prefix = BLOB_PREFIXES.get(category)
        if not prefix:
            raise ValueError(f"Categoria blob sconosciuta: {category}")

        blob_path = f"{prefix}/{blob_name}"
        blob_client = container_client.get_blob_client(blob_path)
        return blob_client.exists()
    except Exception as e:
        logger.error(
            f"Errore durante il controllo del blob {category}/{blob_name}: {str(e)}"
        )
        return False


def download_from_blob(category: str, blob_name: str, destination_path: Path) -> bool:
    """Download a blob from Azure Blob Storage to the local filesystem."""
    try:
        blob_service = BlobServiceClient.from_connection_string(
            os.getenv("CONNECTION_STRING"), logging_enable=False
        )
        container_name = os.getenv("CONTAINER_NAME")
        container_client = blob_service.get_container_client(container_name)

        BLOB_PREFIXES = {
            "requirements": "requirements",
            "sanctions": "sanctions",
            "subjects": "subjects",
            "comparisons": "comparisons",
            "amendments": "amendments",
            "versionings": "versionings",
            "implementations": "implementations",
        }
        prefix = BLOB_PREFIXES.get(category)
        if not prefix:
            raise ValueError(f"Categoria blob sconosciuta: {category}")

        blob_path = f"{prefix}/{blob_name}"
        blob_client = container_client.get_blob_client(blob_path)

        # Create directories if they don't exist
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        # Download the blob to a local file
        with open(destination_path, "wb") as download_file:
            download_file.write(blob_client.download_blob().readall())

        logger.info(f"[BLOB] Scaricato {blob_path} → {destination_path}")
        return True
    except ResourceNotFoundError:
        logger.warning(f"[BLOB] Blob non trovato: {category}/{blob_name}")
        return False
    except Exception as e:
        logger.error(
            f"[BLOB] Errore durante il download del blob {category}/{blob_name}: {str(e)}"
        )
        return False

# === File-system house-keeping =================================================

DIRECTORIES_TO_CLEAN = [
    "out",
    "out_analisi",
    "out_flat",
    "out_parser",
    "out_schema_attuativo",
    "output",
    "output_internal",
]


def cleanup_local_json_files(directories: list[str] | None = None) -> None:
    """Remove all *.json files from the specified *directories* (recursively).

    If *directories* is *None* the default set defined in
    ``DIRECTORIES_TO_CLEAN`` is used.  Errors during deletion are logged but do
    **not** raise, in modo da non interrompere il flusso principale.
    """
    dirs = directories or DIRECTORIES_TO_CLEAN
    for dir_name in dirs:
        dir_path = Path(dir_name)
        if not dir_path.exists():
            continue
        for json_path in dir_path.rglob("*.json"):
            try:
                json_path.unlink(missing_ok=True)
                logger.debug(f"[CLEANUP] Deleted {json_path}")
            except Exception as exc:
                logger.warning(f"[CLEANUP] Could not delete {json_path}: {exc}")
