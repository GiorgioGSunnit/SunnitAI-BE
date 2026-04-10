# Refactoring: Durable Functions → Job + Polling

## 1. Piano Refactoring

### Obiettivo
Rimuovere Azure Durable Functions e sostituire con job in-memory + endpoint polling.

### Scope
| Endpoint | Tipo attuale | Azione |
|----------|--------------|--------|
| `upload` | durable → polling | refactor |
| `extract_requirements` | durable → polling | refactor |
| `compare_requirements` | durable → polling | refactor |
| `extract_subjects` | durable (wait) | refactor → polling |
| `extract_sanctions` | durable → polling | refactor |
| `translate` | durable → polling | refactor |
| Altri (login, get-results, download, ...) | HTTP sync | invariati |

### Modifiche principali

1. **job_store**: dict in-memory `job_id → {status, result, error, created_at, job_type, custom_status}`
2. **Nuovo endpoint** `GET /api/job/{job_id}`: ritorna status compatibile con format Azure (runtimeStatus, output)
3. **POST endpoints**: sostituire `client.start_new()` con avvio thread/executor, ritornare `{id, statusQueryGetUri: "/api/job/{id}"}`
4. **Estrarre activities** in funzioni pure chiamabili (upload_to_blob, process_requirements, ...)
5. **Rimuovere**: df.DFApp, durable_client_input, orchestration_trigger, activity_trigger, host.json durableTask
6. **Cambiare** app da `df.DFApp` a `func.FunctionApp`

### Compatibilità FE
- `statusQueryGetUri` → `/api/job/{job_id}`
- Polling ritorna `{id, runtimeStatus, output?, customStatus?}`

---

## 2. Piano Test

### 2.1 Test unitari (mock)

| Test | Mock | Verifica |
|------|------|----------|
| `test_upload_to_blob` | BlobServiceClient | Scrittura blob, cleanup file test |
| `test_process_requirements` | requests.post → VMAI | Chiamata corretta, payload |
| `test_compare_requirements` | requests.post | Chiamata corretta |
| `test_extract_text_from_pdf` | PDF reale minore | Estrazione testo da PDF |
| `test_job_lifecycle` | Nessuno (in-memory) | Start job → poll pending → completed |

### 2.2 Test integrazione (mock OpenAI / VMAI)

| Test | Setup | Verifica |
|------|-------|----------|
| `test_extract_requirements_e2e` | Mock VMAI response | POST → job_id → poll → output |
| `test_compare_requirements_e2e` | Mock VMAI response | Idem |

### 2.3 Cleanup
- Blob test: usare container/prefix `test-{uuid}`, cancellare in teardown
- File locali: `tempfile`, auto-cleanup

### Struttura file test
```
src/be/azure-durable-function/
  tests/
    conftest.py          # fixtures, mocks
    test_job_store.py    # OK senza azure-functions
    test_activities.py    # richiede azure-functions
    test_api_endpoints.py # richiede azure-functions
```

`poetry run pytest src/be/azure-durable-function/tests/` - job_store tests pass; altri skipped se azure-functions non installato.

---

## 3. Checklist implementazione

- [ ] job_store module
- [ ] GET /api/job/{job_id}
- [ ] Refactor upload
- [ ] Refactor extract_requirements
- [ ] Refactor compare_requirements
- [ ] Refactor extract_subjects
- [ ] Refactor extract_sanctions
- [ ] Refactor translate
- [ ] Rimuovere durable deps, host.json durableTask
- [ ] Test
