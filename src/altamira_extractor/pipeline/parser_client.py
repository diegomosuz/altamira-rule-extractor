"""Invocacion del JAR parser-cli (Prompt 4) para un unico programa COBOL.

Responsabilidades de `ProLeapParserClient.parse_program`:

- construir la lista explicita de argumentos e invocar `java -jar` sin
  shell (`subprocess.run(..., shell=False)`);
- escribir el JSON de salida en un temporal unico dentro del mismo
  directorio del destino final (nunca se le pasa al JAR el path final);
- validar inmediatamente el resultado contra `CanonicalProgram` y contra
  los valores esperados de Inventory/RunState;
- promover el temporal al destino final via `os.replace` SOLO si todo
  valida, y eliminarlo en `finally` en cualquier otro caso.

Distingue dos clases de resultado:

- Fallos FATALES (`ParserUnavailableError` / `ParserContractViolationError`,
  ambas excepciones): indican que algo esta mal a nivel de la etapa PARSED
  completa (JAR/Java ausentes, argumentos invalidos, el parser violo su
  contrato documentado, o los datos de origen no son consistentes con lo
  que INVENTORIED registro). El llamador (`parsed_stage.py`) debe abortar
  toda la etapa, no solo el programa en curso.
- Fallos RECUPERABLES (`ParseFailure`, valor de retorno normal): propios
  de un programa COBOL puntual (exit code 3, timeout, encoding sin
  resolver). El llamador puede continuar con el resto de los programas.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from ..contracts.canonical import CanonicalProgram
from ..contracts.enums import SourceFormat, TextEncoding
from .errors import ParserContractViolationError, ParserUnavailableError

_EXIT_SUCCESS = 0
_EXIT_PARSE_ERROR = 3

# Normalizacion del unico eje que ProLeapParserClient controla: el nombre
# de Charset que se le pasa a `Charset.forName` en el JAR. TextEncoding ya
# esta acotado a estos tres valores (contracts/enums.py); no hay logica de
# adivinanza aqui, solo el mapeo 1:1 al nombre canonico de Java.
_JAVA_CHARSET_BY_DETECTED: dict[TextEncoding, str] = {
    TextEncoding.UTF_8: "UTF-8",
    TextEncoding.WINDOWS_1252: "windows-1252",
    TextEncoding.ISO_8859_1: "ISO-8859-1",
}

_DIAGNOSTIC_MAX_CHARS = 2000


@dataclass(frozen=True)
class ParseSuccess:
    canonical_program: CanonicalProgram


@dataclass(frozen=True)
class ParseFailure:
    """Fallo recuperable de UN programa: el llamador continua con el resto."""

    reason: Literal["PARSE_ERROR", "TIMEOUT", "ENCODING_UNRESOLVED"]
    message: str


ParseOutcome = ParseSuccess | ParseFailure


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _is_contained(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def _sanitize_diagnostic(text: str, *, run_root: Path) -> str:
    """Elimina rutas absolutas conocidas del run y acota la longitud.

    Nunca se persiste stderr completo ni sin limite (.claude/rules): se
    reemplaza el prefijo absoluto del run por un marcador estable y, si el
    texto sigue siendo largo, se trunca dejando constancia explicita.
    """
    sanitized = text.replace(str(run_root), "<run>").replace(run_root.as_posix(), "<run>")
    if len(sanitized) > _DIAGNOSTIC_MAX_CHARS:
        original_len = len(sanitized)
        sanitized = (
            sanitized[:_DIAGNOSTIC_MAX_CHARS]
            + f" ...[truncado, {original_len} caracteres originales]"
        )
    return sanitized


@dataclass(frozen=True)
class ProLeapParserClient:
    """Capa pequena e inyectable: sin estado mutable, sin cola ni hilos."""

    java_bin: str
    jar_path: Path
    timeout_seconds: float

    def parse_program(
        self,
        *,
        input_path: Path,
        output_final_path: Path,
        run_root: Path,
        extracted_dir: Path,
        canonical_dir: Path,
        source_package_hash: str,
        source_file: str,
        expected_sha256: str,
        source_format: SourceFormat,
        detected_encoding: TextEncoding | None,
        copybook_dirs: list[Path],
    ) -> ParseOutcome:
        if detected_encoding is None:
            return ParseFailure(
                reason="ENCODING_UNRESOLVED",
                message=(
                    f"{source_file}: detected_encoding no resuelto en Inventory; no se "
                    "invoca el parser sin un encoding confirmado (nunca se adivina UTF-8)"
                ),
            )

        if not self.jar_path.is_file():
            raise ParserUnavailableError(
                f"no se encontro el JAR del parser ({self.jar_path.name}); "
                "ejecute primero: mvn -q -f parser/pom.xml package"
            )

        if not input_path.is_file():
            raise ParserContractViolationError(
                f"{source_file}: el archivo fuente no existe o no es un archivo regular "
                "(cambio desde INVENTORIED)"
            )

        if not _is_contained(input_path, extracted_dir):
            raise ParserContractViolationError(
                f"{source_file}: la ruta resuelta del archivo fuente escapa de work/extracted"
            )

        actual_sha256 = _sha256_of(input_path)
        if actual_sha256 != expected_sha256:
            raise ParserContractViolationError(
                f"{source_file}: el SHA-256 del archivo cambio desde INVENTORIED "
                "(se modifico o corrompio)"
            )

        for copybook_dir in copybook_dirs:
            if not _is_contained(copybook_dir, extracted_dir):
                raise ParserContractViolationError(
                    "un directorio de copybooks escapa de work/extracted"
                )

        if not _is_contained(output_final_path, canonical_dir):
            raise ParserContractViolationError(
                f"{source_file}: el destino del artefacto canonico escapa de "
                "artifacts/02-canonical/"
            )

        encoding_name = _JAVA_CHARSET_BY_DETECTED[detected_encoding]

        output_final_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{output_final_path.name}.", suffix=".tmp", dir=str(output_final_path.parent)
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            args = [
                self.java_bin,
                "-jar",
                str(self.jar_path),
                "parse",
                "--input",
                str(input_path),
                "--output",
                str(tmp_path),
                "--source-package-hash",
                source_package_hash,
                "--source-file",
                source_file,
                "--format",
                source_format.value,
                "--encoding",
                encoding_name,
            ]
            for copybook_dir in copybook_dirs:
                args.extend(["--copybook-dir", str(copybook_dir)])

            try:
                completed = subprocess.run(  # noqa: S603 - lista fija, shell=False
                    args,
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise ParserUnavailableError(
                    f"no se pudo ejecutar el binario java ({self.java_bin!r}): {exc}"
                ) from exc
            except subprocess.TimeoutExpired:
                return ParseFailure(
                    reason="TIMEOUT",
                    message=f"{source_file}: timeout tras {self.timeout_seconds}s parseando",
                )

            if completed.stdout:
                raise ParserContractViolationError(
                    f"{source_file}: el parser escribio en stdout de forma inesperada "
                    f"(viola el contrato documentado): "
                    f"{_sanitize_diagnostic(completed.stdout, run_root=run_root)}"
                )

            if completed.returncode == _EXIT_PARSE_ERROR:
                return ParseFailure(
                    reason="PARSE_ERROR",
                    message=(
                        f"{source_file}: exit code 3 (error de preprocesamiento o parseo "
                        f"COBOL): {_sanitize_diagnostic(completed.stderr, run_root=run_root)}"
                    ),
                )

            if completed.returncode != _EXIT_SUCCESS:
                raise ParserContractViolationError(
                    f"{source_file}: exit code {completed.returncode} (fatal): "
                    f"{_sanitize_diagnostic(completed.stderr, run_root=run_root)}"
                )

            if not tmp_path.is_file():
                raise ParserContractViolationError(
                    f"{source_file}: el parser devolvio exit 0 pero no genero el archivo "
                    "de salida"
                )

            json_text = tmp_path.read_text(encoding="utf-8")
            try:
                canonical = CanonicalProgram.model_validate_json(json_text)
            except ValidationError as exc:
                raise ParserContractViolationError(
                    f"{source_file}: el JSON del parser no valida contra CanonicalProgram: {exc}"
                ) from exc

            self._check_cross_fields(
                canonical,
                source_file=source_file,
                expected_sha256=expected_sha256,
                source_package_hash=source_package_hash,
            )

            os.replace(tmp_path, output_final_path)
            return ParseSuccess(canonical_program=canonical)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    @staticmethod
    def _check_cross_fields(
        canonical: CanonicalProgram,
        *,
        source_file: str,
        expected_sha256: str,
        source_package_hash: str,
    ) -> None:
        if canonical.source_file != source_file:
            raise ParserContractViolationError(
                f"{source_file}: CanonicalProgram.source_file no coincide "
                f"({canonical.source_file!r})"
            )
        if canonical.source_hash != expected_sha256:
            raise ParserContractViolationError(
                f"{source_file}: CanonicalProgram.source_hash no coincide con "
                "InventoryFile.sha256"
            )
        if canonical.source_package_hash != source_package_hash:
            raise ParserContractViolationError(
                f"{source_file}: CanonicalProgram.source_package_hash no coincide con "
                "RunState.source_package_hash"
            )
        if canonical.source_format == SourceFormat.AUTO:
            raise ParserContractViolationError(
                f"{source_file}: CanonicalProgram.source_format es AUTO (nunca deberia "
                "persistirse un formato sin resolver)"
            )
