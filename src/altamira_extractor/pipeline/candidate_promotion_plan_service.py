"""Servicio de filesystem del plan de promocion controlada (Fase 10 de
la ampliacion semantica, `feat/controlled-candidate-promotion-plan`).

Localiza un run, calcula el `CandidatePromotionAssessmentArtifact`
(Fase 9) **en memoria** (nunca lee/escribe su propio diagnostico), lee
el `CandidatePromotionReviewPackage` YA PERSISTIDO en `<run_dir>/
diagnostics/candidate-promotion-review-package.json` (generado
previamente por el comando `candidate-promotion-review-package` --
"ausente" es un error explicito aqui, a diferencia de las fuentes de
Fase 9, que siempre se calculan en memoria), carga y sanea la ruta
`--decisions` (un archivo EXTERNO, humano, nunca copiado al
repositorio), valida el manifiesto contra `CandidatePromotionDecision
Manifest`, ejecuta el constructor puro (`candidate_promotion_plan_
builder.py`) y persiste el resultado UNICAMENTE en `<run_dir>/
diagnostics/candidate-promotion-plan.json`.

NO es un `PipelineStage`: bajo demanda (CLI `candidate-promotion-
plan`), nunca desde `runner.py`/`run_ingestion`. Nunca modifica
`run.json` ni ningun artefacto de entrada; nunca usa Neo4j ni un
proveedor LLM directamente; nunca copia el manifiesto humano al
repositorio (solo se lee, se valida y se descarta)."""

from __future__ import annotations

from pathlib import Path

from ..contracts.candidate_promotion_assessment import CandidatePromotionAssessmentArtifact
from ..contracts.candidate_promotion_plan import CandidatePromotionPlanArtifact
from ..contracts.candidate_promotion_review import (
    CandidatePromotionDecisionManifest,
    CandidatePromotionReviewPackage,
)
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.run_state import RunState
from .artifact_store import atomic_write_json
from .candidate_promotion_assessment_service import (
    compute_candidate_promotion_assessment_artifact,
)
from .candidate_promotion_plan_builder import build_candidate_promotion_plan
from .errors import CandidatePromotionAssessmentError, CandidatePromotionPlanError

_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REVIEW_PACKAGE_FILENAME = "candidate-promotion-review-package.json"
_PLAN_FILENAME = "candidate-promotion-plan.json"

_REQUIRED_STAGES = (PipelineStage.PARSED,)


def _load_run_state(run_dir: Path) -> RunState:
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise CandidatePromotionPlanError(f"run {run_dir.name!r} no encontrado: run.json ausente")
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CandidatePromotionPlanError("run.json invalido") from exc


def _require_stages_succeeded(state: RunState) -> None:
    for required_stage in _REQUIRED_STAGES:
        execution = next((s for s in state.stages if s.stage == required_stage), None)
        if execution is None or execution.status != StageStatus.SUCCEEDED:
            raise CandidatePromotionPlanError(
                f"el run no alcanzo {required_stage.value} (SUCCEEDED); no se puede "
                "construir el plan todavia"
            )


def _load_assessment(run_dir: Path, run_id: str) -> CandidatePromotionAssessmentArtifact:
    try:
        return compute_candidate_promotion_assessment_artifact(run_dir, run_id)
    except CandidatePromotionAssessmentError as exc:
        raise CandidatePromotionPlanError(
            "no se pudo calcular el catalogo unificado de candidatos (Fase 9) requerido "
            "para el plan"
        ) from exc


def _load_review_package(run_dir: Path) -> CandidatePromotionReviewPackage:
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _REVIEW_PACKAGE_FILENAME
    if path.is_symlink() or not path.is_file():
        raise CandidatePromotionPlanError(
            "el paquete de revision no existe: ejecute primero "
            "'candidate-promotion-review-package' para este run"
        )
    try:
        return CandidatePromotionReviewPackage.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise CandidatePromotionPlanError(
            "el paquete de revision existente no es valido (JSON invalido o esquema "
            "incompatible) -- vuelva a ejecutar 'candidate-promotion-review-package'"
        ) from exc


def _resolve_decisions_path(decisions_path: str) -> Path:
    """Sanea la ruta de `--decisions`: nunca se acepta vacia, un
    symlink, ni algo que no sea un archivo regular existente. Es un
    archivo EXTERNO al repositorio (el manifiesto humano) -- nunca se
    restringe a un directorio del run, pero nunca se sigue un symlink
    ni se acepta un directorio en su lugar."""
    if not decisions_path or not decisions_path.strip():
        raise CandidatePromotionPlanError("la ruta de --decisions no puede estar vacia")
    path = Path(decisions_path)
    if path.is_symlink():
        raise CandidatePromotionPlanError("la ruta de --decisions no puede ser un symlink")
    if not path.is_file():
        raise CandidatePromotionPlanError(
            "el manifiesto de decisiones no existe o no es un archivo regular"
        )
    return path


def _load_decision_manifest(decisions_path: str) -> CandidatePromotionDecisionManifest:
    path = _resolve_decisions_path(decisions_path)
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CandidatePromotionPlanError("no se pudo leer el manifiesto de decisiones") from exc
    try:
        return CandidatePromotionDecisionManifest.model_validate_json(raw_text)
    except ValueError as exc:
        raise CandidatePromotionPlanError(
            "el manifiesto de decisiones no es JSON valido o no cumple el esquema "
            "esperado (version incompatible)"
        ) from exc


def compute_candidate_promotion_plan_artifact(
    run_dir: Path, run_id: str, *, decisions_path: str
) -> CandidatePromotionPlanArtifact:
    """Localiza `run_dir`, exige `PARSED` (`SUCCEEDED`), calcula el
    assessment (Fase 9, en memoria), carga el review package ya
    persistido, carga y sanea el manifiesto de `decisions_path`, y
    ejecuta el constructor puro. Nunca escribe nada -- la persistencia
    es responsabilidad de `write_candidate_promotion_plan_artifact`/el
    comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise CandidatePromotionPlanError(f"run {run_dir.name!r} no encontrado")

    state = _load_run_state(run_dir)
    _require_stages_succeeded(state)

    assessment = _load_assessment(run_dir, run_id)
    review_package = _load_review_package(run_dir)
    manifest = _load_decision_manifest(decisions_path)

    try:
        return build_candidate_promotion_plan(
            assessment=assessment, review_package=review_package, manifest=manifest
        )
    except CandidatePromotionPlanError:
        raise
    except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
        raise CandidatePromotionPlanError(
            "no se pudo construir el plan de promocion: artefactos inconsistentes entre si"
        ) from exc


def candidate_promotion_plan_artifact_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _PLAN_FILENAME


def write_candidate_promotion_plan_artifact(
    run_dir: Path, artifact: CandidatePromotionPlanArtifact
) -> Path:
    """Persiste `artifact` de forma atomica en `diagnostics/
    candidate-promotion-plan.json`. Nunca escribe un archivo parcial;
    nunca copia el manifiesto humano al repositorio (solo se leyo, se
    valido y se descarta)."""
    report_path = candidate_promotion_plan_artifact_path(run_dir)
    atomic_write_json(report_path, artifact)
    return report_path
