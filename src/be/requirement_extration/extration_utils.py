import hashlib
import json
import os
import sys
from pathlib import Path
import logging
from threading import Lock
from rich.console import Console
from rich.table import Table
try:
    from azure.core.exceptions import ResourceNotFoundError
except ImportError:
    ResourceNotFoundError = FileNotFoundError  # type: ignore

# Usa blob_storage_client centralizzato (Managed Identity, no CONNECTION_STRING)
_app_src = Path("/app/src")
if _app_src.exists() and str(_app_src) not in sys.path:
    sys.path.insert(0, str(_app_src))
try:
    from utils import blob_storage_client as bsc
except ImportError:
    bsc = None

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
        Prima da ./tmp/ e ./output/, poi integra con il contenuto di pdf_mapping.json se esiste,
        così dopo un restart il mapping è disponibile anche senza file locali.
        """
        tmp_dir = Path("./tmp")
        output_dir = Path("./output")
        self.mapping.clear()
        self.comparisonMapping = {}
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
        # Carica mapping persistito così il download Excel funziona dopo restart (anche senza ./tmp)
        mapping_file = Path("pdf_mapping.json")
        if mapping_file.exists():
            loaded_map, loaded_comp = load_pdf_mapping_full(str(mapping_file))
            if loaded_map:
                self.mapping.update(loaded_map)
                logger.info(f"Integrati {len(loaded_map)} nomi da {mapping_file}")
            if loaded_comp:
                self.comparisonMapping.update(loaded_comp)
                logger.info(f"Integrati {len(loaded_comp)} confronti da {mapping_file}")

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


def load_pdf_mapping_full(filename: str = "pdf_mapping.json") -> tuple:
    """Carica mapping e comparisonMapping da file. Ritorna (mapping, comparison_mapping)."""
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}, {}
        if "mapping" in data and isinstance(data.get("mapping"), dict):
            mapping = data["mapping"]
            comparison = data.get("comparisonMapping") or {}
        else:
            mapping = data  # formato legacy: tutto il dict è il mapping
            comparison = {}
        if not isinstance(comparison, dict):
            comparison = {}
        logger.info(f"Mapping full caricato da {filename}: {len(mapping)} voci mapping, {len(comparison)} confronti")
        return mapping, comparison
    except Exception as e:
        logger.warning(f"Mapping full non caricato: {str(e)}")
        return {}, {}


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
    BLOB_PREFIXES = {
        "requirements": "out/requirements",
        "sanctions": "out/sanctions",
        "subjects": "out/subjects",
        "comparisons": "out/comparisons",
        "amendments": "out/amendments",
        "versionings": "out/versionings",
        "implementations": "out/implementations",
    }
    prefix = BLOB_PREFIXES.get(category)
    name = blob_name or file_path.name
    if not prefix:
        raise ValueError(f"Categoria blob sconosciuta: {category}")
    blob_path = f"{prefix}/{name}"
    with open(file_path, "rb") as data:
        bsc.get_blob_client(blob_path).upload_blob(data, overwrite=True)
    logger.info(f"[BLOB] Caricato {file_path} → {blob_path}")


def check_file_exists(file_path: str) -> bool:
    """Check if a file exists on the filesystem."""
    return Path(file_path).exists() and Path(file_path).stat().st_size > 0


def blob_exists(category: str, blob_name: str) -> bool:
    """Check if a blob exists in Azure Blob Storage."""
    try:
        BLOB_PREFIXES = {
            "requirements": "out/requirements",
            "sanctions": "out/sanctions",
            "subjects": "out/subjects",
            "comparisons": "out/comparisons",
            "amendments": "out/amendments",
            "versionings": "out/versionings",
            "implementations": "out/implementations",
        }
        prefix = BLOB_PREFIXES.get(category)
        if not prefix:
            raise ValueError(f"Categoria blob sconosciuta: {category}")
        return bsc.get_blob_client(f"{prefix}/{blob_name}").exists()
    except Exception as e:
        logger.error(f"Errore controllo blob {category}/{blob_name}: {e}")
        return False


def download_from_blob(category: str, blob_name: str, destination_path: Path) -> bool:
    """Download a blob from Azure Blob Storage to the local filesystem."""
    try:
        BLOB_PREFIXES = {
            "requirements": "out/requirements",
            "sanctions": "out/sanctions",
            "subjects": "out/subjects",
            "comparisons": "out/comparisons",
            "amendments": "out/amendments",
            "versionings": "out/versionings",
            "implementations": "out/implementations",
        }
        prefix = BLOB_PREFIXES.get(category)
        if not prefix:
            raise ValueError(f"Categoria blob sconosciuta: {category}")

        blob_path = f"{prefix}/{blob_name}"
        blob_client = bsc.get_blob_client(blob_path)

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


def get_blob_bytes(category: str, blob_name: str):
    """
    Legge il contenuto di un blob come bytes. Nessuna scrittura su filesystem.
    Ritorna bytes se trovato, None altrimenti.
    """
    if bsc is None:
        return None
    try:
        BLOB_PREFIXES = {
            "requirements": "out/requirements",
            "sanctions": "out/sanctions",
            "subjects": "out/subjects",
            "comparisons": "out/comparisons",
            "amendments": "out/amendments",
            "versionings": "out/versionings",
            "implementations": "out/implementations",
        }
        prefix = BLOB_PREFIXES.get(category)
        if not prefix:
            return None
        blob_path = f"{prefix}/{blob_name}"
        blob_client = bsc.get_blob_client(blob_path)
        return blob_client.download_blob().readall()
    except ResourceNotFoundError:
        logger.debug(f"[BLOB] Blob non trovato: {category}/{blob_name}")
        return None
    except Exception as e:
        logger.warning(f"[BLOB] Errore lettura blob {category}/{blob_name}: {e}")
        return None


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
