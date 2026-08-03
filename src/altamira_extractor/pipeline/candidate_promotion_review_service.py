"""Servicio de filesystem del paquete de revision humana (Fase 10 de la
ampliacion semantica, `feat/controlled-candidate-promotion-plan`).

Localiza un run, DELEGA en `candidate_promotion_assessment_service.
compute_candidate_promotion_assessment_artifact` (Fase 9) para obtener
el `CandidatePromotionAssessmentArtifact` **en memoria** -- NUNCA lee ni
escribe `diagnostics/candidate-promotion-assessment.json`, ni siquiera
cuando ya existe en disco -- ejecuta el generador puro
(`candidate_promotion_review_generator.py`) y persiste el resultado
UNICAMENTE en `<run_dir>/diagnostics/candidate-promotion-review-
package.json`.

NO es un `PipelineStage`: se invoca exclusivamente bajo demanda (CLI
`candidate-promotion-review-package`), nunca desde `runner.py`/
`run_ingestion`. Nunca modifica `run.json` ni ningun artefacto de
entrada; nunca usa Neo4j ni un proveedor LLM directamente."""

from __future__ import annotations

from pathlib import Path

from ..contracts.candidate_promotion_review import CandidatePromotionReviewPackage
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.run_state import RunState
from .artifact_store import atomic_write_json
from .candidate_promotion_assessment_service import (
    compute_candidate_promotion_assessment_artifact,
)
from .candidate_promotion_review_generator import generate_candidate_promotion_review_package
from .errors import CandidatePromotionAssessmentError, CandidatePromotionReviewError

_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REPORT_FILENAME = "candidate-promotion-review-package.json"

_REQUIRED_STAGES = (PipelineStage.PARSED,)


def _load_run_state(run_dir: Path) -> RunState:
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise CandidatePromotionReviewError(
            f"run {run_dir.name!r} no encontrado: run.json ausente"
        )
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CandidatePromotionReviewError("run.json invalido") from exc


def _require_stages_succeeded(state: RunState) -> None:
    for required_stage in _REQUIRED_STAGES:
        execution = next((s for s in state.stages if s.stage == required_stage), None)
        if execution is None or execution.status != StageStatus.SUCCEEDED:
            raise CandidatePromotionReviewError(
                f"el run no alcanzo {required_stage.value} (SUCCEEDED); no se puede "
                "generar el paquete de revision todavia"
            )


def compute_candidate_promotion_review_package(
    run_dir: Path, run_id: str
) -> CandidatePromotionReviewPackage:
    """Localiza `run_dir`, exige `PARSED` (`SUCCEEDED`), calcula el
    `CandidatePromotionAssessmentArtifact` (Fase 9) en memoria y genera
    el `CandidatePromotionReviewPackage` correspondiente. Nunca escribe
    nada -- la persistencia es responsabilidad de
    `write_candidate_promotion_review_package`/el comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise CandidatePromotionReviewError(f"run {run_dir.name!r} no encontrado")

    state = _load_run_state(run_dir)
    _require_stages_succeeded(state)

    try:
        assessment = compute_candidate_promotion_assessment_artifact(run_dir, run_id)
    except CandidatePromotionAssessmentError as exc:
        raise CandidatePromotionReviewError(
            "no se pudo calcular el catalogo unificado de candidatos (Fase 9) requerido "
            "para el paquete de revision"
        ) from exc

    try:
        return generate_candidate_promotion_review_package(assessment)
    except CandidatePromotionReviewError:
        raise
    except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
        raise CandidatePromotionReviewError(
            "no se pudo generar el paquete de revision: assessment inconsistente"
        ) from exc


def candidate_promotion_review_package_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_candidate_promotion_review_package(
    run_dir: Path, package: CandidatePromotionReviewPackage
) -> Path:
    """Persiste `package` de forma atomica en `diagnostics/
    candidate-promotion-review-package.json`. Nunca escribe un archivo
    parcial: `atomic_write_json` ya garantiza temporal-hermano + flush +
    fsync + replace."""
    report_path = candidate_promotion_review_package_path(run_dir)
    atomic_write_json(report_path, package)
    return report_path
