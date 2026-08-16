"""Ayudas de presentacion (modernizacion UI): mapea valores contractuales
(enum values de `contracts/enums.py`) a etiquetas en espanol y deriva
texto secundario puramente visual desde datos ya expuestos.

Nunca cambia un valor interno: `label()` siempre recibe y muestra tambien
el valor original en algun lugar del template (identificadores tecnicos
como informacion secundaria, nunca ocultos). Si un valor no esta en el
mapa, se devuelve tal cual -- nunca revienta ante un enum nuevo que este
modulo todavia no conozca."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from ..contracts.enums import PipelineStage, StageStatus
from ..contracts.run_state import RunState

_STATUS_LABELS: dict[str, str] = {
    # PipelineStage
    "RECEIVED": "Recibido",
    "VALIDATED": "Validado",
    "EXTRACTED": "Extraido",
    "INVENTORIED": "Inventariado",
    "PARSED": "Analizado",
    "DEPENDENCIES_BUILT": "Dependencias construidas",
    "SEMANTIC_ENRICHMENT_BUILT": "Enriquecimiento semantico",
    "SEMANTIC_GRAPH_BUILT": "Grafo semantico construido",
    "SEMANTIC_GRAPH_LOADED": "Grafo semantico cargado",
    "GRAPH_VALIDATED": "Grafo validado",
    "CANDIDATES_DETECTED": "Candidatos detectados",
    "CONTEXTS_BUILT": "Contextos construidos",
    "RULE_DRAFTS_GENERATED": "Borradores generados",
    "GUARDRAILS_APPLIED": "Guardrails aplicados",
    "COMPLETED": "Completado",
    "FAILED": "Fallido",
    # StageStatus
    "PENDING": "Pendiente",
    "RUNNING": "En ejecucion",
    "SUCCEEDED": "Completado",
    "SKIPPED": "Omitido",
    # Severity
    "WARNING": "Advertencia",
    "ERROR": "Error",
    # CompletenessStatus / BatchContextStatus
    "COMPLETE": "Completo",
    "PARTIAL": "Parcial",
    "NOT_AVAILABLE": "No disponible",
    # ApplicabilityStatus
    "EXACT": "Exacto",
    "UNRESOLVED": "No resuelto",
    "NOT_APPLICABLE": "No aplica",
    # AttributionScope
    "DIRECT": "Directo",
    "DEPENDENCY_SLICE": "Por dependencia",
    "PROGRAM_CONTEXT": "Contexto del programa",
    # TableEffectOperation
    "WRITES": "Escritura",
    "UPDATES": "Actualizacion",
    "INSERTS": "Insercion",
    # InclusionReason
    "CANDIDATE": "Candidato",
    "DATA_DEPENDENCY": "Dependencia de datos",
    "CONTROL_DEPENDENCY": "Dependencia de control",
    "BOTH": "Ambas",
    # EvidenceValidationStatus / GuardrailVerdict
    "EVIDENCE_VALIDATED": "Evidencia validada",
    "REJECTED": "Rechazado",
    # FunctionalReviewStatus
    "NEEDS_FUNCTIONAL_REVIEW": "Pendiente de validacion funcional",
    # CandidateStatus
    "DETECTED_CANDIDATE": "Candidato detectado",
}


def status_label(value: str) -> str:
    """Etiqueta en espanol para un valor de estado contractual. El valor
    original nunca se descarta -- los templates lo muestran ademas de
    esta etiqueta (ver `_icons.html`/`base.html` y cada template de
    detalle: la etiqueta es el texto primario del badge, el valor
    tecnico queda como texto secundario)."""
    return _STATUS_LABELS.get(value, value)


def omit_keys(data: dict[str, object], *keys: str) -> dict[str, object]:
    """Filtro Jinja: excluye claves de un dict ya serializado (p. ej.
    `source_package_hash` de un `model_dump()`) antes de volcarlo como
    JSON tecnico secundario en un panel colapsable. Nunca modifica el
    dict original."""
    return {key: value for key, value in data.items() if key not in keys}


# Fuente unica de verdad del orden/total de etapas (Fase v1.17.1,
# Feature 2/3): `PipelineStage` declara sus miembros en el MISMO orden en
# que `pipeline/runner.py::run_ingestion` los ejecuta (FAILED es un
# centinela terminal, nunca una etapa progresable, se excluye
# explicitamente). Nunca se duplica esta lista/conteo en un template.
_PROGRESSABLE_STAGES: tuple[PipelineStage, ...] = tuple(
    stage for stage in PipelineStage if stage != PipelineStage.FAILED
)

# Credito determinista para una etapa RUNNING dentro del progreso GLOBAL
# (Fase v1.17.1, Feature 2): ninguna etapa actual expone un contador
# interno en vivo (archivos/programas/candidatos procesados hasta ahora)
# sin instrumentar profundamente cada etapa -- fuera de alcance de una
# mejora de UI/UX. Un credito FIJO de 0.5 por la etapa en curso es
# deliberado: nunca depende del reloj/tiempo transcurrido (prohibido
# explicitamente), es 100% reproducible para el mismo RunState, y refleja
# honestamente "esta etapa arranco pero no termino" sin fingir precision
# que no existe.
_RUNNING_STAGE_CREDIT = 0.5


@dataclass(frozen=True)
class StageProgressView:
    """Progreso de presentacion de UNA etapa. `percent`: `None` significa
    "indeterminado" (RUNNING, sin contador interno verificable -- el
    template lo renderiza como una barra en curso, nunca un numero
    inventado); `0` para PENDING/SKIPPED/FAILED (FAILED no completo NINGUNA
    fraccion verificable de su propio trabajo); `100` unicamente para
    SUCCEEDED.

    `stale`: `True` UNICAMENTE cuando `status == RUNNING` pero
    `RunExecutor` (la fuente autoritativa de "en ejecucion ahora mismo",
    en memoria del proceso, ver `api/executor.py`) ya NO posee este
    run_id -- p. ej. el proceso se reinicio con una etapa persistida como
    RUNNING sin haber alcanzado SUCCEEDED/FAILED. `_mark_running`
    (`runner.py`) nunca actualiza `current_stage`, asi que esta entrada
    RUNNING obsoleta jamas hace avanzar el estado ni se confunde con
    exito -- este flag es EXCLUSIVAMENTE de presentacion: evita mostrar
    "en curso" (barra animada) cuando en realidad nada la esta
    procesando; el analista debe usar Reanudar para continuarla."""

    stage: str
    label: str
    status: str
    percent: int | None
    stale: bool = False


@dataclass(frozen=True)
class PipelineProgress:
    """Progreso de presentacion del pipeline completo de un run. Nunca se
    persiste: se deriva en cada lectura desde `RunState.stages` (fuente
    de verdad ya persistida por `runner.py`), nunca desde un timer ni un
    campo nuevo obligatorio -- por eso un `run.json` de v1.17.0 (sin
    ninguna nocion de RUNNING) sigue siendo perfectamente legible: sus
    etapas ausentes de `stages` se presentan como PENDING, exactamente
    igual que un run v1.17.1 recien creado antes de que su primera etapa
    reporte RUNNING."""

    overall_percent: int
    stages: tuple[StageProgressView, ...]


def compute_pipeline_progress(run: RunState, *, is_actively_owned: bool) -> PipelineProgress:
    """Deriva el progreso GLOBAL y POR ETAPA de un `RunState` real,
    exclusivamente a partir de `run.stages` (cada entrada ya refleja
    PENDING-por-ausencia/RUNNING/SUCCEEDED/FAILED, ver `runner.py::
    _mark_running`/`_mark_succeeded`/`_mark_failed`) y del orden real de
    `PipelineStage` -- nunca de tiempo transcurrido, nunca de un conteo
    duplicado. 100% unicamente cuando las 15 etapas progresables estan
    SUCCEEDED (equivalente a `current_stage == COMPLETED` con exito real,
    nunca por alcanzar FAILED ni por detenerse antes por configuracion de
    proveedor LLM ausente -- ese caso simplemente no suma sus etapas
    restantes, como cualquier otro fallo).

    `is_actively_owned` (keyword-only, obligatorio -- nunca un default
    que un llamador pueda olvidar): viene de `RunExecutor.is_active(run_id)`
    (la UNICA fuente autoritativa de "en ejecucion ahora mismo", en
    memoria del proceso, nunca persistida). Una etapa RUNNING persistida
    en `run.stages` (posible tras un reinicio del proceso a mitad de una
    etapa -- ver docstring de `StageProgressView.stale`) jamas se
    presenta como "en curso" salvo que el executor realmente la posea en
    este momento; el credito hacia `overall_percent` no cambia (el
    trabajo hasta ese punto sigue siendo real), solo la presentacion de
    ESA etapa puntual."""
    executions_by_stage = {execution.stage: execution for execution in run.stages}

    stage_views: list[StageProgressView] = []
    completed_units = 0.0
    for stage in _PROGRESSABLE_STAGES:
        execution = executions_by_stage.get(stage)
        status = execution.status if execution is not None else StageStatus.PENDING
        stale = False
        if status == StageStatus.SUCCEEDED:
            completed_units += 1.0
            percent: int | None = 100
        elif status == StageStatus.RUNNING:
            completed_units += _RUNNING_STAGE_CREDIT
            percent = None
            stale = not is_actively_owned
        else:
            # PENDING (incluye ausencia en `stages`), FAILED, SKIPPED:
            # ninguna fraccion verificable de trabajo propio completado.
            percent = 0
        stage_views.append(
            StageProgressView(
                stage=stage.value,
                label=status_label(stage.value),
                status=status.value,
                percent=percent,
                stale=stale,
            )
        )

    total_units = len(_PROGRESSABLE_STAGES)
    overall_percent = round(100 * completed_units / total_units) if total_units else 0
    overall_percent = max(0, min(100, overall_percent))
    return PipelineProgress(overall_percent=overall_percent, stages=tuple(stage_views))


def program_name_from_source_file(source_file: str | None) -> str:
    """Deriva un nombre de programa legible desde `source_file` (p. ej.
    `01-codigo/cobol/CLEGAR01.cbl` -> `CLEGAR01`) para presentacion
    UNICAMENTE -- `RuleCandidate`/`ContextPackageScope` no tienen un
    campo `program` propio fuera de `ContextPackageScope.program`
    (candidates.html no tiene acceso a el, solo a `source_file`). Nunca
    se persiste ni se usa para logica: puramente derivado para mostrar
    una columna "Programa" en el listado de candidatos. Si el nombre no
    tiene la forma esperada, devuelve el `source_file` completo en vez
    de fallar. `source_file=None` (Fase 15B4-CANDIDATE-QUALITY-5A,
    programas con COPY) se presenta explicitamente como "(origen no
    determinable)" -- nunca como el texto literal "None"."""
    if source_file is None:
        return "(origen no determinable)"
    if not source_file:
        return source_file
    return PurePosixPath(source_file).stem or source_file
