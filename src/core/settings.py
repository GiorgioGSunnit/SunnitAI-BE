"""
Settings senza Azure Key Vault — carica da variabili d'ambiente / .env file.

Sostituisce la versione precedente che richiedeva AZURE_KEY_VAULT_NAME.
Tutti i valori vengono letti direttamente da os.environ o dal file .env.
"""
import os
from typing import Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from utils import singleton


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


@singleton
class Settings(BaseSettings):
    """
    Configurazione centralizzata — solo variabili d'ambiente / .env.

    Azure Key Vault rimosso: tutte le credenziali devono essere presenti
    nel file .env (o come variabili d'ambiente nel container).
    """

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Redis ────────────────────────────────────────────────────────────────
    redis_host: Optional[str] = None
    redis_port: Optional[int] = None

    # ── Celery ───────────────────────────────────────────────────────────────
    celery_settings: CelerySettings = Field(default_factory=CelerySettings)

    # ── Misc ─────────────────────────────────────────────────────────────────
    client_state: str = Field(default="someSecretClientState")
    notification_email: Optional[str] = None
    translator_key: Optional[str] = None
    translator_location: Optional[str] = None

    # ── Azure stubs (always None — kept for API compatibility) ───────────────
    # Code that does `if settings.azure_openai:` will take the fallback path.
    @property
    def azure_openai(self):
        return None

    @property
    def azure_storage(self):
        return None

    @property
    def azure_ai_search(self):
        return None

    @property
    def azure_reader(self):
        return None

    @property
    def azure_sender(self):
        return None


def get_settings() -> Settings:
    """Restituisce l'istanza singleton dei settings."""
    return Settings()


# Per retrocompatibilità con import diretti (`from core.settings import settings`)
settings = get_settings()
