"""Prueba de no-regresion de la fundacion interprocedural CALL/LINKAGE
(Fase 6 de la ampliacion semantica, `feat/interprocedural-call-linkage-
foundation`, Fase 21 del plan).

Para un paquete SIN ninguna construccion CALL/LINKAGE (`schema_version`
"1.0"/"1.1" preservado, ningun `CanonicalStatement.kind=CALL`), correr el
comando CLI `semantic-interprocedural` NUNCA debe alterar ningun artefacto
V1 preexistente (`run.json`, `artifacts/01-10`, ningun `diagnostics/*`
anterior). El UNICO archivo nuevo permitido es
`diagnostics/interprocedural-call-linkage.json`. Compara el arbol
COMPLETO de `run_dir` (bytes exactos por path relativo) antes/despues,
en vez de inspeccionar archivo por archivo -- para no dejar ningun
artefacto fuera del alcance de la comparacion por descuido."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import altamira_extractor.cli as cli_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.canonical import (
    CanonicalConditionName,
    CanonicalConditionValue,
    CanonicalDataItem,
    CanonicalParagraph,
    CanonicalProgram,
    CanonicalStatement,
)
from altamira_extractor.contracts.enums import (
    LocationKind,
    PipelineStage,
    SourceFormat,
    StageStatus,
    StatementKind,
)
from altamira_extractor.contracts.run_state import RunState, StageExecution
from altamira_extractor.pipeline.artifact_store import atomic_write_json

runner = CliRunner()

_HASH = "3" * 64
_RUN_ID = "20260101T000000000000-aaaaaaaa"


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


def _build_historical_program_1_0() -> CanonicalProgram:
    """schema_version="1.0": ninguna extension de nivel 88 ni CALL/LINKAGE."""
    stmt = CanonicalStatement(
        statement_id="LEGACY1::A::0::MOVE",
        kind=StatementKind.MOVE,
        source_text="MOVE '0005' TO WS-COD-AUX",
        location_kind=LocationKind.UNKNOWN,
        target_data_items=["WS-COD-AUX"],
        variables_written=["WS-COD-AUX"],
        assigned_literal="0005",
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[stmt],
        variables_written=["WS-COD-AUX"],
    )
    return CanonicalProgram(
        schema_version="1.0",
        program_name="LEGACY1",
        source_file="01-codigo/cobol/LEGACY1.cbl",
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=[
            CanonicalDataItem(
                name="WS-COD-AUX",
                qualified_name="WS-COD-AUX",
                level=1,
                location_kind=LocationKind.UNKNOWN,
            )
        ],
        paragraphs=[paragraph],
    )


def _build_historical_program_1_1() -> CanonicalProgram:
    """schema_version="1.1": usa nivel 88 (Fase 3) pero ningun CALL/LINKAGE
    (Fase 6) -- prueba que ambas extensiones conviven sin interferir."""
    condition = CanonicalConditionName(
        name="COD-INVALIDO",
        qualified_name="WS-COD-RETORNO.COD-INVALIDO",
        parent_name="WS-COD-RETORNO",
        parent_qualified_name="WS-COD-RETORNO",
        values=[CanonicalConditionValue(value="0005", location_kind=LocationKind.UNKNOWN)],
        location_kind=LocationKind.UNKNOWN,
    )
    stmt = CanonicalStatement(
        statement_id="LEGACY2::A::0::SET",
        kind=StatementKind.SET,
        source_text="SET COD-INVALIDO TO TRUE",
        location_kind=LocationKind.UNKNOWN,
        target_data_items=["COD-INVALIDO"],
        variables_written=["COD-INVALIDO"],
        assigned_literal="true",
        condition_name_target="COD-INVALIDO",
        condition_set_value=True,
    )
    paragraph = CanonicalParagraph(
        name="A",
        source_text="A.",
        location_kind=LocationKind.UNKNOWN,
        statements=[stmt],
        variables_written=["COD-INVALIDO"],
    )
    return CanonicalProgram(
        schema_version="1.1",
        program_name="LEGACY2",
        source_file="01-codigo/cobol/LEGACY2.cbl",
        source_hash=_HASH,
        source_package_hash=_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=[
            CanonicalDataItem(
                name="WS-COD-RETORNO",
                qualified_name="WS-COD-RETORNO",
                level=1,
                location_kind=LocationKind.UNKNOWN,
            )
        ],
        condition_names=[condition],
        paragraphs=[paragraph],
    )


def _write_placeholder_json(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _setup_run_with_full_v1_artifacts(run_dir: Path) -> None:
    """Construye un run que ya alcanzo COMPLETED (registros de stage) y
    tiene artefactos V1 preexistentes (03-06), ademas de diagnostics/
    semantic-effects.json y semantic-propagation.json ya calculados por
    fases anteriores. El CONTENIDO de los artefactos 03-06 es un
    placeholder deliberadamente arbitrario: lo unico bajo prueba es que
    `semantic-interprocedural` nunca los lee ni los escribe, nunca que su
    forma sea valida."""
    now = datetime.now(UTC)
    stages = [
        StageExecution(
            stage=stage,
            status=StageStatus.SUCCEEDED,
            started_at=now,
            finished_at=now,
            duration_seconds=0.0,
        )
        for stage in (
            PipelineStage.PARSED,
            PipelineStage.DEPENDENCIES_BUILT,
            PipelineStage.SEMANTIC_GRAPH_BUILT,
            PipelineStage.SEMANTIC_GRAPH_LOADED,
            PipelineStage.GRAPH_VALIDATED,
            PipelineStage.CANDIDATES_DETECTED,
        )
    ]
    state = RunState(
        run_id=_RUN_ID,
        package_filename="input/package.zip",
        source_package_hash=_HASH,
        current_stage=PipelineStage.CANDIDATES_DETECTED,
        stages=stages,
        created_at=now,
        updated_at=now,
    )
    atomic_write_json(run_dir / "run.json", state)

    canonical_dir = run_dir / "artifacts" / "02-canonical"
    canonical_dir.mkdir(parents=True)
    atomic_write_json(canonical_dir / "LEGACY1.json", _build_historical_program_1_0())
    atomic_write_json(canonical_dir / "LEGACY2.json", _build_historical_program_1_1())

    # Artefactos V1 preexistentes (contenido placeholder deliberado).
    _write_placeholder_json(
        run_dir / "artifacts" / "03-dependencies.json", '{"placeholder": "dependencies"}\n'
    )
    _write_placeholder_json(
        run_dir / "artifacts" / "04-semantic-graph.json", '{"placeholder": "semantic-graph"}\n'
    )
    _write_placeholder_json(
        run_dir / "artifacts" / "05-invariants.json", '{"placeholder": "invariants"}\n'
    )
    _write_placeholder_json(
        run_dir / "artifacts" / "06-candidates.json", '{"placeholder": "candidates"}\n'
    )


def _snapshot(run_dir: Path) -> dict[str, bytes]:
    return {
        path.relative_to(run_dir).as_posix(): path.read_bytes()
        for path in sorted(run_dir.rglob("*"))
        if path.is_file()
    }


def test_semantic_interprocedural_never_regresses_a_package_without_call_or_linkage(
    patched_settings: Settings,
) -> None:
    run_dir = patched_settings.runs_dir / _RUN_ID
    _setup_run_with_full_v1_artifacts(run_dir)

    # Fases previas ya calculadas y persistidas (Fase 2-9), como en un run
    # real que llega hasta este punto antes de invocar el diagnostico
    # interprocedural bajo demanda.
    effects_result = runner.invoke(cli_module.app, ["semantic-effects", _RUN_ID])
    assert effects_result.exit_code == 0, effects_result.stderr
    propagation_result = runner.invoke(cli_module.app, ["semantic-propagation", _RUN_ID])
    assert propagation_result.exit_code == 0, propagation_result.stderr

    before = _snapshot(run_dir)
    assert "diagnostics/interprocedural-call-linkage.json" not in before

    result = runner.invoke(cli_module.app, ["semantic-interprocedural", _RUN_ID])
    assert result.exit_code == 0, result.stderr

    after = _snapshot(run_dir)

    new_paths = set(after) - set(before)
    assert new_paths == {"diagnostics/interprocedural-call-linkage.json"}

    removed_paths = set(before) - set(after)
    assert removed_paths == set()

    unchanged_paths = set(before) & set(after)
    for path in unchanged_paths:
        assert after[path] == before[path], f"{path} cambio de contenido: no deberia haberlo hecho"


def test_semantic_interprocedural_preserves_historical_schema_versions_and_reports_zero_calls(
    patched_settings: Settings,
) -> None:
    """El analisis interprocedural sobre un paquete sin CALL/LINKAGE
    produce un artefacto valido con `canonical_schema_versions` EXACTAMENTE
    ["1.0", "1.1"] (nunca fuerza una version 1.2 inexistente) y ningun
    call_site (nunca inventa llamadas que no existen)."""
    run_dir = patched_settings.runs_dir / _RUN_ID
    _setup_run_with_full_v1_artifacts(run_dir)

    result = runner.invoke(cli_module.app, ["semantic-interprocedural", _RUN_ID])
    assert result.exit_code == 0, result.stderr
    assert "programs: 2" in result.stdout
    assert "call_sites: 0" in result.stdout

    payload = json.loads(
        (run_dir / "diagnostics" / "interprocedural-call-linkage.json").read_text(encoding="utf-8")
    )
    assert payload["canonical_schema_versions"] == ["1.0", "1.1"]
    assert payload["call_sites"] == []
    assert payload["call_edges"] == []
    assert payload["cycles"] == []
    assert len(payload["interfaces"]) == 2
    assert all(interface["parameters"] == [] for interface in payload["interfaces"])
