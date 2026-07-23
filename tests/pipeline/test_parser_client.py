"""Tests unitarios de ProLeapParserClient: sin JAR ni Java reales.

`subprocess.run` se reemplaza por un doble de prueba: cada test controla
exit code, stdout/stderr y, cuando corresponde, el contenido que "escribe"
el JAR en `--output` (simulando el efecto real de CanonicalProgramWriter).
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.contracts.canonical import CanonicalProgram
from altamira_extractor.contracts.enums import SourceFormat, TextEncoding
from altamira_extractor.pipeline import parser_client as pc_module
from altamira_extractor.pipeline.errors import ParserContractViolationError, ParserUnavailableError
from altamira_extractor.pipeline.parser_client import (
    ParseFailure,
    ParseSuccess,
    ProLeapParserClient,
)

VALID_PACKAGE_HASH = "a" * 64


@dataclass
class Env:
    run_root: Path
    extracted_dir: Path
    canonical_dir: Path
    cobol_relative: str
    input_path: Path
    sha256: str
    output_final_path: Path
    client: ProLeapParserClient


def _build_env(
    tmp_path: Path, *, cobol_relative: str = "01-codigo/cobol/PROG1.cbl"
) -> Env:
    run_root = tmp_path / "run"
    extracted_dir = run_root / "work" / "extracted"
    canonical_dir = run_root / "artifacts" / "02-canonical"
    extracted_dir.mkdir(parents=True)
    canonical_dir.mkdir(parents=True)

    jar_path = tmp_path / "parser.jar"
    jar_path.write_bytes(b"dummy")

    input_path = extracted_dir / cobol_relative
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_bytes(b"       IDENTIFICATION DIVISION.\n")
    sha256 = hashlib.sha256(input_path.read_bytes()).hexdigest()

    output_final_path = canonical_dir / f"{cobol_relative}.json"

    client = ProLeapParserClient(java_bin="java", jar_path=jar_path, timeout_seconds=5.0)

    return Env(
        run_root=run_root,
        extracted_dir=extracted_dir,
        canonical_dir=canonical_dir,
        cobol_relative=cobol_relative,
        input_path=input_path,
        sha256=sha256,
        output_final_path=output_final_path,
        client=client,
    )


def _canonical_program_json(
    *,
    source_file: str,
    source_hash: str,
    source_package_hash: str = VALID_PACKAGE_HASH,
    source_format: str = "FIXED",
) -> str:
    program = CanonicalProgram(
        program_name="PROG1",
        source_file=source_file,
        source_hash=source_hash,
        source_package_hash=source_package_hash,
        source_format=source_format,  # type: ignore[arg-type]
        encoding="UTF-8",
        data_items=[],
        paragraphs=[],
        warnings=[],
        unsupported_constructs=[],
    )
    return program.to_stable_json()


def _output_path_from_args(args: list[str]) -> Path:
    return Path(args[args.index("--output") + 1])


def _make_fake_run(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    write_json: str | None = None,
    raise_timeout: bool = False,
    raise_not_found: bool = False,
) -> Any:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        if raise_not_found:
            raise FileNotFoundError(f"no such file or directory: {args[0]!r}")
        if raise_timeout:
            raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 0))
        if write_json is not None:
            _output_path_from_args(args).write_text(write_json, encoding="utf-8")
        return subprocess.CompletedProcess(
            args, returncode=returncode, stdout=stdout, stderr=stderr
        )

    fake_run.calls = calls  # type: ignore[attr-defined]
    return fake_run


def _parse(env: Env, *, copybook_dirs: list[Path] | None = None) -> Any:
    return env.client.parse_program(
        input_path=env.input_path,
        output_final_path=env.output_final_path,
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        source_package_hash=VALID_PACKAGE_HASH,
        source_file=env.cobol_relative,
        expected_sha256=env.sha256,
        source_format=SourceFormat.FIXED,
        detected_encoding=TextEncoding.UTF_8,
        copybook_dirs=copybook_dirs or [],
    )


def test_builds_exact_argument_list_without_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    copybook_dir1 = env.extracted_dir / "01-codigo/copybooks"
    copybook_dir2 = env.extracted_dir / "01-codigo/copybooks2"
    fake_run = _make_fake_run(
        returncode=0,
        write_json=_canonical_program_json(
            source_file=env.cobol_relative, source_hash=env.sha256
        ),
    )
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    result = _parse(env, copybook_dirs=[copybook_dir1, copybook_dir2])

    assert isinstance(result, ParseSuccess)
    [call_args] = fake_run.calls  # type: ignore[attr-defined]
    assert call_args[0] == "java"
    assert call_args[1] == "-jar"
    assert call_args[2] == str(env.client.jar_path)
    assert call_args[3] == "parse"
    assert call_args[4] == "--input"
    assert call_args[5] == str(env.input_path)
    assert call_args[6] == "--output"
    assert call_args[7] != str(env.output_final_path), "nunca se pasa el path final al JAR"
    assert Path(call_args[7]).parent == env.output_final_path.parent
    assert call_args[8:12] == [
        "--source-package-hash", VALID_PACKAGE_HASH, "--source-file", env.cobol_relative,
    ]
    assert call_args[12:14] == ["--format", "FIXED"]
    assert call_args[14:16] == ["--encoding", "UTF-8"]
    assert call_args[16:] == [
        "--copybook-dir", str(copybook_dir1), "--copybook-dir", str(copybook_dir2),
    ]


def test_subprocess_invoked_with_shell_false_and_expected_kwargs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    captured_kwargs: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured_kwargs.update(kwargs)
        _output_path_from_args(args).write_text(
            _canonical_program_json(source_file=env.cobol_relative, source_hash=env.sha256),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    _parse(env)

    assert captured_kwargs["shell"] is False
    assert captured_kwargs["capture_output"] is True
    assert captured_kwargs["text"] is True
    assert captured_kwargs["encoding"] == "utf-8"
    assert captured_kwargs["errors"] == "replace"
    assert captured_kwargs["timeout"] == 5.0
    assert captured_kwargs["check"] is False


def test_cob_extension_source_file_round_trips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path, cobol_relative="01-codigo/cobol/OTHER.cob")
    fake_run = _make_fake_run(
        returncode=0,
        write_json=_canonical_program_json(source_file=env.cobol_relative, source_hash=env.sha256),
    )
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    result = _parse(env)

    assert isinstance(result, ParseSuccess)
    assert result.canonical_program.source_file == "01-codigo/cobol/OTHER.cob"


@pytest.mark.parametrize(
    ("detected", "expected_flag"),
    [
        (TextEncoding.UTF_8, "UTF-8"),
        (TextEncoding.WINDOWS_1252, "windows-1252"),
        (TextEncoding.ISO_8859_1, "ISO-8859-1"),
    ],
)
def test_encoding_is_mapped_to_java_charset_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, detected: TextEncoding, expected_flag: str
) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(
        returncode=0,
        write_json=_canonical_program_json(source_file=env.cobol_relative, source_hash=env.sha256),
    )
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    result = env.client.parse_program(
        input_path=env.input_path,
        output_final_path=env.output_final_path,
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        source_package_hash=VALID_PACKAGE_HASH,
        source_file=env.cobol_relative,
        expected_sha256=env.sha256,
        source_format=SourceFormat.FIXED,
        detected_encoding=detected,
        copybook_dirs=[],
    )

    assert isinstance(result, ParseSuccess)
    [call_args] = fake_run.calls  # type: ignore[attr-defined]
    assert call_args[call_args.index("--encoding") + 1] == expected_flag


def test_unresolved_encoding_fails_program_without_invoking_java(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(returncode=0)
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    result = env.client.parse_program(
        input_path=env.input_path,
        output_final_path=env.output_final_path,
        run_root=env.run_root,
        extracted_dir=env.extracted_dir,
        canonical_dir=env.canonical_dir,
        source_package_hash=VALID_PACKAGE_HASH,
        source_file=env.cobol_relative,
        expected_sha256=env.sha256,
        source_format=SourceFormat.FIXED,
        detected_encoding=None,
        copybook_dirs=[],
    )

    assert isinstance(result, ParseFailure)
    assert result.reason == "ENCODING_UNRESOLVED"
    assert fake_run.calls == []  # type: ignore[attr-defined]


def test_missing_jar_raises_parser_unavailable_without_invoking_java(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    env.client.jar_path.unlink()
    fake_run = _make_fake_run(returncode=0)
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserUnavailableError):
        _parse(env)

    assert fake_run.calls == []  # type: ignore[attr-defined]


def test_missing_java_binary_raises_parser_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(raise_not_found=True)
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserUnavailableError):
        _parse(env)


def test_timeout_is_a_recoverable_program_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(raise_timeout=True)
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    result = _parse(env)

    assert isinstance(result, ParseFailure)
    assert result.reason == "TIMEOUT"


def test_exit_code_two_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(returncode=2, stderr="argumentos invalidos")
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserContractViolationError):
        _parse(env)


def test_exit_code_three_is_recoverable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(returncode=3, stderr="syntax error linea 10")
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    result = _parse(env)

    assert isinstance(result, ParseFailure)
    assert result.reason == "PARSE_ERROR"
    assert "syntax error linea 10" in result.message


def test_exit_code_four_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(returncode=4, stderr="no se pudo leer --input")
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserContractViolationError):
        _parse(env)


def test_exit_code_five_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(returncode=5, stderr="error interno inesperado")
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserContractViolationError):
        _parse(env)


def test_unexpected_stdout_is_fatal_even_with_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(
        returncode=0,
        stdout="esto no deberia aparecer nunca",
        write_json=_canonical_program_json(source_file=env.cobol_relative, source_hash=env.sha256),
    )
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserContractViolationError):
        _parse(env)


def test_exit_zero_without_output_file_is_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(returncode=0)  # no write_json: el JAR "no crea" el archivo
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserContractViolationError):
        _parse(env)


def test_invalid_json_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(returncode=0, write_json="{not valid json")
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserContractViolationError):
        _parse(env)


def test_source_file_mismatch_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(
        returncode=0,
        write_json=_canonical_program_json(
            source_file="01-codigo/cobol/OTRO.cbl", source_hash=env.sha256
        ),
    )
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserContractViolationError):
        _parse(env)


def test_source_hash_mismatch_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(
        returncode=0,
        write_json=_canonical_program_json(source_file=env.cobol_relative, source_hash="b" * 64),
    )
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserContractViolationError):
        _parse(env)


def test_source_package_hash_mismatch_is_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(
        returncode=0,
        write_json=_canonical_program_json(
            source_file=env.cobol_relative, source_hash=env.sha256, source_package_hash="c" * 64
        ),
    )
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserContractViolationError):
        _parse(env)


def test_file_modified_after_inventoried_is_fatal_before_invoking_java(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    env.input_path.write_bytes(b"       CONTENIDO MODIFICADO.\n")  # hash real ya no coincide
    fake_run = _make_fake_run(returncode=0)
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserContractViolationError):
        _parse(env)

    assert fake_run.calls == []  # type: ignore[attr-defined]


def test_temporary_file_removed_after_recoverable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(returncode=3, stderr="syntax error")
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    _parse(env)

    leftovers = list(env.output_final_path.parent.glob("*.tmp"))
    assert leftovers == []
    assert not env.output_final_path.exists()


def test_temporary_file_removed_after_fatal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(returncode=5, stderr="boom")
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserContractViolationError):
        _parse(env)

    leftovers = list(env.output_final_path.parent.glob("*.tmp"))
    assert leftovers == []


def test_success_never_leaves_output_final_path_passed_to_the_jar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    fake_run = _make_fake_run(
        returncode=0,
        write_json=_canonical_program_json(source_file=env.cobol_relative, source_hash=env.sha256),
    )
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    result = _parse(env)

    assert isinstance(result, ParseSuccess)
    assert env.output_final_path.is_file()
    leftovers = list(env.output_final_path.parent.glob("*.tmp"))
    assert leftovers == []


def test_output_path_outside_canonical_dir_is_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    outside_path = tmp_path / "outside" / f"{Path(env.cobol_relative).name}.json"
    fake_run = _make_fake_run(returncode=0)
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserContractViolationError):
        env.client.parse_program(
            input_path=env.input_path,
            output_final_path=outside_path,
            run_root=env.run_root,
            extracted_dir=env.extracted_dir,
            canonical_dir=env.canonical_dir,
            source_package_hash=VALID_PACKAGE_HASH,
            source_file=env.cobol_relative,
            expected_sha256=env.sha256,
            source_format=SourceFormat.FIXED,
            detected_encoding=TextEncoding.UTF_8,
            copybook_dirs=[],
        )
    assert fake_run.calls == []  # type: ignore[attr-defined]


def test_copybook_dir_outside_extracted_is_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _build_env(tmp_path)
    outside_copybook_dir = tmp_path / "outside-copybooks"
    fake_run = _make_fake_run(returncode=0)
    monkeypatch.setattr(pc_module.subprocess, "run", fake_run)

    with pytest.raises(ParserContractViolationError):
        _parse(env, copybook_dirs=[outside_copybook_dir])

    assert fake_run.calls == []  # type: ignore[attr-defined]


def test_stderr_is_sanitized_and_truncated() -> None:
    run_root = Path("/some/run/dir")
    # La ruta absoluta va al principio para que sobreviva a la truncacion
    # (que ocurre despues del reemplazo, sobre el texto ya sanitizado).
    long_text = str(run_root) + ("x" * 5000)

    sanitized = pc_module._sanitize_diagnostic(long_text, run_root=run_root)

    assert str(run_root) not in sanitized
    assert "<run>" in sanitized
    assert "truncado" in sanitized
    assert len(sanitized) < len(long_text)


def test_stderr_short_and_clean_is_left_mostly_intact() -> None:
    run_root = Path("/some/run/dir")
    text = "syntax error in line 4"

    sanitized = pc_module._sanitize_diagnostic(text, run_root=run_root)

    assert sanitized == text
