#!/usr/bin/env bash
set -euo pipefail

export IPVMAI="${IPVMAI:-127.0.0.1}"
export VMAI_PORT="${VMAI_PORT:-2025}"
export PYTHONPATH="/app/src:/app/src/be/src:${PYTHONPATH:-}"

# Bootstrap: load secrets from KeyVault into environment BEFORE starting apps
echo "Running bootstrap to load KeyVault secrets..."
python -c "
import sys
sys.path.insert(0, '/app/src')
import os

# Import bootstrap which populates env vars
import core.bootstrap

# Write env vars to file for shell to source
env_file = '/tmp/.env_bootstrap'
keys = ['AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_API_VERSION', 
        'AZURE_OPENAI_DEPLOYMENT_NAME', 'LLM_MODEL', 'LLM_PROVIDER', 'LLM_AZURE_DEPLOYMENT',
        'AZURE_STORAGE_ACCOUNT_NAME', 'AZURE_STORAGE_ACCOUNT_URL', 'CONTAINER_NAME',
        'CONNECTION_STRING', 'AZURE_API_VERSION']

with open(env_file, 'w') as f:
    for key in keys:
        val = os.getenv(key, '')
        if val:
            # Escape for shell
            val_escaped = val.replace(\"'\", \"'\\\"'\\\"'\")
            f.write(f\"export {key}='{val_escaped}'\\n\")
            print(f'  Loaded: {key}')
"

# Source the env file
if [ -f /tmp/.env_bootstrap ]; then
    source /tmp/.env_bootstrap
    echo "Bootstrap complete - environment loaded."
else
    echo "Warning: Bootstrap env file not created."
fi

# Run uvicorn without changing directory to avoid PYTHONPATH conflicts
# Use app-dir instead of pushd/popd
python -m uvicorn call_fast_api:app --host 0.0.0.0 --port "${VMAI_PORT}" --app-dir /app/requirement_extration &
vmai_pid=$!

/azure-functions-host/Microsoft.Azure.WebJobs.Script.WebHost &
func_pid=$!

wait -n "${vmai_pid}" "${func_pid}"
kill -TERM "${vmai_pid}" "${func_pid}" 2>/dev/null || true
wait
