# SunnitAI-BE — Release Guide

Deploys the backend to the production server (`204.168.183.198`) as a Docker container.  
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

1. Checks Docker is installed and the daemon is running
2. Tags the current image as `sunnitai-be:previous` (enables one-command rollback)
3. Builds `sunnitai-be:latest` from the uploaded source
4. Stops and removes the old container (15-second graceful shutdown)
5. Starts the new container with `--restart unless-stopped`
6. Waits 15 seconds, then verifies the container is still running and prints the last 20 log lines

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
ssh -i ~/.ssh/server_key root@204.168.183.198 'docker logs sunnitai-be -f'

# Check container status
ssh -i ~/.ssh/server_key root@204.168.183.198 'docker ps'

# Open a shell inside the container
ssh -i ~/.ssh/server_key root@204.168.183.198 'docker exec -it sunnitai-be bash'

# View the deploy log
ssh -i ~/.ssh/server_key root@204.168.183.198 'tail -100 /var/log/sunnitai-be-deploy.log'
```

---

## Rollback

If a deploy breaks the service, revert to the previous image in one command:

```bash
ssh -i ~/.ssh/server_key root@204.168.183.198 '
  docker stop sunnitai-be && docker rm sunnitai-be
  docker run --detach --name sunnitai-be --restart unless-stopped \
    --env-file /opt/sunnitai-be/.env \
    -p 7071:7071 -p 2025:2025 \
    sunnitai-be:previous
'
```

> The `previous` tag is created automatically at the start of each deploy. It points to whatever was running before.

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

**Container exits immediately**  
The pre-flight check in the entrypoint will print exactly which env var is missing or misconfigured. Look for `❌ [preflight]` lines in the logs.

**Deploy to a different server**  
Override the target without editing the script:
```bash
DEPLOY_SERVER=1.2.3.4 bash release.sh
```
