"""CandidateDetector (Prompt 10a): ejecuta `queries/v1/q0_candidates.cypher`
sobre el grafo Neo4j ya cargado y validado, y mapea cada fila a un
`RuleCandidate` tipado.

Q0 devuelve hipotesis estructurales, no reglas confirmadas
(docs/METAMODEL_ALIGNMENT.md, punto 6). El unico detector V1
(`q0-return-code-decision`) es un predicado puramente estructural: Q0
usa `MATCH` exacto (nunca `OPTIONAL`), asi que toda fila devuelta ya
satisface el patron completo. Por eso `DETECTOR_SCORE` es la constante
`1.0`, cuya unica semantica es "la fila cumple el predicado estructural
de Q0" — nunca confianza funcional, validacion humana, aplicabilidad
parametrica ni efecto de negocio, y nunca se deriva ni se combina con
`DataItem.semantic_confidence` (fuente distinta, `SemanticTagger`). Si
la semantica de Q0 cambia, `DETECTOR_VERSION` debe incrementarse.

`candidate_id` no reutiliza el alias `candidate_id` que Q0 devolvia
antes de la modificacion autorizada en Prompt 10a (ese alias era en
realidad `Paragraph.id`): se calcula en Python a partir de
`decision_id`, ya de por si versionado (pais/programa/version/hash de
codigo/paragraph/linea/ordinal), mas el detector y `source_package_hash`
completo — sin UUID ni timestamp.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..contracts.candidate import RuleCandidate
from ..contracts.enums import CandidateStatus
from .errors import CandidateDetectionError
from .neo4j_repository import Neo4jRepository

DETECTOR_ID = "q0-return-code-decision"
DETECTOR_VERSION = "1.0"
DETECTOR_SCORE = 1.0

_REQUIRED_COLUMNS = (
    "paragraph_id",
    "paragraph_name",
    "decision_id",
    "condition",
    "outcome",
    "rule_type",
    "line_start",
    "source_file",
)


def candidate_id_for(*, source_package_hash: str, decision_id: str) -> str:
    """Formula literal autorizada (Prompt 10a, punto 4): detector,
    version del detector, paquete completo y decision_id (que ya
    encapsula programa/version/paragraph transitivamente)."""
    return f"candidate::{DETECTOR_ID}::{DETECTOR_VERSION}::{source_package_hash}::{decision_id}"


def load_q0_query(q0_cypher_path: Path) -> tuple[str, str]:
    """Lee `q0_candidates.cypher`, calcula su SHA-256 real y devuelve
    `(texto, hash)`. Fatal si el archivo no existe, no es un archivo
    regular, o esta vacio."""
    if not q0_cypher_path.is_file():
        raise CandidateDetectionError(
            f"no se encontro el archivo de Q0: {q0_cypher_path.name}"
        )
    raw_bytes = q0_cypher_path.read_bytes()
    if not raw_bytes.strip():
        raise CandidateDetectionError(f"{q0_cypher_path.name} esta vacio")
    query_hash = hashlib.sha256(raw_bytes).hexdigest()
    return raw_bytes.decode("utf-8"), query_hash


def _row_to_candidate(row: dict[str, Any], *, source_package_hash: str) -> RuleCandidate:
    missing = [column for column in _REQUIRED_COLUMNS if column not in row]
    if missing:
        raise CandidateDetectionError(
            f"fila de Q0 con estructura invalida: faltan columnas {missing}"
        )

    decision_id = str(row["decision_id"])
    try:
        return RuleCandidate(
            candidate_id=candidate_id_for(
                source_package_hash=source_package_hash, decision_id=decision_id
            ),
            paragraph_id=str(row["paragraph_id"]),
            paragraph_name=str(row["paragraph_name"]),
            decision_id=decision_id,
            detector_id=DETECTOR_ID,
            detector_version=DETECTOR_VERSION,
            detector_score=DETECTOR_SCORE,
            status=CandidateStatus.DETECTED_CANDIDATE,
            condition=str(row["condition"]),
            outcome_code=row["outcome"],
            rule_type=row["rule_type"],
            line_start=row["line_start"],
            source_file=row["source_file"],
            source_package_hash=source_package_hash,
        )
    except ValidationError as exc:
        raise CandidateDetectionError(f"fila de Q0 con estructura invalida: {exc}") from exc


def detect_candidates(
    repository: Neo4jRepository, *, q0_cypher_text: str, package_hash: str
) -> list[RuleCandidate]:
    """Ejecuta Q0 y mapea cada fila a `RuleCandidate`.

    Identidad de candidato = `(detector_id, detector_version,
    source_package_hash, decision_id)` (codificada en `candidate_id`).
    Filas exactamente iguales para la misma identidad se consolidan en
    un unico candidato (caso esperado cuando una Decision tiene varios
    `LEADS_TO` hacia distintos DataItems `return_code`: Q0 no expone el
    sink, asi que esas filas son indistinguibles por diseno). Filas con
    la misma identidad pero columnas distintas son un error fatal: nunca
    se elige una fila arbitrariamente.
    """
    rows = repository.run_candidate_query(q0_cypher_text, package_hash=package_hash)

    by_candidate_id: dict[str, RuleCandidate] = {}
    for row in rows:
        candidate = _row_to_candidate(row, source_package_hash=package_hash)
        existing = by_candidate_id.get(candidate.candidate_id)
        if existing is None:
            by_candidate_id[candidate.candidate_id] = candidate
        elif existing != candidate:
            raise CandidateDetectionError(
                "Q0 devolvio filas inconsistentes para el mismo candidato: "
                f"{candidate.candidate_id!r}"
            )

    return sorted(by_candidate_id.values(), key=lambda candidate: candidate.candidate_id)
