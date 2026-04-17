"""
Local-filesystem storage provider — drop-in replacement for AzureBlobStorageProvider.

Replaces the Azure Blob Storage SDK with a local-filesystem backend.
The public API (upload_blob, download_blob, list_files, delete_blob) is preserved.
"""
import logging
import os
from pathlib import Path
from typing import Optional

from utils import singleton

_log = logging.getLogger(__name__)


class LocalStorageProvider:
    """CRUD operations on the local filesystem, mimicking AzureBlobStorageProvider."""

    def __init__(self, container_name: str, storage_root: str):
        self.container_name = container_name
        self._base = Path(storage_root) / container_name

    @classmethod
    def from_settings(cls):
        storage_root = os.getenv("LOCAL_STORAGE_PATH", "/opt/sunnitai-be/storage")
        container = os.getenv("BLOB_CONTAINER_NAME", "sunnitai")
        return cls(container_name=container, storage_root=storage_root)

    def _path(self, blob_name: str) -> Path:
        return self._base / blob_name

    def upload_blob(self, blob_name: str, data: bytes, overwrite: bool = True) -> str:
        p = self._path(blob_name)
        if not overwrite and p.exists():
            raise FileExistsError(f"Blob already exists: {p}")
        p.parent.mkdir(parents=True, exist_ok=True)
        content = data.encode("utf-8") if isinstance(data, str) else data
        p.write_bytes(content)
        _log.debug("Stored blob: %s", p)
        return p.as_uri()

    def download_blob(self, blob_name: str) -> bytes:
        p = self._path(blob_name)
        if not p.exists():
            raise FileNotFoundError(
                f"Blob {blob_name!r} not found in container {self.container_name!r}"
            )
        return p.read_bytes()

    def list_files(self, prefix: Optional[str] = None) -> list[str]:
        if not self._base.exists():
            return []
        results = []
        for path in sorted(self._base.rglob("*")):
            if path.is_file():
                rel = path.relative_to(self._base).as_posix()
                if prefix is None or rel.startswith(prefix):
                    results.append(rel)
        return results

    def delete_blob(self, blob_name: str) -> None:
        p = self._path(blob_name)
        if p.exists():
            p.unlink()
            _log.debug("Deleted blob: %s", p)


# Keep old class name for import compatibility
AzureBlobStorageProvider = LocalStorageProvider


def get_blob_storage_provider() -> LocalStorageProvider:
    """Restituisce il provider (lazy loading)."""
    return LocalStorageProvider.from_settings()
