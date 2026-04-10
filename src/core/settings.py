"""
Gestione centralizzata delle configurazioni con Azure Key Vault.

Le configurazioni vengono caricate in ordine di priorità:
1. Argomenti di inizializzazione
2. Variabili d'ambiente  
3. File .env
4. File secrets
5. Azure Key Vault (per secrets sicuri)

Esempio d'uso:
    from core.settings import settings
    
    # Accesso ai settings
    api_key = settings.azure_openai.api_key
    container = settings.azure_storage.container_name
    db_server = settings.db.server_name

Variabili d'ambiente richieste:
    - AZURE_KEY_VAULT_NAME: Nome del Key Vault Azure
"""
import os
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_settings import (
    AzureKeyVaultSettingsSource,
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from utils import singleton
from utils.azure_credentials import azure_client_credentials


# === Modelli di configurazione ===

class AzureCredentialsSettings(BaseModel):
    """Credenziali Service Principal Azure."""
    client_id: str
    client_secret: str


class AzureOpenAISettings(BaseModel):
    """Configurazione Azure OpenAI."""
    api_key: str
    endpoint: str
    # Campi con default - il factory.py usa già questi valori come fallback
    chat_model_deployment: str = "gpt-4o-mini"
    embedding_model_deployment: str = "gpt-4o-mini"
    api_version: str = "2024-08-01-preview"
    # Scope per token v2 - usa App Registration ID per ottenere token con issuer v2.0
    scope: str = "c989d43a-9e62-4c01-a67c-7eefbeef70ce/.default"


class AzureBlobStorageSettings(BaseModel):
    """Configurazione Azure Blob Storage."""
    account_name: str
    container_name: str = "cdp"  # Default container
    connection_string: Optional[str] = None  # Opzionale: per backward compatibility


class AzureAiSearchSettings(BaseModel):
    """Configurazione Azure AI Search."""
    api_key: str
    service_name: str
    index_name_exemplars: Optional[str] = None
    index_name_notes: Optional[str] = None


class DatabaseSettings(BaseModel):
    """Configurazione database SQL."""
    server_name: str
    name: str
    user: str
    password: str


class CelerySettings(BaseModel):
    """Configurazione Celery worker."""
    visibility_timeout_minutes: int = Field(default=12)
    max_retries: Optional[int] = Field(default=None)
    task_time_limit_minutes: int = Field(default=10)
    task_soft_time_limit_minutes: int = Field(default=5)
    worker_concurrency: int = Field(default=2)
    worker_prefetch_multiplier: int = Field(default=1)
    default_retry_delay: int = Field(default=15)
    worker_max_tasks_per_child: int = Field(default=1)


# === Settings principale ===

@singleton
class Settings(BaseSettings):
    """
    Configurazione centralizzata dell'applicazione.
    
    Integra automaticamente Azure Key Vault per i secrets.
    Usa il pattern singleton per garantire un'unica istanza.
    """

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
    )

    # Azure base
    azure_key_vault_name: str
    azure_tenant_id: str

    # Servizi Azure (Optional - secrets may not exist in Key Vault)
    azure_reader: Optional[AzureCredentialsSettings] = None
    azure_sender: Optional[AzureCredentialsSettings] = None
    azure_openai: Optional[AzureOpenAISettings] = None
    azure_storage: Optional[AzureBlobStorageSettings] = None
    azure_ai_search: Optional[AzureAiSearchSettings] = None

    # Database (opzionale)
    db: Optional[DatabaseSettings] = None

    # Microsoft Graph (webhook)
    client_state: str = Field(default="someSecretClientState")
    notification_email: Optional[str] = None

    # Redis (opzionali)
    redis_host: Optional[str] = None
    redis_port: Optional[int] = None

    # Celery
    celery_settings: CelerySettings = Field(default_factory=CelerySettings)
    
    # Translator (opzionale)
    translator_key: Optional[str] = None
    translator_location: Optional[str] = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """
        Configura le sorgenti dei settings includendo Azure Key Vault.
        """
        key_vault_name = os.getenv('AZURE_KEY_VAULT_NAME', None)
        if not key_vault_name:
            raise ValueError("AZURE_KEY_VAULT_NAME is not set")

        key_vault_url = f"https://{key_vault_name}.vault.azure.net"

        az_key_vault_settings = AzureKeyVaultSettingsSource(
            settings_cls,
            key_vault_url,
            azure_client_credentials.get_credential(),
        )

        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            az_key_vault_settings,
        )


# Istanza singleton - lazy initialization
def get_settings() -> Settings:
    """
    Restituisce l'istanza dei settings.
    
    Usa lazy loading per evitare errori se le variabili
    d'ambiente non sono ancora configurate.
    """
    return Settings()


# Per retrocompatibilità con import diretti
# NOTA: Questo creerà l'istanza all'import del modulo.
# Commentare se si preferisce lazy loading esplicito.
# settings = Settings()
