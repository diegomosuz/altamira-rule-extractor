"""Tests de resolucion del JAR del parser para `/ready` (cierre
correctivo 15B2-B, Seccion 4): la resolucion nunca depende de una unica
ruta absoluta hardcodeada -- funciona igual en ejecucion desde
repositorio, wheel instalado (layout `--target=/app` + `pyproject.toml`
copiado, ver comentario del Dockerfile) e imagen runtime, porque las
tres son el MISMO mecanismo (`_discover_repo_root`: camina hacia arriba
buscando `pyproject.toml`, nunca depende del CWD)."""

from __future__ import annotations

from pathlib import Path

from altamira_extractor.config import _default_parser_jar_path, _discover_repo_root


def test_default_parser_jar_path_is_derived_from_discovered_root_not_hardcoded() -> None:
    """Ejecucion desde repositorio: el JAR resuelve relativo a
    `_discover_repo_root()`, nunca a una constante de string."""
    repo_root = _discover_repo_root()
    jar_path = _default_parser_jar_path()
    assert jar_path == repo_root / "parser" / "target" / "altamira-cobol-parser.jar"


def test_discover_repo_root_works_from_an_arbitrary_synthetic_root(tmp_path: Path) -> None:
    """Simula el layout `wheel instalado`/imagen runtime (Dockerfile:
    segunda instalacion `--target=/app` + `pyproject.toml` copiado, sin
    secretos, para que `_discover_repo_root` encuentre `/app` como
    raiz): un `pyproject.toml` sintetico en CUALQUIER directorio, sin
    relacion con el repositorio real, sigue siendo encontrado por el
    mismo mecanismo -- la resolucion no esta atada a la ruta real de
    este checkout."""
    synthetic_root = tmp_path / "app"
    synthetic_root.mkdir()
    (synthetic_root / "pyproject.toml").write_text("[project]\nname='synthetic'\n")
    nested_module_file = synthetic_root / "src" / "altamira_extractor" / "config.py"
    nested_module_file.parent.mkdir(parents=True)
    nested_module_file.write_text("# archivo sintetico, nunca importado")

    discovered = _discover_repo_root(start=nested_module_file)

    assert discovered == synthetic_root
    assert discovered != _discover_repo_root()


def test_discover_repo_root_raises_when_no_pyproject_toml_is_an_ancestor(tmp_path: Path) -> None:
    """Layout `wheel instalado` SIN el truco del Dockerfile (solo
    site-packages, sin `pyproject.toml` copiado): falla explicitamente,
    nunca resuelve en silencio a un directorio incorrecto -- documenta
    por que el Dockerfile instala el wheel una segunda vez con
    `--target=/app`."""
    isolated_file = tmp_path / "site-packages" / "altamira_extractor" / "config.py"
    isolated_file.parent.mkdir(parents=True)
    isolated_file.write_text("# sin pyproject.toml en ningun ancestro")

    try:
        _discover_repo_root(start=isolated_file)
        raised = False
    except RuntimeError:
        raised = True
    assert raised
