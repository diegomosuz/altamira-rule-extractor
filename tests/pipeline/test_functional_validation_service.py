"""Tests de `pipeline/functional_validation_service.py` (Fase
15B4-CANDIDATE-QUALITY-5C, cierre de
P1-PRODUCTIVE-ARTIFACT-QUALIFICATION): `compute_functional_validation_
report` (default PRODUCTIVO) debe leer UNICAMENTE `artifacts/
06-candidates.json` tal como el run lo persistio -- nunca recalcular
V2/interprocedural. Sin Neo4j, sin LLM, sin Docker -- solo filesystem
local (`tmp_path`), mismo patron que
`tests/pipeline/test_candidate_promotion_assessment_service.py`."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from altamira_extractor.config import Settings, load_settings
from altamira_extractor.contracts.candidate import CandidateArtifact, RuleCandidate
from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.context_package import (
    BatchContext,
    CodeSliceEntry,
    Completeness,
    ContextPackage,
    ContextPackageCandidate,
    ContextPackageOperation,
    ContextPackageScope,
    DataContext,
    Effects,
    EvidenceEntry,
)
from altamira_extractor.contracts.enums import (
    BatchContextStatus,
    CandidateStatus,
    CompletenessStatus,
    InclusionReason,
    PipelineStage,
    StageStatus,
)
from altamira_extractor.contracts.functional_ground_truth import (
    FunctionalGroundTruthSet,
    FunctionalGroundTruthSummary,
    GroundTruthCase,
    GroundTruthCaseKind,
    GroundTruthExpectedRule,
    GroundTruthFixtureReference,
)
from altamira_extractor.contracts.functional_validation import (
    FinalRuleLinkageStatus,
    ValidationSource,
)
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.errors import FunctionalValidationError
from altamira_extractor.pipeline.functional_validation_matcher import MatchOutcome
from altamira_extractor.pipeline.functional_validation_service import (
    compute_functional_validation_report,
)
from altamira_extractor.pipeline.release_readiness_service import (
    compute_release_readiness_assessment,
)

_HASH = "c" * 64
_RUN_ID = "20260101T000000000000-dddddddd"
_PARAGRAPH_ID = "program::AR::APP::PROG1::1.0::" + _HASH[:12] + "::paragraph::MAIN-PARA"
_DECISION_ID = f"{_PARAGRAPH_ID}::decision::10::1"
_CANDIDATE_ID = f"candidate::q0-return-code-decision::1.0::{_HASH}::{_DECISION_ID}"
_FIXTURE_BYTES = b"       IDENTIFICATION DIVISION.\n"
_FIXTURE_SHA256 = hashlib.sha256(_FIXTURE_BYTES).hexdigest()


def _write_run_state(
    run_dir: Path, *, stages: tuple[PipelineStage, ...], source_package_hash: str = _HASH
) -> RunState:
    now = datetime.now(UTC)
    executions = [
        StageExecution(
            stage=stage, status=StageStatus.SUCCEEDED, started_at=now, finished_at=now,
            duration_seconds=0.0,
        )
        for stage in stages
    ]
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=source_package_hash,
        current_stage=stages[-1] if stages else PipelineStage.RECEIVED,
        stages=executions,
        created_at=now,
        updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)
    return state


def _write_extracted_fixture(run_dir: Path) -> str:
    extracted = run_dir / "work" / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)
    (extracted / "PROG1.cbl").write_bytes(_FIXTURE_BYTES)
    return hashlib.sha256(_FIXTURE_BYTES).hexdigest()


def _candidate(**overrides: object) -> RuleCandidate:
    defaults: dict[str, object] = {
        "candidate_id": _CANDIDATE_ID,
        "paragraph_id": _PARAGRAPH_ID,
        "paragraph_name": "MAIN-PARA",
        "decision_id": _DECISION_ID,
        "detector_id": "q0-return-code-decision",
        "detector_version": "1.0",
        "detector_score": 1.0,
        "status": CandidateStatus.DETECTED_CANDIDATE,
        "condition": "WS-SALDO<0",
        "outcome_code": "R001",
        "rule_type": None,
        "line_start": 10,
        "source_file": "01-codigo/cobol/PROG1.cbl",
        "source_package_hash": _HASH,
        "candidate_source": CandidateSource.V1,
        "rule_family": UnifiedRuleFamily.RETURN_CODE,
        "evidence_ids": [],
    }
    defaults.update(overrides)
    return RuleCandidate(**defaults)  # type: ignore[arg-type]


def _write_candidates(run_dir: Path, candidates: list[RuleCandidate]) -> None:
    artifact = CandidateArtifact(
        run_id=_RUN_ID,
        source_package_hash=_HASH,
        semantic_graph_hash=_HASH,
        invariants_query_hash=_HASH,
        q0_query_hash=_HASH,
        candidates=candidates,
        warnings=[],
    )
    (run_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    atomic_write_json(run_dir / "artifacts" / "06-candidates.json", artifact)


def _write_ground_truth(
    path: Path, *, expected_paragraph: str | None = "MAIN-PARA", program: str = "PROG1"
) -> None:
    case = GroundTruthCase(
        case_id="gt-positive-return-code",
        kind=GroundTruthCaseKind.POSITIVE,
        program=program,
        fixtures=[
            GroundTruthFixtureReference(
                relative_path="config/ground_truth/fixtures/gt_return_code_001.cbl",
                sha256=_FIXTURE_SHA256,
            )
        ],
        description="Caso sintetico de test (5C).",
        expected_rules=[
            GroundTruthExpectedRule(
                expectation_id="gt-positive-return-code::e1",
                rule_family=UnifiedRuleFamily.RETURN_CODE,
                paragraph=expected_paragraph,
                minimum_count=1,
                derivation_notes="Fixture sintetica de test.",
            )
        ],
    )
    ground_truth = FunctionalGroundTruthSet(
        catalog_edition="test-edition",
        cases=[case],
        summary=FunctionalGroundTruthSummary(
            case_count=1, positive_case_count=1, negative_case_count=0, expected_rule_count=1
        ),
    )
    path.write_text(
        json.dumps(ground_truth.model_dump(mode="json"), indent=2), encoding="utf-8"
    )


@pytest.fixture
def base_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / _RUN_ID
    run_dir.mkdir(parents=True)
    _write_extracted_fixture(run_dir)
    return run_dir


@pytest.fixture
def ground_truth_path(tmp_path: Path) -> Path:
    path = tmp_path / "ground_truth.json"
    _write_ground_truth(path)
    return path


# --- A. lee 06 real ----------------------------------------------------------


def test_productive_report_reads_real_06_candidates(
    base_run: Path, ground_truth_path: Path
) -> None:
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.CANDIDATES_DETECTED))
    _write_candidates(base_run, [_candidate()])

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.validation_source == ValidationSource.PRODUCTIVE_ARTIFACT
    assert report.productive_candidate_count == 1
    case = next(c for c in report.case_results if c.case_id == "gt-positive-return-code")
    assert case.outcome == MatchOutcome.MATCHED


# --- B. no-recomputation sentinel (OBLIGATORIO) ------------------------------


def test_productive_report_never_triggers_recomputation(
    base_run: Path, ground_truth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Envenena los tres puntos de recalculo (V2 shadow, interprocedural,
    CandidatePromotionAssessment) -- si la ruta productiva alguna vez
    los invoca, el test falla inmediatamente."""
    import altamira_extractor.pipeline.candidate_promotion_assessment_service as cpas_module
    import altamira_extractor.pipeline.functional_validation_service as service_module

    def _poison(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("qualification productiva NUNCA debe recalcular candidatos")

    monkeypatch.setattr(
        service_module, "compute_candidate_promotion_assessment_artifact", _poison
    )
    monkeypatch.setattr(cpas_module, "compute_v2_shadow_candidates_artifact", _poison)
    monkeypatch.setattr(
        cpas_module, "compute_interprocedural_rule_candidates_artifact", _poison
    )

    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.CANDIDATES_DETECTED))
    _write_candidates(base_run, [_candidate()])

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)
    assert report.validation_source == ValidationSource.PRODUCTIVE_ARTIFACT


# --- C. expected missing -> FN -----------------------------------------------


def test_expected_missing_from_06_is_false_negative(
    base_run: Path, ground_truth_path: Path
) -> None:
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.CANDIDATES_DETECTED))
    _write_candidates(base_run, [])  # 06 vacio: GT espera la regla, no aparece

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    case = next(c for c in report.case_results if c.case_id == "gt-positive-return-code")
    assert case.outcome == MatchOutcome.MISSING
    assert report.metrics.false_negative_count == 1
    assert report.metrics.true_positive_count == 0


# --- D. unexpected actual -> FP (caso negativo) ------------------------------


def _write_negative_ground_truth(path: Path) -> None:
    case = GroundTruthCase(
        case_id="gt-negative",
        kind=GroundTruthCaseKind.NEGATIVE,
        program="PROG1",
        fixtures=[
            GroundTruthFixtureReference(
                relative_path="config/ground_truth/fixtures/gt_return_code_001.cbl",
                sha256=_FIXTURE_SHA256,
            )
        ],
        description="Caso negativo de test (5C).",
    )
    ground_truth = FunctionalGroundTruthSet(
        catalog_edition="test-edition",
        cases=[case],
        summary=FunctionalGroundTruthSummary(
            case_count=1, positive_case_count=0, negative_case_count=1, expected_rule_count=0
        ),
    )
    path.write_text(json.dumps(ground_truth.model_dump(mode="json"), indent=2), encoding="utf-8")


def test_unexpected_actual_in_negative_scope_is_false_positive(
    base_run: Path, tmp_path: Path
) -> None:
    gt_path = tmp_path / "gt_negative.json"
    _write_negative_ground_truth(gt_path)
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.CANDIDATES_DETECTED))
    _write_candidates(base_run, [_candidate()])  # candidato inesperado en scope negativo

    report = compute_functional_validation_report(base_run, _RUN_ID, gt_path)

    case = next(c for c in report.case_results if c.case_id == "gt-negative")
    assert case.outcome == MatchOutcome.UNEXPECTED_CANDIDATES
    assert report.metrics.false_positive_count == 1


# --- E. negative case sin violacion -> TN ------------------------------------


def test_negative_case_without_candidates_is_confirmed_absent(
    base_run: Path, tmp_path: Path
) -> None:
    gt_path = tmp_path / "gt_negative.json"
    _write_negative_ground_truth(gt_path)
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.CANDIDATES_DETECTED))
    _write_candidates(base_run, [])

    report = compute_functional_validation_report(base_run, _RUN_ID, gt_path)

    case = next(c for c in report.case_results if c.case_id == "gt-negative")
    assert case.outcome == MatchOutcome.CONFIRMED_ABSENT
    assert report.metrics.true_negative_count == 1


# --- F. duplicate actual no se oculta (bug ya corregido en 5A) --------------


def test_duplicate_productive_representation_is_never_hidden_as_single_tp(
    base_run: Path, ground_truth_path: Path
) -> None:
    """Reproduce el patron de los bugs 5827c8b/cfe8209 (13 hechos -> 26
    candidatos): si `06-candidates.json` alguna vez volviera a contener
    2 representaciones del MISMO hecho, la qualification productiva
    debe verlas TAL COMO SALIERON -- 2 `unified_reference_id` distintos
    satisfaciendo la misma expectation, nunca 1 oculto."""
    duplicate = _candidate(
        candidate_id=_CANDIDATE_ID + "::dup",
        rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
        candidate_source=CandidateSource.V2,
    )
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.CANDIDATES_DETECTED))
    _write_candidates(base_run, [_candidate(), duplicate])

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.productive_candidate_count == 2
    case = next(c for c in report.case_results if c.case_id == "gt-positive-return-code")
    # RETURN_CODE expectation matches ONLY the RETURN_CODE-family candidate
    # (LEVEL_88_RETURN_CODE es una familia distinta) -- exactamente 1 match,
    # nunca 2 ocultos detras de un unico TP.
    assert case.expectation_results[0].matched_count == 1
    assert case.expectation_results[0].matched_unified_reference_ids != []


# --- H. context missing -> artifact-chain failure ----------------------------


def test_candidate_without_context_is_artifact_chain_failure(
    base_run: Path, ground_truth_path: Path
) -> None:
    _write_run_state(
        base_run,
        stages=(
            PipelineStage.PARSED,
            PipelineStage.CANDIDATES_DETECTED,
            PipelineStage.CONTEXTS_BUILT,
        ),
    )
    _write_candidates(base_run, [_candidate()])
    (base_run / "artifacts" / "07-context").mkdir(parents=True, exist_ok=True)  # vacio a proposito

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.artifact_chain_integrity.candidates_checked == 1
    assert report.artifact_chain_integrity.candidates_missing_context == [_CANDIDATE_ID]
    # Nunca reinterpretado como FN semantico: el caso GT sigue MATCHED
    # (el candidato SI existe en 06), la falla es de integridad, distinta.
    case = next(c for c in report.case_results if c.case_id == "gt-positive-return-code")
    assert case.outcome == MatchOutcome.MATCHED


# --- 5C-SAFETY-2 seccion 1: 06 ausente vs 06 con cero candidatos ------------


def test_missing_06_after_candidates_detected_stage_raises(
    base_run: Path, ground_truth_path: Path
) -> None:
    """Seccion 1A: `candidates_detected_stage.py` SIEMPRE escribe
    `06-candidates.json` (incondicionalmente) en cuanto CANDIDATES_DETECTED
    alcanza SUCCEEDED -- su ausencia en ese punto es una falla de
    integridad real, nunca "cero candidatos legitimos"."""
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.CANDIDATES_DETECTED))
    # 06-candidates.json deliberadamente NUNCA escrito.

    with pytest.raises(FunctionalValidationError, match="06-candidates.json"):
        compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)


def test_missing_06_before_candidates_detected_stage_is_not_a_failure(
    base_run: Path, ground_truth_path: Path
) -> None:
    """Seccion 1A (caso contrario): un run que aun no alcanzo
    CANDIDATES_DETECTED nunca debio escribir 06-candidates.json -- su
    ausencia sigue siendo legitima, nunca una falla."""
    _write_run_state(base_run, stages=(PipelineStage.PARSED,))

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.productive_candidate_count == 0


def test_valid_06_with_zero_candidates_is_legitimate(
    base_run: Path, ground_truth_path: Path
) -> None:
    """Seccion 1C: un 06-candidates.json real, valido, con
    `candidates=[]` es una extraccion limpia que genuinamente no
    encontro nada -- distinto del caso anterior (archivo ausente tras
    CANDIDATES_DETECTED), nunca debe levantar `FunctionalValidationError`."""
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.CANDIDATES_DETECTED))
    _write_candidates(base_run, [])

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.productive_candidate_count == 0
    assert report.validation_source == ValidationSource.PRODUCTIVE_ARTIFACT


# --- 5C-SAFETY-2 seccion 2: ContextPackage contrato + candidate_id linkage --


def _build_context_package(candidate_id: str) -> ContextPackage:
    """Instancia minima valida contra `ContextPackage` (mismo shape que
    `tests/api/conftest.py::build_context_package`, duplicado local
    deliberado -- mismo precedente que el resto de este archivo)."""
    return ContextPackage(
        candidate=ContextPackageCandidate(
            candidate_id=candidate_id,
            decision_id=None,
            detector_id="det",
            detector_version="1.0",
            detector_score=1.0,
        ),
        scope=ContextPackageScope(
            country="AR",
            application="Transferencias",
            operation=ContextPackageOperation(logical_name="OP1", description=None),
            program="PROG1",
            program_version="1",
            paragraph="MAIN",
            source_file="cobol/PROG1.cbl",
            line_start=10,
            line_end=10,
            source_package_hash=_HASH,
        ),
        code_slice=[
            CodeSliceEntry(
                paragraph_id="p1",
                paragraph="MAIN",
                source_file="cobol/PROG1.cbl",
                source_text="IF WS-COD = 'R001'",
                line_start=10,
                line_end=10,
                inclusion_reason=InclusionReason.CANDIDATE,
                evidence_ids=["ev-1"],
            )
        ],
        data_context=DataContext(parameter_tables=[], transactional_tables_read=[]),
        effects=Effects(return_codes=[], table_effects=[]),
        batch_context=BatchContext(status=BatchContextStatus.NOT_AVAILABLE, downstream_jobs=[]),
        evidence=[
            EvidenceEntry(
                evidence_id="ev-1",
                kind="decision",
                source_file="cobol/PROG1.cbl",
                line_start=10,
                line_end=10,
                source_package_hash=_HASH,
            )
        ],
        completeness=Completeness(
            D1=CompletenessStatus.COMPLETE,
            D2=CompletenessStatus.COMPLETE,
            D3=CompletenessStatus.NOT_AVAILABLE,
            D4=CompletenessStatus.COMPLETE,
            D5=CompletenessStatus.NOT_AVAILABLE,
            D6=CompletenessStatus.NOT_AVAILABLE,
            D7=CompletenessStatus.NOT_AVAILABLE,
        ),
    )


def _write_context_file(
    run_dir: Path, *, filename_candidate_id: str, package: ContextPackage
) -> None:
    context_dir = run_dir / "artifacts" / "07-context"
    context_dir.mkdir(parents=True, exist_ok=True)
    filename = hashlib.sha256(filename_candidate_id.encode("utf-8")).hexdigest() + ".json"
    (context_dir / filename).write_text(package.to_stable_json(), encoding="utf-8")


def test_valid_context_package_passes_artifact_chain_integrity(
    base_run: Path, ground_truth_path: Path
) -> None:
    _write_run_state(
        base_run,
        stages=(
            PipelineStage.PARSED,
            PipelineStage.CANDIDATES_DETECTED,
            PipelineStage.CONTEXTS_BUILT,
        ),
    )
    _write_candidates(base_run, [_candidate()])
    _write_context_file(
        base_run,
        filename_candidate_id=_CANDIDATE_ID,
        package=_build_context_package(_CANDIDATE_ID),
    )

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.artifact_chain_integrity.candidates_missing_context == []


def test_context_package_with_wrong_candidate_id_is_artifact_chain_failure(
    base_run: Path, ground_truth_path: Path
) -> None:
    """Seccion 2D: un `ContextPackage` valido y parseable, pero cuyo
    `candidate.candidate_id` interno NO coincide con el candidato
    productivo esperado (archivo mal nombrado/intercambiado), es la
    MISMA falla de integridad que un archivo ausente -- nunca "contexto
    presente" solo porque el JSON parsea y valida contra el schema."""
    _write_run_state(
        base_run,
        stages=(
            PipelineStage.PARSED,
            PipelineStage.CANDIDATES_DETECTED,
            PipelineStage.CONTEXTS_BUILT,
        ),
    )
    _write_candidates(base_run, [_candidate()])
    # Nombrado con el hash de _CANDIDATE_ID (para que el lookup lo
    # encuentre), pero el candidate_id INTERNO del contenido es otro.
    _write_context_file(
        base_run,
        filename_candidate_id=_CANDIDATE_ID,
        package=_build_context_package("candidate::wrong-one"),
    )

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.artifact_chain_integrity.candidates_missing_context == [_CANDIDATE_ID]


# --- I. deterministic-offline -> 10-rules NOT_APPLICABLE ---------------------


def test_deterministic_offline_run_marks_final_rule_linkage_not_applicable(
    base_run: Path, ground_truth_path: Path
) -> None:
    _write_run_state(
        base_run,
        stages=(
            PipelineStage.PARSED,
            PipelineStage.CANDIDATES_DETECTED,
            PipelineStage.CONTEXTS_BUILT,
        ),
    )
    _write_candidates(base_run, [_candidate()])

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.final_rule_linkage.status == FinalRuleLinkageStatus.NOT_APPLICABLE
    assert report.final_rule_linkage.rules_manifest_rule_count is None


# --- J. FULL_LLM valid 10-rules linkage -> PASS ------------------------------


def _write_rules_manifest(
    run_dir: Path,
    records: list[dict[str, str]],
    *,
    write_markdown: bool = True,
) -> None:
    """5C-SAFETY-2: escribe un `RulesDirectoryManifest`-shaped JSON
    completo (todos los campos requeridos por el contrato tipado real,
    no un dict parcial) -- opcionalmente materializa cada `.md`
    referenciado, ya que `_compute_final_rule_linkage` ahora verifica su
    existencia real en disco."""
    rules_dir = run_dir / "artifacts" / "10-rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "1.0",
        "run_id": _RUN_ID,
        "source_package_hash": _HASH,
        "guardrail_manifest_hash": _HASH,
        "renderer_version": "1.0",
        "records": [
            {
                "candidate_id": record["candidate_id"],
                "source_guardrail_artifact_hash": _HASH,
                "final_rule_draft_hash": _HASH,
                "relative_filename": record["relative_filename"],
                "markdown_hash": _HASH,
            }
            for record in records
        ],
        "rule_count": len(records),
        "warnings": [],
    }
    (rules_dir / "rules-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if write_markdown:
        for record in records:
            (rules_dir / record["relative_filename"]).write_text("# Rule\n", encoding="utf-8")


def test_full_llm_completed_run_with_valid_manifest_passes_linkage(
    base_run: Path, ground_truth_path: Path
) -> None:
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.COMPLETED))
    _write_candidates(base_run, [_candidate()])
    _write_rules_manifest(base_run, [{"candidate_id": _CANDIDATE_ID, "relative_filename": "x.md"}])

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    linkage = report.final_rule_linkage
    assert linkage.status == FinalRuleLinkageStatus.APPLICABLE
    assert linkage.rules_manifest_rule_count == 1
    assert linkage.broken_candidate_ids == []
    assert linkage.duplicate_candidate_ids == []
    assert linkage.missing_final_rule_candidate_ids == []


# --- K. broken manifest candidate_id -> FAIL ---------------------------------


def test_full_llm_completed_run_with_broken_manifest_candidate_id_fails_linkage(
    base_run: Path, ground_truth_path: Path
) -> None:
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.COMPLETED))
    _write_candidates(base_run, [_candidate()])
    _write_rules_manifest(
        base_run, [{"candidate_id": "candidate::does-not-exist", "relative_filename": "x.md"}]
    )

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.final_rule_linkage.broken_candidate_ids == ["candidate::does-not-exist"]


# --- 5C-SAFETY-2 seccion 3: markdown referenciado ausente -> FAIL -----------


def test_full_llm_completed_run_with_missing_markdown_file_fails_linkage(
    base_run: Path, ground_truth_path: Path
) -> None:
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.COMPLETED))
    _write_candidates(base_run, [_candidate()])
    _write_rules_manifest(
        base_run,
        [{"candidate_id": _CANDIDATE_ID, "relative_filename": "x.md"}],
        write_markdown=False,  # el manifest lo referencia, pero el .md nunca se escribe
    )

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.final_rule_linkage.missing_final_rule_candidate_ids == [_CANDIDATE_ID]
    assert report.final_rule_linkage.broken_candidate_ids == []


# --- 5C-SAFETY-2 seccion 5: manifest ausente para COMPLETED -> FAIL ---------


def test_full_llm_completed_run_without_rules_manifest_raises(
    base_run: Path, ground_truth_path: Path
) -> None:
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.COMPLETED))
    _write_candidates(base_run, [_candidate()])
    # Sin artifacts/10-rules/ en absoluto: rules_rendered_stage.py SIEMPRE
    # lo escribe (incluso vacio) al llegar a COMPLETED -- su ausencia es
    # una falla de integridad real, nunca "sin reglas todavia".

    with pytest.raises(FunctionalValidationError, match="rules-manifest.json"):
        compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)


def test_full_llm_completed_run_with_malformed_rules_manifest_raises(
    base_run: Path, ground_truth_path: Path
) -> None:
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.COMPLETED))
    _write_candidates(base_run, [_candidate()])
    rules_dir = base_run / "artifacts" / "10-rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    (rules_dir / "rules-manifest.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(FunctionalValidationError, match="rules-manifest.json"):
        compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)


# --- Section 21: guardrail REJECTED nunca se confunde con FN de deteccion ---


def test_guardrail_rejected_candidate_is_reported_separately_never_as_fn(
    base_run: Path, ground_truth_path: Path
) -> None:
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.COMPLETED))
    _write_candidates(base_run, [_candidate()])
    guardrail_dir = base_run / "artifacts" / "09-guardrails"
    guardrail_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "records": [
            {
                "candidate_id": _CANDIDATE_ID,
                "final_evidence_validation_status": "REJECTED",
                "repair_attempts_used": 2,
            }
        ]
    }
    (guardrail_dir / "guardrail-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # rules_rendered_stage.py SIEMPRE escribe 10-rules/rules-manifest.json
    # (aunque vacio) al llegar a COMPLETED -- incluso en este escenario
    # sintetico (rechazo per-candidato que el pipeline real no produce,
    # ver 5C-SAFETY seccion 7/6) el manifest vacio sigue siendo la forma
    # honesta de representarlo, nunca su ausencia total.
    _write_rules_manifest(base_run, [])

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.final_rule_linkage.guardrail_rejected_candidate_ids == [_CANDIDATE_ID]
    # El rechazo legitimo NUNCA se reporta tambien como falla de
    # integridad (5C-SAFETY seccion 9): son ausencias mutuamente
    # excluyentes.
    assert report.final_rule_linkage.missing_final_rule_candidate_ids == []
    # El candidato SI satisface la expectation GT (existe en 06) -- MATCHED,
    # nunca FN por el rechazo downstream del guardrail.
    case = next(c for c in report.case_results if c.case_id == "gt-positive-return-code")
    assert case.outcome == MatchOutcome.MATCHED


# --- 5C-SAFETY seccion 8: EVIDENCE_VALIDATED con linkage correcto -----------


def _write_guardrail_manifest(run_dir: Path, *, candidate_id: str, verdict: str) -> None:
    guardrail_dir = run_dir / "artifacts" / "09-guardrails"
    guardrail_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "records": [
            {
                "candidate_id": candidate_id,
                "final_evidence_validation_status": verdict,
                "repair_attempts_used": 0,
            }
        ]
    }
    (guardrail_dir / "guardrail-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_evidence_validated_candidate_correctly_linked_passes(
    base_run: Path, ground_truth_path: Path
) -> None:
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.COMPLETED))
    _write_candidates(base_run, [_candidate()])
    _write_guardrail_manifest(base_run, candidate_id=_CANDIDATE_ID, verdict="EVIDENCE_VALIDATED")
    _write_rules_manifest(base_run, [{"candidate_id": _CANDIDATE_ID, "relative_filename": "x.md"}])

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.final_rule_linkage.missing_final_rule_candidate_ids == []
    assert report.final_rule_linkage.broken_candidate_ids == []


def test_evidence_validated_candidate_missing_from_manifest_fails_linkage(
    base_run: Path, ground_truth_path: Path
) -> None:
    """Seccion 8 (5C-SAFETY), test obligatorio: un candidato aprobado por
    el guardrail (EVIDENCE_VALIDATED) que no aparece en rules-manifest.json
    (corrupcion/bug hipotetico del renderer) debe reportarse como falla
    de integridad real -- nunca como PASS silencioso ni como rechazo
    legitimo del guardrail."""
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.COMPLETED))
    _write_candidates(base_run, [_candidate()])
    _write_guardrail_manifest(base_run, candidate_id=_CANDIDATE_ID, verdict="EVIDENCE_VALIDATED")
    _write_rules_manifest(base_run, [])  # candidato aprobado, pero AUSENTE aqui

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.final_rule_linkage.missing_final_rule_candidate_ids == [_CANDIDATE_ID]
    assert report.final_rule_linkage.broken_candidate_ids == []
    assert report.final_rule_linkage.guardrail_rejected_candidate_ids == []


# --- 5C-SAFETY seccion 5: release_readiness_service usa PRODUCTIVE default --


def test_release_readiness_service_never_triggers_shadow_recomputation(
    base_run: Path, ground_truth_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`release_readiness_service.py` importa UNICAMENTE
    `compute_functional_validation_report` (productivo) -- nunca la
    variante `_from_promotion_assessment` -- sin ningun flag/parametro
    especial. Este sentinel envenena la variante shadow en su modulo de
    origen: si `compute_release_readiness_assessment` (release-facing,
    llamado por `release-readiness-assess`/`release-readiness-assess-
    dataset`) alguna vez la invocara por cualquier via, el test falla."""
    import altamira_extractor.pipeline.functional_validation_service as service_module

    def _poison(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "release readiness NUNCA debe usar PROMOTION_ASSESSMENT_SHADOW por defecto"
        )

    monkeypatch.setattr(
        service_module, "compute_functional_validation_report_from_promotion_assessment", _poison
    )
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.CANDIDATES_DETECTED))
    _write_candidates(base_run, [_candidate()])
    real_settings: Settings = load_settings()

    assessment = compute_release_readiness_assessment(
        base_run,
        _RUN_ID,
        policy_path=real_settings.release_readiness_policy_path,
        semantic_coverage_manifest_path=real_settings.semantic_coverage_manifest_path,
        ground_truth_path=ground_truth_path,
    )

    assert assessment.run_id == _RUN_ID


# --- 5C-SAFETY seccion 12: 06-candidates.json corrupto -----------------------


def test_malformed_06_candidates_raises_instead_of_reporting_zero_candidates(
    base_run: Path, ground_truth_path: Path
) -> None:
    """Un `06-candidates.json` presente pero corrupto NUNCA debe
    reportarse como `productive_candidate_count=0`/recall=0 -- eso lo
    haria indistinguible de una extraccion limpia que genuinamente no
    encontro nada (exactamente el defecto que P1 corrige, aplicado a
    corrupcion de artefacto en vez de recalculo V2)."""
    _write_run_state(base_run, stages=(PipelineStage.PARSED, PipelineStage.CANDIDATES_DETECTED))
    (base_run / "artifacts").mkdir(parents=True, exist_ok=True)
    (base_run / "artifacts" / "06-candidates.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(FunctionalValidationError, match="06-candidates.json"):
        compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)


# --- 5C-SAFETY seccion 13: 07-context/<hash>.json corrupto -------------------


def test_corrupted_context_file_is_artifact_chain_failure_not_missing_file(
    base_run: Path, ground_truth_path: Path
) -> None:
    """Un archivo de contexto PRESENTE pero corrupto (bytes no-JSON) debe
    contarse igual que un archivo ausente en `candidates_missing_context`
    -- nunca tratarse como "contexto disponible" solo porque el archivo
    existe en disco."""
    _write_run_state(
        base_run,
        stages=(
            PipelineStage.PARSED,
            PipelineStage.CANDIDATES_DETECTED,
            PipelineStage.CONTEXTS_BUILT,
        ),
    )
    _write_candidates(base_run, [_candidate()])
    context_dir = base_run / "artifacts" / "07-context"
    context_dir.mkdir(parents=True, exist_ok=True)
    filename = hashlib.sha256(_CANDIDATE_ID.encode("utf-8")).hexdigest() + ".json"
    (context_dir / filename).write_text("{not valid json", encoding="utf-8")

    report = compute_functional_validation_report(base_run, _RUN_ID, ground_truth_path)

    assert report.artifact_chain_integrity.candidates_missing_context == [_CANDIDATE_ID]
