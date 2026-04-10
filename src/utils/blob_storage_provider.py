"""
Provider per Azure Blob Storage.

Fornisce operazioni CRUD per file su Azure Blob Storage
usando le credenziali centralizzate.

Esempio d'uso:
    from utils.blob_storage_provider import azure_blob_storage_provider
    
    # Upload di un file
    url = azure_blob_storage_provider.upload_blob("path/file.pdf", data_bytes)
    
    # Download
    content = azure_blob_storage_provider.download_blob("path/file.pdf")
    
    # Lista file
    files = azure_blob_storage_provider.list_files(prefix="documents/")
    
    # Eliminazione
    azure_blob_storage_provider.delete_blob("path/file.pdf")
"""
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

from core.settings import get_settings
from utils.azure_credentials import azure_client_credentials

from utils import singleton

class AzureBlobStorageProvider:
    """Client per operazioni su Azure Blob Storage."""

    def __init__(self, container_name: str, account_name: str):
        """
        Inizializza il provider.
        
        Args:
            container_name: Nome del container blob
            account_name: Nome dell'account storage
        """
        self.container_name = container_name
        self.account_name = account_name
        account_url = f"https://{self.account_name}.blob.core.windows.net"
        
        self.service_client = BlobServiceClient(
            account_url,
            credential=azure_client_credentials.get_credential()
        )
        self.container_client = self.service_client.get_container_client(self.container_name)

    @classmethod
    def from_settings(cls):
        """Factory method che crea il provider usando i settings dell'app."""
        settings = get_settings()
        return cls(
            container_name=settings.azure_storage.container_name,
            account_name=settings.azure_storage.account_name,
        )

    def upload_blob(self, blob_name: str, data: bytes, overwrite: bool = True) -> str:
        """
        Carica dati su Azure Blob Storage.
        
        Args:
            blob_name: Path del blob (es: "folder/file.pdf")
            data: Contenuto binario da caricare
            overwrite: Se True, sovrascrive blob esistente
        
        Returns:
            URL del blob caricato
        """
        blob_client = self.container_client.get_blob_client(blob_name)
        blob_client.upload_blob(data, overwrite=overwrite)
        return blob_client.url

    def download_blob(self, blob_name: str) -> bytes:
        """
        Scarica il contenuto di un blob.
        
        Args:
            blob_name: Path del blob da scaricare
        
        Returns:
            Contenuto del blob come bytes
        
        Raises:
            FileNotFoundError: Se il blob non esiste
        """
        try:
            blob_client = self.container_client.get_blob_client(blob_name)
            return blob_client.download_blob().readall()
        except ResourceNotFoundError:
            raise FileNotFoundError(f"Il blob {blob_name} non esiste nel container {self.container_name}")

    def list_files(self, prefix: str = None) -> list[str]:
        """
        Elenca i file nel container.
        
        Args:
            prefix: Prefisso per filtrare (es: "documents/")
        
        Returns:
            Lista dei nomi dei blob
        """
        return [blob.name for blob in self.container_client.list_blobs(name_starts_with=prefix)]

    def delete_blob(self, blob_name: str):
        """
        Elimina un blob.
        
        Args:
            blob_name: Path del blob da eliminare
        """
        blob_client = self.container_client.get_blob_client(blob_name)
        blob_client.delete_blob()


# Istanza lazy - verrà creata solo quando importata e settings disponibili
def get_blob_storage_provider() -> AzureBlobStorageProvider:
    """Restituisce l'istanza del provider (lazy loading)."""
    return AzureBlobStorageProvider.from_settings()
