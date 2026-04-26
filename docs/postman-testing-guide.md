# SunnitAI — Postman Testing Guide

**Base URL:** `http://<server>:2025`  
All requests that upload a PDF use `multipart/form-data`.

---

## 1. Health check (verify server is up)

| Field | Value |
|-------|-------|
| Method | `GET` |
| URL | `http://<server>:2025/health` |

**Expected response `200`:**
```json
{
  "status": "healthy",
  "service": "aiac-be"
}
```

---

## 2. Parse a document (async)

### Step A — Submit the job

| Field | Value |
|-------|-------|
| Method | `POST` |
| URL | `http://<server>:2025/api/parse` |
| Body | `form-data` |

In the Body tab, set:
| Key | Type | Value |
|-----|------|-------|
| `file` | File | *(select your PDF)* |

Optional — force a specific parser by adding a query param to the URL:
```
http://<server>:2025/api/parse?template_hint=banca
http://<server>:2025/api/parse?template_hint=regolamento
```

**Expected response `202`:**
```json
{
  "job_id": "3f7a1c2d-...",
  "status": "pending",
  "statusQueryGetUri": "/api/job/3f7a1c2d-..."
}
```

Copy the `job_id`.

### Step B — Poll for the result

| Field | Value |
|-------|-------|
| Method | `GET` |
| URL | `http://<server>:2025/api/job/<job_id>` |

Repeat every 5–10 seconds until `status` is `completed` or `failed`.

**While running:**
```json
{
  "status": "running",
  "step": "parsing",
  "updated_at": "2026-04-26T14:53:00+00:00"
}
```

**When completed:**
```json
{
  "status": "completed",
  "result": {
    "document_name": "EXT_1_1_Provvedimento UIF.pdf",
    "template_used": "banca",
    "confidence": 0.67,
    "articoli": [ ... ],
    "stats": {
      "articoli": 4,
      "commi": 0,
      "sottocommi": 0
    }
  }
}
```

---

## 3. Extract metadata (sync — no polling needed)

| Field | Value |
|-------|-------|
| Method | `POST` |
| URL | `http://<server>:2025/api/metadata` |
| Body | `form-data` |

In the Body tab, set:
| Key | Type | Value |
|-----|------|-------|
| `file` | File | *(select your PDF)* |

**Expected response `200` (immediate):**
```json
{
  "document_name": "Disposizioni di vigilanza in materia di AVC",
  "document_number": "285",
  "issue_date": "2019-07-30",
  "editor_enterprises": ["Banca d'Italia"]
}
```

> If the LLM is unavailable, returns `503`. Wait a moment and retry.

---

## 4. Ingest: parse + metadata (async, no Neo4J)

| Field | Value |
|-------|-------|
| Method | `POST` |
| URL | `http://<server>:2025/api/document/ingest` |
| Body | `form-data` |

In the Body tab, set:
| Key | Type | Value |
|-----|------|-------|
| `file` | File | *(select your PDF)* |

**Expected response `202`:** same job pattern as `/api/parse`

**Poll result when completed:**
```json
{
  "status": "completed",
  "result": {
    "parse": {
      "template_used": "regolamento",
      "confidence": 0.40,
      "articoli": [ ... ],
      "stats": { "articoli": 34, "commi": 0, "sottocommi": 0 }
    },
    "metadata": {
      "document_name": "Disposizioni AVC",
      "document_number": "285",
      "issue_date": "2019-07-30",
      "editor_enterprises": ["Banca d'Italia"]
    }
  }
}
```

---

## 5. Process: full pipeline → Neo4J write (async)

This is the main endpoint that writes nodes and relationships to the Neo4J database.

| Field | Value |
|-------|-------|
| Method | `POST` |
| URL | `http://<server>:2025/api/document/process` |
| Body | `form-data` |

In the Body tab, set:
| Key | Type | Value |
|-----|------|-------|
| `file` | File | *(select your PDF)* |

**Expected response `202`:**
```json
{
  "job_id": "3f7a1c2d-...",
  "status": "pending",
  "statusQueryGetUri": "/api/job/3f7a1c2d-..."
}
```

### Poll for progress

Poll `GET /api/job/<job_id>` every 5–10 seconds.  
The `step` field shows which stage the pipeline is in:

| Step | Description |
|------|-------------|
| `parsing` | Reading and parsing the PDF |
| `analisi` | LLM analysis of each article |
| `flatten` | Flattening the analysis tree |
| `build_graph` | Building the Neo4J graph payload |
| `neo4j_write` | Writing nodes and relationships to the DB |

> This endpoint takes **2–10 minutes** depending on document size — the LLM analysis step is the slowest.

**When completed:**
```json
{
  "status": "completed",
  "result": {
    "document_name": "EXT_1_2_Disposizioni AVC.pdf",
    "template_used": "regolamento",
    "confidence": 0.40,
    "parse_stats": {
      "articoli": 34,
      "commi": 0,
      "sottocommi": 0
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

### Verify in Neo4J

After the job completes, open the Neo4J browser at your Aura console and run:

```cypher
// Count all nodes written
MATCH (n) RETURN labels(n), count(n)

// See the document node
MATCH (d:Document) RETURN d

// See the full graph for one document
MATCH (d:Document {name: "EXT_1_2_Disposizioni AVC_30_07_2019 (Banca Italia)"})-[r*1..3]-(n)
RETURN d, r, n
```

---

## Polling pattern (summary)

For all async endpoints (`/api/parse`, `/api/document/ingest`, `/api/document/process`):

```
POST endpoint → get job_id
    ↓
GET /api/job/<job_id>  ←── repeat every 5–10s
    ↓
status = "completed" → read result
status = "failed"    → read error field
```

---

## File reference

| File | Type | Expected template |
|------|------|-------------------|
| `EXT_1_1_Provvedimento UIF...pdf` | EXT_1_1 | `banca` |
| `EXT_1_2_Disposizioni AVC...pdf` | EXT_1_2 | `regolamento` |
| `EXT_6_Circ-285...pdf` | EXT_6 | `banca` |
| `INT_1_1_REG_Indicatori...pdf` | INT_1_1 | `indice` |
