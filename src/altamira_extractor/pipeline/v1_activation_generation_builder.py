"""Constructor de generaciones V1 (Fase 14B Parte 6,
`feat/controlled-unified-materialization`).

Nunca copia ni modifica ningun archivo productivo: crea UNICAMENTE un
`MaterializedGenerationManifest` que REFERENCIA, por ruta relativa al
run, la mejor superficie V1 ya existente en disco --
`artifacts/06-candidates.json` (unica superficie OBLIGATORIA; sin ella
no existe una base V1 resoluble) mas, cuando existan realmente,
`artifacts/07-context/context-manifest.json`, `artifacts/08-rule-
drafts/rule-draft-manifest.json` y `artifacts/09-guardrails/guardrail-
manifest.json` -- una ausencia legitima de cualquiera de estas tres
NUNCA fabrica un archivo sintetico ni lanza un error (el router,
Parte 10, tipa esa ausencia explicitamente).

Separacion deliberada en dos capas (Parte 6: "ser puro respecto de
filesystem cuando recibe bytes/metadatos"): `_read_v1_surface` es la
UNICA funcion de este modulo con efectos de lectura sobre el
filesystem (lee, nunca escribe, nunca modifica); `_build_manifest_from_
bytes` es puro -- recibe bytes ya leidos, nunca un `Path`. El store
(Fase 14B Parte 8) es quien escribe la generacion resultante; este
modulo nunca escribe nada."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.unified_activation_materialization import (
    MaterializedActivationLane,
    MaterializedFileReference,
    MaterializedGenerationKind,
    MaterializedGenerationManifest,
    compute_generation_id,
)
from .errors import UnifiedMaterializationError

_LOGICAL_NAME_CANDIDATES = "candidates"
_LOGICAL_NAME_CONTEXT_PACKAGES = "context-packages"
_LOGICAL_NAME_RULE_DRAFTS = "rule-drafts"
_LOGICAL_NAME_GUARDRAILS = "guardrails"

_RELATIVE_PATH_BY_LOGICAL_NAME = {
    _LOGICAL_NAME_CANDIDATES: "artifacts/06-candidates.json",
    _LOGICAL_NAME_CONTEXT_PACKAGES: "artifacts/07-context/context-manifest.json",
    _LOGICAL_NAME_RULE_DRAFTS: "artifacts/08-rule-drafts/rule-draft-manifest.json",
    _LOGICAL_NAME_GUARDRAILS: "artifacts/09-guardrails/guardrail-manifest.json",
}


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_v1_surface(run_dir: Path) -> dict[str, bytes]:
    """UNICA funcion con efectos de lectura de este modulo. Nunca lee
    un symlink (rechazado explicitamente, mismo principio de
    saneamiento de rutas que el resto del pipeline); una ausencia
    (archivo inexistente/symlink) simplemente omite esa entrada del
    diccionario -- nunca lanza por si sola."""
    surface: dict[str, bytes] = {}
    for logical_name, relative_path in _RELATIVE_PATH_BY_LOGICAL_NAME.items():
        path = run_dir / relative_path
        if path.is_symlink() or not path.is_file():
            continue
        surface[logical_name] = path.read_bytes()
    return surface


def _build_manifest_from_bytes(
    surface: dict[str, bytes],
    *,
    run_id: str,
    source_package_hash: str,
    activation_evaluation_hash: str,
    authorization_hash: str,
) -> MaterializedGenerationManifest:
    """Punto de entrada PURO: nunca toca el filesystem, nunca muta
    `surface`. `candidates` es la unica clave obligatoria."""
    candidates_bytes = surface.get(_LOGICAL_NAME_CANDIDATES)
    if candidates_bytes is None:
        raise UnifiedMaterializationError(
            "no existe una base V1 resoluble: artifacts/06-candidates.json ausente o invalido"
        )

    files: list[MaterializedFileReference] = []
    for logical_name, relative_path in _RELATIVE_PATH_BY_LOGICAL_NAME.items():
        data = surface.get(logical_name)
        if data is None:
            continue
        files.append(
            MaterializedFileReference(
                logical_name=logical_name,
                relative_path=relative_path,
                sha256=_hash_bytes(data),
                byte_size=len(data),
            )
        )
    files.sort(key=lambda reference: reference.logical_name)
    file_hashes = {reference.logical_name: reference.sha256 for reference in files}

    generation_id = compute_generation_id(
        lane=MaterializedActivationLane.V1,
        kind=MaterializedGenerationKind.V1_BASELINE,
        run_id=run_id,
        file_hashes=file_hashes,
    )

    return MaterializedGenerationManifest(
        generation_id=generation_id,
        run_id=run_id,
        lane=MaterializedActivationLane.V1,
        kind=MaterializedGenerationKind.V1_BASELINE,
        source_package_hash=source_package_hash,
        activation_evaluation_hash=activation_evaluation_hash,
        authorization_hash=authorization_hash,
        candidate_v1_artifact_hash=_hash_bytes(candidates_bytes),
        files=files,
    )


def build_v1_generation_manifest(
    run_dir: Path,
    *,
    run_id: str,
    source_package_hash: str,
    activation_evaluation_hash: str,
    authorization_hash: str,
) -> MaterializedGenerationManifest:
    """Envoltorio de conveniencia: lee la superficie V1 real
    (`_read_v1_surface`) y delega el calculo puro a
    `_build_manifest_from_bytes`. Lanza `UnifiedMaterializationError`
    si no existe una base V1 resoluble (`artifacts/06-candidates.json`
    ausente o invalido)."""
    surface = _read_v1_surface(run_dir)
    return _build_manifest_from_bytes(
        surface,
        run_id=run_id,
        source_package_hash=source_package_hash,
        activation_evaluation_hash=activation_evaluation_hash,
        authorization_hash=authorization_hash,
    )


__all__ = ["build_v1_generation_manifest"]
