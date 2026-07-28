"""Ayudas de presentacion (modernizacion UI): mapea valores contractuales
(enum values de `contracts/enums.py`) a etiquetas en espanol y deriva
texto secundario puramente visual desde datos ya expuestos.

Nunca cambia un valor interno: `label()` siempre recibe y muestra tambien
el valor original en algun lugar del template (identificadores tecnicos
como informacion secundaria, nunca ocultos). Si un valor no esta en el
mapa, se devuelve tal cual -- nunca revienta ante un enum nuevo que este
modulo todavia no conozca."""

from __future__ import annotations

from pathlib import PurePosixPath

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


def program_name_from_source_file(source_file: str) -> str:
    """Deriva un nombre de programa legible desde `source_file` (p. ej.
    `01-codigo/cobol/CLEGAR01.cbl` -> `CLEGAR01`) para presentacion
    UNICAMENTE -- `RuleCandidate`/`ContextPackageScope` no tienen un
    campo `program` propio fuera de `ContextPackageScope.program`
    (candidates.html no tiene acceso a el, solo a `source_file`). Nunca
    se persiste ni se usa para logica: puramente derivado para mostrar
    una columna "Programa" en el listado de candidatos. Si el nombre no
    tiene la forma esperada, devuelve el `source_file` completo en vez
    de fallar."""
    if not source_file:
        return source_file
    return PurePosixPath(source_file).stem or source_file
