#!/usr/bin/env bash
# =============================================================================
# SunnitAI-BE — One-Shot Release from Local Mac
#
# Usage:
#   1. Create .env.production in the project root with your secrets
#      (see .env.example for required keys)
#   2. Run:  bash release.sh
#
# What it does:
#   1. Preflight checks (SSH key, .env.production, server connectivity)
#   2. Uploads source code via rsync (excludes data, caches, secrets)
#   3. Copies .env.production → .env on the server
#   4. Uploads deploy.sh and runs it remotely
#   5. Verifies the service came up with a health check
# =============================================================================

set -euo pipefail

# -------------------------------------------------------
# Configuration
# -------------------------------------------------------
SERVER="${DEPLOY_SERVER:-204.168.183.198}"
SSH_KEY="$HOME/.ssh/server_key"
SSH_OPTS="-i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
REMOTE_DIR="/opt/sunnitai-be"
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$PROJECT_ROOT/.env.production"

PORT_FUNCTIONS=7071   # Azure Functions host
PORT_VMAI=2025        # uvicorn / call_fast_api

# -------------------------------------------------------
# Preflight checks
# -------------------------------------------------------
echo "=== SunnitAI-BE — Release ==="
echo ""

if [ ! -f "$SSH_KEY" ]; then
    echo "ERROR: SSH key not found at $SSH_KEY"
    echo "Fix:   puttygen ~/Downloads/private.ppk -O private-openssh -o ~/.ssh/server_key"
    exit 1
fi
chmod 600 "$SSH_KEY"
echo "[ok] SSH key found"

if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: .env.production not found at $ENV_FILE"
    echo "Fix:   cp .env.example .env.production  &&  nano .env.production"
    exit 1
fi
echo "[ok] .env.production found"

if grep -qE "PASTE_YOUR|<YOUR_|CHANGE_ME" "$ENV_FILE"; then
    echo "ERROR: .env.production still contains placeholder values."
    echo "Fix:   Edit .env.production and replace all placeholders with real values."
    exit 1
fi
echo "[ok] No placeholder values"

echo ""
echo "[1/5] Checking server connectivity..."
if ! ssh $SSH_OPTS "root@$SERVER" "echo ok" > /dev/null 2>&1; then
    echo "ERROR: Cannot reach $SERVER via SSH."
    echo ""
    echo "Possible causes:"
    echo "  1. Server is off           — check your cloud dashboard"
    echo "  2. IP changed              — verify current public IP"
    echo "  3. Port 22 blocked         — check firewall / security groups"
    echo "  4. Wrong key or passphrase — try: ssh -i $SSH_KEY root@$SERVER"
    exit 1
fi
echo "  Connected to $SERVER"

# -------------------------------------------------------
# Upload source code
# rsync sends only changed files — incremental after first run
# -------------------------------------------------------
echo ""
echo "[2/5] Uploading source to $SERVER:$REMOTE_DIR ..."
ssh $SSH_OPTS "root@$SERVER" "mkdir -p $REMOTE_DIR"

rsync -az --delete \
    --exclude '.git/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '*.pyo' \
    --exclude '.DS_Store' \
    --exclude '.env' \
    --exclude '.env.*' \
    --exclude 'venv/' \
    --exclude '.venv/' \
    --exclude 'release.sh' \
    --exclude 'src/be/src/lex_package/data/' \
    --exclude 'src/be/src/lex_package/out_parser/' \
    --exclude 'src/be/src/lex_package/out_analisi/' \
    --exclude 'src/be/src/lex_package/out_flat/' \
    --exclude 'src/be/src/lex_package/out_schema_attuativo/' \
    --exclude 'src/be/src/lex_package/out_enriched/' \
    --exclude '*.log' \
    -e "ssh $SSH_OPTS" \
    "$PROJECT_ROOT/" "root@$SERVER:$REMOTE_DIR/"

echo "  Upload complete"

# -------------------------------------------------------
# Copy production env and lock down permissions
# -------------------------------------------------------
echo ""
echo "[3/5] Setting production environment..."
scp $SSH_OPTS "$ENV_FILE" "root@$SERVER:$REMOTE_DIR/.env"
ssh $SSH_OPTS "root@$SERVER" "chmod 600 $REMOTE_DIR/.env"
echo "  .env deployed and secured (chmod 600)"

# -------------------------------------------------------
# Upload and run deploy script
# -------------------------------------------------------
echo ""
echo "[4/5] Uploading deploy script..."
scp $SSH_OPTS "$PROJECT_ROOT/deploy.sh" "root@$SERVER:$REMOTE_DIR/deploy.sh"
ssh $SSH_OPTS "root@$SERVER" "chmod +x $REMOTE_DIR/deploy.sh"

echo "[5/5] Running deployment on server (first run takes ~5-10 min for Docker build + spaCy download)..."
echo ""
ssh $SSH_OPTS "root@$SERVER" "bash $REMOTE_DIR/deploy.sh"

# -------------------------------------------------------
# Post-deploy health check
# -------------------------------------------------------
echo ""
echo "Waiting for service to be ready..."
sleep 8

HEALTH_URL="http://$SERVER:$PORT_FUNCTIONS/api/health"
if curl -sf --max-time 10 "$HEALTH_URL" > /dev/null 2>&1; then
    echo "  Health check PASSED ($HEALTH_URL)"
else
    echo "  WARNING: Health check did not respond at $HEALTH_URL"
    echo "  The service may still be starting up. Check with:"
    echo "    ssh -i $SSH_KEY root@$SERVER 'docker logs sunnitai-be --tail 50'"
fi

echo ""
echo "==========================================="
echo "  RELEASE COMPLETE"
echo "==========================================="
echo ""
echo "  Functions API:  http://$SERVER:$PORT_FUNCTIONS"
echo "  VMAI API:       http://$SERVER:$PORT_VMAI"
echo "  Health:         http://$SERVER:$PORT_FUNCTIONS/api/health"
echo ""
echo "  Useful commands:"
echo "    Logs:    ssh -i $SSH_KEY root@$SERVER 'docker logs sunnitai-be -f'"
echo "    Status:  ssh -i $SSH_KEY root@$SERVER 'docker ps'"
echo "    Shell:   ssh -i $SSH_KEY root@$SERVER 'docker exec -it sunnitai-be bash'"
echo ""
