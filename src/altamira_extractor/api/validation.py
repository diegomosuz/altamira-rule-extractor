"""Validacion de identificadores provistos por el cliente (Prompt 13b).

Ningun identificador del cliente se convierte directamente en un path:
`run_id` se valida contra el formato EXACTO producido por
`runner.generate_run_id()` (nunca lo genera el cliente para `POST /api/
runs`; solo aparece como parametro de path en los endpoints de lectura/
resume), y `candidate_id` nunca se usa para derivar un filename -- el
lookup siempre pasa por el manifest correspondiente (ver `api/reads.py`).
Esta validacion es una primera defensa barata (422 inmediato) antes de
tocar el filesystem, no la unica: los lectores de artefactos vuelven a
verificar contencion de path (sin `/`/`\\`, sin symlink) en el manifest
resuelto."""

from __future__ import annotations

import re

from .errors import InvalidIdentifierError

# Debe coincidir EXACTAMENTE con runner.generate_run_id():
# f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}-{secrets.token_hex(4)}"
_RUN_ID_RE = re.compile(r"^\d{8}T\d{12}-[0-9a-f]{8}$")

_CANDIDATE_ID_MAX_LENGTH = 512
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def validate_run_id(run_id: str) -> str:
    """Exige el formato exacto de `generate_run_id()`: rechaza `/`, `\\`,
    `..`, NUL, caracteres de control y cualquier otra desviacion."""
    if not _RUN_ID_RE.match(run_id):
        raise InvalidIdentifierError("run_id con formato invalido")
    return run_id


def validate_candidate_id(candidate_id: str) -> str:
    """Deliberadamente permisivo con el CONTENIDO (admite `::`, `.`, `-`
    y cualquier otro caracter que el pipeline use realmente en
    `candidate_id` -- no hay un formato unico documentado mas alla de
    "string no vacio" en `RuleCandidate.candidate_id`), pero rechaza lo
    que podria ser peligroso si alguna vez se usara en un path: `/`, `\\`,
    NUL, caracteres de control, cadena vacia o excesivamente larga. El
    lookup real siempre es una comparacion exacta contra el manifest
    correspondiente (nunca se deriva un filename desde este valor)."""
    if not candidate_id:
        raise InvalidIdentifierError("candidate_id no puede estar vacio")
    if len(candidate_id) > _CANDIDATE_ID_MAX_LENGTH:
        raise InvalidIdentifierError(
            f"candidate_id excede el limite de {_CANDIDATE_ID_MAX_LENGTH} caracteres"
        )
    if "/" in candidate_id or "\\" in candidate_id:
        raise InvalidIdentifierError("candidate_id no puede contener '/' ni '\\'")
    if _CONTROL_CHARS_RE.search(candidate_id):
        raise InvalidIdentifierError("candidate_id contiene caracteres de control")
    return candidate_id
