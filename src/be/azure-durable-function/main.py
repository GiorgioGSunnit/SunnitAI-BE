"""
ASGI entry point for the Functions API (port 7071).

Imports function_app which registers all @app.route() handlers as FastAPI
routes via the azure_func_compat shim, then exports the FastAPI app so
uvicorn can serve it:

    uvicorn main:app --host 0.0.0.0 --port 7071
"""
import function_app  # noqa: F401 — side-effect: registers all routes

from azure_func_compat import _fastapi_app as app  # noqa: F401


@app.get("/api/health")
async def health():
    """Health check for release.sh and monitoring."""
    return {"status": "ok", "service": "functions-api"}


__all__ = ["app"]
