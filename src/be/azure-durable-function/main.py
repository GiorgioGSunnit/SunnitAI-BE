"""
ASGI entry point for the Functions API (port 7071).

Imports function_app which registers all @app.route() handlers as FastAPI
routes via the azure_func_compat shim, then exports the FastAPI app so
uvicorn can serve it:

    uvicorn main:app --host 0.0.0.0 --port 7071
"""
import logging

# ── Configure root logger before anything else loads ─────────────────────────
# Uvicorn only sets up its own loggers (uvicorn.*). Without this, all
# application logger.info/error calls are silently discarded.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s %(name)s — %(message)s",
)

import function_app  # noqa: F401 — side-effect: registers all routes

from azure_func_compat import _fastapi_app as app  # noqa: F401


# ── Suppress polling noise from uvicorn access logs ───────────────────────────
# GET /api/job/<id> is polled every ~5s by the FE. At 4 concurrent clients
# that's ~50 lines/minute of noise that buries real application logs.
class _SuppressJobPolling(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/api/job/" not in record.getMessage()

logging.getLogger("uvicorn.access").addFilter(_SuppressJobPolling())


@app.get("/api/health")
async def health():
    """Health check for release.sh and monitoring."""
    return {"status": "ok", "service": "functions-api"}


__all__ = ["app"]
