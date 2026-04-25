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
        load_dotenv(override=False)
    except ImportError:
        pass

    llm_base_url = os.getenv("LLM_BASE_URL")
    llm_model = os.getenv("LLM_MODEL", "nemotron-2-30B-A3B")

    logger.info("Bootstrap: LLM_BASE_URL=%s", "set" if llm_base_url else "NOT SET")
    logger.info("Bootstrap: LLM_MODEL=%s", llm_model)
    logger.info("Bootstrap complete.")


_populate_env_vars()
