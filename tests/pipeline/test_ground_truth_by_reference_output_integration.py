"""Ground truth BY_REFERENCE_OUTPUT (Fase 15B2-A, cierre, Seccion 3):
evidencia real de que `gt-positive-by-reference-output-unconditional-move`
(`config/ground_truth/synthetic_engineering.yaml`) es tecnicamente viable
de punta a punta -- desde `CandidatePromotionAssessmentArtifact` (Fase 9,
unificado) hasta `FunctionalValidationReport` (Parte F) -- usando
EXCLUSIVAMENTE las dos fixtures sinteticas del propio caso (caller +
callee), sin Neo4j (Fase 6/7/8/9 de interprocedural son puramente en
memoria sobre `artifacts/02-canonical/`).

Marcado `integration` (requiere el JAR real). Nunca invoca un proveedor
LLM: solo se ejecuta hasta PARSED.

Nota (Fase 15B4-CANDIDATE-QUALITY-5C): BY_REFERENCE_OUTPUT solo lo
calcula el analisis V2/interprocedural en memoria (Fase 9) -- nunca
se escribe en el `06-candidates.json` productivo (candidates_detected_
stage no invoca interprocedural_rule_detectors). Por eso este test usa
explicitamente `compute_functional_validation_report_from_promotion_
assessment` (shadow/diagnostico) en vez del default productivo, que a
partir de 5C solo lee `06-candidates.json` real y reportaria este caso
como MISSING (correcto: el candidato nunca llego al artefacto
productivo)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate_promotion_assessment import UnifiedRuleFamily
from altamira_extractor.contracts.functional_validation import (
    Applicability,
    FunctionalDatasetCoverageStatus,
    FunctionalDatasetDisposition,
    MatchOutcome,
    ValidationSource,
)
from altamira_extractor.pipeline.candidate_promotion_assessment_service import (
    compute_candidate_promotion_assessment_artifact,
)
from altamira_extractor.pipeline.functional_validation_service import (
    compute_functional_validation_report,
    compute_functional_validation_report_from_promotion_assessment,
)
from altamira_extractor.pipeline.runner import run_ingestion

from ..e2e_support import regular_file_info, require_jar

pytestmark = pytest.mark.integration

_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="GTByReference"/>
  <operation logical-name="OP-GT-BY-REFERENCE" description="Ground truth BY_REFERENCE_OUTPUT"/>
  <implementation version="1.0">
    <entry-program>GTBRCLR1</entry-program>
    <entry-program>GTBRCLE1</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_GTBR" ddl="02-parametria/ddl/PARAM_GTBR.sql"/>
  </parameter-tables>
</altamira-package>
"""

_FIXTURES_DIR = (
    Path(__file__).resolve().parents[2] / "config" / "ground_truth" / "fixtures"
)


def _build_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
    )


def _write_package_zip(path: Path) -> Path:
    caller = (_FIXTURES_DIR / "gt_by_reference_output_caller_001.cbl").read_bytes()
    callee = (_FIXTURES_DIR / "gt_by_reference_output_callee_001.cbl").read_bytes()
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(regular_file_info("manifest.xml"), _MANIFEST_XML)
        zf.writestr(regular_file_info("01-codigo/cobol/GTBRCLR1.cbl"), caller)
        zf.writestr(regular_file_info("01-codigo/cobol/GTBRCLE1.cbl"), callee)
        zf.writestr(
            regular_file_info("02-parametria/ddl/PARAM_GTBR.sql"),
            b"CREATE TABLE PARAM_GTBR (ID INT);",
        )
    return path


def test_by_reference_output_ground_truth_case_matches_end_to_end(tmp_path: Path) -> None:
    require_jar()
    settings = _build_settings(tmp_path)
    zip_path = _write_package_zip(tmp_path / "package.zip")

    state = run_ingestion(source_zip=zip_path, settings=settings)
    run_dir = settings.runs_dir / state.run_id

    assessment = compute_candidate_promotion_assessment_artifact(run_dir, state.run_id)
    assert len(assessment.candidate_references) == 1
    reference = assessment.candidate_references[0]
    assert reference.rule_family == UnifiedRuleFamily.BY_REFERENCE_OUTPUT
    assert reference.program == "GTBRCLR1"
    assert reference.paragraph == "MAIN-PARA"
    assert reference.output_literal == "OK00"

    report = compute_functional_validation_report_from_promotion_assessment(
        run_dir, state.run_id, settings.ground_truth_path
    )

    by_case_id = {c.case_id: c for c in report.case_results}
    by_reference_case = by_case_id["gt-positive-by-reference-output-unconditional-move"]
    assert by_reference_case.applicability == Applicability.APPLICABLE
    assert by_reference_case.outcome == MatchOutcome.MATCHED
    assert by_reference_case.expectation_results[0].matched_count == 1

    # Este paquete solo contiene las fixtures del caso BY_REFERENCE_OUTPUT
    # (checkpoint correctivo, cierre de Fase 15B2-A): los demas casos
    # POSITIVE, cuyas fixtures NO estan presentes en este run, deben
    # quedar NOT_APPLICABLE/NOT_EVALUATED -- nunca MISSING (eso seria
    # contar un paquete distinto como una regresion detectada) ni
    # MATCHED por coincidencia.
    assert by_case_id["gt-positive-return-code-if-else"].applicability == (
        Applicability.NOT_APPLICABLE
    )
    assert by_case_id["gt-positive-return-code-if-else"].outcome == MatchOutcome.NOT_EVALUATED
    assert by_case_id["gt-positive-level88-return-code-nested-set"].applicability == (
        Applicability.NOT_APPLICABLE
    )
    assert (
        by_case_id["gt-positive-level88-return-code-nested-set"].outcome
        == MatchOutcome.NOT_EVALUATED
    )
    assert report.metrics.true_positive_count == 1
    assert report.metrics.false_positive_count == 0
    assert report.metrics.false_negative_count == 0
    assert report.dataset_applicability == Applicability.APPLICABLE

    # Checkpoint correctivo (completitud del dataset, cierre de Fase
    # 15B2-A): este run, aislado, deja pendientes los otros casos
    # REQUIRED/FORBIDDEN del catalogo -- nunca puede afirmar por si solo
    # que el dataset completo paso, aunque el unico caso que evaluo haya
    # resultado MATCHED. Ver test_hermetic_harness_regression.py /
    # functional-validate-dataset para la agregacion real que SI cubre
    # el catalogo completo.
    assert report.coverage_status == FunctionalDatasetCoverageStatus.PARTIALLY_EVALUATED
    assert report.dataset_disposition == FunctionalDatasetDisposition.NOT_EVALUATED
    assert report.dataset_disposition != FunctionalDatasetDisposition.PASS_ENGINEERING
    assert "gt-positive-return-code-if-else" in report.pending_case_ids
    assert "gt-positive-level88-return-code-nested-set" in report.pending_case_ids

    # Checkpoint 5C (P1-PRODUCTIVE-ARTIFACT-QUALIFICATION): el default
    # productivo lee EXCLUSIVAMENTE el 06-candidates.json real. Como
    # BY_REFERENCE_OUTPUT nunca llega a ese artefacto en este run, debe
    # reportarse honestamente como MISSING -- nunca un TP fabricado por
    # una recomputacion V2/interprocedural en memoria.
    productive_report = compute_functional_validation_report(
        run_dir, state.run_id, settings.ground_truth_path
    )
    productive_by_case_id = {c.case_id: c for c in productive_report.case_results}
    productive_by_reference_case = productive_by_case_id[
        "gt-positive-by-reference-output-unconditional-move"
    ]
    assert productive_by_reference_case.outcome == MatchOutcome.MISSING
    assert productive_report.productive_candidate_count == 0
    assert productive_report.validation_source == ValidationSource.PRODUCTIVE_ARTIFACT
