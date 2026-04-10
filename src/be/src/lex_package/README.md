# Strumento di Analisi e Confronto di Documenti Legali

## Panoramica
Questo repository contiene un pacchetto Python per il parsing, l’analisi semantica e il confronto di documenti normativi in PDF. Il flusso tipico è: estrazione strutturata → analisi dei contenuti → confronto tra versioni o testi diversi, con supporto a LLM configurabili.

## Struttura del progetto
- `src/lex_package/` pacchetto principale (parsing, analisi, confronto, CLI)
- `src/lex_package/parsing_utils/` parser e logiche di estrazione dai PDF
- `src/lex_package/confronto_search/` indicizzazione, chunking e ricerca
- `src/lex_package/utils/` utility e prompt in `src/lex_package/utils/prompts/`
- `src/data/` PDF di esempio per test/analisi (non necessari in produzione)

## Funzionalita principali
- **Parsing PDF**: identifica articoli, sezioni e struttura gerarchica.
- **Analisi**: individua requisiti, obblighi e concetti chiave.
- **Confronto**: calcola similarita tra articoli e supporta confronti normativi/emendativi.
- **CLI**: orchestrazione dei flussi da riga di comando.

## Installazione
Richiede Python >= 3.8.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .
```

## Configurazione LLM
Imposta almeno `OPENAI_API_KEY` (local). Per deployment o gateway OpenAI-compatibili:

```bash
export LLM_PROVIDER=openai
export LLM_MODEL=gpt-4o-mini
export LLM_BASE_URL=http://llm-gateway.svc.cluster.local/v1
export OPENAI_API_KEY=<service-token>
```

Variabili supportate (con fallback): `LLM_PROVIDER`, `LLM_MODEL`, `LLM_BASE_URL`, `LLM_API_VERSION`.
Le variabili Azure storiche restano compatibili.

## Comandi principali
- Build del pacchetto:
  ```bash
  python -m build
  ```
- CLI:
  ```bash
  python -m lex_package.cli --help
  ```
- Run completo (placeholder; usa il prefisso del file in `src/data/`, senza estensione):
  ```bash
  python -m lex_package.cli --parse <nome_documento>
  python -m lex_package.cli -a <nome_documento>
  python -m lex_package.cli -e <documento_base> <documento_emendativo>
  ```
- Test utility principali:
  ```bash
  python -m lex_package.confronto_search.test.test_insert_deep
  ```
  Esegue un harness interno (non pytest) che usa le fixture JSON in `src/lex_package/confronto_search/test/`
  per validare la logica di ricerca/chunking, con output su console.

Se installato come pacchetto:
- `cli` (entrypoint principale)
- `test_utils`
- `test_chunk_retriever`

## Docker
Costruzione immagine:
```bash
docker build -t sunnit-cdp:latest .
```
Esecuzione locale:
```bash
docker run --rm --env-file .env -v $(pwd)/src/data:/app/src/data sunnit-cdp:latest python -m lex_package.cli --help
```

## Note su dati e output
- I PDF in `src/data/` sono opzionali e consigliati solo per test.
- Gli output generati vengono tipicamente scritti in cartelle `out*` (escluse dal controllo versione).
