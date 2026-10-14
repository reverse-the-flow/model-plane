from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse, urlunparse


NETWORK_MODES = {"private_trusted_lan", "local_only", "secured_remote"}
LOCAL_HOST_ALIASES = {"127.0.0.1", "localhost", "0.0.0.0", "::1", "host.docker.internal"}


def network_config(profile: dict[str, Any]) -> dict[str, Any]:
    configured = profile.get("network")
    return configured if isinstance(configured, dict) else {}


def network_mode(profile: dict[str, Any]) -> str:
    configured = str(network_config(profile).get("mode") or "private_trusted_lan").strip().lower()
    return configured if configured in NETWORK_MODES else "private_trusted_lan"


def clean_mdns_label(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9-]+", "-", value.strip()).strip("-").lower()
    return cleaned or "model-plane"


def default_mdns_host() -> str:
    return f"{clean_mdns_label(socket.gethostname())}.local"


def network_bind_host(profile: dict[str, Any]) -> str:
    configured = network_config(profile)
    if configured.get("bind_host"):
        host = str(configured["bind_host"])
        if network_mode(profile) == "local_only":
            host = host.strip()
            # Docker port publication requires an IP; do not resolve localhost via DNS.
            if host.lower() == "localhost":
                return "127.0.0.1"
        return host
    mode = network_mode(profile)
    if mode == "local_only":
        return "127.0.0.1"
    return "0.0.0.0"


def is_loopback_bind_host(value: Any) -> bool:
    """Accept loopback literals and localhost without consulting DNS."""
    if not isinstance(value, str):
        return False
    host = value.strip()
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def network_auth(profile: dict[str, Any]) -> str:
    configured = network_config(profile)
    if configured.get("auth"):
        return str(configured["auth"])
    if network_mode(profile) == "secured_remote":
        return "token"
    return "none"


def mdns_enabled(profile: dict[str, Any]) -> bool:
    configured = network_config(profile)
    if "mdns" in configured:
        return bool(configured["mdns"])
    return network_mode(profile) == "private_trusted_lan"


def advertise_enabled(profile: dict[str, Any]) -> bool:
    configured = network_config(profile)
    if "advertise" in configured:
        return bool(configured["advertise"])
    return network_mode(profile) == "private_trusted_lan"


def advertised_host(profile: dict[str, Any]) -> str:
    configured = network_config(profile)
    for key in ("advertise_host", "mdns_host", "public_host"):
        if configured.get(key):
            return str(configured[key])
    mode = network_mode(profile)
    if mode == "local_only":
        host = network_bind_host(profile)
        return host if is_loopback_bind_host(host) else "127.0.0.1"
    return default_mdns_host()


def url_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def url_with_host(url: str, host: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    netloc = url_host(host)
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def rewrite_local_url_for_policy(profile: dict[str, Any], url: str) -> str:
    parsed = urlparse(str(url or ""))
    if not parsed.scheme or not parsed.hostname:
        return str(url or "")
    if parsed.hostname not in LOCAL_HOST_ALIASES:
        return str(url)
    return url_with_host(str(url), advertised_host(profile))


def url_for_port(profile: dict[str, Any], port: int | str | None, path: str = "") -> str:
    if port in (None, ""):
        return ""
    suffix = path if path.startswith("/") or not path else f"/{path}"
    return f"http://{url_host(advertised_host(profile))}:{port}{suffix}"


def local_url_for_port(port: int | str | None, path: str = "") -> str:
    if port in (None, ""):
        return ""
    suffix = path if path.startswith("/") or not path else f"/{path}"
    return f"http://127.0.0.1:{port}{suffix}"


def docker_harness_url(profile: dict[str, Any], url: str) -> str:
    return url_with_host(url, "host.docker.internal")


def network_policy_summary(profile: dict[str, Any]) -> dict[str, Any]:
    mode = network_mode(profile)
    notes = []
    if mode == "private_trusted_lan":
        notes.append("Private trusted LAN mode binds to 0.0.0.0 with no auth and advertises a .local host.")
    elif mode == "local_only":
        notes.append("Local-only mode requires a loopback bind (default 127.0.0.1) and expects SSH tunnels for remote clients.")
    else:
        notes.append("Secured remote mode is reserved for token/Tailscale/TLS-style hardening.")
    return {
        "mode": mode,
        "bind_host": network_bind_host(profile),
        "auth": network_auth(profile),
        "mdns": mdns_enabled(profile),
        "advertise": advertise_enabled(profile),
        "advertise_host": advertised_host(profile),
        "supported": mode != "secured_remote",
        "ssh_tunnel_hint": "ssh -L <local_port>:127.0.0.1:<remote_port> <host>" if mode == "local_only" else None,
        "notes": notes,
    }


def validate_network_policy(profile: dict[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    configured = network_config(profile)
    configured_mode = str(configured.get("mode") or "private_trusted_lan").strip().lower()
    if configured_mode not in NETWORK_MODES:
        messages.append({
            "level": "error",
            "code": "network_mode",
            "message": "network.mode must be one of: private_trusted_lan, local_only, secured_remote.",
        })
        return messages
    mode = network_mode(profile)
    if mode == "local_only":
        bind_host = configured.get("bind_host")
        # Missing/null/empty overrides keep the loopback default. Other values
        # must be explicit loopback addresses, including false-y YAML scalars.
        if bind_host not in (None, "") and not is_loopback_bind_host(bind_host):
            messages.append({
                "level": "error",
                "code": "network_local_only_bind_host",
                "message": "network.mode local_only requires network.bind_host to be a loopback IP (127.0.0.0/8 or ::1) or localhost; remove the override to use 127.0.0.1.",
            })
    if mode == "private_trusted_lan" and network_auth(profile) != "none":
        messages.append({
            "level": "warning",
            "code": "network_private_lan_auth",
            "message": "Private trusted LAN mode is intended for no-auth home-network use; use secured_remote for auth later.",
        })
    if mode == "secured_remote":
        messages.append({
            "level": "warning",
            "code": "network_secured_remote_future",
            "message": "secured_remote is reserved for future token/Tailscale/TLS-style hardening; v1 remains export-only metadata.",
        })
    return messages


def network_mode_descriptors() -> list[dict[str, Any]]:
    return [
        {
            "mode": "private_trusted_lan",
            "label": "Private LAN",
            "bind_host": "0.0.0.0",
            "auth": "none",
            "mdns": True,
            "description": "Lowest-friction home-network mode. Visible on the trusted LAN.",
        },
        {
            "mode": "local_only",
            "label": "Local Only",
            "bind_host": "127.0.0.1",
            "auth": "none",
            "mdns": False,
            "description": "Single-machine or SSH-tunnel mode.",
        },
        {
            "mode": "secured_remote",
            "label": "Secured Remote",
            "bind_host": "configurable",
            "auth": "token",
            "mdns": "optional",
            "description": "Future hardened mode for token/Tailscale/TLS-style deployments.",
        },
    ]
