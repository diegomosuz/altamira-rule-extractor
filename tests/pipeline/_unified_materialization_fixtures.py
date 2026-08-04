"""Fixtures sinteticas compartidas para los tests de Fase 14B
(`feat/controlled-unified-materialization`). NO es un archivo de tests
(pytest lo ignora, no empieza con `test_`).

Reutiliza `activation_golden_path()` (Fase 14A,
`tests/pipeline/_unified_activation_fixtures.py`) -- el mismo
escenario CALLER10/MAIN/WS-COD-RETORNO/R001 -- y agrega el flujo
completo de evaluacion real (`UNIFIED_CANARY` con allowlist explicita)
mas helpers para escribir el run en disco y autorizaciones YAML."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from altamira_extractor.contracts.unified_activation_config import (
    UnifiedActivationConfig,
    UnifiedActivationMode,
    UnifiedCanarySelectionStrategy,
)
from altamira_extractor.contracts.unified_activation_config import (
    UnifiedFallbackPolicy as ConfigFallbackPolicy,
)
from altamira_extractor.contracts.unified_activation_evaluation import (
    UnifiedActivationEvaluationArtifact,
)
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.unified_activation_evaluator import evaluate_unified_activation

from ._unified_activation_fixtures import ActivationGoldenPath, activation_golden_path

CONFIG_HASH = "c" * 64


@dataclass(frozen=True)
class MaterializationFixture:
    gp: ActivationGoldenPath
    run_id: str
    source_package_hash: str
    evaluation: UnifiedActivationEvaluationArtifact
    approved_group_ids: list[str]


def build_materialization_fixture() -> MaterializationFixture:
    """Evaluacion real de Fase 14A en modo `UNIFIED_CANARY` con
    allowlist explicita del `source_package_hash` real -- produce
    `READY_FOR_UNIFIED_CANARY` de forma determinista."""
    gp = activation_golden_path()
    run_id = gp.unified_shadow.run_id
    source_hash = gp.unified_shadow.source_package_hash
    config = UnifiedActivationConfig(
        mode=UnifiedActivationMode.UNIFIED_CANARY,
        canary_strategy=UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST,
        package_hash_allowlist=[source_hash],
        fallback_policy=ConfigFallbackPolicy.FALLBACK_TO_V1,
    )
    evaluation = evaluate_unified_activation(
        config,
        run_id=run_id,
        source_package_hash=source_hash,
        config_hash=CONFIG_HASH,
        candidate_v1_artifact=gp.v1_artifact,
        candidate_v1_artifact_hash=source_hash,
        unified_shadow=gp.unified_shadow,
        unified_candidates_shadow_hash=source_hash,
        validation_report=gp.validation_report,
        validation_report_hash=source_hash,
        downstream_artifact=gp.downstream_artifact,
        downstream_artifact_hash=source_hash,
    )
    approved_group_ids = sorted(
        {
            r.group_id
            for r in evaluation.unified_references
            if r.level.value == "RULE" and r.guardrail_status.value == "PASSED"
        }
    )
    return MaterializationFixture(
        gp=gp,
        run_id=run_id,
        source_package_hash=source_hash,
        evaluation=evaluation,
        approved_group_ids=approved_group_ids,
    )


def write_run_dir(tmp_path: Path, fx: MaterializationFixture) -> Path:
    """Escribe `artifacts/06-candidates.json` + los `diagnostics/*.json`
    reales que el servicio de materializacion (Fase 14B Parte 12)
    necesita -- nunca fabrica un `run.json` (el servicio no lo exige)."""
    run_dir = tmp_path / fx.run_id
    (run_dir / "artifacts").mkdir(parents=True)
    atomic_write_json(run_dir / "artifacts" / "06-candidates.json", fx.gp.v1_artifact)
    atomic_write_json(run_dir / "diagnostics" / "unified-activation-evaluation.json", fx.evaluation)
    atomic_write_json(
        run_dir / "diagnostics" / "unified-shadow-downstream.json", fx.gp.downstream_artifact
    )
    return run_dir


def evaluation_hash_of(run_dir: Path) -> str:
    path = run_dir / "diagnostics" / "unified-activation-evaluation.json"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_authorization_yaml(
    path: Path,
    *,
    run_id: str,
    activation_evaluation_hash: str,
    action: str,
    expected_readiness_disposition: str,
    approved_group_ids: list[str] | None = None,
    reason_code: str = "CANARY_APPROVED",
    review_reference: str = "fixture-review-reference",
    fallback_authorized: bool = True,
    rollback_authorized: bool = False,
    target_generation_id: str | None = None,
    materialization_enabled: bool = True,
    provider_policy: str = "DETERMINISTIC_FAKE_ONLY",
) -> None:
    """Escribe un YAML de `UnifiedMaterializationAuthorization` valido
    (o deliberadamente invalido, via los parametros) EXTERNO en
    `tmp_path` -- nunca en el repositorio ni en el directorio del run,
    exactamente como el operador lo haria via `--authorization`."""
    approved = approved_group_ids or []
    lines = [
        'schema_version: "1.0"',
        f"run_id: {run_id!r}",
        f"activation_evaluation_hash: {activation_evaluation_hash!r}",
        f"expected_readiness_disposition: {expected_readiness_disposition}",
        f"action: {action}",
    ]
    if target_generation_id is not None:
        lines.append(f"target_generation_id: {target_generation_id!r}")
    lines.extend(
        [
            f"reason_code: {reason_code}",
            f"review_reference: {review_reference!r}",
            f"approved_group_ids: {approved!r}",
            f"fallback_authorized: {str(fallback_authorized).lower()}",
            f"rollback_authorized: {str(rollback_authorized).lower()}",
            f"provider_policy: {provider_policy}",
            f"materialization_enabled: {str(materialization_enabled).lower()}",
            "diagnostics: []",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
