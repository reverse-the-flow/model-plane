from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import secrets
import shutil
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


TRAY_SCHEMA_VERSION = "model-plane-capsule-handoff-tray-v1"
HANDOFF_SOURCE = "capsule_handoff_tray"
DEFAULT_PORT = 19112
DEFAULT_MAX_HANDOFF_BYTES = 32 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_state_dir() -> Path:
    configured = os.environ.get("CAPSULE_HANDOFF_TRAY_STATE_DIR")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "ModelPlane" / "CapsuleHandoffTray"
    return Path.home() / ".local" / "share" / "model-plane" / "capsule-handoff-tray"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_filename(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "-" for ch in value.strip())
    return cleaned.strip(".-") or secrets.token_hex(8)


def canonical_json(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def signature_payload(manifest: dict[str, Any]) -> bytes:
    selected = copy.deepcopy(manifest)
    selected.pop("signature", None)
    return canonical_json(selected)


def verify_manifest_signature(manifest: dict[str, Any], trusted_keys: dict[str, str]) -> dict[str, Any]:
    signature = manifest.get("signature")
    if not isinstance(signature, dict):
        return {"signed": False, "ok": False, "reason": "missing_signature"}
    algorithm = str(signature.get("algorithm") or "")
    key_id = str(signature.get("key_id") or "")
    value = str(signature.get("value") or "")
    if algorithm != "hmac-sha256":
        return {"signed": True, "ok": False, "reason": "unsupported_signature_algorithm", "algorithm": algorithm}
    if not key_id or key_id not in trusted_keys:
        return {"signed": True, "ok": False, "reason": "unknown_signature_key", "key_id": key_id}
    expected = hmac.new(trusted_keys[key_id].encode("utf-8"), signature_payload(manifest), hashlib.sha256).hexdigest()
    return {
        "signed": True,
        "ok": hmac.compare_digest(expected, value),
        "algorithm": algorithm,
        "key_id": key_id,
        "reason": "signature_valid" if hmac.compare_digest(expected, value) else "signature_mismatch",
    }


def load_trusted_keys() -> dict[str, str]:
    raw = os.environ.get("CAPSULE_HANDOFF_TRAY_TRUSTED_KEYS_JSON")
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return {str(key): str(value) for key, value in parsed.items()} if isinstance(parsed, dict) else {}
    path = os.environ.get("CAPSULE_HANDOFF_TRAY_TRUSTED_KEYS")
    if not path:
        return {}
    try:
        parsed = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(key): str(value) for key, value in parsed.items()} if isinstance(parsed, dict) else {}


def read_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": TRAY_SCHEMA_VERSION, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": TRAY_SCHEMA_VERSION, "items": []}
    if not isinstance(data, dict):
        return {"schema_version": TRAY_SCHEMA_VERSION, "items": []}
    data.setdefault("schema_version", TRAY_SCHEMA_VERSION)
    data.setdefault("items", [])
    return data


def write_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def ensure_zip_member_safe(name: str) -> None:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or normalized.startswith("../") or "/../" in normalized or normalized == "..":
        raise ValueError(f"Unsafe capsule bundle member path: {name}")


def extract_zip_safely(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            ensure_zip_member_safe(member.filename)
        archive.extractall(destination)


def find_manifest(extracted_dir: Path) -> dict[str, Any]:
    for candidate in ("capsule.json", "manifest.json"):
        path = extracted_dir / candidate
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    raise ValueError("Capsule bundle must contain capsule.json or manifest.json.")


def verify_checksums(extracted_dir: Path) -> dict[str, Any]:
    checksums_path = extracted_dir / "checksums.json"
    if not checksums_path.exists():
        return {"present": False, "ok": False, "reason": "missing_checksums"}
    checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
    files = checksums.get("files") if isinstance(checksums, dict) else None
    if not isinstance(files, dict):
        return {"present": True, "ok": False, "reason": "invalid_checksums"}
    mismatches = []
    checked = []
    for relative, expected in files.items():
        ensure_zip_member_safe(str(relative))
        path = extracted_dir / str(relative)
        if not path.exists() or not path.is_file():
            mismatches.append({"path": str(relative), "reason": "missing"})
            continue
        actual = sha256_bytes(path.read_bytes())
        checked.append(str(relative))
        if actual != str(expected):
            mismatches.append({"path": str(relative), "reason": "sha256_mismatch", "actual": actual})
    return {
        "present": True,
        "ok": not mismatches,
        "checked": checked,
        "mismatches": mismatches,
        "reason": "checksums_valid" if not mismatches else "checksum_mismatch",
    }


def validate_capsule_manifest(manifest: dict[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if not manifest.get("capsule_id"):
        messages.append({"level": "error", "code": "capsule_id", "message": "Capsule manifest must include capsule_id."})
    if not manifest.get("capsule_kind"):
        messages.append({"level": "warning", "code": "capsule_kind", "message": "Capsule manifest should include capsule_kind."})
    if not manifest.get("restore_requirements"):
        messages.append({
            "level": "warning",
            "code": "restore_requirements",
            "message": "Capsule manifest should include restore_requirements for launch-card matching.",
        })
    fallback = manifest.get("fallback")
    if not isinstance(fallback, dict) or fallback.get("transcript_replay") is not True:
        messages.append({
            "level": "warning",
            "code": "transcript_replay",
            "message": "Capsule manifest should preserve transcript replay fallback.",
        })
    return messages


def compatibility_summary(manifest: dict[str, Any], target: dict[str, Any] | None) -> dict[str, Any]:
    target = target or {}
    requirements = manifest.get("restore_requirements") if isinstance(manifest.get("restore_requirements"), dict) else {}
    hard = requirements.get("hard_snapshot") if isinstance(requirements.get("hard_snapshot"), dict) else {}
    soft = requirements.get("soft_replay") if isinstance(requirements.get("soft_replay"), dict) else {}
    target_digest = target.get("launch_card_digest") or target.get("profile_digest")
    required_digest = hard.get("requires_launch_card_digest")
    exact_launch_match = bool(required_digest and target_digest and str(required_digest) == str(target_digest))
    context_ok = True
    min_context = soft.get("min_context_tokens")
    target_context = target.get("context_size") or target.get("max_context_tokens")
    if min_context is not None and target_context is not None:
        try:
            context_ok = int(target_context) >= int(min_context)
        except (TypeError, ValueError):
            context_ok = False
    transcript_replay = manifest.get("fallback", {}).get("transcript_replay") is True and context_ok
    return {
        "target_known": bool(target),
        "exact_launch_match": exact_launch_match,
        "hard_restore_allowed": exact_launch_match,
        "transcript_replay_allowed": transcript_replay,
        "decision": "hard_restore"
        if exact_launch_match
        else "transcript_replay" if transcript_replay else "hold_for_review",
        "notes": [
            "Hard restore requires an exact launch-card/runtime match.",
            "Cross-model compatibility scoring is out of scope; non-exact targets use transcript replay only.",
        ],
    }


class TrayStore:
    def __init__(
        self,
        state_dir: Path | None = None,
        trusted_keys: dict[str, str] | None = None,
        max_handoff_bytes: int = DEFAULT_MAX_HANDOFF_BYTES,
    ) -> None:
        self.state_dir = state_dir or default_state_dir()
        self.store_path = self.state_dir / "tray.json"
        self.bundles_dir = self.state_dir / "bundles"
        self.trusted_keys = trusted_keys if trusted_keys is not None else load_trusted_keys()
        self.max_handoff_bytes = max_handoff_bytes

    def list_items(self, state: str | None = None) -> list[dict[str, Any]]:
        items = read_store(self.store_path).get("items", [])
        selected = [item for item in items if isinstance(item, dict)]
        if state:
            selected = [item for item in selected if item.get("state") == state]
        return selected

    def get_item(self, bundle_id: str) -> dict[str, Any] | None:
        for item in self.list_items():
            if item.get("bundle_id") == bundle_id:
                return item
        return None

    def upsert_item(self, item: dict[str, Any]) -> dict[str, Any]:
        data = read_store(self.store_path)
        items = [existing for existing in data.get("items", []) if existing.get("bundle_id") != item.get("bundle_id")]
        items.append(item)
        data["items"] = items
        write_store(self.store_path, data)
        return item

    def update_item(self, bundle_id: str, **updates: Any) -> dict[str, Any]:
        item = self.get_item(bundle_id)
        if item is None:
            raise KeyError(bundle_id)
        item.update(updates)
        item["updated_at"] = utc_now()
        events = list(item.get("events") or [])
        if "state" in updates:
            events.append({"state": updates["state"], "at": item["updated_at"]})
        item["events"] = events
        return self.upsert_item(item)

    def import_inline_bundle(
        self,
        manifest: dict[str, Any],
        allow_unsigned: bool = False,
        source_label: str | None = None,
        target_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        bundle_id = safe_filename(str(manifest.get("capsule_id") or secrets.token_hex(8)))
        bundle_dir = self.bundles_dir / bundle_id
        if bundle_dir.exists():
            shutil.rmtree(bundle_dir)
        bundle_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = bundle_dir / "capsule.json"
        artifact_bytes = canonical_json(manifest)
        artifact_path.write_bytes(artifact_bytes)
        return self._record_import(
            bundle_id,
            manifest,
            artifact_path,
            sha256_bytes(artifact_bytes),
            {"present": False, "ok": False, "reason": "inline_manifest_only"},
            allow_unsigned,
            source_label,
            target_hint,
        )

    def import_zip_bytes(
        self,
        data: bytes,
        allow_unsigned: bool = False,
        expected_sha256: str | None = None,
        source_label: str | None = None,
        target_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact_sha256 = sha256_bytes(data)
        if expected_sha256 and artifact_sha256 != expected_sha256:
            raise ValueError("Capsule bundle sha256 did not match expected_sha256.")
        temp_id = secrets.token_hex(8)
        temp_dir = self.bundles_dir / f".import-{temp_id}"
        archive_path = temp_dir / "capsule.bundle.zip"
        temp_dir.mkdir(parents=True, exist_ok=True)
        archive_path.write_bytes(data)
        extracted_dir = temp_dir / "expanded"
        extract_zip_safely(archive_path, extracted_dir)
        manifest = find_manifest(extracted_dir)
        bundle_id = safe_filename(str(manifest.get("capsule_id") or temp_id))
        bundle_dir = self.bundles_dir / bundle_id
        if bundle_dir.exists():
            shutil.rmtree(bundle_dir)
        bundle_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir.rename(bundle_dir)
        checksum_result = verify_checksums(bundle_dir / "expanded")
        return self._record_import(
            bundle_id,
            manifest,
            bundle_dir / "capsule.bundle.zip",
            artifact_sha256,
            checksum_result,
            allow_unsigned,
            source_label,
            target_hint,
        )

    def import_from_url(
        self,
        source_url: str,
        allow_unsigned: bool = False,
        expected_sha256: str | None = None,
        target_hint: dict[str, Any] | None = None,
        urlopen: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        if not source_url.startswith(("http://", "https://")):
            raise ValueError("Capsule imports only accept http:// or https:// source URLs.")
        selected_urlopen = urlopen or urllib.request.urlopen
        with selected_urlopen(source_url, timeout=30) as response:
            data = response.read()
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self.import_zip_bytes(data, allow_unsigned, expected_sha256, source_url, target_hint)
        if not isinstance(parsed, dict):
            raise ValueError("Inline capsule JSON import must be a JSON object.")
        return self.import_inline_bundle(parsed, allow_unsigned, source_url, target_hint)

    def _record_import(
        self,
        bundle_id: str,
        manifest: dict[str, Any],
        artifact_path: Path,
        artifact_sha256: str,
        checksum_result: dict[str, Any],
        allow_unsigned: bool,
        source_label: str | None,
        target_hint: dict[str, Any] | None,
    ) -> dict[str, Any]:
        validation = validate_capsule_manifest(manifest)
        signature = verify_manifest_signature(manifest, self.trusted_keys)
        errors = [message for message in validation if message["level"] == "error"]
        if errors:
            raise ValueError("; ".join(message["message"] for message in errors))
        if signature["signed"] and not signature["ok"]:
            raise ValueError(f"Capsule signature rejected: {signature.get('reason')}")
        if not signature["signed"] and not allow_unsigned:
            raise ValueError("Unsigned capsule imports require allow_unsigned=true.")
        now = utc_now()
        item = {
            "schema_version": TRAY_SCHEMA_VERSION,
            "bundle_id": bundle_id,
            "capsule_id": manifest.get("capsule_id"),
            "thread_id": manifest.get("thread_id"),
            "capsule_kind": manifest.get("capsule_kind"),
            "state": "pending",
            "received_at": now,
            "updated_at": now,
            "source": source_label or "inline",
            "artifact_path": str(artifact_path),
            "artifact_sha256": artifact_sha256,
            "manifest": manifest,
            "verification": {
                "signature": signature,
                "checksums": checksum_result,
                "validation_messages": validation,
                "unsigned_allowed": allow_unsigned,
            },
            "compatibility": compatibility_summary(manifest, target_hint),
            "events": [{"state": "pending", "at": now}],
        }
        return self.upsert_item(item)

    def attach(
        self,
        bundle_id: str,
        gateway_url: str,
        thread_id: str | None = None,
        target_launch: dict[str, Any] | None = None,
        urlopen: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        item = self.get_item(bundle_id)
        if item is None:
            raise KeyError(bundle_id)
        if item.get("state") not in {"pending", "verified"}:
            raise ValueError(f"Capsule bundle is not attachable from state {item.get('state')}.")
        artifact_path = Path(str(item["artifact_path"]))
        data = artifact_path.read_bytes()
        if len(data) > self.max_handoff_bytes:
            raise ValueError("Capsule bundle is too large for inline handoff.")
        selected_thread_id = thread_id or item.get("thread_id")
        if not selected_thread_id:
            raise ValueError("Attach requires thread_id from the request or capsule manifest.")
        prepare_payload = {
            "operation": "upload",
            "mode": "import",
            "source": HANDOFF_SOURCE,
            "bundle_id": item["bundle_id"],
            "capsule_id": item.get("capsule_id"),
            "thread_id": selected_thread_id,
            "size_bytes": len(data),
            "sha256": item.get("artifact_sha256"),
            "manifest": item.get("manifest"),
            "target_launch": target_launch or {},
            "compatibility": compatibility_summary(item.get("manifest") or {}, target_launch),
        }
        url = gateway_url.rstrip("/") + "/api/capsules/handoff"
        selected_urlopen = urlopen or urllib.request.urlopen

        def post_handoff(payload: dict[str, Any]) -> tuple[int | None, str, dict[str, Any]]:
            request = urllib.request.Request(
                url,
                data=canonical_json(payload),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with selected_urlopen(request, timeout=30) as response:
                response_body = response.read().decode("utf-8", errors="replace")
                status = getattr(response, "status", None)
            try:
                parsed = json.loads(response_body) if response_body else {}
            except json.JSONDecodeError:
                parsed = {"raw": response_body}
            return status, response_body, parsed if isinstance(parsed, dict) else {"raw": parsed}

        prepare_status, prepare_body, prepare = post_handoff(prepare_payload)
        if prepare.get("accepted") is not True:
            self.update_item(
                bundle_id,
                last_handoff_status=prepare_status,
                last_handoff_response=prepare_body,
                last_handoff_error="gateway_rejected_prepare",
            )
            raise ValueError(f"Gateway rejected capsule handoff: {prepare.get('reasons') or prepare}")
        handoff_id = str(prepare["handoff_id"])
        commit_payload = {
            "operation": "upload",
            "phase": "commit",
            "handoff_id": handoff_id,
            "artifact_b64": base64.b64encode(data).decode("ascii"),
        }
        commit_status, commit_body, commit = post_handoff(commit_payload)
        if commit.get("accepted") is False:
            self.update_item(
                bundle_id,
                last_handoff_status=commit_status,
                last_handoff_response=commit_body,
                last_handoff_error="gateway_rejected_commit",
            )
            raise ValueError(f"Gateway rejected capsule handoff commit: {commit}")
        updated = self.update_item(
            bundle_id,
            state="attached",
            attached_at=utc_now(),
            gateway_url=gateway_url,
            attached_thread_id=selected_thread_id,
            last_handoff_id=handoff_id,
            last_handoff_status=commit_status,
            last_handoff_prepare=prepare,
            last_handoff_response=commit_body,
        )
        ok = bool(commit_status is None or 200 <= int(commit_status) < 300)
        return {"ok": ok, "status": commit_status, "handoff_id": handoff_id, "prepare": prepare, "commit": commit, "item": updated}

    def reject(self, bundle_id: str, reason: str | None = None) -> dict[str, Any]:
        return self.update_item(bundle_id, state="rejected", rejected_at=utc_now(), reject_reason=reason)

    def expire(self, bundle_id: str, reason: str | None = None) -> dict[str, Any]:
        return self.update_item(bundle_id, state="expired", expired_at=utc_now(), expire_reason=reason)
