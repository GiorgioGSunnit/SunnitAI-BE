"""
Bootstrap module — carica le variabili d'ambiente dal file .env.

Versione semplificata senza Azure Key Vault.
Il file .env deve contenere tutte le credenziali necessarie.

Uso:
    # All'inizio di function_app.py o main.py:
    import core.bootstrap  # noqa: F401 - side effect import

    # Ora il resto del codice può usare os.getenv() normalmente
    from lex_package.llm.factory import build_chat_model
"""
import os
import logging

logger = logging.getLogger(__name__)


def _populate_env_vars():
    """Carica il file .env e logga la configurazione attiva."""
    try:
        from dotenv import load_dotenv
        # Cerca .env nella directory di lavoro corrente e nei parent tipici
        load_dotenv(override=False)  # non sovrascrive variabili già presenti
    except ImportError:
        pass  # python-dotenv non installato — env vars da variabili di sistema

    llm_base_url = os.getenv("LLM_BASE_URL") or os.getenv("AZURE_OPENAI_ENDPOINT")
    llm_model = os.getenv("LLM_MODEL") or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
    llm_provider = os.getenv("LLM_PROVIDER", "openai")

    logger.info("Bootstrap: LLM_PROVIDER=%s", llm_provider)
    logger.info("Bootstrap: LLM_BASE_URL=%s", "set" if llm_base_url else "NOT SET")
    logger.info("Bootstrap: LLM_MODEL=%s", llm_model or "NOT SET")

    # Normalizza: se AZURE_OPENAI_* sono presenti ma LLM_* no, copia i valori
    # così factory.py può sempre leggere LLM_BASE_URL / LLM_API_KEY
    if not os.getenv("LLM_BASE_URL") and os.getenv("AZURE_OPENAI_ENDPOINT"):
        os.environ["LLM_BASE_URL"] = os.environ["AZURE_OPENAI_ENDPOINT"]
    if not os.getenv("LLM_API_KEY") and os.getenv("AZURE_OPENAI_API_KEY"):
        os.environ["LLM_API_KEY"] = os.environ["AZURE_OPENAI_API_KEY"]
    if not os.getenv("LLM_MODEL") and os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME"):
        os.environ["LLM_MODEL"] = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]

    logger.info("Bootstrap complete.")


_populate_env_vars()
