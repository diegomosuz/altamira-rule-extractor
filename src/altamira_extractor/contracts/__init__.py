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
from .candidate import CandidateArtifact, RuleCandidate
from .canonical import (
    CanonicalCallArgument,
    CanonicalConditionName,
    CanonicalConditionValue,
    CanonicalDataItem,
    CanonicalEntryParameter,
    CanonicalLinkageDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalSqlAccess,
    CanonicalStatement,
)
from .context_manifest import ContextDirectoryManifest, ContextRecord, QueryRecord
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
from .dependencies import DependencyArtifact, DependencyEvidence, ParagraphDependency
from .enums import (
    ApplicabilityStatus,
    AttributionScope,
    BatchContextStatus,
    BranchKind,
    CallPassingMode,
    CallTargetKind,
    CandidateStatus,
    ClaimField,
    CompletenessStatus,
    DependencyEvidenceRole,
    DependencyType,
    EvidenceValidationStatus,
    FunctionalReviewStatus,
    GuardrailVerdict,
    InclusionReason,
    InventoryFileKind,
    LlmProvider,
    LocationKind,
    NodeLabel,
    ParseSupportStatus,
    PipelineStage,
    RelationshipType,
    Severity,
    SourceFormat,
    StageStatus,
    StatementKind,
    TableAccessOperation,
    TableEffectOperation,
    TextEncoding,
)
from .guardrail import GuardrailReport, GuardrailViolation
from .guardrail_candidate import GuardrailCandidateArtifact, RepairAttemptRecord
from .guardrail_manifest import GuardrailDirectoryManifest, GuardrailRecord
from .invariants import InvariantArtifact, InvariantViolation
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
from .rule_draft_manifest import RuleDraftDirectoryManifest, RuleDraftRecord
from .rules_manifest import RulesDirectoryManifest, RulesRecord
from .run_state import RunState, StageExecution
from .semantic_enrichment import (
    DataItemDomainTermMapping,
    DataItemSemanticTag,
    DomainTermRecord,
    ParameterColumnDefinition,
    ParameterEntryRecord,
    ParameterTableRecord,
    SemanticEnrichmentArtifact,
    SemanticTagRuleMatch,
)
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
    "BranchKind",
    "CallPassingMode",
    "CallTargetKind",
    "CandidateStatus",
    "ClaimField",
    "CompletenessStatus",
    "DependencyEvidenceRole",
    "DependencyType",
    "EvidenceValidationStatus",
    "FunctionalReviewStatus",
    "GuardrailVerdict",
    "InclusionReason",
    "InventoryFileKind",
    "LlmProvider",
    "LocationKind",
    "NodeLabel",
    "ParseSupportStatus",
    "PipelineStage",
    "RelationshipType",
    "Severity",
    "SourceFormat",
    "StageStatus",
    "StatementKind",
    "TableAccessOperation",
    "TableEffectOperation",
    "TextEncoding",
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
    "CanonicalCallArgument",
    "CanonicalConditionName",
    "CanonicalConditionValue",
    "CanonicalDataItem",
    "CanonicalEntryParameter",
    "CanonicalLinkageDataItem",
    "CanonicalParagraph",
    "CanonicalProgram",
    "CanonicalSqlAccess",
    "CanonicalStatement",
    # dependencies
    "DependencyArtifact",
    "DependencyEvidence",
    "ParagraphDependency",
    # semantic enrichment
    "DataItemDomainTermMapping",
    "DataItemSemanticTag",
    "DomainTermRecord",
    "ParameterColumnDefinition",
    "ParameterEntryRecord",
    "ParameterTableRecord",
    "SemanticEnrichmentArtifact",
    "SemanticTagRuleMatch",
    # semantic graph
    "GraphNode",
    "GraphRelationship",
    "SemanticGraph",
    # invariants
    "InvariantArtifact",
    "InvariantViolation",
    # candidate
    "CandidateArtifact",
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
    # context manifest
    "ContextDirectoryManifest",
    "ContextRecord",
    "QueryRecord",
    # rule draft
    "Claim",
    "RuleDraft",
    # rule draft manifest
    "RuleDraftDirectoryManifest",
    "RuleDraftRecord",
    # guardrail
    "GuardrailReport",
    "GuardrailViolation",
    # guardrail candidate wrapper
    "GuardrailCandidateArtifact",
    "RepairAttemptRecord",
    # guardrail manifest
    "GuardrailDirectoryManifest",
    "GuardrailRecord",
    # rules manifest
    "RulesDirectoryManifest",
    "RulesRecord",
]
