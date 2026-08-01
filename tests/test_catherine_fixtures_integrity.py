"""Guardia de integridad de los golden fixtures Catherine (Fase 3 de la
ampliacion semantica, soporte nivel 88): verifica el SHA-256 completo (64
caracteres hexadecimales, nunca truncado) de cada archivo bajo
`examples/PAQUETE_SINTETICO_CATHERINE*` contra el valor confirmado
manualmente durante la auditoria forense.

Deliberadamente en la suite NO-integration (no requiere JAR, Neo4j ni
Docker): si alguien modifica accidentalmente un golden fixture -- incluso
sin tocar ningun test de integracion -- esta prueba lo detecta de
inmediato en `pytest -m "not integration"`.

No depende de stash, File Library, directorios personales ni contenido
generado manualmente: los cuatro archivos viven bajo `examples/` (mismo
directorio que el resto de paquetes sinteticos ya versionados) y esta
prueba solo lee bytes de ese directorio del propio checkout."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_DIR = REPO_ROOT / "examples"

# Hashes SHA-256 completos (64 caracteres hex), confirmados manualmente
# contra los archivos reales durante la auditoria forense de la Fase 3.
CATHERINE_ORIGINAL_SHA256 = "14e45c07bcadbbba8a7dd4dd59b191f78a9824b022ca2b920015666bd97c68ed"
CATHERINE_CORRECTED_SHA256 = "e8a41fc98ab54aa7cddaf7cb454129af1e5d3d82016b59135c6cb4a4938e34a3"
CATHERINE_VALIDATION_TXT_SHA256 = "1c85c634b30174e918495539575bfa1539f987d24aea9d762ac0dec41500e833"
COBOL1_JCL_SHA256 = "50115ada5afcc6e80979af87f6440db673e238f9e9b787096b38db431b102da6"

_FIXTURES = {
    "PAQUETE_SINTETICO_CATHERINE.zip": CATHERINE_ORIGINAL_SHA256,
    "PAQUETE_SINTETICO_CATHERINE_CORREGIDO_APP_ACTUAL.zip": CATHERINE_CORRECTED_SHA256,
    "PAQUETE_SINTETICO_CATHERINE_CORREGIDO_VALIDACION.txt": CATHERINE_VALIDATION_TXT_SHA256,
    "cobol1.jcl": COBOL1_JCL_SHA256,
}


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("filename", sorted(_FIXTURES))
def test_catherine_fixture_sha256_matches_confirmed_value(filename: str) -> None:
    path = EXAMPLES_DIR / filename
    if not path.is_file():
        pytest.skip(
            f"{filename} ausente en examples/ (fixture opcional no versionada en este "
            "checkout); ver docs/LEVEL_88_SUPPORT.md"
        )
    expected = _FIXTURES[filename]
    assert len(expected) == 64, f"hash esperado para {filename} no tiene 64 caracteres hex"
    actual = _sha256_of(path)
    assert actual == expected, (
        f"{filename} cambio de contenido respecto al golden fixture confirmado "
        f"(esperado {expected}, obtenido {actual}); los golden fixtures Catherine "
        "deben permanecer byte a byte identicos"
    )


def test_cobol1_jcl_is_byte_identical_to_embedded_cobol_in_original_zip() -> None:
    """cobol1.jcl no es JCL real: es una copia byte-identica de
    01-codigo/cobol/cobol1.cbl dentro de PAQUETE_SINTETICO_CATHERINE.zip.
    Confirmado por auditoria; esta prueba lo mantiene verificado ante
    cualquier cambio futuro."""
    jcl_path = EXAMPLES_DIR / "cobol1.jcl"
    zip_path = EXAMPLES_DIR / "PAQUETE_SINTETICO_CATHERINE.zip"
    if not jcl_path.is_file() or not zip_path.is_file():
        pytest.skip("cobol1.jcl o el ZIP original ausentes en este checkout")

    with zipfile.ZipFile(zip_path) as zf:
        embedded_cobol_bytes = zf.read("01-codigo/cobol/cobol1.cbl")

    assert jcl_path.read_bytes() == embedded_cobol_bytes
