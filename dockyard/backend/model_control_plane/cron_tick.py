from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import orchestration_jobs, run_state


CRON_TICK_SCHEMA_VERSION = "model-plane-cron-tick-v1"
DEFAULT_HEALTH_STALE_SECONDS = 15 * 60
DEFAULT_STALE_LAUNCHING_SECONDS = 30 * 60

COMMON_FORBIDDEN_ACTIONS = [
    "download_models",
    "use_tokens",
    "docker_prune",
    "broad_deletion",
    "unapproved_prompt_traffic",
    "unapproved_model_launch",
    "start_model_server",
]


def parse_utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_seconds(timestamp: Any) -> float | None:
    parsed = parse_utc_timestamp(timestamp)
    if parsed is None:
        return None
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def public_job_view(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job.get("job_id"),
        "job_type": job.get("job_type"),
        "status": job.get("status"),
        "profile_id": job.get("profile_id"),
        "run_id": job.get("run_id"),
        "model_id": job.get("model_id"),
        "backend_family": job.get("backend_family"),
        "dedupe_key": job.get("dedupe_key"),
        "payload": job.get("payload"),
    }


def should_health_check(run: dict[str, Any], health_stale_seconds: int) -> tuple[bool, str]:
    status = str(run.get("status") or "")
    if status == "launched":
        return True, "run_launched_without_current_health_check"
    if status == "healthy":
        checked_at = (run.get("last_health_result") or {}).get("checked_at")
        checked_age = age_seconds(checked_at)
        if checked_age is None:
            return True, "healthy_run_without_health_timestamp"
        if checked_age >= health_stale_seconds:
            return True, f"healthy_run_stale_health:{int(checked_age)}s"
    return False, f"status_not_health_check_candidate:{status or 'unknown'}"


def should_moe_probe_plan(run: dict[str, Any]) -> tuple[bool, str]:
    status = str(run.get("status") or "")
    if status in {"launched", "healthy"}:
        return True, f"run_status_{status}"
    return False, f"status_not_probe_candidate:{status or 'unknown'}"


def profile_job_payload(profile: dict[str, Any], messages: list[dict[str, str]]) -> dict[str, Any]:
    profile_id = str(profile.get("id") or "")
    return {
        "api_path": f"/profiles/{profile_id}/validate",
        "command_class": "profile_validation_review",
        "validation_summary": {
            "warnings": sum(1 for message in messages if message.get("level") == "warning"),
            "errors": sum(1 for message in messages if message.get("level") == "error"),
        },
        "messages": messages,
        "next_step": "Review validation messages and record completion metadata.",
    }


def run_health_job_payload(run: dict[str, Any], reason: str) -> dict[str, Any]:
    run_id = str(run.get("run_id") or "")
    return {
        "api_path": f"/runs/{run_id}/health",
        "command_class": "run_health_check",
        "reason": reason,
        "health_url": run.get("health_url"),
        "next_step": "Approved caller may check only this run health URL and then complete the job.",
    }


def moe_probe_job_payload(run: dict[str, Any], reason: str) -> dict[str, Any]:
    run_id = str(run.get("run_id") or "")
    return {
        "api_path": f"/runs/{run_id}/moe-probe-manifest",
        "command_class": "moe_run_anyway_probe_plan",
        "planner_mode": "plan_or_review_only",
        "reason": reason,
        "next_step": "Export this run-scoped manifest and feed MoE Run Anyway planner in dry-run planning mode.",
    }


def cleanup_job_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    run_id = str(candidate.get("run_id") or "")
    return {
        "api_path": f"/runs/{run_id}/cleanup",
        "command_class": "cleanup_review_only",
        "request_body": {"remove_container": False, "notes": "cron review packet"},
        "cleanup_plan_candidate": candidate,
        "next_step": "Record review metadata only unless a human separately authorizes container removal.",
    }


def create_packet(
    *,
    job_type: str,
    dedupe_key: str,
    payload: dict[str, Any],
    allowed_actions: list[str],
    forbidden_actions: list[str],
    profile_id: str | None = None,
    run_id: str | None = None,
    model_id: str | None = None,
    backend_family: str | None = None,
    jobs_path: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    return orchestration_jobs.create_or_reuse_open_job(
        job_type=job_type,
        source="cron_tick",
        allowed_actions=allowed_actions,
        forbidden_actions=forbidden_actions,
        payload=payload,
        profile_id=profile_id,
        run_id=run_id,
        model_id=model_id,
        backend_family=backend_family,
        dedupe_key=dedupe_key,
        path=jobs_path,
    )


def cron_tick(
    *,
    profiles: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    cleanup_plan: dict[str, Any],
    validate_profile: Callable[[dict[str, Any]], list[dict[str, str]]],
    jobs_path: Path | None = None,
    health_stale_seconds: int = DEFAULT_HEALTH_STALE_SECONDS,
) -> dict[str, Any]:
    created_jobs: list[dict[str, Any]] = []
    reused_open_jobs: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    def record(job: dict[str, Any], reused: bool) -> None:
        if reused:
            reused_open_jobs.append(public_job_view(job))
        else:
            created_jobs.append(public_job_view(job))

    for profile in sorted(profiles, key=lambda item: str(item.get("id") or "")):
        profile_id = str(profile.get("id") or "")
        if not profile_id:
            skipped.append({"kind": "profile", "reason": "missing_profile_id"})
            continue
        messages = validate_profile(profile)
        job, reused = create_packet(
            job_type="profile_validate",
            dedupe_key=f"profile_validate:{profile_id}",
            profile_id=profile_id,
            model_id=(profile.get("model") or {}).get("id"),
            backend_family=str((profile.get("runtime") or {}).get("backend") or ""),
            allowed_actions=[
                "read_profile",
                "call_profile_validate_endpoint",
                "record_completion_metadata",
            ],
            forbidden_actions=COMMON_FORBIDDEN_ACTIONS,
            payload=profile_job_payload(profile, messages),
            jobs_path=jobs_path,
        )
        record(job, reused)

    cleanup_candidates_by_run_id = {
        str(candidate.get("run_id") or ""): candidate
        for candidate in cleanup_plan.get("candidates", [])
        if isinstance(candidate, dict) and candidate.get("run_id")
    }
    for run_id, candidate in sorted(cleanup_candidates_by_run_id.items()):
        job, reused = create_packet(
            job_type="cleanup_review",
            dedupe_key=f"cleanup_review:{run_id}",
            run_id=run_id,
            profile_id=candidate.get("profile_id"),
            model_id=next((run.get("model_id") for run in runs if run.get("run_id") == run_id), None),
            backend_family=next((run.get("backend_family") for run in runs if run.get("run_id") == run_id), None),
            allowed_actions=[
                "read_cleanup_plan",
                "inspect_run_state",
                "record_run_cleanup_review_without_container_removal",
                "record_completion_metadata",
            ],
            forbidden_actions=COMMON_FORBIDDEN_ACTIONS
            + [
                "docker_rm_without_explicit_operator_approval",
                "delete_run_record",
            ],
            payload=cleanup_job_payload(candidate),
            jobs_path=jobs_path,
        )
        record(job, reused)

    for selected in sorted(runs, key=lambda item: str(item.get("run_id") or "")):
        run_id = str(selected.get("run_id") or "")
        if not run_id:
            skipped.append({"kind": "run", "reason": "missing_run_id"})
            continue
        if run_id in cleanup_candidates_by_run_id:
            skipped.append({"kind": "run", "run_id": run_id, "reason": "cleanup_candidate_prioritized"})
            continue

        health_candidate, health_reason = should_health_check(selected, health_stale_seconds)
        if health_candidate:
            job, reused = create_packet(
                job_type="run_health_check",
                dedupe_key=f"run_health_check:{run_id}",
                run_id=run_id,
                profile_id=selected.get("profile_id"),
                model_id=selected.get("model_id"),
                backend_family=selected.get("backend_family"),
                allowed_actions=[
                    "inspect_run_state",
                    "call_single_run_health_endpoint",
                    "record_completion_metadata",
                ],
                forbidden_actions=COMMON_FORBIDDEN_ACTIONS,
                payload=run_health_job_payload(selected, health_reason),
                jobs_path=jobs_path,
            )
            record(job, reused)
        else:
            skipped.append({"kind": "run_health_check", "run_id": run_id, "reason": health_reason})

        probe_candidate, probe_reason = should_moe_probe_plan(selected)
        if probe_candidate:
            job, reused = create_packet(
                job_type="moe_probe_plan",
                dedupe_key=f"moe_probe_plan:{run_id}",
                run_id=run_id,
                profile_id=selected.get("profile_id"),
                model_id=selected.get("model_id"),
                backend_family=selected.get("backend_family"),
                allowed_actions=[
                    "inspect_run_state",
                    "export_run_scoped_moe_probe_manifest",
                    "run_moe_planner_in_plan_only_mode",
                    "record_completion_metadata",
                ],
                forbidden_actions=COMMON_FORBIDDEN_ACTIONS
                + [
                    "send_probe_prompt_without_explicit_operator_approval",
                    "start_probe_sidecar_without_explicit_operator_approval",
                ],
                payload=moe_probe_job_payload(selected, probe_reason),
                jobs_path=jobs_path,
            )
            record(job, reused)
        else:
            skipped.append({"kind": "moe_probe_plan", "run_id": run_id, "reason": probe_reason})

    return {
        "schema_version": CRON_TICK_SCHEMA_VERSION,
        "generated_at": run_state.utc_now(),
        "created_jobs": created_jobs,
        "reused_open_jobs": reused_open_jobs,
        "skipped": skipped,
        "safety_note": (
            "Cron tick only creates or reuses bounded job packets. It does not call Docker, "
            "download models, use tokens, launch model servers, send prompts, or run cleanup."
        ),
    }
