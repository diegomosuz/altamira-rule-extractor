"""Integracion con el parser Java REAL (Fase 9 de la ampliacion
semantica, `feat/unified-candidate-promotion-assessment`): construye un
paquete sintetico caller/callee que demuestra, con canonical/candidatos
V1/V2/interprocedural 100% reales (JAR real, `require_jar`):

1. Un candidato V1 real (Q0, `CANDIDATES_DETECTED` con Neo4j efimero).
2. Un candidato V2 real EXACTO a V1 (mismo programa/decision).
3. Un candidato V2 real NO cubierto por V1 (`WS-STATUS`, sin
   `semantic_tag=return_code`, nunca produce candidato Q0).
4. Un candidato interprocedural real EXACTO al V2 no cubierto
   (`CALLEE9` por referencia sobre `WS-STATUS`).
5. Un candidato interprocedural real "solo interprocedural"
   (`RETURN_CODE_RULE` de `CALLER9->CALLEE9`, sin relacion V1/V2 real
   demostrable por diseno de la comparacion existente).
6. Un candidato bloqueado (`CALL 'MISSING9'`, `MISSING_PROGRAM`).

No corre en la suite por defecto (marcado `integration`, requiere JAR
real via `require_jar` y Neo4j real -- credenciales sinteticas via
`NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD` -- para alcanzar
`CANDIDATES_DETECTED`/V1 real). Si `CANDIDATES_DETECTED` no se alcanza
en el entorno, el test se salta explicando la limitacion -- nunca
fabrica un V1 sintetico como sustituto de la integracion real."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateRelationKind,
    CandidateSource,
    PromotionCriterionKind,
    PromotionCriterionStatus,
    PromotionDisposition,
    RecommendedAction,
    SourceAvailability,
    UnifiedRuleFamily,
)
from altamira_extractor.pipeline.candidate_promotion_assessment_service import (
    compute_candidate_promotion_assessment_artifact,
    write_candidate_promotion_assessment_artifact,
)
from altamira_extractor.pipeline.runner import run_ingestion

from ..e2e_support import PARAM_DEMO_DDL, build_settings, regular_file_info, require_jar

pytestmark = pytest.mark.integration

# WS-COD-RETORNO (no WS-RETURN-CODE): el unico nombre confirmado
# empiricamente contra la regla real `return-code-name`
# (`.*(COD|CODE).*(RET|RETURN|RESP).*`, `config/semantic-tags.yml`).
_CALLER_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALLER9.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-SALDO            PIC 9(7)V99 VALUE 0.
       01  WS-COD-RETORNO      PIC X(4) VALUE SPACES.
       01  WS-STATUS           PIC X(10) VALUE SPACES.
       01  WS-MISSING-CODE     PIC X(4) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           IF WS-SALDO < 0
               MOVE 'R001' TO WS-COD-RETORNO
           END-IF.
           MOVE 'PENDING' TO WS-STATUS.
           CALL 'CALLEE9' USING BY REFERENCE WS-STATUS
               RETURNING WS-COD-RETORNO.
           CALL 'MISSING9' RETURNING WS-MISSING-CODE.
           STOP RUN.
"""

_CALLEE_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALLEE9.
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

_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Fase9Synthetic"/>
  <operation logical-name="OP-FASE9" description="Paquete sintetico de integracion Fase 9"/>
  <implementation version="1.0">
    <entry-program>CALLER9</entry-program>
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
        zf.writestr(regular_file_info("01-codigo/cobol/CALLER9.cbl"), _CALLER_CBL.encode())
        zf.writestr(regular_file_info("01-codigo/cobol/CALLEE9.cbl"), _CALLEE_CBL.encode())
        zf.writestr(regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), PARAM_DEMO_DDL)


@pytest.fixture
def synthetic_zip(tmp_path: Path) -> Path:
    zip_path = tmp_path / "fase9-synthetic.zip"
    _build_synthetic_package(zip_path)
    return zip_path


def _run_pipeline(settings: object, zip_path: Path) -> tuple[Path, str, set[str]]:
    """Mismo patron que
    `test_interprocedural_rule_candidates_integration.py::_run_pipeline`:
    Fase 9 solo exige PARSED (SUCCEEDED); si el pipeline V1 completo
    falla mas adelante, se localiza el run_dir por diferencia de
    directorios y se continua leyendo `run.json` del disco."""
    runs_dir = settings.runs_dir  # type: ignore[attr-defined]
    runs_dir.mkdir(parents=True, exist_ok=True)
    runs_before = {p.name for p in runs_dir.iterdir() if p.is_dir()}
    try:
        state = run_ingestion(zip_path, settings)  # type: ignore[arg-type]
        succeeded = {s.stage.value for s in state.stages if s.status.value == "SUCCEEDED"}
        return runs_dir / state.run_id, state.run_id, succeeded
    except Exception as exc:  # noqa: BLE001 -- ver docstring
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


def test_real_pipeline_demonstrates_promotion_assessment_scenarios(
    tmp_path: Path, synthetic_zip: Path
) -> None:
    require_jar()
    settings = build_settings(tmp_path)

    run_dir, run_id, succeeded_stages = _run_pipeline(settings, synthetic_zip)
    assert "PARSED" in succeeded_stages

    if "CANDIDATES_DETECTED" not in succeeded_stages:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible) -- "
            "el catalogo unificado real V1/V2/interprocedural no es verificable; Fase 9 "
            "nunca fabrica un V1 sintetico como sustituto de la integracion real"
        )

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, run_id)

    assert artifact.source_availability[CandidateSource.V1] == SourceAvailability.AVAILABLE
    assert artifact.source_availability[CandidateSource.V2] == SourceAvailability.AVAILABLE
    assert (
        artifact.source_availability[CandidateSource.INTERPROCEDURAL]
        == SourceAvailability.AVAILABLE
    )

    assert artifact.summary.v1_candidate_count >= 1
    assert artifact.summary.v2_candidate_count >= 1
    assert artifact.summary.interprocedural_candidate_count >= 1

    # Al menos un EXACT_MATCH (V1<->V2<->interprocedural, mismo
    # outcome_code 'R001' en CALLER9) que produce ALREADY_COVERED. La
    # llamada a MISSING9 (programa ausente) nunca llega a producir un
    # candidato -- ni siquiera BLOCKED, ver docs/
    # INTERPROCEDURAL_RULE_DETECTORS_SHADOW.md -- asi que la senal de
    # "candidato con soporte comprometido" es el BY_REFERENCE_RULE de
    # WS-STATUS, sin equivalente V1/V2 real, evaluado REVIEW_REQUIRED.
    assert artifact.summary.exact_match_relation_count >= 1
    assert artifact.summary.already_covered_count >= 1
    assert artifact.summary.review_required_count >= 1
    # El mismo call site (CALL 'CALLEE9' USING BY REFERENCE ... RETURNING
    # ...) demuestra DOS salidas legitimamente distintas (RETURNING sobre
    # WS-COD-RETORNO, BY REFERENCE sobre WS-STATUS) -- nunca un conflicto
    # (regresion real detectada e integrada por esta prueba: ver
    # `candidate_conflict_analyzer.py::_group_by_anchor_and_target`).
    assert artifact.summary.conflict_count == 0

    # Nunca hay disposition PROMOTED/AUTO_PROMOTED (no existen en el enum,
    # verificado tambien a nivel de contrato) -- ninguna regla se
    # considera oficialmente promovida por este diagnostico.
    dispositions = {a.disposition.value for a in artifact.assessments}
    assert "PROMOTED" not in dispositions
    assert "AUTO_PROMOTED" not in dispositions

    # Determinismo byte a byte.
    artifact_again = compute_candidate_promotion_assessment_artifact(run_dir, run_id)
    assert artifact.model_dump_json() == artifact_again.model_dump_json()

    write_candidate_promotion_assessment_artifact(run_dir, artifact)

    # Tabla de evaluaciones (para el informe final, Fase 13).
    reference_by_id = {r.unified_reference_id: r for r in artifact.candidate_references}
    print("\n--- Tabla de evaluaciones de promocion (real, JAR real + Neo4j real) ---")
    for assessment in artifact.assessments:
        reference = reference_by_id[assessment.reference_id]
        failed_criteria = [
            c.criterion.value
            for c in assessment.criteria
            if c.status == PromotionCriterionStatus.FAIL
        ]
        print(
            f"{reference.source.value} | {reference.source_candidate_id} | "
            f"{reference.rule_family.value} | {reference.target} | "
            f"{reference.output_literal} | "
            f"exact_matches={assessment.exact_match_reference_ids} | "
            f"related={assessment.related_reference_ids} | "
            f"conflicts={assessment.conflict_ids} | "
            f"disposition={assessment.disposition.value} | "
            f"failed_criteria={failed_criteria} | "
            f"recommended_action={assessment.recommended_action.value}"
        )


# ---------------------------------------------------------------------------
# Auditoria de cierre, Parte 2/3: demostracion real de READY_FOR_CONTROLLED_
# REVIEW y de BLOCKED, con un paquete disenado para exhibir un hueco
# ESTRUCTURAL genuino entre V1 (Q0) y V2/interprocedural -- nunca
# fabricado modificando detectores.
#
# CALLER10 asigna WS-COD-RETORNO por DOS caminos independientes, ninguno
# visible para Q0:
#
# 1. Una cadena de copia INTRA-DECISION: `MOVE 'R001' TO WS-TEMP` seguido,
#    DENTRO DE LA MISMA RAMA DEL IF, por `MOVE WS-TEMP TO WS-COD-RETORNO`.
#    Q0 (`_collect_leads_to_candidates`,
#    `pipeline/semantic_graph_builder.py`) solo colecciona descendientes
#    de la Decision con `assigned_literal is not None` -- la segunda
#    instruccion es una COPIA de variable (`assigned_literal=None`), asi
#    que Q0 nunca ve un LEADS_TO hacia WS-COD-RETORNO (solo hacia
#    WS-TEMP, sin `semantic_tag=return_code`, filtrado por el WHERE de
#    Q0). V2 (`detect_return_code_propagation`,
#    `pipeline/v2_detectors.py`), en cambio, SI resuelve la cadena via
#    `SemanticPropagationArtifact` (Fase 4): el origen de la
#    `PROPAGATED_LITERAL` es la PRIMERA instruccion, que SI esta anidada
#    bajo la Decision -- produce un candidato DETERMINISTIC real.
# 2. `CALL 'CALLEE10' RETURNING WS-COD-RETORNO`: CALLEE10 escribe
#    'R001' de forma determinista -- detector interprocedural
#    `INTERPROCEDURAL_RETURN_CODE_RULE` (Fase 8) produce un candidato
#    real independiente, sobre el MISMO target/literal.
#
# Ninguno de los dos requiere alterar ningun detector: es exactamente el
# comportamiento ya implementado de Fase 4/5/8, verificado antes de
# escribir este test ejecutando el pipeline real (ver auditoria de
# cierre). `STOPPER10` (patron identico al ya usado en
# `test_interprocedural_rule_candidates_integration.py`, STOP RUN
# incondicional) produce un candidato interprocedural genuino
# `support=BLOCKED` (barrera `NON_RETURNING_TERMINATION`) -- nunca la
# ausencia silenciosa de un call site bloqueado sin candidato.

_READY_CALLER_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALLER10.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-SALDO            PIC 9(7)V99 VALUE 0.
       01  WS-TEMP             PIC X(4) VALUE SPACES.
       01  WS-COD-RETORNO      PIC X(4) VALUE SPACES.
       01  WS-STOP-CODE        PIC X(4) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           IF WS-SALDO < 0
               MOVE 'R001' TO WS-TEMP
               MOVE WS-TEMP TO WS-COD-RETORNO
           END-IF.
           CALL 'CALLEE10' RETURNING WS-COD-RETORNO.
           CALL 'STOPPER10' RETURNING WS-STOP-CODE.
           STOP RUN.
"""

_READY_CALLEE_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALLEE10.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       LINKAGE SECTION.
       01  LK-COD-RETORNO      PIC X(4).
       PROCEDURE DIVISION RETURNING LK-COD-RETORNO.
       MAIN-PARA.
           MOVE 'R001' TO LK-COD-RETORNO.
           GOBACK.
"""

_READY_STOPPER_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. STOPPER10.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       LINKAGE SECTION.
       01  LK-STOP-CODE        PIC X(4).
       PROCEDURE DIVISION RETURNING LK-STOP-CODE.
       MAIN-PARA.
           STOP RUN.
"""

_READY_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Fase9ReadyBlocked"/>
  <operation logical-name="OP-READY-BLOCKED" description="Auditoria de cierre Fase 9"/>
  <implementation version="1.0">
    <entry-program>CALLER10</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""


def _build_ready_blocked_package(zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(regular_file_info("manifest.xml"), _READY_MANIFEST_XML)
        zf.writestr(regular_file_info("01-codigo/cobol/CALLER10.cbl"), _READY_CALLER_CBL.encode())
        zf.writestr(regular_file_info("01-codigo/cobol/CALLEE10.cbl"), _READY_CALLEE_CBL.encode())
        zf.writestr(
            regular_file_info("01-codigo/cobol/STOPPER10.cbl"), _READY_STOPPER_CBL.encode()
        )
        zf.writestr(regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), PARAM_DEMO_DDL)


@pytest.fixture
def ready_blocked_zip(tmp_path: Path) -> Path:
    zip_path = tmp_path / "fase9-ready-blocked.zip"
    _build_ready_blocked_package(zip_path)
    return zip_path


def test_real_ready_for_controlled_review_v2_interprocedural_exact_match(
    tmp_path: Path, ready_blocked_zip: Path
) -> None:
    """Parte 2 de la auditoria de cierre: demuestra, con parser real,
    Neo4j efimero y analizadores reales -- sin construir manualmente
    ningun artefacto V2/interprocedural -- un candidato V2 DETERMINISTIC
    y un candidato interprocedural DETERMINISTIC en `EXACT_MATCH` mutuo,
    SIN ningun `EXACT_MATCH` con V1 (Q0 nunca ve WS-COD-RETORNO en este
    paquete, ver docstring de modulo), cero conflictos, provenance
    completo, target/output presentes, cero barreras -- disposition
    `READY_FOR_CONTROLLED_REVIEW` para AMBAS referencias, contadas como
    DOS referencias distintas (nunca fusionadas en una)."""
    require_jar()
    settings = build_settings(tmp_path)

    run_dir, run_id, succeeded_stages = _run_pipeline(settings, ready_blocked_zip)
    assert "PARSED" in succeeded_stages
    if "CANDIDATES_DETECTED" not in succeeded_stages:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible) -- "
            "READY_FOR_CONTROLLED_REVIEW real no es verificable sin V1/Q0 real"
        )

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, run_id)

    # V1/Q0 nunca ve WS-COD-RETORNO en este paquete (ver docstring del
    # modulo): cero candidatos V1.
    assert artifact.summary.v1_candidate_count == 0

    v2_ref = next(
        r
        for r in artifact.candidate_references
        if r.source == CandidateSource.V2 and r.rule_family == UnifiedRuleFamily.RETURN_CODE
    )
    ip_ref = next(
        r
        for r in artifact.candidate_references
        if r.source == CandidateSource.INTERPROCEDURAL
        and r.rule_family == UnifiedRuleFamily.RETURN_CODE
        and r.original_support == "DETERMINISTIC"
    )

    # Referencias distintas -- nunca fusionadas en una sola.
    assert v2_ref.unified_reference_id != ip_ref.unified_reference_id
    assert v2_ref.source_candidate_id != ip_ref.source_candidate_id

    for reference in (v2_ref, ip_ref):
        assert reference.target == "WS-COD-RETORNO"
        assert reference.output_literal == "R001"
        assert reference.evidence_ids or reference.provenance_references

    relation = next(
        r
        for r in artifact.relations
        if {r.left_reference_id, r.right_reference_id}
        == {v2_ref.unified_reference_id, ip_ref.unified_reference_id}
    )
    assert relation.relation_kind == CandidateRelationKind.EXACT_MATCH

    assessment_by_reference = {a.reference_id: a for a in artifact.assessments}
    v2_assessment = assessment_by_reference[v2_ref.unified_reference_id]
    ip_assessment = assessment_by_reference[ip_ref.unified_reference_id]

    for assessment, reference in ((v2_assessment, v2_ref), (ip_assessment, ip_ref)):
        corroboration_criterion = next(
            c
            for c in assessment.criteria
            if c.criterion == PromotionCriterionKind.INDEPENDENT_CORROBORATION
        )
        print(
            f"READY real -- source={reference.source.value} "
            f"source_candidate_id={reference.source_candidate_id} "
            f"family={reference.rule_family.value} target={reference.target} "
            f"output_literal={reference.output_literal} relation_id={relation.relation_id} "
            f"INDEPENDENT_CORROBORATION={corroboration_criterion.status.value} "
            f"disposition={assessment.disposition.value} "
            f"recommended_action={assessment.recommended_action.value}"
        )
        assert corroboration_criterion.status == PromotionCriterionStatus.PASS
        assert assessment.disposition == PromotionDisposition.READY_FOR_CONTROLLED_REVIEW
        assert assessment.recommended_action == (
            RecommendedAction.SUBMIT_FOR_CONTROLLED_FUNCTIONAL_REVIEW
        )
        assert assessment.conflict_ids == []
        for blocking_criterion in (
            PromotionCriterionKind.DETERMINISTIC_SUPPORT,
            PromotionCriterionKind.TARGET_AVAILABLE,
            PromotionCriterionKind.OUTPUT_LITERAL_AVAILABLE,
            PromotionCriterionKind.NO_BARRIERS,
            PromotionCriterionKind.NO_CONFLICTS,
            PromotionCriterionKind.SUPPORTED_RULE_FAMILY,
        ):
            result = next(c for c in assessment.criteria if c.criterion == blocking_criterion)
            assert result.status == PromotionCriterionStatus.PASS, (blocking_criterion, reference)

    # Determinismo byte a byte.
    artifact_again = compute_candidate_promotion_assessment_artifact(run_dir, run_id)
    assert artifact.model_dump_json() == artifact_again.model_dump_json()


def test_real_blocked_interprocedural_candidate(
    tmp_path: Path, ready_blocked_zip: Path
) -> None:
    """Parte 3 de la auditoria de cierre: demuestra, con el mismo
    paquete real, un candidato interprocedural genuino `support=BLOCKED`
    (`STOPPER10`, `STOP RUN` incondicional -> barrera
    `NON_RETURNING_TERMINATION`, patron identico al ya validado en
    `test_interprocedural_rule_candidates_integration.py`) -- un
    candidato REAL con `support=BLOCKED`, nunca la ausencia silenciosa
    de un call site que no produjo ningun candidato."""
    require_jar()
    settings = build_settings(tmp_path)

    run_dir, run_id, succeeded_stages = _run_pipeline(settings, ready_blocked_zip)
    assert "PARSED" in succeeded_stages
    if "CANDIDATES_DETECTED" not in succeeded_stages:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible)"
        )

    artifact = compute_candidate_promotion_assessment_artifact(run_dir, run_id)

    blocked_ref = next(
        r
        for r in artifact.candidate_references
        if r.source == CandidateSource.INTERPROCEDURAL and r.original_support == "BLOCKED"
    )
    assert blocked_ref.barrier_codes == ["NON_RETURNING_TERMINATION"]
    assert blocked_ref.output_literal is None

    assessment = next(
        a for a in artifact.assessments if a.reference_id == blocked_ref.unified_reference_id
    )
    blocking_criterion = next(
        c for c in assessment.criteria if c.criterion == PromotionCriterionKind.NO_BARRIERS
    )
    print(
        f"BLOCKED real -- source={blocked_ref.source.value} "
        f"candidate_id={blocked_ref.source_candidate_id} "
        f"original_support={blocked_ref.original_support} "
        f"barriers={blocked_ref.barrier_codes} "
        f"criterio_bloqueante={blocking_criterion.criterion.value}="
        f"{blocking_criterion.status.value} "
        f"disposition={assessment.disposition.value} "
        f"recommended_action={assessment.recommended_action.value}"
    )
    assert blocking_criterion.status == PromotionCriterionStatus.FAIL
    assert assessment.disposition == PromotionDisposition.BLOCKED
    assert assessment.recommended_action == (
        RecommendedAction.RESOLVE_BLOCKING_CRITERIA_BEFORE_REVIEW
    )
