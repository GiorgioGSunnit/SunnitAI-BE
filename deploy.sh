#!/usr/bin/env bash
# =============================================================================
# SunnitAI-BE — Server-Side Deploy Script
#
# Runs ON THE SERVER after release.sh has uploaded the source code.
# Do not run this manually from your Mac — use release.sh instead.
#
# What it does:
#   1. Checks system requirements (Docker)
#   2. Backs up the currently running container (for quick rollback)
#   3. Builds the new Docker image from the uploaded source
#   4. Stops and removes the old container
#   5. Starts the new container
#   6. Verifies startup via container health check
# =============================================================================

set -euo pipefail

APP_DIR="/opt/sunnitai-be"
IMAGE_NAME="sunnitai-be"
CONTAINER_NAME="sunnitai-be"
PORT_FUNCTIONS=7071
PORT_VMAI=2025
LOG_FILE="/var/log/sunnitai-be-deploy.log"

log()  { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
ok()   { echo "[$(date '+%H:%M:%S')] [ok] $*" | tee -a "$LOG_FILE"; }
fail() { echo "[$(date '+%H:%M:%S')] [ERROR] $*" | tee -a "$LOG_FILE"; exit 1; }

log "=== SunnitAI-BE — Server Deploy ==="
log "Directory: $APP_DIR"
log "Image:     $IMAGE_NAME"

# -------------------------------------------------------
# 1. System checks
# -------------------------------------------------------
log ""
log "[1/5] System checks..."

command -v docker &>/dev/null || fail "Docker is not installed. Run: curl -fsSL https://get.docker.com | sh"
ok "Docker found: $(docker --version)"

[ -f "$APP_DIR/Dockerfile" ]          || fail "Dockerfile not found in $APP_DIR — was the upload successful?"
[ -f "$APP_DIR/.env" ]                || fail ".env not found in $APP_DIR — deployment aborted."
[ -f "$APP_DIR/requirements.txt" ]    || fail "requirements.txt not found."

# Ensure Docker daemon is running
docker info &>/dev/null || fail "Docker daemon is not running. Run: systemctl start docker"
ok "Docker daemon running"

# -------------------------------------------------------
# 2. Backup current container (tag it as :previous)
# -------------------------------------------------------
log ""
log "[2/5] Backing up current image..."

if docker inspect "$CONTAINER_NAME" &>/dev/null; then
    CURRENT_IMAGE=$(docker inspect --format='{{.Config.Image}}' "$CONTAINER_NAME" 2>/dev/null || true)
    if [ -n "$CURRENT_IMAGE" ]; then
        docker tag "$CURRENT_IMAGE" "${IMAGE_NAME}:previous" 2>/dev/null || true
        ok "Previous image tagged as ${IMAGE_NAME}:previous (rollback available)"
    fi
else
    log "  No existing container found — this is a fresh deploy"
fi

# -------------------------------------------------------
# 3. Build new image
# -------------------------------------------------------
log ""
log "[3/5] Building Docker image (this may take several minutes on first run)..."
log "  Note: first build downloads spaCy it_core_news_lg (~600 MB) — be patient"

cd "$APP_DIR"

docker build \
    --tag "${IMAGE_NAME}:latest" \
    --tag "${IMAGE_NAME}:$(date +%Y%m%d-%H%M%S)" \
    --file Dockerfile \
    . 2>&1 | tee -a "$LOG_FILE"

ok "Image built: ${IMAGE_NAME}:latest"

# -------------------------------------------------------
# 4. Stop and remove old container
# -------------------------------------------------------
log ""
log "[4/5] Replacing container..."

if docker inspect "$CONTAINER_NAME" &>/dev/null; then
    log "  Stopping $CONTAINER_NAME..."
    docker stop "$CONTAINER_NAME" --time 15 || true
    docker rm   "$CONTAINER_NAME"           || true
    ok "Old container removed"
fi

# -------------------------------------------------------
# 5. Start new container
# -------------------------------------------------------
log ""
log "[5/5] Starting new container..."

docker run \
    --detach \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    --env-file "$APP_DIR/.env" \
    --publish "${PORT_FUNCTIONS}:${PORT_FUNCTIONS}" \
    --publish "${PORT_VMAI}:${PORT_VMAI}" \
    --volume "${APP_DIR}/src/be/src/lex_package/data:/app/src/be/src/lex_package/data" \
    "${IMAGE_NAME}:latest"

ok "Container started: $CONTAINER_NAME"

# -------------------------------------------------------
# Verify startup
# -------------------------------------------------------
log ""
log "Waiting for container to initialise (bootstrap + Azure Functions startup)..."
sleep 15

# Check container is still running (didn't crash immediately)
STATUS=$(docker inspect --format='{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "missing")
if [ "$STATUS" != "running" ]; then
    log ""
    log "Container logs (last 50 lines):"
    docker logs "$CONTAINER_NAME" --tail 50 2>&1 | tee -a "$LOG_FILE" || true
    fail "Container is not running (status: $STATUS). Check logs above."
fi

ok "Container is running"

# Print last log lines for quick sanity check
log ""
log "Recent logs:"
docker logs "$CONTAINER_NAME" --tail 20 2>&1 | tee -a "$LOG_FILE" || true

# -------------------------------------------------------
# Summary
# -------------------------------------------------------
log ""
log "==========================================="
log "  DEPLOY COMPLETE"
log "==========================================="
log ""
log "  Container:      $CONTAINER_NAME"
log "  Functions API:  http://localhost:$PORT_FUNCTIONS"
log "  VMAI API:       http://localhost:$PORT_VMAI"
log "  Full log:       $LOG_FILE"
log ""
log "  Manage:"
log "    Logs:     docker logs $CONTAINER_NAME -f"
log "    Stop:     docker stop $CONTAINER_NAME"
log "    Shell:    docker exec -it $CONTAINER_NAME bash"
log ""
log "  Rollback (if needed):"
log "    docker stop $CONTAINER_NAME && docker rm $CONTAINER_NAME"
log "    docker run --detach --name $CONTAINER_NAME --restart unless-stopped \\"
log "      --env-file $APP_DIR/.env \\"
log "      -p ${PORT_FUNCTIONS}:${PORT_FUNCTIONS} -p ${PORT_VMAI}:${PORT_VMAI} \\"
log "      ${IMAGE_NAME}:previous"
log ""
