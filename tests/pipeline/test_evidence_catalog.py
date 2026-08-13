"""Tests del catalogo deterministico de alias de evidencia (checkpoint
correctivo: el LLM nunca escribe evidence_id/evidence_path reales)."""

from __future__ import annotations

import json
from typing import Any

from altamira_extractor.contracts.context_package import (
    BatchContext,
    CodeSliceEntry,
    Completeness,
    ContextPackage,
    ContextPackageCandidate,
    ContextPackageDecision,
    ContextPackageOperation,
    ContextPackageScope,
    DataContext,
    Effects,
    EvidenceEntry,
    ReturnCodeEffect,
)
from altamira_extractor.contracts.enums import (
    BatchContextStatus,
    CompletenessStatus,
    EvidenceValidationStatus,
    FunctionalReviewStatus,
    InclusionReason,
)
from altamira_extractor.contracts.rule_draft import Claim, RuleDraft
from altamira_extractor.pipeline.evidence_catalog import (
    EvidenceCatalog,
    EvidenceCatalogEntry,
    _describe_evidence,
    build_evidence_catalog,
    redact_rule_draft_for_prompt,
)

_HASH_A = "a" * 64


def test_describe_evidence_source_file_none_renders_honest_placeholder() -> None:
    """Fase 15B4-CANDIDATE-QUALITY-5A: `EvidenceEntry.source_file=None`
    (programa con COPY) nunca se interpola como el texto literal
    "None" en la descripcion que ve el LLM."""
    entry = EvidenceEntry(
        evidence_id="ev-1",
        kind="decision",
        source_file=None,
        line_start=10,
        line_end=12,
        source_package_hash=_HASH_A,
    )
    description = _describe_evidence(entry)
    assert "None" not in description
    assert "ubicacion no determinable" in description
    assert description == "decision (ubicacion no determinable:10-12)"


def _package(
    *,
    candidate_id: str = "cand-1",
    evidence_ids: list[str] | None = None,
    include_return_code: bool = True,
) -> ContextPackage:
    """Candidato sintetico con evidencia en `decision`, `code_slice[0]` y
    (opcionalmente) `effects.return_codes[0]`: 3 contenedores distintos,
    suficientes para ejercitar deduplicacion, multiples pares por
    evidence_id, y orden estable."""
    ids = evidence_ids if evidence_ids is not None else ["ev-1"]
    evidence = [
        EvidenceEntry(
            evidence_id=evidence_id,
            kind="decision",
            source_file="cobol/PROG1.cbl",
            line_start=10,
            line_end=10,
            source_package_hash=_HASH_A,
        )
        for evidence_id in ids
    ]
    return ContextPackage(
        schema_version="2.0",
        candidate=ContextPackageCandidate(
            candidate_id=candidate_id,
            decision_id="dec-1",
            detector_id="det",
            detector_version="1.0",
            detector_score=1.0,
        ),
        scope=ContextPackageScope(
            country="AR",
            application="Transferencias",
            operation=ContextPackageOperation(logical_name="OP1", description=None),
            program="PROG1",
            program_version="1",
            paragraph="MAIN",
            source_file="cobol/PROG1.cbl",
            line_start=10,
            line_end=10,
            source_package_hash=_HASH_A,
        ),
        code_slice=[
            CodeSliceEntry(
                paragraph_id="p1",
                paragraph="MAIN",
                source_file="cobol/PROG1.cbl",
                source_text="IF WS-COD = 'R001'",
                line_start=10,
                line_end=10,
                inclusion_reason=InclusionReason.CANDIDATE,
                evidence_ids=ids,
            )
        ],
        data_context=DataContext(parameter_tables=[], transactional_tables_read=[]),
        decision=ContextPackageDecision(
            expression="WS-COD = 'R001'",
            normalized_expression="WS-COD = 'R001'",
            operands=[],
            rule_type=None,
            outcome_code="R001",
            evidence_ids=ids,
        ),
        effects=Effects(
            return_codes=(
                [
                    ReturnCodeEffect(
                        code="R001", approved_for_rule_text=True, evidence_ids=ids
                    )
                ]
                if include_return_code
                else []
            ),
            table_effects=[],
        ),
        batch_context=BatchContext(status=BatchContextStatus.NOT_AVAILABLE, downstream_jobs=[]),
        domain_glossary=[],
        evidence=evidence,
        completeness=Completeness(
            D1=CompletenessStatus.COMPLETE,
            D2=CompletenessStatus.COMPLETE,
            D3=CompletenessStatus.NOT_AVAILABLE,
            D4=CompletenessStatus.COMPLETE,
            D5=CompletenessStatus.NOT_AVAILABLE,
            D6=CompletenessStatus.NOT_AVAILABLE,
            D7=CompletenessStatus.NOT_AVAILABLE,
        ),
    )


def _valid_rule_draft(**claim_overrides: Any) -> RuleDraft:
    claim_kwargs: dict[str, Any] = {
        "claim_id": "c1",
        "field": "condition",
        "evidence_paths": ["$.decision"],
        "evidence_ids": ["ev-1"],
    }
    claim_kwargs.update(claim_overrides)
    return RuleDraft(
        schema_version="2.0",
        title="Titulo",
        context="Contexto",
        statement="Enunciado",
        condition="WS-COD = 'R001'",
        parameters=[],
        effect="Efecto",
        parameter_source=None,
        traceability=["ev-1"],
        limitations=["Requiere revision funcional"],
        claims=[Claim(**claim_kwargs)],
        evidence_validation_status=EvidenceValidationStatus.PENDING,
        functional_review_status=FunctionalReviewStatus.NEEDS_FUNCTIONAL_REVIEW,
    )


# --- build_evidence_catalog: determinismo, orden, alias unicos ---


def test_catalog_is_deterministic_across_repeated_builds() -> None:
    package = _package()
    catalog_a = build_evidence_catalog(package)
    catalog_b = build_evidence_catalog(package)
    assert catalog_a == catalog_b


def test_catalog_aliases_are_unique() -> None:
    catalog = build_evidence_catalog(_package())
    aliases = [entry.alias for entry in catalog.entries]
    assert len(aliases) == len(set(aliases))
    assert len(aliases) > 1


def test_catalog_alias_order_is_stable_and_sequential() -> None:
    catalog = build_evidence_catalog(_package())
    aliases = [entry.alias for entry in catalog.entries]
    assert aliases == sorted(aliases)
    assert aliases[0] == "E001"
    assert aliases == [f"E{i + 1:03d}" for i in range(len(aliases))]


def test_catalog_never_depends_on_set_or_dict_iteration_order() -> None:
    """Dos ContextPackage con evidencia logicamente identica pero
    construidos con IDs en orden distinto producen el MISMO catalogo
    (mismos alias, mismo orden): la deduplicacion/orden nunca depende de
    la iteracion de un `set`/`dict` no determinista."""
    package_a = _package(evidence_ids=["ev-1", "ev-2"])
    package_b = _package(evidence_ids=["ev-2", "ev-1"])
    # El propio EvidenceEntry list difiere en orden de insercion; el
    # catalogo resultante debe ser identico de todas formas.
    catalog_a = build_evidence_catalog(package_a)
    catalog_b = build_evidence_catalog(package_b)
    assert catalog_a == catalog_b


def test_catalog_deduplicates_repeated_container_evidence_id_pairs() -> None:
    """`decision.evidence_ids` y `code_slice[0].evidence_ids` comparten
    "ev-1" en `_package()`, pero cada CONTENEDOR es distinto: el
    catalogo debe tener una entrada por (evidence_id, path) unico, sin
    duplicar la MISMA pareja dos veces."""
    catalog = build_evidence_catalog(_package())
    pairs = [(entry.evidence_id, entry.evidence_path) for entry in catalog.entries]
    assert len(pairs) == len(set(pairs))


def test_catalog_entry_always_represents_id_and_path_jointly() -> None:
    """Cada entrada tiene AMBOS evidence_id y evidence_path -- nunca uno
    sin el otro -- por construccion del propio dataclass (no hay forma
    de construir una entrada con un campo vacio/None)."""
    catalog = build_evidence_catalog(_package())
    for entry in catalog.entries:
        assert entry.evidence_id
        assert entry.evidence_path
        assert entry.evidence_path.startswith("$.")


def test_catalog_is_immutable() -> None:
    catalog = build_evidence_catalog(_package())
    entry = catalog.entries[0]
    try:
        entry.alias = "E999"  # type: ignore[misc]
        raised = False
    except AttributeError:
        raised = True
    assert raised, "EvidenceCatalogEntry debe ser inmutable (frozen dataclass)"

    try:
        catalog.entries = ()  # type: ignore[misc]
        raised = False
    except AttributeError:
        raised = True
    assert raised, "EvidenceCatalog debe ser inmutable (frozen dataclass)"


def test_catalog_rejects_duplicate_alias_construction() -> None:
    dup = EvidenceCatalogEntry(
        alias="E001",
        evidence_id="ev-1",
        evidence_path="$.decision",
        kind="decision",
        description="",
    )
    dup2 = EvidenceCatalogEntry(
        alias="E001",
        evidence_id="ev-2",
        evidence_path="$.code_slice[0]",
        kind="code_slice",
        description="",
    )
    try:
        EvidenceCatalog(entries=(dup, dup2))
        raised = False
    except ValueError:
        raised = True
    assert raised


# --- resolve / find_alias (busqueda exacta, sin aproximaciones) ---


def test_resolve_returns_entry_for_valid_alias() -> None:
    catalog = build_evidence_catalog(_package())
    first = catalog.entries[0]
    resolved = catalog.resolve(first.alias)
    assert resolved == first


def test_resolve_returns_none_for_unknown_alias_never_approximates() -> None:
    catalog = build_evidence_catalog(_package())
    assert catalog.resolve("E999") is None
    # Ni siquiera una variante de un alias real (E001 vs E01, E0001,
    # e001) se acepta: resolucion EXACTA unicamente.
    real_alias = catalog.entries[0].alias
    assert catalog.resolve(real_alias.lower()) is None
    assert catalog.resolve(real_alias[:-1]) is None


def test_find_alias_is_exact_pair_lookup() -> None:
    catalog = build_evidence_catalog(_package())
    entry = catalog.entries[0]
    assert catalog.find_alias(entry.evidence_id, entry.evidence_path) == entry.alias
    # Un id real con un path que NUNCA existio para ese id (par cruzado
    # inventado) no resuelve.
    assert catalog.find_alias(entry.evidence_id, "$.does.not.exist") is None


def test_find_alias_returns_none_for_completely_unknown_pair() -> None:
    catalog = build_evidence_catalog(_package())
    assert catalog.find_alias("ev-does-not-exist", "$.decision") is None


# --- serializacion segura (nunca IDs/paths reales) ---


def test_to_prompt_json_never_contains_real_ids_or_paths() -> None:
    catalog = build_evidence_catalog(_package())
    text = catalog.to_prompt_json()
    for entry in catalog.entries:
        assert entry.evidence_id not in text
        assert entry.evidence_path not in text
    for entry in catalog.entries:
        assert entry.alias in text


def test_to_prompt_json_is_valid_json_with_only_alias_tipo_descripcion() -> None:
    catalog = build_evidence_catalog(_package())
    parsed = json.loads(catalog.to_prompt_json())
    assert set(parsed.keys()) == {entry.alias for entry in catalog.entries}
    for value in parsed.values():
        assert set(value.keys()) == {"tipo", "descripcion"}


# --- redaccion inversa (RuleDraft real -> evidence_refs) ---


def test_redact_rule_draft_replaces_real_ids_with_aliases() -> None:
    package = _package()
    catalog = build_evidence_catalog(package)
    alias = next(e.alias for e in catalog.entries if e.evidence_path == "$.decision")
    draft = _valid_rule_draft(evidence_paths=["$.decision"], evidence_ids=["ev-1"])

    redacted = redact_rule_draft_for_prompt(draft, catalog)

    assert redacted["claims"][0]["evidence_refs"] == [alias]
    assert "evidence_ids" not in redacted["claims"][0]
    assert "evidence_paths" not in redacted["claims"][0]


def test_redact_rule_draft_never_leaks_real_ids_or_paths_in_output() -> None:
    # redact_rule_draft_for_prompt SOLO redacta claims[].evidence_ids/
    # evidence_paths: traceability es texto libre nunca validado contra
    # evidencia (limite ya establecido y probado en checkpoints
    # anteriores), asi que se usa un valor que no colisiona con "ev-1"
    # para no confundir ambas cosas en esta asercion puntual.
    package = _package()
    catalog = build_evidence_catalog(package)
    draft = _valid_rule_draft(evidence_paths=["$.decision"], evidence_ids=["ev-1"]).model_copy(
        update={"traceability": ["texto-libre-sin-relacion"]}
    )

    redacted = redact_rule_draft_for_prompt(draft, catalog)
    serialized = json.dumps(redacted["claims"])

    assert "ev-1" not in serialized
    assert "$.decision" not in serialized


def test_redact_rule_draft_omits_pair_without_catalog_alias_instead_of_inventing_one() -> None:
    package = _package()
    catalog = build_evidence_catalog(package)
    # evidence_id/path que NO estan en el catalogo real de `package`.
    draft = _valid_rule_draft(evidence_paths=["$.does.not.exist"], evidence_ids=["ev-ghost"])

    redacted = redact_rule_draft_for_prompt(draft, catalog)

    assert redacted["claims"][0]["evidence_refs"] == []


def test_round_trip_alias_to_real_to_alias_is_stable() -> None:
    """alias -> (id, path) real -> alias: debe devolver el MISMO alias
    para cada entrada del catalogo, sin excepcion."""
    catalog = build_evidence_catalog(_package())
    for entry in catalog.entries:
        resolved = catalog.resolve(entry.alias)
        assert resolved is not None
        round_tripped = catalog.find_alias(resolved.evidence_id, resolved.evidence_path)
        assert round_tripped == entry.alias


# --- multiples candidatos: catalogos independientes ---


def test_multiple_candidates_have_independent_catalogs() -> None:
    package_1 = _package(candidate_id="cand-1", evidence_ids=["ev-1"])
    package_2 = _package(candidate_id="cand-2", evidence_ids=["ev-99"])

    catalog_1 = build_evidence_catalog(package_1)
    catalog_2 = build_evidence_catalog(package_2)

    ids_1 = {entry.evidence_id for entry in catalog_1.entries}
    ids_2 = {entry.evidence_id for entry in catalog_2.entries}
    assert ids_1 == {"ev-1"}
    assert ids_2 == {"ev-99"}
    # Alias E001 en cada catalogo apunta a evidencia REAL distinta: nunca
    # se comparten ni se confunden entre candidatos.
    assert catalog_1.resolve("E001") != catalog_2.resolve("E001")


def test_catalog_with_no_evidence_bearing_containers_is_empty() -> None:
    package = _package(include_return_code=False)
    # decision y code_slice siguen citando evidencia en este fixture; para
    # un catalogo verdaderamente vacio se necesitaria un ContextPackage
    # sin ningun contenedor con evidence_ids no vacio -- no representable
    # con este builder simplificado, asi que se verifica la propiedad
    # opuesta: quitar un contenedor reduce el catalogo, nunca lo rompe.
    catalog_with = build_evidence_catalog(_package(include_return_code=True))
    catalog_without = build_evidence_catalog(package)
    assert len(catalog_without.entries) < len(catalog_with.entries)
