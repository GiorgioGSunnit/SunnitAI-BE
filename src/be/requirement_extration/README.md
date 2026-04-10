# Backend API (FastAPI)

## Setup locale
```bash
python3.11 -m venv venv
pip install -r requirements.txt
python -m spacy download it_core_news_lg
```

## Avvio API
Questo progetto espone l'app FastAPI in `call_fast_api.py` (`app = FastAPI()`).
Esempio con Uvicorn:

```bash
uvicorn call_fast_api:app --host 0.0.0.0 --port 6002
```

## Configurazione LLM (APIM / OpenAI-compatibile)
Variabili richieste per il routing via APIM:

- `LLM_PROVIDER` (es. `openai`)
- `LLM_BASE_URL` (URL APIM, es. `https://<apim-domain>/openai`)
- `LLM_MODEL` (es. `gpt-4o-mini`)
- `LLM_API_VERSION` (es. `2024-08-01-preview`)
- `OPENAI_API_KEY` (subscription/key APIM)

## Altre variabili usate dal codice (os.getenv)
Da valorizzare se attivi i moduli corrispondenti:

- Storage blob: `CONNECTION_STRING`, `CONTAINER_NAME`, `CONTAINER_NAME_EXT`
- Search/Translator: `SEARCH_KEY`, `TRANSLATOR_KEY`, `TRANSLATOR_LOCATION`
- Azure OpenAI diretto: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_API_VERSION`

## Deploy AKS con Key Vault (Percorso A)
I manifest si trovano in `aks/`. Per leggere i secret da Key Vault senza modifiche al codice:

- `aks/secret-provider-app.yaml` definisce un `SecretProviderClass` dedicato
  e sincronizza i secret in `${APP_NAME}-kv-secrets`.
- `aks/deployment.yaml` monta il volume CSI e importa gli env via `envFrom`.

Nota operativa: i nomi dei secret in Key Vault devono coincidere con i nomi
delle env var. I secret opzionali sono gia' elencati in `aks/secret-provider-app.yaml`
come voci commentate con la funzione d'uso.
