# Endpoints API BE

## 1. GET /api/warmup
**Scopo:** Health check / keep-alive per Azure Functions.
- Evita cold start
- Verifica che l’host risponda
- Ritorna 200 senza body

## 2. POST /api/upload
**Scopo:** Carica un PDF su Blob Storage.
- **Input:** multipart: `file` (PDF), `external` (1/0)
- **Output:** 202 con `job_id`, `statusQueryGetUri`
- **Flusso:** avvia job in background → upload su blob → FE fa polling su statusQueryGetUri

## 3. GET /api/job/{id}
**Scopo:** Polling dello stato di un job.
- **Output:** `runtimeStatus` (Pending | Running | Completed | Failed), `output` quando completo
- Usato dal FE per verificare il completamento di upload, extract_requirements, compare_requirements, ecc.

## 4. POST /api/extract_requirements
**Scopo:** Estrae requisiti da un PDF tramite VMAI (LLM).
- **Input:** multipart: `file` (PDF), `external` (1/0)
- **Output:** 202 con `job_id`, `statusQueryGetUri` + stime
- **Flusso:** upload PDF → chiamata VMAI extract-requirements → salvataggio JSON su blob → FE fa polling

## 5. Altri endpoint (sync)
- `get-results`, `compare_requirements`, `extract_subjects`, `extract_sanctions`, `translate`, `login`, download, ecc.
