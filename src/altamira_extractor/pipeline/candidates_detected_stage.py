"""Orquestacion de la etapa CANDIDATES_DETECTED:
GRAPH_VALIDATED -> artifacts/06-candidates.json (Prompt 10a).

`CandidateDetector` es de solo lectura sobre un grafo ya cargado y
validado: nunca repara drift, nunca vuelve a ejecutar
`invariants.cypher`/`GraphInvariantValidator` ni recarga Neo4j. La
deteccion de drift reutiliza directamente las mismas piezas de Prompt 9
(`load_and_validate_semantic_graph`, `Neo4jRepository.read_active_graph_load`/
`compute_drift`) en vez de invocar `run_graph_validated_stage` como
mecanismo de validacion — invocar esa etapa completa reejecutaria
`invariants.cypher`, que esta explicitamente prohibido aqui.

Idempotencia: en cada corrida se reejecutan precondiciones y Q0 por
completo (nunca se usa unicamente `q0_query_hash`/`RunState` para omitir
la ejecucion contra Neo4j); el artefacto recomputado solo se reescribe
si difiere del existente.

`Settings.enhanced_candidates_enabled` (Fase 15B3-B, opt-in, `False` por
defecto): cuando esta activo, ademas de Q0 se ejecutan en memoria los
detectores V2 RETURN_CODE_RULE/LEVEL_88_RETURN_CODE_RULE (ver
`enhanced_candidate_integration.py`) sobre los mismos artefactos que
PARSED/SEMANTIC_GRAPH_BUILT ya persistieron -- nunca se agrega una
consulta Neo4j adicional. Los candidatos V1 nunca se modifican; solo se
agregan los candidatos V2 no cubiertos ya por un `decision_id` de V1.
Con el default `False`, el artefacto producido es identico al de antes
de esta fase.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..config import Settings
from ..contracts.candidate import CandidateArtifact, RuleCandidate
from ..contracts.canonical import CanonicalProgram
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.invariants import InvariantArtifact
from ..contracts.run_state import StageExecution
from ..contracts.semantic_enrichment import SemanticEnrichmentArtifact
from .artifact_store import atomic_write_json
from .candidate_detector import detect_candidates, load_q0_query
from .enhanced_candidate_integration import (
    detect_enhanced_candidates,
    suppress_superseded_v1_return_code_ghosts,
)
from .errors import CandidateDetectionError, GraphLoadError, Neo4jError, SemanticConfigError
from .neo4j_repository import Neo4jRepository
from .semantic_graph_load_stage import load_and_validate_semantic_graph
from .semantic_tagger import load_semantic_tags_config

_REQUIRED_RETURN_CODE_TAG = "return_code"


def load_and_validate_candidate_artifact(candidates_path: Path) -> tuple[CandidateArtifact, str]:
    """Relee `06-candidates.json`, calcula su SHA-256 real y lo valida
    contra `CandidateArtifact`. Reutilizado por `contexts_built_stage.py`
    (misma verificacion, misma fuente unica de verdad)."""
    if not candidates_path.is_file():
        raise CandidateDetectionError(f"no se encontro {candidates_path.name}")
    raw_bytes = candidates_path.read_bytes()
    candidate_artifact_hash = hashlib.sha256(raw_bytes).hexdigest()
    try:
        artifact = CandidateArtifact.model_validate_json(raw_bytes.decode("utf-8"))
    except ValueError as exc:
        raise CandidateDetectionError(
            f"{candidates_path.name} no valida contra CandidateArtifact: {exc}"
        ) from exc
    return artifact, candidate_artifact_hash


def _verify_graph_validated_precondition(stages: list[StageExecution]) -> None:
    matches = [stage for stage in stages if stage.stage == PipelineStage.GRAPH_VALIDATED]
    if len(matches) != 1:
        raise CandidateDetectionError(
            "GRAPH_VALIDATED debe tener exactamente una StageExecution en RunState; "
            f"se encontraron {len(matches)}"
        )
    if matches[0].status != StageStatus.SUCCEEDED:
        raise CandidateDetectionError(
            f"GRAPH_VALIDATED no esta SUCCEEDED (status={matches[0].status.value}); no se "
            "pueden detectar candidatos sobre un grafo no validado"
        )


def _read_invariant_artifact(invariants_path: Path) -> InvariantArtifact:
    if not invariants_path.is_file():
        raise CandidateDetectionError(f"no se encontro {invariants_path.name}")
    try:
        return InvariantArtifact.model_validate_json(invariants_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CandidateDetectionError(
            f"{invariants_path.name} no valida contra InvariantArtifact: {exc}"
        ) from exc


def _read_semantic_tags_config_hash(semantic_enrichment_path: Path) -> str:
    if not semantic_enrichment_path.is_file():
        raise CandidateDetectionError(f"no se encontro {semantic_enrichment_path.name}")
    try:
        artifact = SemanticEnrichmentArtifact.model_validate_json(
            semantic_enrichment_path.read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise CandidateDetectionError(
            f"{semantic_enrichment_path.name} no valida contra SemanticEnrichmentArtifact: {exc}"
        ) from exc
    return artifact.semantic_tags_config_hash


def _verify_semantic_tags_config(settings: Settings, expected_config_hash: str) -> None:
    """Confirma que `config/semantic-tags.yml` no cambio desde
    SEMANTIC_ENRICHMENT_BUILT y que sigue admitiendo `return_code` (el
    tag que Q0 exige como literal). No reejecuta SemanticTagger: solo
    reutiliza el mismo loader de configuracion de Prompt 7."""
    try:
        loaded = load_semantic_tags_config(settings.semantic_tags_path)
    except SemanticConfigError as exc:
        raise CandidateDetectionError(
            f"{settings.semantic_tags_path.name} invalido: {exc}"
        ) from exc
    if loaded.config_hash != expected_config_hash:
        raise CandidateDetectionError(
            f"{settings.semantic_tags_path.name} cambio desde SEMANTIC_ENRICHMENT_BUILT: el "
            "hash actual no coincide con semantic_tags_config_hash de "
            "03b-semantic-enrichment.json"
        )
    if _REQUIRED_RETURN_CODE_TAG not in loaded.allowed_tags:
        raise CandidateDetectionError(
            f"{settings.semantic_tags_path.name} ya no admite el tag "
            f"{_REQUIRED_RETURN_CODE_TAG!r} que Q0 requiere"
        )


def _load_canonical_programs_for_enhanced_detection(canonical_dir: Path) -> list[CanonicalProgram]:
    """Carga `artifacts/02-canonical/*.json` unicamente para la deteccion
    ampliada opt-in (Fase 15B3-B) -- V1/Q0 nunca depende de esto. Mismo
    patron ya usado por `v2_shadow_candidates_service.py`/
    `interprocedural_call_linkage_service.py` (glob ordenado +
    `CanonicalProgram.model_validate_json`); CANDIDATES_DETECTED ya exige
    GRAPH_VALIDATED (y por lo tanto PARSED) SUCCEEDED, asi que estos
    artefactos estan garantizados presentes."""
    json_paths = sorted(
        path for path in canonical_dir.rglob("*.json") if path.is_file() and not path.is_symlink()
    )
    if not json_paths:
        raise CandidateDetectionError(
            "enhanced_candidates_enabled=true pero artifacts/02-canonical no contiene ningun "
            "CanonicalProgram"
        )
    programs: list[CanonicalProgram] = []
    for json_path in json_paths:
        try:
            programs.append(
                CanonicalProgram.model_validate_json(json_path.read_text(encoding="utf-8"))
            )
        except ValueError as exc:
            raise CandidateDetectionError(
                f"artifacts/02-canonical/{json_path.name}: JSON invalido o incompatible con "
                f"CanonicalProgram: {exc}"
            ) from exc
    return programs


def _build_candidate_artifact(
    *,
    run_id: str,
    source_package_hash: str,
    run_stages: list[StageExecution],
    semantic_graph_path: Path,
    semantic_enrichment_path: Path,
    invariants_path: Path,
    q0_cypher_path: Path,
    canonical_dir: Path,
    settings: Settings,
) -> CandidateArtifact:
    _verify_graph_validated_precondition(run_stages)

    try:
        graph, semantic_graph_hash = load_and_validate_semantic_graph(semantic_graph_path)
    except GraphLoadError as exc:
        raise CandidateDetectionError(
            f"04-semantic-graph.json ya no se puede releer/validar: {exc}"
        ) from exc

    invariant_artifact = _read_invariant_artifact(invariants_path)
    if not invariant_artifact.graph_validated or invariant_artifact.error_count != 0:
        raise CandidateDetectionError(
            "05-invariants.json no tiene graph_validated=true / error_count=0"
        )
    if invariant_artifact.source_package_hash != source_package_hash:
        raise CandidateDetectionError("source_package_hash no coincide con 05-invariants.json")
    if invariant_artifact.semantic_graph_hash != semantic_graph_hash:
        raise CandidateDetectionError(
            "semantic_graph_hash no coincide con 05-invariants.json (el artefacto cambio "
            "despues de validar)"
        )

    semantic_tags_config_hash = _read_semantic_tags_config_hash(semantic_enrichment_path)
    _verify_semantic_tags_config(settings, semantic_tags_config_hash)

    q0_query_text, q0_query_hash = load_q0_query(q0_cypher_path)

    repository: Neo4jRepository | None = None
    try:
        repository = Neo4jRepository.connect(settings)
        repository.verify_connectivity()

        active = repository.read_active_graph_load()
        if active is None:
            raise CandidateDetectionError("no existe un nodo AltamiraGraphLoad activo en Neo4j")
        if active.semantic_graph_hash != semantic_graph_hash:
            raise CandidateDetectionError(
                "semantic_graph_hash no coincide con la carga activa de Neo4j"
            )
        if active.source_package_hash != source_package_hash:
            raise CandidateDetectionError("source_package_hash no coincide con la carga activa")
        if active.node_count != len(graph.nodes):
            raise CandidateDetectionError("node_count no coincide con la carga activa")
        if active.relationship_count != len(graph.relationships):
            raise CandidateDetectionError("relationship_count no coincide con la carga activa")

        drift = repository.compute_drift(graph)
        if not drift.is_clean:
            raise CandidateDetectionError(
                "drift detectado entre 04-semantic-graph.json y el estado real de Neo4j "
                f"(faltantes: {len(drift.missing_ids)} nodos/{len(drift.missing_edge_keys)} "
                f"relaciones; inesperados: {len(drift.extra_ids)} nodos/"
                f"{len(drift.extra_edge_keys)} relaciones)"
            )

        candidates: list[RuleCandidate] = detect_candidates(
            repository, q0_cypher_text=q0_query_text, package_hash=source_package_hash
        )
    except Neo4jError as exc:
        raise CandidateDetectionError(str(exc)) from exc
    finally:
        if repository is not None:
            repository.close()

    warnings: list[str] = []
    if settings.enhanced_candidates_enabled:
        v1_artifact = CandidateArtifact(
            run_id=run_id,
            source_package_hash=source_package_hash,
            semantic_graph_hash=semantic_graph_hash,
            invariants_query_hash=invariant_artifact.invariants_query_hash,
            q0_query_hash=q0_query_hash,
            candidates=candidates,
            warnings=[],
        )
        canonical_programs = _load_canonical_programs_for_enhanced_detection(canonical_dir)
        enhanced_candidates, enhanced_warnings = detect_enhanced_candidates(
            canonical_programs=canonical_programs,
            semantic_graph=graph,
            v1_candidates=v1_artifact,
            run_id=run_id,
            source_package_hash=source_package_hash,
        )
        # enhanced_candidates puede contener reemplazos (mismo candidate_id
        # que un V1 original, con evidence_ids fusionados -- regla D de
        # enhanced_candidate_integration.py) o candidatos nuevos: indexar
        # por candidate_id y sobrescribir, nunca concatenar (evita
        # candidate_id duplicado en CandidateArtifact).
        by_id = {candidate.candidate_id: candidate for candidate in candidates}
        for candidate in enhanced_candidates:
            by_id[candidate.candidate_id] = candidate
        candidates = sorted(by_id.values(), key=lambda c: c.candidate_id)
        # Fase 15B4-CANDIDATE-QUALITY-3B: post-procesamiento estrecho,
        # posterior a la fusion V1/V2 de arriba -- omite unicamente un V1
        # RETURN_CODE "ghost" (sin outcome/evidencia propia) cuando algun
        # V2_RETURN_CODE_PROPAGATION deterministico ya lo superseded sobre
        # la misma decision (ver docstring de la funcion).
        candidates, ghost_warnings = suppress_superseded_v1_return_code_ghosts(candidates)
        warnings = sorted(set(enhanced_warnings) | set(ghost_warnings))

    return CandidateArtifact(
        run_id=run_id,
        source_package_hash=source_package_hash,
        semantic_graph_hash=semantic_graph_hash,
        invariants_query_hash=invariant_artifact.invariants_query_hash,
        q0_query_hash=q0_query_hash,
        candidates=candidates,
        warnings=warnings,
    )


def run_candidates_detected_stage(
    *,
    run_id: str,
    source_package_hash: str,
    run_stages: list[StageExecution],
    semantic_graph_path: Path,
    semantic_enrichment_path: Path,
    invariants_path: Path,
    q0_cypher_path: Path,
    candidates_path: Path,
    canonical_dir: Path,
    settings: Settings,
) -> list[str]:
    """Ejecuta CANDIDATES_DETECTED y devuelve los warnings (resumen para
    RunState; el detalle completo vive en `candidates_path`)."""
    artifact = _build_candidate_artifact(
        run_id=run_id,
        source_package_hash=source_package_hash,
        run_stages=run_stages,
        semantic_graph_path=semantic_graph_path,
        semantic_enrichment_path=semantic_enrichment_path,
        invariants_path=invariants_path,
        q0_cypher_path=q0_cypher_path,
        canonical_dir=canonical_dir,
        settings=settings,
    )

    if candidates_path.is_file():
        try:
            existing = CandidateArtifact.model_validate_json(
                candidates_path.read_text(encoding="utf-8")
            )
        except ValueError:
            existing = None
        if existing == artifact:
            return [f"detectados {len(artifact.candidates)} candidato(s) (sin cambios)"]

    atomic_write_json(candidates_path, artifact)
    return [f"detectados {len(artifact.candidates)} candidato(s)"]
