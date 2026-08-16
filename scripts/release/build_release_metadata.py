"""Ensambla `release-metadata.json` (Fase 15B4-B Seccion 23): la
metadata minima de un release, sin secretos, en un unico JSON simple
-- nunca una base de datos de releases, nunca un formato nuevo por
release.

Uso:
    python -m scripts.release.build_release_metadata \\
        --app-image altamira-rule-extractor-app:1.18.0 \\
        --neo4j-source-image neo4j:5-community \\
        --neo4j-bundle-reference altamira-dependencies/neo4j:5.26.28 \\
        --output release-metadata.json \\
        [--sbom-dir DIR]

Requiere que las imagenes esten YA PRESENTES localmente (reutiliza
`image_identity.inspect_image`, nunca hace pull) -- en particular,
`--neo4j-bundle-reference` debe existir ya como alias local (ver
`scripts/release/export_bundle.py::create_local_alias`, que lo crea
antes de `docker save`). Requiere que el working tree este limpio para
que `git_sha` sea significativo como identidad de release inmutable
(Fase 15B4-B Seccion 34/35: una imagen construida PRE-COMMIT nunca es
el release final) -- este script reporta `working_tree_dirty` en la
metadata pero NO bloquea su propia ejecucion (permite generar metadata
de qualification pre-commit explicitamente marcada como tal); el
proceso de release formal es responsable de rechazar un bundle con
`working_tree_dirty=true`.

Distincion Fase 15B4-B2 (`bundle_reference` vs `source_reference`):
la imagen `app` se construye localmente para este release, por lo que
ambas coinciden (nunca hay una fuente externa distinta). La imagen
`neo4j` en cambio se re-etiqueta desde una referencia flotante ya
presente (`source_reference`, p.ej. `neo4j:5-community`) hacia un
alias local controlado por el release (`bundle_reference`, p.ej.
`altamira-dependencies/neo4j:5.26.28`) -- ver el comentario extenso en
`deploy/k3s/neo4j-statefulset.yaml` para la justificacion empirica
(auditoria real de archive `docker save`; la verificacion end-to-end
de `ctr images import` contra un cluster real queda para Fase 15B4-C).
`image_id`
identifica el contenido real (mismo Id en ambas referencias, por ser
el mismo alias local); `source_repo_digest` es el digest de registry
ya asociado a la imagen (si existe -- nunca se fabrica uno) y
`detected_version` es la version detectada dentro de la imagen (no
verificada contra un registry publico en esta fase)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.release.image_identity import inspect_image
from scripts.release.version_check import collect_version_report


def _extract_repo_digest_for(repo_digests: list[str], repo_ref: str) -> str | None:
    """Busca, entre los RepoDigests ya presentes en la imagen
    inspeccionada, el que corresponde al nombre de repositorio de
    `repo_ref`. Nunca fabrica un digest -- si no hay coincidencia ni
    ningun RepoDigest presente, devuelve None explicitamente."""
    repo_name = repo_ref.split("@")[0].split(":")[0]
    for entry in repo_digests:
        entry_repo = entry.split("@")[0]
        if entry_repo == repo_name or entry_repo.endswith(f"/{repo_name}"):
            return entry
    return repo_digests[0] if repo_digests else None


def _detected_version_from_bundle_reference(bundle_reference: str) -> str:
    if ":" not in bundle_reference:
        return "unknown"
    return bundle_reference.rsplit(":", 1)[1]


def build_metadata(
    *,
    app_image_ref: str,
    neo4j_source_image_ref: str,
    neo4j_bundle_reference: str,
    sbom_dir: Path | None = None,
) -> dict[str, object]:
    version_report = collect_version_report()
    app_image = inspect_image(app_image_ref)
    neo4j_image = inspect_image(neo4j_bundle_reference)

    metadata: dict[str, object] = {
        "schema_version": "1.0",
        "app_version": version_report["pyproject_version"],
        "git_sha": version_report["git_sha"],
        "git_branch": version_report["git_branch"],
        "working_tree_dirty": version_report["working_tree_dirty"],
        "version_sources_consistent": version_report["consistent"],
        "build_timestamp_utc": datetime.now(UTC).isoformat(),
        "app_image": {
            "bundle_reference": app_image_ref,
            "source_reference": app_image_ref,
            "image_id": app_image["id"],
            "source_repo_digest": _extract_repo_digest_for(
                app_image["repo_digests"], app_image_ref
            ),
            "repo_digests": app_image["repo_digests"],
            "labels": app_image["labels"],
        },
        "neo4j_image": {
            "bundle_reference": neo4j_bundle_reference,
            "source_reference": neo4j_source_image_ref,
            "image_id": neo4j_image["id"],
            "source_repo_digest": _extract_repo_digest_for(
                neo4j_image["repo_digests"], neo4j_source_image_ref
            ),
            "detected_version": _detected_version_from_bundle_reference(neo4j_bundle_reference),
            "repo_digests": neo4j_image["repo_digests"],
        },
    }
    if sbom_dir is not None:
        metadata["sbom_dir"] = str(sbom_dir)
        metadata["sbom_files"] = (
            sorted(p.name for p in sbom_dir.glob("*.json")) if sbom_dir.is_dir() else []
        )
    return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-image", required=True)
    parser.add_argument(
        "--neo4j-source-image",
        default="neo4j:5-community",
        help="Referencia original ya presente localmente (posiblemente flotante).",
    )
    parser.add_argument(
        "--neo4j-bundle-reference",
        default="altamira-dependencies/neo4j:5.26.28",
        help="Alias local controlado por el release, ya creado via "
        "scripts/release/export_bundle.py antes de invocar este script.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sbom-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        metadata = build_metadata(
            app_image_ref=args.app_image,
            neo4j_source_image_ref=args.neo4j_source_image,
            neo4j_bundle_reference=args.neo4j_bundle_reference,
            sbom_dir=args.sbom_dir,
        )
    except Exception as exc:  # noqa: BLE001 - reportado como fallo de tooling de release, nunca silenciado
        print(f"ERROR generando release-metadata: {exc}", file=sys.stderr)
        return 1

    args.output.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))
    if metadata["working_tree_dirty"]:
        print(
            "ADVERTENCIA: working tree con cambios sin commitear -- esta metadata NO "
            "es valida para un release final (ver Fase 15B4-B Seccion 34/35)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
