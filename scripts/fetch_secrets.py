#!/usr/bin/env python3
"""
Script per generare un file .env locale con le variabili necessarie al progetto.

Azure Key Vault è stato rimosso. Le variabili d'ambiente vanno impostate
manualmente o copiate da un vault sicuro del team (Bitwarden, 1Password, etc.).

Uso:
    python scripts/fetch_secrets.py [output_file]

Output:
    Crea un file .env template nella root del progetto.
"""
import sys
from pathlib import Path
from datetime import datetime

TEMPLATE = """\
# Generato da scripts/fetch_secrets.py il {date}
# Compila i valori prima di usare il server.

# ── LLM (self-hosted OpenAI-compatible endpoint) ─────────────────────────────
LLM_BASE_URL=https://<runpod-or-server>/v1
LLM_API_KEY=<your-api-key>
LLM_MODEL=nemotron-2-30B-A3B

# Fallback LLM (opzionale)
# LLM_BASE_URL_FALLBACK=
# LLM_API_KEY_FALLBACK=
# LLM_FALLBACK_MODEL=

# ── Embeddings ────────────────────────────────────────────────────────────────
LLM_EMBEDDING_BASE_URL=https://<runpod-or-server>/v1
LLM_EMBEDDING_API_KEY=<your-api-key>
LLM_EMBEDDING_MODEL=<embedding-model-name>

# ── Storage locale ────────────────────────────────────────────────────────────
LOCAL_STORAGE_PATH=/opt/sunnitai-be/storage
CONTAINER_NAME_EXT=cdp-ext

# ── Database ──────────────────────────────────────────────────────────────────
DB_SERVER=<host>
DB_NAME=<dbname>
DB_USER=<user>
DB_PASSWORD=<password>

# ── Redis ─────────────────────────────────────────────────────────────────────
REDIS_HOST=localhost
REDIS_PORT=6379

# ── App ───────────────────────────────────────────────────────────────────────
NOTIFICATION_EMAIL=
"""


def main():
    output_file = sys.argv[1] if len(sys.argv) > 1 else ".env.template"
    output_path = Path(__file__).parent.parent / output_file

    content = TEMPLATE.format(date=datetime.now().strftime("%Y-%m-%d %H:%M"))

    with open(output_path, "w") as f:
        f.write(content)

    print(f"Template scritto in: {output_path}")
    print("Compila i valori e rinomina il file in .env prima di avviare il server.")


if __name__ == "__main__":
    main()
