"""Servicio de filesystem de la ejecucion downstream del artefacto
unificado de candidatos en shadow mode (Fase 13 de la ampliacion
semantica, `feat/unified-shadow-downstream-pipeline`).

Localiza un run, exige PARSED (SUCCEEDED), y la existencia/validez de
AMBOS objetos PRINCIPALES: `diagnostics/unified-candidates-shadow.json`
(Fase 11) y `diagnostics/unified-shadow-validation-report.json` (Fase
12) -- ninguno es opcional, a diferencia de las fuentes SECUNDARIAS de
Fase 12. Carga tambien el canonico requerido
(`artifacts/04-semantic-graph.json`) y el schema de `RuleDraft`
(`schemas/rule-draft.schema.json`, via `settings.rule_draft_schema_path`
-- NUNCA leido de `.env`). Cualquier ausencia/invalidez de estas
fuentes, o una inconsistencia de hash/run_id entre ellas (ver
`unified_shadow_downstream_executor.py::_validate_sources`), es
SIEMPRE un error tecnico DURO: `UnifiedShadowDownstreamError`, exit
code distinto de cero en el CLI, sin traceback, sin ruta absoluta, sin
archivo parcial.

Inyecta EXPLICITAMENTE `DeterministicFakeDraftProvider` (Fase 13 Parte
7) -- el UNICO proveedor admitido por esta fase: nunca lee
configuracion de proveedor real, nunca inicializa OpenAI/PwC Gateway/
Ollama, nunca lee `.env`, nunca acepta una API key.

Persiste EXCLUSIVAMENTE en `<run_dir>/diagnostics/unified-shadow-
downstream.json` via `atomic_write_json` (mismo mecanismo atomico que
el resto del pipeline). Nunca modifica `run.json`, ningun `artifacts/`,
ni ningun otro `diagnostics/` preexistente -- en particular, nunca
regenera `unified-candidates-shadow.json` ni `unified-shadow-
validation-report.json` ausentes, nunca reemplaza `artifacts/06-
candidates.json`, nunca escribe un `ContextPackage`/`RuleDraft`/regla
productivos. No usa Neo4j.

Una disposicion de validacion REVIEW_REQUIRED/BLOCKED/NOT_EVALUATED
NUNCA lanza `UnifiedShadowDownstreamError` cuando las fuentes son
validas -- es una respuesta VALIDA
(`UnifiedShadowDownstreamDisposition.NOT_EXECUTED`), el artefacto se
genera y persiste igual (exit code 0 en el CLI)."""

from __future__ import annotations

from pathlib import Path

import jsonschema

from ..config import Settings
from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.run_state import RunState
from ..contracts.semantic_graph import SemanticGraph
from ..contracts.unified_candidates_shadow import UnifiedCandidatesShadowArtifact
from ..contracts.unified_shadow_downstream import UnifiedShadowDownstreamArtifact
from ..contracts.unified_shadow_validation import UnifiedShadowValidationReport
from .artifact_store import atomic_write_json
from .errors import UnifiedShadowDownstreamError
from .rule_draft_assembly import RuleDraftAssemblyError, load_rule_draft_schema
from .unified_shadow_downstream_executor import (
    UnifiedShadowDownstreamExecutorError,
    run_unified_shadow_downstream,
)
from .unified_shadow_draft_generator import DeterministicFakeDraftProvider

_DIAGNOSTICS_DIR_NAME = "diagnostics"
_SEMANTIC_GRAPH_FILENAME = "04-semantic-graph.json"
_UNIFIED_SHADOW_FILENAME = "unified-candidates-shadow.json"
_VALIDATION_REPORT_FILENAME = "unified-shadow-validation-report.json"
_DOWNSTREAM_FILENAME = "unified-shadow-downstream.json"

_REQUIRED_STAGES = (PipelineStage.PARSED,)


def _load_run_state(run_dir: Path) -> RunState:
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise UnifiedShadowDownstreamError(f"run {run_dir.name!r} no encontrado: run.json ausente")
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise UnifiedShadowDownstreamError("run.json invalido") from exc


def _require_stages_succeeded(state: RunState) -> None:
    for required_stage in _REQUIRED_STAGES:
        execution = next((s for s in state.stages if s.stage == required_stage), None)
        if execution is None or execution.status != StageStatus.SUCCEEDED:
            raise UnifiedShadowDownstreamError(
                f"el run no alcanzo {required_stage.value} (SUCCEEDED); no se puede "
                "ejecutar el downstream shadow todavia"
            )


def _load_unified_shadow(run_dir: Path) -> UnifiedCandidatesShadowArtifact:
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _UNIFIED_SHADOW_FILENAME
    if path.is_symlink() or not path.is_file():
        raise UnifiedShadowDownstreamError(
            "el artefacto unificado de candidatos (diagnostics/unified-candidates-shadow.json) "
            "no existe: ejecute primero 'unified-candidates-shadow' para este run"
        )
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise UnifiedShadowDownstreamError(
            "el artefacto unificado de candidatos no se pudo leer del filesystem"
        ) from exc
    try:
        return UnifiedCandidatesShadowArtifact.model_validate_json(raw_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UnifiedShadowDownstreamError(
            "el artefacto unificado de candidatos existente no es valido (JSON invalido, "
            "version incompatible o esquema incompatible) -- vuelva a ejecutar "
            "'unified-candidates-shadow'"
        ) from exc


def _load_validation_report(run_dir: Path) -> UnifiedShadowValidationReport:
    path = run_dir / _DIAGNOSTICS_DIR_NAME / _VALIDATION_REPORT_FILENAME
    if path.is_symlink() or not path.is_file():
        raise UnifiedShadowDownstreamError(
            "el reporte de validacion diferencial (diagnostics/unified-shadow-validation-"
            "report.json) no existe: ejecute primero 'unified-shadow-validate' para este run"
        )
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise UnifiedShadowDownstreamError(
            "el reporte de validacion diferencial no se pudo leer del filesystem"
        ) from exc
    try:
        return UnifiedShadowValidationReport.model_validate_json(raw_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UnifiedShadowDownstreamError(
            "el reporte de validacion diferencial existente no es valido (JSON invalido, "
            "version incompatible o esquema incompatible) -- vuelva a ejecutar "
            "'unified-shadow-validate'"
        ) from exc


def _load_semantic_graph(run_dir: Path) -> SemanticGraph:
    path = run_dir / "artifacts" / _SEMANTIC_GRAPH_FILENAME
    if path.is_symlink() or not path.is_file():
        raise UnifiedShadowDownstreamError(
            "el grafo semantico (artifacts/04-semantic-graph.json) no existe para este run"
        )
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise UnifiedShadowDownstreamError(
            "el grafo semantico no se pudo leer del filesystem"
        ) from exc
    try:
        return SemanticGraph.model_validate_json(raw_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise UnifiedShadowDownstreamError("el grafo semantico existente no es valido") from exc


def _load_schema_validator(settings: Settings) -> jsonschema.protocols.Validator:
    try:
        schema, _schema_hash = load_rule_draft_schema(settings.rule_draft_schema_path)
    except RuleDraftAssemblyError as exc:
        raise UnifiedShadowDownstreamError(
            "el schema de RuleDraft no se pudo cargar: no se puede generar el draft shadow"
        ) from exc
    validator_cls = jsonschema.validators.validator_for(schema)
    return validator_cls(schema)


def compute_unified_shadow_downstream_artifact(
    run_dir: Path, run_id: str, *, settings: Settings
) -> UnifiedShadowDownstreamArtifact:
    """Localiza `run_dir`, exige PARSED (SUCCEEDED) y la existencia/
    validez de AMBOS objetos principales -- las precondiciones DURAS --
    antes de invocar el ejecutor puro (Fase 13 Parte 9). Nunca escribe
    nada; la persistencia es responsabilidad de
    `write_unified_shadow_downstream_artifact`/el comando CLI."""
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise UnifiedShadowDownstreamError(f"run {run_dir.name!r} no encontrado")

    state = _load_run_state(run_dir)
    _require_stages_succeeded(state)

    unified_shadow = _load_unified_shadow(run_dir)
    validation_report = _load_validation_report(run_dir)
    semantic_graph = _load_semantic_graph(run_dir)
    schema_validator = _load_schema_validator(settings)
    provider = DeterministicFakeDraftProvider()

    try:
        return run_unified_shadow_downstream(
            run_id=run_id,
            unified_shadow=unified_shadow,
            validation_report=validation_report,
            semantic_graph=semantic_graph,
            provider=provider,
            schema_validator=schema_validator,
        )
    except UnifiedShadowDownstreamExecutorError as exc:
        raise UnifiedShadowDownstreamError(str(exc)) from exc


def unified_shadow_downstream_artifact_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _DOWNSTREAM_FILENAME


def write_unified_shadow_downstream_artifact(
    run_dir: Path, artifact: UnifiedShadowDownstreamArtifact
) -> Path:
    """Persiste `artifact` de forma atomica en `diagnostics/unified-
    shadow-downstream.json`. Nunca escribe un archivo parcial; nunca
    modifica ningun otro archivo del run."""
    artifact_path = unified_shadow_downstream_artifact_path(run_dir)
    atomic_write_json(artifact_path, artifact)
    return artifact_path
