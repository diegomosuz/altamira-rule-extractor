"""GraphInvariantValidator (Prompt 9): ejecuta `queries/v1/invariants.cypher`
como una unica consulta `UNION ALL` contra la base ya cargada por
`Neo4jRepository`.

No implementa un parser Cypher: el archivo se lee tal cual y se ejecuta
como una unica consulta parametrizada. Los tres parametros que recibe:

- `package_hash`: `source_package_hash` del run actual.
- `allowed_semantic_tags`: catalogo de tags de `config/semantic-tags.yml`,
  cargado con `semantic_tagger.load_semantic_tags_config` (el mismo
  loader validado de Prompt 7) — nunca se re-ejecuta el matching de
  SemanticTagger, solo se reutiliza la carga/validacion de la
  configuracion ya existente.
- `allowed_relationship_signatures`: firmas `{type, from_label,
  to_label}` derivadas de `_RELATIONSHIP_ENDPOINTS`, la misma tabla
  gobernante de endpoints ya definida en `contracts/semantic_graph.py`
  (se reutiliza por import, nunca se duplica manualmente).

DomainTerm sin `HAS_DOMAIN_TERM` NO es una violacion (el catalogo de
Prompt 7 puede legitimamente contener terminos funcionales no usados por
el paquete actual) y Decision sin `LEADS_TO` NO es una violacion (Prompt
8 crea Decision para cada IF/EVALUATE aunque no tenga una asignacion
resoluble) — ninguna de las dos se implementa aqui, deliberadamente.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.enums import Severity
from ..contracts.invariants import InvariantViolation
from ..contracts.semantic_graph import _RELATIONSHIP_ENDPOINTS
from .errors import GraphValidationError
from .neo4j_repository import Neo4jRepository
from .semantic_tagger import load_semantic_tags_config


def relationship_endpoint_signatures() -> list[dict[str, str]]:
    """Aplana `_RELATIONSHIP_ENDPOINTS` (contracts/semantic_graph.py) a
    una lista de firmas `{type, from_label, to_label}` — la misma tabla
    que ya gobierna la validacion de `SemanticGraph`, sin duplicarla
    manualmente en 17 bloques Cypher."""
    signatures: list[dict[str, str]] = []
    for relationship_type, (origins, destinations) in _RELATIONSHIP_ENDPOINTS.items():
        for origin in origins:
            for destination in destinations:
                signatures.append(
                    {
                        "type": relationship_type.value,
                        "from_label": origin.value,
                        "to_label": destination.value,
                    }
                )
    signatures.sort(
        key=lambda signature: (signature["type"], signature["from_label"], signature["to_label"])
    )
    return signatures


def _load_allowed_semantic_tags(semantic_tags_path: Path) -> tuple[list[str], str]:
    config = load_semantic_tags_config(semantic_tags_path)
    return sorted(config.allowed_tags), config.config_hash


def run_invariants(
    repository: Neo4jRepository,
    *,
    invariants_cypher_path: Path,
    package_hash: str,
    semantic_tags_path: Path,
    expected_semantic_tags_config_hash: str,
) -> tuple[list[InvariantViolation], str]:
    """Ejecuta `invariants.cypher` y devuelve `(violations, invariants_query_hash)`,
    ambos deterministicos (ordenados, sin duplicados exactos).

    Fatal (`GraphValidationError`) si `invariants.cypher` no existe, si el
    hash actual de `config/semantic-tags.yml` ya no coincide con
    `expected_semantic_tags_config_hash` (drift de configuracion desde
    SEMANTIC_ENRICHMENT_BUILT), o si `Neo4jRepository.run_invariants`
    falla (error Cypher) o devuelve una `severity` desconocida.
    """
    if not invariants_cypher_path.is_file():
        raise GraphValidationError(
            f"no se encontro el archivo de invariantes: {invariants_cypher_path.name}"
        )
    cypher_bytes = invariants_cypher_path.read_bytes()
    invariants_query_hash = hashlib.sha256(cypher_bytes).hexdigest()
    cypher_text = cypher_bytes.decode("utf-8")

    allowed_semantic_tags, semantic_tags_config_hash = _load_allowed_semantic_tags(
        semantic_tags_path
    )
    if semantic_tags_config_hash != expected_semantic_tags_config_hash:
        raise GraphValidationError(
            f"{semantic_tags_path.name} cambio desde SEMANTIC_ENRICHMENT_BUILT: el hash "
            "actual no coincide con semantic_tags_config_hash de "
            "03b-semantic-enrichment.json"
        )

    raw_rows = repository.run_invariants(
        cypher_text,
        package_hash=package_hash,
        allowed_semantic_tags=allowed_semantic_tags,
        allowed_relationship_signatures=relationship_endpoint_signatures(),
    )

    violations: list[InvariantViolation] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in raw_rows:
        try:
            severity = Severity(row["severity"])
        except ValueError as exc:
            raise GraphValidationError(
                f"invariants.cypher devolvio una severity desconocida: {row['severity']!r} "
                f"(codigo {row.get('code')!r})"
            ) from exc
        violation = InvariantViolation(
            code=row["code"], severity=severity, entity_id=row["entity_id"], message=row["message"]
        )
        key = (violation.code, violation.entity_id, violation.severity.value, violation.message)
        if key not in seen:
            seen.add(key)
            violations.append(violation)

    violations.sort(key=lambda v: (v.code, v.entity_id, v.severity.value, v.message))
    return violations, invariants_query_hash
