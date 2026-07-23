"""Enums compartidos por los contratos tipados del pipeline.

Los valores replican exactamente CLAUDE.md y los JSON Schemas
existentes (schemas/*.json) — no son un diseno libre. Excepcion
documentada: `PipelineStage.SEMANTIC_ENRICHMENT_BUILT` no esta en la
lista original de CLAUDE.md (ese documento no reserva un estado para el
Prompt 7 del runbook); se agrega por decision explicita del usuario para
representar ParameterLoader/SemanticTagger/DomainTermMapper como una
etapa propia entre DEPENDENCIES_BUILT y SEMANTIC_GRAPH_BUILT.
"""

from __future__ import annotations

from enum import StrEnum


class PipelineStage(StrEnum):
    """Estados minimos del pipeline (CLAUDE.md, seccion Pipeline)."""

    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    EXTRACTED = "EXTRACTED"
    INVENTORIED = "INVENTORIED"
    PARSED = "PARSED"
    DEPENDENCIES_BUILT = "DEPENDENCIES_BUILT"
    SEMANTIC_ENRICHMENT_BUILT = "SEMANTIC_ENRICHMENT_BUILT"
    SEMANTIC_GRAPH_BUILT = "SEMANTIC_GRAPH_BUILT"
    SEMANTIC_GRAPH_LOADED = "SEMANTIC_GRAPH_LOADED"
    GRAPH_VALIDATED = "GRAPH_VALIDATED"
    CANDIDATES_DETECTED = "CANDIDATES_DETECTED"
    CONTEXTS_BUILT = "CONTEXTS_BUILT"
    RULE_DRAFTS_GENERATED = "RULE_DRAFTS_GENERATED"
    GUARDRAILS_APPLIED = "GUARDRAILS_APPLIED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StageStatus(StrEnum):
    """Estado de ejecucion de una etapa individual dentro de RunState."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class Severity(StrEnum):
    """Severidad de warnings/errores (invariantes, guardrail, etc.)."""

    WARNING = "WARNING"
    ERROR = "ERROR"


class NodeLabel(StrEnum):
    """Labels de nodo permitidos del metamodelo (docs/NEO4J_METAMODEL.md)."""

    COUNTRY = "Country"
    APPLICATION = "Application"
    OPERATION = "Operation"
    PROGRAM = "Program"
    PARAGRAPH = "Paragraph"
    DATA_ITEM = "DataItem"
    DECISION = "Decision"
    TABLE = "Table"
    PARAMETER_TABLE = "ParameterTable"
    PARAMETER_ENTRY = "ParameterEntry"
    BATCH_JOB = "BatchJob"
    DOMAIN_TERM = "DomainTerm"


class RelationshipType(StrEnum):
    """Relaciones permitidas del metamodelo, incluido el CPG reducido."""

    HAS_APPLICATION = "HAS_APPLICATION"
    HAS_OPERATION = "HAS_OPERATION"
    EXECUTES_VIA = "EXECUTES_VIA"
    CONTAINS = "CONTAINS"
    HAS_DECISION = "HAS_DECISION"
    LEADS_TO = "LEADS_TO"
    USES = "USES"
    READS = "READS"
    WRITES = "WRITES"
    UPDATES = "UPDATES"
    INSERTS = "INSERTS"
    HAS_ENTRY = "HAS_ENTRY"
    PRECEDED_BY = "PRECEDED_BY"
    TRIGGERS = "TRIGGERS"
    HAS_DOMAIN_TERM = "HAS_DOMAIN_TERM"
    DATA_DEPENDS_ON = "DATA_DEPENDS_ON"
    CONTROL_DEPENDS_ON = "CONTROL_DEPENDS_ON"


class CompletenessStatus(StrEnum):
    """Estado de completitud de cada dimension D1-D7 del ContextPackage."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    ERROR = "ERROR"


class BatchContextStatus(StrEnum):
    """Subconjunto de CompletenessStatus valido para batch_context.status."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NOT_AVAILABLE = "NOT_AVAILABLE"


class ApplicabilityStatus(StrEnum):
    """Estados de aplicabilidad parametrica (CLAUDE.md, seccion Parametria)."""

    EXACT = "EXACT"
    PARTIAL = "PARTIAL"
    UNRESOLVED = "UNRESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class AttributionScope(StrEnum):
    """Alcance de atribucion de un efecto de tabla (CLAUDE.md, seccion Efectos)."""

    DIRECT = "DIRECT"
    DEPENDENCY_SLICE = "DEPENDENCY_SLICE"
    PROGRAM_CONTEXT = "PROGRAM_CONTEXT"


class TableEffectOperation(StrEnum):
    """Operaciones de escritura clasificables como efecto (D5)."""

    WRITES = "WRITES"
    UPDATES = "UPDATES"
    INSERTS = "INSERTS"


class TableAccessOperation(StrEnum):
    """Operaciones de acceso a tabla en el artefacto canonico (incluye lectura)."""

    READS = "READS"
    WRITES = "WRITES"
    UPDATES = "UPDATES"
    INSERTS = "INSERTS"


class DependencyType(StrEnum):
    """Tipos de arista del CPG reducido Paragraph->Paragraph."""

    DATA_DEPENDS_ON = "DATA_DEPENDS_ON"
    CONTROL_DEPENDS_ON = "CONTROL_DEPENDS_ON"


class DependencyEvidenceRole(StrEnum):
    """Rol de un CanonicalStatement dentro de la evidencia de una
    ParagraphDependency (DEPENDENCIES_BUILT)."""

    WRITER = "WRITER"
    READER = "READER"
    CONTROL = "CONTROL"


class InclusionReason(StrEnum):
    """Motivo de inclusion de un paragraph en el code slice (D2)."""

    CANDIDATE = "CANDIDATE"
    DATA_DEPENDENCY = "DATA_DEPENDENCY"
    CONTROL_DEPENDENCY = "CONTROL_DEPENDENCY"
    BOTH = "BOTH"


class EvidenceValidationStatus(StrEnum):
    """Estado de validacion de evidencia de un RuleDraft (guardrail)."""

    PENDING = "PENDING"
    EVIDENCE_VALIDATED = "EVIDENCE_VALIDATED"
    REJECTED = "REJECTED"


class GuardrailVerdict(StrEnum):
    """Veredicto final (terminal) del guardrail determinístico."""

    EVIDENCE_VALIDATED = "EVIDENCE_VALIDATED"
    REJECTED = "REJECTED"


class FunctionalReviewStatus(StrEnum):
    """Estado de revision funcional. En V1 solo existe este valor: ninguna
    salida puede presentarse como regla aprobada (CLAUDE.md,
    'Candidato, fidelidad y aprobacion')."""

    NEEDS_FUNCTIONAL_REVIEW = "NEEDS_FUNCTIONAL_REVIEW"


class CandidateStatus(StrEnum):
    """Estado de un candidato estructural (Q0). Distinto de aprobacion funcional."""

    DETECTED_CANDIDATE = "DETECTED_CANDIDATE"


class ClaimField(StrEnum):
    """Campos de RuleDraft que pueden respaldarse con un claim de evidencia."""

    TITLE = "title"
    CONTEXT = "context"
    STATEMENT = "statement"
    CONDITION = "condition"
    PARAMETERS = "parameters"
    EFFECT = "effect"
    PARAMETER_SOURCE = "parameter_source"
    TRACEABILITY = "traceability"
    LIMITATIONS = "limitations"


class SourceFormat(StrEnum):
    """Formato de fuente COBOL declarado en manifest.xml (schemas/manifest.xsd)."""

    AUTO = "AUTO"
    FIXED = "FIXED"
    FREE = "FREE"
    TANDEM = "TANDEM"


class TextEncoding(StrEnum):
    """Encoding real detectado por archivo durante INVENTORIED.

    Distinto de `manifest.source.encoding` (valor declarado globalmente por
    el paquete, que puede ser `AUTO`): este es el resultado de una deteccion
    deterministica por archivo (ver `pipeline/encoding_detector.py`), nunca
    un valor inventado."""

    UTF_8 = "UTF-8"
    WINDOWS_1252 = "WINDOWS-1252"
    ISO_8859_1 = "ISO-8859-1"


class InventoryFileKind(StrEnum):
    """Clasificacion de cada archivo descubierto en el paquete (Inventory)."""

    COBOL = "COBOL"
    COPYBOOK = "COPYBOOK"
    DCLGEN = "DCLGEN"
    DDL = "DDL"
    SNAPSHOT = "SNAPSHOT"
    MANIFEST = "MANIFEST"
    OTHER = "OTHER"


class StatementKind(StrEnum):
    """Tipo de un CanonicalStatement dentro de un CanonicalParagraph."""

    IF = "IF"
    EVALUATE = "EVALUATE"
    MOVE = "MOVE"
    SET = "SET"
    COMPUTE = "COMPUTE"
    GO_TO = "GO_TO"
    PERFORM = "PERFORM"
    EXEC_SQL = "EXEC_SQL"
    OTHER = "OTHER"


class LocationKind(StrEnum):
    """Confiabilidad de la ubicacion fuente (source_file/line) de un elemento
    canonico. Distingue una ubicacion fisica real de una posicion dentro del
    stream ya expandido por COPY, cuya atribucion a un archivo fisico no
    puede demostrarse sin un preprocesador propio (fuera de alcance V1)."""

    EXACT = "EXACT"
    PREPROCESSED_STREAM = "PREPROCESSED_STREAM"
    UNKNOWN = "UNKNOWN"


class BranchKind(StrEnum):
    """Rama de control representada por un CanonicalStatement hijo
    (parent_statement_id) dentro de un IF o EVALUATE aplanado."""

    THEN = "THEN"
    ELSE = "ELSE"
    WHEN = "WHEN"
    WHEN_OTHER = "WHEN_OTHER"


class ParseSupportStatus(StrEnum):
    """Nivel de soporte al interpretar DDL/CSV de parametria
    (docs/SUPPORTED_COMPLEXITY_STRATEGY.md): SUPPORTED = evidencia completa;
    PARTIAL = evidencia parcial con warning; UNSUPPORTED = sin evidencia
    utilizable, nunca se inventa contenido."""

    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
