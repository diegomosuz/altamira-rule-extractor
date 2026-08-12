"""Servicio de filesystem del catalogo unificado de candidatos y
evaluacion de promocion (Fase 9 de la ampliacion semantica,
`feat/unified-candidate-promotion-assessment`). Unico punto que localiza
un run, carga `CandidateArtifact` V1 si existe, DELEGA en los servicios
ya existentes de Fase 5 y Fase 8
(`v2_shadow_candidates_service.compute_v2_shadow_candidates_artifact`/
`interprocedural_rule_candidates_service.compute_interprocedural_rule_
candidates_artifact`) para obtener V2/interprocedural **en memoria**
-- NUNCA lee ni escribe `diagnostics/v2-candidates-shadow.json` ni
`diagnostics/interprocedural-rule-candidates-shadow.json`, ni siquiera
cuando ya existen en disco -- registra la disponibilidad REAL de cada
fuente, ejecuta el analizador puro y persiste el resultado UNICAMENTE en
`<run_dir>/diagnostics/candidate-promotion-assessment.json`.

NO es un `PipelineStage`: se invoca exclusivamente bajo demanda (CLI
`candidate-promotion-assessment`), nunca desde `runner.py`/
`run_ingestion`. Nunca modifica `run.json` ni ningun artefacto de
entrada (incluyendo `artifacts/06-candidates.json`, que solo se lee);
nunca escribe un reporte parcial; nunca usa Neo4j ni un proveedor LLM
directamente (aunque `compute_v2_shadow_candidates_artifact` internamente
pueda necesitar Neo4j via etapas previas del run, este servicio nunca lo
invoca el mismo).

La UNICA etapa exigida es PARSED (`SUCCEEDED`). Ausencia de
`artifacts/06-candidates.json` (V1), de `artifacts/04-semantic-graph.
json`/`artifacts/06-candidates.json` (prerequisitos de V2) NUNCA es un
error: se marca `SourceAvailability.NOT_AVAILABLE`, se agrega un
diagnostico explicito, y el analisis continua con las fuentes restantes.
Si una fuente SI tiene sus prerequisitos presentes pero el computo falla
(JSON invalido, contrato incompatible, excepcion inesperada), se marca
`SourceAvailability.INVALID` -- nunca se oculta el error, nunca se
fabrica un artefacto vacio en su lugar."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.candidate import CandidateArtifact
from ..contracts.candidate_promotion_assessment import (
    CandidatePromotionAssessmentArtifact,
    CandidateSource,
    SourceAvailability,
)
from ..contracts.enums import NodeLabel, PipelineStage, StageStatus
from ..contracts.interprocedural_rule_candidates import InterproceduralRuleCandidatesArtifact
from ..contracts.run_state import RunState
from ..contracts.semantic_graph import SemanticGraph
from ..contracts.v2_shadow_candidates import V2ShadowCandidatesArtifact
from .artifact_store import atomic_write_json
from .candidate_promotion_assessment_analyzer import analyze_candidate_promotion_assessment
from .errors import (
    CandidatePromotionAssessmentError,
    InterproceduralRuleCandidatesError,
    V2ShadowCandidatesError,
)
from .interprocedural_rule_candidates_service import (
    compute_interprocedural_rule_candidates_artifact,
)
from .v2_shadow_candidates_service import compute_v2_shadow_candidates_artifact

_CANDIDATES_FILENAME = "06-candidates.json"
_SEMANTIC_GRAPH_FILENAME = "04-semantic-graph.json"
_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REPORT_FILENAME = "candidate-promotion-assessment.json"

_REQUIRED_STAGES = (PipelineStage.PARSED,)

_V1_NOT_AVAILABLE_DIAGNOSTIC = "V1_CANDIDATES_UNAVAILABLE_ARTIFACTS_06_CANDIDATES_JSON_ABSENT"
_V1_INVALID_DIAGNOSTIC = "V1_CANDIDATES_INVALID_ARTIFACTS_06_CANDIDATES_JSON_MALFORMED"
_V2_NOT_AVAILABLE_DIAGNOSTIC = "V2_SHADOW_CANDIDATES_UNAVAILABLE_PREREQUISITES_ABSENT"
_V2_INVALID_DIAGNOSTIC = "V2_SHADOW_CANDIDATES_INVALID_COMPUTATION_FAILED"
_INTERPROCEDURAL_NOT_AVAILABLE_DIAGNOSTIC = (
    "INTERPROCEDURAL_RULE_CANDIDATES_UNAVAILABLE_PREREQUISITES_ABSENT"
)
_INTERPROCEDURAL_INVALID_DIAGNOSTIC = "INTERPROCEDURAL_RULE_CANDIDATES_INVALID_COMPUTATION_FAILED"


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_json_artifact(artifact: object) -> str:
    """Para fuentes calculadas en memoria (V2/interprocedural, nunca
    persistidas por este servicio): hash determinista sobre su propia
    serializacion JSON estable -- ambos artefactos ya garantizan bytes
    identicos para entradas identicas (Fase 5/Fase 8), asi que este hash
    es una procedencia real, nunca arbitraria."""
    dump = artifact.model_dump_json()  # type: ignore[attr-defined]
    return _hash_bytes(dump.encode("utf-8"))


def _load_run_state(run_dir: Path) -> RunState:
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise CandidatePromotionAssessmentError(
            f"run {run_dir.name!r} no encontrado: run.json ausente"
        )
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CandidatePromotionAssessmentError("run.json invalido") from exc


def _require_stages_succeeded(state: RunState) -> None:
    for required_stage in _REQUIRED_STAGES:
        execution = next((s for s in state.stages if s.stage == required_stage), None)
        if execution is None or execution.status != StageStatus.SUCCEEDED:
            raise CandidatePromotionAssessmentError(
                f"el run no alcanzo {required_stage.value} (SUCCEEDED); no se puede "
                "evaluar el catalogo unificado de candidatos todavia"
            )


def _load_v1_candidates(
    run_dir: Path,
) -> tuple[CandidateArtifact | None, SourceAvailability, str | None, str | None]:
    """Retorna (artifact, availability, source_artifact_hash, diagnostic)."""
    path = run_dir / "artifacts" / _CANDIDATES_FILENAME
    if path.is_symlink() or not path.is_file():
        return None, SourceAvailability.NOT_AVAILABLE, None, _V1_NOT_AVAILABLE_DIAGNOSTIC
    try:
        raw_bytes = path.read_bytes()
        artifact = CandidateArtifact.model_validate_json(raw_bytes.decode("utf-8"))
    except (OSError, ValueError):
        return None, SourceAvailability.INVALID, None, _V1_INVALID_DIAGNOSTIC
    return artifact, SourceAvailability.AVAILABLE, _hash_bytes(raw_bytes), None


def _index_semantic_tags_by_data_item(semantic_graph: SemanticGraph) -> dict[tuple[str, str], str]:
    """`(program_name, qualified_name) -> semantic_tag` para cada
    DataItem del grafo -- duplicacion MINIMA y deliberada de la seccion
    DataItem de `v2_detector_context._index_graph` (nunca importada
    directamente: ese modulo construye un `V2DetectorContext` completo,
    con `statements`/`SemanticEffects`/`SemanticPropagation`/V1 que este
    servicio no necesita solo para resolver un semantic_tag). Ver
    `tests/pipeline/test_candidate_promotion_assessment_service.py::
    test_index_semantic_tags_by_data_item_matches_v2_detector_context`
    para la prueba de equivalencia exigida entre ambos caminos. Nodos sin
    `qualified_name`/`semantic_tag` string validos se omiten, nunca
    fabrican una entrada con valor `None`."""
    program_node_id_by_name: dict[str, str] = {}
    for node in semantic_graph.nodes:
        if NodeLabel.PROGRAM in node.labels:
            name = node.properties.get("name")
            if isinstance(name, str):
                program_node_id_by_name[name] = node.id

    semantic_tag_by_data_item: dict[tuple[str, str], str] = {}
    for program_name, program_node_id in program_node_id_by_name.items():
        prefix = f"{program_node_id}::data::"
        for node in semantic_graph.nodes:
            if NodeLabel.DATA_ITEM not in node.labels or not node.id.startswith(prefix):
                continue
            qualified_name = node.properties.get("qualified_name")
            semantic_tag = node.properties.get("semantic_tag")
            if isinstance(qualified_name, str) and isinstance(semantic_tag, str):
                semantic_tag_by_data_item[(program_name, qualified_name)] = semantic_tag
    return semantic_tag_by_data_item


def _load_semantic_tag_by_data_item(run_dir: Path) -> dict[tuple[str, str], str]:
    """Tolerante a ausencia/invalidez (mismo principio que el resto de
    este servicio): `04-semantic-graph.json` ausente o invalido produce
    `{}`, nunca una excepcion -- el gate de `STATE_CHANGE_RULE` en
    `candidate_source_adapters.py` ya trata un mapping vacio igual que
    uno `None` (siempre `UNKNOWN`, nunca relevancia asumida)."""
    semantic_graph_path = run_dir / "artifacts" / _SEMANTIC_GRAPH_FILENAME
    if semantic_graph_path.is_symlink() or not semantic_graph_path.is_file():
        return {}
    try:
        semantic_graph = SemanticGraph.model_validate_json(
            semantic_graph_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    return _index_semantic_tags_by_data_item(semantic_graph)


def _load_v2_candidates(
    run_dir: Path, run_id: str
) -> tuple[V2ShadowCandidatesArtifact | None, SourceAvailability, str | None, str | None]:
    semantic_graph_path = run_dir / "artifacts" / _SEMANTIC_GRAPH_FILENAME
    candidates_path = run_dir / "artifacts" / _CANDIDATES_FILENAME
    prerequisites_present = (
        semantic_graph_path.is_file()
        and not semantic_graph_path.is_symlink()
        and candidates_path.is_file()
        and not candidates_path.is_symlink()
    )
    if not prerequisites_present:
        return None, SourceAvailability.NOT_AVAILABLE, None, _V2_NOT_AVAILABLE_DIAGNOSTIC
    try:
        artifact = compute_v2_shadow_candidates_artifact(run_dir, run_id)
    except (V2ShadowCandidatesError, ValueError, OSError):
        return None, SourceAvailability.INVALID, None, _V2_INVALID_DIAGNOSTIC
    return artifact, SourceAvailability.AVAILABLE, _hash_json_artifact(artifact), None


def _load_interprocedural_candidates(
    run_dir: Path, run_id: str
) -> tuple[
    InterproceduralRuleCandidatesArtifact | None, SourceAvailability, str | None, str | None
]:
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    if not canonical_dir.is_dir() or canonical_dir.is_symlink():
        return (
            None,
            SourceAvailability.NOT_AVAILABLE,
            None,
            _INTERPROCEDURAL_NOT_AVAILABLE_DIAGNOSTIC,
        )
    try:
        artifact = compute_interprocedural_rule_candidates_artifact(run_dir, run_id)
    except (InterproceduralRuleCandidatesError, ValueError, OSError):
        return None, SourceAvailability.INVALID, None, _INTERPROCEDURAL_INVALID_DIAGNOSTIC
    return artifact, SourceAvailability.AVAILABLE, _hash_json_artifact(artifact), None


def compute_candidate_promotion_assessment_artifact(
    run_dir: Path, run_id: str
) -> CandidatePromotionAssessmentArtifact:
    """Localiza `run_dir`, exige `PARSED` (`SUCCEEDED`), carga/deriva
    las tres fuentes (tolerante a la ausencia de cualquiera), ejecuta el
    analizador puro, y devuelve el `CandidatePromotionAssessmentArtifact`
    calculado. Nunca escribe nada -- la persistencia es responsabilidad
    de `write_candidate_promotion_assessment_artifact`/el comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise CandidatePromotionAssessmentError(f"run {run_dir.name!r} no encontrado")

    state = _load_run_state(run_dir)
    _require_stages_succeeded(state)

    v1_candidates, v1_availability, v1_hash, v1_diagnostic = _load_v1_candidates(run_dir)
    v2_candidates, v2_availability, v2_hash, v2_diagnostic = _load_v2_candidates(run_dir, run_id)
    semantic_tag_by_data_item = _load_semantic_tag_by_data_item(run_dir)
    (
        interprocedural_candidates,
        interprocedural_availability,
        interprocedural_hash,
        ip_diagnostic,
    ) = _load_interprocedural_candidates(run_dir, run_id)

    source_availability = {
        CandidateSource.V1: v1_availability,
        CandidateSource.V2: v2_availability,
        CandidateSource.INTERPROCEDURAL: interprocedural_availability,
    }
    source_artifact_hash_by_source: dict[CandidateSource, str] = {}
    if v1_hash is not None:
        source_artifact_hash_by_source[CandidateSource.V1] = v1_hash
    if v2_hash is not None:
        source_artifact_hash_by_source[CandidateSource.V2] = v2_hash
    if interprocedural_hash is not None:
        source_artifact_hash_by_source[CandidateSource.INTERPROCEDURAL] = interprocedural_hash

    source_package_hash = state.source_package_hash
    if source_package_hash is None:
        if v1_candidates is not None:
            source_package_hash = v1_candidates.source_package_hash
        elif v2_candidates is not None:
            source_package_hash = v2_candidates.source_package_hash
        elif interprocedural_candidates is not None:
            source_package_hash = interprocedural_candidates.source_package_hash
        else:
            raise CandidatePromotionAssessmentError(
                "no se pudo determinar source_package_hash: run.json no lo registra y "
                "ninguna fuente de candidatos esta disponible"
            )

    source_artifact_hashes: dict[str, str] = {}
    if v1_hash is not None:
        source_artifact_hashes[f"artifacts/{_CANDIDATES_FILENAME}"] = v1_hash
    if v2_hash is not None:
        source_artifact_hashes["v2-candidates-shadow(in-memory)"] = v2_hash
    if interprocedural_hash is not None:
        source_artifact_hashes["interprocedural-rule-candidates-shadow(in-memory)"] = (
            interprocedural_hash
        )

    try:
        artifact = analyze_candidate_promotion_assessment(
            v1_candidates=v1_candidates,
            v2_candidates=v2_candidates,
            interprocedural_candidates=interprocedural_candidates,
            source_availability=source_availability,
            source_artifact_hash_by_source=source_artifact_hash_by_source,
            run_id=run_id,
            source_package_hash=source_package_hash,
            source_artifact_hashes=source_artifact_hashes,
            semantic_tag_by_data_item=semantic_tag_by_data_item,
        )
    except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
        raise CandidatePromotionAssessmentError(
            "no se pudo calcular el catalogo unificado de candidatos: artefactos "
            "inconsistentes entre si"
        ) from exc

    extra_diagnostics = sorted(
        {
            diagnostic
            for diagnostic in (v1_diagnostic, v2_diagnostic, ip_diagnostic)
            if diagnostic is not None
        }
    )
    if extra_diagnostics:
        artifact = artifact.model_copy(
            update={"diagnostics": sorted({*artifact.diagnostics, *extra_diagnostics})}
        )
    return artifact


def candidate_promotion_assessment_artifact_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_candidate_promotion_assessment_artifact(
    run_dir: Path, artifact: CandidatePromotionAssessmentArtifact
) -> Path:
    """Persiste `artifact` de forma atomica en `diagnostics/
    candidate-promotion-assessment.json`. Nunca escribe un archivo
    parcial: `atomic_write_json` ya garantiza temporal-hermano + flush +
    fsync + replace."""
    report_path = candidate_promotion_assessment_artifact_path(run_dir)
    atomic_write_json(report_path, artifact)
    return report_path
