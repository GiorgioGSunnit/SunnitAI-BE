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
