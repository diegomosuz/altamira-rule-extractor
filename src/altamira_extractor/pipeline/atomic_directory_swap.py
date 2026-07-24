"""Reemplazo atomico de un directorio de artefactos completo.

Patron compartido por RULE_DRAFTS_GENERATED y GUARDRAILS_APPLIED (mismo
mecanismo ya usado por CONTEXTS_BUILT en `contexts_built_stage.py`, no
modificado aqui): Windows no soporta reemplazar atomicamente un
directorio no vacio existente, asi que se usan unicamente renames hacia
nombres inexistentes (backup + promote), con restauracion inmediata si
el segundo rename falla."""

from __future__ import annotations

import os
import secrets
import shutil
from collections.abc import Callable
from pathlib import Path


def cleanup_orphan_directories(artifacts_dir: Path, dir_name: str) -> None:
    """Limpia temporales/backups huerfanos de una corrida previa
    interrumpida, acotado al patron de nombres de ESTA etapa — nunca se
    confunden con la salida canonica."""
    if not artifacts_dir.is_dir():
        return
    for stray in artifacts_dir.glob(f"{dir_name}.tmp-*"):
        shutil.rmtree(stray, ignore_errors=True)
    for stray in artifacts_dir.glob(f"{dir_name}.backup-*"):
        shutil.rmtree(stray, ignore_errors=True)


def new_temp_directory(artifacts_dir: Path, dir_name: str) -> Path:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = artifacts_dir / f"{dir_name}.tmp-{secrets.token_hex(8)}"
    temp_dir.mkdir(parents=True)
    return temp_dir


def swap_directory(
    temp_dir: Path, target_dir: Path, *, error_factory: Callable[[str], Exception]
) -> None:
    """Promueve `temp_dir` a `target_dir`. Si `target_dir` ya existe: la
    respalda con un rename, promueve el temporal, y solo entonces borra
    el respaldo. Si el segundo rename falla, intenta restaurar el
    respaldo de inmediato; si eso tambien falla, conserva ambos
    directorios para recuperacion manual (nunca deja `target_dir`
    ausente ni a medio escribir)."""
    if not target_dir.exists():
        os.rename(temp_dir, target_dir)
        return

    backup_dir = target_dir.parent / f"{target_dir.name}.backup-{secrets.token_hex(8)}"
    try:
        os.rename(target_dir, backup_dir)
    except OSError as exc:
        raise error_factory(
            f"no se pudo respaldar {target_dir.name} antes del reemplazo: {type(exc).__name__}"
        ) from exc

    try:
        os.rename(temp_dir, target_dir)
    except OSError as exc:
        try:
            os.rename(backup_dir, target_dir)
        except OSError as restore_exc:
            raise error_factory(
                "fallo el reemplazo Y la restauracion del directorio; revisar manualmente "
                f"{backup_dir.name!r} y {temp_dir.name!r}: {type(restore_exc).__name__}"
            ) from restore_exc
        raise error_factory(
            f"fallo el reemplazo de {target_dir.name}, se restauro el respaldo previo: "
            f"{type(exc).__name__}"
        ) from exc
    else:
        shutil.rmtree(backup_dir, ignore_errors=True)


def discard_temp_directory(temp_dir: Path) -> None:
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
