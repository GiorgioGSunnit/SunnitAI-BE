"""
Bootstrap module - Inizializza l'ambiente caricando i secrets da Azure Key Vault.

Questo modulo deve essere importato ALL'INIZIO dell'applicazione,
PRIMA di qualsiasi altro import che usa os.getenv().

Uso:
    # All'inizio di function_app.py o main.py:
    import core.bootstrap  # Popola automaticamente le env vars
    
    # Ora il resto del codice può usare os.getenv() normalmente
    from lex_package.llm.factory import build_chat_model
    ...

Il modulo carica i settings da Azure Key Vault e popola le variabili
d'ambiente per compatibilità con il codice esistente.
"""
import os
import logging

logger = logging.getLogger(__name__)


def _populate_env_vars():
    """
    Carica i settings da Key Vault e popola le variabili d'ambiente.
    
    Questa funzione viene eseguita automaticamente all'import del modulo.
    """
    key_vault_name = os.getenv("AZURE_KEY_VAULT_NAME")
    tenant_id = os.getenv("AZURE_TENANT_ID")
    
    logger.info(f"🔧 Bootstrap starting - AZURE_KEY_VAULT_NAME={key_vault_name}")
    logger.info(f"🔧 Bootstrap - AZURE_TENANT_ID={tenant_id}")
    
    # Skip se AZURE_KEY_VAULT_NAME non è configurato (es. in test locali)
    if not key_vault_name:
        logger.warning(
            "AZURE_KEY_VAULT_NAME not set - skipping Key Vault bootstrap. "
            "Environment variables must be set manually."
        )
        return

    try:
        logger.info("🔧 Importing Settings class...")
        # Import lazy per evitare circular imports
        from core.settings import Settings
        
        logger.info("🔧 Creating Settings instance (connecting to Key Vault)...")
        settings = Settings()
        logger.info("🔧 Settings loaded successfully from Key Vault")
        
        # === Azure OpenAI ===
        if settings.azure_openai:
            _set_env("AZURE_OPENAI_API_KEY", settings.azure_openai.api_key)
            _set_env("AZURE_OPENAI_ENDPOINT", settings.azure_openai.endpoint)
            _set_env("AZURE_OPENAI_API_VERSION", settings.azure_openai.api_version)
            _set_env("AZURE_API_VERSION", settings.azure_openai.api_version)  # alias
            
            # Per lex_package/llm/factory.py
            _set_env("AZURE_OPENAI_DEPLOYMENT_NAME", settings.azure_openai.chat_model_deployment)
            _set_env("LLM_MODEL", settings.azure_openai.chat_model_deployment)
            _set_env("LLM_AZURE_DEPLOYMENT", settings.azure_openai.chat_model_deployment)
            
            # Fallback model (usa embedding come fallback se non c'è altro)
            _set_env("AZURE_OPENAI_DEPLOYMENT_NAME_BIS", settings.azure_openai.embedding_model_deployment)
            _set_env("LLM_FALLBACK_MODEL", settings.azure_openai.embedding_model_deployment)
            
            # Provider
            _set_env("LLM_PROVIDER", "azure_openai")
        else:
            # Fallback: carica direttamente dal KeyVault (pydantic_settings non mappa i nomi con -)
            logger.info("🔧 azure_openai not loaded via pydantic, trying direct KeyVault fetch...")
            _load_openai_from_keyvault_direct(key_vault_name)
        
        # === Azure Storage ===
        use_azurite = os.getenv("USE_AZURITE", "").lower() in ("1", "true", "yes")
        if use_azurite:
            _setup_azurite_env()
        elif settings.azure_storage:
            storage_url = f"https://{settings.azure_storage.account_name}.blob.core.windows.net"
            _set_env("AZURE_STORAGE_ACCOUNT_URL", storage_url)
            _set_env("AZURE_STORAGE_ACCOUNT_NAME", settings.azure_storage.account_name)
            _set_env("CONTAINER_NAME", settings.azure_storage.container_name)
            if settings.azure_storage.connection_string:
                _set_env("CONNECTION_STRING", settings.azure_storage.connection_string)
                _set_env("AzureWebJobsStorage", settings.azure_storage.connection_string)
            else:
                _set_env("AzureWebJobsStorage__accountName", settings.azure_storage.account_name)
        else:
            logger.info("🔧 azure_storage not loaded via pydantic, trying direct KeyVault fetch...")
            _load_storage_from_keyvault_direct(key_vault_name)
        
        # === Azure AI Search ===
        if settings.azure_ai_search:
            _set_env("SEARCH_KEY", settings.azure_ai_search.api_key)
            _set_env("AZURE_SEARCH_SERVICE_NAME", settings.azure_ai_search.service_name)
            if settings.azure_ai_search.index_name_exemplars:
                _set_env("AZURE_SEARCH_INDEX_EXEMPLARS", settings.azure_ai_search.index_name_exemplars)
            if settings.azure_ai_search.index_name_notes:
                _set_env("AZURE_SEARCH_INDEX_NOTES", settings.azure_ai_search.index_name_notes)
        
        # === Database ===
        if settings.db:
            _set_env("DB_SERVER", settings.db.server_name)
            _set_env("DB_NAME", settings.db.name)
            _set_env("DB_USER", settings.db.user)
            _set_env("DB_PASSWORD", settings.db.password)
        
        # === Redis ===
        if settings.redis_host:
            _set_env("REDIS_HOST", settings.redis_host)
        if settings.redis_port:
            _set_env("REDIS_PORT", str(settings.redis_port))
        
        # === Microsoft Graph ===
        _set_env("CLIENT_STATE", settings.client_state)
        if settings.notification_email:
            _set_env("NOTIFICATION_EMAIL", settings.notification_email)
        
        # === Service Principals ===
        if settings.azure_reader:
            _set_env("AZURE_READER__CLIENT_ID", settings.azure_reader.client_id)
            _set_env("AZURE_READER__CLIENT_SECRET", settings.azure_reader.client_secret)
        else:
            # Fallback: carica direttamente dal KeyVault (pydantic non mappa nomi con -)
            _load_service_principals_from_keyvault_direct(key_vault_name)
        
        if settings.azure_sender:
            _set_env("AZURE_SENDER__CLIENT_ID", settings.azure_sender.client_id)
            _set_env("AZURE_SENDER__CLIENT_SECRET", settings.azure_sender.client_secret)
        
        # === Translator ===
        if settings.translator_key:
            _set_env("TRANSLATOR_KEY", settings.translator_key)
        if settings.translator_location:
            _set_env("TRANSLATOR_LOCATION", settings.translator_location)
        
        logger.info("✅ Environment variables populated from Azure Key Vault")
        
    except Exception as e:
        logger.error(f"❌ Failed to load settings from Key Vault: {type(e).__name__}: {e}")
        import traceback
        logger.error(f"❌ Traceback: {traceback.format_exc()}")
        raise


def _set_env(key: str, value: str):
    """
    Imposta una variabile d'ambiente solo se non è già definita.
    
    Questo permette di fare override locale via .env o variabili esplicite.
    """
    if value and not os.getenv(key):
        os.environ[key] = value
        masked = value[:4] + "***" if len(value) > 4 else "***"
        logger.debug(f"  SET {key}={masked}")


# Connection string default per Azurite (sidecar in pod: 127.0.0.1)
AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    "QueueEndpoint=http://127.0.0.1:10001/devstoreaccount1;"
    "TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)


def _setup_azurite_env():
    """Imposta env: Azurite solo per AzureWebJobsStorage, Storage reale per dati app."""
    # Runtime Azure Functions -> Azurite (lock lease, queue)
    _set_env("AzureWebJobsStorage", os.getenv("AzureWebJobsStorage") or AZURITE_CONNECTION_STRING)
    # Dati app -> Azure Storage reale via Managed Identity
    _set_env("AZURE_STORAGE_ACCOUNT_NAME", os.getenv("AZURE_STORAGE_ACCOUNT_NAME") or "sacdpdev001")
    _set_env("BLOB_CONTAINER_NAME", os.getenv("BLOB_CONTAINER_NAME") or "ai-audit-poc-sa")
    logger.info("🔧 Storage: Azurite for runtime, Azure Storage for app data")


def _load_openai_from_keyvault_direct(key_vault_name: str):
    """
    Carica le configurazioni Azure OpenAI direttamente dal KeyVault.
    
    Fallback quando pydantic_settings non riesce a mappare i nomi
    (KeyVault usa - invece di _ nei nomi dei secrets).
    
    WORKAROUND: I valori nel KeyVault sono INVERTITI:
    - AZURE-OPENAI--API-KEY contiene l'endpoint URL
    - AZURE-OPENAI--ENDPOINT contiene la API key
    Questo codice li swappa per iniettarli correttamente.
    """
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        
        vault_url = f"https://{key_vault_name}.vault.azure.net"
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=vault_url, credential=credential)
        
        # Leggi i valori dal KeyVault
        api_key_secret = None
        endpoint_secret = None
        
        try:
            api_key_secret = client.get_secret("AZURE-OPENAI--API-KEY").value
        except Exception:
            pass
        try:
            endpoint_secret = client.get_secret("AZURE-OPENAI--ENDPOINT").value
        except Exception:
            pass
        
        # WORKAROUND: I valori sono invertiti nel KeyVault!
        # - "API-KEY" contiene URL (inizia con http)
        # - "ENDPOINT" contiene la key (stringa alfanumerica)
        # Detectiamo e swappiamo se necessario
        actual_endpoint = None
        actual_api_key = None
        
        if api_key_secret and endpoint_secret:
            if api_key_secret.startswith("http"):
                # Valori invertiti - swap
                actual_endpoint = api_key_secret
                actual_api_key = endpoint_secret
                logger.info("🔄 Detected swapped KeyVault values, correcting...")
            else:
                # Valori corretti
                actual_api_key = api_key_secret
                actual_endpoint = endpoint_secret
        
        loaded = []
        if actual_api_key:
            _set_env("AZURE_OPENAI_API_KEY", actual_api_key)
            loaded.append("AZURE_OPENAI_API_KEY")
        
        # Parse endpoint URL se è un URL completo con deployment
        # Es: https://dev-api.cdp.it/azure/openai/deployments/gpt-4.1/chat/completions?api-version=2024-12-01-preview
        parsed_deployment = None
        parsed_api_version = None
        
        if actual_endpoint:
            import re
            # Cerca pattern /openai/deployments/{name}/ nell'URL (API Gateway custom)
            # Es: https://dev-api.cdp.it/azure/openai/deployments/gpt-4.1/chat/completions
            deployment_match = re.search(r'/deployments/([^/]+)/', actual_endpoint)
            if deployment_match:
                parsed_deployment = deployment_match.group(1)
                # Estrai endpoint base (prima di /openai/deployments)
                # Il SDK LangChain aggiunge "/openai/deployments/..." automaticamente
                # quindi dobbiamo rimuovere anche "/openai" dalla base
                base_endpoint = actual_endpoint.split('/openai/deployments/')[0]
                if not base_endpoint:
                    # Fallback se il pattern era diverso
                    base_endpoint = actual_endpoint.split('/deployments/')[0].rstrip('/openai')
                logger.info(f"🔧 Parsed endpoint URL - base: {base_endpoint}, deployment: {parsed_deployment}")
                _set_env("AZURE_OPENAI_ENDPOINT", base_endpoint)
            else:
                _set_env("AZURE_OPENAI_ENDPOINT", actual_endpoint)
            loaded.append("AZURE_OPENAI_ENDPOINT")
            
            # Estrai api-version dalla query string se presente
            version_match = re.search(r'api-version=([^&]+)', actual_endpoint)
            if version_match:
                parsed_api_version = version_match.group(1)
                logger.info(f"🔧 Parsed api-version from URL: {parsed_api_version}")
        
        if loaded:
            # Usa valori parsati dall'URL o default
            api_version = parsed_api_version or "2024-08-01-preview"
            deployment = parsed_deployment or "gpt-4o-mini"
            
            _set_env("AZURE_OPENAI_API_VERSION", api_version)
            _set_env("AZURE_API_VERSION", api_version)
            _set_env("AZURE_OPENAI_DEPLOYMENT_NAME", deployment)
            _set_env("LLM_MODEL", deployment)
            _set_env("LLM_AZURE_DEPLOYMENT", deployment)
            _set_env("LLM_PROVIDER", "azure_openai")
            logger.info(f"✅ Loaded OpenAI settings directly from KeyVault: {loaded}")
        else:
            logger.warning("⚠️ Could not load any OpenAI settings from KeyVault")
            
    except Exception as e:
            logger.warning(f"⚠️ Failed to load OpenAI from KeyVault directly: {e}")


def _load_storage_from_keyvault_direct(key_vault_name: str):
    """
    Carica le configurazioni Azure Storage direttamente dal KeyVault.
    """
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        
        vault_url = f"https://{key_vault_name}.vault.azure.net"
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=vault_url, credential=credential)
        
        loaded = []
        
        # Account name
        try:
            account_name = client.get_secret("AZURE-STORAGE--ACCOUNT-NAME").value
            if account_name:
                _set_env("AZURE_STORAGE_ACCOUNT_NAME", account_name)
                storage_url = f"https://{account_name}.blob.core.windows.net"
                _set_env("AZURE_STORAGE_ACCOUNT_URL", storage_url)
                loaded.append("AZURE_STORAGE_ACCOUNT_NAME")
        except Exception:
            pass
        
        # Container name (usa default se non presente)
        _set_env("CONTAINER_NAME", "cdp")
        
        # Connection string (opzionale)
        try:
            conn_str = client.get_secret("AZURE-STORAGE--CONNECTION-STRING").value
            if conn_str:
                _set_env("CONNECTION_STRING", conn_str)
                _set_env("AzureWebJobsStorage", conn_str)  # Per Azure Functions
                loaded.append("CONNECTION_STRING")
                loaded.append("AzureWebJobsStorage")
        except Exception:
            # Fallback: usa Managed Identity per Azure Functions (v4+)
            # Invece di connection string, usiamo account name + managed identity
            if os.getenv("AZURE_STORAGE_ACCOUNT_NAME"):
                account = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
                _set_env("AzureWebJobsStorage__accountName", account)
                loaded.append("AzureWebJobsStorage__accountName (managed identity)")
        
        if loaded:
            logger.info(f"✅ Loaded Storage settings directly from KeyVault: {loaded}")
        else:
            logger.warning("⚠️ Could not load any Storage settings from KeyVault")
            
    except Exception as e:
        logger.warning(f"⚠️ Failed to load Storage from KeyVault directly: {e}")


def _load_service_principals_from_keyvault_direct(key_vault_name: str):
    """
    Carica le credenziali dei Service Principal direttamente dal KeyVault.
    
    Fallback quando pydantic_settings non riesce a mappare i nomi
    (KeyVault usa - invece di _ nei nomi dei secrets).
    """
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        
        vault_url = f"https://{key_vault_name}.vault.azure.net"
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=vault_url, credential=credential)
        
        loaded = []
        
        # Azure Reader
        try:
            reader_client_id = client.get_secret("AZURE-READER--CLIENT-ID").value
            if reader_client_id:
                _set_env("AZURE_READER__CLIENT_ID", reader_client_id)
                loaded.append("AZURE_READER__CLIENT_ID")
        except Exception:
            pass
        
        try:
            reader_client_secret = client.get_secret("AZURE-READER--CLIENT-SECRET").value
            if reader_client_secret:
                _set_env("AZURE_READER__CLIENT_SECRET", reader_client_secret)
                loaded.append("AZURE_READER__CLIENT_SECRET")
        except Exception:
            pass
        
        # Azure Sender
        try:
            sender_client_id = client.get_secret("AZURE-SENDER--CLIENT-ID").value
            if sender_client_id:
                _set_env("AZURE_SENDER__CLIENT_ID", sender_client_id)
                loaded.append("AZURE_SENDER__CLIENT_ID")
        except Exception:
            pass
        
        try:
            sender_client_secret = client.get_secret("AZURE-SENDER--CLIENT-SECRET").value
            if sender_client_secret:
                _set_env("AZURE_SENDER__CLIENT_SECRET", sender_client_secret)
                loaded.append("AZURE_SENDER__CLIENT_SECRET")
        except Exception:
            pass
        
        if loaded:
            logger.info(f"✅ Loaded Service Principal settings directly from KeyVault: {loaded}")
        else:
            logger.warning("⚠️ Could not load any Service Principal settings from KeyVault")
            
    except Exception as e:
        logger.warning(f"⚠️ Failed to load Service Principals from KeyVault directly: {e}")


# === Auto-execute al primo import ===
_populate_env_vars()
