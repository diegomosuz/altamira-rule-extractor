"""Tests contractuales del manifiesto ESTATICO de cobertura semantica
(Fase 15B2-A, Parte A/B/J): `SemanticCoverageManifest` y sus modelos
anidados (`contracts/semantic_coverage.py`). Distinto de
`test_semantic_coverage.py` (Fase 1, `SemanticCoverageReport` por-run)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.enums import Severity, StatementKind
from altamira_extractor.contracts.semantic_coverage import (
    SemanticConstructCoverage,
    SemanticCoverageEvidenceReference,
    SemanticCoverageIssue,
    SemanticCoverageLayer,
    SemanticCoverageManifest,
    SemanticCoverageManifestSummary,
    SemanticCoverageStatus,
    SemanticLayerCoverage,
    ValidationEvidenceKind,
)

_ALL_LAYERS = list(SemanticCoverageLayer)


def _evidence(**overrides: object) -> SemanticCoverageEvidenceReference:
    defaults: dict[str, object] = {
        "kind": ValidationEvidenceKind.UNIT_TEST,
        "reference": "tests/pipeline/test_x.py",
    }
    defaults.update(overrides)
    return SemanticCoverageEvidenceReference(**defaults)  # type: ignore[arg-type]


def _layers(
    *, status: SemanticCoverageStatus = SemanticCoverageStatus.SUPPORTED, with_evidence: bool = True
) -> list[SemanticLayerCoverage]:
    evidence = [_evidence()] if with_evidence else []
    return [
        SemanticLayerCoverage(layer=layer, status=status, evidence=evidence)
        for layer in _ALL_LAYERS
    ]


def _construct(**overrides: object) -> SemanticConstructCoverage:
    defaults: dict[str, object] = {
        "construct_id": "IF",
        "display_name": "IF",
        "category": "CONTROL_FLOW",
        "layers": _layers(),
        "java_statement_kind": StatementKind.IF,
    }
    defaults.update(overrides)
    return SemanticConstructCoverage(**defaults)  # type: ignore[arg-type]


def _manifest(**overrides: object) -> SemanticCoverageManifest:
    constructs = overrides.pop("constructs", [_construct()])
    issues = overrides.pop("issues", [])
    domain_reviewed_count = sum(1 for c in constructs if c.domain_reviewed)
    counts: dict[SemanticCoverageLayer, dict[SemanticCoverageStatus, int]] = {}
    for construct in constructs:
        for layer_entry in construct.layers:
            by_status = counts.setdefault(layer_entry.layer, {})
            by_status[layer_entry.status] = by_status.get(layer_entry.status, 0) + 1
    defaults: dict[str, object] = {
        "manifest_edition": "test-edition",
        "constructs": constructs,
        "issues": issues,
        "summary": SemanticCoverageManifestSummary(
            construct_count=len(constructs),
            domain_reviewed_count=domain_reviewed_count,
            issue_count=len(issues),
            counts_by_layer_and_status=counts,
        ),
    }
    defaults.update(overrides)
    return SemanticCoverageManifest(**defaults)  # type: ignore[arg-type]


def test_valid_construct_round_trips() -> None:
    construct = _construct()
    assert construct.construct_id == "IF"
    assert len(construct.layers) == 11


def test_supported_layer_without_evidence_rejected() -> None:
    with pytest.raises(ValidationError, match="evidencia"):
        SemanticLayerCoverage(
            layer=SemanticCoverageLayer.DETECTOR, status=SemanticCoverageStatus.SUPPORTED
        )


def test_construct_id_pattern_rejected() -> None:
    with pytest.raises(ValidationError, match="construct_id"):
        _construct(construct_id="if-lowercase")


def test_construct_missing_layer_rejected() -> None:
    incomplete = _layers()[:-1]
    with pytest.raises(ValidationError, match="faltan capas"):
        _construct(layers=incomplete)


def test_construct_duplicate_layer_rejected() -> None:
    layers = _layers()
    layers.append(layers[0])
    with pytest.raises(ValidationError, match="duplicados"):
        _construct(layers=layers)


def test_construct_layers_out_of_order_rejected() -> None:
    layers = _layers()
    layers[0], layers[1] = layers[1], layers[0]
    with pytest.raises(ValidationError, match="ordenado"):
        _construct(layers=layers)


def test_domain_reviewed_without_domain_review_evidence_rejected() -> None:
    with pytest.raises(ValidationError, match="DOMAIN_REVIEW"):
        _construct(domain_reviewed=True)


def test_domain_reviewed_with_domain_review_evidence_accepted() -> None:
    layers = _layers()
    layers[0] = SemanticLayerCoverage(
        layer=layers[0].layer,
        status=SemanticCoverageStatus.SUPPORTED,
        evidence=[_evidence(kind=ValidationEvidenceKind.DOMAIN_REVIEW, reference="docs/REVIEW.md")],
    )
    construct = _construct(layers=layers, domain_reviewed=True)
    assert construct.domain_reviewed is True


def test_detectors_out_of_order_rejected() -> None:
    with pytest.raises(ValidationError, match="detectors"):
        _construct(
            detectors=[
                {"detector_id": "z-detector", "rule_families": []},
                {"detector_id": "a-detector", "rule_families": []},
            ]
        )


def test_fixtures_duplicate_rejected() -> None:
    with pytest.raises(ValidationError, match="fixtures"):
        _construct(fixtures=["a.cbl", "a.cbl"])


def test_manifest_constructs_out_of_order_rejected() -> None:
    with pytest.raises(ValidationError, match="ordenado"):
        _manifest(
            constructs=[_construct(construct_id="Z_LAST"), _construct(construct_id="A_FIRST")]
        )


def test_manifest_summary_construct_count_mismatch_rejected() -> None:
    manifest = _manifest()
    bad_summary = manifest.summary.model_copy(update={"construct_count": 999})
    with pytest.raises(ValidationError, match="construct_count"):
        SemanticCoverageManifest(
            manifest_edition=manifest.manifest_edition,
            constructs=manifest.constructs,
            issues=manifest.issues,
            summary=bad_summary,
        )


def test_manifest_issue_count_matches_issues() -> None:
    issue = SemanticCoverageIssue(
        issue_id="ISSUE::1",
        construct_id="IF",
        severity=Severity.WARNING,
        reason_code="TEST_REASON",
        message="mensaje de prueba",
    )
    manifest = _manifest(issues=[issue])
    assert manifest.summary.issue_count == 1


def test_manifest_valid_round_trips_json() -> None:
    manifest = _manifest()
    raw = manifest.to_stable_json()
    reloaded = SemanticCoverageManifest.model_validate_json(raw)
    assert reloaded == manifest


def test_manifest_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SemanticCoverageManifest.model_validate(
            {
                "manifest_edition": "x",
                "constructs": [],
                "issues": [],
                "summary": {
                    "construct_count": 0,
                    "domain_reviewed_count": 0,
                    "issue_count": 0,
                    "counts_by_layer_and_status": {},
                },
                "unexpected_field": "boom",
            }
        )
