"""Carga versionada de las queries Q1-Q7 (Prompt 10b).

Cada archivo se lee tal cual desde `queries/v1/` (path estable,
independiente del CWD). Cuatro de los nueve (Q2, Q3a, Q3b, Q5b) usan
`DATA_DEPENDS_ON`/`CONTROL_DEPENDS_ON` de longitud variable: Cypher no
admite un parametro en el limite de una relacion de longitud variable
(verificado empiricamente contra Neo4j 5 real: `MATCH (a)-[:REL*1..$n]->
(b)` falla con "Parameter maps cannot be used in `MATCH` patterns"), asi
que la profundidad se sustituye como texto — nunca via `str.format()`
(los archivos contienen literales de mapa Cypher `{...}` que `format()`
interpretaria como campos de reemplazo), siempre mediante un
`str.replace()` de un placeholder explicito y unico
(`__DEPENDENCY_DEPTH__`), y solo despues de validar que el numero de
ocurrencias coincide exactamente con lo esperado por cada query."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .errors import ContextBuildError

DEPTH_PLACEHOLDER = "__DEPENDENCY_DEPTH__"


@dataclass(frozen=True)
class LoadedContextQuery:
    logical_query: str
    relative_path: str
    template_hash: str
    effective_text: str
    effective_query_hash: str


def load_context_query(
    path: Path,
    *,
    relative_path: str,
    logical_query: str,
    expected_placeholder_count: int,
    dependency_depth: int,
) -> LoadedContextQuery:
    """Lee `path`, valida que sea un archivo regular no vacio, exige
    exactamente `expected_placeholder_count` ocurrencias de
    `__DEPENDENCY_DEPTH__`, sustituye unicamente ese token (nunca un
    `format()` general) y calcula SHA-256 tanto de la plantilla como del
    texto efectivamente ejecutado."""
    if not path.exists():
        raise ContextBuildError(f"no se encontro la query {logical_query} ({path.name})")
    if not path.is_file():
        raise ContextBuildError(f"la query {logical_query} ({path.name}) no es un archivo regular")

    raw_bytes = path.read_bytes()
    if not raw_bytes.strip():
        raise ContextBuildError(f"la query {logical_query} ({path.name}) esta vacia")

    template_text = raw_bytes.decode("utf-8")
    template_hash = hashlib.sha256(raw_bytes).hexdigest()

    occurrences = template_text.count(DEPTH_PLACEHOLDER)
    if occurrences != expected_placeholder_count:
        raise ContextBuildError(
            f"la query {logical_query} ({path.name}) tiene {occurrences} ocurrencias de "
            f"{DEPTH_PLACEHOLDER!r}, se esperaban exactamente {expected_placeholder_count}"
        )

    effective_text = (
        template_text.replace(DEPTH_PLACEHOLDER, str(dependency_depth))
        if occurrences
        else template_text
    )
    effective_query_hash = hashlib.sha256(effective_text.encode("utf-8")).hexdigest()

    return LoadedContextQuery(
        logical_query=logical_query,
        relative_path=relative_path,
        template_hash=template_hash,
        effective_text=effective_text,
        effective_query_hash=effective_query_hash,
    )
