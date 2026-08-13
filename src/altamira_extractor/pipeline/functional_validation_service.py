"""Servicio de filesystem de validacion funcional (Fase 15B2-A, Parte F).
Carga `config/ground_truth/synthetic_engineering.yaml` (Parte E), invoca
`compute_candidate_promotion_assessment_artifact` (Fase 9, ya existente --
este servicio NUNCA recalcula candidatos por su cuenta ni relee un
`diagnostics/candidate-promotion-assessment.json` persistido, mismo
principio que `candidate_promotion_review_service.py`), ejecuta el
matching puro (`functional_validation_matcher.validate_ground_truth`) y
persiste el resultado en `<run_dir>/diagnostics/functional-validation-
report.json`.

NO es un `PipelineStage`: se invoca exclusivamente bajo demanda (CLI),
nunca desde `runner.py`/`run_ingestion`. Nunca modifica ningun artefacto
de entrada (decision arquitectonica #7).

`_compute_run_fixture_hashes` (checkpoint correctivo, cierre de Fase
15B2-A): calcula el sha256 de CADA archivo regular realmente ingerido
por este run (`run_dir/work/extracted/**`, la copia intacta post-ZIP
Slip-safe del paquete original -- ver `runner.py::_run_extracted`).
`functional_validation_matcher.validate_ground_truth` usa este conjunto
para decidir la aplicabilidad de cada `GroundTruthCase`: un caso cuyo
fixture set no esta byte-a-byte presente en este run nunca se evalua."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from ..contracts.candidate import CandidateArtifact
from ..contracts.candidate_promotion_assessment import CandidatePromotionAssessmentArtifact
from ..contracts.context_package import ContextPackage
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.functional_ground_truth import FunctionalGroundTruthSet
from ..contracts.functional_validation import (
    ArtifactChainIntegrityReport,
    FinalRuleLinkageReport,
    FinalRuleLinkageStatus,
    FunctionalValidationReport,
    ValidationSource,
)
from ..contracts.rules_manifest import RulesDirectoryManifest
from ..contracts.run_state import RunState
from .artifact_store import atomic_write_json
from .candidate_promotion_assessment_service import compute_candidate_promotion_assessment_artifact
from .candidate_source_adapters import adapt_productive_candidates
from .errors import (
    CandidatePromotionAssessmentError,
    FunctionalValidationError,
    SemanticConfigError,
)
from .functional_validation_matcher import GuardrailLookupEntry, validate_ground_truth
from .yaml_utils import read_yaml_config

_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REPORT_FILENAME = "functional-validation-report.json"
_EXTRACTED_DIR_RELATIVE = ("work", "extracted")
_GUARDRAIL_MANIFEST_RELATIVE = ("artifacts", "09-guardrails", "guardrail-manifest.json")
_CANDIDATES_RELATIVE = ("artifacts", "06-candidates.json")
_CONTEXT_DIR_RELATIVE = ("artifacts", "07-context")
_RULES_MANIFEST_RELATIVE = ("artifacts", "10-rules", "rules-manifest.json")


def _load_guardrail_lookup(run_dir: Path) -> dict[str, GuardrailLookupEntry]:
    """Carga `artifacts/09-guardrails/guardrail-manifest.json` (si
    existe -- ausente es normal para un run con cero candidatos V1
    promovidos) y construye `candidate_id -> GuardrailLookupEntry`, para
    que `validate_ground_truth` pueda trazar metricas de caso reales
    (Seccion 5, cierre de Fase 15B2-A) sin que el analizador puro toque
    filesystem."""
    manifest_path = run_dir.joinpath(*_GUARDRAIL_MANIFEST_RELATIVE)
    if not manifest_path.is_file():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        record["candidate_id"]: GuardrailLookupEntry(
            verdict=record["final_evidence_validation_status"],
            repair_attempts=record["repair_attempts_used"],
        )
        for record in manifest.get("records", [])
    }


def _compute_run_fixture_hashes(run_dir: Path) -> frozenset[str]:
    """sha256 hexdigest de cada archivo regular bajo `run_dir/work/
    extracted/`, sin importar su ruta relativa dentro del ZIP original
    (una fixture identica byte a byte pero re-empaquetada bajo otro
    directorio interno sigue siendo la MISMA fixture). Directorio
    ausente (run que nunca alcanzo EXTRACTED) -> conjunto vacio, nunca
    un error: eso simplemente hace que ningun caso resulte APPLICABLE."""
    extracted_dir = run_dir.joinpath(*_EXTRACTED_DIR_RELATIVE)
    if not extracted_dir.is_dir():
        return frozenset()
    hashes: set[str] = set()
    for path in extracted_dir.rglob("*"):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        hashes.add(digest.hexdigest())
    return frozenset(hashes)


def _load_run_state(run_dir: Path) -> RunState:
    """Mismo principio que `candidate_promotion_assessment_service.py::
    _load_run_state` (duplicado deliberado, nunca importado de un
    modulo privado ajeno -- mismo precedente que `_program_name_from_
    paragraph_id` en `candidate_source_adapters.py`)."""
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise FunctionalValidationError(f"run {run_dir.name!r} no encontrado: run.json ausente")
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise FunctionalValidationError("run.json invalido") from exc


def _artifact_filename(candidate_id: str) -> str:
    """Misma formula que `contexts_built_stage.py`/`rule_drafts_
    generated_stage.py`/`guardrails_applied_stage.py` (identica en las
    tres etapas, sha256 del candidate_id) -- nunca reimportada de un
    modulo privado, solo la formula replicada, igual que los tests
    herméticos existentes."""
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


def _stage_succeeded(state: RunState, stage: PipelineStage) -> bool:
    return any(s.stage == stage and s.status == StageStatus.SUCCEEDED for s in state.stages)


def _load_productive_candidate_artifact(
    run_dir: Path, state: RunState
) -> tuple[CandidateArtifact | None, str | None]:
    """Fase 15B4-CANDIDATE-QUALITY-5C: UNICA fuente de candidatos para
    la qualification productiva -- lee `artifacts/06-candidates.json`
    tal como el run lo persistio, nunca recalcula V1/V2/interprocedural.
    AUSENTE: `candidates_detected_stage.py::run_candidates_detected_stage`
    SIEMPRE escribe este artefacto (incondicionalmente, incluso con 0
    candidatos) en cuanto CANDIDATES_DETECTED alcanza SUCCEEDED (5C-SAFETY-2
    seccion 1) -- por eso la ausencia solo es legitima ANTES de esa etapa;
    si el run YA la alcanzo, un 06 ausente es una falla de integridad real,
    nunca "cero candidatos". PRESENTE pero invalido/corrupto (5C-SAFETY
    seccion 12) -> `FunctionalValidationError` igual que antes: tratar un
    artefacto corrupto como "cero candidatos" fabricaria un `recall`
    identico al de una extraccion limpia que genuinamente no encontro
    nada -- exactamente la clase de defecto honesto que
    P1-PRODUCTIVE-ARTIFACT-QUALIFICATION corrige."""
    path = run_dir.joinpath(*_CANDIDATES_RELATIVE)
    if path.is_symlink() or not path.is_file():
        if _stage_succeeded(state, PipelineStage.CANDIDATES_DETECTED):
            raise FunctionalValidationError(
                "el run alcanzo CANDIDATES_DETECTED (SUCCEEDED) pero "
                "artifacts/06-candidates.json esta ausente -- falla de integridad de artefactos"
            )
        return None, None
    try:
        raw_bytes = path.read_bytes()
        artifact = CandidateArtifact.model_validate_json(raw_bytes.decode("utf-8"))
    except OSError as exc:
        raise FunctionalValidationError(
            f"artifacts/06-candidates.json no se pudo leer: {exc}"
        ) from exc
    except ValueError as exc:
        raise FunctionalValidationError(
            f"artifacts/06-candidates.json existe pero es invalido/corrupto: {exc}"
        ) from exc
    return artifact, hashlib.sha256(raw_bytes).hexdigest()


def _context_file_is_usable(path: Path, *, expected_candidate_id: str) -> bool:
    """5C-SAFETY-2 seccion 2: reutiliza el contrato Pydantic EXISTENTE
    `ContextPackage` (`contracts/context_package.py`) en vez de un
    chequeo barato de solo-parseo (5C-SAFETY seccion 13, ahora
    endurecido) -- un `07-context/<hash>.json` corrupto, o que no valida
    contra `ContextPackage`, o cuyo `candidate.candidate_id` no coincide
    con el candidato productivo esperado (archivo mal nombrado/
    intercambiado) NUNCA es "contexto presente"; las tres fallas son la
    MISMA falla de integridad que un archivo ausente."""
    if not path.is_file():
        return False
    try:
        package = ContextPackage.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return package.candidate.candidate_id == expected_candidate_id


def _compute_artifact_chain_integrity(
    run_dir: Path, artifact: CandidateArtifact | None
) -> ArtifactChainIntegrityReport:
    """Seccion 18: invariante barata -- todo candidato productivo debe
    tener un `ContextPackage` LEGIBLE, valido y correctamente enlazado en
    `artifacts/07-context/` cuando ese directorio existe (el run alcanzo
    CONTEXTS_BUILT o posterior). Si el directorio no existe (run que
    nunca llego tan lejos), no se puede afirmar nada sobre integridad --
    se reporta `candidates_checked=0`, nunca se fabrican fallas contra un
    artefacto que legitimamente no existe todavia."""
    if artifact is None:
        return ArtifactChainIntegrityReport(candidates_checked=0, candidates_missing_context=[])
    context_dir = run_dir.joinpath(*_CONTEXT_DIR_RELATIVE)
    if not context_dir.is_dir():
        return ArtifactChainIntegrityReport(candidates_checked=0, candidates_missing_context=[])
    missing = sorted(
        candidate.candidate_id
        for candidate in artifact.candidates
        if not _context_file_is_usable(
            context_dir / _artifact_filename(candidate.candidate_id),
            expected_candidate_id=candidate.candidate_id,
        )
    )
    return ArtifactChainIntegrityReport(
        candidates_checked=len(artifact.candidates), candidates_missing_context=missing
    )


def _compute_final_rule_linkage(
    run_dir: Path,
    state: RunState,
    artifact: CandidateArtifact | None,
    guardrail_by_candidate_id: Mapping[str, GuardrailLookupEntry],
) -> FinalRuleLinkageReport:
    """Secciones 19-21: perfil-aware, deterministico, sin LLM.
    `NOT_APPLICABLE` para cualquier run que no alcanzo COMPLETED (perfil
    DETERMINISTIC_OFFLINE, final esperado = CONTEXTS_BUILT sin
    10-rules/ -- nunca una falla). Para runs COMPLETED (5C-SAFETY-2
    seccion 3/5): `rules-manifest.json` AUSENTE o presente pero
    invalido/corrupto/no valida contra `RulesDirectoryManifest` (el
    contrato tipado EXISTENTE de `contracts/rules_manifest.py`, que ya
    garantiza sin duplicados y `relative_filename` relativo/sin escape
    via `RelativePath` -- nunca reimplementado aqui) es una falla de
    integridad explicita (`rules_rendered_stage.py` SIEMPRE escribe este
    artefacto, incluso vacio, en cuanto COMPLETED). `broken_candidate_ids`
    son `records[].candidate_id` que no corresponden a NINGUN candidato
    real de 06; `guardrail_rejected_candidate_ids` son candidatos de 06
    cuyo guardrail real tiene verdict=REJECTED -- la ausencia legitima de
    regla final para ellos NUNCA se confunde con una falla (Seccion 21).
    `missing_final_rule_candidate_ids` cubre DOS causas -- candidato
    aprobado (EVIDENCE_VALIDATED) ausente del manifest (5C-SAFETY seccion
    8), o presente en el manifest pero cuyo `.md` referenciado no existe
    en disco (5C-SAFETY-2 seccion 3) -- ambas son la MISMA falla desde la
    perspectiva de qualification: "este candidato no tiene una regla
    final disponible"."""
    if state.current_stage != PipelineStage.COMPLETED:
        return FinalRuleLinkageReport(status=FinalRuleLinkageStatus.NOT_APPLICABLE)

    valid_candidate_ids = {c.candidate_id for c in artifact.candidates} if artifact else set()
    manifest_path = run_dir.joinpath(*_RULES_MANIFEST_RELATIVE)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise FunctionalValidationError(
            "run COMPLETED sin artifacts/10-rules/rules-manifest.json "
            "(se espera siempre para un run FULL_LLM COMPLETED)"
        )
    try:
        manifest = RulesDirectoryManifest.model_validate_json(
            manifest_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise FunctionalValidationError(
            f"artifacts/10-rules/rules-manifest.json invalido/corrupto: {exc}"
        ) from exc

    rejected = sorted(
        candidate_id
        for candidate_id in valid_candidate_ids
        if (entry := guardrail_by_candidate_id.get(candidate_id)) is not None
        and entry.verdict == "REJECTED"
    )
    broken: list[str] = []
    missing_final_rule: list[str] = []
    manifest_candidate_ids: set[str] = set()
    for record in manifest.records:
        manifest_candidate_ids.add(record.candidate_id)
        if record.candidate_id not in valid_candidate_ids:
            broken.append(record.candidate_id)
        elif (
            record.candidate_id not in rejected
            and not (manifest_path.parent / record.relative_filename).is_file()
        ):
            missing_final_rule.append(record.candidate_id)
    # Candidato aprobado (EVIDENCE_VALIDATED) que ni siquiera aparece en
    # el manifest -- distinto del caso anterior (aparece pero el .md fue
    # borrado/nunca escrito).
    missing_final_rule.extend(
        candidate_id
        for candidate_id in valid_candidate_ids
        if candidate_id not in manifest_candidate_ids
        and (entry := guardrail_by_candidate_id.get(candidate_id)) is not None
        and entry.verdict == "EVIDENCE_VALIDATED"
    )
    return FinalRuleLinkageReport(
        status=FinalRuleLinkageStatus.APPLICABLE,
        rules_manifest_rule_count=manifest.rule_count,
        broken_candidate_ids=sorted(set(broken)),
        duplicate_candidate_ids=[],
        guardrail_rejected_candidate_ids=rejected,
        missing_final_rule_candidate_ids=sorted(set(missing_final_rule)),
    )


def load_ground_truth_set(path: Path) -> FunctionalGroundTruthSet:
    """Carga y valida `path` (tipicamente `config/ground_truth/synthetic_
    engineering.yaml`) contra `FunctionalGroundTruthSet`. Ausente/mal
    formado/no valida siempre levanta `FunctionalValidationError` (nunca
    `SemanticConfigError` sin traducir)."""
    try:
        document, _config_hash = read_yaml_config(path)
    except SemanticConfigError as exc:
        raise FunctionalValidationError(f"{path.name}: {exc}") from exc
    try:
        return FunctionalGroundTruthSet.model_validate(document)
    except ValueError as exc:
        raise FunctionalValidationError(
            f"{path.name}: no valida contra FunctionalGroundTruthSet: {exc}"
        ) from exc


def compute_functional_validation_report_from_promotion_assessment(
    run_dir: Path, run_id: str, ground_truth_path: Path
) -> FunctionalValidationReport:
    """Modo SHADOW/development (Fase 15B4-CANDIDATE-QUALITY-5C,
    `ValidationSource.PROMOTION_ASSESSMENT_SHADOW`): localiza `run_dir`,
    calcula `CandidatePromotionAssessmentArtifact` (Fase 9 -- V1 real
    desde `06-candidates.json`, V2/interprocedural RECALCULADOS en
    memoria, ignorando `enhanced_candidates_enabled`) y lo compara
    contra `ground_truth_path`. Mide capacidad de deteccion del codigo
    actual, NUNCA lo que el run realmente promovio a un artefacto
    productivo -- NUNCA usar como release/product qualification gate
    (ver `compute_functional_validation_report`, el default productivo).
    Nunca escribe nada."""
    ground_truth = load_ground_truth_set(ground_truth_path)

    try:
        assessment: CandidatePromotionAssessmentArtifact = (
            compute_candidate_promotion_assessment_artifact(run_dir, run_id)
        )
    except CandidatePromotionAssessmentError as exc:
        raise FunctionalValidationError(
            f"no se pudo calcular CandidatePromotionAssessmentArtifact: {exc}"
        ) from exc

    return validate_ground_truth(
        ground_truth,
        assessment.candidate_references,
        run_id=run_id,
        source_package_hash=assessment.source_package_hash,
        run_fixture_hashes=_compute_run_fixture_hashes(run_dir),
        guardrail_by_candidate_id=_load_guardrail_lookup(run_dir),
        validation_source=ValidationSource.PROMOTION_ASSESSMENT_SHADOW,
    )


def compute_functional_validation_report(
    run_dir: Path, run_id: str, ground_truth_path: Path
) -> FunctionalValidationReport:
    """Modo PRODUCTIVO (Fase 15B4-CANDIDATE-QUALITY-5C, cierre de
    P1-PRODUCTIVE-ARTIFACT-QUALIFICATION, `ValidationSource.
    PRODUCTIVE_ARTIFACT`): default de `functional-validate`/release
    readiness. Lee UNICAMENTE `artifacts/06-candidates.json` tal como el
    run lo persistio -- responde "que produjo realmente este run", NUNCA
    "que podria producir el codigo actual". No invoca
    `compute_candidate_promotion_assessment_artifact` ni ningun detector
    V2/interprocedural (ver `tests/pipeline/
    test_functional_validation_service.py::
    test_productive_report_never_triggers_recomputation`, sentinel
    obligatorio). Nunca escribe nada -- la persistencia es
    responsabilidad de `write_functional_validation_report`/el comando
    CLI."""
    ground_truth = load_ground_truth_set(ground_truth_path)
    state = _load_run_state(run_dir)

    artifact, source_artifact_hash = _load_productive_candidate_artifact(run_dir, state)
    candidate_references = adapt_productive_candidates(
        artifact, source_artifact_hash=source_artifact_hash or ""
    )
    source_package_hash = (
        artifact.source_package_hash if artifact is not None else state.source_package_hash
    )
    if source_package_hash is None:
        raise FunctionalValidationError(
            "no se pudo determinar source_package_hash: run.json no lo registra y "
            "06-candidates.json no esta disponible"
        )

    guardrail_by_candidate_id = _load_guardrail_lookup(run_dir)
    return validate_ground_truth(
        ground_truth,
        candidate_references,
        run_id=run_id,
        source_package_hash=source_package_hash,
        run_fixture_hashes=_compute_run_fixture_hashes(run_dir),
        guardrail_by_candidate_id=guardrail_by_candidate_id,
        validation_source=ValidationSource.PRODUCTIVE_ARTIFACT,
        artifact_chain_integrity=_compute_artifact_chain_integrity(run_dir, artifact),
        final_rule_linkage=_compute_final_rule_linkage(
            run_dir, state, artifact, guardrail_by_candidate_id
        ),
    )


def functional_validation_report_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_functional_validation_report(run_dir: Path, report: FunctionalValidationReport) -> Path:
    """Persiste `report` de forma atomica en `diagnostics/functional-
    validation-report.json` (mismo mecanismo que el resto de diagnosticos
    Fase 15B2-A: temporal-hermano + flush + fsync + replace)."""
    report_path = functional_validation_report_path(run_dir)
    atomic_write_json(report_path, report)
    return report_path
