# SunnitAI-BE — Release Guide

Deploys the backend to the production server (`204.168.183.198`) as three systemd services running inside a Python virtualenv. No Docker required.  
The whole process is driven by a single command from your Mac — no manual SSH steps required.

---

## Prerequisites

### 1. SSH key

The deploy uses `~/.ssh/server_key`. If you only have a PuTTY `.ppk` key, convert it first:

```bash
puttygen ~/Downloads/private.ppk -O private-openssh -o ~/.ssh/server_key
```

Test that it works:

```bash
ssh -i ~/.ssh/server_key root@204.168.183.198 "echo ok"
```

### 2. Production environment file

Create `.env.production` in the project root by copying the example and filling in real values:

```bash
cp .env.local.example .env.production
nano .env.production
```

**Required variables** (the deploy will refuse to proceed if any are missing or still contain a placeholder):

| Variable | Description |
|---|---|
| `LLM_BASE_URL` | LLM endpoint, e.g. `https://<runpod-id>.proxy.runpod.net/v1` |
| `LLM_API_KEY` | API key for the LLM server |
| `LLM_MODEL` | Model name served by the LLM endpoint (e.g. `nemotron-2-30B-A3B`) |

**Optional variables** (add when the relevant features are needed):

| Variable | Default | Description |
|---|---|---|
| `LLM_EMBEDDING_BASE_URL` | — | Embedding endpoint (if separate from LLM) |
| `LLM_EMBEDDING_API_KEY` | — | API key for the embedding endpoint |
| `LLM_EMBEDDING_MODEL` | — | Embedding model name |
| `LOCAL_STORAGE_PATH` | `/opt/sunnitai-be/storage` | Where PDFs and outputs are stored |
| `BLOB_CONTAINER_NAME` | `cdp-ext` | Storage container name |
| `WATCH_DIR` | `/opt/sunnitai-be/inbox` | Directory watched by the watcher service for new PDFs |
| `WATCHER_POLL_SECONDS` | `10` | How often the watcher scans for new files |
| `NEO4J_URI` | — | Neo4J bolt URI — if unset, watcher skips DB write and saves JSON only |
| `NEO4J_USER` | `neo4j` | Neo4J username |
| `NEO4J_PASSWORD` | — | Neo4J password |
| `NEO4J_DATABASE` | `neo4j` | Neo4J database name |

> `.env.production` is excluded from git and never uploaded anywhere other than the server. It is deployed as `.env` with `chmod 600`.

---

## Running a release

```bash
bash release.sh
```

That's it. The script handles everything and exits non-zero on any failure.

### What happens

| Step | What it does |
|---|---|
| **Preflight** | Checks SSH key exists, `.env.production` is present and has no placeholder values, and the server is reachable |
| **1 — Upload source** | `rsync` sends only changed files to `/opt/sunnitai-be` on the server. Excludes `.git`, caches, secrets, and local data directories. Incremental after the first run. |
| **2 — Deploy env** | Copies `.env.production` → `/opt/sunnitai-be/.env` with `chmod 600` |
| **3 — Upload deploy script** | Copies `deploy.sh` to the server |
| **4 — Remote build & start** | Runs `deploy.sh` on the server (see [What deploy.sh does](#what-deploysh-does)) |
| **5 — Health check** | Hits both API health endpoints to confirm services are up |

### First-run note

The first deploy takes **5–10 minutes** — it installs all Python dependencies and downloads the spaCy Italian model (`it_core_news_lg`, ~600 MB). Subsequent deploys are fast because the virtualenv is reused.

---

## What deploy.sh does

`deploy.sh` runs **on the server** and is not meant to be called directly.

1. Checks Python 3.11 is installed (installs if not) and that system libs are present
2. Copies the current source to `/opt/sunnitai-be-previous` (enables one-command rollback)
3. Creates/updates a virtualenv at `/opt/sunnitai-be/venv` and runs `pip install` from `requirements.txt`
4. Downloads the spaCy `it_core_news_lg` model (~600 MB) if not already present — only on first deploy
5. Writes systemd unit files for all three services to `/etc/systemd/system/`
6. Reloads systemd and restarts all three services
7. Waits 6 seconds, checks `systemctl is-active` for each service, and prints journal logs if any failed

A timestamped log of every deploy is appended to `/var/log/sunnitai-be-deploy.log` on the server.

---

## Services

| Service | systemd unit | Port | Description |
|---|---|---|---|
| Functions API | `sunnitai-functions` | 7071 | Main parsing & analysis API (`azure_func_compat` FastAPI shim) |
| VMAI API | `sunnitai-vmai` | 2025 | Requirements extraction API |
| PDF Watcher | `sunnitai-watcher` | — | Monitors `WATCH_DIR` for new PDFs and runs the full ingestion pipeline → Neo4J |

### Health checks

```
http://204.168.183.198:7071/api/health
http://204.168.183.198:2025/health
```

### Ingesting a PDF via the watcher

Drop any PDF into `WATCH_DIR` on the server — the watcher picks it up within `WATCHER_POLL_SECONDS` seconds, runs the full pipeline (parse → analyse → flatten → Neo4J write), and moves the file to `done/` on success or `failed/` on error:

```bash
scp -i ~/.ssh/server_key my_document.pdf root@204.168.183.198:/opt/sunnitai-be/inbox/
```

---

## Post-deploy management

All commands run from your Mac via SSH:

```bash
# Stream live logs
ssh -i ~/.ssh/server_key root@204.168.183.198 'journalctl -u sunnitai-functions -f'
ssh -i ~/.ssh/server_key root@204.168.183.198 'journalctl -u sunnitai-vmai -f'
ssh -i ~/.ssh/server_key root@204.168.183.198 'journalctl -u sunnitai-watcher -f'

# Check service status
ssh -i ~/.ssh/server_key root@204.168.183.198 'systemctl status sunnitai-functions sunnitai-vmai sunnitai-watcher'

# Restart a service
ssh -i ~/.ssh/server_key root@204.168.183.198 'systemctl restart sunnitai-functions'

# View the deploy log
ssh -i ~/.ssh/server_key root@204.168.183.198 'tail -100 /var/log/sunnitai-be-deploy.log'
```

---

## Rollback

If a deploy breaks a service, revert to the previous source in one command:

```bash
ssh -i ~/.ssh/server_key root@204.168.183.198 '
  systemctl stop sunnitai-functions sunnitai-vmai sunnitai-watcher
  rm -rf /opt/sunnitai-be/src
  cp -r /opt/sunnitai-be-previous /opt/sunnitai-be/src
  systemctl start sunnitai-functions sunnitai-vmai sunnitai-watcher
'
```

> The previous source is backed up automatically to `/opt/sunnitai-be-previous` at the start of each deploy.

---

## Troubleshooting

**SSH key permission denied**
```bash
chmod 600 ~/.ssh/server_key
```

**Health check fails after deploy**  
The service may still be initialising. Check logs:
```bash
ssh -i ~/.ssh/server_key root@204.168.183.198 'journalctl -u sunnitai-functions -n 50 --no-pager'
ssh -i ~/.ssh/server_key root@204.168.183.198 'journalctl -u sunnitai-vmai -n 50 --no-pager'
```

**Watcher not processing files**  
Check the watcher logs and verify `WATCH_DIR` is set in `.env`:
```bash
ssh -i ~/.ssh/server_key root@204.168.183.198 'journalctl -u sunnitai-watcher -n 50 --no-pager'
```

**Service fails to start**  
Journal logs will show exactly which env var is missing or misconfigured:
```bash
ssh -i ~/.ssh/server_key root@204.168.183.198 'journalctl -u sunnitai-functions -n 50 --no-pager'
```

**Deploy to a different server**  
Override the target without editing the script:
```bash
DEPLOY_SERVER=1.2.3.4 bash release.sh
```
