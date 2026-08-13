"""Integracion con el parser Java REAL (Fase 10 de la ampliacion
semantica, `feat/controlled-candidate-promotion-plan`): construye un
paquete sintetico que demuestra, con canonical/candidatos V1/V2/
interprocedural 100% reales (JAR real, Neo4j efimero), las CINCO
`PromotionDisposition` en un unico run:

- `CALLER11` tiene un IF directo sobre `WS-COD-RETORNO` (Q0 y V2 lo ven
  igual -> V1 `BASELINE_V1` + V2/interprocedural `ALREADY_COVERED`, via
  `CALL 'CALLEE11' USING BY REFERENCE WS-STATUS RETURNING WS-COD-
  RETORNO` -- mismo patron de `test_candidate_promotion_assessment_
  integration.py::CALLER9`).
- El mismo `CALL` produce un candidato interprocedural
  `BY_REFERENCE_RULE` sobre `WS-STATUS`, sin equivalente V1/V2 real ->
  `REVIEW_REQUIRED`.
- Un segundo IF (`WS-SALDO2`) escribe `WS-COD-RETORNO2` via una cadena
  de copia intra-decision (`MOVE 'R002' TO WS-TEMP2` seguido, DENTRO DE
  LA MISMA RAMA, de `MOVE WS-TEMP2 TO WS-COD-RETORNO2`) -- invisible
  para Q0 (que solo seria un literal DIRECTO), visible para V2 via
  propagacion; `CALL 'CALLEE11B' RETURNING WS-COD-RETORNO2` produce el
  candidato interprocedural correspondiente -> AMBOS
  `READY_FOR_CONTROLLED_REVIEW` (mismo patron de `CALLER10` de la
  auditoria de cierre de Fase 9).
- `CALL 'STOPPER11' RETURNING WS-STOP-CODE` (`STOPPER11` hace `STOP
  RUN` incondicional) produce un candidato interprocedural genuino
  `BLOCKED`; el `V2_STATE_CHANGE` sobre `WS-TEMP2` (familia `UNKNOWN`)
  tambien resulta `BLOCKED` por politica.

El manifiesto de decisiones (sintetico pero valido) incluye: `APPROVE`
sobre un `READY_FOR_CONTROLLED_REVIEW` real, `REJECT` sobre el otro,
`DEFER` sobre `REVIEW_REQUIRED`, y NINGUN intento de aprobar `BLOCKED`.
El plan resultante demuestra `KEEP_BASELINE`, `SKIP_ALREADY_COVERED`,
`PROPOSE_SHADOW_PROMOTION`, `REJECT`, `DEFER` y `BLOCK` -- todos reales,
nunca fabricados manualmente como sustituto de la integracion.

No corre en la suite por defecto (marcado `integration`, requiere JAR
real via `require_jar` y Neo4j real). Si `CANDIDATES_DETECTED` no se
alcanza en el entorno, el test se salta explicando la limitacion."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from altamira_extractor.contracts.candidate_promotion_assessment import (
    PromotionDisposition,
)
from altamira_extractor.contracts.candidate_promotion_plan import (
    PromotionPlanAction,
    PromotionPlanItemStatus,
)
from altamira_extractor.contracts.candidate_promotion_review import ReviewEligibility
from altamira_extractor.pipeline.candidate_promotion_plan_service import (
    compute_candidate_promotion_plan_artifact,
    write_candidate_promotion_plan_artifact,
)
from altamira_extractor.pipeline.candidate_promotion_review_service import (
    compute_candidate_promotion_review_package,
    write_candidate_promotion_review_package,
)
from altamira_extractor.pipeline.runner import run_ingestion

from ..e2e_support import PARAM_DEMO_DDL, build_settings, regular_file_info, require_jar

pytestmark = pytest.mark.integration

_CALLER_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALLER11.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-SALDO            PIC 9(7)V99 VALUE 0.
       01  WS-SALDO2           PIC 9(7)V99 VALUE 0.
       01  WS-COD-RETORNO      PIC X(4) VALUE SPACES.
       01  WS-STATUS           PIC X(10) VALUE SPACES.
       01  WS-TEMP2            PIC X(4) VALUE SPACES.
       01  WS-COD-RETORNO2     PIC X(4) VALUE SPACES.
       01  WS-STOP-CODE        PIC X(4) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           IF WS-SALDO < 0
               MOVE 'R001' TO WS-COD-RETORNO
           END-IF.
           MOVE 'PENDING' TO WS-STATUS.
           CALL 'CALLEE11' USING BY REFERENCE WS-STATUS
               RETURNING WS-COD-RETORNO.
           IF WS-SALDO2 < 0
               MOVE 'R002' TO WS-TEMP2
               MOVE WS-TEMP2 TO WS-COD-RETORNO2
           END-IF.
           CALL 'CALLEE11B' RETURNING WS-COD-RETORNO2.
           CALL 'STOPPER11' RETURNING WS-STOP-CODE.
           STOP RUN.
"""

_CALLEE_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALLEE11.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       LINKAGE SECTION.
       01  LK-STATUS           PIC X(10).
       01  LK-COD-RETORNO      PIC X(4).
       PROCEDURE DIVISION USING LK-STATUS RETURNING LK-COD-RETORNO.
       MAIN-PARA.
           MOVE 'R001' TO LK-COD-RETORNO.
           MOVE 'APPROVED' TO LK-STATUS.
           GOBACK.
"""

_CALLEE_B_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALLEE11B.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       LINKAGE SECTION.
       01  LK-COD-RETORNO      PIC X(4).
       PROCEDURE DIVISION RETURNING LK-COD-RETORNO.
       MAIN-PARA.
           MOVE 'R002' TO LK-COD-RETORNO.
           GOBACK.
"""

_STOPPER_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. STOPPER11.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       LINKAGE SECTION.
       01  LK-STOP-CODE        PIC X(4).
       PROCEDURE DIVISION RETURNING LK-STOP-CODE.
       MAIN-PARA.
           STOP RUN.
"""

_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Fase10Synthetic"/>
  <operation logical-name="OP-FASE10" description="Auditoria Fase 10"/>
  <implementation version="1.0">
    <entry-program>CALLER11</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""


def _build_synthetic_package(zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(regular_file_info("manifest.xml"), _MANIFEST_XML)
        zf.writestr(regular_file_info("01-codigo/cobol/CALLER11.cbl"), _CALLER_CBL.encode())
        zf.writestr(regular_file_info("01-codigo/cobol/CALLEE11.cbl"), _CALLEE_CBL.encode())
        zf.writestr(regular_file_info("01-codigo/cobol/CALLEE11B.cbl"), _CALLEE_B_CBL.encode())
        zf.writestr(regular_file_info("01-codigo/cobol/STOPPER11.cbl"), _STOPPER_CBL.encode())
        zf.writestr(regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), PARAM_DEMO_DDL)


@pytest.fixture
def synthetic_zip(tmp_path: Path) -> Path:
    zip_path = tmp_path / "fase10-synthetic.zip"
    _build_synthetic_package(zip_path)
    return zip_path


def _run_pipeline(settings: object, zip_path: Path) -> tuple[Path, str, set[str]]:
    runs_dir = settings.runs_dir  # type: ignore[attr-defined]
    runs_dir.mkdir(parents=True, exist_ok=True)
    runs_before = {p.name for p in runs_dir.iterdir() if p.is_dir()}
    try:
        state = run_ingestion(zip_path, settings)  # type: ignore[arg-type]
        succeeded = {s.stage.value for s in state.stages if s.status.value == "SUCCEEDED"}
        return runs_dir / state.run_id, state.run_id, succeeded
    except Exception as exc:  # noqa: BLE001 -- ver docstring de modulo
        runs_after = {p.name for p in runs_dir.iterdir() if p.is_dir()}
        new_run_ids = runs_after - runs_before
        assert len(new_run_ids) == 1, (
            f"no se pudo localizar un unico run_dir nuevo tras la excepcion ({exc!r}): "
            f"{new_run_ids}"
        )
        run_id = next(iter(new_run_ids))
        run_dir = runs_dir / run_id
        run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        succeeded = {s["stage"] for s in run_json["stages"] if s["status"] == "SUCCEEDED"}
        assert "PARSED" in succeeded, f"PARSED no tuvo exito segun run.json en disco: {succeeded}"
        return run_dir, run_id, succeeded


def test_real_review_package_and_plan_demonstrate_all_dispositions(
    tmp_path: Path, synthetic_zip: Path
) -> None:
    """Fase 15B4-CANDIDATE-QUALITY-5E: enhanced_candidates_enabled=False
    explicito -- el escenario exige un baseline V1/Q0 exacto (1
    candidato) para razonar sobre las disposiciones de revision."""
    require_jar()
    settings = build_settings(tmp_path, enhanced_candidates_enabled=False)

    run_dir, run_id, succeeded_stages = _run_pipeline(settings, synthetic_zip)
    assert "PARSED" in succeeded_stages
    if "CANDIDATES_DETECTED" not in succeeded_stages:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible) -- "
            "el catalogo unificado real V1/V2/interprocedural no es verificable"
        )

    package = compute_candidate_promotion_review_package(run_dir, run_id)
    write_candidate_promotion_review_package(run_dir, package)

    assert package.summary.baseline_count == 1
    assert package.summary.already_covered_count >= 1
    assert package.summary.eligible_count >= 2
    assert package.summary.not_eligible_count >= 1
    assert package.summary.blocked_count >= 1

    ready_items = [
        item for item in package.review_items if item.eligibility == ReviewEligibility.ELIGIBLE
    ]
    assert len(ready_items) >= 2, "se esperaban al menos dos candidatos READY_FOR_CONTROLLED_REVIEW"
    review_required_item = next(
        item
        for item in package.review_items
        if item.disposition == PromotionDisposition.REVIEW_REQUIRED
    )
    blocked_items = [
        item for item in package.review_items if item.eligibility == ReviewEligibility.BLOCKED
    ]
    assert blocked_items

    review_package_hash = hashlib.sha256(package.to_stable_json().encode("utf-8")).hexdigest()

    manifest_payload = {
        "schema_version": "1.0",
        "review_package_hash": review_package_hash,
        "assessment_artifact_hash": package.assessment_artifact_hash,
        "run_id": run_id,
        "decisions": [
            {
                "decision_id": "decision::approve",
                "review_item_id": ready_items[0].review_item_id,
                "assessment_id": ready_items[0].assessment_id,
                "reference_id": ready_items[0].reference_id,
                "assessment_artifact_hash": package.assessment_artifact_hash,
                "decision": "APPROVE_FOR_SHADOW_PROMOTION",
                "reason_code": "EVIDENCE_CONFIRMED",
                "reviewer_reference": "analyst.one@example.com",
            },
            {
                "decision_id": "decision::reject",
                "review_item_id": ready_items[1].review_item_id,
                "assessment_id": ready_items[1].assessment_id,
                "reference_id": ready_items[1].reference_id,
                "assessment_artifact_hash": package.assessment_artifact_hash,
                "decision": "REJECT",
                "reason_code": "DUPLICATE_RULE",
                "reviewer_reference": "analyst.two@example.com",
            },
            {
                "decision_id": "decision::defer",
                "review_item_id": review_required_item.review_item_id,
                "assessment_id": review_required_item.assessment_id,
                "reference_id": review_required_item.reference_id,
                "assessment_artifact_hash": package.assessment_artifact_hash,
                "decision": "DEFER",
                "reason_code": "DEFERRED_FOR_DOMAIN_REVIEW",
                "reviewer_reference": "analyst.three@example.com",
            },
        ],
    }
    manifest_path = tmp_path / "decisions.json"
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

    plan = compute_candidate_promotion_plan_artifact(
        run_dir, run_id, decisions_path=str(manifest_path)
    )
    write_candidate_promotion_plan_artifact(run_dir, plan)

    actions_by_review_item = {item.review_item_id: item.action for item in plan.plan_items}
    statuses_by_review_item = {item.review_item_id: item.status for item in plan.plan_items}

    assert actions_by_review_item[ready_items[0].review_item_id] == (
        PromotionPlanAction.PROPOSE_SHADOW_PROMOTION
    )
    assert statuses_by_review_item[ready_items[0].review_item_id] == PromotionPlanItemStatus.VALID
    assert actions_by_review_item[ready_items[1].review_item_id] == PromotionPlanAction.REJECT
    assert actions_by_review_item[review_required_item.review_item_id] == (
        PromotionPlanAction.DEFER
    )

    baseline_item = next(
        item
        for item in plan.plan_items
        if item.assessment_disposition == PromotionDisposition.BASELINE_V1
    )
    assert baseline_item.action == PromotionPlanAction.KEEP_BASELINE

    already_covered_items = [
        item
        for item in plan.plan_items
        if item.assessment_disposition == PromotionDisposition.ALREADY_COVERED
    ]
    assert already_covered_items
    assert all(
        item.action == PromotionPlanAction.SKIP_ALREADY_COVERED for item in already_covered_items
    )

    for blocked_item_ref in blocked_items:
        plan_item = next(
            p for p in plan.plan_items if p.review_item_id == blocked_item_ref.review_item_id
        )
        assert plan_item.action == PromotionPlanAction.BLOCK
        assert plan_item.decision is None  # nunca se intento aprobar BLOCKED

    # Determinismo byte a byte con el mismo manifiesto.
    plan_again = compute_candidate_promotion_plan_artifact(
        run_dir, run_id, decisions_path=str(manifest_path)
    )
    assert plan.model_dump_json() == plan_again.model_dump_json()

    print("\n--- Tabla del plan (real, JAR real + Neo4j real) ---")
    for item in plan.plan_items:
        print(
            f"{item.source.value} | {item.source_candidate_id} | "
            f"{item.assessment_disposition.value} | {item.eligibility.value} | "
            f"decision={item.decision} | action={item.action.value} | "
            f"status={item.status.value} | reviewer={item.reviewer_reference}"
        )
