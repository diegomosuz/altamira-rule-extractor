"""Reconciliacion ejecutable de `config/semantic_coverage.yaml` (Fase
15B2-A, Parte C) contra los registries reales de detectores y contra
`UnifiedRuleFamily`.

`contracts/semantic_coverage.py` declara que `SemanticDetectorCoverage.
detector_id` debe coincidir con un id real de V1 (`candidate_detector.
DETECTOR_ID`), `V2_DETECTOR_REGISTRY` o `INTERPROCEDURAL_RULE_DETECTOR_
REGISTRY`, y que `SemanticRuleFamilyCoverage.rule_family` debe coincidir
con un valor real de `UnifiedRuleFamily` -- pero ninguno de esos campos
es un `Enum` (son `str` libres, ver el docstring de ambos modelos): un
`construct_id` con un id inventado o mal escrito solo se detecta
ejecutando este modulo, nunca en `model_validate()`. Este modulo es esa
verificacion, hecha explicita y sin discovery dinamico -- mismo patron
que `v2_detector_registry.py`/`interprocedural_rule_detector_registry.py`
(sin imports condicionales, sin plugins).

Deliberadamente NO reconcila `graph_nodes`/`graph_relationships`/
`java_statement_kind` (ya son `NodeLabel`/`RelationshipType`/
`StatementKind` tipados: Pydantic los rechaza en `model_validate()`, este
modulo seria codigo muerto para esos tres campos).

Distinto de `semantic_coverage_service.py` (Fase 1, diagnostico POR-RUN
sobre artefactos V1 de un run concreto): este modulo nunca lee un `run_id`
ni artefactos de ejecucion -- reconcilia el manifiesto ESTATICO contra el
codigo Python actualmente instalado, independiente de cualquier run."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..contracts.candidate_promotion_assessment import UnifiedRuleFamily
from ..contracts.enums import Severity
from ..contracts.semantic_coverage import (
    SemanticConstructCoverage,
    SemanticCoverageIssue,
    SemanticCoverageManifest,
)
from .candidate_detector import DETECTOR_ID as V1_DETECTOR_ID
from .errors import SemanticConfigError, SemanticCoverageRegistryError
from .interprocedural_rule_detector_registry import INTERPROCEDURAL_RULE_DETECTOR_REGISTRY
from .v2_detector_registry import V2_DETECTOR_REGISTRY
from .yaml_utils import read_yaml_config

REASON_UNKNOWN_DETECTOR_ID = "UNKNOWN_DETECTOR_ID"
REASON_UNDOCUMENTED_DETECTOR = "UNDOCUMENTED_DETECTOR"
REASON_UNKNOWN_RULE_FAMILY = "UNKNOWN_RULE_FAMILY"


def known_detector_ids() -> frozenset[str]:
    """Union de los tres unicos registries reales de detectores: V1
    (`candidate_detector.DETECTOR_ID`, un solo id), `V2_DETECTOR_REGISTRY`
    (Fase 5) e `INTERPROCEDURAL_RULE_DETECTOR_REGISTRY` (Fase 8). Sin
    discovery dinamico: cada fuente se importa explicitamente arriba."""
    return frozenset(
        {V1_DETECTOR_ID, *V2_DETECTOR_REGISTRY, *INTERPROCEDURAL_RULE_DETECTOR_REGISTRY}
    )


def known_rule_family_values() -> frozenset[str]:
    """Valores reales de `UnifiedRuleFamily`, incluyendo `UNKNOWN`
    (mapping deliberado de `V2RuleType.STATE_CHANGE_RULE`, ver
    `candidate_source_adapters.py`)."""
    return frozenset(member.value for member in UnifiedRuleFamily)


def reconcile_constructs(
    constructs: Sequence[SemanticConstructCoverage],
) -> list[SemanticCoverageIssue]:
    """Reconcilia `constructs` contra los registries reales. Determinístico:
    misma entrada produce exactamente los mismos issues, en el mismo orden
    (ordenados por `issue_id` al final, como exige `SemanticCoverageManifest.
    _check_issues_sorted_and_unique`)."""
    detector_ids = known_detector_ids()
    rule_families = known_rule_family_values()
    issues: list[SemanticCoverageIssue] = []
    referenced_detector_ids: set[str] = set()

    for construct in constructs:
        for detector in construct.detectors:
            referenced_detector_ids.add(detector.detector_id)
            if detector.detector_id not in detector_ids:
                issues.append(
                    SemanticCoverageIssue(
                        issue_id=(
                            f"{REASON_UNKNOWN_DETECTOR_ID}::{construct.construct_id}::"
                            f"{detector.detector_id}"
                        ),
                        construct_id=construct.construct_id,
                        severity=Severity.ERROR,
                        reason_code=REASON_UNKNOWN_DETECTOR_ID,
                        message=(
                            f"detector_id={detector.detector_id!r} declarado en "
                            f"construct_id={construct.construct_id!r} no existe en V1/"
                            "V2_DETECTOR_REGISTRY/INTERPROCEDURAL_RULE_DETECTOR_REGISTRY."
                        ),
                    )
                )
            for family in detector.rule_families:
                if family not in rule_families:
                    issues.append(
                        SemanticCoverageIssue(
                            issue_id=(
                                f"{REASON_UNKNOWN_RULE_FAMILY}::{construct.construct_id}::"
                                f"detector::{detector.detector_id}::{family}"
                            ),
                            construct_id=construct.construct_id,
                            severity=Severity.ERROR,
                            reason_code=REASON_UNKNOWN_RULE_FAMILY,
                            message=(
                                f"rule_family={family!r} declarada para detector_id="
                                f"{detector.detector_id!r} en construct_id="
                                f"{construct.construct_id!r} no es un valor real de "
                                "UnifiedRuleFamily."
                            ),
                        )
                    )
        for rule_family_coverage in construct.rule_families:
            if rule_family_coverage.rule_family not in rule_families:
                issues.append(
                    SemanticCoverageIssue(
                        issue_id=(
                            f"{REASON_UNKNOWN_RULE_FAMILY}::{construct.construct_id}::"
                            f"rule_families::{rule_family_coverage.rule_family}"
                        ),
                        construct_id=construct.construct_id,
                        severity=Severity.ERROR,
                        reason_code=REASON_UNKNOWN_RULE_FAMILY,
                        message=(
                            f"rule_family={rule_family_coverage.rule_family!r} declarada "
                            f"en construct_id={construct.construct_id!r} no es un valor "
                            "real de UnifiedRuleFamily."
                        ),
                    )
                )

    for detector_id in sorted(detector_ids - referenced_detector_ids):
        issues.append(
            SemanticCoverageIssue(
                issue_id=f"{REASON_UNDOCUMENTED_DETECTOR}::{detector_id}",
                construct_id=None,
                severity=Severity.WARNING,
                reason_code=REASON_UNDOCUMENTED_DETECTOR,
                message=(
                    f"detector_id={detector_id!r} existe en un registry real pero ningun "
                    "construct_id del manifiesto lo declara en su lista de detectors."
                ),
            )
        )

    return sorted(issues, key=lambda issue: issue.issue_id)


def reconcile_manifest(manifest: SemanticCoverageManifest) -> list[SemanticCoverageIssue]:
    """Atajo sobre `reconcile_constructs(manifest.constructs)`."""
    return reconcile_constructs(manifest.constructs)


def load_semantic_coverage_manifest(path: Path) -> SemanticCoverageManifest:
    """Carga y valida `path` (tipicamente `config/semantic_coverage.yaml`)
    contra `SemanticCoverageManifest`, sin ejecutar reconciliacion --
    para eso ver `reconcile_manifest`/`check_manifest_reconciled`. Ausente/
    mal formado/no valida siempre levanta `SemanticCoverageRegistryError`
    (nunca `SemanticConfigError` sin traducir)."""
    try:
        document, _config_hash = read_yaml_config(path)
    except SemanticConfigError as exc:
        raise SemanticCoverageRegistryError(f"{path.name}: {exc}") from exc
    try:
        return SemanticCoverageManifest.model_validate(document)
    except Exception as exc:  # noqa: BLE001 -- traducido a error de dominio
        raise SemanticCoverageRegistryError(
            f"{path.name}: no valida contra SemanticCoverageManifest: {exc}"
        ) from exc


def check_manifest_reconciled(manifest: SemanticCoverageManifest) -> None:
    """Levanta `SemanticCoverageRegistryError` si `manifest.issues` no
    coincide EXACTAMENTE (mismo conjunto de `issue_id`) con una
    reconciliacion fresca contra el codigo actualmente instalado -- indica
    que el YAML quedo desactualizado respecto de los registries reales
    (un detector renombrado/eliminado/agregado sin regenerar el
    manifiesto)."""
    expected = {issue.issue_id for issue in reconcile_manifest(manifest)}
    actual = {issue.issue_id for issue in manifest.issues}
    if expected != actual:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise SemanticCoverageRegistryError(
            "config/semantic_coverage.yaml desincronizado de los registries reales: "
            f"issues faltantes={missing!r} issues obsoletos={extra!r}"
        )
