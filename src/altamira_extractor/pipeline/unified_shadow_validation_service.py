"""Servicio de filesystem de la validacion diferencial del artefacto
unificado de candidatos en shadow mode (Fase 12 de la ampliacion
semantica, `feat/unified-shadow-differential-validation`).

Localiza un run, exige PARSED (SUCCEEDED) -- una precondicion DURA
(el run debe existir, `run.json` debe ser valido, y la etapa debe
haberse alcanzado) -- Y la existencia/validez de
`diagnostics/unified-candidates-shadow.json`
(`UnifiedCandidatesShadowArtifact`), el objeto PRINCIPAL de esta
validacion, NUNCA una fuente opcional: su ausencia, JSON sintacticamente
invalido, version incompatible, contrato Pydantic invalido o un fallo
de lectura del filesystem son SIEMPRE errores tecnicos DUROS -- el
reporte NUNCA se genera, se lanza `UnifiedShadowValidationError`, exit
code distinto de cero en el CLI, sin traceback, sin ruta absoluta, sin
archivo parcial, sin fabricar un artefacto vacio, sin sustituir el
artefacto real por otro.

A partir de ahi, CADA fuente SECUNDARIA (`CandidateArtifact` V1, V2/
interprocedural en memoria, assessment en memoria, review package/plan
persistidos) se intenta cargar de forma BLANDA: su ausencia o
invalidez NUNCA lanza una excepcion, se traduce en un `LoadedSource`
con `SourceAvailability.NOT_AVAILABLE`/`INVALID` que el analizador
PURO (Fase 12 Parte 10) convierte en un hallazgo representable dentro
del reporte (`NOT_EVALUATED`, `BLOCKED`, `REVIEW_REQUIRED` segun la
causa) -- esa respuesta SIGUE siendo un reporte generado exitosamente
(exit code 0 en el CLI), nunca un crash.

Persiste EXCLUSIVAMENTE en `<run_dir>/diagnostics/unified-shadow-
validation-report.json` via `atomic_write_json` (mismo mecanismo
atomico que el resto del pipeline). Nunca modifica `run.json`, ningun
`artifacts/`, ni ningun otro `diagnostics/` preexistente. Nunca
regenera un `unified-candidates-shadow.json`/plan/decisiones ausentes
-- si el unified-candidates-shadow.json no existe, el servicio falla
duro (nunca lo regenera ni lo reemplaza); si plan/decisiones no
existen, el reporte los refleja como fuente secundaria ausente. No usa
Neo4j ni un proveedor LLM."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.candidate import CandidateArtifact
from ..contracts.candidate_promotion_assessment import (
    CandidatePromotionAssessmentArtifact,
    SourceAvailability,
)
from ..contracts.candidate_promotion_plan import CandidatePromotionPlanArtifact
from ..contracts.candidate_promotion_review import CandidatePromotionReviewPackage
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.interprocedural_rule_candidates import InterproceduralRuleCandidatesArtifact
from ..contracts.run_state import RunState
from ..contracts.unified_candidates_shadow import UnifiedCandidatesShadowArtifact
from ..contracts.unified_shadow_validation import UnifiedShadowValidationReport
from ..contracts.v2_shadow_candidates import V2ShadowCandidatesArtifact
from .artifact_store import atomic_write_json
from .candidate_promotion_assessment_service import (
    compute_candidate_promotion_assessment_artifact,
)
from .errors import (
    CandidatePromotionAssessmentError,
    InterproceduralRuleCandidatesError,
    UnifiedShadowValidationError,
    V2ShadowCandidatesError,
)
from .interprocedural_rule_candidates_service import (
    compute_interprocedural_rule_candidates_artifact,
)
from .unified_shadow_source_validator import LoadedSource
from .unified_shadow_validation_analyzer import analyze_unified_shadow_validation
from .v2_shadow_candidates_service import compute_v2_shadow_candidates_artifact

_CANDIDATES_FILENAME = "06-candidates.json"
_SEMANTIC_GRAPH_FILENAME = "04-semantic-graph.json"
_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REVIEW_PACKAGE_FILENAME = "candidate-promotion-review-package.json"
_PLAN_FILENAME = "candidate-promotion-plan.json"
_UNIFIED_SHADOW_FILENAME = "unified-candidates-shadow.json"
_REPORT_FILENAME = "unified-shadow-validation-report.json"

_REQUIRED_STAGES = (PipelineStage.PARSED,)


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_json_artifact(artifact: object) -> str:
    """Duplicado deliberado de `unified_candidates_shadow_service.py::
    _hash_json_artifact` (misma razon documentada alli: V2/
    interprocedural se calculan EN MEMORIA, nunca se persisten por este
    servicio, y `candidate_promotion_assessment_service.py` ya registro
    ese mismo hash en `assessment.source_artifact_hashes` usando
    `model_dump_json()` -- esta fase debe reproducir EXACTAMENTE el
    mismo valor para que la verificacion de vigencia (Parte 4) compare
    lo comparable, nunca `to_stable_json()` aqui)."""
    dump = artifact.model_dump_json()  # type: ignore[attr-defined]
    return _hash_bytes(dump.encode("utf-8"))


def _hash_stable_json(artifact: object) -> str:
    return hashlib.sha256(artifact.to_stable_json().encode("utf-8")).hexdigest()  # type: ignore[attr-defined]


def _load_run_state(run_dir: Path) -> RunState:
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise UnifiedShadowValidationError(f"run {run_dir.name!r} no encontrado: run.json ausente")
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise UnifiedShadowValidationError("run.json invalido") from exc


def _require_stages_succeeded(state: RunState) -> None:
    for required_stage in _REQUIRED_STAGES:
        execution = next((s for s in state.stages if s.stage == required_stage), None)
        if execution is None or execution.status != StageStatus.SUCCEEDED:
            raise UnifiedShadowValidationError(
                f"el run no alcanzo {required_stage.value} (SUCCEEDED); no se puede "
                "validar el artefacto unificado de candidatos todavia"
            )


def _load_v1_candidates(run_dir: Path) -> tuple[LoadedSource, str | None]:
    path = run_dir / "artifacts" / _CANDIDATES_FILENAME
    if path.is_symlink() or not path.is_file():
        return LoadedSource(artifact=None, availability=SourceAvailability.NOT_AVAILABLE), None
    try:
        raw_bytes = path.read_bytes()
        artifact = CandidateArtifact.model_validate_json(raw_bytes.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return LoadedSource(artifact=None, availability=SourceAvailability.INVALID), None
    return (
        LoadedSource(artifact=artifact, availability=SourceAvailability.AVAILABLE),
        _hash_bytes(raw_bytes),
    )


def _load_v2_candidates(run_dir: Path, run_id: str) -> tuple[LoadedSource, str | None]:
    semantic_graph_path = run_dir / "artifacts" / _SEMANTIC_GRAPH_FILENAME
    candidates_path = run_dir / "artifacts" / _CANDIDATES_FILENAME
    prerequisites_present = (
        semantic_graph_path.is_file()
        and not semantic_graph_path.is_symlink()
        and candidates_path.is_file()
        and not candidates_path.is_symlink()
    )
    if not prerequisites_present:
        return LoadedSource(artifact=None, availability=SourceAvailability.NOT_AVAILABLE), None
    try:
        artifact: V2ShadowCandidatesArtifact = compute_v2_shadow_candidates_artifact(
            run_dir, run_id
        )
    except (V2ShadowCandidatesError, ValueError, OSError):
        return LoadedSource(artifact=None, availability=SourceAvailability.NOT_AVAILABLE), None
    return (
        LoadedSource(artifact=artifact, availability=SourceAvailability.AVAILABLE),
        _hash_json_artifact(artifact),
    )


def _load_interprocedural_candidates(run_dir: Path, run_id: str) -> tuple[LoadedSource, str | None]:
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    if not canonical_dir.is_dir() or canonical_dir.is_symlink():
        return LoadedSource(artifact=None, availability=SourceAvailability.NOT_AVAILABLE), None
    try:
        artifact: InterproceduralRuleCandidatesArtifact = (
            compute_interprocedural_rule_candidates_artifact(run_dir, run_id)
        )
    except (InterproceduralRuleCandidatesError, ValueError, OSError):
        return LoadedSource(artifact=None, availability=SourceAvailability.NOT_AVAILABLE), None
    return (
        LoadedSource(artifact=artifact, availability=SourceAvailability.AVAILABLE),
        _hash_json_artifact(artifact),
    )


def _load_assessment(run_dir: Path, run_id: str) -> tuple[LoadedSource, str | None]:
    try:
        artifact: CandidatePromotionAssessmentArtifact = (
            compute_candidate_promotion_assessment_artifact(run_dir, run_id)
        )
    except (CandidatePromotionAssessmentError, ValueError, OSError):
        return LoadedSource(artifact=None, availability=SourceAvailability.NOT_AVAILABLE), None
    return (
        LoadedSource(artifact=artifact, availability=SourceAvailability.AVAILABLE),
        _hash_stable_json(artifact),
    )


def _load_review_package(run_dir: Path) -> tuple[LoadedSource, str | None]:
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _REVIEW_PACKAGE_FILENAME
    if path.is_symlink() or not path.is_file():
        return LoadedSource(artifact=None, availability=SourceAvailability.NOT_AVAILABLE), None
    try:
        artifact = CandidatePromotionReviewPackage.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return LoadedSource(artifact=None, availability=SourceAvailability.INVALID), None
    return (
        LoadedSource(artifact=artifact, availability=SourceAvailability.AVAILABLE),
        _hash_stable_json(artifact),
    )


def _load_plan(run_dir: Path) -> tuple[LoadedSource, str | None]:
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _PLAN_FILENAME
    if path.is_symlink() or not path.is_file():
        return LoadedSource(artifact=None, availability=SourceAvailability.NOT_AVAILABLE), None
    try:
        artifact = CandidatePromotionPlanArtifact.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return LoadedSource(artifact=None, availability=SourceAvailability.INVALID), None
    return (
        LoadedSource(artifact=artifact, availability=SourceAvailability.AVAILABLE),
        _hash_stable_json(artifact),
    )


def _load_unified_shadow(run_dir: Path) -> tuple[LoadedSource, str]:
    """`UnifiedCandidatesShadowArtifact` es el objeto PRINCIPAL de esta
    validacion -- a diferencia de V1/V2/interprocedural/assessment/
    review package/plan (fuentes que Fase 12 valida de forma BLANDA,
    representando su ausencia/invalidez como un hallazgo dentro de un
    reporte `NOT_EVALUATED`), su ausencia, JSON sintacticamente
    invalido, version incompatible, contrato Pydantic invalido o un
    fallo de lectura del filesystem son SIEMPRE errores tecnicos DUROS:
    `UnifiedShadowValidationError` tipado, propagado sin capturar hasta
    el CLI -- nunca se genera un `UnifiedShadowValidationReport`, nunca
    se fabrica un artefacto vacio, nunca se sustituye el artefacto real
    por otro. El hash se calcula sobre los BYTES CRUDOS del archivo --
    es el valor contra el que el verificador de integridad de fuentes
    (Parte 4) recalcula un roundtrip independiente
    (`UNIFIED_ARTIFACT_HASH_MISMATCH`, Parte 11)."""
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _UNIFIED_SHADOW_FILENAME
    if path.is_symlink() or not path.is_file():
        raise UnifiedShadowValidationError(
            "el artefacto unificado de candidatos (diagnostics/unified-candidates-shadow.json) "
            "no existe: ejecute primero 'unified-candidates-shadow' para este run"
        )
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise UnifiedShadowValidationError(
            "el artefacto unificado de candidatos no se pudo leer del filesystem"
        ) from exc
    try:
        artifact = UnifiedCandidatesShadowArtifact.model_validate_json(raw_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UnifiedShadowValidationError(
            "el artefacto unificado de candidatos existente no es valido (JSON invalido, "
            "version incompatible o esquema incompatible) -- vuelva a ejecutar "
            "'unified-candidates-shadow'"
        ) from exc
    return (
        LoadedSource(artifact=artifact, availability=SourceAvailability.AVAILABLE),
        _hash_bytes(raw_bytes),
    )


def compute_unified_shadow_validation_report(
    run_dir: Path, run_id: str
) -> UnifiedShadowValidationReport:
    """Localiza `run_dir`, exige PARSED (SUCCEEDED) y la existencia/
    validez del artefacto unificado de candidatos -- las precondiciones
    DURAS (ver `_load_unified_shadow`) -- ANTES de intentar ninguna
    otra carga, para nunca invertir tiempo en fuentes secundarias
    (V2/interprocedural, potencialmente costosas) cuando el objeto
    principal de esta validacion ya es irrecuperable. El resto de las
    fuentes se cargan de forma blanda. Nunca escribe nada -- la
    persistencia es responsabilidad de
    `write_unified_shadow_validation_report`/el comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise UnifiedShadowValidationError(f"run {run_dir.name!r} no encontrado")

    state = _load_run_state(run_dir)
    _require_stages_succeeded(state)
    if state.source_package_hash is None:
        raise UnifiedShadowValidationError(
            "el run no registra source_package_hash; no se puede validar el artefacto "
            "unificado de candidatos todavia"
        )

    unified_shadow, unified_candidates_shadow_hash = _load_unified_shadow(run_dir)

    v1, candidate_v1_artifact_hash = _load_v1_candidates(run_dir)
    v2, v2_artifact_hash = _load_v2_candidates(run_dir, run_id)
    interprocedural, interprocedural_artifact_hash = _load_interprocedural_candidates(
        run_dir, run_id
    )
    assessment, assessment_artifact_hash = _load_assessment(run_dir, run_id)
    review_package, review_package_hash = _load_review_package(run_dir)
    plan, promotion_plan_hash = _load_plan(run_dir)

    source_artifact_hashes: dict[str, str] = {}
    if isinstance(assessment.artifact, CandidatePromotionAssessmentArtifact):
        source_artifact_hashes = dict(assessment.artifact.source_artifact_hashes)

    try:
        return analyze_unified_shadow_validation(
            run_id=run_id,
            source_package_hash=state.source_package_hash,
            v1=v1,
            v2=v2,
            interprocedural=interprocedural,
            assessment=assessment,
            review_package=review_package,
            plan=plan,
            unified_shadow=unified_shadow,
            candidate_v1_artifact_hash=candidate_v1_artifact_hash,
            v2_artifact_hash=v2_artifact_hash,
            interprocedural_artifact_hash=interprocedural_artifact_hash,
            assessment_artifact_hash=assessment_artifact_hash,
            review_package_hash=review_package_hash,
            promotion_plan_hash=promotion_plan_hash,
            unified_candidates_shadow_hash=unified_candidates_shadow_hash,
            source_artifact_hashes=source_artifact_hashes,
        )
    except UnifiedShadowValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
        raise UnifiedShadowValidationError(
            "no se pudo calcular el reporte de validacion diferencial: artefactos "
            "inconsistentes entre si"
        ) from exc


def unified_shadow_validation_report_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_unified_shadow_validation_report(
    run_dir: Path, report: UnifiedShadowValidationReport
) -> Path:
    """Persiste `report` de forma atomica en `diagnostics/unified-
    shadow-validation-report.json`. Nunca escribe un archivo parcial;
    nunca modifica ningun otro archivo del run."""
    report_path = unified_shadow_validation_report_path(run_dir)
    atomic_write_json(report_path, report)
    return report_path
