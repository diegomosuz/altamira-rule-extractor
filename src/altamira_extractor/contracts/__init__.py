"""Contratos tipados (Pydantic v2) del pipeline Altamira Rule Extractor.

Sin logica de pipeline: solo modelos, enums y validaciones de forma
(campos obligatorios, enums permitidos, coherencia entre campos).
"""

from __future__ import annotations

from .base import (
    AltamiraBaseModel,
    RelativePath,
    Sha256Hex,
    ensure_relative_path,
    ensure_sha256_hex,
)
from .candidate import RuleCandidate
from .canonical import (
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalSqlAccess,
)
from .context_package import (
    ApplicableParameterRow,
    BatchContext,
    CodeSliceEntry,
    Completeness,
    ContextPackage,
    ContextPackageCandidate,
    ContextPackageDecision,
    ContextPackageOperation,
    ContextPackageScope,
    ContextParameterRow,
    DataContext,
    DomainGlossaryEntry,
    Effects,
    EvidenceEntry,
    ParameterTableContext,
    ReturnCodeEffect,
    TableEffect,
    TransactionalTableRead,
)
from .dependencies import ParagraphDependency
from .enums import (
    ApplicabilityStatus,
    AttributionScope,
    BatchContextStatus,
    CandidateStatus,
    ClaimField,
    CompletenessStatus,
    DependencyType,
    EvidenceValidationStatus,
    FunctionalReviewStatus,
    GuardrailVerdict,
    InclusionReason,
    InventoryFileKind,
    NodeLabel,
    PipelineStage,
    RelationshipType,
    Severity,
    SourceFormat,
    StageStatus,
    TableAccessOperation,
    TableEffectOperation,
)
from .guardrail import GuardrailReport, GuardrailViolation
from .inventory import Inventory, InventoryFile
from .manifest import (
    Manifest,
    ManifestApplication,
    ManifestCountry,
    ManifestImplementation,
    ManifestOperation,
    ManifestParameterTable,
    ManifestSource,
)
from .rule_draft import Claim, RuleDraft
from .run_state import RunState, StageExecution
from .semantic_graph import GraphNode, GraphRelationship, SemanticGraph

__all__ = [
    "AltamiraBaseModel",
    "RelativePath",
    "Sha256Hex",
    "ensure_relative_path",
    "ensure_sha256_hex",
    # enums
    "ApplicabilityStatus",
    "AttributionScope",
    "BatchContextStatus",
    "CandidateStatus",
    "ClaimField",
    "CompletenessStatus",
    "DependencyType",
    "EvidenceValidationStatus",
    "FunctionalReviewStatus",
    "GuardrailVerdict",
    "InclusionReason",
    "InventoryFileKind",
    "NodeLabel",
    "PipelineStage",
    "RelationshipType",
    "Severity",
    "SourceFormat",
    "StageStatus",
    "TableAccessOperation",
    "TableEffectOperation",
    # manifest
    "Manifest",
    "ManifestApplication",
    "ManifestCountry",
    "ManifestImplementation",
    "ManifestOperation",
    "ManifestParameterTable",
    "ManifestSource",
    # inventory
    "Inventory",
    "InventoryFile",
    # run state
    "RunState",
    "StageExecution",
    # canonical
    "CanonicalDataItem",
    "CanonicalParagraph",
    "CanonicalProgram",
    "CanonicalSqlAccess",
    # dependencies
    "ParagraphDependency",
    # semantic graph
    "GraphNode",
    "GraphRelationship",
    "SemanticGraph",
    # candidate
    "RuleCandidate",
    # context package
    "ApplicableParameterRow",
    "BatchContext",
    "CodeSliceEntry",
    "Completeness",
    "ContextParameterRow",
    "ContextPackage",
    "ContextPackageCandidate",
    "ContextPackageDecision",
    "ContextPackageOperation",
    "ContextPackageScope",
    "DataContext",
    "DomainGlossaryEntry",
    "Effects",
    "EvidenceEntry",
    "ParameterTableContext",
    "ReturnCodeEffect",
    "TableEffect",
    "TransactionalTableRead",
    # rule draft
    "Claim",
    "RuleDraft",
    # guardrail
    "GuardrailReport",
    "GuardrailViolation",
]
