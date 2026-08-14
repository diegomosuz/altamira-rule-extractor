"""Exporta un bundle offline (Fase 15B4-B Seccion 24, mismo precedente
conceptual que `release/v1.0.0/`): `docker save` de ambas imagenes +
checksum SHA256 del tarball. Nunca asume que el cliente tiene Docker
-- el procedimiento de IMPORTACION en el cliente usa K3s/containerd
(`ctr images import`, ver docs/release/INSTALL_K3S.md), este script
solo produce el artefacto de TRANSFERENCIA.

PRERREQUISITO OBLIGATORIO (Fase 15B4-C, defectos P1/P2, cerrados en
Fase 15B4-C-RC-PACKAGING-REPRODUCIBILITY): ambas imagenes de entrada
DEBEN ya ser manifests planos/importables antes de invocar este
script -- ejecutar primero `python -m scripts.release.
build_release_images`, que construye `--app-image` sin provenance/SBOM
y repackea Neo4j (ver `scripts/release/neo4j-offline.Dockerfile`) como
`altamira-dependencies/neo4j:5.26.28`. Con ese repack ya aplicado,
invocar este script con `--neo4j-source-image` IGUAL a
`--neo4j-bundle-reference` (el repack ya dejo la imagen tageada
directamente con la referencia final; `create_local_alias` de abajo se
vuelve un `docker tag` no-op sobre si misma, inofensivo). Pasar
directamente la referencia oficial flotante `neo4j:5-community` como
`--neo4j-source-image` SIN repack previo produce un archive que
`ctr images import` rechaza (verificado empiricamente: "content digest
... not found" / "mismatched image rootfs and manifest layers") --
error real, no teorico.

Referencia offline de Neo4j (Fase 15B4-B2, corrige 15B4-B): un digest
puro (`neo4j@sha256:...`) es un riesgo como referencia offline --
verificado empiricamente (auditoria de archive real via `docker save`,
scratch fuera del repo) que el archive resultante trae, en su INDICE
OCI, la anotacion `io.containerd.image.name` asociada a la imagen
(nunca solo el digest); para la imagen original esa anotacion resuelve
a `docker.io/library/neo4j:5-community` (la referencia flotante que se
queria evitar). Este script SIEMPRE crea primero un alias LOCAL
controlado por el release (`docker tag <source> <bundle-reference>`,
idempotente) antes de `docker save` -- el alias es el que se
transporta, nunca la imagen bajo su tag original.

Uso (secuencia oficial completa, ver
docs/release/QA_TO_PROD_AND_ROLLBACK.md):
    python -m scripts.release.build_release_images
    python -m scripts.release.export_bundle \\
        --app-image altamira-rule-extractor-app:1.17.0 \\
        --neo4j-source-image altamira-dependencies/neo4j:5.26.28 \\
        --neo4j-bundle-reference altamira-dependencies/neo4j:5.26.28 \\
        --output-dir dist/release/1.17.0

No hace push. No hace tag remoto. No maneja secretos.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024

_DEFAULT_NEO4J_SOURCE_IMAGE = "neo4j:5-community"
_DEFAULT_NEO4J_BUNDLE_REFERENCE = "altamira-dependencies/neo4j:5.26.28"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_local_alias(source_image: str, bundle_reference: str) -> None:
    """`docker tag` es puramente local -- nunca contacta un registry,
    nunca hace push. Idempotente: repetir con el mismo par
    source/bundle no tiene efecto observable."""
    subprocess.run(  # noqa: S603, S607 - referencias validadas por el llamador, sin shell
        ["docker", "tag", source_image, bundle_reference],
        check=True,
    )


def docker_save(image_refs: list[str], tarball_path: Path) -> None:
    subprocess.run(  # noqa: S603, S607 - referencias validadas por el llamador, sin shell
        ["docker", "save", "-o", str(tarball_path), *image_refs],
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-image", required=True)
    parser.add_argument(
        "--neo4j-source-image",
        default=_DEFAULT_NEO4J_SOURCE_IMAGE,
        help="Imagen YA PRESENTE localmente (nunca se hace pull). Referencia original, "
        "posiblemente flotante -- nunca se guarda bajo este nombre en el bundle.",
    )
    parser.add_argument(
        "--neo4j-bundle-reference",
        default=_DEFAULT_NEO4J_BUNDLE_REFERENCE,
        help="Alias local controlado por el release -- esta es la referencia que "
        "sobrevive el docker save -> ctr images import, y la que deben usar los "
        "manifests K3s (deploy/k3s/neo4j-statefulset.yaml).",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--bundle-name",
        default="altamira-rule-extractor-images.tar",
        help="Nombre del tarball dentro de --output-dir.",
    )
    args = parser.parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    tarball_path = args.output_dir / args.bundle_name
    checksum_path = tarball_path.with_suffix(tarball_path.suffix + ".sha256")

    try:
        create_local_alias(args.neo4j_source_image, args.neo4j_bundle_reference)
        docker_save([args.app_image, args.neo4j_bundle_reference], tarball_path)
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: docker tag/save fallo: {exc}", file=sys.stderr)
        return 1

    checksum = _sha256_file(tarball_path)
    checksum_path.write_text(f"{checksum}  {tarball_path.name}\n", encoding="utf-8")

    print(f"tarball: {tarball_path}")
    print(f"sha256:  {checksum}")
    print(f"checksum file: {checksum_path}")
    print(f"neo4j bundle reference (usar en manifests): {args.neo4j_bundle_reference}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
