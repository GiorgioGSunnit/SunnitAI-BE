# SunnitAI-BE — Release Guide

Deploys the backend to the production server (`204.168.183.198`) as two systemd services running inside a Python virtualenv. No Docker required.  
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
cp .env.example .env.production
nano .env.production
```

**Required variables** (the deploy will refuse to proceed if any are missing or still `CHANGE_ME`):

| Variable | Description |
|---|---|
| `LLM_BASE_URL` | LLM endpoint, e.g. `http://server:8000/v1` |
| `LLM_API_KEY` | API key — use `EMPTY` for unauthenticated local servers |
| `LLM_MODEL` | Model/deployment name on the LLM server |
| `LLM_EMBEDDING_MODEL` | Embedding model name on the LLM server |
| `LOCAL_STORAGE_PATH` | Where PDFs and outputs are stored on the server (default: `/opt/sunnitai-be/storage`) |
| `BLOB_CONTAINER_NAME` | Storage container name (default: `sunnitai`) |

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
| **5 — Health check** | Hits `http://204.168.183.198:7071/api/health` to confirm the service is up |

### First-run note

The first Docker build takes **5–10 minutes** — it downloads the spaCy Italian model (`it_core_news_lg`, ~600 MB). Subsequent deploys are fast because Docker caches the layer.

---

## What deploy.sh does

`deploy.sh` runs **on the server** and is not meant to be called directly.

1. Checks Python 3.11 is installed (installs via deadsnakes PPA if not) and that system libs are present
2. Copies the current source to `/opt/sunnitai-be-previous` (enables one-command rollback)
3. Creates/updates a virtualenv at `/opt/sunnitai-be/venv` and runs `pip install` for both requirement files
4. Downloads the spaCy `it_core_news_lg` model (~600 MB) if not already present — only on first deploy
5. Writes systemd unit files for both services to `/etc/systemd/system/`
6. Reloads systemd and restarts both services
7. Waits 6 seconds, checks `systemctl is-active` for each service, and prints journal logs if either failed

A timestamped log of every deploy is appended to `/var/log/sunnitai-be-deploy.log` on the server.

---

## Service endpoints

| Service | Port | URL |
|---|---|---|
| Functions API | 7071 | `http://204.168.183.198:7071` |
| VMAI API | 2025 | `http://204.168.183.198:2025` |
| Health check | 7071 | `http://204.168.183.198:7071/api/health` |

---

## Post-deploy management

All commands run from your Mac via SSH:

```bash
# Stream live logs
ssh -i ~/.ssh/server_key root@204.168.183.198 'journalctl -u sunnitai-functions -f'
ssh -i ~/.ssh/server_key root@204.168.183.198 'journalctl -u sunnitai-vmai -f'

# Check service status
ssh -i ~/.ssh/server_key root@204.168.183.198 'systemctl status sunnitai-functions sunnitai-vmai'

# Restart a service
ssh -i ~/.ssh/server_key root@204.168.183.198 'systemctl restart sunnitai-functions'

# View the deploy log
ssh -i ~/.ssh/server_key root@204.168.183.198 'tail -100 /var/log/sunnitai-be-deploy.log'
```

---

## Rollback

If a deploy breaks the service, revert to the previous source in one command:

```bash
ssh -i ~/.ssh/server_key root@204.168.183.198 '
  systemctl stop sunnitai-functions sunnitai-vmai
  rm -rf /opt/sunnitai-be/src
  cp -r /opt/sunnitai-be-previous /opt/sunnitai-be/src
  systemctl start sunnitai-functions sunnitai-vmai
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
ssh -i ~/.ssh/server_key root@204.168.183.198 'docker logs sunnitai-be --tail 50'
```

**Service fails to start**  
The pre-flight check in the systemd `ExecStartPre` step will print exactly which env var is missing or misconfigured:
```bash
journalctl -u sunnitai-functions -n 50 --no-pager
```

**Deploy to a different server**  
Override the target without editing the script:
```bash
DEPLOY_SERVER=1.2.3.4 bash release.sh
```
