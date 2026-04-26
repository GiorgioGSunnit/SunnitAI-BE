"""
Azure Functions → FastAPI compatibility shim.

Provides the azure.functions API surface (FunctionApp, HttpRequest,
HttpResponse, AuthLevel) backed by a FastAPI application so that
function_app.py can run on a plain server without the Azure Functions host.

Usage in function_app.py:
    import azure_func_compat as func    # replaces: import azure.functions as func

Then run the app with:
    uvicorn main:app --host 0.0.0.0 --port 7071 --app-dir /home/site/wwwroot

Where main.py contains:
    import function_app          # registers all routes as a side-effect
    from azure_func_compat import _fastapi_app as app
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.datastructures import UploadFile as StarletteUploadFile

logger = logging.getLogger(__name__)

# ── The single FastAPI app that backs all registered routes ───────────────────

_fastapi_app: FastAPI = FastAPI(title="SunnitAI-BE Functions API")


# ── AuthLevel (kept as enum-like namespace for API parity) ───────────────────

class AuthLevel:
    ANONYMOUS = "anonymous"
    FUNCTION = "function"
    ADMIN = "admin"


# ── HttpRequest / HttpResponse (mimic azure.functions) ───────────────────────

class _UploadFile:
    """Minimal file-like object matching the Azure Functions / Werkzeug API."""

    def __init__(self, filename: str, content: bytes, content_type: str = ""):
        self.filename = filename
        self.content_type = content_type
        self.stream = io.BytesIO(content)

    def read(self) -> bytes:
        return self.stream.read()


class HttpRequest:
    """Wraps a Starlette Request so Azure Functions handlers receive the same API."""

    def __init__(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        params: Dict[str, str],
        body: bytes,
        route_params: Dict[str, str],
        files: Optional[Dict[str, "_UploadFile"]] = None,
        form: Optional[Dict[str, str]] = None,
    ):
        self.method = method
        self.url = url
        self.headers = headers
        self.params = params
        self._body = body
        self.route_params = route_params
        self.files = files or {}
        self.form = form or {}

    def get_body(self) -> bytes:
        return self._body

    def get_json(self) -> Any:
        return json.loads(self._body)


class HttpResponse:
    """Wraps an HTTP response so Azure Functions handlers return the same API."""

    def __init__(
        self,
        body: Any = "",
        *,
        status_code: int = 200,
        mimetype: str = "text/plain",
        headers: Optional[Dict[str, str]] = None,
        charset: str = "utf-8",
    ):
        if isinstance(body, bytes):
            self.body = body
        elif isinstance(body, str):
            self.body = body.encode(charset)
        elif body is None:
            self.body = b""
        else:
            # e.g. dict/list passed directly
            self.body = json.dumps(body, ensure_ascii=False).encode(charset)
            mimetype = "application/json"
        self.status_code = status_code
        self.mimetype = mimetype
        self.headers = headers or {}


# ── FunctionApp (wraps FastAPI, registers routes via .route()) ────────────────

class FunctionApp:
    """Drop-in replacement for azure.functions.FunctionApp."""

    def __init__(self, http_auth_level: str = AuthLevel.ANONYMOUS):
        self._auth_level = http_auth_level

    def route(
        self,
        route: str,
        methods: Optional[List[str]] = None,
        auth_level: Optional[str] = None,
    ) -> Callable:
        """Decorator that registers a function handler as a FastAPI route.

        Azure route syntax uses ``{param}`` which is identical to FastAPI's.
        The Azure Functions base path ``/api/`` is prepended automatically.
        """
        http_methods = [m.upper() for m in (methods or ["GET"])]
        fastapi_path = f"/api/{route}"  # preserve Azure Functions convention

        def decorator(handler: Callable) -> Callable:
            # Build a generic async FastAPI endpoint that delegates to handler.
            # We use request.path_params so we don't need explicit path-param
            # declarations in the signature (Starlette populates them anyway).
            async def _endpoint(request: Request) -> Response:
                content_type = request.headers.get("content-type", "")
                files: Dict[str, "_UploadFile"] = {}
                form_fields: Dict[str, str] = {}
                body = b""

                if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
                    form_data = await request.form()
                    for key, value in form_data.multi_items():
                        if isinstance(value, StarletteUploadFile):
                            content = await value.read()
                            files[key] = _UploadFile(
                                filename=value.filename or key,
                                content=content,
                                content_type=value.content_type or "",
                            )
                        else:
                            form_fields[key] = value
                else:
                    body = await request.body()

                req = HttpRequest(
                    method=request.method,
                    url=str(request.url),
                    headers=dict(request.headers),
                    params=dict(request.query_params),
                    body=body,
                    route_params=dict(request.path_params),
                    files=files,
                    form=form_fields,
                )
                try:
                    if asyncio.iscoroutinefunction(handler):
                        resp = await handler(req)
                    else:
                        loop = asyncio.get_event_loop()
                        resp = await loop.run_in_executor(None, handler, req)
                except Exception as exc:
                    logger.exception("Unhandled error in %s: %s", handler.__name__, exc)
                    return Response(
                        content=json.dumps({"error": str(exc)}).encode(),
                        status_code=500,
                        media_type="application/json",
                    )

                return Response(
                    content=resp.body,
                    status_code=resp.status_code,
                    media_type=resp.mimetype,
                    headers=resp.headers,
                )

            _endpoint.__name__ = handler.__name__
            _endpoint.__qualname__ = handler.__qualname__

            _fastapi_app.add_api_route(
                fastapi_path,
                _endpoint,
                methods=http_methods,
                name=handler.__name__,
            )
            logger.debug("Registered route: %s %s → %s", http_methods, fastapi_path, handler.__name__)
            return handler

        return decorator
