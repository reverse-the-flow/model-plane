from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .tray import DEFAULT_PORT, TRAY_SCHEMA_VERSION, TrayStore, default_state_dir


router = APIRouter()


class ImportRequest(BaseModel):
    source_url: str | None = None
    bundle: dict[str, Any] | None = None
    allow_unsigned: bool = False
    expected_sha256: str | None = None
    target_hint: dict[str, Any] | None = None


class AttachRequest(BaseModel):
    bundle_id: str
    gateway_url: str
    thread_id: str | None = None
    target_launch: dict[str, Any] | None = None


class StateRequest(BaseModel):
    reason: str | None = None


def store() -> TrayStore:
    return TrayStore()


@router.get("/tray/status")
def tray_status() -> dict[str, Any]:
    selected = store()
    items = selected.list_items()
    return {
        "schema_version": TRAY_SCHEMA_VERSION,
        "state_dir": str(default_state_dir()),
        "bind_host": "127.0.0.1",
        "default_port": DEFAULT_PORT,
        "item_count": len(items),
        "pending_count": sum(1 for item in items if item.get("state") == "pending"),
        "job": "receive, verify, stage, and hand off capsule bundles only",
        "boundaries": [
            "does not transfer model weights",
            "does not transfer live KV tensors",
            "does not browse remote capsule stores",
            "does not write outside the tray state directory",
        ],
    }


@router.post("/tray/import")
def import_capsule(request: ImportRequest) -> dict[str, Any]:
    selected = store()
    try:
        if request.bundle is not None:
            item = selected.import_inline_bundle(
                request.bundle,
                allow_unsigned=request.allow_unsigned,
                source_label="inline_api",
                target_hint=request.target_hint,
            )
        elif request.source_url:
            item = selected.import_from_url(
                request.source_url,
                allow_unsigned=request.allow_unsigned,
                expected_sha256=request.expected_sha256,
                target_hint=request.target_hint,
            )
        else:
            raise ValueError("Import requires bundle or source_url.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": item}


@router.get("/tray/pending")
def pending_capsules() -> dict[str, Any]:
    return {"schema_version": TRAY_SCHEMA_VERSION, "items": store().list_items("pending")}


@router.get("/tray/items/{bundle_id}")
def tray_item(bundle_id: str) -> dict[str, Any]:
    item = store().get_item(bundle_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Capsule bundle not found.")
    return item


@router.post("/tray/attach")
def attach_capsule(request: AttachRequest) -> dict[str, Any]:
    try:
        return store().attach(
            request.bundle_id,
            request.gateway_url,
            thread_id=request.thread_id,
            target_launch=request.target_launch,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Capsule bundle not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tray/items/{bundle_id}/reject")
def reject_capsule(bundle_id: str, request: StateRequest | None = None) -> dict[str, Any]:
    try:
        item = store().reject(bundle_id, (request or StateRequest()).reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Capsule bundle not found.") from exc
    return {"ok": True, "item": item}


@router.post("/tray/items/{bundle_id}/expire")
def expire_capsule(bundle_id: str, request: StateRequest | None = None) -> dict[str, Any]:
    try:
        item = store().expire(bundle_id, (request or StateRequest()).reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Capsule bundle not found.") from exc
    return {"ok": True, "item": item}


app = FastAPI(title="Capsule Handoff Tray", version="0.1.0")
app.include_router(router)
