"""Fixtures per test azure-durable-function."""
import os
import pytest

# Set env before any imports that read it
os.environ.setdefault("CONNECTION_STRING", "DefaultEndpointsProtocol=https;AccountName=devstoreaccount1;AccountKey=testkey;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1")
os.environ.setdefault("IPVMAI", "127.0.0.1")
os.environ.setdefault("IPVMNER", "127.0.0.1")


@pytest.fixture
def mock_requests(monkeypatch):
    """Mock requests.post per chiamate VMAI/VMNER."""
    calls = []

    def _mock_post(url, *args, **kwargs):
        calls.append({"url": url, "args": args, "kwargs": kwargs})
        class FakeResponse:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {"status": "ok", "result": []}
        return FakeResponse()

    monkeypatch.setattr("requests.post", _mock_post)
    return calls


@pytest.fixture
def mock_blob(monkeypatch):
    """Mock blob_storage_client per evitare connessione reale."""
    uploaded = []

    class FakeBlobClient:
        def upload_blob(self, data, overwrite=False):
            uploaded.append({"overwrite": overwrite})

    def _fake_get_blob_client(blob_path):
        return FakeBlobClient()

    def _fake_is_available():
        return True

    monkeypatch.setattr("utils.blob_storage_client.get_blob_client", _fake_get_blob_client)
    monkeypatch.setattr("utils.blob_storage_client.is_available", _fake_is_available)
    return uploaded
