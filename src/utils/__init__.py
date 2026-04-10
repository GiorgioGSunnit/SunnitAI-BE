"""
Utility module per il progetto aiac-be.

Contiene:
- singleton: decorator per implementare il pattern Singleton
- azure_credentials: gestione autenticazione Azure
- blob_storage_provider: client per Azure Blob Storage
- llm_provider: wrapper per Azure OpenAI
"""


def singleton(cls):
    """
    Decorator che implementa il pattern Singleton.
    
    Garantisce che esista una sola istanza della classe decorata.
    
    Esempio:
        @singleton
        class MyService:
            def __init__(self):
                self.value = 42
        
        # Ogni chiamata restituisce la stessa istanza
        s1 = MyService()
        s2 = MyService()
        assert s1 is s2  # True
    """
    instances = {}

    def get_instance(*args, **kwargs):
        if cls not in instances:
            instances[cls] = cls(*args, **kwargs)
        return instances[cls]

    return get_instance


__all__ = ["singleton"]
