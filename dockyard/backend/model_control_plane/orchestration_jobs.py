from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .run_state import utc_now


ROOT = Path(__file__).resolve().parents[2]
JOBS_PATH = ROOT / "state" / "agent_jobs.json"
AGENT_JOBS_SCHEMA_VERSION = "model-plane-agent-jobs-v1"
OPEN_JOB_STATUSES = {"open"}
TERMINAL_JOB_STATUSES = {"completed", "failed", "skipped"}


def empty_store() -> dict[str, Any]:
    return {"schema_version": AGENT_JOBS_SCHEMA_VERSION, "jobs": []}


def read_store(path: Path | None = None) -> dict[str, Any]:
    selected = path or JOBS_PATH
    if not selected.exists():
        return empty_store()
    data = json.loads(selected.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return empty_store()
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        jobs = []
    return {
        "schema_version": str(data.get("schema_version") or AGENT_JOBS_SCHEMA_VERSION),
        "jobs": [job for job in jobs if isinstance(job, dict)],
    }


def write_store(store: dict[str, Any], path: Path | None = None) -> None:
    selected = path or JOBS_PATH
    selected.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": str(store.get("schema_version") or AGENT_JOBS_SCHEMA_VERSION),
        "jobs": list(store.get("jobs", [])),
    }
    tmp = selected.with_suffix(selected.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(selected)


def list_jobs(path: Path | None = None, status: str | None = None) -> list[dict[str, Any]]:
    jobs = read_store(path)["jobs"]
    if status is not None:
        jobs = [job for job in jobs if job.get("status") == status]
    return sorted(jobs, key=lambda job: str(job.get("created_at", "")), reverse=True)


def get_job(job_id: str, path: Path | None = None) -> dict[str, Any] | None:
    for job in read_store(path)["jobs"]:
        if job.get("job_id") == job_id:
            return job
    return None


def save_job(job: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    store = read_store(path)
    jobs = store["jobs"]
    job["updated_at"] = utc_now()
    for index, existing in enumerate(jobs):
        if existing.get("job_id") == job.get("job_id"):
            jobs[index] = job
            break
    else:
        jobs.append(job)
    write_store(store, path)
    return job


def new_job_id(job_type: str) -> str:
    return f"job-{job_type}-{uuid.uuid4().hex[:12]}"


def append_history(job: dict[str, Any], event: str, metadata: dict[str, Any] | None = None) -> None:
    history = job.get("history")
    if not isinstance(history, list):
        history = []
    entry: dict[str, Any] = {"event": event, "at": utc_now()}
    if metadata:
        entry["metadata"] = metadata
    history.append(entry)
    job["history"] = history


def create_job(
    *,
    job_type: str,
    source: str,
    allowed_actions: list[str],
    forbidden_actions: list[str],
    payload: dict[str, Any],
    profile_id: str | None = None,
    run_id: str | None = None,
    model_id: str | None = None,
    backend_family: str | None = None,
    dedupe_key: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    created_at = utc_now()
    job: dict[str, Any] = {
        "job_id": new_job_id(job_type),
        "job_type": job_type,
        "status": "open",
        "created_at": created_at,
        "updated_at": created_at,
        "source": source,
        "profile_id": profile_id,
        "run_id": run_id,
        "model_id": model_id,
        "backend_family": backend_family,
        "allowed_actions": allowed_actions,
        "forbidden_actions": forbidden_actions,
        "payload": payload,
        "result": None,
        "history": [],
    }
    if dedupe_key:
        job["dedupe_key"] = dedupe_key
    append_history(job, "created", {"source": source})
    return save_job(job, path)


def find_open_job_by_key(dedupe_key: str, path: Path | None = None) -> dict[str, Any] | None:
    for job in read_store(path)["jobs"]:
        if job.get("dedupe_key") == dedupe_key and job.get("status") in OPEN_JOB_STATUSES:
            return job
    return None


def create_or_reuse_open_job(
    *,
    job_type: str,
    source: str,
    allowed_actions: list[str],
    forbidden_actions: list[str],
    payload: dict[str, Any],
    profile_id: str | None = None,
    run_id: str | None = None,
    model_id: str | None = None,
    backend_family: str | None = None,
    dedupe_key: str,
    path: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    existing = find_open_job_by_key(dedupe_key, path)
    if existing is not None:
        return existing, True
    return (
        create_job(
            job_type=job_type,
            source=source,
            allowed_actions=allowed_actions,
            forbidden_actions=forbidden_actions,
            payload=payload,
            profile_id=profile_id,
            run_id=run_id,
            model_id=model_id,
            backend_family=backend_family,
            dedupe_key=dedupe_key,
            path=path,
        ),
        False,
    )


def record_job_outcome(
    job_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    path: Path | None = None,
) -> dict[str, Any] | None:
    if status not in TERMINAL_JOB_STATUSES:
        raise ValueError(f"Unsupported terminal job status: {status}")
    job = get_job(job_id, path)
    if job is None:
        return None
    recorded_result = dict(result or {})
    recorded_result["recorded_at"] = utc_now()
    job["status"] = status
    job["result"] = recorded_result
    append_history(job, status, recorded_result)
    return save_job(job, path)


def complete_job(job_id: str, result: dict[str, Any] | None = None, path: Path | None = None) -> dict[str, Any] | None:
    return record_job_outcome(job_id, "completed", result, path)


def fail_job(job_id: str, result: dict[str, Any] | None = None, path: Path | None = None) -> dict[str, Any] | None:
    return record_job_outcome(job_id, "failed", result, path)


def skip_job(job_id: str, result: dict[str, Any] | None = None, path: Path | None = None) -> dict[str, Any] | None:
    return record_job_outcome(job_id, "skipped", result, path)
