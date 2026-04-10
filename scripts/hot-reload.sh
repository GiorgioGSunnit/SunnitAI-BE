#!/usr/bin/env bash
#
# hot-reload.sh - Upload local code to pod and restart server
# Usage: ./scripts/hot-reload.sh
#
set -euo pipefail

NAMESPACE="aiac"
POD_PREFIX="aiac-be-service"
UVICORN_PORT="2025"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# Get script directory (project root)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "═══════════════════════════════════════════"
echo "  HOT RELOAD - Side-loading to AKS Pod"
echo "═══════════════════════════════════════════"
echo ""

# 1. Find the BE pod
log "Finding pod..."
POD=$(kubectl get pods -n "$NAMESPACE" --no-headers -o custom-columns=":metadata.name" | grep "^${POD_PREFIX}" | head -1)

if [[ -z "$POD" ]]; then
    error "No pod found with prefix '$POD_PREFIX' in namespace '$NAMESPACE'"
fi

log "Found pod: $POD"

# 2. Kill existing uvicorn
log "Stopping uvicorn..."
UVICORN_PID=$(kubectl exec "$POD" -n "$NAMESPACE" -- sh -c "
    for pid in \$(ls /proc | grep -E '^[0-9]+$'); do
        if [ -f /proc/\$pid/cmdline ] 2>/dev/null; then
            if cat /proc/\$pid/cmdline 2>/dev/null | tr '\\0' ' ' | grep -q 'uvicorn.*call_fast_api'; then
                echo \$pid
                break
            fi
        fi
    done
" 2>/dev/null || echo "")

if [[ -n "$UVICORN_PID" ]]; then
    kubectl exec "$POD" -n "$NAMESPACE" -- sh -c "kill $UVICORN_PID 2>/dev/null || true"
    log "Killed uvicorn (PID $UVICORN_PID)"
    sleep 1
else
    warn "No uvicorn process found (may already be stopped)"
fi

# 3. Copy code to pod (replicating Dockerfile COPY commands)
log "Copying code to pod..."

# Remove old directories first, then copy fresh
# kubectl cp creates nested dirs if dest exists, so we delete first

echo "  → src/core/ → /app/src/core/"
kubectl exec "$POD" -n "$NAMESPACE" -- rm -rf /app/src/core
kubectl cp src/core "$POD":/app/src/core -n "$NAMESPACE"

echo "  → src/utils/ → /app/src/utils/"
kubectl exec "$POD" -n "$NAMESPACE" -- rm -rf /app/src/utils
kubectl cp src/utils "$POD":/app/src/utils -n "$NAMESPACE"

echo "  → src/be/src/ → /app/src/be/src/"
kubectl exec "$POD" -n "$NAMESPACE" -- rm -rf /app/src/be/src
kubectl cp src/be/src "$POD":/app/src/be/src -n "$NAMESPACE"

echo "  → src/be/requirement_extration/ → /app/requirement_extration/"
kubectl exec "$POD" -n "$NAMESPACE" -- rm -rf /app/requirement_extration
kubectl cp src/be/requirement_extration "$POD":/app/requirement_extration -n "$NAMESPACE"

# Optional: Azure Functions (uncomment if needed)
# echo "  → src/be/azure-durable-function/ → /home/site/wwwroot/"
# kubectl exec "$POD" -n "$NAMESPACE" -- rm -rf /home/site/wwwroot
# kubectl cp src/be/azure-durable-function "$POD":/home/site/wwwroot -n "$NAMESPACE"

log "Code copied successfully"

# 3b. Clear Python cache to force reimport
log "Clearing Python cache..."
kubectl exec "$POD" -n "$NAMESPACE" -- sh -c 'find /app -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true'

# 4. Start uvicorn with env vars
log "Starting uvicorn..."
kubectl exec "$POD" -n "$NAMESPACE" -- sh -c '
    # Source env vars from bootstrap
    if [ -f /tmp/.env_bootstrap ]; then
        . /tmp/.env_bootstrap
    fi
    
    # Set PYTHONPATH
    export PYTHONPATH="/app/src:/app/src/be/src"
    
    # Start uvicorn in background
    cd /app
    nohup python -m uvicorn call_fast_api:app \
        --host 0.0.0.0 \
        --port 2025 \
        --app-dir /app/requirement_extration \
        > /tmp/uvicorn.log 2>&1 &
    
    echo $!
'

# 5. Wait and verify
sleep 2
log "Verifying server is running..."

HEALTH=$(kubectl exec "$POD" -n "$NAMESPACE" -- sh -c "
    wget -q -O - http://localhost:$UVICORN_PORT/health 2>/dev/null || echo 'FAILED'
" 2>/dev/null || echo "FAILED")

if echo "$HEALTH" | grep -q "status"; then
    log "Server is UP and healthy!"
    echo ""
    echo "═══════════════════════════════════════════"
    echo -e "  ${GREEN}HOT RELOAD COMPLETE${NC}"
    echo "═══════════════════════════════════════════"
    echo ""
    echo "  Pod:    $POD"
    echo "  Server: http://localhost:$UVICORN_PORT"
    echo ""
    echo "  Test:   kubectl exec $POD -n $NAMESPACE -- wget -qO- http://localhost:$UVICORN_PORT/health"
    echo ""
else
    warn "Server may not be ready yet. Check logs:"
    echo "  kubectl exec $POD -n $NAMESPACE -- cat /tmp/uvicorn.log"
fi
