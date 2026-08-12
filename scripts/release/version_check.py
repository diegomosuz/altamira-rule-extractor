"""Valida consistencia de version entre las tres fuentes de verdad
actuales (Fase 15B4-A2 Seccion 22: nunca hubo una sola) y reporta el
SHA de git actual + si el working tree tiene cambios sin commitear.

Nunca modifica ningun archivo. Nunca hace commit/push/tag. Solo
lectura + stdout/exit code -- pensado para invocarse desde CI/release
tooling externo, nunca desde la aplicacion en runtime.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from xml.etree import ElementTree

REPO_ROOT = Path(__file__).resolve().parents[2]
_POM_NS = {"m": "http://maven.apache.org/POM/4.0.0"}


def _read_pyproject_version() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def _read_pom_version() -> str:
    tree = ElementTree.parse(REPO_ROOT / "parser" / "pom.xml")
    root = tree.getroot()
    version_el = root.find("m:version", _POM_NS)
    if version_el is None or not version_el.text:
        raise ValueError("parser/pom.xml: <project><version> no encontrado")
    return version_el.text.strip()


def _read_package_version() -> str:
    text = (REPO_ROOT / "src" / "altamira_extractor" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if match is None:
        raise ValueError("src/altamira_extractor/__init__.py: __version__ no encontrado")
    return match.group(1)


def _git(*args: str) -> str:
    result = subprocess.run(  # noqa: S603 - argumentos fijos, sin shell, sin input externo
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def collect_version_report() -> dict[str, object]:
    """Nunca lanza por inconsistencia -- devuelve el reporte completo;
    el llamador (CLI de abajo) decide el exit code."""
    pyproject_version = _read_pyproject_version()
    pom_version = _read_pom_version()
    package_version = _read_package_version()
    versions = {pyproject_version, pom_version, package_version}

    git_sha = _git("rev-parse", "HEAD")
    git_branch = _git("branch", "--show-current")
    dirty = bool(_git("status", "--porcelain"))

    return {
        "pyproject_version": pyproject_version,
        "parser_pom_version": pom_version,
        "package_version": package_version,
        "consistent": len(versions) == 1,
        "git_sha": git_sha,
        "git_branch": git_branch,
        "working_tree_dirty": dirty,
    }


def main() -> int:
    report = collect_version_report()
    print(json.dumps(report, indent=2))
    if not report["consistent"]:
        print(
            "ERROR: version inconsistente entre pyproject.toml/parser/pom.xml/"
            "__init__.py -- ver detalle arriba",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
