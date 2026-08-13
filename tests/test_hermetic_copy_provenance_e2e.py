"""E2E hermetico productivo del fix P0-COPY-CANDIDATE-CRASH (Fase
15B4-CANDIDATE-QUALITY-5A): "package sintetico versionado -> parser Java
real -> canonical -> semantic graph en Neo4j real efimero -> V1 (y V2
cuando aplica) -> 06-candidates.json -> ContextPackage -> RuleDraft ->
guardrail -> 10-rules", ejercitando exclusivamente `run_ingestion` y los
stages productivos reales -- nunca un pipeline alternativo.

Contexto del defecto corregido: cualquier programa COBOL que contenga
`COPY` (en cualquier punto, incluso solo en WORKING-STORAGE) hacia que
TODO el programa se extrajera con `location_kind=PREPROCESSED_STREAM`
(ProLeap no puede atribuir cada linea post-expansion a su archivo fisico
de origen). Antes de esta fase, `RuleCandidate.source_file` era un
`RelativePath` obligatorio: un candidato Q0/V1 anclado a un Paragraph
con `source_file=None` producia un `ValidationError` sin capturar en
`candidate_detector.py`, que abortaba la etapa CANDIDATES_DETECTED por
completo (la regla se perdia, el run terminaba en FAILED). El fix hace
que `source_file` sea legitimamente `None` (nunca un valor inventado:
ni cadena vacia, ni el archivo principal del programa emparejado con un
`line_start` potencialmente desplazado por la expansion de COPY) en
`RuleCandidate`/`EvidenceEntry`/`CodeSliceEntry`, con el resto del
pipeline preservando la regla end-to-end.

Hermetismo: identico a `test_hermetic_enhanced_candidates_e2e.py`."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.context_package import ContextPackage
from altamira_extractor.contracts.enums import GuardrailVerdict
from altamira_extractor.contracts.guardrail_candidate import GuardrailCandidateArtifact
from altamira_extractor.contracts.rule_draft import RuleDraft
from altamira_extractor.pipeline.runner import run_ingestion

from .e2e_support import require_jar
from .hermetic_llm_support import build_hermetic_settings, hermetic_llm_and_network_guard

pytestmark = pytest.mark.integration


def _artifact_filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


def _regular_file_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


_PARAM_DEMO_DDL = b"CREATE TABLE PARAM_DEMO (ID INT);\n"


def _settings(tmp_path: Path, *, run_label: str, enhanced: bool = False) -> Settings:
    return build_hermetic_settings(
        tmp_path / f"hermetic_data_{run_label}",
        NEO4J_URI=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        NEO4J_USER=os.environ.get("NEO4J_USER", "neo4j"),
        NEO4J_PASSWORD=os.environ.get("NEO4J_PASSWORD", "neo4j"),
        NEO4J_DATABASE=os.environ.get("NEO4J_DATABASE", "neo4j"),
        enhanced_candidates_enabled=enhanced,
    )


# ---------------------------------------------------------------------------
# Caso A/B/E/G: COPY + RETURN_CODE V1 -> 06..10 completo, source_file=None
# honesto, guardrail EVIDENCE_VALIDATED (antes: CANDIDATES_DETECTED FAILED).
# ---------------------------------------------------------------------------

_COPY_RC_MANIFEST = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-COPY-RC" description="15B4-CANDIDATE-QUALITY-5A"/>
  <implementation version="1.0">
    <entry-program>COPYRC01</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

_COPY_RC_MAIN_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. COPYRC01.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-SALDO PIC 9(7)V99 VALUE 0.
       COPY CPYRETCD.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM CHECK-SALDO-PARA.
           GOBACK.
       CHECK-SALDO-PARA.
           IF WS-SALDO < 0
               MOVE 'R001' TO WS-COD-RETORNO
           END-IF.
"""

_CPYRETCD_SOURCE = b"""      *CPYRETCD -- copybook con el data item de codigo de retorno
       01 WS-COD-RETORNO PIC X(4) VALUE SPACES.
"""


def _write_copy_return_code_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(_regular_file_info("manifest.xml"), _COPY_RC_MANIFEST)
        zf.writestr(_regular_file_info("01-codigo/cobol/COPYRC01.cbl"), _COPY_RC_MAIN_SOURCE)
        zf.writestr(_regular_file_info("01-codigo/copybooks/CPYRETCD.cpy"), _CPYRETCD_SOURCE)
        zf.writestr(_regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), _PARAM_DEMO_DDL)
    return path


def test_copy_with_return_code_reaches_completed_with_honest_null_source_file(
    tmp_path: Path,
) -> None:
    require_jar()
    zip_path = _write_copy_return_code_zip(tmp_path / "copy_rc.zip")
    settings = _settings(tmp_path, run_label="copy_rc")

    with hermetic_llm_and_network_guard():
        state = run_ingestion(source_zip=zip_path, settings=settings)

    assert state.current_stage.value == "COMPLETED", [
        (s.stage.value, s.status.value, s.error) for s in state.stages
    ]
    assert all(s.status.value != "FAILED" for s in state.stages)

    run_dir = settings.runs_dir / state.run_id
    artifact = CandidateArtifact.model_validate_json(
        (run_dir / "artifacts" / "06-candidates.json").read_text(encoding="utf-8")
    )
    assert len(artifact.candidates) == 1, artifact.candidates
    candidate = artifact.candidates[0]
    assert candidate.paragraph_name == "CHECK-SALDO-PARA"
    assert candidate.rule_family == UnifiedRuleFamily.RETURN_CODE
    assert candidate.outcome_code == "R001"
    # Caso E: nunca se fabrica un source_file de reemplazo (ni el
    # archivo principal del programa, ni una cadena vacia).
    assert candidate.source_file is None
    assert candidate.line_start >= 1

    # No-silent-loss: el candidato con source_file=None queda trazado.
    assert any(
        candidate.candidate_id in w and "sin source_file disponible" in w
        for w in artifact.warnings
    ), artifact.warnings

    filename = _artifact_filename(candidate.candidate_id)
    context_path = run_dir / "artifacts" / "07-context" / filename
    assert context_path.is_file()
    package = ContextPackage.model_validate_json(context_path.read_text(encoding="utf-8"))
    assert package.candidate.candidate_id == candidate.candidate_id
    # scope.source_file proviene de Program.source_file (siempre
    # conocido, ni siquiera en programas con COPY) -- nunca None.
    assert package.scope.source_file == "01-codigo/cobol/COPYRC01.cbl"
    # code_slice/evidence del propio Paragraph SI son None (honesto).
    assert any(entry.source_file is None for entry in package.code_slice)
    assert any(entry.source_file is None for entry in package.evidence)

    draft_path = run_dir / "artifacts" / "08-rule-drafts" / filename
    assert draft_path.is_file()
    RuleDraft.model_validate_json(draft_path.read_text(encoding="utf-8"))

    # Caso G: guardrail valida la evidencia igual (sin atribucion falsa).
    guardrail_path = run_dir / "artifacts" / "09-guardrails" / filename
    assert guardrail_path.is_file()
    guardrail_candidate = GuardrailCandidateArtifact.model_validate_json(
        guardrail_path.read_text(encoding="utf-8")
    )
    assert guardrail_candidate.guardrail_report.verdict == GuardrailVerdict.EVIDENCE_VALIDATED

    rules_manifest_path = run_dir / "artifacts" / "10-rules" / "rules-manifest.json"
    assert rules_manifest_path.is_file()
    rules_manifest = json.loads(rules_manifest_path.read_text(encoding="utf-8"))
    assert rules_manifest["rule_count"] == 1
    assert rules_manifest["records"][0]["candidate_id"] == candidate.candidate_id


# ---------------------------------------------------------------------------
# Caso C: COPY presente pero SIN patron de regla -- debe completar
# normalmente con cero candidatos, nunca fabricar uno.
# ---------------------------------------------------------------------------

_COPY_NORULE_MANIFEST = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-COPY-NORULE" description="15B4-CANDIDATE-QUALITY-5A"/>
  <implementation version="1.0">
    <entry-program>COPYNR01</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

_COPY_NORULE_MAIN_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. COPYNR01.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-SALDO PIC 9(7)V99 VALUE 0.
       COPY CPYAUX01.
       PROCEDURE DIVISION.
       MAIN-PARA.
           ADD 1 TO WS-SALDO.
           GOBACK.
"""

_CPYAUX01_SOURCE = b"""      *CPYAUX01 -- copybook auxiliar sin dato de codigo de retorno
       01 WS-AUX PIC 9(5) VALUE 0.
"""


def _write_copy_no_rule_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(_regular_file_info("manifest.xml"), _COPY_NORULE_MANIFEST)
        zf.writestr(_regular_file_info("01-codigo/cobol/COPYNR01.cbl"), _COPY_NORULE_MAIN_SOURCE)
        zf.writestr(_regular_file_info("01-codigo/copybooks/CPYAUX01.cpy"), _CPYAUX01_SOURCE)
        zf.writestr(_regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), _PARAM_DEMO_DDL)
    return path


def test_copy_without_rule_pattern_completes_with_zero_candidates(tmp_path: Path) -> None:
    require_jar()
    zip_path = _write_copy_no_rule_zip(tmp_path / "copy_norule.zip")
    settings = _settings(tmp_path, run_label="copy_norule")

    with hermetic_llm_and_network_guard():
        state = run_ingestion(source_zip=zip_path, settings=settings)

    assert state.current_stage.value == "COMPLETED", [
        (s.stage.value, s.status.value, s.error) for s in state.stages
    ]
    run_dir = settings.runs_dir / state.run_id
    artifact = CandidateArtifact.model_validate_json(
        (run_dir / "artifacts" / "06-candidates.json").read_text(encoding="utf-8")
    )
    assert artifact.candidates == []


# ---------------------------------------------------------------------------
# Caso F: LEVEL_88 return-code definido DENTRO del copybook, ejercido via
# SET <condicion-88> TO TRUE -- enhanced_candidates_enabled=True.
# ---------------------------------------------------------------------------

_COPY_L88_MANIFEST = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-COPY-L88" description="15B4-CANDIDATE-QUALITY-5A"/>
  <implementation version="1.0">
    <entry-program>COPYL801</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

_COPY_L88_MAIN_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. COPYL801.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-SALDO PIC 9(7)V99 VALUE 0.
       COPY CPYL88CD.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM CHECK-INVALIDO-PARA.
           GOBACK.
       CHECK-INVALIDO-PARA.
           IF WS-SALDO > 999999
               SET COD-SALDO-INVALIDO TO TRUE
           END-IF.
"""

_CPYL88CD_SOURCE = b"""      *CPYL88CD -- copybook con return-code y su condicion 88
       01 WS-COD-RETORNO PIC X(4) VALUE SPACES.
          88 COD-SALDO-INVALIDO VALUE 'R003'.
"""


def _write_copy_level88_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(_regular_file_info("manifest.xml"), _COPY_L88_MANIFEST)
        zf.writestr(_regular_file_info("01-codigo/cobol/COPYL801.cbl"), _COPY_L88_MAIN_SOURCE)
        zf.writestr(_regular_file_info("01-codigo/copybooks/CPYL88CD.cpy"), _CPYL88CD_SOURCE)
        zf.writestr(_regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), _PARAM_DEMO_DDL)
    return path


def test_copy_with_level_88_return_code_reaches_completed(tmp_path: Path) -> None:
    require_jar()
    zip_path = _write_copy_level88_zip(tmp_path / "copy_l88.zip")
    settings = _settings(tmp_path, run_label="copy_l88", enhanced=True)

    with hermetic_llm_and_network_guard():
        state = run_ingestion(source_zip=zip_path, settings=settings)

    assert state.current_stage.value == "COMPLETED", [
        (s.stage.value, s.status.value, s.error) for s in state.stages
    ]
    run_dir = settings.runs_dir / state.run_id
    artifact = CandidateArtifact.model_validate_json(
        (run_dir / "artifacts" / "06-candidates.json").read_text(encoding="utf-8")
    )
    assert len(artifact.candidates) == 1, artifact.candidates
    candidate = artifact.candidates[0]
    assert candidate.rule_family == UnifiedRuleFamily.LEVEL_88_RETURN_CODE
    assert candidate.candidate_source == CandidateSource.V2
    assert candidate.outcome_code == "R003"
    assert candidate.source_file is None
    assert candidate.evidence_ids != []

    filename = _artifact_filename(candidate.candidate_id)
    guardrail_path = run_dir / "artifacts" / "09-guardrails" / filename
    assert guardrail_path.is_file()
    guardrail_candidate = GuardrailCandidateArtifact.model_validate_json(
        guardrail_path.read_text(encoding="utf-8")
    )
    assert guardrail_candidate.guardrail_report.verdict == GuardrailVerdict.EVIDENCE_VALIDATED


# ---------------------------------------------------------------------------
# Fase 15B4-CANDIDATE-QUALITY-5A-SAFETY, seccion 5: paridad de diagnostico
# V1/V2 sin source_file para las 4 familias productivas -- STATE_TRANSITION
# y CALCULATION (RETURN_CODE/LEVEL_88_RETURN_CODE ya cubiertas arriba).
# ---------------------------------------------------------------------------

_COPY_FAMPARITY_MANIFEST = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-COPY-FAMPARITY" description="15B4-CANDIDATE-QUALITY-5A-SAFETY"/>
  <implementation version="1.0">
    <entry-program>COPYFP01</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

_COPY_FAMPARITY_MAIN_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. COPYFP01.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-SALDO PIC 9(7)V99 VALUE 0.
       01 WS-MONTO PIC 9(7)V99 VALUE 100.
       01 WS-TASA PIC 9(3)V99 VALUE 5.
       01 WS-COMISION PIC 9(7)V99 VALUE 0.
       COPY CPYFPCD.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM CHECK-TRANSICION-PARA.
           PERFORM CHECK-CALCULO-PARA.
           GOBACK.
       CHECK-TRANSICION-PARA.
           IF WS-SALDO < -1000
               MOVE 'R' TO WS-ESTADO-OPERACION
           END-IF.
       CHECK-CALCULO-PARA.
           IF WS-SALDO < -3000
               COMPUTE WS-COMISION = WS-MONTO * WS-TASA
           END-IF.
"""

_CPYFPCD_SOURCE = b"""      *CPYFPCD -- copybook con el estado de operacion (status)
       01 WS-ESTADO-OPERACION PIC X(1) VALUE SPACES.
"""


def _write_copy_family_parity_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(_regular_file_info("manifest.xml"), _COPY_FAMPARITY_MANIFEST)
        zf.writestr(
            _regular_file_info("01-codigo/cobol/COPYFP01.cbl"), _COPY_FAMPARITY_MAIN_SOURCE
        )
        zf.writestr(_regular_file_info("01-codigo/copybooks/CPYFPCD.cpy"), _CPYFPCD_SOURCE)
        zf.writestr(_regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), _PARAM_DEMO_DDL)
    return path


def test_copy_state_transition_and_calculation_reach_completed_with_diagnostic(
    tmp_path: Path,
) -> None:
    """Paridad de diagnostico V1/V2 (Fase 15B4-CANDIDATE-QUALITY-5A-SAFETY,
    seccion 5): `_missing_source_file_warnings` (candidates_detected_stage.py)
    opera sobre la lista final de candidatos sin distincion de
    rule_family -- confirma explicitamente que STATE_TRANSITION y
    CALCULATION (ademas de RETURN_CODE/LEVEL_88_RETURN_CODE, ya cubiertas
    por los tests anteriores de este archivo) tambien reciben el
    diagnostico auditable, nunca source_file=None silencioso."""
    require_jar()
    zip_path = _write_copy_family_parity_zip(tmp_path / "copy_famparity.zip")
    settings = _settings(tmp_path, run_label="copy_famparity", enhanced=True)

    with hermetic_llm_and_network_guard():
        state = run_ingestion(source_zip=zip_path, settings=settings)

    assert state.current_stage.value == "COMPLETED", [
        (s.stage.value, s.status.value, s.error) for s in state.stages
    ]
    run_dir = settings.runs_dir / state.run_id
    artifact = CandidateArtifact.model_validate_json(
        (run_dir / "artifacts" / "06-candidates.json").read_text(encoding="utf-8")
    )
    assert len(artifact.candidates) == 2, artifact.candidates
    families = {c.rule_family for c in artifact.candidates}
    assert families == {UnifiedRuleFamily.STATE_TRANSITION, UnifiedRuleFamily.CALCULATION}

    for candidate in artifact.candidates:
        assert candidate.source_file is None
        assert any(
            candidate.candidate_id in w and "sin source_file disponible" in w
            for w in artifact.warnings
        ), (candidate.rule_family, artifact.warnings)

        filename = _artifact_filename(candidate.candidate_id)
        context_path = run_dir / "artifacts" / "07-context" / filename
        package = ContextPackage.model_validate_json(context_path.read_text(encoding="utf-8"))
        assert package.scope.source_file == "01-codigo/cobol/COPYFP01.cbl"

        guardrail_path = run_dir / "artifacts" / "09-guardrails" / filename
        guardrail_candidate = GuardrailCandidateArtifact.model_validate_json(
            guardrail_path.read_text(encoding="utf-8")
        )
        assert guardrail_candidate.guardrail_report.verdict == GuardrailVerdict.EVIDENCE_VALIDATED

    rules_manifest = json.loads(
        (run_dir / "artifacts" / "10-rules" / "rules-manifest.json").read_text(encoding="utf-8")
    )
    assert rules_manifest["rule_count"] == 2
