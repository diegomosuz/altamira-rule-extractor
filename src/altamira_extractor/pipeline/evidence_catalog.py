"""Catalogo deterministico de alias de evidencia (checkpoint correctivo).

Problema que resuelve: pedirle al LLM que copie `evidence_id`/
`evidence_path` reales (identificadores largos y opacos, rutas JSONPath
completas) es fragil -- un run multiprograma con muchos `claims` por
candidato aumenta la probabilidad de que el modelo transcriba mal al
menos uno. La reparacion estructural anterior (listas separadas de IDs
permitidos y paths permitidos) reducia pero no eliminaba el problema:
seguia exigiendole al modelo escribir el mismo tipo de dato fragil.

Este modulo construye, para CADA candidato (un `ContextPackage`), un
catalogo numerado `E001, E002, ...` donde cada alias representa
CONJUNTAMENTE un `(evidence_id, evidence_path)` real -- nunca por
separado. El LLM nunca ve `evidence_id`/`evidence_path` reales: solo
alias + una descripcion funcional derivada (`to_prompt_json`). Python
resuelve los alias de vuelta a valores reales (`resolve`) antes de
ensamblar el `RuleDraft` contractual (ver `rule_draft_assembly.
resolve_evidence_aliases`/`assemble_rule_draft_with_evidence_catalog`).

El catalogo es puramente en memoria: nunca se persiste, nunca aparece en
ningun artefacto de `artifacts/`. `RuleDraft`/`Claim` (Pydantic) y
`rule-draft.schema.json` no cambian -- solo cambia el protocolo de
conversacion con el LLM."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from ..contracts.context_package import ContextPackage, EvidenceEntry
from ..contracts.rule_draft import RuleDraft

_ALIAS_WIDTH = 3
_PATH_SEGMENT_RE = re.compile(r"\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)|\[(?P<index>\d+)\]")
_EVIDENCE_IDS_KEY = "evidence_ids"
_EVIDENCE_PATHS_KEY = "evidence_paths"
_EVIDENCE_REFS_KEY = "evidence_refs"


@dataclass(frozen=True)
class EvidenceCatalogEntry:
    """Un alias representa CONJUNTAMENTE un `evidence_id` y un
    `evidence_path` reales -- nunca uno sin el otro, para que ambos
    campos del `Claim` final nunca puedan quedar desalineados. `kind`
    ("tipo") se deriva estructuralmente del ultimo segmento con nombre
    de `evidence_path` (nunca inventado); `description` ("descripcion")
    se deriva de los metadatos reales de la `EvidenceEntry` asociada
    (`kind`/`source_file`/`line_start`/`line_end`)."""

    alias: str
    evidence_id: str
    evidence_path: str
    kind: str
    description: str


@dataclass(frozen=True)
class EvidenceCatalog:
    """Inmutable y deterministico: dado el mismo `ContextPackage`,
    `build_evidence_catalog` siempre produce el mismo catalogo, byte a
    byte, en cualquier ejecucion. `entries` esta ordenado por alias
    ascendente (`E001, E002, ...`); nunca depende del orden de
    iteracion de un `set`/`dict` (esos se convierten a listas ordenadas
    ANTES de numerar, ver `build_evidence_catalog`)."""

    entries: tuple[EvidenceCatalogEntry, ...]

    def __post_init__(self) -> None:
        aliases = [entry.alias for entry in self.entries]
        if len(aliases) != len(set(aliases)):
            raise ValueError("EvidenceCatalog no puede tener un alias duplicado")
        pairs = [(entry.evidence_id, entry.evidence_path) for entry in self.entries]
        if len(pairs) != len(set(pairs)):
            raise ValueError(
                "EvidenceCatalog no puede tener el mismo (evidence_id, evidence_path) dos veces"
            )

    @cached_property
    def _by_alias(self) -> dict[str, EvidenceCatalogEntry]:
        return {entry.alias: entry for entry in self.entries}

    @cached_property
    def _by_pair(self) -> dict[tuple[str, str], str]:
        return {(entry.evidence_id, entry.evidence_path): entry.alias for entry in self.entries}

    def resolve(self, alias: str) -> EvidenceCatalogEntry | None:
        """Resolucion EXACTA unicamente: sin prefijos, sin similitud,
        sin distancia de edicion. Un alias que no esta literalmente en
        el catalogo devuelve `None` -- el llamador lo reporta como
        `unknown_evidence_alias`, nunca lo aproxima a la entrada "mas
        parecida"."""
        return self._by_alias.get(alias)

    def find_alias(self, evidence_id: str, evidence_path: str) -> str | None:
        """Busqueda inversa EXACTA (par completo, no solo el id o solo
        el path): usada por `redact_rule_draft_for_prompt` para mostrarle
        al modelo, durante una reparacion de guardrail, un draft ya
        resuelto sin exponer sus IDs/paths reales."""
        return self._by_pair.get((evidence_id, evidence_path))

    def to_prompt_json(self) -> str:
        """Serializacion segura para el LLM: UNICAMENTE alias + tipo +
        descripcion. `evidence_id`/`evidence_path` reales nunca
        aparecen aqui."""
        payload = {
            entry.alias: {"tipo": entry.kind, "descripcion": entry.description}
            for entry in self.entries
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _kind_of_anchor_path(path: str) -> str:
    """Deriva un 'tipo' legible del ULTIMO segmento con nombre de un
    JSONPath (`$.effects.table_effects[0]` -> `table_effects`):
    puramente estructural, nunca conoce un modelo Pydantic especifico --
    se mantiene correcto automaticamente si ContextPackage gana un nuevo
    contenedor con `evidence_ids` en el futuro, sin tocar este modulo."""
    last_field: str | None = None
    for match in _PATH_SEGMENT_RE.finditer(path):
        field = match.group("field")
        if field is not None:
            last_field = field
    return last_field or "root"


def _describe_evidence(entry: EvidenceEntry | None) -> str:
    """Descripcion derivada EXCLUSIVAMENTE de metadatos reales ya
    recolectados por el pipeline (`EvidenceEntry.kind`/`source_file`/
    `line_start`/`line_end`): nunca se inventa contenido nuevo."""
    if entry is None:
        return "evidencia sin metadata adicional"
    if entry.line_start is not None and entry.line_end is not None:
        location = f"{entry.source_file}:{entry.line_start}-{entry.line_end}"
    else:
        location = entry.source_file
    return f"{entry.kind} ({location})"


def _collect_evidence_id_container_pairs(
    node: object, path: str, known_evidence_ids: frozenset[str]
) -> list[tuple[str, str]]:
    """Camina genericamente el `dict` de un ContextPackage ya serializado
    (nunca conoce un modelo Pydantic especifico) y, por cada contenedor
    con `evidence_ids` no vacio, emite un par `(evidence_id, path del
    contenedor)` por cada id REALMENTE conocido (presente en
    `package.evidence`) que cite. Usa el path del propio contenedor
    (p. ej. `$.effects.table_effects[0]`, no un campo escalar interno)
    para preservar compatibilidad con los chequeos de guardrail que
    matchean por prefijo de contenedor (`_check_approved_for_rule_text`)."""
    pairs: list[tuple[str, str]] = []
    if isinstance(node, dict):
        evidence_ids = node.get("evidence_ids")
        if isinstance(evidence_ids, list):
            for evidence_id in evidence_ids:
                if isinstance(evidence_id, str) and evidence_id in known_evidence_ids:
                    pairs.append((evidence_id, path))
        for key, value in node.items():
            pairs.extend(
                _collect_evidence_id_container_pairs(value, f"{path}.{key}", known_evidence_ids)
            )
    elif isinstance(node, list):
        for index, item in enumerate(node):
            pairs.extend(
                _collect_evidence_id_container_pairs(
                    item, f"{path}[{index}]", known_evidence_ids
                )
            )
    return pairs


def build_evidence_catalog(package: ContextPackage) -> EvidenceCatalog:
    """Construye el catalogo deterministico de UN candidato. Mismo
    `ContextPackage` -> mismo catalogo, siempre: los pares
    `(evidence_id, evidence_path)` se deduplican y ordenan
    lexicograficamente (`sorted(set(...))`, nunca se itera un `set`/
    `dict` directamente para decidir el orden) ANTES de numerar
    `E001, E002, ...` secuencialmente."""
    known_evidence_ids = frozenset(entry.evidence_id for entry in package.evidence)
    evidence_by_id = {entry.evidence_id: entry for entry in package.evidence}
    context_dict = package.model_dump(mode="json")

    pairs = _collect_evidence_id_container_pairs(context_dict, "$", known_evidence_ids)
    unique_pairs = sorted(set(pairs))

    entries = tuple(
        EvidenceCatalogEntry(
            alias=f"E{index + 1:0{_ALIAS_WIDTH}d}",
            evidence_id=evidence_id,
            evidence_path=path,
            kind=_kind_of_anchor_path(path),
            description=_describe_evidence(evidence_by_id.get(evidence_id)),
        )
        for index, (evidence_id, path) in enumerate(unique_pairs)
    )
    return EvidenceCatalog(entries=entries)


def redact_rule_draft_for_prompt(
    rule_draft: RuleDraft, catalog: EvidenceCatalog
) -> dict[str, Any]:
    """Vista de un `RuleDraft` YA RESUELTO (IDs/paths reales) para
    mostrarsela al modelo durante una reparacion de guardrail, con
    `evidence_ids`/`evidence_paths` de cada claim sustituidos por
    `evidence_refs` (alias). Nunca se persiste: existe solo como texto
    de prompt (checkpoint correctivo -- antes, `current_draft.
    to_stable_json()` exponia IDs reales al modelo durante la
    reparacion de guardrail, reintroduciendo el problema original en
    esa capa).

    `evidence_ids`/`evidence_paths` de un `Claim` no estan garantizados
    por el contrato Pydantic a tener la misma longitud (no hay
    validador de esa relacion): se emparejan posicionalmente hasta el
    mas corto de los dos (nunca se levanta una excepcion por esto, es
    una transformacion de presentacion, no una validacion). Un par real
    sin alias en el catalogo (no deberia ocurrir para un RuleDraft cuyo
    origen es el mismo catalogo) se omite de `evidence_refs` en vez de
    inventar un alias falso."""
    payload = rule_draft.model_dump(mode="json")
    redacted_claims: list[dict[str, Any]] = []
    for claim in payload["claims"]:
        evidence_ids = claim.pop(_EVIDENCE_IDS_KEY)
        evidence_paths = claim.pop(_EVIDENCE_PATHS_KEY)
        refs = [
            alias
            for evidence_id, evidence_path in zip(evidence_ids, evidence_paths, strict=False)
            if (alias := catalog.find_alias(evidence_id, evidence_path)) is not None
        ]
        claim[_EVIDENCE_REFS_KEY] = refs
        redacted_claims.append(claim)
    payload["claims"] = redacted_claims
    return payload
