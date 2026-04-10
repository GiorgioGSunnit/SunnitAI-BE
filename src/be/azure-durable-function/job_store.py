"""Job store per operazioni async senza Durable Functions.

In produzione (Blob Storage disponibile) lo stato job è persistito in conf/jobs/{job_id}.json
così che tutte le istanze del servizio condividano lo stesso stato (singolo servizio, mono-utenza).
Se Blob non è configurato (locale/test) si usa solo memoria in-process.
"""
import json
import threading
import uuid
from datetime import datetime, timezone

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _bsc():
    """Lazy import per evitare dipendenze circolari e per ambienti senza utils."""
    try:
        from utils import blob_storage_client as bsc
        return bsc
    except ImportError:
        return None


def _job_payload(
    job_id: str,
    job_type: str,
    status: str = "Pending",
    runtime_status: str = "Pending",
    result=None,
    error=None,
    custom_status=None,
    created_at: str = None,
) -> dict:
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "id": job_id,
        "status": status,
        "runtimeStatus": runtime_status,
        "job_type": job_type,
        "result": result,
        "error": error,
        "custom_status": custom_status,
        "created_at": created_at,
    }


def _read_job_from_blob(job_id: str) -> dict | None:
    bsc = _bsc()
    if not bsc or not bsc.is_available():
        return None
    try:
        blob_client = bsc.get_blob_client(bsc.path_job(job_id))
        data = blob_client.download_blob().readall().decode("utf-8")
        return json.loads(data)
    except Exception:
        return None


def _write_job_to_blob(job: dict) -> bool:
    bsc = _bsc()
    if not bsc or not bsc.is_available():
        return False
    try:
        blob_client = bsc.get_blob_client(bsc.path_job(job["id"]))
        blob_client.upload_blob(
            json.dumps(job, ensure_ascii=False),
            overwrite=True,
        )
        return True
    except Exception:
        return False


def create_job(job_type: str, initial_status: str = "Pending") -> str:
    job_id = str(uuid.uuid4())
    job = _job_payload(job_id, job_type, status=initial_status)
    if _write_job_to_blob(job):
        return job_id
    with _lock:
        _jobs[job_id] = job
    return job_id


def update_job(
    job_id: str,
    status: str = None,
    runtime_status: str = None,
    result=None,
    error=None,
    custom_status=None,
):
    bsc = _bsc()
    if bsc and bsc.is_available():
        job = _read_job_from_blob(job_id)
        if not job:
            return False
        if status is not None:
            job["status"] = status
        if runtime_status is not None:
            job["runtimeStatus"] = runtime_status
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = error
        if custom_status is not None:
            job["custom_status"] = custom_status
        return _write_job_to_blob(job)
    with _lock:
        if job_id not in _jobs:
            return False
        j = _jobs[job_id]
        if status is not None:
            j["status"] = status
        if runtime_status is not None:
            j["runtimeStatus"] = runtime_status
        if result is not None:
            j["result"] = result
        if error is not None:
            j["error"] = error
        if custom_status is not None:
            j["custom_status"] = custom_status
        return True


def get_job(job_id: str) -> dict | None:
    bsc = _bsc()
    if bsc and bsc.is_available():
        return _read_job_from_blob(job_id)
    with _lock:
        return _jobs.get(job_id)


def set_completed(job_id: str, result: dict):
    update_job(job_id, status="Completed", runtime_status="Completed", result=result)


def set_failed(job_id: str, error: str):
    update_job(job_id, status="Failed", runtime_status="Failed", error=error)


def set_running(job_id: str, custom_status: dict = None):
    update_job(
        job_id,
        status="Running",
        runtime_status="Running",
        custom_status=custom_status,
    )
