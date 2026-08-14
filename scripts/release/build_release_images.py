"""Construye las DOS imagenes de release listas para exportar offline
(Fase 15B4-C-RC-PACKAGING-REPRODUCIBILITY): unico punto de verdad
versionado para lo que antes eran comandos manuales de qualification.

Sin este script, un `docker build`/`docker tag` ingenuo produce
imagenes que `ctr images import` (containerd, usado por K3s/kind)
rechaza -- ver Fase 15B4-C, defectos P1/P2 -- porque:

- `docker build` via buildx adjunta manifests de provenance/SBOM por
  defecto (`app`);
- la imagen oficial "neo4j:5-community" es un indice multi-plataforma
  con un manifest-referrer de atestacion adjunto (`neo4j`).

Este script:

1. build_app_image(): construye `altamira-rule-extractor-app:<version>`
   desde el `Dockerfile` del repo (`--target runtime`), con
   `--provenance=false --sbom=false` (manifest plano, importable), y
   los labels `org.opencontainers.image.{version,revision}` correctos
   (reutiliza `scripts.release.version_check.collect_version_report`
   para APP_VERSION/GIT_SHA -- nunca hardcodeados aqui).
2. build_neo4j_offline_image(): repackea Neo4j vía
   `scripts/release/neo4j-offline.Dockerfile` (ver ese archivo para el
   detalle completo), tambien con `--provenance=false --sbom=false`,
   tageado directamente como la referencia de bundle final
   (`altamira-dependencies/neo4j:5.26.28`) -- listo para
   `scripts/release/export_bundle.py` sin necesidad de re-tag
   adicional (usar `--neo4j-source-image` == `--neo4j-bundle-reference`
   en ese script cuando se invoca despues de este).

Uso (secuencia oficial completa, ver docs/release/QA_TO_PROD_AND_ROLLBACK.md):

    python -m scripts.release.build_release_images
    python -m scripts.release.export_bundle \\
        --app-image altamira-rule-extractor-app:<version> \\
        --neo4j-source-image altamira-dependencies/neo4j:5.26.28 \\
        --neo4j-bundle-reference altamira-dependencies/neo4j:5.26.28 \\
        --output-dir dist/release/<version>

Requiere Docker con soporte buildx (Docker Desktop/Engine actual). No
hace push. No hace pull explicito (deja que `docker build` resuelva
`neo4j:5-community` normalmente si no esta ya presente localmente --
identico comportamiento a un `docker build`/`docker pull` manual, sin
registry propio)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from scripts.release.export_bundle import (
    _DEFAULT_NEO4J_BUNDLE_REFERENCE,
    _DEFAULT_NEO4J_SOURCE_IMAGE,
)
from scripts.release.image_identity import inspect_image
from scripts.release.version_check import collect_version_report

REPO_ROOT = Path(__file__).resolve().parents[2]
_NEO4J_OFFLINE_DOCKERFILE = Path(__file__).resolve().parent / "neo4j-offline.Dockerfile"

# Nunca defaults implicitos de una version particular de buildx (Fase
# 15B4-C, Seccion 3 del enunciado): explicitos siempre, en el unico
# lugar que invoca `docker build` para el release.
_NO_ATTESTATION_FLAGS = ("--provenance=false", "--sbom=false")


def build_app_image(*, app_image_ref: str) -> dict[str, object]:
    """Construye el target `runtime` del `Dockerfile` del repo con los
    labels de identidad correctos. Falla explicitamente
    (`CalledProcessError`) si el build falla -- nunca continua con una
    imagen a medio construir."""
    version_report = collect_version_report()
    subprocess.run(  # noqa: S603, S607 - argumentos fijos/derivados del repo, sin shell
        [
            "docker",
            "build",
            "--target",
            "runtime",
            *_NO_ATTESTATION_FLAGS,
            "--build-arg",
            f"APP_VERSION={version_report['pyproject_version']}",
            "--build-arg",
            f"GIT_SHA={version_report['git_sha']}",
            "-t",
            app_image_ref,
            str(REPO_ROOT),
        ],
        check=True,
    )
    return inspect_image(app_image_ref)


def build_neo4j_offline_image(
    *, neo4j_source_image: str, neo4j_bundle_reference: str
) -> dict[str, object]:
    """Repackea `neo4j_source_image` (referencia oficial, posiblemente
    flotante) hacia un manifest plano tageado directamente como
    `neo4j_bundle_reference` -- ver `neo4j-offline.Dockerfile` para el
    porque. `FROM` dentro de ese Dockerfile fija la imagen fuente real;
    `neo4j_source_image` aqui solo debe coincidir con esa linea (se
    valida por convencion, nunca se sustituye dinamicamente -- cambiar
    la version de Neo4j exige editar ambos archivos juntos, ver
    docstring de `neo4j-offline.Dockerfile`)."""
    subprocess.run(  # noqa: S603, S607 - argumentos fijos/derivados del repo, sin shell
        [
            "docker",
            "build",
            *_NO_ATTESTATION_FLAGS,
            "-f",
            str(_NEO4J_OFFLINE_DOCKERFILE),
            "-t",
            neo4j_bundle_reference,
            str(_NEO4J_OFFLINE_DOCKERFILE.parent),
        ],
        check=True,
    )
    return inspect_image(neo4j_bundle_reference)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app-image", default=None, help="Default: altamira-rule-extractor-app:<pyproject version>"
    )
    parser.add_argument("--neo4j-source-image", default=_DEFAULT_NEO4J_SOURCE_IMAGE)
    parser.add_argument("--neo4j-bundle-reference", default=_DEFAULT_NEO4J_BUNDLE_REFERENCE)
    args = parser.parse_args(argv)

    app_image_ref = args.app_image
    if app_image_ref is None:
        version_report = collect_version_report()
        app_image_ref = f"altamira-rule-extractor-app:{version_report['pyproject_version']}"

    try:
        app_identity = build_app_image(app_image_ref=app_image_ref)
        neo4j_identity = build_neo4j_offline_image(
            neo4j_source_image=args.neo4j_source_image,
            neo4j_bundle_reference=args.neo4j_bundle_reference,
        )
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: docker build fallo: {exc}", file=sys.stderr)
        return 1

    result = {"app_image": app_identity, "neo4j_image": neo4j_identity}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
