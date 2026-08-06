"""Servicio de filesystem del agregador MULTI-RUN de validacion
funcional (cierre de Fase 15B2-A, Seccion 2/3). Carga el dataset logico
por `dataset_id` (catalogo CERRADO -- nunca una ruta arbitraria provista
por el usuario), calcula el `FunctionalValidationReport` de cada
`run_id` (reutilizando `functional_validation_service.py`, nunca relee
un diagnostico persistido de otro run) y ejecuta el agregador puro
(`functional_validation_aggregator.aggregate_functional_validation_
reports`).

`dataset_id` NUNCA es una ruta: es una clave logica contra
`_DATASET_PATHS`, un catalogo cerrado (mismo principio que
`ReleaseReadinessCriterionKind`/`FunctionalDatasetLane` -- nada de rutas
ni globs libres). Hoy solo existe `synthetic_engineering`
(`config/ground_truth/synthetic_engineering.yaml`, via `settings.
ground_truth_path`); agregar un dataset nuevo exige extender este
diccionario, nunca aceptar `--dataset-path` desde la CLI.

Persistencia: `functional-dataset-validation-report.json` SIEMPRE bajo
`diagnostics/` del PRIMER `run_id` de la lista (el "ancla" de la
invocacion) -- nunca un directorio elegido por el usuario."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from ..api.validation import validate_run_id
from ..config import Settings
from ..contracts.functional_dataset_validation import (
    FunctionalDatasetLane,
    FunctionalDatasetValidationReport,
)
from .artifact_store import atomic_write_json
from .errors import FunctionalDatasetAggregationError
from .functional_validation_aggregator import aggregate_functional_validation_reports
from .functional_validation_service import (
    compute_functional_validation_report,
    load_ground_truth_set,
)

_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REPORT_FILENAME = "functional-dataset-validation-report.json"

_DATASET_PATHS: dict[str, Callable[[Settings], Path]] = {
    "synthetic_engineering": lambda settings: settings.ground_truth_path,
}


def resolve_dataset_path(settings: Settings, dataset_id: str) -> Path:
    """Traduce `dataset_id` (clave logica CERRADA) a una ruta real ya
    resuelta por `Settings` -- nunca acepta la ruta directamente."""
    resolver = _DATASET_PATHS.get(dataset_id)
    if resolver is None:
        raise FunctionalDatasetAggregationError(
            f"dataset_id desconocido: {dataset_id!r} (catalogo cerrado: "
            f"{sorted(_DATASET_PATHS)})"
        )
    return resolver(settings)


def compute_functional_dataset_validation_report(
    settings: Settings,
    run_ids: Sequence[str],
    *,
    dataset_id: str,
    lane: FunctionalDatasetLane,
) -> FunctionalDatasetValidationReport:
    """Localiza cada `run_id` (validado con `validate_run_id`, nunca una
    ruta arbitraria), calcula su `FunctionalValidationReport` real y los
    agrega. Nunca escribe nada -- la persistencia es responsabilidad de
    `write_functional_dataset_validation_report`/el comando CLI."""
    if not run_ids:
        raise FunctionalDatasetAggregationError(
            "se requiere al menos un --run-id para agregar el dataset"
        )
    ground_truth_path = resolve_dataset_path(settings, dataset_id)
    ground_truth = load_ground_truth_set(ground_truth_path)

    reports = []
    for run_id in run_ids:
        validate_run_id(run_id)
        run_dir = settings.runs_dir / run_id
        reports.append(compute_functional_validation_report(run_dir, run_id, ground_truth_path))

    return aggregate_functional_validation_reports(
        reports,
        ground_truth=ground_truth,
        dataset_id=dataset_id,
        lane=lane,
    )


def functional_dataset_validation_report_path(anchor_run_dir: Path) -> Path:
    return anchor_run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_functional_dataset_validation_report(
    anchor_run_dir: Path, report: FunctionalDatasetValidationReport
) -> Path:
    """Persiste `report` de forma atomica bajo `diagnostics/` del run
    ancla (primer `--run-id` de la invocacion) -- mismo mecanismo que el
    resto de diagnosticos Fase 15B2-A: temporal-hermano + flush + fsync
    + replace."""
    report_path = functional_dataset_validation_report_path(anchor_run_dir)
    atomic_write_json(report_path, report)
    return report_path
