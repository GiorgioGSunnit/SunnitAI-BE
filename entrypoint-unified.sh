#!/usr/bin/env bash
# =============================================================================
# SunnitAI-BE — Unified Entrypoint
#
# Starts two services:
#   1. Functions API  — port 7071  (was Azure Functions, now FastAPI via uvicorn)
#   2. VMAI API       — port 2025  (FastAPI / call_fast_api.py, unchanged)
#
# Environment variables are loaded from .env by python-dotenv inside each app.
# No Azure Key Vault bootstrap is needed.
# =============================================================================
set -euo pipefail

export PYTHONPATH="/app/src:/app/src/be/src:${PYTHONPATH:-}"
export IPVMAI="${IPVMAI:-127.0.0.1}"
export VMAI_PORT="${VMAI_PORT:-2025}"
export FUNCTIONS_PORT="${FUNCTIONS_PORT:-7071}"

# =============================================================================
# Pre-flight checks — fail fast with a clear message before starting services
# =============================================================================

_fail() { echo "❌ [preflight] $*" >&2; exit 1; }
_warn() { echo "⚠️  [preflight] $*" >&2; }
_ok()   { echo "✅ [preflight] $*"; }

echo "Running pre-flight checks..."

# ── Required LLM vars ─────────────────────────────────────────────────────────
[[ -n "${LLM_BASE_URL:-}" ]]  || _fail "LLM_BASE_URL is not set. Set it to your LLM endpoint (e.g. http://server:8000/v1)."
[[ -n "${LLM_API_KEY:-}" ]]   || _fail "LLM_API_KEY is not set. Set it to your API key (use EMPTY for unauthenticated local servers)."
[[ -n "${LLM_MODEL:-}" ]]     || _fail "LLM_MODEL is not set. Set it to the model/deployment name on your LLM server."
[[ -n "${LLM_EMBEDDING_MODEL:-}" ]] || _fail "LLM_EMBEDDING_MODEL is not set. Set it to the embedding model name on your LLM server."

_ok "LLM vars present (base_url=${LLM_BASE_URL}, model=${LLM_MODEL}, embedding=${LLM_EMBEDDING_MODEL})"

# ── LLM endpoint reachability ─────────────────────────────────────────────────
if curl --silent --max-time 5 --output /dev/null "${LLM_BASE_URL%/}/models" 2>/dev/null; then
    _ok "LLM endpoint reachable at ${LLM_BASE_URL}"
else
    _warn "LLM endpoint ${LLM_BASE_URL} did not respond in 5 s — continuing anyway (server may not expose /models)."
fi

# ── Warn if CHANGE_ME placeholders are still set ──────────────────────────────
for var in LLM_BASE_URL LLM_API_KEY LLM_MODEL LLM_EMBEDDING_MODEL; do
    [[ "${!var}" == "CHANGE_ME" ]] && _fail "${var} is still set to the placeholder value 'CHANGE_ME'. Update it in your .env file."
done

# ── Local storage path ────────────────────────────────────────────────────────
_storage_path="${LOCAL_STORAGE_PATH:-/opt/sunnitai-be/storage}"
mkdir -p "${_storage_path}" 2>/dev/null || _fail "Cannot create LOCAL_STORAGE_PATH '${_storage_path}'. Check directory permissions."
[[ -w "${_storage_path}" ]] || _fail "LOCAL_STORAGE_PATH '${_storage_path}' is not writable."
_ok "Local storage path OK (${_storage_path})"

echo "Pre-flight checks passed."
echo ""

# =============================================================================
echo "Starting SunnitAI-BE services..."
echo "  Functions API → port ${FUNCTIONS_PORT}"
echo "  VMAI API      → port ${VMAI_PORT}"

# ── Service 1: Functions API (port 7071) ─────────────────────────────────────
python -m uvicorn main:app \
    --host 0.0.0.0 \
    --port "${FUNCTIONS_PORT}" \
    --app-dir /home/site/wwwroot \
    --log-level info \
    &
func_pid=$!

# ── Service 2: VMAI FastAPI (port 2025) ──────────────────────────────────────
python -m uvicorn call_fast_api:app \
    --host 0.0.0.0 \
    --port "${VMAI_PORT}" \
    --app-dir /app/requirement_extration \
    --log-level info \
    &
vmai_pid=$!

echo "Both services started (PIDs: func=${func_pid}, vmai=${vmai_pid})"

# Wait for either process to exit, then shut down the other
wait -n "${func_pid}" "${vmai_pid}"
echo "One service exited — shutting down..."
kill -TERM "${func_pid}" "${vmai_pid}" 2>/dev/null || true
wait
