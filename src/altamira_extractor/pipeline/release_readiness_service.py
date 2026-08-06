"""Servicio de filesystem de release readiness funcional (Fase 15B2-A,
Parte G). Carga `config/release_readiness_policy.yaml`, reconcilia el
manifiesto estatico de cobertura semantica (Parte C) y calcula el
`FunctionalValidationReport` de un run (Parte F), ejecuta el evaluador
puro (`release_readiness_evaluator.evaluate_release_readiness`) y
persiste el resultado en `<run_dir>/diagnostics/release-readiness-
assessment.json`.

`compute_release_readiness_assessment_for_dataset` (cierre de Fase
15B2-A, Seccion 4): variante multi-run -- agrega N `FunctionalValidation
Report` (`functional_dataset_validation_service.py`) y evalua el mismo
`ReleaseReadinessPolicy` contra el `FunctionalDatasetValidationReport`
resultante (`release_readiness_evaluator.evaluate_release_readiness_
for_dataset`).

NO es un `PipelineStage`: se invoca exclusivamente bajo demanda (CLI),
nunca desde `runner.py`/`run_ingestion`. Nunca modifica ningun artefacto
de entrada (decision arquitectonica #7)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..config import Settings
from ..contracts.functional_dataset_validation import FunctionalDatasetLane
from ..contracts.release_readiness import (
    DatasetReleaseReadinessAssessment,
    ReleaseReadinessAssessment,
    ReleaseReadinessPolicy,
)
from .artifact_store import atomic_write_json
from .errors import (
    FunctionalDatasetAggregationError,
    FunctionalValidationError,
    ReleaseReadinessError,
    SemanticConfigError,
    SemanticCoverageRegistryError,
)
from .functional_dataset_validation_service import compute_functional_dataset_validation_report
from .functional_validation_service import compute_functional_validation_report
from .release_readiness_evaluator import (
    evaluate_release_readiness,
    evaluate_release_readiness_for_dataset,
)
from .semantic_coverage_registry import load_semantic_coverage_manifest, reconcile_manifest
from .yaml_utils import read_yaml_config

_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REPORT_FILENAME = "release-readiness-assessment.json"
_DATASET_REPORT_FILENAME = "dataset-release-readiness-assessment.json"


def load_release_readiness_policy(path: Path) -> ReleaseReadinessPolicy:
    """Carga y valida `path` (tipicamente `config/release_readiness_
    policy.yaml`) contra `ReleaseReadinessPolicy`. Ausente/mal formado/no
    valida siempre levanta `ReleaseReadinessError` (nunca
    `SemanticConfigError` sin traducir)."""
    try:
        document, _config_hash = read_yaml_config(path)
    except SemanticConfigError as exc:
        raise ReleaseReadinessError(f"{path.name}: {exc}") from exc
    try:
        return ReleaseReadinessPolicy.model_validate(document)
    except ValueError as exc:
        raise ReleaseReadinessError(
            f"{path.name}: no valida contra ReleaseReadinessPolicy: {exc}"
        ) from exc


def compute_release_readiness_assessment(
    run_dir: Path,
    run_id: str,
    *,
    policy_path: Path,
    semantic_coverage_manifest_path: Path,
    ground_truth_path: Path,
) -> ReleaseReadinessAssessment:
    """Localiza `run_dir`, carga la politica y ambas senales (Parte
    C/Parte F), y devuelve el `ReleaseReadinessAssessment` calculado.
    Nunca escribe nada -- la persistencia es responsabilidad de
    `write_release_readiness_assessment`/el comando CLI."""
    policy = load_release_readiness_policy(policy_path)

    try:
        manifest = load_semantic_coverage_manifest(semantic_coverage_manifest_path)
    except SemanticCoverageRegistryError as exc:
        raise ReleaseReadinessError(f"manifiesto de cobertura semantica invalido: {exc}") from exc
    coverage_issues = reconcile_manifest(manifest)

    try:
        validation_report = compute_functional_validation_report(run_dir, run_id, ground_truth_path)
    except FunctionalValidationError as exc:
        raise ReleaseReadinessError(f"no se pudo calcular la validacion funcional: {exc}") from exc

    return evaluate_release_readiness(policy, coverage_issues, validation_report)


def release_readiness_assessment_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_release_readiness_assessment(
    run_dir: Path, assessment: ReleaseReadinessAssessment
) -> Path:
    """Persiste `assessment` de forma atomica en `diagnostics/release-
    readiness-assessment.json` (mismo mecanismo que el resto de
    diagnosticos Fase 15B2-A: temporal-hermano + flush + fsync +
    replace)."""
    assessment_path = release_readiness_assessment_path(run_dir)
    atomic_write_json(assessment_path, assessment)
    return assessment_path


def compute_release_readiness_assessment_for_dataset(
    settings: Settings,
    run_ids: Sequence[str],
    *,
    dataset_id: str,
    lane: FunctionalDatasetLane,
    policy_path: Path,
    semantic_coverage_manifest_path: Path,
) -> DatasetReleaseReadinessAssessment:
    """Variante multi-run (cierre de Fase 15B2-A, Seccion 4): agrega los
    `FunctionalValidationReport` de `run_ids` contra `dataset_id`/`lane`
    y evalua la misma `ReleaseReadinessPolicy` contra el agregado. Nunca
    escribe nada -- la persistencia es responsabilidad de
    `write_dataset_release_readiness_assessment`/el comando CLI."""
    policy = load_release_readiness_policy(policy_path)

    try:
        manifest = load_semantic_coverage_manifest(semantic_coverage_manifest_path)
    except SemanticCoverageRegistryError as exc:
        raise ReleaseReadinessError(f"manifiesto de cobertura semantica invalido: {exc}") from exc
    coverage_issues = reconcile_manifest(manifest)

    try:
        dataset_report = compute_functional_dataset_validation_report(
            settings, run_ids, dataset_id=dataset_id, lane=lane
        )
    except FunctionalDatasetAggregationError as exc:
        raise ReleaseReadinessError(f"no se pudo agregar el dataset: {exc}") from exc

    return evaluate_release_readiness_for_dataset(policy, coverage_issues, dataset_report)


def dataset_release_readiness_assessment_path(anchor_run_dir: Path) -> Path:
    return anchor_run_dir / _DIAGNOSTICS_DIR_NAME / _DATASET_REPORT_FILENAME


def write_dataset_release_readiness_assessment(
    anchor_run_dir: Path, assessment: DatasetReleaseReadinessAssessment
) -> Path:
    """Persiste `assessment` de forma atomica bajo `diagnostics/` del
    run ancla (primer `--run-id` de la invocacion) -- mismo mecanismo
    que el resto de diagnosticos Fase 15B2-A."""
    assessment_path = dataset_release_readiness_assessment_path(anchor_run_dir)
    atomic_write_json(assessment_path, assessment)
    return assessment_path
