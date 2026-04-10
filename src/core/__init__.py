"""
Core module - Configurazioni centralizzate del progetto.

Contiene:
- bootstrap: inizializzazione ambiente da Key Vault (import per primo!)
- settings: configurazione dell'applicazione con supporto Azure Key Vault

Uso tipico all'avvio dell'app:
    import core.bootstrap  # Popola env vars da Key Vault
    # ... resto degli import ...
"""

__all__ = ["bootstrap", "settings"]
