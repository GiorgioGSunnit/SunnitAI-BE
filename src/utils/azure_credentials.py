"""
Gestione centralizzata delle credenziali Azure.

Usa DefaultAzureCredential che supporta automaticamente:
- Managed Identity (in produzione su Azure)
- Azure CLI (in sviluppo locale)
- Environment variables (AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID)

Esempio d'uso:
    from utils.azure_credentials import azure_client_credentials
    
    # Ottenere un token OAuth
    token = azure_client_credentials.get_token("https://management.azure.com/.default")
    
    # Per connessioni ODBC al database
    token_struct = azure_client_credentials.get_token_struct_for_odbc()
    
    # Ottenere l'oggetto credential per altri client Azure
    credential = azure_client_credentials.get_credential()
"""
import logging
import os
import struct

from azure.identity import DefaultAzureCredential

from utils import singleton

logger = logging.getLogger(__name__)


@singleton
class AzureClientCredentials:
    """
    Singleton per gestire le credenziali Azure.
    
    Utilizza DefaultAzureCredential che tenta automaticamente diversi metodi
    di autenticazione in ordine di priorità.
    """

    def __init__(self):
        logger.info("🔑 Initializing DefaultAzureCredential...")
        # Log env vars che influenzano l'autenticazione (senza valori sensibili)
        logger.info(f"🔑 AZURE_CLIENT_ID set: {bool(os.getenv('AZURE_CLIENT_ID'))}")
        logger.info(f"🔑 AZURE_CLIENT_SECRET set: {bool(os.getenv('AZURE_CLIENT_SECRET'))}")
        logger.info(f"🔑 AZURE_TENANT_ID set: {bool(os.getenv('AZURE_TENANT_ID'))}")
        logger.info(f"🔑 MSI_ENDPOINT set: {bool(os.getenv('MSI_ENDPOINT'))}")
        logger.info(f"🔑 IDENTITY_ENDPOINT set: {bool(os.getenv('IDENTITY_ENDPOINT'))}")
        
        try:
            self.credential = DefaultAzureCredential()
            logger.info("🔑 DefaultAzureCredential initialized successfully")
        except Exception as e:
            logger.error(f"🔑 Failed to initialize DefaultAzureCredential: {e}")
            raise

    def get_token(self, scope: str) -> str:
        """
        Ottiene un token OAuth per lo scope specificato.
        
        Args:
            scope: Lo scope Azure per cui ottenere il token
                   Es: "https://management.azure.com/.default"
        
        Returns:
            Il token come stringa
        """
        return self.credential.get_token(scope).token

    def get_token_struct_for_odbc(self) -> bytes:
        """
        Genera un token formattato per connessioni ODBC ad Azure SQL.
        
        Il formato è richiesto dal driver ODBC per l'autenticazione
        tramite Azure AD token.
        
        Returns:
            Token codificato come struct bytes per ODBC
        """
        token_bytes = self.get_token("https://database.windows.net/.default").encode("UTF-16-LE")
        token_struct = struct.pack(f'<I{len(token_bytes)}s', len(token_bytes), token_bytes)
        return token_struct

    def get_credential(self) -> DefaultAzureCredential:
        """
        Restituisce l'oggetto credential per uso con altri client Azure.
        
        Returns:
            L'istanza DefaultAzureCredential
        """
        return self.credential


# Istanza singleton pronta all'uso
azure_client_credentials = AzureClientCredentials()
