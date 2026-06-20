from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from capsule_handoff_tray.app import tray_status
from capsule_handoff_tray.tray import TrayStore, canonical_json, signature_payload


def manifest(capsule_id: str = "capsule-1") -> dict:
    return {
        "capsule_id": capsule_id,
        "thread_id": "thread-1",
        "capsule_kind": "soft",
        "restore_requirements": {
            "hard_snapshot": {"requires_launch_card_digest": "launch-abc"},
            "soft_replay": {"source_model_id": "local/model", "min_context_tokens": 2048},
        },
        "fallback": {"transcript_replay": True},
    }


def signed_manifest(secret: str) -> dict:
    selected = manifest("signed-capsule")
    selected["signature"] = {
        "algorithm": "hmac-sha256",
        "key_id": "gx10",
        "value": hmac.new(secret.encode("utf-8"), signature_payload(selected), hashlib.sha256).hexdigest(),
    }
    return selected


def zip_bundle(selected_manifest: dict, include_bad_path: bool = False) -> bytes:
    payload = b'{"hello":"capsule"}\n'
    checksums = {"files": {"capsule.json": hashlib.sha256(canonical_json(selected_manifest)).hexdigest(), "payload.json": hashlib.sha256(payload).hexdigest()}}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("capsule.json", canonical_json(selected_manifest))
        archive.writestr("payload.json", payload)
        archive.writestr("checksums.json", canonical_json(checksums))
        if include_bad_path:
            archive.writestr("../escape.txt", "bad")
    return stream.getvalue()


class FakeAttachResponse:
    def __init__(self, body: bytes = b'{"accepted":true}') -> None:
        self.body = body

    status = 200

    def __enter__(self) -> "FakeAttachResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class CapsuleHandoffTrayTests(unittest.TestCase):
    def test_status_surface_is_local_and_narrow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            previous = os.environ.get("CAPSULE_HANDOFF_TRAY_STATE_DIR")
            os.environ["CAPSULE_HANDOFF_TRAY_STATE_DIR"] = temp_dir
            try:
                status = tray_status()
            finally:
                if previous is None:
                    os.environ.pop("CAPSULE_HANDOFF_TRAY_STATE_DIR", None)
                else:
                    os.environ["CAPSULE_HANDOFF_TRAY_STATE_DIR"] = previous

        self.assertEqual(status["bind_host"], "127.0.0.1")
        self.assertEqual(status["default_port"], 19112)
        self.assertIn("receive, verify, stage", status["job"])
        self.assertIn("does not transfer model weights", status["boundaries"])

    def test_import_inline_unsigned_capsule_when_explicitly_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TrayStore(Path(temp_dir))
            item = store.import_inline_bundle(manifest(), allow_unsigned=True)

            self.assertEqual(item["state"], "pending")
            self.assertEqual(item["capsule_id"], "capsule-1")
            self.assertFalse(item["verification"]["signature"]["signed"])
            self.assertEqual(store.list_items("pending")[0]["bundle_id"], "capsule-1")

    def test_unsigned_import_is_rejected_without_explicit_allow(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TrayStore(Path(temp_dir))

            with self.assertRaises(ValueError) as raised:
                store.import_inline_bundle(manifest(), allow_unsigned=False)

        self.assertIn("Unsigned capsule imports", str(raised.exception))

    def test_signed_import_verifies_hmac_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TrayStore(Path(temp_dir), trusted_keys={"gx10": "secret"})
            item = store.import_inline_bundle(signed_manifest("secret"), allow_unsigned=False)

        self.assertTrue(item["verification"]["signature"]["ok"])
        self.assertEqual(item["verification"]["signature"]["key_id"], "gx10")

    def test_zip_import_verifies_checksums_and_blocks_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TrayStore(Path(temp_dir))
            item = store.import_zip_bytes(zip_bundle(manifest("zip-capsule")), allow_unsigned=True)

            self.assertTrue(item["verification"]["checksums"]["ok"])

            with self.assertRaises(ValueError):
                store.import_zip_bytes(zip_bundle(manifest("bad-zip"), include_bad_path=True), allow_unsigned=True)

    def test_attach_posts_only_to_capsule_handoff_endpoint(self) -> None:
        captured = []

        def fake_urlopen(request, timeout: int = 30) -> FakeAttachResponse:
            body = json.loads(request.data.decode("utf-8"))
            captured.append({"url": request.full_url, "body": body})
            if "/v1/chat/completions" in request.full_url:
                raise AssertionError("Tray must not send prompt traffic.")
            if body.get("phase") == "commit":
                return FakeAttachResponse(b'{"accepted":true,"result":{"thread_id":"thread-1"}}')
            return FakeAttachResponse(b'{"accepted":true,"handoff_id":"handoff-1"}')

        with tempfile.TemporaryDirectory() as temp_dir:
            store = TrayStore(Path(temp_dir))
            item = store.import_inline_bundle(manifest("attach-capsule"), allow_unsigned=True)
            result = store.attach(
                item["bundle_id"],
                "http://127.0.0.1:8765",
                target_launch={"launch_card_digest": "launch-abc", "context_size": 32768},
                urlopen=fake_urlopen,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["handoff_id"], "handoff-1")
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0]["url"], "http://127.0.0.1:8765/api/capsules/handoff")
        self.assertEqual(captured[0]["body"]["operation"], "upload")
        self.assertEqual(captured[0]["body"]["thread_id"], "thread-1")
        self.assertEqual(captured[0]["body"]["source"], "capsule_handoff_tray")
        self.assertEqual(captured[0]["body"]["compatibility"]["decision"], "hard_restore")
        self.assertEqual(captured[0]["body"]["sha256"], item["artifact_sha256"])
        self.assertNotIn("artifact_b64", captured[0]["body"])
        self.assertEqual(captured[1]["body"]["phase"], "commit")
        self.assertEqual(captured[1]["body"]["handoff_id"], "handoff-1")
        self.assertIn("artifact_b64", captured[1]["body"])


if __name__ == "__main__":
    unittest.main()
