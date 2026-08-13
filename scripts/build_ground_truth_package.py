"""Construye examples/PAQUETE_SINTETICO_GROUND_TRUTH_FASE_15D.zip de forma
deterministica a partir de config/ground_truth/fixtures/ y del catalogo real
config/ground_truth/synthetic_engineering.yaml (Fase 15B4-CANDIDATE-QUALITY-5D,
cierre de P1-CORPUS-GAP-GT; corregido en 5D-SAFETY seccion 1).

Un unico paquete multiprograma versionado que contiene EXACTAMENTE los
fixtures del catalogo PRODUCTIVO formal (14 programas <-> 14 GT cases, 1:1,
sin huerfanos) -- reemplaza la dependencia de scripts ad hoc/temporales para
la evidencia de release qualification (Seccion 22/23 de 5D: reproducible desde
repo limpio, sin timestamps/IDs aleatorios). BY_REFERENCE_OUTPUT (caller/
callee GTBRCLR1/GTBRCLE1) NO se incluye aqui -- es DEFERRED_UNSUPPORTED,
shadow-only, y vive en su propio catalogo separado
(config/ground_truth/shadow_interprocedural.yaml), ejercitado por
tests/pipeline/test_ground_truth_by_reference_output_integration.py con su
propio paquete sintetico aislado.

Ejecutar: python scripts/build_ground_truth_package.py
"""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES_DIR = _REPO_ROOT / "config" / "ground_truth" / "fixtures"
_OUTPUT_PATH = _REPO_ROOT / "examples" / "PAQUETE_SINTETICO_GROUND_TRUTH_FASE_15D.zip"

_MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Ground Truth Sintetico Fase 15D"/>
  <operation logical-name="OP-GROUND-TRUTH-FASE-15D"
             description="Corpus GT formal completo (Fase 15B4-CANDIDATE-QUALITY-5D):
             todos los fixtures de synthetic_engineering.yaml en un paquete versionado"/>
  <implementation version="1.0">
    <entry-program>GTSTNEG1</entry-program>
    <entry-program>GTNEG001</entry-program>
    <entry-program>GTCALC001</entry-program>
    <entry-program>GTCALC002</entry-program>
    <entry-program>GTCALC003</entry-program>
    <entry-program>GTCALC004</entry-program>
    <entry-program>GTCALC005</entry-program>
    <entry-program>GTCALC006</entry-program>
    <entry-program>GTDVAL01</entry-program>
    <entry-program>GTL88001</entry-program>
    <entry-program>GTRC001</entry-program>
    <entry-program>GTSQLST1</entry-program>
    <entry-program>GTSQLCD1</entry-program>
    <entry-program>GTST001</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_GT_15D" ddl="02-parametria/ddl/PARAM_GT_15D.sql"/>
  </parameter-tables>
</altamira-package>
"""

_PARAM_TABLE_DDL = (
    b"CREATE TABLE PARAM_GT_15D (COD_GT CHAR(4) NOT NULL, DESCRIPCION VARCHAR(100));\n"
)

_FIXTURE_FILENAMES = [
    "gt_state_transition_negative_001.cbl",
    "gt_negative_001.cbl",
    "gt_calculation_001.cbl",
    "gt_calculation_unconditional_001.cbl",
    "gt_calculation_unconditional_002.cbl",
    "gt_calculation_add_001.cbl",
    "gt_calculation_subtract_001.cbl",
    "gt_calculation_divide_001.cbl",
    "gt_declared_value_return_code_001.cbl",
    "gt_level88_return_code_001.cbl",
    "gt_return_code_001.cbl",
    "gt_sql_select_into_state_transition_001.cbl",
    "gt_sqlcode_evaluate_state_transition_001.cbl",
    "gt_state_transition_001.cbl",
]


def _regular_file_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def build() -> Path:
    _OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(_OUTPUT_PATH, "w") as zf:
        zf.writestr(_regular_file_info("manifest.xml"), _MANIFEST_XML)
        zf.writestr(
            _regular_file_info("02-parametria/ddl/PARAM_GT_15D.sql"), _PARAM_TABLE_DDL
        )
        for filename in _FIXTURE_FILENAMES:
            source = _FIXTURES_DIR / filename
            zf.writestr(
                _regular_file_info(f"01-codigo/cobol/{filename}"), source.read_bytes()
            )
    return _OUTPUT_PATH


if __name__ == "__main__":
    output = build()
    print(f"escrito: {output}")
