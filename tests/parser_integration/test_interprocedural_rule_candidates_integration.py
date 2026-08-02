"""Integracion con el parser Java REAL (Fase 8 de la ampliacion
semantica, `feat/interprocedural-rule-detectors-shadow`, item 12 de los
40 tests obligatorios; ampliado en la auditoria de cierre, Parte 5):
construye un paquete sintetico caller/callee/stopper que demuestra, con
canonical/call-linkage/propagation 100% reales (JAR real, `require_jar`):

1. RETURN_CODE_RULE deterministico (`CALL 'CALLEE8' ... RETURNING`).
2. BY_REFERENCE_RULE deterministico (`CALL 'CALLEE8' USING BY REFERENCE
   WS-STATUS`, PENDING->APPROVED).
3. STATE_TRANSITION_RULE deterministico -- requiere `semantic_tag=
   status` en WS-STATUS, que `config/semantic-tags.yml` actualmente NO
   asigna por ninguna regla real (ninguna de sus reglas mapea a
   status/status_flag, solo a return_code/sqlcode/amount/.../customer_
   segment) -- se documenta esta limitacion y se inyecta, UNICAMENTE
   para esta demostracion, un `SemanticEnrichmentArtifact` real (mismos
   program_id/qualified_name que el run genuino, nunca fabricados) con
   ese tag, ya que Fase 8 no puede alterar `config/semantic-tags.yml`
   (fuera de alcance, config productiva compartida).
4. INTERPROCEDURAL_ONLY (BY_REFERENCE_RULE/STATE_TRANSITION_RULE no
   tienen equivalente V1/V2 real).
5. MATCHED_V1 (Q0 detecta un candidato real con el MISMO outcome_code
   'R001' en CALLER8, vecino intraparrafo de la version interprocedural
   -- mismo patron IF/MOVE empiricamente confirmado por
   `tests/e2e_support.py::PROGRAM_SOURCE`/`DECISION_EVIDENCE_ID`).
6. CALL dinamico bloqueado (`CALL WS-PROGRAM-NAME`).
7. Programa ausente bloqueado (`CALL 'MISSING8'`).
8. STOP RUN bloqueado (`CALL 'STOPPER8' RETURNING ...`, STOPPER8 nunca
   retorna control).
9. Self-call bloqueado (`CALL 'CALLER8' RETURNING ...`, recursion).
10. Determinismo byte a byte (dos ejecuciones consecutivas).

No corre en la suite por defecto (marcado `integration`, requiere JAR
real vía `require_jar`, y Neo4j real -- credenciales sinteticas via
`NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD` -- para alcanzar
CANDIDATES_DETECTED y obtener un `CandidateArtifact` V1 real)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.contracts.canonical import CanonicalProgram
from altamira_extractor.contracts.interprocedural_rule_candidates import (
    InterproceduralComparisonStatus,
    InterproceduralRuleType,
)
from altamira_extractor.contracts.semantic_enrichment import (
    DataItemSemanticTag,
    SemanticEnrichmentArtifact,
    SemanticTagRuleMatch,
)
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.interprocedural_rule_candidates_service import (
    compute_interprocedural_rule_candidates_artifact,
    interprocedural_rule_candidates_artifact_path,
    write_interprocedural_rule_candidates_artifact,
)
from altamira_extractor.pipeline.runner import run_ingestion

from ..e2e_support import PARAM_DEMO_DDL, build_settings, regular_file_info, require_jar

pytestmark = pytest.mark.integration

_CLI_RUNNER = CliRunner()

# WS-COD-RETORNO (no WS-RETURN-CODE): el unico nombre confirmado
# empiricamente contra la regla real `return-code-name`
# (`.*(COD|CODE).*(RET|RETURN|RESP).*`, `config/semantic-tags.yml`) --
# "RETURN-CODE" NO coincide (RETURN precede a CODE, orden invertido).
_CALLER_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALLER8.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-SALDO            PIC 9(7)V99 VALUE 0.
       01  WS-COD-RETORNO      PIC X(4) VALUE SPACES.
       01  WS-STATUS           PIC X(10) VALUE SPACES.
       01  WS-PROGRAM-NAME     PIC X(8) VALUE SPACES.
       01  WS-STOP-CODE        PIC X(4) VALUE SPACES.
       01  WS-SELF-CODE        PIC X(4) VALUE SPACES.
       01  WS-MISSING-CODE     PIC X(4) VALUE SPACES.
       01  WS-DYNAMIC-CODE     PIC X(4) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM CHECK-SALDO-PARA.
           MOVE 'PENDING' TO WS-STATUS.
           CALL 'CALLEE8' USING BY REFERENCE WS-STATUS
               RETURNING WS-COD-RETORNO.
           CALL 'MISSING8' RETURNING WS-MISSING-CODE.
           CALL WS-PROGRAM-NAME RETURNING WS-DYNAMIC-CODE.
           CALL 'STOPPER8' RETURNING WS-STOP-CODE.
           CALL 'CALLER8' RETURNING WS-SELF-CODE.
           STOP RUN.
       CHECK-SALDO-PARA.
           IF WS-SALDO < 0
               MOVE 'R001' TO WS-COD-RETORNO
           END-IF.
"""

_CALLEE_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. CALLEE8.
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

_STOPPER_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. STOPPER8.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       LINKAGE SECTION.
       01  LK-STOP-CODE        PIC X(4).
       PROCEDURE DIVISION RETURNING LK-STOP-CODE.
       MAIN-PARA.
           STOP RUN.
"""

_ISOLATED_CALLER_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. ISOLATED8.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-ISO-CODE         PIC X(4) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           CALL 'ISOLATEE8' RETURNING WS-ISO-CODE.
           STOP RUN.
"""

_ISOLATED_CALLEE_CBL = """\
       IDENTIFICATION DIVISION.
       PROGRAM-ID. ISOLATEE8.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       LINKAGE SECTION.
       01  LK-ISO-CODE         PIC X(4).
       PROCEDURE DIVISION RETURNING LK-ISO-CODE.
       MAIN-PARA.
           MOVE 'Z999' TO LK-ISO-CODE.
           GOBACK.
"""

_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Fase8Synthetic"/>
  <operation logical-name="OP-FASE8" description="Paquete sintetico de integracion Fase 8"/>
  <implementation version="1.0">
    <entry-program>CALLER8</entry-program>
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
        zf.writestr(regular_file_info("01-codigo/cobol/CALLER8.cbl"), _CALLER_CBL.encode())
        zf.writestr(regular_file_info("01-codigo/cobol/CALLEE8.cbl"), _CALLEE_CBL.encode())
        zf.writestr(regular_file_info("01-codigo/cobol/STOPPER8.cbl"), _STOPPER_CBL.encode())
        zf.writestr(
            regular_file_info("01-codigo/cobol/ISOLATED8.cbl"), _ISOLATED_CALLER_CBL.encode()
        )
        zf.writestr(
            regular_file_info("01-codigo/cobol/ISOLATEE8.cbl"), _ISOLATED_CALLEE_CBL.encode()
        )
        zf.writestr(regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), PARAM_DEMO_DDL)


@pytest.fixture
def synthetic_zip(tmp_path: Path) -> Path:
    zip_path = tmp_path / "fase8-synthetic.zip"
    _build_synthetic_package(zip_path)
    return zip_path


def _run_pipeline(settings: object, zip_path: Path) -> tuple[Path, str, set[str]]:
    """Corre `run_ingestion` hasta donde llegue. Fase 8 solo exige PARSED
    (SUCCEEDED); si el pipeline V1 completo falla mas adelante (p. ej.
    falta el fake LLM para RULE_DRAFTS_GENERATED, irrelevante para este
    test), se localiza el run_dir por diferencia de directorios y se
    continua leyendo `run.json` del disco -- mismo patron que
    `test_catherine_no_regression_integration.py`. Devuelve tambien el
    conjunto de stages SUCCEEDED, para condicionar la comparacion V1."""
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


def _inject_synthetic_enrichment_for_ws_status(run_dir: Path, run_id: str) -> None:
    """Escribe `artifacts/03b-semantic-enrichment.json` con
    `semantic_tag=status` para WS-STATUS de CALLER8, usando el
    `program_id` REAL (extraido del `CanonicalProgram` genuino ya
    persistido por el run, nunca inventado) -- unico input sintetizado
    de este test, documentado en el docstring del modulo: ninguna regla
    de `config/semantic-tags.yml` asigna status/status_flag hoy, y Fase
    8 no puede modificar esa configuracion productiva compartida."""
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    (canonical_path,) = [p for p in canonical_dir.rglob("*.json") if p.stem.startswith("CALLER8")]
    program = CanonicalProgram.model_validate_json(canonical_path.read_text(encoding="utf-8"))
    program_id = f"program::AR::Fase8Synthetic::CALLER8::1.0::{program.source_hash[:12]}"
    tag = DataItemSemanticTag(
        data_item_id=f"{program_id}::data::WS-STATUS",
        program_id=program_id,
        original_name="WS-STATUS",
        qualified_name="WS-STATUS",
        semantic_tag="status",
        semantic_confidence=0.9,
        evidence=[
            SemanticTagRuleMatch(rule_id="fase8-audit-synthetic", tag="status", base_confidence=0.9)
        ],
    )
    enrichment = SemanticEnrichmentArtifact(
        run_id=run_id,
        source_package_hash=program.source_package_hash,
        semantic_tags_config_hash="a" * 64,
        domain_glossary_config_hash="a" * 64,
        data_item_tags=[tag],
    )
    atomic_write_json(run_dir / "artifacts" / "03b-semantic-enrichment.json", enrichment)


def test_real_parser_demonstrates_all_ten_required_scenarios(
    tmp_path: Path, synthetic_zip: Path
) -> None:
    require_jar()
    settings = build_settings(tmp_path)

    run_dir, run_id, succeeded_stages = _run_pipeline(settings, synthetic_zip)
    assert "PARSED" in succeeded_stages

    # Item 3 (STATE_TRANSITION_RULE) requiere SemanticEnrichment con
    # semantic_tag=status -- inyectado UNICAMENTE aqui, ver docstring.
    _inject_synthetic_enrichment_for_ws_status(run_dir, run_id)

    artifact = compute_interprocedural_rule_candidates_artifact(run_dir, run_id)

    by_rule_type: dict[InterproceduralRuleType, list] = {}
    for candidate in artifact.candidates:
        by_rule_type.setdefault(candidate.rule_type, []).append(candidate)

    # 1. RETURN_CODE_RULE deterministico (dos: CALLER8->CALLEE8 y el par
    # aislado ISOLATED8->ISOLATEE8, usado para demostrar INTERPROCEDURAL_ONLY).
    assert InterproceduralRuleType.RETURN_CODE_RULE in by_rule_type
    return_code_candidates = by_rule_type[InterproceduralRuleType.RETURN_CODE_RULE]
    returning = next(c for c in return_code_candidates if c.caller_program == "CALLER8")
    assert returning.output_literal == "R001"
    assert returning.callee_program == "CALLEE8"
    isolated_returning = next(c for c in return_code_candidates if c.caller_program == "ISOLATED8")
    assert isolated_returning.output_literal == "Z999"
    assert isolated_returning.callee_program == "ISOLATEE8"

    # 2. BY_REFERENCE_RULE deterministico.
    assert InterproceduralRuleType.BY_REFERENCE_RULE in by_rule_type
    by_reference = by_rule_type[InterproceduralRuleType.BY_REFERENCE_RULE][0]
    assert by_reference.input_literal == "PENDING"
    assert by_reference.output_literal == "APPROVED"

    # 3. STATE_TRANSITION_RULE deterministico (con enrichment sintetico).
    assert InterproceduralRuleType.STATE_TRANSITION_RULE in by_rule_type
    state_transition = by_rule_type[InterproceduralRuleType.STATE_TRANSITION_RULE][0]
    assert state_transition.input_literal == "PENDING"
    assert state_transition.output_literal == "APPROVED"

    # 6/7/9. CALL dinamico / programa ausente / self-call bloqueados: NUNCA
    # generan candidato (ni siquiera BLOCKED) -- se verifican via
    # diagnostics, nunca desaparecen en silencio.
    blocked_diagnostics = [
        d for d in artifact.diagnostics if d.startswith("BLOCKED_CALL_SITE_NO_CANDIDATE::")
    ]
    barriers_seen = {d.rsplit("::", 1)[-1] for d in blocked_diagnostics}
    assert "DYNAMIC_CALL" in barriers_seen
    assert "MISSING_PROGRAM" in barriers_seen
    assert "RECURSION" in barriers_seen

    # 8. STOP RUN bloqueado: candidato BLOCKED genuino (patron RETURNING +
    # certeza estructural).
    blocked_candidates = [c for c in artifact.candidates if c.support.value == "BLOCKED"]
    assert len(blocked_candidates) == 1
    assert blocked_candidates[0].caller_program == "CALLER8"
    assert blocked_candidates[0].callee_program == "STOPPER8"

    # 4/5. Al menos una relacion V1 (MATCHED_V1, Q0 real) y al menos un
    # INTERPROCEDURAL_ONLY -- solo verificable si CANDIDATES_DETECTED (V1
    # real) se alcanzo; documentado como limitacion si no.
    if "CANDIDATES_DETECTED" in succeeded_stages:
        matched_v1 = [
            c
            for c in artifact.comparisons
            if c.status == InterproceduralComparisonStatus.MATCHED_V1
        ]
        assert matched_v1, "se esperaba al menos una comparacion MATCHED_V1 con Q0 real"
        interprocedural_only = [
            c
            for c in artifact.comparisons
            if c.status == InterproceduralComparisonStatus.INTERPROCEDURAL_ONLY
        ]
        assert interprocedural_only, "se esperaba al menos un INTERPROCEDURAL_ONLY"
    else:
        pytest.skip(
            "CANDIDATES_DETECTED no se alcanzo en este entorno (Neo4j no disponible) -- "
            "MATCHED_V1/INTERPROCEDURAL_ONLY reales no verificables; ver limitacion "
            "documentada en el informe de auditoria"
        )

    # 10. Determinismo byte a byte.
    artifact_again = compute_interprocedural_rule_candidates_artifact(run_dir, run_id)
    assert artifact.model_dump_json() == artifact_again.model_dump_json()

    write_interprocedural_rule_candidates_artifact(run_dir, artifact)
    report_path = interprocedural_rule_candidates_artifact_path(run_dir)
    assert report_path.is_file()

    first_bytes = report_path.read_bytes()
    artifact_third = compute_interprocedural_rule_candidates_artifact(run_dir, run_id)
    write_interprocedural_rule_candidates_artifact(run_dir, artifact_third)
    second_bytes = report_path.read_bytes()
    assert first_bytes == second_bytes

    # Tabla de candidatos (para el informe de auditoria, Parte 5).
    print("\n--- Tabla de candidatos (real, JAR real) ---")
    for candidate in artifact.candidates:
        comparison = next(
            c
            for c in artifact.comparisons
            if c.interprocedural_candidate_id == candidate.candidate_id
        )
        print(
            f"{candidate.candidate_id} | {candidate.detector} | {candidate.rule_type.value} | "
            f"{candidate.support.value} | {candidate.caller_program} | "
            f"{candidate.callee_program} | "
            f"{candidate.target} | {candidate.input_literal} | {candidate.output_literal} | "
            f"v1={comparison.v1_relation.value}({comparison.v1_candidate_id}) | "
            f"v2={comparison.v2_relation.value}({comparison.v2_candidate_id}) | "
            f"facts={[fid for e in candidate.evidence for fid in e.propagation_fact_ids]} | "
            f"bindings={[e.binding_id for e in candidate.evidence if e.binding_id]} | "
            f"call_site={candidate.call_site_id}"
        )
    print("--- Tabla de call sites bloqueados sin candidato ---")
    for diagnostic in artifact.diagnostics:
        print(diagnostic)


def test_cli_end_to_end_against_real_parser_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, synthetic_zip: Path
) -> None:
    require_jar()
    settings = build_settings(tmp_path)
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)

    run_dir, run_id, _ = _run_pipeline(settings, synthetic_zip)

    result = _CLI_RUNNER.invoke(cli_module.app, ["interprocedural-candidates-shadow", run_id])
    assert result.exit_code == 0, result.stderr
    assert f"run_id: {run_id}" in result.stdout
    assert (run_dir / "diagnostics" / "interprocedural-rule-candidates-shadow.json").is_file()
