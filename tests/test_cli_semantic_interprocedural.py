"""Tests unitarios del comando CLI `semantic-interprocedural` (Fase 6 de
la ampliacion semantica, `feat/interprocedural-call-linkage-foundation`).
Mismo patron que `tests/test_cli_semantic_propagation.py`: sin Docker,
sin JAR, sin Neo4j, sin FastAPI -- solo filesystem local (`tmp_path`) via
`CliRunner`.

Fase 20 (integracion multiprograma): un paquete sintetico de TRES
`CanonicalProgram` (CALLER + 2 callees, `CALLEE_A` con LINKAGE SECTION/
PROCEDURE DIVISION USING/RETURNING real, `CALLEE_B` presente en el
paquete pero nunca invocado) ejercitando las tres formas de resolucion:
una CALL literal resuelta internamente, una CALL literal a un programa
ausente del paquete, y una CALL dinamica -- encadenado con
`semantic-effects`/`semantic-propagation` sobre el MISMO run para probar
que las tres capas componen correctamente sin modificarse entre si, y
que ningun artefacto V1 (run.json, artifacts/02-canonical) cambia."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.canonical import (
    CanonicalCallArgument,
    CanonicalDataItem,
    CanonicalEntryParameter,
    CanonicalLinkageDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
    CallPassingMode,
    CallTargetKind,
    LocationKind,
    PipelineStage,
    SourceFormat,
    StageStatus,
    StatementKind,
)
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.pipeline.artifact_store import atomic_write_json

runner = CliRunner()

_HASH = "7" * 64
_RUN_ID = "20260101T000000000000-cccccccc"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
    )


@pytest.fixture
def patched_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Settings:
    monkeypatch.setattr(cli_module, "load_settings", lambda: settings)
    return settings


def _build_caller() -> CanonicalProgram:
    call_resolved = CanonicalStatement(
        statement_id="CALLER::MAIN::0::CALL",
        kind=StatementKind.CALL,
        source_text="CALL 'CALLEE_A' USING BY REFERENCE WS-INPUT RETURNING WS-RESULT",
        location_kind=LocationKind.UNKNOWN,
        variables_read=["WS-INPUT"],
        call_target_kind=CallTargetKind.LITERAL,
        called_program_name="CALLEE_A",
        call_arguments=[
            CanonicalCallArgument(
                ordinal=1,
                expression="WS-INPUT",
                data_item_name="WS-INPUT",
                qualified_data_item_name="WS-INPUT",
                passing_mode=CallPassingMode.REFERENCE,
                location_kind=LocationKind.UNKNOWN,
            )
        ],
        call_returning_data_item="WS-RESULT",
    )
    call_missing = CanonicalStatement(
        statement_id="CALLER::MAIN::1::CALL",
        kind=StatementKind.CALL,
        source_text="CALL 'MISSING-PROG'",
        location_kind=LocationKind.UNKNOWN,
        call_target_kind=CallTargetKind.LITERAL,
        called_program_name="MISSING-PROG",
    )
    call_dynamic = CanonicalStatement(
        statement_id="CALLER::MAIN::2::CALL",
        kind=StatementKind.CALL,
        source_text="CALL WS-PROGRAM-NAME",
        location_kind=LocationKind.UNKNOWN,
        variables_read=["WS-PROGRAM-NAME"],
        call_target_kind=CallTargetKind.DYNAMIC,
        called_program_expression="WS-PROGRAM-NAME",
    )
    statements = [call_resolved, call_missing, call_dynamic]
    paragraph = CanonicalParagraph(
        name="MAIN",
        source_text="MAIN.",
        location_kind=LocationKind.UNKNOWN,
        statements=statements,
        variables_read=["WS-INPUT", "WS-PROGRAM-NAME"],
    )
    return CanonicalProgram(
        schema_version="1.2",
        program_name="CALLER",
        source_file="01-codigo/cobol/CALLER.cbl",
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=[
            CanonicalDataItem(
                name="WS-INPUT",
                qualified_name="WS-INPUT",
                level=1,
                pic="X(10)",
                location_kind=LocationKind.UNKNOWN,
            ),
            CanonicalDataItem(
                name="WS-RESULT",
                qualified_name="WS-RESULT",
                level=1,
                pic="X(10)",
                location_kind=LocationKind.UNKNOWN,
            ),
            CanonicalDataItem(
                name="WS-PROGRAM-NAME",
                qualified_name="WS-PROGRAM-NAME",
                level=1,
                pic="X(8)",
                location_kind=LocationKind.UNKNOWN,
            ),
        ],
        paragraphs=[paragraph],
    )


def _build_callee_a() -> CanonicalProgram:
    return CanonicalProgram(
        schema_version="1.2",
        program_name="CALLEE_A",
        source_file="01-codigo/cobol/CALLEE_A.cbl",
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        linkage_data_items=[
            CanonicalLinkageDataItem(
                name="LK-INPUT",
                qualified_name="LK-INPUT",
                level=1,
                pic="X(10)",
                location_kind=LocationKind.UNKNOWN,
            ),
            CanonicalLinkageDataItem(
                name="LK-RESULT",
                qualified_name="LK-RESULT",
                level=1,
                pic="X(10)",
                location_kind=LocationKind.UNKNOWN,
            ),
        ],
        entry_parameters=[
            CanonicalEntryParameter(
                ordinal=1,
                name="LK-INPUT",
                qualified_name="LK-INPUT",
                linkage_item_qualified_name="LK-INPUT",
                passing_mode=CallPassingMode.REFERENCE,
                location_kind=LocationKind.UNKNOWN,
            ),
        ],
        entry_returning_data_item="LK-RESULT",
        paragraphs=[],
    )


def _build_callee_b() -> CanonicalProgram:
    """Presente en el paquete, nunca invocado por CALLER -- prueba que
    `interfaces`/`program_count` incluyen todo el paquete, no solo los
    programas alcanzados por alguna CALL."""
    return CanonicalProgram(
        schema_version="1.0",
        program_name="CALLEE_B",
        source_file="01-codigo/cobol/CALLEE_B.cbl",
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        paragraphs=[],
    )


def _write_valid_run(run_dir: Path) -> None:
    now = datetime.now(UTC)
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH,
        current_stage=PipelineStage.PARSED,
        stages=[
            StageExecution(
                stage=PipelineStage.PARSED,
                status=StageStatus.SUCCEEDED,
                started_at=now,
                finished_at=now,
                duration_seconds=0.0,
            )
        ],
        created_at=now,
        updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    canonical_dir.mkdir(parents=True)
    atomic_write_json(canonical_dir / "CALLER.json", _build_caller())
    atomic_write_json(canonical_dir / "CALLEE_A.json", _build_callee_a())
    atomic_write_json(canonical_dir / "CALLEE_B.json", _build_callee_b())


# ---------------------------------------------------------------------------
# Camino feliz: resolucion interna + programa ausente + CALL dinamico
# ---------------------------------------------------------------------------


def test_semantic_interprocedural_summary_reports_the_three_call_kinds(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-interprocedural", _RUN_ID])

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"run_id: {_RUN_ID}"
    assert "programs: 3" in result.stdout
    assert "call_sites: 3" in result.stdout
    assert "resolved_internal: 1" in result.stdout
    assert "dynamic: 1" in result.stdout
    assert "missing_program: 1" in result.stdout
    assert "ambiguous_program: 0" in result.stdout
    assert lines[-1] == "report: diagnostics/interprocedural-call-linkage.json"


def test_semantic_interprocedural_persisted_artifact_matches_expected_resolution(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-interprocedural", _RUN_ID])
    assert result.exit_code == 0

    artifact_path = run_dir / "diagnostics" / "interprocedural-call-linkage.json"
    assert artifact_path.is_file()
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert payload["run_id"] == _RUN_ID
    assert sorted(interface["program"] for interface in payload["interfaces"]) == [
        "CALLEE_A",
        "CALLEE_B",
        "CALLER",
    ]
    assert payload["canonical_schema_versions"] == ["1.0", "1.2"]

    call_sites = {site["call_site_id"]: site for site in payload["call_sites"]}
    resolved = next(
        s
        for s in call_sites.values()
        if s["target_kind"] == "LITERAL" and s["resolved_callee_program"] == "CALLEE_A"
    )
    assert resolved["resolution_status"] == "RESOLVED_INTERNAL"
    assert resolved["arguments"][0]["status"] == "RESOLVED_POSITIONAL"
    assert resolved["arguments"][0]["potential_flow"] == "INPUT_OUTPUT"
    assert resolved["returning_binding"]["potential_flow"] == "OUTPUT_ONLY"
    assert resolved["returning_binding"]["formal_name"] == "LK-RESULT"
    assert resolved["returning_binding"]["linkage_item_qualified_name"] == "LK-RESULT"

    missing = next(s for s in call_sites.values() if s["declared_target"] == "MISSING-PROG")
    assert missing["resolution_status"] == "UNRESOLVED_MISSING_PROGRAM"
    assert missing.get("resolved_callee_program") is None

    dynamic = next(s for s in call_sites.values() if s["target_kind"] == "DYNAMIC")
    assert dynamic["resolution_status"] == "UNRESOLVED_DYNAMIC"
    assert dynamic["declared_target"] == "WS-PROGRAM-NAME"

    assert payload["call_edges"] == [
        edge for edge in payload["call_edges"] if edge["caller_program"] == "CALLER"
    ]
    assert len(payload["call_edges"]) == 1
    assert payload["call_edges"][0]["callee_program"] == "CALLEE_A"
    assert payload["cycles"] == []


def test_semantic_interprocedural_is_byte_for_byte_deterministic_across_invocations(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    artifact_path = run_dir / "diagnostics" / "interprocedural-call-linkage.json"

    first = runner.invoke(cli_module.app, ["semantic-interprocedural", _RUN_ID])
    assert first.exit_code == 0
    first_bytes = artifact_path.read_bytes()

    second = runner.invoke(cli_module.app, ["semantic-interprocedural", _RUN_ID])
    assert second.exit_code == 0
    second_bytes = artifact_path.read_bytes()

    assert first_bytes == second_bytes


def test_semantic_interprocedural_no_timestamps_no_absolute_paths(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    result = runner.invoke(cli_module.app, ["semantic-interprocedural", _RUN_ID])
    assert result.exit_code == 0
    text = (run_dir / "diagnostics" / "interprocedural-call-linkage.json").read_text(
        encoding="utf-8"
    )
    for forbidden in ("generated_at", "timestamp", "created_at", "updated_at"):
        assert forbidden not in text
    assert str(patched_settings.runs_dir) not in text


# ---------------------------------------------------------------------------
# Composicion con semantic-effects/semantic-propagation sobre el MISMO run;
# ausencia de efectos sobre artefactos V1 (Fase 20/21)
# ---------------------------------------------------------------------------


def test_semantic_interprocedural_composes_with_effects_and_propagation_without_mutating_them(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    effects_result = runner.invoke(cli_module.app, ["semantic-effects", _RUN_ID])
    assert effects_result.exit_code == 0, effects_result.stderr
    effects_path = run_dir / "diagnostics" / "semantic-effects.json"
    effects_bytes_before = effects_path.read_bytes()

    propagation_result = runner.invoke(cli_module.app, ["semantic-propagation", _RUN_ID])
    assert propagation_result.exit_code == 0, propagation_result.stderr
    propagation_path = run_dir / "diagnostics" / "semantic-propagation.json"
    propagation_bytes_before = propagation_path.read_bytes()

    interprocedural_result = runner.invoke(cli_module.app, ["semantic-interprocedural", _RUN_ID])
    assert interprocedural_result.exit_code == 0, interprocedural_result.stderr

    # semantic-interprocedural nunca lee ni escribe los diagnostics
    # preexistentes de semantic-effects/semantic-propagation (calcula su
    # propio SemanticEffectsArtifact en memoria) -- deben quedar
    # bit a bit identicos.
    assert effects_path.read_bytes() == effects_bytes_before
    assert propagation_path.read_bytes() == propagation_bytes_before

    interprocedural_payload = json.loads(
        (run_dir / "diagnostics" / "interprocedural-call-linkage.json").read_text(encoding="utf-8")
    )
    assert interprocedural_payload["semantic_effects_schema_version"] == "1.2"


def test_semantic_interprocedural_never_modifies_run_json_or_canonical_artifacts(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)
    run_json_before = (run_dir / "run.json").read_bytes()
    canonical_dir = run_dir / "artifacts" / "02-canonical"
    canonical_before = {
        path.name: path.read_bytes() for path in sorted(canonical_dir.glob("*.json"))
    }

    result = runner.invoke(cli_module.app, ["semantic-interprocedural", _RUN_ID])
    assert result.exit_code == 0

    assert (run_dir / "run.json").read_bytes() == run_json_before
    canonical_after = {
        path.name: path.read_bytes() for path in sorted(canonical_dir.glob("*.json"))
    }
    assert canonical_after == canonical_before


def test_semantic_interprocedural_never_creates_v1_candidate_or_semantic_graph_artifacts(
    patched_settings: Settings,
) -> None:
    """Fase 20: 'No agregues candidatos V2 por estas llamadas' -- el
    comando no invoca ni V1 (Q0) ni V2 shadow, asi que ninguno de sus
    artefactos debe existir despues de correrlo (nunca se crean como
    efecto secundario)."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _write_valid_run(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-interprocedural", _RUN_ID])
    assert result.exit_code == 0

    assert not (run_dir / "artifacts" / "06-candidates.json").exists()
    assert not (run_dir / "artifacts" / "04-semantic-graph.json").exists()
    assert not (run_dir / "diagnostics" / "v2-shadow-candidates.json").exists()
    diagnostics_files = sorted(p.name for p in (run_dir / "diagnostics").glob("*.json"))
    assert diagnostics_files == ["interprocedural-call-linkage.json"]


# ---------------------------------------------------------------------------
# Errores sanitizados
# ---------------------------------------------------------------------------


def test_semantic_interprocedural_requires_parsed_stage(patched_settings: Settings) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    run_dir.mkdir(parents=True)
    now = datetime.now(UTC)
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH,
        current_stage=PipelineStage.VALIDATED,
        stages=[],
        created_at=now,
        updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)

    result = runner.invoke(cli_module.app, ["semantic-interprocedural", _RUN_ID])

    assert result.exit_code != 0
    assert str(patched_settings.runs_dir) not in result.stdout
    assert str(patched_settings.runs_dir) not in (result.stderr or "")
