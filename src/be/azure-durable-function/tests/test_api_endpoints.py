"""Test API endpoints (job flow)."""
import base64
import json
import os
import time

import pytest

os.environ.setdefault("CONNECTION_STRING", "x")
os.environ.setdefault("IPVMAI", "127.0.0.1")
os.environ.setdefault("IPVMNER", "127.0.0.1")

pytest.importorskip("azure.functions")


class FakeHttpRequest:
    """Minimal mock per func.HttpRequest."""

    def __init__(self, method="GET", url="/", body=None, params=None, route_params=None, files=None, form=None):
        self.method = method
        self.url = url or "/"
        self._body = body or b""
        self.params = params or {}
        self.route_params = route_params or {}
        self.files = files or {}
        self.form = form or {}

    def get_body(self):
        return self._body

    def get_json(self):
        return json.loads(self._body.decode()) if self._body else None


def test_job_polling_endpoint():
    """Test GET /api/job/{job_id} ritorna 404 per job inesistente."""
    from function_app import app, get_job_status

    req = FakeHttpRequest(method="GET", route_params={"job_id": "nonexistent-uuid"})
    resp = get_job_status(req)
    assert resp.status_code == 404
    body = json.loads(resp.get_body().decode())
    assert "error" in body
    assert "Job not found" in body["error"]


def test_upload_returns_job_id(mock_blob):
    """Test POST upload ritorna job_id e statusQueryGetUri."""
    from function_app import upload_client

    class FakeFile:
        filename = "test.pdf"

        class Stream:
            def read(self):
                return b"%PDF-1.4 minimal"

        stream = Stream()

    req = FakeHttpRequest(method="POST", url="/api/upload")
    req.files = {"file": FakeFile()}
    req.form = {"external": "1"}

    resp = upload_client(req)
    assert resp.status_code == 202
    body = json.loads(resp.get_body().decode())
    assert "id" in body
    assert "statusQueryGetUri" in body
    assert "/api/job/" in body["statusQueryGetUri"]
