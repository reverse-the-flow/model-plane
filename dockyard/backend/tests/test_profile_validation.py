from __future__ import annotations

import unittest

import yaml

from model_control_plane import app as app_module


def llama_profile(args: list[str], extra: dict | None = None) -> dict:
    profile = {
        "id": "llama-local",
        "name": "Local llama.cpp",
        "model": {"id": "local/example", "local_path": "/models/example.gguf"},
        "runtime": {"backend": "llama_cpp", "image": "example:1", "args": args},
        "container": {
            "name": "dockyard-llama-local",
            "host_port": 18080,
            "internal_port": 8080,
            "gpu": "all",
            "env": {},
            "volumes": [],
        },
        "health": {"url": "http://127.0.0.1:18080/health"},
    }
    if extra:
        profile.update(extra)
    return profile


class ProfileValidationTests(unittest.TestCase):
    def test_llama_cpp_validation_warns_when_moe_observability_is_missing(self) -> None:
        messages = app_module.validate_profile(llama_profile(args=[]))
        codes = {message["code"] for message in messages}

        self.assertIn("llama_cpp_moe_observability_flags", codes)
        self.assertIn("llama_cpp_moe_log_file", codes)
        self.assertIn("llama_cpp_moe_observability_paths", codes)
        self.assertTrue(
            all(message["level"] == "warning" for message in messages if message["code"].startswith("llama_cpp_moe_"))
        )

    def test_llama_cpp_example_profile_has_required_moe_observability_metadata(self) -> None:
        profile_path = app_module.PROFILES / "llama-cpp.example.yaml"
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))

        messages = app_module.validate_profile(profile)
        codes = {message["code"] for message in messages}

        self.assertNotIn("llama_cpp_moe_observability_flags", codes)
        self.assertNotIn("llama_cpp_moe_log_file", codes)
        self.assertNotIn("llama_cpp_moe_observability_paths", codes)


if __name__ == "__main__":
    unittest.main()
