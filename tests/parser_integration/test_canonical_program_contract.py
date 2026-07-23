"""Test de integracion: ejecuta el JAR real del parser Java (Prompt 4) y
valida su salida contra el contrato Pydantic CanonicalProgram (Fase A).

Requiere el JAR ya construido y un runtime Java 17 disponible como
`java` en PATH. Orden obligatorio:

    mvn -q -f parser/pom.xml package
    pytest -q -m integration

No corre en la suite por defecto (marcado `integration`). Maven no
depende de Python: esta validacion vive del lado Python exclusivamente.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from altamira_extractor.contracts import CanonicalProgram

REPO_ROOT = Path(__file__).resolve().parents[2]
JAR_PATH = REPO_ROOT / "parser" / "target" / "altamira-cobol-parser.jar"
FIXTURES_DIR = REPO_ROOT / "parser" / "src" / "test" / "resources" / "fixtures"
COMPREHENSIVE_FIXTURE = FIXTURES_DIR / "comprehensive.cbl"
INVALID_FIXTURE = FIXTURES_DIR / "invalid-syntax.cbl"
VALID_HASH = "a" * 64


def _require_jar() -> None:
    if not JAR_PATH.is_file():
        pytest.fail(
            f"{JAR_PATH} no existe. Ejecute primero: mvn -q -f parser/pom.xml package"
        )


def _run_jar(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - lista fija de argumentos, sin shell
        ["java", "-jar", str(JAR_PATH), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def canonical_program(tmp_path_factory: pytest.TempPathFactory) -> CanonicalProgram:
    _require_jar()
    output = tmp_path_factory.mktemp("canonical") / "comprehensive.json"

    result = _run_jar([
        "parse",
        "--input", str(COMPREHENSIVE_FIXTURE),
        "--output", str(output),
        "--source-package-hash", VALID_HASH,
        "--source-file", "01-codigo/cobol/comprehensive.cbl",
        "--format", "FIXED",
        "--encoding", "UTF-8",
    ])

    assert result.returncode == 0, (
        f"el JAR no devolvio exit code 0.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert result.stdout == "", "no debe escribirse nada en stdout (solo mensajes por stderr)"
    assert output.is_file(), "el JAR debe crear el archivo --output"

    json_text = output.read_text(encoding="utf-8")
    return CanonicalProgram.model_validate_json(json_text)


@pytest.mark.integration
def test_jar_output_validates_against_canonical_program_contract(
    canonical_program: CanonicalProgram,
) -> None:
    assert canonical_program.program_name == "COMPREH1"
    assert canonical_program.source_file == "01-codigo/cobol/comprehensive.cbl"
    assert canonical_program.source_format.value == "FIXED"
    assert canonical_program.encoding == "UTF-8"
    assert len(canonical_program.source_hash) == 64
    assert canonical_program.data_items
    assert canonical_program.paragraphs
    assert any(
        statement.kind.value == "EXEC_SQL"
        for paragraph in canonical_program.paragraphs
        for statement in paragraph.statements
    )


@pytest.mark.integration
def test_round_trip_is_byte_stable(canonical_program: CanonicalProgram) -> None:
    first = canonical_program.to_stable_json()
    restored = CanonicalProgram.model_validate_json(first)
    assert restored == canonical_program
    assert restored.to_stable_json() == first


@pytest.mark.integration
def test_no_absolute_paths_anywhere(canonical_program: CanonicalProgram) -> None:
    # RelativePath ya rechaza paths absolutos en la validacion Pydantic
    # (ensure_relative_path): si el JAR hubiera emitido uno, la fixture
    # canonical_program habria fallado antes de llegar a este test. Se deja
    # una verificacion explicita adicional para que la intencion del test
    # quede documentada por si el contrato cambiara en el futuro.
    assert not canonical_program.source_file.startswith("/")
    for item in canonical_program.data_items:
        if item.source_file is not None:
            assert not item.source_file.startswith("/")
    for paragraph in canonical_program.paragraphs:
        if paragraph.source_file is not None:
            assert not paragraph.source_file.startswith("/")
        for statement in paragraph.statements:
            if statement.source_file is not None:
                assert not statement.source_file.startswith("/")
            for access in statement.sql_access:
                if access.source_file is not None:
                    assert not access.source_file.startswith("/")


@pytest.mark.integration
def test_paragraph_aggregates_match_statement_union(
    canonical_program: CanonicalProgram,
) -> None:
    # CanonicalParagraph ya valida esto al construirse (Fase A); aqui se
    # recalcula independientemente para dejar constancia explicita.
    seen_statement_ids: set[str] = set()
    for paragraph in canonical_program.paragraphs:
        expected_read: list[str] = []
        expected_written: list[str] = []
        for statement in paragraph.statements:
            assert statement.statement_id not in seen_statement_ids, "statement_id duplicado"
            seen_statement_ids.add(statement.statement_id)
            for variable in statement.variables_read:
                if variable not in expected_read:
                    expected_read.append(variable)
            for variable in statement.variables_written:
                if variable not in expected_written:
                    expected_written.append(variable)
        assert paragraph.variables_read == expected_read
        assert paragraph.variables_written == expected_written


@pytest.mark.integration
def test_exit_code_two_on_invalid_arguments() -> None:
    _require_jar()
    result = _run_jar(["parse", "--input", str(COMPREHENSIVE_FIXTURE)], timeout=30)
    assert result.returncode == 2


@pytest.mark.integration
def test_exit_code_three_on_invalid_cobol(tmp_path: Path) -> None:
    _require_jar()
    output = tmp_path / "out.json"
    result = _run_jar([
        "parse",
        "--input", str(INVALID_FIXTURE),
        "--output", str(output),
        "--source-package-hash", VALID_HASH,
    ])
    assert result.returncode == 3
    assert not output.exists(), "no debe quedar output parcial ante un fallo de parseo"
