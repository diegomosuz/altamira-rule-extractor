"""Ground truth BY_REFERENCE_OUTPUT (Fase 15B2-A, cierre, Seccion 3):
evidencia real de que `gt-positive-by-reference-output-unconditional-move`
(`config/ground_truth/shadow_interprocedural.yaml`, Fase 15B4-CANDIDATE-
QUALITY-5D-SAFETY: catalogo SHADOW-ONLY separado, ver seccion 1) es
tecnicamente viable de punta a punta -- desde `CandidatePromotionAssessment
Artifact` (Fase 9, unificado) hasta `FunctionalValidationReport` (Parte
F) -- usando EXCLUSIVAMENTE las dos fixtures sinteticas del propio caso
(caller + callee), sin Neo4j (Fase 6/7/8/9 de interprocedural son
puramente en memoria sobre `artifacts/02-canonical/`).

Marcado `integration` (requiere el JAR real). Nunca invoca un proveedor
LLM: solo se ejecuta hasta PARSED.

Nota (Fase 15B4-CANDIDATE-QUALITY-5C, endurecida en 5D-SAFETY):
BY_REFERENCE_OUTPUT solo lo calcula el analisis V2/interprocedural en
memoria (Fase 9) -- nunca se escribe en el `06-candidates.json`
productivo (candidates_detected_stage no invoca interprocedural_rule_
detectors). Por eso este test usa explicitamente `compute_functional_
validation_report_from_promotion_assessment` (shadow/diagnostico) contra
el catalogo SHADOW-ONLY (`shadow_interprocedural.yaml`) -- nunca contra
`config/ground_truth/synthetic_engineering.yaml` (el catalogo PRODUCTIVO
real, que a partir de 5D-SAFETY ya NO declara este caso: una capacidad
DEFERRED_UNSUPPORTED nunca debe figurar como expected positive
business-rule fact del release gate productivo, ver docs/
CAPABILITY_COVERAGE_1_17.md)."""

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


_SHADOW_GROUND_TRUTH_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "ground_truth"
    / "shadow_interprocedural.yaml"
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
        run_dir, state.run_id, _SHADOW_GROUND_TRUTH_PATH
    )

    by_case_id = {c.case_id: c for c in report.case_results}
    by_reference_case = by_case_id["gt-positive-by-reference-output-unconditional-move"]
    assert by_reference_case.applicability == Applicability.APPLICABLE
    assert by_reference_case.outcome == MatchOutcome.MATCHED
    assert by_reference_case.expectation_results[0].matched_count == 1
    assert report.metrics.true_positive_count == 1
    assert report.metrics.false_positive_count == 0
    assert report.metrics.false_negative_count == 0
    assert report.dataset_applicability == Applicability.APPLICABLE

    # 5D-SAFETY seccion 1: el catalogo shadow tiene EXACTAMENTE 1 caso
    # (BY_REFERENCE_OUTPUT), asi que este run lo cubre por completo --
    # a diferencia del catalogo productivo compartido de antes de 5D-SAFETY,
    # ya no queda ningun otro caso pendiente en este catalogo dedicado.
    assert report.coverage_status == FunctionalDatasetCoverageStatus.COMPLETELY_EVALUATED
    assert report.dataset_disposition == FunctionalDatasetDisposition.PASS_ENGINEERING
    assert report.pending_case_ids == []

    # Checkpoint 5D-SAFETY (seccion 1, P1-CORPUS-GAP-GT): el catalogo
    # PRODUCTIVO real (`synthetic_engineering.yaml`) ya NO declara
    # BY_REFERENCE_OUTPUT como expected positive business-rule fact --
    # DEFERRED_UNSUPPORTED nunca debe figurar alli. El release report
    # productivo (que lee EXCLUSIVAMENTE 06-candidates.json, nunca
    # recalcula interprocedural) debe quedar honestamente NOT_APPLICABLE
    # para este run: ningun caso del catalogo productivo tiene sus
    # fixtures presentes en este paquete (solo caller/callee del caso
    # shadow) -- nunca un MISSING que despues haya que excluir a mano.
    productive_report = compute_functional_validation_report(
        run_dir, state.run_id, settings.ground_truth_path
    )
    assert "gt-positive-by-reference-output-unconditional-move" not in {
        c.case_id for c in productive_report.case_results
    }
    assert productive_report.dataset_applicability == Applicability.NOT_APPLICABLE
    assert productive_report.productive_candidate_count == 0
    assert productive_report.validation_source == ValidationSource.PRODUCTIVE_ARTIFACT
