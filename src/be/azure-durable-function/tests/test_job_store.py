"""Test job_store module."""
import pytest

# Import before function_app (no azure deps)
from job_store import create_job, get_job, set_completed, set_failed, set_running, update_job


@pytest.fixture(autouse=True)
def job_store_memory_only(monkeypatch):
    """Forza uso memoria in test (nessun Blob) così i test non dipendono da storage."""
    monkeypatch.setattr("job_store._bsc", lambda: None)


def test_create_job():
    job_id = create_job("upload", "Pending")
    assert job_id
    assert len(job_id) == 36  # uuid4
    job = get_job(job_id)
    assert job["status"] == "Pending"
    assert job["runtimeStatus"] == "Pending"
    assert job["job_type"] == "upload"


def test_set_running():
    job_id = create_job("test")
    set_running(job_id, {"progress": 50})
    job = get_job(job_id)
    assert job["runtimeStatus"] == "Running"
    assert job["custom_status"] == {"progress": 50}


def test_set_completed():
    job_id = create_job("test")
    set_completed(job_id, {"result": "ok"})
    job = get_job(job_id)
    assert job["runtimeStatus"] == "Completed"
    assert job["result"] == {"result": "ok"}


def test_set_failed():
    job_id = create_job("test")
    set_failed(job_id, "Something went wrong")
    job = get_job(job_id)
    assert job["runtimeStatus"] == "Failed"
    assert job["error"] == "Something went wrong"


def test_get_job_not_found():
    assert get_job("nonexistent-id") is None
