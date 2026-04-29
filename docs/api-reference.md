# SunnitAI API Reference

## Services

| Service | Port | Purpose |
|---------|------|---------|
| Functions API | `7071` | Upload, ingest, job management |
| VMAI API | `2025` | Parse, metadata, document processing |

All file uploads use `multipart/form-data`.  
Async endpoints return `202` immediately — poll `GET /api/job/{job_id}` until `status` is `completed` or `failed`.

---

## Endpoints

### Functions API — `http://<server>:7071`

| Method | Endpoint | Type | Description |
|--------|----------|------|-------------|
| POST | `/api/upload` | Async | Upload PDF to blob storage (FE preview & extraction) |
| POST | `/api/ingest` | Sync | Drop PDF into watcher inbox → full Neo4J pipeline |
| GET | `/api/job/{job_id}` | Sync | Poll async job status |

### VMAI API — `http://<server>:2025`

| Method | Endpoint | Type | Description |
|--------|----------|------|-------------|
| GET | `/health` | Sync | Service health check |
| POST | `/api/parse` | Async | Parse a PDF → articoli structure |
| POST | `/api/metadata` | Sync | Extract metadata via LLM |
| POST | `/api/document/ingest` | Async | Parse + metadata (no Neo4J) |
| POST | `/api/document/process` | Async | Full pipeline → Neo4J write (inline) |
| GET | `/api/job/{job_id}` | Sync | Poll async job status |

---

## POST /api/ingest (port 7071)

Drops a PDF into the watcher inbox to trigger the full ingestion pipeline:  
**parse → LLM analysis → flatten → Neo4J write**

Processing happens asynchronously via the watcher service. Returns `202` immediately.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | PDF file | Yes | The document to ingest |

**Response `202`:**
```json
{
  "status": "accepted",
  "filename": "EXT_1_1_Provvedimento UIF.pdf",
  "message": "File queued for ingestion. Check watcher logs for progress."
}
```

**Monitor progress** via server logs:
```bash
journalctl -u sunnitai-watcher -f
```

> Use this endpoint to write documents to Neo4J. For FE preview only, use `/api/upload` instead.

---

---

## GET /health

Returns the service status.

**Response `200`:**
```json
{
  "status": "healthy",
  "service": "aiac-be"
}
```

---

## POST /api/parse

Parses a PDF and returns the full articoli structure.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | PDF file | Yes | The document to parse |
| `template_hint` | query param | No | Force a parser: `banca`, `regolamento`, etc. |

**Response `202`:**
```json
{
  "job_id": "3f7a1c2d-...",
  "status": "pending",
  "statusQueryGetUri": "/api/job/3f7a1c2d-..."
}
```

**Poll result when `completed`:**
```json
{
  "status": "completed",
  "result": {
    "document_name": "EXT_1_1_Provvedimento UIF.pdf",
    "template_used": "banca",
    "template_label": "Banca d'Italia",
    "confidence": 0.95,
    "scores": { "banca": 8, "regolamento": 2 },
    "articoli": [ "..." ],
    "stats": {
      "articoli": 10,
      "commi": 45,
      "sottocommi": 12
    }
  }
}
```

---

## POST /api/metadata

Extracts structured metadata from the first pages of a PDF using the LLM.  
This endpoint is **synchronous** — it returns the result directly (no polling needed).

**Request:** `multipart/form-data`

| Field | Type | Required |
|-------|------|----------|
| `file` | PDF file | Yes |

**Response `200`:**
```json
{
  "document_name": "Disposizioni di vigilanza in materia di AVC",
  "document_number": "285",
  "issue_date": "2019-07-30",
  "editor_enterprises": ["Banca d'Italia"]
}
```

**Response `503`:** LLM is unavailable — retry later.

---

## POST /api/document/ingest

Parses the PDF and extracts metadata in a single async job.  
Does **not** write to Neo4J.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | PDF file | Yes | The document to ingest |
| `template_hint` | query param | No | Force a parser: `banca`, `regolamento`, etc. |

**Response `202`:**
```json
{
  "job_id": "3f7a1c2d-...",
  "status": "pending",
  "statusQueryGetUri": "/api/job/3f7a1c2d-..."
}
```

**Poll result when `completed`:**
```json
{
  "status": "completed",
  "result": {
    "parse": {
      "document_name": "EXT_1_2_Disposizioni AVC.pdf",
      "template_used": "regolamento",
      "template_label": "Regolamento",
      "confidence": 0.88,
      "scores": { "banca": 8, "regolamento": 2 },
      "articoli": [ "..." ],
      "stats": {
        "articoli": 7,
        "commi": 30,
        "sottocommi": 5
      }
    },
    "metadata": {
      "document_name": "Disposizioni di vigilanza in materia di AVC",
      "document_number": "285",
      "issue_date": "2019-07-30",
      "editor_enterprises": ["Banca d'Italia"]
    }
  }
}
```

---

## POST /api/document/process

Full ingestion pipeline: **parse → LLM analysis → flatten → Neo4J write**.  
This is the API equivalent of the watcher service.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `file` | PDF file | Yes | The document to process |
| `template_hint` | query param | No | Force a parser: `banca`, `regolamento`, etc. |

**Response `202`:**
```json
{
  "job_id": "3f7a1c2d-...",
  "status": "pending",
  "statusQueryGetUri": "/api/job/3f7a1c2d-..."
}
```

**Progress steps** (visible in the `step` field while polling):

```
parsing → analisi → flatten → build_graph → neo4j_write → completed
```

**Poll result when `completed`:**
```json
{
  "status": "completed",
  "result": {
    "document_name": "EXT_1_2_Disposizioni AVC.pdf",
    "template_used": "regolamento",
    "template_label": "Regolamento",
    "confidence": 0.88,
    "parse_stats": {
      "articoli": 7,
      "commi": 30,
      "sottocommi": 5
    },
    "graph_stats": {
      "nodes": 43,
      "relationships": 38
    },
    "neo4j": {
      "skipped": false,
      "nodes_written": 43,
      "relationships_written": 38
    }
  }
}
```

---

## GET /api/job/{job_id}

Polls the status of any async job.

**Response fields:**

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `pending` / `running` / `completed` / `failed` |
| `step` | string | Current pipeline stage (only present when `running`) |
| `result` | object | Final result (only present when `completed`) |
| `error` | string | Error message (only present when `failed`) |
| `updated_at` | ISO 8601 | Timestamp of last status update |

**Example (running):**
```json
{
  "status": "running",
  "step": "analisi",
  "updated_at": "2026-04-26T14:53:00+00:00"
}
```

**Example (failed):**
```json
{
  "status": "failed",
  "error": "LLM endpoint unreachable",
  "updated_at": "2026-04-26T14:53:05+00:00"
}
```

---

## Polling pattern

For all async endpoints, use this pattern:

1. `POST` the endpoint → get `job_id`
2. `GET /api/job/{job_id}` every 5–10 seconds
3. Stop when `status` is `completed` or `failed`

Typical durations:
| Endpoint | Expected time |
|----------|--------------|
| `/api/parse` | 5–30s depending on document size |
| `/api/metadata` | 5–15s (single LLM call) |
| `/api/document/ingest` | 10–45s |
| `/api/document/process` | 2–10 min (full LLM analysis pipeline) |
