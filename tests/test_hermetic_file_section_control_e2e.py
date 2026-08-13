"""E2E hermetico productivo del cierre de P1-FD-FILE-CONTROL-SILENT (Fase
15B4-CANDIDATE-QUALITY-5B): "package sintetico versionado -> parser Java
real -> canonical -> semantic graph en Neo4j real efimero -> V1 ->
06-candidates.json -> ContextPackage -> RuleDraft -> guardrail -> 10-rules",
ejercitando exclusivamente `run_ingestion` y los stages productivos reales.

Reutiliza el fixture COBOL versionado ya agregado por 5B
(`parser/src/test/resources/fixtures/file-section-and-control.cbl`, ya
cubierto por `FileSectionAndFileControlUnsupportedTest.java` a nivel
canonico) leyendolo directamente -- nunca duplica su contenido como un
segundo fixture Python. Ese programa combina, deliberadamente, en un unico
.cbl: FILE SECTION/FD (CLIENT-FILE/CLIENT-RECORD), FILE-CONTROL/SELECT
(CLIENT-FILE), verbos de Procedure Division ya trazados como no soportados
(OPEN/READ/CLOSE) y una decision RETURN_CODE productiva (V1/Q0) real -- el
objetivo de este test es demostrar que los diagnostics nuevos de FD/
FILE-CONTROL son *unicamente* no-silent-loss (nunca candidatos, nunca
nodos de grafo nuevos, nunca alteran la regla productiva).

Hermetismo: identico a `test_hermetic_copy_provenance_e2e.py`."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedRuleFamily,
)
from altamira_extractor.contracts.context_package import ContextPackage
from altamira_extractor.contracts.enums import GuardrailVerdict, Severity
from altamira_extractor.contracts.guardrail_candidate import GuardrailCandidateArtifact
from altamira_extractor.contracts.rule_draft import RuleDraft
from altamira_extractor.pipeline.runner import run_ingestion

from .e2e_support import REPO_ROOT, require_jar
from .hermetic_llm_support import build_hermetic_settings, hermetic_llm_and_network_guard

pytestmark = pytest.mark.integration

_JAVA_FIXTURE_PATH = (
    REPO_ROOT
    / "parser"
    / "src"
    / "test"
    / "resources"
    / "fixtures"
    / "file-section-and-control.cbl"
)

_MANIFEST = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-FD-FILE-CONTROL" description="15B4-CANDIDATE-QUALITY-5B"/>
  <implementation version="1.0">
    <entry-program>FILETEST1</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

_PARAM_DEMO_DDL = b"CREATE TABLE PARAM_DEMO (ID INT);\n"


def _artifact_filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


def _regular_file_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _write_package_zip(path: Path) -> Path:
    program_source = _JAVA_FIXTURE_PATH.read_bytes()
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(_regular_file_info("manifest.xml"), _MANIFEST)
        zf.writestr(_regular_file_info("01-codigo/cobol/FILETEST1.cbl"), program_source)
        zf.writestr(_regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), _PARAM_DEMO_DDL)
    return path


def test_fd_and_file_control_are_traced_without_affecting_the_productive_rule(
    tmp_path: Path,
) -> None:
    require_jar()
    assert _JAVA_FIXTURE_PATH.is_file(), (
        f"fixture Java/versionado no encontrado: {_JAVA_FIXTURE_PATH}"
    )
    zip_path = _write_package_zip(tmp_path / "file_section_control.zip")
    settings = build_hermetic_settings(
        tmp_path / "hermetic_data",
        NEO4J_URI=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        NEO4J_USER=os.environ.get("NEO4J_USER", "neo4j"),
        NEO4J_PASSWORD=os.environ.get("NEO4J_PASSWORD", "neo4j"),
        NEO4J_DATABASE=os.environ.get("NEO4J_DATABASE", "neo4j"),
        # Fase 15B4-CANDIDATE-QUALITY-5E: default global paso a True;
        # este test cubre trazado de FD/FILE-CONTROL, no deteccion V2,
        # asi que fija explicitamente el modo V1-only legacy.
        enhanced_candidates_enabled=False,
    )
    assert settings.enhanced_candidates_enabled is False

    with hermetic_llm_and_network_guard():
        state = run_ingestion(source_zip=zip_path, settings=settings)

    # I. RunState final stage.
    assert state.current_stage.value == "COMPLETED", [
        (s.stage.value, s.status.value, s.error) for s in state.stages
    ]
    assert all(s.status.value != "FAILED" for s in state.stages)

    run_dir = settings.runs_dir / state.run_id

    # A/B/C. Canonical: unsupported_constructs y data_items.
    canonical_paths = list((run_dir / "artifacts" / "02-canonical").rglob("*.json"))
    assert len(canonical_paths) == 1, canonical_paths
    canonical = json.loads(canonical_paths[0].read_text(encoding="utf-8"))

    unsupported = canonical["unsupported_constructs"]
    file_section_diagnostics = [w for w in unsupported if w.startswith("UNSUPPORTED_FILE_SECTION")]
    file_control_diagnostics = [w for w in unsupported if w.startswith("UNSUPPORTED_FILE_CONTROL")]
    assert len(file_section_diagnostics) == 1, unsupported
    assert "CLIENT-FILE" in file_section_diagnostics[0]
    assert len(file_control_diagnostics) == 1, unsupported
    assert "CLIENT-FILE" in file_control_diagnostics[0]

    # Caso C: OPEN/READ/CLOSE preexistentes siguen trazados (kind=OTHER),
    # sin que este fix los reemplace ni los oculte.
    other_diagnostics = [
        w for w in unsupported if "kind=OTHER" in w and "MAIN-PARA" in w
    ]
    assert len(other_diagnostics) >= 3, unsupported

    data_item_names = {item["name"] for item in canonical["data_items"]}
    assert "CLIENT-RECORD" not in data_item_names
    assert "CR-ID" not in data_item_names
    assert "CR-NOMBRE" not in data_item_names
    assert data_item_names == {"WS-SALDO", "WS-COD-RETORNO"}

    # No-impacto funcional: ningun statement OTHER de OPEN/READ/CLOSE
    # aparece en ninguna Decision/candidato, y ningun nodo File/Table se
    # crea a partir de FD/FILE-CONTROL (no hay mecanismo que los cree --
    # confirmado indirectamente via D/E/F/G/H: exactamente 1 candidato,
    # 1 contexto, 1 draft, 1 regla final, todos correspondientes
    # exclusivamente a la Decision real de MAIN-PARA).

    # D. CandidateArtifact.
    artifact = CandidateArtifact.model_validate_json(
        (run_dir / "artifacts" / "06-candidates.json").read_text(encoding="utf-8")
    )
    assert len(artifact.candidates) == 1, artifact.candidates
    candidate = artifact.candidates[0]
    # El fixture tiene una unica Paragraph (MAIN-PARA): la Decision
    # RETURN_CODE (IF WS-SALDO < 0 / MOVE 'R001') vive directamente ahi,
    # despues de OPEN/READ/CLOSE -- nunca en un paragraph separado.
    assert candidate.paragraph_name == "MAIN-PARA"
    assert candidate.rule_family == UnifiedRuleFamily.RETURN_CODE
    assert candidate.candidate_source == CandidateSource.V1
    assert candidate.outcome_code == "R001"
    assert candidate.detector_id == "q0-return-code-decision"
    # Identidad funcional estable (nunca un hash fragil dependiente de
    # tmp_path): detector + family + paragraph, sin hardcodear
    # line/ordinal (dependen de la posicion exacta en el fixture, mas
    # fragiles que necesario para este test).
    assert candidate.candidate_id.startswith("candidate::q0-return-code-decision::1.0::")
    assert "::paragraph::MAIN-PARA::decision::" in candidate.candidate_id

    # E. Context.
    context_manifest = json.loads(
        (run_dir / "artifacts" / "07-context" / "context-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert context_manifest["context_count"] == 1

    filename = _artifact_filename(candidate.candidate_id)
    context_path = run_dir / "artifacts" / "07-context" / filename
    assert context_path.is_file()
    package = ContextPackage.model_validate_json(context_path.read_text(encoding="utf-8"))
    assert package.candidate.candidate_id == candidate.candidate_id

    # F. RuleDraft.
    draft_path = run_dir / "artifacts" / "08-rule-drafts" / filename
    assert draft_path.is_file()
    RuleDraft.model_validate_json(draft_path.read_text(encoding="utf-8"))

    # G. Guardrail.
    guardrail_path = run_dir / "artifacts" / "09-guardrails" / filename
    assert guardrail_path.is_file()
    guardrail_candidate = GuardrailCandidateArtifact.model_validate_json(
        guardrail_path.read_text(encoding="utf-8")
    )
    assert guardrail_candidate.candidate_id == candidate.candidate_id
    assert guardrail_candidate.guardrail_report.candidate_id == candidate.candidate_id
    assert guardrail_candidate.guardrail_report.verdict == GuardrailVerdict.EVIDENCE_VALIDATED
    assert all(
        v.severity != Severity.ERROR for v in guardrail_candidate.guardrail_report.violations
    ), guardrail_candidate.guardrail_report.violations

    # H. Final.
    rules_dir = run_dir / "artifacts" / "10-rules"
    rules_manifest = json.loads((rules_dir / "rules-manifest.json").read_text(encoding="utf-8"))
    assert rules_manifest["rule_count"] == 1
    assert len(rules_manifest["records"]) == 1
    record = rules_manifest["records"][0]
    assert record["candidate_id"] == candidate.candidate_id
    markdown_path = rules_dir / record["relative_filename"]
    assert markdown_path.is_file()
