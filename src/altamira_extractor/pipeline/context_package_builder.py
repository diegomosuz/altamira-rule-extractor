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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import neo4j
from pydantic import ValidationError

from ..config import Settings
from ..contracts.candidate import RuleCandidate
from ..contracts.candidate_promotion_assessment import UnifiedRuleFamily
from ..contracts.canonical import CanonicalDataItem, CanonicalParagraph, CanonicalStatement
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
    LocationKind,
    StatementKind,
    TableEffectOperation,
)
from .cypher_query_loader import LoadedContextQuery
from .errors import ContextBuildError
from .identifiers import (
    DECISION_STATEMENT_KINDS,
    SymbolTable,
    build_symbol_table,
    decision_id_for,
    decision_statements_in_order,
)
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
    canonical_paragraphs: Mapping[tuple[str, str], CanonicalParagraph] | None = None,
    data_items_by_source_file: Mapping[str, tuple[str, Sequence[CanonicalDataItem]]]
    | None = None,
) -> list[ContextPackage]:
    """`canonical_paragraphs`: opcional, mapa `(source_file,
    paragraph_name) -> CanonicalParagraph` para el enriquecimiento
    SQLCODE (`_enrich_decision_with_sql_causal_evidence`); su ausencia
    nunca impide construir un `ContextPackage`. `data_items_by_source_file`
    (Fase 15B3-C5-B): opcional, mapa `source_file -> (program_name,
    CanonicalDataItem[])` del programa dueno, para el enriquecimiento
    `declared_value_context` (`_enrich_decision_with_declared_value_evidence`)
    -- `program_name` viaja explicito desde `CanonicalProgram` (nunca
    reconstruido parseando `paragraph_id`/`candidate_id`/`decision_id`,
    correccion pre-commit "declaration provenance identity"); su ausencia
    tampoco impide construir un `ContextPackage`. `SymbolTable` se
    construye una unica vez por `source_file` (nunca por candidato)."""
    paragraphs = canonical_paragraphs or {}
    program_names: dict[str, str] = {}
    symbol_tables: dict[str, SymbolTable] = {}
    for source_file, (program_name, items) in (data_items_by_source_file or {}).items():
        program_names[source_file] = program_name
        symbol_tables[source_file] = build_symbol_table(items)
    return [
        _build_one_context_package(
            tx,
            candidate,
            queries=queries,
            settings=settings,
            canonical_paragraphs=paragraphs,
            # candidate.source_file: str | None (Fase
            # 15B4-CANDIDATE-QUALITY-5A, programas con COPY) -- si es
            # None, ningun source_file de data_items_by_source_file
            # puede coincidir; el enriquecimiento queda ausente, nunca
            # se busca con una clave fabricada.
            program_name=(
                program_names.get(candidate.source_file)
                if candidate.source_file is not None
                else None
            ),
            symbol_table=(
                symbol_tables.get(candidate.source_file)
                if candidate.source_file is not None
                else None
            ),
        )
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
    canonical_paragraphs: Mapping[tuple[str, str], CanonicalParagraph],
    program_name: str | None = None,
    symbol_table: SymbolTable | None = None,
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
        decision, d4_evidence = _enrich_decision_with_sql_causal_evidence(
            decision, d4_evidence, candidate, canonical_paragraphs
        )
        decision, d4_evidence = _enrich_decision_with_declared_value_evidence(
            decision, d4_evidence, candidate, program_name, symbol_table
        )
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
        package = ContextPackage(
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

    # Fail-closed antes de considerar CONTEXTS_BUILT exitoso para este
    # candidato: un ContextPackage puede ser Pydantic-valido (outcome_code
    # null es un valor estructuralmente legitimo) y aun asi ser
    # semanticamente incompleto si RuleCandidate ya tenia el hecho
    # determinista resuelto. Nunca continua hacia generacion de RuleDraft
    # con una perdida silenciosa de este tipo.
    _validate_deterministic_integrity(candidate, package)
    return package


def _validate_deterministic_integrity(candidate: RuleCandidate, package: ContextPackage) -> None:
    """Guardia fail-closed en el limite 06 -> 07 (Fase DETERMINISTIC
    INTEGRITY HARDENING, post-v1.17.0): confirma que ningun hecho
    determinista ya autoritativo en `RuleCandidate` se perdio o fue
    sustituido silenciosamente por una representacion secundaria (p.ej.
    la propiedad `Decision.outcome_code` del grafo, que para candidatos
    V2 es una computacion estructuralmente distinta -- ver
    `_build_decision`). Solo valida hechos donde `RuleCandidate` es la
    fuente autoritativa confirmada (outcome_code, condition, identidad);
    nunca valida hechos GRAPH_OWNED (rule_type, code_slice, parameter/
    table context, batch, domain glossary) que `RuleCandidate` no posee
    y para los que el grafo SI es la fuente autoritativa -- fallar ahi
    seria un falso positivo, no una proteccion real.

    candidate.outcome_code no es simplemente "preservado o no": es
    autoritativo de forma INCONDICIONAL (Fase 1/2 del hardening) -- un
    valor de grafo contradictorio (no solo null) NUNCA debe preferirse
    silenciosamente ni disparar este fallo, porque `Decision.outcome_code`
    del grafo es una heuristica V1-only (unico literal MOVE resuelto)
    que nunca tuvo el contrato de representar el outcome real de un
    candidato V2; una discrepancia ahi es esperada y normal (confirmado
    empiricamente: Catherine real produce esta discrepancia en sus 13/13
    candidatos V2), no una senal de corrupcion. Por eso esta funcion
    nunca compara contra el valor de fila Q4/Q5a (ya descartado en
    `_build_decision`/`_build_return_codes`): valida unicamente que el
    propio `ContextPackage` ya construido refleje `candidate.outcome_code`
    sin perdida, sin importar que trajo el grafo."""

    def _violation(invariant: str, upstream: object, downstream: object) -> ContextBuildError:
        return ContextBuildError(
            "DETERMINISTIC_INTEGRITY_VIOLATION "
            f"candidate={candidate.candidate_id!r} invariant={invariant} "
            f"upstream={upstream!r} downstream={downstream!r}"
        )

    if package.candidate.candidate_id != candidate.candidate_id:
        raise _violation(
            "CANDIDATE_ID_STABILITY", candidate.candidate_id, package.candidate.candidate_id
        )
    if package.candidate.decision_id != candidate.decision_id:
        raise _violation(
            "DECISION_ID_STABILITY", candidate.decision_id, package.candidate.decision_id
        )

    if candidate.outcome_code is not None:
        downstream_outcome = package.decision.outcome_code if package.decision else None
        if downstream_outcome != candidate.outcome_code:
            raise _violation(
                "OUTCOME_CODE_PRESERVATION", candidate.outcome_code, downstream_outcome
            )
        return_codes = [effect.code for effect in package.effects.return_codes]
        if return_codes != [candidate.outcome_code]:
            raise _violation(
                "RETURN_CODE_EFFECT_PRESERVATION", candidate.outcome_code, return_codes
            )

    if candidate.condition is not None:
        downstream_condition = package.decision.expression if package.decision else None
        if downstream_condition != candidate.condition:
            raise _violation("CONDITION_PRESERVATION", candidate.condition, downstream_condition)


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


# --- D4: decision (Q4) + SQLCODE causal evidence (Fase 15B3-C3-C-B) ---
# Evidencial unicamente: nunca fabrica SQLCODE como DataItem, nunca usa
# DATA_DEPENDS_ON (prohibe auto-dependencia; EXEC SQL y la Decision
# suelen compartir paragraph).

_SQL_CAUSAL_BARRIER_KINDS = frozenset(
    {
        StatementKind.CALL,
        StatementKind.PERFORM,
        StatementKind.GO_TO,
        StatementKind.PROGRAM_TERMINATION,
    }
)


def _is_sqlcode_decision(statement: CanonicalStatement) -> bool:
    """Match exacto (case-insensitive) de ``SQLCODE`` en ``operands``,
    nunca por substring (``WS-SQLCODE-FLAG`` no califica)."""
    if statement.kind not in DECISION_STATEMENT_KINDS:
        return False
    return any(operand.strip().upper() == "SQLCODE" for operand in statement.operands)


@dataclass(frozen=True)
class _SqlCausalLinkage:
    status: Literal["PROVEN", "AMBIGUOUS", "NOT_AVAILABLE"]
    exec_sql_statement: CanonicalStatement | None = None


def _statements_by_parent(
    statements: Sequence[CanonicalStatement],
) -> dict[str | None, list[CanonicalStatement]]:
    grouped: dict[str | None, list[CanonicalStatement]] = {}
    for statement in statements:
        grouped.setdefault(statement.parent_statement_id, []).append(statement)
    return grouped


def _subtree_has_barrier_or_sql(
    by_parent: Mapping[str | None, list[CanonicalStatement]], root_statement_id: str
) -> bool:
    """`True` si algun descendiente de `root_statement_id` (cualquier
    rama, cualquier profundidad) es un barrier o un EXEC_SQL -- esa rama
    podria ejecutarse y alterar SQLCODE, nunca se asume que no."""
    for child in by_parent.get(root_statement_id, []):
        if child.kind == StatementKind.EXEC_SQL or child.kind in _SQL_CAUSAL_BARRIER_KINDS:
            return True
        if _subtree_has_barrier_or_sql(by_parent, child.statement_id):
            return True
    return False


def _nearest_preceding_operative_exec_sql(
    statements: Sequence[CanonicalStatement], decision: CanonicalStatement
) -> _SqlCausalLinkage:
    """Escanea hacia atras dentro del mismo scope (`parent_statement_id`
    de `decision`), en el orden ya contractual de
    `CanonicalParagraph.statements` -- nunca reordena por `line_start`,
    nunca cruza de paragraph/rama. Preferencia: falso negativo antes que
    evidencia causal falsa (ver docs/SEMANTIC_EFFECTS.md)."""
    same_scope = [s for s in statements if s.parent_statement_id == decision.parent_statement_id]
    decision_index: int | None = None
    for index, statement in enumerate(same_scope):
        if statement.statement_id == decision.statement_id:
            decision_index = index
            break
    if decision_index is None:
        return _SqlCausalLinkage(status="NOT_AVAILABLE")

    by_parent = _statements_by_parent(statements)
    for statement in reversed(same_scope[:decision_index]):
        if statement.kind == StatementKind.EXEC_SQL:
            if statement.sql_access:
                return _SqlCausalLinkage(status="PROVEN", exec_sql_statement=statement)
            return _SqlCausalLinkage(status="AMBIGUOUS")
        if statement.kind in _SQL_CAUSAL_BARRIER_KINDS:
            return _SqlCausalLinkage(status="AMBIGUOUS")
        if statement.kind in DECISION_STATEMENT_KINDS and _subtree_has_barrier_or_sql(
            by_parent, statement.statement_id
        ):
            return _SqlCausalLinkage(status="AMBIGUOUS")
    return _SqlCausalLinkage(status="NOT_AVAILABLE")


def _decision_statement_for_candidate(
    paragraph: CanonicalParagraph, candidate: RuleCandidate
) -> CanonicalStatement | None:
    """Unica fuente de identidad para `decision_id` (Fase 15B3-C3-C-B,
    corrective: antes reimplementaba la enumeracion de
    `semantic_graph_builder`, ahora regenera el id con las mismas
    `identifiers.decision_statements_in_order`/`decision_id_for` y
    compara por igualdad -- nunca puede divergir del grafo."""
    if candidate.decision_id is None:
        return None
    for ordinal, statement in enumerate(decision_statements_in_order(paragraph), start=1):
        candidate_id = decision_id_for(
            candidate.paragraph_id, ordinal=ordinal, line_start=statement.line_start
        )
        if candidate_id == candidate.decision_id:
            return statement
    return None


def _sql_causal_evidence_id(*, source_package_hash: str, exec_sql_statement_id: str) -> str:
    """Sin `candidate_id` (a diferencia de `_evidence_id`): representa el
    EXEC SQL real, no la pareja SQL+Decision -- Decisions distintas
    causadas por el mismo EXEC SQL citan el mismo id."""
    payload = {
        "logical_query": "SQL_CAUSAL_CONTEXT",
        "source_package_hash": source_package_hash,
        "exec_sql_statement_id": exec_sql_statement_id,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"evidence::{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def _enrich_decision_with_sql_causal_evidence(
    decision: ContextPackageDecision,
    evidence: list[EvidenceEntry],
    candidate: RuleCandidate,
    canonical_paragraphs: Mapping[tuple[str, str], CanonicalParagraph],
) -> tuple[ContextPackageDecision, list[EvidenceEntry]]:
    """Si la Decision es SQLCODE-related y el linkage es PROVEN, agrega
    un `EvidenceEntry` y su id a `decision.evidence_ids` -- cualquier
    otro caso (AMBIGUOUS/NOT_AVAILABLE/paragraph o decision no
    correlacionables) devuelve `decision`/`evidence` sin modificar.

    `candidate.source_file is None` (Fase 15B4-CANDIDATE-QUALITY-5A,
    programas con COPY) nunca puede coincidir con una clave real de
    `canonical_paragraphs`: el enriquecimiento SQLCODE queda ausente,
    nunca se busca con una clave fabricada."""
    if candidate.source_file is None:
        return decision, evidence
    paragraph = canonical_paragraphs.get((candidate.source_file, candidate.paragraph_name))
    if paragraph is None:
        return decision, evidence

    decision_statement = _decision_statement_for_candidate(paragraph, candidate)
    if decision_statement is None or not _is_sqlcode_decision(decision_statement):
        return decision, evidence

    linkage = _nearest_preceding_operative_exec_sql(paragraph.statements, decision_statement)
    if linkage.status != "PROVEN" or linkage.exec_sql_statement is None:
        return decision, evidence

    exec_sql = linkage.exec_sql_statement
    causal_evidence_id = _sql_causal_evidence_id(
        source_package_hash=candidate.source_package_hash,
        exec_sql_statement_id=exec_sql.statement_id,
    )
    sql_access_summary = [
        {"table": access.table, "operation": access.operation.value}
        for access in exec_sql.sql_access
    ]
    causal_evidence = EvidenceEntry(
        evidence_id=causal_evidence_id,
        kind="sql_causal_context",
        source_file=candidate.source_file,
        line_start=exec_sql.line_start,
        line_end=exec_sql.line_end,
        source_package_hash=candidate.source_package_hash,
        details={
            "paragraph_id": candidate.paragraph_id,
            "exec_sql_statement_id": exec_sql.statement_id,
            "statement_kind": exec_sql.kind.value,
            "source_text": exec_sql.source_text,
            "sql_access": sql_access_summary,
        },
    )
    enriched_decision = decision.model_copy(
        update={"evidence_ids": sorted({*decision.evidence_ids, causal_evidence_id})}
    )
    return enriched_decision, [*evidence, causal_evidence]


def _declared_value_evidence_id(
    *,
    source_package_hash: str,
    program_name: str,
    source_file: str,
    line: int,
    qualified_name: str,
) -> str:
    """Sin `candidate_id` (misma razon que `_sql_causal_evidence_id`):
    identifica la DECLARACION del DataItem, no la Decision -- dos
    Decisions distintas que referencian el mismo DataItem citan el mismo
    id. `program_name` distingue programas (nunca `source_file` solo,
    correccion pre-commit "declaration provenance identity"); `source_file`
    + `line` (correccion pre-commit "strong declaration evidence identity")
    identifican la declaracion FISICA exacta -- nunca solo el nombre del
    DataItem dentro de un programa, sin asumir que `program_name` por si
    solo sea globalmente unico. Solo se llama cuando `location_kind ==
    EXACT` ya garantizo que `source_file`/`line` son reales (ver
    `_enrich_decision_with_declared_value_evidence`)."""
    payload = {
        "logical_query": "DECLARED_VALUE_CONTEXT",
        "source_package_hash": source_package_hash,
        "program_name": program_name,
        "source_file": source_file,
        "line": line,
        "data_item_qualified_name": qualified_name,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"evidence::{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def _enrich_decision_with_declared_value_evidence(
    decision: ContextPackageDecision,
    evidence: list[EvidenceEntry],
    candidate: RuleCandidate,
    program_name: str | None,
    symbol_table: SymbolTable | None,
) -> tuple[ContextPackageDecision, list[EvidenceEntry]]:
    """Por cada operand de la Decision (`decision.operands`, nunca por
    substring de `expression`) que resuelve SIN ambiguedad a un
    CanonicalDataItem con `declared_value` no nulo Y `location_kind ==
    EXACT` (unico caso donde `source_file`/`line` del DataItem son reales
    y confiables -- correccion pre-commit "no fabricar source location"),
    agrega un `EvidenceEntry(kind="declared_value_context")` -- provenance
    de DECLARACION unicamente, `effective_runtime_value_proven` siempre
    `False` (nunca afirma valor efectivo/runtime, C5-A2 seccion 4). La
    evidencia SIEMPRE cita `data_item.source_file`/`data_item.line`
    (nunca `candidate.source_file` como fallback): un DataItem con
    `location_kind` PREPROCESSED_STREAM/UNKNOWN nunca produce evidencia
    citable, aunque `declared_value` siga poblado en el `CanonicalDataItem`
    -- falso negativo aceptable, nunca una ubicacion fabricada. Operand
    ausente/ambiguo/sin declared_value/sin program_name/sin ubicacion
    confiable: se omite, la regla permanece valida sin cambios
    (enrichment, nunca gate)."""
    if symbol_table is None or program_name is None or not decision.operands:
        return decision, evidence

    new_entries: list[EvidenceEntry] = []
    new_evidence_ids: set[str] = set()
    for operand in decision.operands:
        resolved = symbol_table.resolve(operand)
        if resolved.ambiguous or resolved.qualified_name is None:
            continue
        data_item = symbol_table.by_qualified_name.get(resolved.qualified_name)
        if data_item is None or data_item.declared_value is None:
            continue
        if (
            data_item.location_kind != LocationKind.EXACT
            or data_item.source_file is None
            or data_item.line is None
        ):
            continue
        evidence_id = _declared_value_evidence_id(
            source_package_hash=candidate.source_package_hash,
            program_name=program_name,
            source_file=data_item.source_file,
            line=data_item.line,
            qualified_name=data_item.qualified_name,
        )
        if evidence_id in new_evidence_ids:
            continue
        new_evidence_ids.add(evidence_id)
        new_entries.append(
            EvidenceEntry(
                evidence_id=evidence_id,
                kind="declared_value_context",
                source_file=data_item.source_file,
                line_start=data_item.line,
                line_end=data_item.line,
                source_package_hash=candidate.source_package_hash,
                details={
                    "data_item_name": data_item.name,
                    "data_item_qualified_name": data_item.qualified_name,
                    "declared_value": data_item.declared_value,
                    "value_semantics": "DECLARED_INITIAL_VALUE",
                    "effective_runtime_value_proven": False,
                    "level": data_item.level,
                    "pic": data_item.pic,
                    "usage": data_item.usage,
                    "program_name": program_name,
                },
            )
        )
    if not new_entries:
        return decision, evidence
    enriched_decision = decision.model_copy(
        update={"evidence_ids": sorted({*decision.evidence_ids, *new_evidence_ids})}
    )
    return enriched_decision, [*evidence, *new_entries]


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

    # outcome_code se toma de candidate.outcome_code (nunca de la fila Q4):
    # los detectores V2 (p.ej. V2_LEVEL_88_RETURN_CODE) resuelven el valor
    # en memoria a partir de hechos de propagacion semantica y nunca lo
    # escriben en Decision.outcome_code del grafo -- usar la fila aqui
    # perderia silenciosamente ese hecho determinista en D4/D5. Para
    # candidatos V1/Q0 esto es un no-op: su propio outcome_code ya proviene
    # de esa misma propiedad del grafo (ver queries/v1/q0_candidates.cypher).
    #
    # condition/normalized_condition: mismo principio (Ciclo 4, v1.18.2,
    # EVALUATE/WHEN), aplicado ahora tambien a expression/normalized_
    # expression. dec.expression/dec.normalized_expression son SIEMPRE el
    # sujeto crudo del EVALUATE completo (ej. "SQLCODE"): un solo nodo
    # Decision representa TODO el EVALUATE, nunca una rama WHEN especifica
    # (ver semantic_graph_builder.py::_build_decisions_and_leads_to). Para
    # una Decision de tipo IF esto es un no-op (candidate.condition ya
    # coincide exactamente con la fila, ambos derivados de la misma
    # CanonicalStatement.expression sin division sujeto/rama). Para una
    # Decision de tipo EVALUATE, candidate.condition ya resuelve el
    # predicado especifico de la rama de ESTE candidato (branch_condition
    # limpio via StatementExtractor.buildBranchCondition cuando la rama es
    # una comparacion directa contra un literal puro; en caso contrario,
    # el mismo sujeto crudo que la fila ya expone -- ver comentario en
    # enhanced_candidate_integration.py::_convert_v2_candidate). Usar la
    # fila aqui perderia silenciosamente ese hecho determinista en D4/D5,
    # exactamente el mismo riesgo que outcome_code arriba.
    # candidate.condition es no-None aqui por construccion: _build_decision
    # solo se invoca cuando decision_id no es None, y _check_decision_anchor_
    # by_family (contracts/candidate.py) exige condition no-None siempre que
    # decision_id no sea None (unica excepcion, CALCULATION incondicional,
    # tiene decision_id=None y por lo tanto nunca llega a Q4).
    assert candidate.condition is not None  # noqa: S101
    condition_text = candidate.condition
    evidence_id = _evidence_id(
        logical_query="Q4",
        candidate_id=candidate.candidate_id,
        origin_entity_id=row["decision_id"],
        evidence_kind="decision",
        content={
            "condition": condition_text,
            "normalized_condition": condition_text,
            "operands_json": operands_raw,
            "outcome_code": candidate.outcome_code,
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
            expression=condition_text,
            normalized_expression=condition_text,
            operands=operands,
            rule_type=row["rule_type"],
            outcome_code=candidate.outcome_code,
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
    # Igual que en D4: el valor autoritativo es candidate.outcome_code, no
    # la fila Q5a (Decision.outcome_code del grafo, ambiguo o ausente para
    # candidatos V2 que resuelven en memoria -- ver _build_decision). Sin
    # outcome_code no hay return code effect que afirmar.
    if candidate.outcome_code is None:
        return [], []

    evidence_id = _evidence_id(
        logical_query="Q5A",
        candidate_id=candidate.candidate_id,
        origin_entity_id=row["decision_id"],
        evidence_kind="return_code_effect",
        content={"return_code": candidate.outcome_code, "triggered_when": row["triggered_when"]},
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
            code=candidate.outcome_code, approved_for_rule_text=True, evidence_ids=[evidence_id]
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
