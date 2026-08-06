"""Servicio de filesystem de validacion funcional (Fase 15B2-A, Parte F).
Carga `config/ground_truth/synthetic_engineering.yaml` (Parte E), invoca
`compute_candidate_promotion_assessment_artifact` (Fase 9, ya existente --
este servicio NUNCA recalcula candidatos por su cuenta ni relee un
`diagnostics/candidate-promotion-assessment.json` persistido, mismo
principio que `candidate_promotion_review_service.py`), ejecuta el
matching puro (`functional_validation_matcher.validate_ground_truth`) y
persiste el resultado en `<run_dir>/diagnostics/functional-validation-
report.json`.

NO es un `PipelineStage`: se invoca exclusivamente bajo demanda (CLI),
nunca desde `runner.py`/`run_ingestion`. Nunca modifica ningun artefacto
de entrada (decision arquitectonica #7).

`_compute_run_fixture_hashes` (checkpoint correctivo, cierre de Fase
15B2-A): calcula el sha256 de CADA archivo regular realmente ingerido
por este run (`run_dir/work/extracted/**`, la copia intacta post-ZIP
Slip-safe del paquete original -- ver `runner.py::_run_extracted`).
`functional_validation_matcher.validate_ground_truth` usa este conjunto
para decidir la aplicabilidad de cada `GroundTruthCase`: un caso cuyo
fixture set no esta byte-a-byte presente en este run nunca se evalua."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..contracts.candidate_promotion_assessment import CandidatePromotionAssessmentArtifact
from ..contracts.functional_ground_truth import FunctionalGroundTruthSet
from ..contracts.functional_validation import FunctionalValidationReport
from .artifact_store import atomic_write_json
from .candidate_promotion_assessment_service import compute_candidate_promotion_assessment_artifact
from .errors import (
    CandidatePromotionAssessmentError,
    FunctionalValidationError,
    SemanticConfigError,
)
from .functional_validation_matcher import GuardrailLookupEntry, validate_ground_truth
from .yaml_utils import read_yaml_config

_DIAGNOSTICS_DIR_NAME = "diagnostics"
_REPORT_FILENAME = "functional-validation-report.json"
_EXTRACTED_DIR_RELATIVE = ("work", "extracted")
_GUARDRAIL_MANIFEST_RELATIVE = ("artifacts", "09-guardrails", "guardrail-manifest.json")


def _load_guardrail_lookup(run_dir: Path) -> dict[str, GuardrailLookupEntry]:
    """Carga `artifacts/09-guardrails/guardrail-manifest.json` (si
    existe -- ausente es normal para un run con cero candidatos V1
    promovidos) y construye `candidate_id -> GuardrailLookupEntry`, para
    que `validate_ground_truth` pueda trazar metricas de caso reales
    (Seccion 5, cierre de Fase 15B2-A) sin que el analizador puro toque
    filesystem."""
    manifest_path = run_dir.joinpath(*_GUARDRAIL_MANIFEST_RELATIVE)
    if not manifest_path.is_file():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        record["candidate_id"]: GuardrailLookupEntry(
            verdict=record["final_evidence_validation_status"],
            repair_attempts=record["repair_attempts_used"],
        )
        for record in manifest.get("records", [])
    }


def _compute_run_fixture_hashes(run_dir: Path) -> frozenset[str]:
    """sha256 hexdigest de cada archivo regular bajo `run_dir/work/
    extracted/`, sin importar su ruta relativa dentro del ZIP original
    (una fixture identica byte a byte pero re-empaquetada bajo otro
    directorio interno sigue siendo la MISMA fixture). Directorio
    ausente (run que nunca alcanzo EXTRACTED) -> conjunto vacio, nunca
    un error: eso simplemente hace que ningun caso resulte APPLICABLE."""
    extracted_dir = run_dir.joinpath(*_EXTRACTED_DIR_RELATIVE)
    if not extracted_dir.is_dir():
        return frozenset()
    hashes: set[str] = set()
    for path in extracted_dir.rglob("*"):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        hashes.add(digest.hexdigest())
    return frozenset(hashes)


def load_ground_truth_set(path: Path) -> FunctionalGroundTruthSet:
    """Carga y valida `path` (tipicamente `config/ground_truth/synthetic_
    engineering.yaml`) contra `FunctionalGroundTruthSet`. Ausente/mal
    formado/no valida siempre levanta `FunctionalValidationError` (nunca
    `SemanticConfigError` sin traducir)."""
    try:
        document, _config_hash = read_yaml_config(path)
    except SemanticConfigError as exc:
        raise FunctionalValidationError(f"{path.name}: {exc}") from exc
    try:
        return FunctionalGroundTruthSet.model_validate(document)
    except ValueError as exc:
        raise FunctionalValidationError(
            f"{path.name}: no valida contra FunctionalGroundTruthSet: {exc}"
        ) from exc


def compute_functional_validation_report(
    run_dir: Path, run_id: str, ground_truth_path: Path
) -> FunctionalValidationReport:
    """Localiza `run_dir`, calcula `CandidatePromotionAssessmentArtifact`
    (Fase 9) y lo compara contra `ground_truth_path`. Nunca escribe nada
    -- la persistencia es responsabilidad de
    `write_functional_validation_report`/el comando CLI."""
    ground_truth = load_ground_truth_set(ground_truth_path)

    try:
        assessment: CandidatePromotionAssessmentArtifact = (
            compute_candidate_promotion_assessment_artifact(run_dir, run_id)
        )
    except CandidatePromotionAssessmentError as exc:
        raise FunctionalValidationError(
            f"no se pudo calcular CandidatePromotionAssessmentArtifact: {exc}"
        ) from exc

    return validate_ground_truth(
        ground_truth,
        assessment.candidate_references,
        run_id=run_id,
        source_package_hash=assessment.source_package_hash,
        run_fixture_hashes=_compute_run_fixture_hashes(run_dir),
        guardrail_by_candidate_id=_load_guardrail_lookup(run_dir),
    )


def functional_validation_report_path(run_dir: Path) -> Path:
    return run_dir / _DIAGNOSTICS_DIR_NAME / _REPORT_FILENAME


def write_functional_validation_report(run_dir: Path, report: FunctionalValidationReport) -> Path:
    """Persiste `report` de forma atomica en `diagnostics/functional-
    validation-report.json` (mismo mecanismo que el resto de diagnosticos
    Fase 15B2-A: temporal-hermano + flush + fsync + replace)."""
    report_path = functional_validation_report_path(run_dir)
    atomic_write_json(report_path, report)
    return report_path
