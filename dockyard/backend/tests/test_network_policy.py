from __future__ import annotations

import unittest
from unittest import mock

from model_control_plane import app as app_module
from model_control_plane.harness_integrations import build_harness_integration_bundle
from model_control_plane.network_policy import network_policy_summary, rewrite_local_url_for_policy
from model_control_plane.profile_types import capsule_client_base_url, capsule_healthcheck_url, render_capsule_gateway_command


def profile(mode: str | None = None) -> dict:
    data = {
        "id": "llama-local",
        "name": "Local llama.cpp",
        "model": {"id": "local/example"},
        "runtime": {"backend": "llama_cpp", "image": "example:1", "args": []},
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
    if mode is not None:
        data["network"] = {"mode": mode}
    return data


def capsule_profile() -> dict:
    return {
        "id": "capsule-local",
        "name": "Capsule gateway",
        "profile_type": "capsule_gateway",
        "runtime": {"backend": "capsule_gateway"},
        "capsule_gateway": {
            "repo_path": "C:/session-capsule",
            "state_dir": "C:/session-capsule-state",
            "endpoint_id": "local-llama",
            "host": "0.0.0.0",
            "port": 8765,
            "checkpoint_mode": "soft",
            "slot": 0,
            "default_prefill": "user_default",
            "healthcheck_url": "http://127.0.0.1:8765/api/capsules/status",
            "client_base_url": "http://127.0.0.1:8765/v1",
        },
    }


class NetworkPolicyTests(unittest.TestCase):
    def test_default_network_policy_is_private_trusted_lan(self) -> None:
        with mock.patch("model_control_plane.network_policy.socket.gethostname", return_value="Kitchen GX10"):
            summary = network_policy_summary(profile())
            rewritten = rewrite_local_url_for_policy(profile(), "http://127.0.0.1:18080/v1")

        self.assertEqual(summary["mode"], "private_trusted_lan")
        self.assertEqual(summary["bind_host"], "0.0.0.0")
        self.assertEqual(summary["auth"], "none")
        self.assertTrue(summary["mdns"])
        self.assertEqual(summary["advertise_host"], "kitchen-gx10.local")
        self.assertEqual(rewritten, "http://kitchen-gx10.local:18080/v1")

    def test_local_only_policy_keeps_localhost_and_docker_tunnel_shape(self) -> None:
        summary = network_policy_summary(profile("local_only"))
        command = app_module.render_command(profile("local_only"))

        self.assertEqual(summary["mode"], "local_only")
        self.assertEqual(summary["bind_host"], "127.0.0.1")
        self.assertFalse(summary["mdns"])
        self.assertIn("-L <local_port>:127.0.0.1:<remote_port>", summary["ssh_tunnel_hint"])
        self.assertIn("127.0.0.1:18080:8080", command)

    def test_private_lan_policy_publishes_docker_on_all_interfaces(self) -> None:
        command = app_module.render_command(profile())

        self.assertIn("0.0.0.0:18080:8080", command)

    def test_private_lan_bundle_keeps_docker_harness_variant(self) -> None:
        with mock.patch("model_control_plane.network_policy.socket.gethostname", return_value="Kitchen GX10"):
            bundle = build_harness_integration_bundle(profile())

        self.assertEqual(bundle["preferred_base_url"], "http://kitchen-gx10.local:18080/v1")
        self.assertEqual(bundle["connectivity_targets"]["host"], "http://kitchen-gx10.local:18080/v1/models")
        self.assertEqual(bundle["connectivity_targets"]["docker_harness"], "http://host.docker.internal:18080/v1/models")

    def test_private_lan_capsule_health_stays_local_but_client_url_is_advertised(self) -> None:
        with mock.patch("model_control_plane.network_policy.socket.gethostname", return_value="Kitchen GX10"):
            selected = capsule_profile()
            command = render_capsule_gateway_command(selected)
            health_url = capsule_healthcheck_url(selected)
            client_url = capsule_client_base_url(selected)

        self.assertIn("--host", command)
        self.assertIn("0.0.0.0", command)
        self.assertEqual(health_url, "http://127.0.0.1:8765/api/capsules/status")
        self.assertEqual(client_url, "http://kitchen-gx10.local:8765/v1")

    def test_network_modes_endpoint_lists_three_modes(self) -> None:
        modes = {entry["mode"]: entry for entry in app_module.network_modes()}

        self.assertEqual({"private_trusted_lan", "local_only", "secured_remote"}, set(modes))
        self.assertEqual(modes["private_trusted_lan"]["auth"], "none")
        self.assertEqual(modes["local_only"]["bind_host"], "127.0.0.1")
        self.assertEqual(modes["secured_remote"]["auth"], "token")

    def test_secured_remote_validates_as_future_warning(self) -> None:
        messages = app_module.validate_profile(profile("secured_remote"))
        codes = {message["code"] for message in messages}

        self.assertIn("network_secured_remote_future", codes)


if __name__ == "__main__":
    unittest.main()
