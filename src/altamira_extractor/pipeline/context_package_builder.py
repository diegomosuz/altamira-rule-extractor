"""ContextPackageBuilder (Prompt 10b): ejecuta Q1-Q7 para cada
`RuleCandidate` dentro de una unica transaccion de lectura y construye un
`ContextPackage` tipado por candidato.

Cada query recibe `paragraph_id` (nunca `candidate_id`: las queries hacen
`MATCH (par:Paragraph {id: $paragraph_id})`, y `RuleCandidate.candidate_id`
no es un `Paragraph.id`). Q4/Q5a reciben ademas `decision_id` para
escoger exactamente la Decision del candidato, nunca todas las del
Paragraph.

Nada aqui reejecuta Q0/CandidateDetector ni `invariants.cypher`: este
modulo es de solo lectura sobre un grafo ya cargado y validado.

CALCULATION incondicional (Fase 15B3-C2-B2, `candidate.rule_family ==
CALCULATION and candidate.decision_id is None`): Q1/Q2/Q3a/Q3b/Q5b/Q6/Q7
reciben UNICAMENTE `paragraph_id` (nunca `decision_id`) y por lo tanto
son validos sin cambios para un calculo sin Decision envolvente -- el
Paragraph existe en el grafo independientemente de que una sentencia
puntual tenga o no una Decision que la envuelva, y el metamodelo
deliberadamente no tiene un nodo Statement individual (CLAUDE.md) del
que depender. Solo Q4 (`_build_decision`) y la porcion return_codes de
Q5a (`_build_return_codes`) exigen un `decision_id` real: ambos se
OMITEN (nunca se ejecutan con un valor fabricado) para este caso,
produciendo `decision=None`/`effects.return_codes=[]` -- un
`RETURN_CODE effect` nunca tiene sentido sin la Decision que lo
origina. `effects.table_effects` (Q5b) se conserva sin cambios: esta
scopeado por `paragraph_id`, igual que para cualquier otro candidato de
ese Paragraph."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import neo4j
from pydantic import ValidationError

from ..config import Settings
from ..contracts.candidate import RuleCandidate
from ..contracts.candidate_promotion_assessment import UnifiedRuleFamily
from ..contracts.context_package import (
    ApplicableParameterRow,
    BatchContext,
    CodeSliceEntry,
    Completeness,
    ContextPackage,
    ContextPackageCandidate,
    ContextPackageDecision,
    ContextPackageOperation,
    ContextPackageScope,
    ContextParameterRow,
    DataContext,
    DomainGlossaryEntry,
    Effects,
    EvidenceEntry,
    ParameterTableContext,
    ReturnCodeEffect,
    TableEffect,
    TransactionalTableRead,
)
from ..contracts.enums import (
    ApplicabilityStatus,
    AttributionScope,
    BatchContextStatus,
    CompletenessStatus,
    InclusionReason,
    TableEffectOperation,
)
from .cypher_query_loader import LoadedContextQuery
from .errors import ContextBuildError
from .parameter_predicate_resolver import (
    aggregate_applicability,
    entry_matches_comparisons,
    resolve_predicate_row,
)


@dataclass(frozen=True)
class ContextQuerySet:
    q1: LoadedContextQuery
    q2: LoadedContextQuery
    q3a: LoadedContextQuery
    q3b: LoadedContextQuery
    q4: LoadedContextQuery
    q5a: LoadedContextQuery
    q5b: LoadedContextQuery
    q6: LoadedContextQuery
    q7: LoadedContextQuery


def _run(tx: neo4j.ManagedTransaction, query_text: str, **params: Any) -> list[dict[str, Any]]:
    result = tx.run(query_text, **params)
    return [dict(record) for record in result]


def _evidence_id(
    *,
    logical_query: str,
    candidate_id: str,
    origin_entity_id: str,
    evidence_kind: str,
    content: dict[str, Any],
) -> str:
    payload = {
        "logical_query": logical_query,
        "candidate_id": candidate_id,
        "origin_entity_id": origin_entity_id,
        "evidence_kind": evidence_kind,
        "content": content,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return f"evidence::{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def build_context_packages(
    tx: neo4j.ManagedTransaction,
    candidates: Sequence[RuleCandidate],
    *,
    queries: ContextQuerySet,
    settings: Settings,
) -> list[ContextPackage]:
    return [
        _build_one_context_package(tx, candidate, queries=queries, settings=settings)
        for candidate in candidates
    ]


def _is_unconditional_calculation(candidate: RuleCandidate) -> bool:
    """Unico predicado que decide el atajo de Fase 15B3-C2-B2: un
    CALCULATION sin Decision envolvente (`decision_id is None`, campo ya
    opcional por diseno -- ver `contracts/candidate.py`). Nunca inventa
    un `decision_id` sintetico; para cualquier otra familia
    `decision_id` sigue siendo obligatorio por el propio contrato de
    `RuleCandidate`."""
    return candidate.rule_family == UnifiedRuleFamily.CALCULATION and candidate.decision_id is None


def _build_one_context_package(
    tx: neo4j.ManagedTransaction,
    candidate: RuleCandidate,
    *,
    queries: ContextQuerySet,
    settings: Settings,
) -> ContextPackage:
    evidence_entries: list[EvidenceEntry] = []
    unconditional_calculation = _is_unconditional_calculation(candidate)

    scope = _build_scope(tx, candidate, queries.q1)
    code_slice, code_slice_evidence = _build_code_slice(tx, candidate, queries.q2, settings)
    evidence_entries.extend(code_slice_evidence)

    data_context, d3_evidence, d3_status = _build_data_context(
        tx, candidate, queries.q3a, queries.q3b, settings
    )
    evidence_entries.extend(d3_evidence)

    decision: ContextPackageDecision | None
    d4_evidence: list[EvidenceEntry]
    if unconditional_calculation:
        # Q4 exige un decision_id real (MATCH ... WHERE dec.id =
        # $decision_id) -- nunca se invoca con un valor fabricado.
        decision, d4_evidence = None, []
    else:
        decision, d4_evidence = _build_decision(tx, candidate, queries.q4)
    evidence_entries.extend(d4_evidence)

    effects, d5_evidence = _build_effects(
        tx, candidate, queries.q5a, queries.q5b, skip_return_codes=unconditional_calculation
    )
    evidence_entries.extend(d5_evidence)

    batch_context = _build_batch_context(tx, candidate, queries.q6)

    domain_glossary, d7_evidence = _build_domain_glossary(tx, candidate, queries.q7)
    evidence_entries.extend(d7_evidence)

    evidence = _dedupe_evidence(evidence_entries)

    d5_status = (
        CompletenessStatus.NOT_AVAILABLE
        if not effects.return_codes and not effects.table_effects
        else CompletenessStatus.COMPLETE
    )
    completeness = Completeness(
        D1=CompletenessStatus.COMPLETE,
        D2=CompletenessStatus.COMPLETE,
        D3=d3_status,
        D4=(
            CompletenessStatus.NOT_AVAILABLE
            if unconditional_calculation
            else CompletenessStatus.COMPLETE
        ),
        D5=d5_status,
        D6=CompletenessStatus(batch_context.status.value),
        D7=(
            CompletenessStatus.NOT_AVAILABLE
            if not domain_glossary
            else CompletenessStatus.COMPLETE
        ),
    )

    try:
        return ContextPackage(
            schema_version="2.0",
            candidate=ContextPackageCandidate(
                candidate_id=candidate.candidate_id,
                decision_id=candidate.decision_id,
                detector_id=candidate.detector_id,
                detector_version=candidate.detector_version,
                detector_score=candidate.detector_score,
            ),
            scope=scope,
            code_slice=code_slice,
            data_context=data_context,
            decision=decision,
            effects=effects,
            batch_context=batch_context,
            domain_glossary=domain_glossary,
            evidence=evidence,
            completeness=completeness,
        )
    except ValidationError as exc:
        raise ContextBuildError(
            f"ContextPackage invalido para el candidato {candidate.candidate_id!r}: {exc}"
        ) from exc


def _dedupe_evidence(entries: list[EvidenceEntry]) -> list[EvidenceEntry]:
    by_id: dict[str, EvidenceEntry] = {}
    for entry in entries:
        existing = by_id.get(entry.evidence_id)
        if existing is not None and existing != entry:
            raise ContextBuildError(
                f"evidence_id duplicado con contenido distinto: {entry.evidence_id!r}"
            )
        by_id[entry.evidence_id] = entry
    return [by_id[key] for key in sorted(by_id)]


# --- D1: scope (Q1) ---


def _build_scope(
    tx: neo4j.ManagedTransaction, candidate: RuleCandidate, query: LoadedContextQuery
) -> ContextPackageScope:
    rows = _run(tx, query.effective_text, paragraph_id=candidate.paragraph_id)
    if len(rows) != 1:
        raise ContextBuildError(
            f"Q1 debe devolver exactamente una fila para paragraph_id={candidate.paragraph_id!r}; "
            f"se encontraron {len(rows)}"
        )
    row = rows[0]
    if row["source_package_hash"] != candidate.source_package_hash:
        raise ContextBuildError(
            f"Q1: source_package_hash de {candidate.paragraph_id!r} no coincide con el candidato"
        )
    try:
        return ContextPackageScope(
            country=row["country"],
            application=row["application"],
            operation=ContextPackageOperation(
                logical_name=row["operation_logical"], description=row["operation_description"]
            ),
            program=row["program"],
            program_version=row["program_version"],
            paragraph=row["paragraph"],
            source_file=row["source_file"],
            line_start=row["line_start"],
            line_end=row["line_end"],
            source_package_hash=candidate.source_package_hash,
        )
    except ValidationError as exc:
        raise ContextBuildError(f"Q1: fila con estructura invalida: {exc}") from exc


# --- D2: code slice (Q2) ---


def _build_code_slice(
    tx: neo4j.ManagedTransaction,
    candidate: RuleCandidate,
    query: LoadedContextQuery,
    settings: Settings,
) -> tuple[list[CodeSliceEntry], list[EvidenceEntry]]:
    rows = _run(tx, query.effective_text, paragraph_id=candidate.paragraph_id)
    if len(rows) > settings.max_code_slice_paragraphs:
        raise ContextBuildError(
            f"Q2: el code slice de {candidate.paragraph_id!r} tiene {len(rows)} paragraphs, "
            f"excede el limite configurado ({settings.max_code_slice_paragraphs})"
        )

    entries_by_id: dict[str, CodeSliceEntry] = {}
    evidence: list[EvidenceEntry] = []
    for row in rows:
        paragraph_id = row["paragraph_id"]
        evidence_id = _evidence_id(
            logical_query="Q2",
            candidate_id=candidate.candidate_id,
            origin_entity_id=paragraph_id,
            evidence_kind="code_slice",
            content={
                "source_file": row["source_file"],
                "line_start": row["line_start"],
                "line_end": row["line_end"],
                "inclusion_reason": row["inclusion_reason"],
            },
        )
        evidence.append(
            EvidenceEntry(
                evidence_id=evidence_id,
                kind="code_slice",
                source_file=row["source_file"],
                line_start=row["line_start"],
                line_end=row["line_end"],
                source_package_hash=candidate.source_package_hash,
            )
        )
        try:
            entry = CodeSliceEntry(
                paragraph_id=paragraph_id,
                paragraph=row["paragraph_name"],
                source_file=row["source_file"],
                source_text=row["source_text"],
                line_start=row["line_start"],
                line_end=row["line_end"],
                inclusion_reason=InclusionReason(row["inclusion_reason"]),
                evidence_ids=[evidence_id],
            )
        except ValidationError as exc:
            raise ContextBuildError(f"Q2: fila con estructura invalida: {exc}") from exc

        existing = entries_by_id.get(paragraph_id)
        existing_view = existing.model_dump(exclude={"evidence_ids"}) if existing else None
        entry_view = entry.model_dump(exclude={"evidence_ids"})
        if existing is not None and existing_view != entry_view:
            raise ContextBuildError(
                f"Q2: paragraph_id {paragraph_id!r} con contenido inconsistente entre filas"
            )
        entries_by_id[paragraph_id] = entry

    if not entries_by_id:
        raise ContextBuildError(
            f"Q2: el code slice de {candidate.paragraph_id!r} no incluyo al propio candidato"
        )

    return [entries_by_id[key] for key in sorted(entries_by_id)], evidence


# --- D3: data context (Q3a + Q3b) ---


def _build_data_context(
    tx: neo4j.ManagedTransaction,
    candidate: RuleCandidate,
    q3a: LoadedContextQuery,
    q3b: LoadedContextQuery,
    settings: Settings,
) -> tuple[DataContext, list[EvidenceEntry], CompletenessStatus]:
    evidence: list[EvidenceEntry] = []
    parameter_tables, parameter_evidence, total_entries = _build_parameter_tables(
        tx, candidate, q3a
    )
    if total_entries > settings.max_parameter_entries_per_context:
        raise ContextBuildError(
            f"Q3a: {candidate.paragraph_id!r} tiene {total_entries} ParameterEntry en su "
            f"contexto, excede el limite configurado ({settings.max_parameter_entries_per_context})"
        )
    evidence.extend(parameter_evidence)

    transactional_tables, transactional_evidence = _build_transactional_tables(
        tx, candidate, q3b, settings
    )
    evidence.extend(transactional_evidence)

    if not parameter_tables and not transactional_tables:
        status = CompletenessStatus.NOT_AVAILABLE
    elif any(
        table.applicability_status != ApplicabilityStatus.EXACT for table in parameter_tables
    ):
        status = CompletenessStatus.PARTIAL
    else:
        status = CompletenessStatus.COMPLETE

    return (
        DataContext(
            parameter_tables=parameter_tables, transactional_tables_read=transactional_tables
        ),
        evidence,
        status,
    )


def _build_parameter_tables(
    tx: neo4j.ManagedTransaction, candidate: RuleCandidate, query: LoadedContextQuery
) -> tuple[list[ParameterTableContext], list[EvidenceEntry], int]:
    rows = _run(tx, query.effective_text, paragraph_id=candidate.paragraph_id)
    evidence: list[EvidenceEntry] = []
    tables: list[ParameterTableContext] = []
    total_entries = 0

    for row in rows:
        table_id = row["parameter_table_id"]
        raw_entries = [entry for entry in (row["entries"] or []) if entry is not None]
        access_evidence = [ev for ev in (row["access_evidence"] or []) if ev is not None]
        total_entries += len(raw_entries)

        entries_by_id: dict[str, dict[str, Any]] = {}
        for raw_entry in raw_entries:
            entry_id = raw_entry["parameter_entry_id"]
            existing = entries_by_id.get(entry_id)
            if existing is not None and existing != raw_entry:
                raise ContextBuildError(
                    f"Q3a: ParameterEntry {entry_id!r} con contenido inconsistente entre filas"
                )
            entries_by_id[entry_id] = raw_entry

        resolutions = [
            resolve_predicate_row(ev["predicate_text"], ev["host_variables_json"])
            for ev in access_evidence
        ]
        status = aggregate_applicability(resolutions)

        resolved_predicates = sorted(
            {
                ev["predicate_text"]
                for ev, resolution in zip(access_evidence, resolutions, strict=True)
                if resolution.status == ApplicabilityStatus.EXACT and ev["predicate_text"]
            }
        )
        unresolved_predicates = sorted(
            {
                ev["predicate_text"]
                for ev, resolution in zip(access_evidence, resolutions, strict=True)
                if resolution.status != ApplicabilityStatus.EXACT and ev["predicate_text"]
            }
        )
        all_predicates = sorted(
            {ev["predicate_text"] for ev in access_evidence if ev["predicate_text"]}
        )

        applicable_entry_ids: set[str] = set()
        for resolution in resolutions:
            if resolution.status != ApplicabilityStatus.EXACT:
                continue
            for entry_id, raw_entry in entries_by_id.items():
                normalized_row = _parse_json_object(raw_entry.get("normalized_row_json"))
                if entry_matches_comparisons(normalized_row, resolution.resolved):
                    applicable_entry_ids.add(entry_id)

        applicable_rows: list[ApplicableParameterRow] = []
        context_rows: list[ContextParameterRow] = []
        for entry_id in sorted(entries_by_id):
            values = _parse_json_object(entries_by_id[entry_id].get("normalized_row_json"))
            if entry_id in applicable_entry_ids:
                applicable_rows.append(
                    ApplicableParameterRow(parameter_entry_id=entry_id, values=values)
                )
            else:
                context_rows.append(
                    ContextParameterRow(parameter_entry_id=entry_id, values=values)
                )

        evidence_ids: list[str] = []
        for ev in access_evidence:
            evidence_id = _evidence_id(
                logical_query="Q3A",
                candidate_id=candidate.candidate_id,
                origin_entity_id=table_id,
                evidence_kind="parameter_access",
                content=ev,
            )
            evidence.append(
                EvidenceEntry(
                    evidence_id=evidence_id,
                    kind="parameter_access",
                    source_file=ev["source_file"],
                    line_start=ev["line_start"],
                    line_end=ev["line_end"],
                    source_package_hash=candidate.source_package_hash,
                )
            )
            evidence_ids.append(evidence_id)

        try:
            tables.append(
                ParameterTableContext(
                    name=row["parameter_table"],
                    snapshot_date=row["snapshot_date"],
                    predicates=all_predicates,
                    resolved_predicates=resolved_predicates,
                    unresolved_predicates=unresolved_predicates,
                    applicability_status=status,
                    applicable_rows=applicable_rows,
                    context_rows=context_rows,
                    evidence_ids=sorted(evidence_ids),
                )
            )
        except ValidationError as exc:
            raise ContextBuildError(f"Q3a: fila con estructura invalida: {exc}") from exc

    tables.sort(key=lambda table: table.name)
    return tables, evidence, total_entries


def _parse_json_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ContextBuildError(f"Q3a: normalized_row_json invalido: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ContextBuildError("Q3a: normalized_row_json no es un objeto JSON")
    return parsed


def _build_transactional_tables(
    tx: neo4j.ManagedTransaction,
    candidate: RuleCandidate,
    query: LoadedContextQuery,
    settings: Settings,
) -> tuple[list[TransactionalTableRead], list[EvidenceEntry]]:
    rows = _run(tx, query.effective_text, paragraph_id=candidate.paragraph_id)
    raw_items = (
        [item for item in (rows[0]["tables_read"] or []) if item is not None] if rows else []
    )
    if len(raw_items) > settings.max_transactional_tables:
        raise ContextBuildError(
            f"Q3b: {candidate.paragraph_id!r} tiene {len(raw_items)} accesos a tablas "
            f"transaccionales, excede el limite configurado ({settings.max_transactional_tables})"
        )

    names_by_id: dict[str, str] = {}
    evidence_ids_by_table: dict[str, list[str]] = {}
    evidence: list[EvidenceEntry] = []

    for item in raw_items:
        table_id = item["table_id"]
        name = item["table_name"]
        existing_name = names_by_id.get(table_id)
        if existing_name is not None and existing_name != name:
            raise ContextBuildError(
                f"Q3b: tabla transaccional {table_id!r} con nombre inconsistente entre filas"
            )
        names_by_id[table_id] = name

        evidence_id = _evidence_id(
            logical_query="Q3B",
            candidate_id=candidate.candidate_id,
            origin_entity_id=table_id,
            evidence_kind="transactional_table_read",
            content=item,
        )
        evidence.append(
            EvidenceEntry(
                evidence_id=evidence_id,
                kind="transactional_table_read",
                source_file=item["source_file"],
                line_start=item["line_start"],
                line_end=item["line_end"],
                source_package_hash=candidate.source_package_hash,
            )
        )
        evidence_ids_by_table.setdefault(table_id, []).append(evidence_id)

    tables = [
        TransactionalTableRead(
            name=names_by_id[table_id], evidence_ids=sorted(set(evidence_ids_by_table[table_id]))
        )
        for table_id in sorted(names_by_id)
    ]
    return tables, evidence


# --- D4: decision (Q4) ---


def _build_decision(
    tx: neo4j.ManagedTransaction, candidate: RuleCandidate, query: LoadedContextQuery
) -> tuple[ContextPackageDecision, list[EvidenceEntry]]:
    rows = _run(
        tx,
        query.effective_text,
        paragraph_id=candidate.paragraph_id,
        decision_id=candidate.decision_id,
    )
    if len(rows) == 0:
        raise ContextBuildError(
            f"Q4: no se encontro la Decision {candidate.decision_id!r} en el Paragraph "
            f"{candidate.paragraph_id!r}"
        )
    if len(rows) > 1:
        raise ContextBuildError(
            f"Q4: se encontraron {len(rows)} filas para la Decision {candidate.decision_id!r}; "
            "se esperaba exactamente 1"
        )
    row = rows[0]

    operands_raw = row["operands_json"]
    operands: list[str] = []
    if operands_raw:
        try:
            parsed = json.loads(operands_raw)
        except ValueError as exc:
            raise ContextBuildError(
                f"Q4: operands_json invalido en la Decision {candidate.decision_id!r}: {exc}"
            ) from exc
        if not isinstance(parsed, list):
            raise ContextBuildError(
                f"Q4: operands_json de la Decision {candidate.decision_id!r} no es una lista"
            )
        operands = [str(item) for item in parsed]

    evidence_id = _evidence_id(
        logical_query="Q4",
        candidate_id=candidate.candidate_id,
        origin_entity_id=row["decision_id"],
        evidence_kind="decision",
        content={
            "condition": row["condition"],
            "normalized_condition": row["normalized_condition"],
            "operands_json": operands_raw,
            "outcome_code": row["outcome_code"],
        },
    )
    evidence = [
        EvidenceEntry(
            evidence_id=evidence_id,
            kind="decision",
            source_file=candidate.source_file,
            line_start=row["line_start"],
            line_end=row["line_end"],
            source_package_hash=candidate.source_package_hash,
        )
    ]

    try:
        decision = ContextPackageDecision(
            expression=row["condition"],
            normalized_expression=row["normalized_condition"],
            operands=operands,
            rule_type=row["rule_type"],
            outcome_code=row["outcome_code"],
            evidence_ids=[evidence_id],
        )
    except ValidationError as exc:
        raise ContextBuildError(f"Q4: fila con estructura invalida: {exc}") from exc
    return decision, evidence


# --- D5: effects (Q5a + Q5b) ---


def _build_effects(
    tx: neo4j.ManagedTransaction,
    candidate: RuleCandidate,
    q5a: LoadedContextQuery,
    q5b: LoadedContextQuery,
    *,
    skip_return_codes: bool = False,
) -> tuple[Effects, list[EvidenceEntry]]:
    evidence: list[EvidenceEntry] = []

    if skip_return_codes:
        # Q5a exige un decision_id real (MATCH ... WHERE dec.id =
        # $decision_id) -- nunca se invoca con un valor fabricado; un
        # CALCULATION incondicional tampoco tiene un return_code effect
        # que afirmar (nace de la Decision que le falta).
        return_codes: list[ReturnCodeEffect] = []
        return_code_evidence: list[EvidenceEntry] = []
    else:
        return_codes, return_code_evidence = _build_return_codes(tx, candidate, q5a)
    evidence.extend(return_code_evidence)

    table_effects, table_effect_evidence = _build_table_effects(tx, candidate, q5b)
    evidence.extend(table_effect_evidence)

    return Effects(return_codes=return_codes, table_effects=table_effects), evidence


def _build_return_codes(
    tx: neo4j.ManagedTransaction, candidate: RuleCandidate, query: LoadedContextQuery
) -> tuple[list[ReturnCodeEffect], list[EvidenceEntry]]:
    rows = _run(
        tx,
        query.effective_text,
        paragraph_id=candidate.paragraph_id,
        decision_id=candidate.decision_id,
    )
    if len(rows) != 1:
        raise ContextBuildError(
            f"Q5a: se encontraron {len(rows)} filas para la Decision {candidate.decision_id!r}; "
            "se esperaba exactamente 1"
        )
    row = rows[0]
    if row["return_code"] is None:
        # Decision con outcome_code ambiguo (varios LEADS_TO resueltos):
        # no se puede afirmar un return code effect de valor unico.
        return [], []

    evidence_id = _evidence_id(
        logical_query="Q5A",
        candidate_id=candidate.candidate_id,
        origin_entity_id=row["decision_id"],
        evidence_kind="return_code_effect",
        content={"return_code": row["return_code"], "triggered_when": row["triggered_when"]},
    )
    evidence = [
        EvidenceEntry(
            evidence_id=evidence_id,
            kind="return_code_effect",
            source_file=candidate.source_file,
            line_start=candidate.line_start,
            line_end=None,
            source_package_hash=candidate.source_package_hash,
        )
    ]
    # approved_for_rule_text=true: la Decision candidata (nunca otra del
    # mismo Paragraph, ver Q5a scopeada por decision_id) es evidencia
    # tecnica directa — no una validacion funcional (CLAUDE.md, seccion
    # "Candidato, fidelidad y aprobacion").
    try:
        effect = ReturnCodeEffect(
            code=row["return_code"], approved_for_rule_text=True, evidence_ids=[evidence_id]
        )
    except ValidationError as exc:
        raise ContextBuildError(f"Q5a: fila con estructura invalida: {exc}") from exc
    return [effect], evidence


def _build_table_effects(
    tx: neo4j.ManagedTransaction, candidate: RuleCandidate, query: LoadedContextQuery
) -> tuple[list[TableEffect], list[EvidenceEntry]]:
    rows = _run(tx, query.effective_text, paragraph_id=candidate.paragraph_id)
    row = rows[0] if rows else {"attributed": [], "program_context": []}
    attributed = [item for item in (row.get("attributed") or []) if item is not None]
    program_context = [item for item in (row.get("program_context") or []) if item is not None]

    evidence: list[EvidenceEntry] = []
    by_key: dict[tuple[str, str, str], TableEffect] = {}

    for item in attributed + program_context:
        key = (item["table_id"], item["operation"], item["attribution_scope"])
        evidence_id = _evidence_id(
            logical_query="Q5B",
            candidate_id=candidate.candidate_id,
            origin_entity_id=item["table_id"],
            evidence_kind="table_effect",
            content=item,
        )
        evidence.append(
            EvidenceEntry(
                evidence_id=evidence_id,
                kind="table_effect",
                source_file=item["source_file"],
                line_start=item["line_start"],
                line_end=item["line_end"],
                source_package_hash=candidate.source_package_hash,
            )
        )
        try:
            effect = TableEffect(
                table=item["table_name"],
                operation=TableEffectOperation(item["operation"]),
                attribution_scope=AttributionScope(item["attribution_scope"]),
                approved_for_rule_text=item["attribution_scope"] != "PROGRAM_CONTEXT",
                evidence_ids=[evidence_id],
            )
        except ValidationError as exc:
            raise ContextBuildError(f"Q5b: fila con estructura invalida: {exc}") from exc

        existing = by_key.get(key)
        if existing is None:
            by_key[key] = effect
            continue
        if existing.model_dump(exclude={"evidence_ids"}) != effect.model_dump(
            exclude={"evidence_ids"}
        ):
            raise ContextBuildError(
                f"Q5b: efecto de tabla {key!r} con clasificacion inconsistente entre filas"
            )
        merged_ids = sorted(set(existing.evidence_ids) | set(effect.evidence_ids))
        by_key[key] = existing.model_copy(update={"evidence_ids": merged_ids})

    return [by_key[key] for key in sorted(by_key)], evidence


# --- D6: batch (Q6) ---


def _build_batch_context(
    tx: neo4j.ManagedTransaction, candidate: RuleCandidate, query: LoadedContextQuery
) -> BatchContext:
    rows = _run(tx, query.effective_text, paragraph_id=candidate.paragraph_id)
    if not rows:
        return BatchContext(status=BatchContextStatus.NOT_AVAILABLE, downstream_jobs=[])

    jobs_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        job_id = row["job_id"]
        job = {
            "job_id": job_id,
            "job_name": row["job_name"],
            "schedule": row["schedule"],
            "window_start": row["window_start"],
            "window_end": row["window_end"],
            "preceded_by": row["preceded_by"],
            "triggers_next": row["triggers_next"],
        }
        existing = jobs_by_id.get(job_id)
        if existing is not None and existing != job:
            raise ContextBuildError(
                f"Q6: BatchJob {job_id!r} con contenido inconsistente entre filas"
            )
        jobs_by_id[job_id] = job

    incomplete = any(job["job_name"] is None for job in jobs_by_id.values())
    status = BatchContextStatus.PARTIAL if incomplete else BatchContextStatus.COMPLETE
    return BatchContext(
        status=status, downstream_jobs=[jobs_by_id[key] for key in sorted(jobs_by_id)]
    )


# --- D7: domain glossary (Q7) ---


def _build_domain_glossary(
    tx: neo4j.ManagedTransaction, candidate: RuleCandidate, query: LoadedContextQuery
) -> tuple[list[DomainGlossaryEntry], list[EvidenceEntry]]:
    rows = _run(tx, query.effective_text, paragraph_id=candidate.paragraph_id)
    evidence: list[EvidenceEntry] = []
    by_key: dict[tuple[str, str], DomainGlossaryEntry] = {}

    for row in rows:
        key = (row["domain_term_id"], row["data_item_id"])
        evidence_id = _evidence_id(
            logical_query="Q7",
            candidate_id=candidate.candidate_id,
            origin_entity_id=row["data_item_id"],
            evidence_kind="domain_glossary",
            content=row,
        )
        evidence.append(
            EvidenceEntry(
                evidence_id=evidence_id,
                kind="domain_glossary",
                source_file=candidate.source_file,
                line_start=None,
                line_end=None,
                source_package_hash=candidate.source_package_hash,
            )
        )
        try:
            entry = DomainGlossaryEntry(
                data_item_id=row["data_item_id"],
                technical_name=row["technical_name"],
                semantic_tag=row["semantic_tag"],
                domain_term_id=row["domain_term_id"],
                functional_name=row["functional_name"],
                definition=row["definition"],
                entity_type=row["entity_type"],
                source_kind=row["source_kind"],
                authoritative_source=row["authoritative_source"],
                confidence=row["confidence"],
                evidence_ids=[evidence_id],
            )
        except ValidationError as exc:
            raise ContextBuildError(f"Q7: fila con estructura invalida: {exc}") from exc

        existing = by_key.get(key)
        if existing is None:
            by_key[key] = entry
            continue
        if existing.model_dump(exclude={"evidence_ids"}) != entry.model_dump(
            exclude={"evidence_ids"}
        ):
            raise ContextBuildError(
                f"Q7: mapping {key!r} (domain_term_id, data_item_id) con contenido "
                "inconsistente entre filas"
            )
        merged_ids = sorted(set(existing.evidence_ids) | set(entry.evidence_ids))
        by_key[key] = existing.model_copy(update={"evidence_ids": merged_ids})

    return [by_key[key] for key in sorted(by_key)], evidence
