"""Servicio de filesystem del artefacto unificado de candidatos en
shadow mode (Fase 11 de la ampliacion semantica,
`feat/unified-candidate-artifact-shadow`).

Localiza un run, carga `CandidateArtifact` V1 DIRECTAMENTE del disco
(REQUERIDO -- a diferencia de Fase 9, que tolera su ausencia; aqui es
el baseline inmutable, objetivo #1 de esta fase), recalcula V2/
interprocedural **en memoria** delegando en los servicios ya
existentes de Fase 5/Fase 8 (mismo patron que
`candidate_promotion_assessment_service.py`, nunca lee/escribe sus
propios diagnosticos), recalcula el `CandidatePromotionAssessmentArtifact`
(Fase 9) **en memoria** delegando en `candidate_promotion_assessment_
service.py`, lee `CandidatePromotionReviewPackage`/
`CandidatePromotionPlanArtifact` YA PERSISTIDOS en disco (REQUERIDOS:
esta fase NUNCA regenera un plan ausente ni decisiones humanas),
ejecuta el analizador puro (`unified_candidates_shadow_analyzer.py`) y
persiste el resultado UNICAMENTE en `<run_dir>/diagnostics/
unified-candidates-shadow.json`.

NO es un `PipelineStage`: bajo demanda (CLI `unified-candidates-
shadow`), nunca desde `runner.py`/`run_ingestion`. Nunca modifica
`run.json` ni ningun artefacto de entrada, ni ningun otro diagnostico
(`candidate-promotion-review-package.json`/`candidate-promotion-plan.
json` solo se leen); nunca usa Neo4j ni un proveedor LLM directamente."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.candidate import CandidateArtifact
from ..contracts.candidate_promotion_assessment import CandidateSource
from ..contracts.candidate_promotion_plan import (
    CandidatePromotionPlanArtifact,
    PromotionPlanAction,
    PromotionPlanItemStatus,
)
from ..contracts.candidate_promotion_review import CandidatePromotionReviewPackage
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.interprocedural_rule_candidates import InterproceduralRuleCandidatesArtifact
from ..contracts.run_state import RunState
from ..contracts.unified_candidates_shadow import UnifiedCandidatesShadowArtifact
from ..contracts.v2_shadow_candidates import V2ShadowCandidatesArtifact
from .artifact_store import atomic_write_json
from .candidate_promotion_assessment_service import (
    compute_candidate_promotion_assessment_artifact,
)
from .errors import (
    CandidatePromotionAssessmentError,
    InterproceduralRuleCandidatesError,
    UnifiedCandidatesShadowError,
    V2ShadowCandidatesError,
)
from .interprocedural_rule_candidates_service import (
    compute_interprocedural_rule_candidates_artifact,
)
from .unified_candidates_shadow_analyzer import analyze_unified_candidates_shadow
from .v2_shadow_candidates_service import compute_v2_shadow_candidates_artifact

_CANDIDATES_FILENAME = "06-candidates.json"
_SEMANTIC_GRAPH_FILENAME = "04-semantic-graph.json"
_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REVIEW_PACKAGE_FILENAME = "candidate-promotion-review-package.json"
_PLAN_FILENAME = "candidate-promotion-plan.json"
_REPORT_FILENAME = "unified-candidates-shadow.json"

_REQUIRED_STAGES = (PipelineStage.PARSED,)


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_json_artifact(artifact: object) -> str:
    """Mismo metodo que `candidate_promotion_assessment_service.py::
    _hash_json_artifact` (V2/interprocedural, calculados en memoria,
    nunca persistidos por este servicio) -- deliberadamente duplicado
    para que esta fase nunca dependa de un modulo de Fase 9."""
    dump = artifact.model_dump_json()  # type: ignore[attr-defined]
    return _hash_bytes(dump.encode("utf-8"))


def _load_run_state(run_dir: Path) -> RunState:
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise UnifiedCandidatesShadowError(f"run {run_dir.name!r} no encontrado: run.json ausente")
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise UnifiedCandidatesShadowError("run.json invalido") from exc


def _require_stages_succeeded(state: RunState) -> None:
    for required_stage in _REQUIRED_STAGES:
        execution = next((s for s in state.stages if s.stage == required_stage), None)
        if execution is None or execution.status != StageStatus.SUCCEEDED:
            raise UnifiedCandidatesShadowError(
                f"el run no alcanzo {required_stage.value} (SUCCEEDED); no se puede "
                "calcular el artefacto unificado de candidatos todavia"
            )


def _load_v1_candidates(run_dir: Path) -> tuple[CandidateArtifact, str]:
    """V1 es REQUERIDO en esta fase (baseline inmutable, objetivo #1) --
    a diferencia de Fase 9, su ausencia o invalidez es SIEMPRE un error
    explicito, nunca `SourceAvailability.NOT_AVAILABLE`."""
    path = run_dir / "artifacts" / _CANDIDATES_FILENAME
    if path.is_symlink() or not path.is_file():
        raise UnifiedCandidatesShadowError(
            "CandidateArtifact V1 (artifacts/06-candidates.json) no existe: el baseline "
            "es requerido para este artefacto"
        )
    try:
        raw_bytes = path.read_bytes()
        artifact = CandidateArtifact.model_validate_json(raw_bytes.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise UnifiedCandidatesShadowError(
            "CandidateArtifact V1 (artifacts/06-candidates.json) es invalido"
        ) from exc
    return artifact, _hash_bytes(raw_bytes)


def _load_v2_candidates(
    run_dir: Path, run_id: str
) -> tuple[V2ShadowCandidatesArtifact | None, str | None]:
    semantic_graph_path = run_dir / "artifacts" / _SEMANTIC_GRAPH_FILENAME
    candidates_path = run_dir / "artifacts" / _CANDIDATES_FILENAME
    prerequisites_present = (
        semantic_graph_path.is_file()
        and not semantic_graph_path.is_symlink()
        and candidates_path.is_file()
        and not candidates_path.is_symlink()
    )
    if not prerequisites_present:
        return None, None
    try:
        artifact = compute_v2_shadow_candidates_artifact(run_dir, run_id)
    except (V2ShadowCandidatesError, ValueError, OSError):
        return None, None
    return artifact, _hash_json_artifact(artifact)


def _load_interprocedural_candidates(
    run_dir: Path, run_id: str
) -> tuple[InterproceduralRuleCandidatesArtifact | None, str | None]:
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    if not canonical_dir.is_dir() or canonical_dir.is_symlink():
        return None, None
    try:
        artifact = compute_interprocedural_rule_candidates_artifact(run_dir, run_id)
    except (InterproceduralRuleCandidatesError, ValueError, OSError):
        return None, None
    return artifact, _hash_json_artifact(artifact)


def _load_review_package(run_dir: Path) -> CandidatePromotionReviewPackage:
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _REVIEW_PACKAGE_FILENAME
    if path.is_symlink() or not path.is_file():
        raise UnifiedCandidatesShadowError(
            "el paquete de revision no existe: ejecute primero "
            "'candidate-promotion-review-package' para este run"
        )
    try:
        return CandidatePromotionReviewPackage.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise UnifiedCandidatesShadowError(
            "el paquete de revision existente no es valido (JSON invalido o esquema "
            "incompatible) -- vuelva a ejecutar 'candidate-promotion-review-package'"
        ) from exc


def _load_plan(run_dir: Path) -> CandidatePromotionPlanArtifact:
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _PLAN_FILENAME
    if path.is_symlink() or not path.is_file():
        raise UnifiedCandidatesShadowError(
            "el plan de promocion no existe: ejecute primero 'candidate-promotion-plan' "
            "para este run (esta fase nunca regenera un plan ausente)"
        )
    try:
        return CandidatePromotionPlanArtifact.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise UnifiedCandidatesShadowError(
            "el plan de promocion existente no es valido (JSON invalido o esquema "
            "incompatible) -- vuelva a ejecutar 'candidate-promotion-plan'"
        ) from exc


def _require_source_present_if_referenced(
    plan: CandidatePromotionPlanArtifact,
    *,
    source: CandidateSource,
    artifact: object | None,
    label: str,
) -> None:
    references_source = any(
        item.source == source
        and item.action == PromotionPlanAction.PROPOSE_SHADOW_PROMOTION
        and item.status == PromotionPlanItemStatus.VALID
        for item in plan.plan_items
    )
    if references_source and artifact is None:
        raise UnifiedCandidatesShadowError(
            f"el plan referencia propuestas aprobadas de origen {label}, pero el "
            f"artefacto {label} actual no esta disponible (ausente o invalido)"
        )


def compute_unified_candidates_shadow_artifact(
    run_dir: Path, run_id: str
) -> UnifiedCandidatesShadowArtifact:
    """Localiza `run_dir`, exige `PARSED` (`SUCCEEDED`), carga V1
    (requerido), recalcula V2/interprocedural y el assessment de Fase 9
    en memoria, carga review package/plan YA PERSISTIDOS, y ejecuta el
    analizador puro. Nunca escribe nada -- la persistencia es
    responsabilidad de `write_unified_candidates_shadow_artifact`/el
    comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise UnifiedCandidatesShadowError(f"run {run_dir.name!r} no encontrado")

    state = _load_run_state(run_dir)
    _require_stages_succeeded(state)

    v1_candidates, candidate_v1_artifact_hash = _load_v1_candidates(run_dir)
    v2_candidates, v2_artifact_hash = _load_v2_candidates(run_dir, run_id)
    interprocedural_candidates, interprocedural_artifact_hash = _load_interprocedural_candidates(
        run_dir, run_id
    )

    try:
        assessment = compute_candidate_promotion_assessment_artifact(run_dir, run_id)
    except CandidatePromotionAssessmentError as exc:
        raise UnifiedCandidatesShadowError(
            "no se pudo recalcular el catalogo unificado de candidatos (Fase 9) requerido"
        ) from exc

    review_package = _load_review_package(run_dir)
    plan = _load_plan(run_dir)

    _require_source_present_if_referenced(
        plan, source=CandidateSource.V2, artifact=v2_candidates, label="V2"
    )
    _require_source_present_if_referenced(
        plan,
        source=CandidateSource.INTERPROCEDURAL,
        artifact=interprocedural_candidates,
        label="INTERPROCEDURAL",
    )

    assessment_artifact_hash = hashlib.sha256(
        assessment.to_stable_json().encode("utf-8")
    ).hexdigest()
    review_package_hash = hashlib.sha256(
        review_package.to_stable_json().encode("utf-8")
    ).hexdigest()
    promotion_plan_hash = hashlib.sha256(plan.to_stable_json().encode("utf-8")).hexdigest()

    try:
        return analyze_unified_candidates_shadow(
            run_id=run_id,
            v1_candidates=v1_candidates,
            v2_candidates=v2_candidates,
            interprocedural_candidates=interprocedural_candidates,
            assessment=assessment,
            review_package=review_package,
            plan=plan,
            source_package_hash=assessment.source_package_hash,
            candidate_v1_artifact_hash=candidate_v1_artifact_hash,
            v2_artifact_hash=v2_artifact_hash,
            interprocedural_artifact_hash=interprocedural_artifact_hash,
            assessment_artifact_hash=assessment_artifact_hash,
            review_package_hash=review_package_hash,
            promotion_plan_hash=promotion_plan_hash,
            source_artifact_hashes=assessment.source_artifact_hashes,
        )
    except UnifiedCandidatesShadowError:
        raise
    except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
        raise UnifiedCandidatesShadowError(
            "no se pudo calcular el artefacto unificado de candidatos en shadow mode: "
            "artefactos inconsistentes entre si"
        ) from exc


def unified_candidates_shadow_artifact_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_unified_candidates_shadow_artifact(
    run_dir: Path, artifact: UnifiedCandidatesShadowArtifact
) -> Path:
    """Persiste `artifact` de forma atomica en `diagnostics/
    unified-candidates-shadow.json`. Nunca escribe un archivo parcial;
    nunca modifica ningun otro archivo del run."""
    report_path = unified_candidates_shadow_artifact_path(run_dir)
    atomic_write_json(report_path, artifact)
    return report_path
