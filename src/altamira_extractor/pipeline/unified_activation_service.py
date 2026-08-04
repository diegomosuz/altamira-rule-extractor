"""Servicio de filesystem del control plane de activacion unificada
(Fase 14A Parte 9, `feat/controlled-unified-activation`).

Localiza un run, exige PARSED (SUCCEEDED) -- la precondicion minima
comun a los cuatro modos -- carga y valida el YAML de configuracion
externo (`--config`, NUNCA copiado al repositorio ni al directorio del
run: solo su `config_hash`, sobre la representacion NORMALIZADA del
`UnifiedActivationConfig` ya validado -- `to_stable_json()`, nunca los
bytes crudos del YAML, para que dos YAML formateados distinto pero
logicamente identicos produzcan el mismo hash), y carga UNICAMENTE los
artefactos que ya existen en disco -- NUNCA regenera
`unified-candidates-shadow.json`/`unified-shadow-validation-report.
json`/`unified-shadow-downstream.json` ausentes, NUNCA ejecuta V1
(`CANDIDATES_DETECTED`/`GUARDRAILS_APPLIED`) si no corrio ya.

Cada fuente (V1 `CandidateArtifact`, `GuardrailDirectoryManifest` +
`GuardrailCandidateArtifact` individuales, `RulesDirectoryManifest`,
`UnifiedCandidatesShadowArtifact`, `UnifiedShadowValidationReport`,
`UnifiedShadowDownstreamArtifact`) se carga de forma BLANDA: su
ausencia nunca lanza una excepcion, se traduce en `None` que el
evaluador (Parte 8) convierte en un hallazgo representable
(`NOT_EVALUATED`/`BLOCKED` segun el modo y la causa) -- esa respuesta
sigue siendo un artefacto generado exitosamente (exit code 0 en el
CLI), nunca un crash.

Persiste EXCLUSIVAMENTE en `<run_dir>/diagnostics/unified-activation-
evaluation.json` via `atomic_write_json`. Nunca modifica `run.json`,
ningun `artifacts/`, ni ningun otro `diagnostics/` preexistente. No usa
Neo4j, no inicializa un proveedor real (esta fase nunca lo necesita)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.candidate import CandidateArtifact
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.guardrail_candidate import GuardrailCandidateArtifact
from ..contracts.guardrail_manifest import GuardrailDirectoryManifest
from ..contracts.rules_manifest import RulesDirectoryManifest
from ..contracts.run_state import RunState
from ..contracts.unified_activation_config import UnifiedActivationConfig
from ..contracts.unified_activation_evaluation import UnifiedActivationEvaluationArtifact
from ..contracts.unified_candidates_shadow import UnifiedCandidatesShadowArtifact
from ..contracts.unified_shadow_downstream import UnifiedShadowDownstreamArtifact
from ..contracts.unified_shadow_validation import UnifiedShadowValidationReport
from .artifact_store import atomic_write_json
from .errors import UnifiedActivationError
from .unified_activation_evaluator import (
    UnifiedActivationEvaluatorError,
    evaluate_unified_activation,
)
from .yaml_utils import read_yaml_config

_DIAGNOSTICS_DIR_NAME = "diagnostics"
_ARTIFACTS_DIR_NAME = "artifacts"
_CANDIDATES_FILENAME = "06-candidates.json"
_GUARDRAILS_DIR_NAME = "09-guardrails"
_GUARDRAIL_MANIFEST_FILENAME = "guardrail-manifest.json"
_RULES_DIR_NAME = "10-rules"
_RULES_MANIFEST_FILENAME = "rules-manifest.json"
_UNIFIED_SHADOW_FILENAME = "unified-candidates-shadow.json"
_VALIDATION_REPORT_FILENAME = "unified-shadow-validation-report.json"
_DOWNSTREAM_FILENAME = "unified-shadow-downstream.json"
_EVALUATION_FILENAME = "unified-activation-evaluation.json"

_REQUIRED_STAGES = (PipelineStage.PARSED,)


def _load_run_state(run_dir: Path) -> RunState:
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise UnifiedActivationError(f"run {run_dir.name!r} no encontrado: run.json ausente")
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise UnifiedActivationError("run.json invalido") from exc


def _require_stages_succeeded(state: RunState) -> None:
    for required_stage in _REQUIRED_STAGES:
        execution = next((s for s in state.stages if s.stage == required_stage), None)
        if execution is None or execution.status != StageStatus.SUCCEEDED:
            raise UnifiedActivationError(
                f"el run no alcanzo {required_stage.value} (SUCCEEDED); no se puede "
                "evaluar la activacion unificada todavia"
            )


def _load_config(config_path: Path) -> tuple[UnifiedActivationConfig, str]:
    """Carga y valida el YAML EXTERNO de configuracion -- NUNCA se
    copia al repositorio ni al directorio del run. `config_hash` se
    calcula sobre `to_stable_json()` del `UnifiedActivationConfig` YA
    VALIDADO (representacion normalizada), nunca sobre los bytes
    crudos del YAML: dos archivos con distinto formato/orden de claves
    pero la misma configuracion logica producen el mismo hash."""
    if config_path.is_symlink():
        raise UnifiedActivationError(
            "la ruta de configuracion no es un archivo regular (symlink rechazado)"
        )
    if not config_path.is_file():
        raise UnifiedActivationError("no se encontro el archivo de configuracion")
    try:
        document, _raw_hash = read_yaml_config(config_path)
    except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
        raise UnifiedActivationError("el archivo de configuracion no es YAML valido") from exc
    try:
        config = UnifiedActivationConfig.model_validate(document)
    except ValueError as exc:
        raise UnifiedActivationError(
            "el archivo de configuracion no cumple el esquema de UnifiedActivationConfig"
        ) from exc
    config_hash = _stable_hash(config)
    return config, config_hash


def _stable_hash(model: object) -> str:
    return hashlib.sha256(model.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_v1_candidates(run_dir: Path) -> tuple[CandidateArtifact | None, str | None]:
    path = run_dir / _ARTIFACTS_DIR_NAME / _CANDIDATES_FILENAME
    if path.is_symlink() or not path.is_file():
        return None, None
    try:
        raw_bytes = path.read_bytes()
        artifact = CandidateArtifact.model_validate_json(raw_bytes.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None, None
    return artifact, _hash_bytes(raw_bytes)


def _load_guardrail_sources(
    run_dir: Path,
) -> dict[str, GuardrailCandidateArtifact]:
    manifest_path = (
        run_dir / _ARTIFACTS_DIR_NAME / _GUARDRAILS_DIR_NAME / _GUARDRAIL_MANIFEST_FILENAME
    )
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return {}
    try:
        manifest = GuardrailDirectoryManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}

    result: dict[str, GuardrailCandidateArtifact] = {}
    guardrails_dir = run_dir / _ARTIFACTS_DIR_NAME / _GUARDRAILS_DIR_NAME
    for record in manifest.records:
        artifact_path = guardrails_dir / record.relative_filename
        if artifact_path.is_symlink() or not artifact_path.is_file():
            continue
        try:
            artifact = GuardrailCandidateArtifact.model_validate_json(
                artifact_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
        result[record.candidate_id] = artifact
    return result


def _load_rule_markdown_filenames(run_dir: Path) -> dict[str, str]:
    manifest_path = run_dir / _ARTIFACTS_DIR_NAME / _RULES_DIR_NAME / _RULES_MANIFEST_FILENAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return {}
    try:
        manifest = RulesDirectoryManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    return {record.candidate_id: record.relative_filename for record in manifest.records}


def _load_unified_shadow(
    run_dir: Path,
) -> tuple[UnifiedCandidatesShadowArtifact | None, str | None]:
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _UNIFIED_SHADOW_FILENAME
    if path.is_symlink() or not path.is_file():
        return None, None
    try:
        raw_bytes = path.read_bytes()
        artifact = UnifiedCandidatesShadowArtifact.model_validate_json(raw_bytes.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None, None
    return artifact, _hash_bytes(raw_bytes)


def _load_validation_report(
    run_dir: Path,
) -> tuple[UnifiedShadowValidationReport | None, str | None]:
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _VALIDATION_REPORT_FILENAME
    if path.is_symlink() or not path.is_file():
        return None, None
    try:
        raw_bytes = path.read_bytes()
        artifact = UnifiedShadowValidationReport.model_validate_json(raw_bytes.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None, None
    return artifact, _hash_bytes(raw_bytes)


def _load_downstream_artifact(
    run_dir: Path,
) -> tuple[UnifiedShadowDownstreamArtifact | None, str | None]:
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _DOWNSTREAM_FILENAME
    if path.is_symlink() or not path.is_file():
        return None, None
    try:
        raw_bytes = path.read_bytes()
        artifact = UnifiedShadowDownstreamArtifact.model_validate_json(raw_bytes.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None, None
    return artifact, _hash_bytes(raw_bytes)


def compute_unified_activation_evaluation(
    run_dir: Path, run_id: str, *, config_path: Path
) -> UnifiedActivationEvaluationArtifact:
    """Localiza `run_dir`, exige PARSED (SUCCEEDED), carga y valida el
    YAML de configuracion -- las precondiciones DURAS -- antes de
    cargar (blandamente) el resto de las fuentes e invocar el
    evaluador puro (Parte 8). Nunca escribe nada; la persistencia es
    responsabilidad de `write_unified_activation_evaluation`/el
    comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise UnifiedActivationError(f"run {run_dir.name!r} no encontrado")

    state = _load_run_state(run_dir)
    _require_stages_succeeded(state)
    if state.source_package_hash is None:
        raise UnifiedActivationError(
            "el run no registra source_package_hash; no se puede evaluar la activacion "
            "unificada todavia"
        )

    config, config_hash = _load_config(config_path)

    candidate_v1_artifact, candidate_v1_artifact_hash = _load_v1_candidates(run_dir)
    guardrail_artifacts_by_candidate_id = _load_guardrail_sources(run_dir)
    rule_markdown_filename_by_candidate_id = _load_rule_markdown_filenames(run_dir)
    unified_shadow, unified_candidates_shadow_hash = _load_unified_shadow(run_dir)
    validation_report, validation_report_hash = _load_validation_report(run_dir)
    downstream_artifact, downstream_artifact_hash = _load_downstream_artifact(run_dir)

    try:
        return evaluate_unified_activation(
            config,
            run_id=run_id,
            source_package_hash=state.source_package_hash,
            config_hash=config_hash,
            candidate_v1_artifact=candidate_v1_artifact,
            candidate_v1_artifact_hash=candidate_v1_artifact_hash,
            guardrail_artifacts_by_candidate_id=guardrail_artifacts_by_candidate_id,
            rule_markdown_filename_by_candidate_id=rule_markdown_filename_by_candidate_id,
            unified_shadow=unified_shadow,
            unified_candidates_shadow_hash=unified_candidates_shadow_hash,
            validation_report=validation_report,
            validation_report_hash=validation_report_hash,
            downstream_artifact=downstream_artifact,
            downstream_artifact_hash=downstream_artifact_hash,
        )
    except UnifiedActivationEvaluatorError as exc:
        raise UnifiedActivationError(str(exc)) from exc


def unified_activation_evaluation_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _EVALUATION_FILENAME


def write_unified_activation_evaluation(
    run_dir: Path, artifact: UnifiedActivationEvaluationArtifact
) -> Path:
    """Persiste `artifact` de forma atomica en `diagnostics/unified-
    activation-evaluation.json`. Nunca escribe un archivo parcial;
    nunca modifica ningun otro archivo del run."""
    artifact_path = unified_activation_evaluation_path(run_dir)
    atomic_write_json(artifact_path, artifact)
    return artifact_path


__all__ = [
    "compute_unified_activation_evaluation",
    "unified_activation_evaluation_path",
    "write_unified_activation_evaluation",
]
