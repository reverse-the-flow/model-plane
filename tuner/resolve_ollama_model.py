from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any


def run_command(command: list[str]) -> str:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout.strip()


def normalize_model_name(model_name: str) -> tuple[str, str]:
    if ":" in model_name:
        repo, tag = model_name.rsplit(":", 1)
    else:
        repo, tag = model_name, "latest"
    return repo, tag


def manifest_path_for_model(model_name: str) -> str:
    repo, tag = normalize_model_name(model_name)
    if repo.startswith("hf.co/") or repo.startswith("registry.ollama.ai/"):
        manifest_root = repo
    else:
        manifest_root = f"registry.ollama.ai/library/{repo}"
    return f"/root/.ollama/models/manifests/{manifest_root}/{tag}"


def blob_path(digest: str) -> str:
    return f"/root/.ollama/models/blobs/sha256-{digest.removeprefix('sha256:')}"


def read_manifest(container_name: str, model_name: str) -> dict[str, Any]:
    manifest_path = manifest_path_for_model(model_name)
    output = run_command(["docker", "exec", container_name, "cat", manifest_path])
    manifest = json.loads(output)
    manifest["_manifest_path"] = manifest_path
    return manifest


def read_blob_magic(container_name: str, path: str) -> str | None:
    try:
        output = run_command(
            [
                "docker",
                "exec",
                container_name,
                "sh",
                "-lc",
                f"od -An -tx1 -N 4 {path}",
            ]
        )
    except subprocess.SubprocessError:
        return None
    cleaned = "".join(output.split()).lower()
    if cleaned == "47475546":
        return "GGUF"
    return cleaned or None


def summarize_model(container_name: str, volume_name: str, model_name: str) -> dict[str, Any]:
    manifest = read_manifest(container_name, model_name)
    model_layers = [layer for layer in manifest.get("layers", []) if layer.get("mediaType") == "application/vnd.ollama.image.model"]
    if not model_layers:
        raise RuntimeError(f"No model layer found for {model_name}")

    primary_model = model_layers[0]
    primary_path = blob_path(primary_model["digest"])
    extra_layers = []
    projector_path = None
    for layer in manifest.get("layers", []):
        media_type = layer.get("mediaType", "")
        if media_type == "application/vnd.ollama.image.projector":
            projector_path = blob_path(layer["digest"])
        if media_type != "application/vnd.ollama.image.model":
            extra_layers.append(
                {
                    "media_type": media_type,
                    "digest": layer.get("digest"),
                    "path": blob_path(layer["digest"]) if layer.get("digest") else None,
                }
            )

    return {
        "model_name": model_name,
        "container_name": container_name,
        "volume_name": volume_name,
        "manifest_path": manifest["_manifest_path"],
        "primary_model": {
            "digest": primary_model["digest"],
            "path_in_volume": primary_path,
            "size_bytes": primary_model.get("size"),
            "magic": read_blob_magic(container_name, primary_path),
        },
        "projector": {
            "path_in_volume": projector_path,
            "present": projector_path is not None,
        },
        "extra_layers": extra_layers,
        "llama_cpp_likely_compatible": read_blob_magic(container_name, primary_path) == "GGUF",
        "docker_mount": f"{volume_name}:/root/.ollama:ro",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve an Ollama model name to its stored blob path.")
    parser.add_argument("--container", default="ollama", help="Running Ollama container name.")
    parser.add_argument("--volume", default="open-webui_ollama", help="Docker volume that stores Ollama models.")
    parser.add_argument("--model", required=True, help="Model name as shown by `ollama list`.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = summarize_model(args.container, args.volume, args.model)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
