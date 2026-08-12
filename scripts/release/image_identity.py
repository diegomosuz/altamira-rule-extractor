"""Inspecciona una imagen Docker YA PRESENTE localmente (nunca hace
pull) y extrae su identidad inmutable: RepoTags, RepoDigests, Id
(sha256 de la config de la imagen) y Labels OCI relevantes.

Uso:
    python -m scripts.release.image_identity <image-ref> [<image-ref> ...]

Nunca hace push/tag. Nunca asume una version desde Internet -- si la
imagen no esta presente localmente, falla explicitamente (nunca hace
pull por su cuenta; usar `docker pull`/`ctr images import` fuera de
este script si hace falta obtenerla).
"""

from __future__ import annotations

import json
import subprocess
import sys


def inspect_image(image_ref: str) -> dict[str, object]:
    """Falla con `subprocess.CalledProcessError` si la imagen no esta
    presente localmente -- nunca intenta un pull implicito."""
    result = subprocess.run(  # noqa: S603, S607 - imagen validada por el llamador, sin shell
        ["docker", "image", "inspect", image_ref],
        capture_output=True,
        text=True,
        check=True,
    )
    raw = json.loads(result.stdout)
    if not raw:
        raise ValueError(f"docker image inspect {image_ref!r} devolvio una lista vacia")
    entry = raw[0]
    return {
        "requested_ref": image_ref,
        "id": entry.get("Id"),
        "repo_tags": entry.get("RepoTags", []),
        "repo_digests": entry.get("RepoDigests", []),
        "labels": (entry.get("Config") or {}).get("Labels") or {},
        "created": entry.get("Created"),
        "size_bytes": entry.get("Size"),
    }


def main(argv: list[str]) -> int:
    if not argv:
        print("uso: image_identity.py <image-ref> [<image-ref> ...]", file=sys.stderr)
        return 2

    results: list[dict[str, object]] = []
    exit_code = 0
    for image_ref in argv:
        try:
            results.append(inspect_image(image_ref))
        except subprocess.CalledProcessError as exc:
            print(
                f"ERROR: {image_ref!r} no esta presente localmente (nunca se intenta pull "
                f"automatico): {exc.stderr.strip()}",
                file=sys.stderr,
            )
            exit_code = 1

    print(json.dumps(results, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
