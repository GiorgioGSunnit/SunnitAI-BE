"""
Local-filesystem storage client — drop-in replacement for Azure Blob Storage.

Replaces the Azure SDK implementation with a local-filesystem backend so the
app can run on a plain server without any Azure credentials.

The same public API is preserved so all callers (job_store, function_app,
call_fast_api, extration_utils) continue to work unchanged.

Storage root: LOCAL_STORAGE_PATH env var (default: /opt/sunnitai-be/storage).

Layout mirrors the original blob container layout:
    <root>/
        <container>/
            conf/jobs/<job_id>.json
            conf/locks/<lock_name>
            out/requirements/<file>
            out/comparisons/<file>
            debug/<file>
            <pdf_name>.pdf
            ...
"""
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

_log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

PREFIX_OUT = "out"
PREFIX_CONF = "conf"
PREFIX_DEBUG = "debug"


def _get_storage_root() -> Path:
    return Path(os.getenv("LOCAL_STORAGE_PATH", "/opt/sunnitai-be/storage"))


def _get_container_name() -> str:
    return os.getenv("BLOB_CONTAINER_NAME", "sunnitai")


# ── Minimal blob-SDK-compatible types ─────────────────────────────────────────

@dataclass
class _BlobItem:
    """Mimics azure.storage.blob.BlobItem (only .name is used)."""
    name: str


class _DownloadStream:
    """Mimics the return value of BlobClient.download_blob()."""

    def __init__(self, data: bytes):
        self._data = data

    def readall(self) -> bytes:
        return self._data


class LocalBlobClient:
    """Local-filesystem replacement for azure.storage.blob.BlobClient."""

    def __init__(self, root: Path, container: str, blob_path: str):
        self._file = root / container / blob_path

    @property
    def url(self) -> str:
        return self._file.as_uri()

    def exists(self) -> bool:
        return self._file.exists()

    def upload_blob(self, data, overwrite: bool = True) -> None:
        if not overwrite and self._file.exists():
            raise FileExistsError(f"Blob already exists: {self._file}")
        self._file.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, (str, bytes)):
            content = data.encode("utf-8") if isinstance(data, str) else data
        else:
            # File-like object
            content = data.read()
        self._file.write_bytes(content)
        _log.debug("Uploaded blob: %s", self._file)

    def download_blob(self) -> _DownloadStream:
        if not self._file.exists():
            # Raise compatible error (callers catch ResourceNotFoundError or Exception)
            raise FileNotFoundError(f"Blob not found: {self._file}")
        return _DownloadStream(self._file.read_bytes())

    def delete_blob(self) -> None:
        if self._file.exists():
            self._file.unlink()
            _log.debug("Deleted blob: %s", self._file)


class LocalContainerClient:
    """Local-filesystem replacement for azure.storage.blob.ContainerClient."""

    def __init__(self, root: Path, container: str):
        self._root = root
        self._container = container
        self._base = root / container

    def get_blob_client(self, blob_path: str) -> LocalBlobClient:
        return LocalBlobClient(self._root, self._container, blob_path)

    def list_blobs(self, name_starts_with: str = None) -> Iterator[_BlobItem]:
        if not self._base.exists():
            return
        for path in sorted(self._base.rglob("*")):
            if path.is_file():
                rel = path.relative_to(self._base).as_posix()
                if name_starts_with is None or rel.startswith(name_starts_with):
                    yield _BlobItem(name=rel)


class LocalBlobServiceClient:
    """Local-filesystem replacement for azure.storage.blob.BlobServiceClient."""

    def __init__(self, root: Path):
        self._root = root

    def get_container_client(self, container: str) -> LocalContainerClient:
        return LocalContainerClient(self._root, container)


# ── Public helpers (same signatures as the Azure version) ─────────────────────

def get_blob_service_client() -> LocalBlobServiceClient:
    return LocalBlobServiceClient(_get_storage_root())


def get_container_client() -> LocalContainerClient:
    return LocalContainerClient(_get_storage_root(), _get_container_name())


def get_blob_client(blob_path: str) -> LocalBlobClient:
    return LocalBlobClient(_get_storage_root(), _get_container_name(), blob_path)


def is_available() -> bool:
    """Always True — local filesystem is always available."""
    return True


# ── Path helpers ──────────────────────────────────────────────────────────────

def path_pdf(filename: str) -> str:
    return filename


def path_out_requirements(blob_name: str) -> str:
    return f"{PREFIX_OUT}/requirements/{blob_name}"


def path_out_comparisons(blob_path: str) -> str:
    return f"{PREFIX_OUT}/comparisons/{blob_path}"


def path_conf(blob_name: str) -> str:
    return f"{PREFIX_CONF}/{blob_name}"


def path_locks(blob_name: str) -> str:
    return f"{PREFIX_CONF}/locks/{blob_name}"


def path_job(job_id: str) -> str:
    return f"{PREFIX_CONF}/jobs/{job_id}.json"


# ── Retro-compatibility aliases ────────────────────────────────────────────────

def path_cdp(filename: str) -> str:
    return path_pdf(filename)


def path_cdp_ext(filename: str) -> str:
    return f"{PREFIX_OUT}/{filename}"


def path_requirements(blob_name: str) -> str:
    return path_out_requirements(blob_name)


def path_comparisons(blob_path: str) -> str:
    return path_out_comparisons(blob_path)


# ── Debug log upload ───────────────────────────────────────────────────────────

def upload_debug_log(filename: str, content: str) -> bool:
    try:
        blob_path = f"{PREFIX_DEBUG}/{filename}"
        get_blob_client(blob_path).upload_blob(content.encode("utf-8"), overwrite=True)
        _log.info("Debug log saved: %s", blob_path)
        return True
    except Exception as exc:
        _log.warning("Failed to save debug log %s: %s", filename, exc)
        return False
