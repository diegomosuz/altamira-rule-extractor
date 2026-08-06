"""Tests de la reconciliacion ejecutable del manifiesto estatico de
cobertura semantica (Fase 15B2-A, Parte C):
`pipeline/semantic_coverage_registry.py`."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from altamira_extractor.contracts.enums import Severity
from altamira_extractor.contracts.semantic_coverage import (
    SemanticConstructCoverage,
    SemanticCoverageLayer,
    SemanticCoverageManifest,
    SemanticCoverageManifestSummary,
    SemanticCoverageStatus,
    SemanticDetectorCoverage,
    SemanticLayerCoverage,
    SemanticRuleFamilyCoverage,
)
from altamira_extractor.pipeline.errors import SemanticCoverageRegistryError
from altamira_extractor.pipeline.semantic_coverage_registry import (
    check_manifest_reconciled,
    known_detector_ids,
    known_rule_family_values,
    load_semantic_coverage_manifest,
    reconcile_manifest,
)

_ALL_LAYERS = list(SemanticCoverageLayer)


def _layers(status: SemanticCoverageStatus = SemanticCoverageStatus.NOT_APPLICABLE) -> list:
    return [SemanticLayerCoverage(layer=layer, status=status) for layer in _ALL_LAYERS]


def _construct_with_detector(detector_id: str, rule_family: str) -> SemanticConstructCoverage:
    return SemanticConstructCoverage(
        construct_id="IF",
        display_name="IF",
        category="CONTROL_FLOW",
        layers=_layers(),
        detectors=[SemanticDetectorCoverage(detector_id=detector_id, rule_families=[rule_family])],
        rule_families=[SemanticRuleFamilyCoverage(rule_family=rule_family, status="SUPPORTED")],
    )


def _manifest(constructs: list[SemanticConstructCoverage]) -> SemanticCoverageManifest:
    counts: dict[SemanticCoverageLayer, dict[SemanticCoverageStatus, int]] = {}
    for construct in constructs:
        for layer_entry in construct.layers:
            by_status = counts.setdefault(layer_entry.layer, {})
            by_status[layer_entry.status] = by_status.get(layer_entry.status, 0) + 1
    return SemanticCoverageManifest(
        manifest_edition="test-edition",
        constructs=constructs,
        issues=[],
        summary=SemanticCoverageManifestSummary(
            construct_count=len(constructs),
            domain_reviewed_count=0,
            issue_count=0,
            counts_by_layer_and_status=counts,
        ),
    )


def test_known_detector_ids_includes_v1() -> None:
    assert "q0-return-code-decision" in known_detector_ids()


def test_known_rule_family_values_includes_unknown() -> None:
    assert "UNKNOWN" in known_rule_family_values()


def test_reconcile_real_detector_id_produces_no_error_severity_issues() -> None:
    # Un manifiesto con un solo construct_id documenta deliberadamente solo
    # un detector real: los otros detectores reales (V2/interprocedural)
    # generan UNDOCUMENTED_DETECTOR (WARNING, esperado) -- lo que este test
    # verifica es que el detector_id REAL citado nunca se marca ERROR.
    manifest = _manifest([_construct_with_detector("q0-return-code-decision", "RETURN_CODE")])
    issues = reconcile_manifest(manifest)
    assert not any(issue.severity == Severity.ERROR for issue in issues)
    assert not any("q0-return-code-decision" in issue.issue_id for issue in issues)


def test_reconcile_unknown_detector_id_flagged_as_error() -> None:
    manifest = _manifest([_construct_with_detector("totally-invented-detector", "RETURN_CODE")])
    issues = reconcile_manifest(manifest)
    assert any(
        issue.reason_code == "UNKNOWN_DETECTOR_ID" and issue.severity == Severity.ERROR
        for issue in issues
    )


def test_reconcile_unknown_rule_family_flagged_as_error() -> None:
    manifest = _manifest([_construct_with_detector("q0-return-code-decision", "NOT_A_REAL_FAMILY")])
    issues = reconcile_manifest(manifest)
    assert any(issue.reason_code == "UNKNOWN_RULE_FAMILY" for issue in issues)


def test_reconcile_flags_undocumented_real_detectors() -> None:
    manifest = _manifest([])
    issues = reconcile_manifest(manifest)
    reason_codes = {issue.reason_code for issue in issues}
    assert "UNDOCUMENTED_DETECTOR" in reason_codes
    assert all(issue.severity == Severity.WARNING for issue in issues)


def test_reconcile_deterministic_across_calls() -> None:
    manifest = _manifest([_construct_with_detector("q0-return-code-decision", "RETURN_CODE")])
    first = reconcile_manifest(manifest)
    second = reconcile_manifest(manifest)
    assert first == second


def test_load_semantic_coverage_manifest_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SemanticCoverageRegistryError):
        load_semantic_coverage_manifest(tmp_path / "does_not_exist.yaml")


def test_load_semantic_coverage_manifest_invalid_yaml_raises(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text("manifest_edition: [unterminated", encoding="utf-8")
    with pytest.raises(SemanticCoverageRegistryError):
        load_semantic_coverage_manifest(path)


def test_load_semantic_coverage_manifest_valid_file_roundtrips(tmp_path: Path) -> None:
    manifest = _manifest([_construct_with_detector("q0-return-code-decision", "RETURN_CODE")])
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest.model_dump(mode="json")), encoding="utf-8")
    reloaded = load_semantic_coverage_manifest(path)
    assert reloaded.manifest_edition == "test-edition"


def test_check_manifest_reconciled_detects_drift_from_undocumented_detectors() -> None:
    # Manifiesto minimo de un solo construct_id: los detectores V2/
    # interprocedural reales quedan UNDOCUMENTED_DETECTOR -- drift real y
    # detectable, exactamente lo que este chequeo debe capturar.
    manifest = _manifest([_construct_with_detector("q0-return-code-decision", "RETURN_CODE")])
    with pytest.raises(SemanticCoverageRegistryError, match="desincronizado"):
        check_manifest_reconciled(manifest)


def test_real_semantic_coverage_yaml_is_fully_reconciled() -> None:
    from altamira_extractor.config import load_settings

    settings = load_settings()
    manifest = load_semantic_coverage_manifest(settings.semantic_coverage_manifest_path)
    check_manifest_reconciled(manifest)
