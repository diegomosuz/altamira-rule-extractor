"""Tests unitarios de run_parsed_stage: orquestacion pura, con un
ProLeapParserClient falso (sin JAR ni Java reales, ver test_parser_client.py
para la capa de invocacion en si)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.contracts import (
    Inventory,
    InventoryFile,
    InventoryFileKind,
    Manifest,
    ManifestApplication,
    ManifestCountry,
    ManifestImplementation,
    ManifestOperation,
    ManifestSource,
    SourceFormat,
    TextEncoding,
)
from altamira_extractor.contracts.canonical import CanonicalProgram
from altamira_extractor.pipeline.errors import ParserUnavailableError
from altamira_extractor.pipeline.parsed_stage import _canonical_output_path, run_parsed_stage
from altamira_extractor.pipeline.parser_client import ParseFailure, ParseSuccess

VALID_PACKAGE_HASH = "a" * 64


def _manifest(*, source_format: SourceFormat = SourceFormat.FIXED) -> Manifest:
    return Manifest(
        schema_version="1.0",
        country=ManifestCountry(code="AR", name="Argentina"),
        application=ManifestApplication(name="Transferencias"),
        operation=ManifestOperation(logical_name="OP-TRF-PROPIA", description=None),
        implementation=ManifestImplementation(version="3.2", entry_programs=["PROG1"]),
        source=ManifestSource(format=source_format, encoding="AUTO"),
        parameter_tables=[],
    )


def _inventory_file(
    relative_path: str,
    *,
    kind: InventoryFileKind = InventoryFileKind.COBOL,
    sha256: str = "b" * 64,
    detected_encoding: TextEncoding | None = TextEncoding.UTF_8,
) -> InventoryFile:
    return InventoryFile(
        relative_path=relative_path,
        kind=kind,
        size_bytes=10,
        sha256=sha256,
        detected_encoding=detected_encoding,
    )


def _canonical_program(*, source_file: str, source_hash: str = "b" * 64) -> CanonicalProgram:
    return CanonicalProgram(
        program_name="PROG1",
        source_file=source_file,
        source_hash=source_hash,
        source_package_hash=VALID_PACKAGE_HASH,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=[],
        paragraphs=[],
        warnings=[],
        unsupported_constructs=[],
    )


@dataclass
class FakeParserClient:
    jar_path: Path
    outcomes: dict[str, Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def parse_program(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        # Replica el chequeo real de ProLeapParserClient.parse_program: el
        # JAR solo se exige en el momento en que efectivamente se necesita
        # invocarlo, nunca por adelantado (ver run_parsed_stage).
        if not self.jar_path.is_file():
            raise ParserUnavailableError(f"no se encontro el JAR del parser ({self.jar_path.name})")
        source_file = kwargs["source_file"]
        outcome = self.outcomes[source_file]
        if isinstance(outcome, ParseSuccess):
            output_path = kwargs["output_final_path"]
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(outcome.canonical_program.to_stable_json(), encoding="utf-8")
        return outcome


@dataclass
class Env:
    run_root: Path
    extracted_dir: Path
    canonical_dir: Path


def _build_env(tmp_path: Path) -> Env:
    run_root = tmp_path / "run"
    extracted_dir = run_root / "work" / "extracted"
    canonical_dir = run_root / "artifacts" / "02-canonical"
    extracted_dir.mkdir(parents=True)
    canonical_dir.mkdir(parents=True)
    return Env(run_root=run_root, extracted_dir=extracted_dir, canonical_dir=canonical_dir)


def test_canonical_output_path_preserves_directories_and_extension() -> None:
    canonical_dir = Path("/run/artifacts/02-canonical")

    result = _canonical_output_path(canonical_dir, "01-codigo/cobol/modulo/PROGRAMA.cbl")

    assert result == Path(
        "/run/artifacts/02-canonical/01-codigo/cobol/modulo/PROGRAMA.cbl.json"
    )


def test_canonical_output_path_distinguishes_cbl_and_cob_same_stem() -> None:
    canonical_dir = Path("/run/artifacts/02-canonical")

    cbl_path = _canonical_output_path(canonical_dir, "01-codigo/cobol/PROG1.cbl")
    cob_path = _canonical_output_path(canonical_dir, "01-codigo/cobol/PROG1.cob")

    assert cbl_path != cob_path
    assert cbl_path.name == "PROG1.cbl.json"
    assert cob_path.name == "PROG1.cob.json"


def test_canonical_output_path_distinguishes_same_basename_different_dirs() -> None:
    canonical_dir = Path("/run/artifacts/02-canonical")

    path_a = _canonical_output_path(canonical_dir, "01-codigo/cobol/modulo-a/PROG1.cbl")
    path_b = _canonical_output_path(canonical_dir, "01-codigo/cobol/modulo-b/PROG1.cbl")

    assert path_a != path_b
    assert canonical_dir in path_a.parents
    assert canonical_dir in path_b.parents


def test_processes_cbl_and_cob_in_deterministic_order(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    files = [
        _inventory_file("01-codigo/cobol/B.cob"),
        _inventory_file("01-codigo/cobol/A.cbl"),
    ]
    inventory = Inventory(
        run_id="run-1", source_package_hash=VALID_PACKAGE_HASH, manifest=_manifest(), files=files
    )
    client = FakeParserClient(
        jar_path=tmp_path / "parser.jar",
        outcomes={
            "01-codigo/cobol/A.cbl": ParseSuccess(
                _canonical_program(source_file="01-codigo/cobol/A.cbl")
            ),
            "01-codigo/cobol/B.cob": ParseSuccess(
                _canonical_program(source_file="01-codigo/cobol/B.cob")
            ),
        },
    )
    client.jar_path.write_bytes(b"dummy")

    outcome = run_parsed_stage(
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        inventory=inventory,
        source_package_hash=VALID_PACKAGE_HASH,
        client=client,
    )

    assert outcome.succeeded
    called_order = [call["source_file"] for call in client.calls]
    assert called_order == ["01-codigo/cobol/A.cbl", "01-codigo/cobol/B.cob"]


def test_non_cobol_kinds_are_never_processed(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    files = [
        _inventory_file("01-codigo/cobol/A.cbl"),
        _inventory_file("01-codigo/copybooks/CPY1.cpy", kind=InventoryFileKind.COPYBOOK),
        _inventory_file("02-parametria/ddl/T1.sql", kind=InventoryFileKind.DDL),
    ]
    inventory = Inventory(
        run_id="run-1", source_package_hash=VALID_PACKAGE_HASH, manifest=_manifest(), files=files
    )
    client = FakeParserClient(
        jar_path=tmp_path / "parser.jar",
        outcomes={
            "01-codigo/cobol/A.cbl": ParseSuccess(
                _canonical_program(source_file="01-codigo/cobol/A.cbl")
            ),
        },
    )
    client.jar_path.write_bytes(b"dummy")

    run_parsed_stage(
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        inventory=inventory,
        source_package_hash=VALID_PACKAGE_HASH,
        client=client,
    )

    assert len(client.calls) == 1


def test_copybook_dirs_are_unique_sorted_and_resolved_under_extracted(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    files = [
        _inventory_file("01-codigo/cobol/A.cbl"),
        _inventory_file("01-codigo/copybooks/z/CPY2.cpy", kind=InventoryFileKind.COPYBOOK),
        _inventory_file("01-codigo/copybooks/a/CPY1.cpy", kind=InventoryFileKind.COPYBOOK),
        _inventory_file("01-codigo/copybooks/a/CPY3.cpy", kind=InventoryFileKind.COPYBOOK),
    ]
    inventory = Inventory(
        run_id="run-1", source_package_hash=VALID_PACKAGE_HASH, manifest=_manifest(), files=files
    )
    client = FakeParserClient(
        jar_path=tmp_path / "parser.jar",
        outcomes={
            "01-codigo/cobol/A.cbl": ParseSuccess(
                _canonical_program(source_file="01-codigo/cobol/A.cbl")
            ),
        },
    )
    client.jar_path.write_bytes(b"dummy")

    run_parsed_stage(
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        inventory=inventory,
        source_package_hash=VALID_PACKAGE_HASH,
        client=client,
    )

    passed_dirs = client.calls[0]["copybook_dirs"]
    assert passed_dirs == [
        env.extracted_dir / "01-codigo/copybooks/a",
        env.extracted_dir / "01-codigo/copybooks/z",
    ]


def test_recoverable_failure_does_not_stop_remaining_programs(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    files = [
        _inventory_file("01-codigo/cobol/A.cbl"),
        _inventory_file("01-codigo/cobol/B.cbl"),
    ]
    inventory = Inventory(
        run_id="run-1", source_package_hash=VALID_PACKAGE_HASH, manifest=_manifest(), files=files
    )
    client = FakeParserClient(
        jar_path=tmp_path / "parser.jar",
        outcomes={
            "01-codigo/cobol/A.cbl": ParseFailure(reason="PARSE_ERROR", message="A.cbl: exit 3"),
            "01-codigo/cobol/B.cbl": ParseSuccess(
                _canonical_program(source_file="01-codigo/cobol/B.cbl")
            ),
        },
    )
    client.jar_path.write_bytes(b"dummy")

    outcome = run_parsed_stage(
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        inventory=inventory,
        source_package_hash=VALID_PACKAGE_HASH,
        client=client,
    )

    assert len(client.calls) == 2
    assert not outcome.succeeded
    assert outcome.error is not None
    assert "01-codigo/cobol/A.cbl" in outcome.error
    assert "01-codigo/cobol/B.cbl" not in outcome.error
    assert any("A.cbl" in warning for warning in outcome.warnings)


def test_timeout_does_not_stop_remaining_programs(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    files = [
        _inventory_file("01-codigo/cobol/A.cbl"),
        _inventory_file("01-codigo/cobol/B.cbl"),
    ]
    inventory = Inventory(
        run_id="run-1", source_package_hash=VALID_PACKAGE_HASH, manifest=_manifest(), files=files
    )
    client = FakeParserClient(
        jar_path=tmp_path / "parser.jar",
        outcomes={
            "01-codigo/cobol/A.cbl": ParseFailure(reason="TIMEOUT", message="A.cbl: timeout"),
            "01-codigo/cobol/B.cbl": ParseSuccess(
                _canonical_program(source_file="01-codigo/cobol/B.cbl")
            ),
        },
    )
    client.jar_path.write_bytes(b"dummy")

    outcome = run_parsed_stage(
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        inventory=inventory,
        source_package_hash=VALID_PACKAGE_HASH,
        client=client,
    )

    assert len(client.calls) == 2
    assert not outcome.succeeded


def test_missing_jar_aborts_when_a_program_actually_needs_processing(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    files = [_inventory_file("01-codigo/cobol/A.cbl")]
    inventory = Inventory(
        run_id="run-1", source_package_hash=VALID_PACKAGE_HASH, manifest=_manifest(), files=files
    )
    client = FakeParserClient(jar_path=tmp_path / "does-not-exist.jar", outcomes={})

    with pytest.raises(ParserUnavailableError):
        run_parsed_stage(
            run_root=env.run_root,
            extracted_dir=env.extracted_dir,
            canonical_dir=env.canonical_dir,
            inventory=inventory,
            source_package_hash=VALID_PACKAGE_HASH,
            client=client,
        )


def test_full_cache_hit_never_checks_jar_even_if_missing(tmp_path: Path) -> None:
    # Si TODOS los artefactos ya son validos, run_parsed_stage no debe
    # exigir el JAR en absoluto: no hay nada que invocar. Esto es lo que
    # permite demostrar idempotencia real en integracion sin depender de
    # timestamps de modificacion (ver tests/parser_integration).
    env = _build_env(tmp_path)
    files = [_inventory_file("01-codigo/cobol/A.cbl")]
    inventory = Inventory(
        run_id="run-1", source_package_hash=VALID_PACKAGE_HASH, manifest=_manifest(), files=files
    )
    output_path = env.canonical_dir / "01-codigo/cobol/A.cbl.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_text(
        _canonical_program(source_file="01-codigo/cobol/A.cbl").to_stable_json(), encoding="utf-8"
    )
    client = FakeParserClient(jar_path=tmp_path / "does-not-exist.jar", outcomes={})

    outcome = run_parsed_stage(
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        inventory=inventory,
        source_package_hash=VALID_PACKAGE_HASH,
        client=client,
    )

    assert outcome.succeeded
    assert client.calls == []


def test_valid_existing_artifact_is_reused_without_invoking_client(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    files = [_inventory_file("01-codigo/cobol/A.cbl")]
    inventory = Inventory(
        run_id="run-1", source_package_hash=VALID_PACKAGE_HASH, manifest=_manifest(), files=files
    )
    output_path = env.canonical_dir / "01-codigo/cobol/A.cbl.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_text(
        _canonical_program(source_file="01-codigo/cobol/A.cbl").to_stable_json(), encoding="utf-8"
    )
    client = FakeParserClient(jar_path=tmp_path / "parser.jar", outcomes={})
    client.jar_path.write_bytes(b"dummy")

    outcome = run_parsed_stage(
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        inventory=inventory,
        source_package_hash=VALID_PACKAGE_HASH,
        client=client,
    )

    assert outcome.succeeded
    assert client.calls == []


def test_invalid_existing_artifact_is_deleted_and_reprocessed(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    files = [_inventory_file("01-codigo/cobol/A.cbl")]
    inventory = Inventory(
        run_id="run-1", source_package_hash=VALID_PACKAGE_HASH, manifest=_manifest(), files=files
    )
    output_path = env.canonical_dir / "01-codigo/cobol/A.cbl.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_text("{not valid json", encoding="utf-8")
    client = FakeParserClient(
        jar_path=tmp_path / "parser.jar",
        outcomes={
            "01-codigo/cobol/A.cbl": ParseSuccess(
                _canonical_program(source_file="01-codigo/cobol/A.cbl")
            ),
        },
    )
    client.jar_path.write_bytes(b"dummy")

    outcome = run_parsed_stage(
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        inventory=inventory,
        source_package_hash=VALID_PACKAGE_HASH,
        client=client,
    )

    assert outcome.succeeded
    assert len(client.calls) == 1


def test_artifact_inconsistent_with_current_source_package_hash_is_reprocessed(
    tmp_path: Path,
) -> None:
    env = _build_env(tmp_path)
    files = [_inventory_file("01-codigo/cobol/A.cbl")]
    inventory = Inventory(
        run_id="run-1", source_package_hash=VALID_PACKAGE_HASH, manifest=_manifest(), files=files
    )
    output_path = env.canonical_dir / "01-codigo/cobol/A.cbl.json"
    output_path.parent.mkdir(parents=True)
    # Artefacto valido pero de OTRO source_package_hash (paquete distinto).
    stale = CanonicalProgram(
        program_name="PROG1",
        source_file="01-codigo/cobol/A.cbl",
        source_hash="b" * 64,
        source_package_hash="c" * 64,
        source_format=SourceFormat.FIXED,
        encoding="UTF-8",
        data_items=[],
        paragraphs=[],
        warnings=[],
        unsupported_constructs=[],
    )
    output_path.write_text(stale.to_stable_json(), encoding="utf-8")
    client = FakeParserClient(
        jar_path=tmp_path / "parser.jar",
        outcomes={
            "01-codigo/cobol/A.cbl": ParseSuccess(
                _canonical_program(source_file="01-codigo/cobol/A.cbl")
            ),
        },
    )
    client.jar_path.write_bytes(b"dummy")

    outcome = run_parsed_stage(
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        inventory=inventory,
        source_package_hash=VALID_PACKAGE_HASH,
        client=client,
    )

    assert outcome.succeeded
    assert len(client.calls) == 1


def test_missing_artifact_despite_prior_success_is_processed(tmp_path: Path) -> None:
    # Simula una etapa marcada SUCCEEDED previamente en RunState cuyo
    # artefacto desaparecio del filesystem: run_parsed_stage no confia en
    # RunState, siempre revalida contra el filesystem.
    env = _build_env(tmp_path)
    files = [_inventory_file("01-codigo/cobol/A.cbl")]
    inventory = Inventory(
        run_id="run-1", source_package_hash=VALID_PACKAGE_HASH, manifest=_manifest(), files=files
    )
    client = FakeParserClient(
        jar_path=tmp_path / "parser.jar",
        outcomes={
            "01-codigo/cobol/A.cbl": ParseSuccess(
                _canonical_program(source_file="01-codigo/cobol/A.cbl")
            ),
        },
    )
    client.jar_path.write_bytes(b"dummy")

    outcome = run_parsed_stage(
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        inventory=inventory,
        source_package_hash=VALID_PACKAGE_HASH,
        client=client,
    )

    assert outcome.succeeded
    assert len(client.calls) == 1


def test_output_paths_always_stay_within_canonical_dir(tmp_path: Path) -> None:
    env = _build_env(tmp_path)
    files = [
        _inventory_file("01-codigo/cobol/modulo/A.cbl"),
        _inventory_file("01-codigo/cobol/B.cob"),
    ]
    inventory = Inventory(
        run_id="run-1", source_package_hash=VALID_PACKAGE_HASH, manifest=_manifest(), files=files
    )
    client = FakeParserClient(
        jar_path=tmp_path / "parser.jar",
        outcomes={
            "01-codigo/cobol/modulo/A.cbl": ParseSuccess(
                _canonical_program(source_file="01-codigo/cobol/modulo/A.cbl")
            ),
            "01-codigo/cobol/B.cob": ParseSuccess(
                _canonical_program(source_file="01-codigo/cobol/B.cob")
            ),
        },
    )
    client.jar_path.write_bytes(b"dummy")

    run_parsed_stage(
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        inventory=inventory,
        source_package_hash=VALID_PACKAGE_HASH,
        client=client,
    )

    for call in client.calls:
        assert env.canonical_dir.resolve() in call["output_final_path"].resolve().parents
