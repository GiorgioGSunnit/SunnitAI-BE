#!/usr/bin/env python3
"""
Script per scaricare i secrets da Azure Key Vault e generare un file .env locale.

Uso:
    # 1. Login Azure (una volta)
    az login
    
    # 2. Esegui lo script con il nome del Key Vault
    python scripts/fetch_secrets.py <nome-keyvault>
    
    # Esempio:
    python scripts/fetch_secrets.py kv-cdp-aiac-dev

Output:
    Crea/aggiorna il file .env nella root del progetto con tutti i secrets.
"""
import sys
import os
from pathlib import Path

# Aggiungi src al path per importare i moduli
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def fetch_secrets(keyvault_name: str, output_file: str = ".env"):
    """
    Scarica tutti i secrets dal Key Vault e li salva in un file .env
    """
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError:
        print("❌ Installa le dipendenze: pip install azure-identity azure-keyvault-secrets")
        sys.exit(1)
    
    vault_url = f"https://{keyvault_name}.vault.azure.net"
    print(f"🔐 Connessione a Key Vault: {vault_url}")
    
    try:
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=vault_url, credential=credential)
    except Exception as e:
        print(f"❌ Errore di autenticazione: {e}")
        print("\n💡 Suggerimento: esegui 'az login' prima di questo script")
        sys.exit(1)
    
    print("📥 Scaricamento secrets...")
    
    # Mappa dei secrets da scaricare (nome_keyvault -> nome_env_var)
    # I nomi nel Key Vault usano '-' mentre le env vars usano '_'
    secrets_map = {
        # Azure OpenAI
        "azure-openai--api-key": "AZURE_OPENAI_API_KEY",
        "azure-openai--endpoint": "AZURE_OPENAI_ENDPOINT", 
        "azure-openai--api-version": "AZURE_OPENAI_API_VERSION",
        "azure-openai--chat-model-deployment": "AZURE_OPENAI_DEPLOYMENT_NAME",
        "azure-openai--embedding-model-deployment": "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
        
        # Azure Storage
        "azure-storage--account-name": "AZURE_STORAGE_ACCOUNT_NAME",
        "azure-storage--container-name": "CONTAINER_NAME",
        "azure-storage--connection-string": "CONNECTION_STRING",
        
        # Azure AI Search
        "azure-ai-search--api-key": "SEARCH_KEY",
        "azure-ai-search--service-name": "AZURE_SEARCH_SERVICE_NAME",
        
        # Database
        "db--server-name": "DB_SERVER",
        "db--name": "DB_NAME",
        "db--user": "DB_USER",
        "db--password": "DB_PASSWORD",
        
        # Redis
        "redis-host": "REDIS_HOST",
        "redis-port": "REDIS_PORT",
        
        # Translator
        "translator-key": "TRANSLATOR_KEY",
        "translator-location": "TRANSLATOR_LOCATION",
        
        # Service Principals
        "azure-reader--client-id": "AZURE_READER__CLIENT_ID",
        "azure-reader--client-secret": "AZURE_READER__CLIENT_SECRET",
        "azure-sender--client-id": "AZURE_SENDER__CLIENT_ID",
        "azure-sender--client-secret": "AZURE_SENDER__CLIENT_SECRET",
        
        # Altri
        "azure-tenant-id": "AZURE_TENANT_ID",
        "notification-email": "NOTIFICATION_EMAIL",
        
        # LLM (per lex_package)
        "LLM-PROVIDER": "LLM_PROVIDER",
        "LLM-MODEL": "LLM_MODEL",
        "LLM-BASE-URL": "LLM_BASE_URL",
        "LLM-API-VERSION": "LLM_API_VERSION",
        "OPENAI-API-KEY": "OPENAI_API_KEY",
    }
    
    env_vars = {}
    errors = []
    
    # Prova a scaricare ogni secret
    for kv_name, env_name in secrets_map.items():
        try:
            secret = client.get_secret(kv_name)
            if secret.value:
                env_vars[env_name] = secret.value
                print(f"  ✅ {env_name}")
        except Exception:
            # Secret non trovato - è opzionale
            pass
    
    # Prova anche a listare tutti i secrets e prendere quelli non mappati
    print("\n📋 Cercando altri secrets...")
    try:
        for secret_properties in client.list_properties_of_secrets():
            name = secret_properties.name
            # Converti nome KV in nome env var (sostituisci - con _ e maiuscolo)
            env_name = name.replace("-", "_").upper()
            
            if env_name not in env_vars and name not in secrets_map:
                try:
                    secret = client.get_secret(name)
                    if secret.value:
                        env_vars[env_name] = secret.value
                        print(f"  ✅ {env_name} (auto)")
                except Exception:
                    pass
    except Exception as e:
        print(f"  ⚠️ Non posso listare secrets: {e}")
    
    if not env_vars:
        print("❌ Nessun secret trovato!")
        sys.exit(1)
    
    # Scrivi file .env
    output_path = Path(__file__).parent.parent / output_file
    
    with open(output_path, "w") as f:
        f.write("# Auto-generated from Azure Key Vault\n")
        f.write(f"# Key Vault: {keyvault_name}\n")
        f.write(f"# Generated: {__import__('datetime').datetime.now().isoformat()}\n\n")
        
        # Aggiungi il nome del Key Vault per il bootstrap
        f.write(f"AZURE_KEY_VAULT_NAME={keyvault_name}\n\n")
        
        for key, value in sorted(env_vars.items()):
            # Escape caratteri speciali
            if any(c in value for c in [' ', '"', "'", '\n', '=']):
                value = f'"{value}"'
            f.write(f"{key}={value}\n")
    
    print(f"\n✅ File creato: {output_path}")
    print(f"   {len(env_vars)} variabili salvate")
    print(f"\n💡 Ora puoi usare 'load_dotenv()' o 'source .env' per caricarle")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n❌ Manca il nome del Key Vault!")
        print("\nUso: python scripts/fetch_secrets.py <nome-keyvault>")
        print("\n💡 Per trovare il nome del Key Vault:")
        print("   - Controlla le variabili CI/CD in GitLab (AKS_KV_NAME)")
        print("   - Oppure chiedi al team DevOps")
        print("   - Oppure: az keyvault list --query '[].name' -o tsv")
        sys.exit(1)
    
    keyvault_name = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else ".env"
    
    fetch_secrets(keyvault_name, output_file)


if __name__ == "__main__":
    main()
