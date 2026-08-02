"""Servicio de filesystem de los detectores de reglas interprocedurales
en shadow mode (Fase 8 de la ampliacion semantica,
`feat/interprocedural-rule-detectors-shadow`). Unico punto que localiza
un run, carga y valida `artifacts/02-canonical/`, calcula
`SemanticEffectsArtifact`/`SemanticPropagationArtifact`/
`InterproceduralCallLinkageArtifact`/`InterproceduralPropagationArtifact`
EN MEMORIA (mismo patron que `interprocedural_propagation_service.py`:
nunca lee ni escribe esos diagnosticos intermedios, ni siquiera cuando ya
existen en disco), carga OPCIONALMENTE `artifacts/06-candidates.json`
(V1), `artifacts/04-semantic-graph.json` (unicamente para derivar V2 en
memoria, nunca persistido) y `artifacts/03b-semantic-enrichment.json`
(unicamente para `STATE_TRANSITION_RULE`), invoca
`interprocedural_rule_detector.analyze_interprocedural_rule_candidates`
(analizador puro) y persiste el resultado UNICAMENTE en `<run_dir>/
diagnostics/interprocedural-rule-candidates-shadow.json`.

NO es un `PipelineStage`: se invoca exclusivamente bajo demanda (CLI
`interprocedural-candidates-shadow`), nunca desde `runner.py`/
`run_ingestion`. Nunca modifica `run.json` ni ningun artefacto de
entrada (incluyendo `artifacts/06-candidates.json` y
`artifacts/04-semantic-graph.json`, que solo se leen); nunca escribe un
reporte parcial; nunca usa Neo4j ni un proveedor LLM.

La UNICA etapa exigida es PARSED (SUCCEEDED): `artifacts/06-candidates.
json` (V1), `artifacts/04-semantic-graph.json` (para V2) y
`artifacts/03b-semantic-enrichment.json` son todos OPCIONALES -- su
ausencia nunca es un error, simplemente reduce la comparacion V1/V2 y/o
deshabilita `STATE_TRANSITION_RULE` (ver `interprocedural_rule_detectors.
py::detect_state_transition_rule`, que devuelve `[]` cuando
`semantic_enrichment is None`)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.candidate import CandidateArtifact
from ..contracts.canonical import CanonicalProgram
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.interprocedural_rule_candidates import InterproceduralRuleCandidatesArtifact
from ..contracts.run_state import RunState
from ..contracts.semantic_enrichment import SemanticEnrichmentArtifact
from ..contracts.semantic_graph import SemanticGraph
from ..contracts.v2_shadow_candidates import V2ShadowCandidatesArtifact
from .artifact_store import atomic_write_json
from .errors import InterproceduralRuleCandidatesError
from .interprocedural_call_linkage_analyzer import analyze_interprocedural_call_linkage
from .interprocedural_propagation_analyzer import analyze_interprocedural_propagation
from .interprocedural_rule_detector import analyze_interprocedural_rule_candidates
from .semantic_effects_analyzer import analyze_semantic_effects
from .semantic_propagation_analyzer import analyze_semantic_propagation
from .v2_detector_context import build_v2_detector_context
from .v2_shadow_detector import run_v2_shadow_detection

_CANONICAL_DIR_NAME = "02-canonical"
_SEMANTIC_GRAPH_FILENAME = "04-semantic-graph.json"
_CANDIDATES_FILENAME = "06-candidates.json"
_SEMANTIC_ENRICHMENT_FILENAME = "03b-semantic-enrichment.json"
_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REPORT_FILENAME = "interprocedural-rule-candidates-shadow.json"

_REQUIRED_STAGES = (PipelineStage.PARSED,)


def _hash_file_bytes(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_directory(dir_path: Path) -> str:
    entries = sorted(
        (path.relative_to(dir_path).as_posix(), _hash_file_bytes(path))
        for path in dir_path.rglob("*.json")
        if path.is_file()
    )
    digest_source = "\n".join(
        f"{relative_path}:{file_hash}" for relative_path, file_hash in entries
    )
    return hashlib.sha256(digest_source.encode("utf-8")).hexdigest()


def _load_run_state(run_dir: Path) -> RunState:
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise InterproceduralRuleCandidatesError(
            f"run {run_dir.name!r} no encontrado: run.json ausente"
        )
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise InterproceduralRuleCandidatesError("run.json invalido") from exc


def _require_stages_succeeded(state: RunState) -> None:
    for required_stage in _REQUIRED_STAGES:
        execution = next((s for s in state.stages if s.stage == required_stage), None)
        if execution is None or execution.status != StageStatus.SUCCEEDED:
            raise InterproceduralRuleCandidatesError(
                f"el run no alcanzo {required_stage.value} (SUCCEEDED); no se pueden "
                "ejecutar los detectores de reglas interprocedurales todavia"
            )


def _require_directory(path: Path, *, artifact_label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise InterproceduralRuleCandidatesError(
            f"{artifact_label} ausente o no es un directorio regular"
        )
    return path


def _load_canonical_programs(canonical_dir: Path) -> list[CanonicalProgram]:
    _require_directory(canonical_dir, artifact_label=f"artifacts/{_CANONICAL_DIR_NAME}")
    json_paths = sorted(
        path for path in canonical_dir.rglob("*.json") if path.is_file() and not path.is_symlink()
    )
    if not json_paths:
        raise InterproceduralRuleCandidatesError(
            f"artifacts/{_CANONICAL_DIR_NAME} no contiene ningun artefacto CanonicalProgram"
        )
    programs: list[CanonicalProgram] = []
    for json_path in json_paths:
        relative_suffix = json_path.relative_to(canonical_dir).as_posix()
        relative_label = f"artifacts/{_CANONICAL_DIR_NAME}/{relative_suffix}"
        try:
            raw_text = json_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InterproceduralRuleCandidatesError(f"{relative_label}: fallo de lectura") from exc
        try:
            programs.append(CanonicalProgram.model_validate_json(raw_text))
        except ValueError as exc:
            raise InterproceduralRuleCandidatesError(
                f"{relative_label}: JSON invalido o incompatible con su contrato"
            ) from exc
    return programs


def _load_optional_v1_candidates(path: Path) -> CandidateArtifact | None:
    """Carga `artifacts/06-candidates.json` (V1) si existe. Su ausencia
    NUNCA es un error (unica etapa exigida: PARSED) -- solo reduce la
    comparacion V1 a `INTERPROCEDURAL_ONLY`/sin match posible."""
    if path.is_symlink() or not path.is_file():
        return None
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InterproceduralRuleCandidatesError(
            f"artifacts/{_CANDIDATES_FILENAME}: fallo de lectura"
        ) from exc
    try:
        return CandidateArtifact.model_validate_json(raw_text)
    except ValueError as exc:
        raise InterproceduralRuleCandidatesError(
            f"artifacts/{_CANDIDATES_FILENAME}: JSON invalido o incompatible con su contrato"
        ) from exc


def _load_optional_semantic_graph(path: Path) -> SemanticGraph | None:
    """Carga `artifacts/04-semantic-graph.json` unicamente para derivar
    `V2ShadowCandidatesArtifact` EN MEMORIA (nunca se persiste, nunca se
    consulta Neo4j). Su ausencia NUNCA es un error -- solo deshabilita la
    comparacion V2."""
    if path.is_symlink() or not path.is_file():
        return None
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InterproceduralRuleCandidatesError(
            f"artifacts/{_SEMANTIC_GRAPH_FILENAME}: fallo de lectura"
        ) from exc
    try:
        return SemanticGraph.model_validate_json(raw_text)
    except ValueError as exc:
        raise InterproceduralRuleCandidatesError(
            f"artifacts/{_SEMANTIC_GRAPH_FILENAME}: JSON invalido o incompatible con su contrato"
        ) from exc


def _load_optional_semantic_enrichment(path: Path) -> SemanticEnrichmentArtifact | None:
    """Carga `artifacts/03b-semantic-enrichment.json` desde disco (nunca
    se recalcula en memoria: requiere `ProgramIdentity`, ausente de
    `CanonicalProgram`). Su ausencia NUNCA es un error -- solo
    deshabilita `STATE_TRANSITION_RULE`."""
    if path.is_symlink() or not path.is_file():
        return None
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InterproceduralRuleCandidatesError(
            f"artifacts/{_SEMANTIC_ENRICHMENT_FILENAME}: fallo de lectura"
        ) from exc
    try:
        return SemanticEnrichmentArtifact.model_validate_json(raw_text)
    except ValueError as exc:
        raise InterproceduralRuleCandidatesError(
            f"artifacts/{_SEMANTIC_ENRICHMENT_FILENAME}: JSON invalido o incompatible con su "
            "contrato"
        ) from exc


def compute_interprocedural_rule_candidates_artifact(
    run_dir: Path, run_id: str
) -> InterproceduralRuleCandidatesArtifact:
    """Localiza `run_dir`, carga y valida `artifacts/02-canonical/`,
    calcula `SemanticEffectsArtifact`/`SemanticPropagationArtifact`/
    `InterproceduralCallLinkageArtifact`/`InterproceduralPropagationArtifact`
    en memoria (en ese orden), carga opcionalmente V1/V2/SemanticEnrichment,
    ejecuta los detectores de reglas interprocedurales puros, y devuelve
    el `InterproceduralRuleCandidatesArtifact` calculado. Nunca escribe
    nada -- la persistencia es responsabilidad de
    `write_interprocedural_rule_candidates_artifact`/el comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise InterproceduralRuleCandidatesError(f"run {run_dir.name!r} no encontrado")

    state = _load_run_state(run_dir)
    _require_stages_succeeded(state)

    canonical_dir = run_dir / "artifacts" / _CANONICAL_DIR_NAME
    canonical_programs = _load_canonical_programs(canonical_dir)

    source_package_hash = state.source_package_hash or canonical_programs[0].source_package_hash
    canonical_hash = _hash_directory(canonical_dir)
    source_artifact_hashes = {f"artifacts/{_CANONICAL_DIR_NAME}": canonical_hash}

    semantic_graph_path = run_dir / "artifacts" / _SEMANTIC_GRAPH_FILENAME
    semantic_graph = _load_optional_semantic_graph(semantic_graph_path)
    if semantic_graph is not None:
        source_artifact_hashes[f"artifacts/{_SEMANTIC_GRAPH_FILENAME}"] = _hash_file_bytes(
            semantic_graph_path
        )

    v1_candidates_path = run_dir / "artifacts" / _CANDIDATES_FILENAME
    v1_candidates = _load_optional_v1_candidates(v1_candidates_path)
    if v1_candidates is not None:
        source_artifact_hashes[f"artifacts/{_CANDIDATES_FILENAME}"] = _hash_file_bytes(
            v1_candidates_path
        )

    semantic_enrichment_path = run_dir / "artifacts" / _SEMANTIC_ENRICHMENT_FILENAME
    semantic_enrichment = _load_optional_semantic_enrichment(semantic_enrichment_path)
    if semantic_enrichment is not None:
        source_artifact_hashes[f"artifacts/{_SEMANTIC_ENRICHMENT_FILENAME}"] = _hash_file_bytes(
            semantic_enrichment_path
        )

    try:
        semantic_effects = analyze_semantic_effects(
            canonical_programs=canonical_programs,
            run_id=run_id,
            source_package_hash=source_package_hash,
            source_artifact_hashes={f"artifacts/{_CANONICAL_DIR_NAME}": canonical_hash},
        )
        semantic_propagation = analyze_semantic_propagation(
            canonical_programs=canonical_programs,
            semantic_effects=semantic_effects,
            run_id=run_id,
            source_package_hash=source_package_hash,
            source_artifact_hashes={f"artifacts/{_CANONICAL_DIR_NAME}": canonical_hash},
        )
        interprocedural_call_linkage = analyze_interprocedural_call_linkage(
            canonical_programs=canonical_programs,
            semantic_effects=semantic_effects,
            run_id=run_id,
            source_package_hash=source_package_hash,
            source_artifact_hashes={f"artifacts/{_CANONICAL_DIR_NAME}": canonical_hash},
        )
        interprocedural_propagation = analyze_interprocedural_propagation(
            canonical_programs=canonical_programs,
            semantic_effects=semantic_effects,
            semantic_propagation=semantic_propagation,
            interprocedural_call_linkage=interprocedural_call_linkage,
            run_id=run_id,
            source_package_hash=source_package_hash,
            source_artifact_hashes={f"artifacts/{_CANONICAL_DIR_NAME}": canonical_hash},
        )
    except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
        raise InterproceduralRuleCandidatesError(
            "no se pudo calcular la base interprocedural (Fase 6/7): artefactos "
            "inconsistentes entre si"
        ) from exc

    v2_candidates: V2ShadowCandidatesArtifact | None = None
    if semantic_graph is not None and v1_candidates is not None:
        try:
            v2_ctx = build_v2_detector_context(
                canonical_programs=canonical_programs,
                semantic_graph=semantic_graph,
                v1_candidates=v1_candidates,
                semantic_effects=semantic_effects,
                semantic_propagation=semantic_propagation,
            )
            v2_candidates = run_v2_shadow_detection(
                v2_ctx,
                run_id=run_id,
                source_package_hash=source_package_hash,
                source_artifact_hashes=source_artifact_hashes,
            )
        except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
            raise InterproceduralRuleCandidatesError(
                "no se pudo derivar V2 en memoria: SemanticGraph/CandidateArtifact "
                "inconsistentes entre si"
            ) from exc

    try:
        return analyze_interprocedural_rule_candidates(
            canonical_programs=canonical_programs,
            v1_candidates=v1_candidates,
            v2_candidates=v2_candidates,
            semantic_effects=semantic_effects,
            semantic_propagation=semantic_propagation,
            interprocedural_call_linkage=interprocedural_call_linkage,
            interprocedural_propagation=interprocedural_propagation,
            semantic_enrichment=semantic_enrichment,
            run_id=run_id,
            source_package_hash=source_package_hash,
            source_artifact_hashes=source_artifact_hashes,
        )
    except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
        raise InterproceduralRuleCandidatesError(
            "no se pudieron calcular los candidatos de reglas interprocedurales: "
            "artefactos inconsistentes entre si"
        ) from exc


def interprocedural_rule_candidates_artifact_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_interprocedural_rule_candidates_artifact(
    run_dir: Path, artifact: InterproceduralRuleCandidatesArtifact
) -> Path:
    """Persiste `artifact` de forma atomica en `diagnostics/
    interprocedural-rule-candidates-shadow.json`. Nunca escribe un
    archivo parcial: `atomic_write_json` ya garantiza temporal-hermano +
    flush + fsync + replace."""
    report_path = interprocedural_rule_candidates_artifact_path(run_dir)
    atomic_write_json(report_path, artifact)
    return report_path
