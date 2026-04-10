#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${SCRIPT_DIR}"

cd "${PROJECT_ROOT}"
source venv/bin/activate
fastapi run call_fast_api.py --port 2025
