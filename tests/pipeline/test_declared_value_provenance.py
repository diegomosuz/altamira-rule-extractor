"""Tests unitarios del enriquecimiento evidencial declared_value (Fase
15B3-C5-B): puros, sin Neo4j, sin filesystem, sin JAR."""

from __future__ import annotations

from altamira_extractor.contracts.candidate import RuleCandidate
from altamira_extractor.contracts.canonical import CanonicalDataItem
from altamira_extractor.contracts.context_package import ContextPackageDecision, EvidenceEntry
from altamira_extractor.contracts.enums import LocationKind
from altamira_extractor.pipeline.context_package_builder import (
    _declared_value_evidence_id,
    _enrich_decision_with_declared_value_evidence,
)
from altamira_extractor.pipeline.identifiers import build_symbol_table

_HASH = "a" * 64
_SOURCE_FILE = "01-codigo/cobol/PROG.cbl"
_PROGRAM_NAME = "PROG1"


def _candidate(**overrides: object) -> RuleCandidate:
    defaults: dict[str, object] = {
        "candidate_id": "candidate::v1::1",
        "paragraph_id": "prog::paragraph::MAIN-PARA",
        "paragraph_name": "MAIN-PARA",
        "decision_id": "prog::paragraph::MAIN-PARA::decision::10::1",
        "detector_id": "q0-return-code-decision",
        "detector_version": "1.0",
        "detector_score": 1.0,
        "condition": "WS-RIESGO > WS-RIESGO-MAXIMO",
        "outcome_code": "PE04",
        "line_start": 10,
        "source_file": _SOURCE_FILE,
        "source_package_hash": _HASH,
    }
    defaults.update(overrides)
    return RuleCandidate(**defaults)  # type: ignore[arg-type]


def _decision(**overrides: object) -> ContextPackageDecision:
    defaults: dict[str, object] = {
        "expression": "WS-RIESGO > WS-RIESGO-MAXIMO",
        "normalized_expression": "WS-RIESGO > WS-RIESGO-MAXIMO",
        "operands": ["WS-RIESGO", "WS-RIESGO-MAXIMO"],
        "outcome_code": "PE04",
    }
    defaults.update(overrides)
    return ContextPackageDecision(**defaults)  # type: ignore[arg-type]


def _data_item(**overrides: object) -> CanonicalDataItem:
    defaults: dict[str, object] = {
        "name": "WS-RIESGO-MAXIMO",
        "qualified_name": "WS-RIESGO-MAXIMO",
        "level": 1,
        "pic": "9",
        "declared_value": "3",
        "source_file": _SOURCE_FILE,
        "line": 3,
        "location_kind": LocationKind.EXACT,
    }
    defaults.update(overrides)
    return CanonicalDataItem(**defaults)  # type: ignore[arg-type]


def test_unambiguous_operand_with_declared_value_adds_evidence() -> None:
    decision = _decision()
    symbol_table = build_symbol_table([_data_item()])
    enriched, evidence = _enrich_decision_with_declared_value_evidence(
        decision, [], _candidate(), _PROGRAM_NAME, symbol_table
    )
    assert len(evidence) == 1
    entry = evidence[0]
    assert entry.kind == "declared_value_context"
    # (D) La evidencia cita la ubicacion REAL del CanonicalDataItem, nunca
    # candidate.source_file como fallback -- aqui coinciden por diseno del
    # fixture, pero el campo consultado debe ser data_item.source_file/line.
    assert entry.source_file == _SOURCE_FILE
    assert entry.line_start == 3
    assert entry.line_end == 3
    assert entry.details is not None
    assert entry.details["data_item_qualified_name"] == "WS-RIESGO-MAXIMO"
    assert entry.details["declared_value"] == "3"
    assert entry.details["value_semantics"] == "DECLARED_INITIAL_VALUE"
    assert entry.details["effective_runtime_value_proven"] is False
    assert entry.details["program_name"] == _PROGRAM_NAME
    assert entry.evidence_id in enriched.evidence_ids
    # Condition/expression nunca se sustituyen por el valor declarado.
    assert enriched.expression == "WS-RIESGO > WS-RIESGO-MAXIMO"
    assert enriched.operands == ["WS-RIESGO", "WS-RIESGO-MAXIMO"]


def test_operand_without_value_clause_adds_no_evidence() -> None:
    decision = _decision()
    # WS-RIESGO no tiene declared_value (VALUE 1 declarado, pero se omite
    # aqui para probar el caso "DataItem sin declared_value").
    other = _data_item(name="WS-RIESGO", qualified_name="WS-RIESGO", declared_value=None)
    symbol_table = build_symbol_table([_data_item(), other])
    enriched, evidence = _enrich_decision_with_declared_value_evidence(
        decision, [], _candidate(), _PROGRAM_NAME, symbol_table
    )
    assert len(evidence) == 1  # unicamente WS-RIESGO-MAXIMO aporta evidencia.
    assert evidence[0].details is not None
    assert evidence[0].details["data_item_qualified_name"] == "WS-RIESGO-MAXIMO"


def test_no_value_clause_at_all_adds_no_evidence() -> None:
    decision = _decision()
    symbol_table = build_symbol_table([_data_item(declared_value=None)])
    enriched, evidence = _enrich_decision_with_declared_value_evidence(
        decision, [], _candidate(), _PROGRAM_NAME, symbol_table
    )
    assert evidence == []
    assert enriched.evidence_ids == []


def test_ambiguous_simple_name_adds_no_evidence() -> None:
    """Mismo simple name bajo dos qualified_name distintos: SymbolTable.resolve
    marca ambiguous=True -- preferir falso negativo, nunca elegir arbitrariamente."""
    decision = _decision(operands=["WS-RIESGO-MAXIMO"])
    symbol_table = build_symbol_table(
        [
            _data_item(qualified_name="WS-GRUPO-A.WS-RIESGO-MAXIMO"),
            _data_item(qualified_name="WS-GRUPO-B.WS-RIESGO-MAXIMO"),
        ]
    )
    enriched, evidence = _enrich_decision_with_declared_value_evidence(
        decision, [], _candidate(), _PROGRAM_NAME, symbol_table
    )
    assert evidence == []
    assert enriched.evidence_ids == []


def test_symbol_table_none_adds_no_evidence() -> None:
    decision = _decision()
    enriched, evidence = _enrich_decision_with_declared_value_evidence(
        decision, [], _candidate(), _PROGRAM_NAME, None
    )
    assert evidence == []
    assert enriched is decision


def test_program_name_none_adds_no_evidence() -> None:
    """Sin program_name (indice canonico ausente/incompleto) no se fabrica
    identidad de evidencia: se omite por completo, nunca con un
    program_name sintetico/vacio."""
    decision = _decision()
    symbol_table = build_symbol_table([_data_item()])
    enriched, evidence = _enrich_decision_with_declared_value_evidence(
        decision, [], _candidate(), None, symbol_table
    )
    assert evidence == []
    assert enriched is decision


def test_unknown_source_location_adds_no_evidence() -> None:
    """(C) Caso obligatorio (correccion pre-commit "no fabricar source
    location"): un CanonicalDataItem con declared_value pero sin
    ubicacion fuente confiable (location_kind != EXACT, source_file=None)
    nunca produce declared_value_context -- falso negativo aceptable,
    nunca una ubicacion fabricada a partir de candidate.source_file."""
    decision = _decision()
    unreliable = _data_item(source_file=None, line=None, location_kind=LocationKind.UNKNOWN)
    symbol_table = build_symbol_table([unreliable])
    enriched, evidence = _enrich_decision_with_declared_value_evidence(
        decision, [], _candidate(), _PROGRAM_NAME, symbol_table
    )
    assert evidence == []
    assert enriched.evidence_ids == []
    assert enriched is decision
    # El CanonicalDataItem en si mismo conserva declared_value intacto --
    # solo se omite la EVIDENCIA CITABLE, nunca el dato canonico.
    assert unreliable.declared_value == "3"


def test_preprocessed_stream_location_adds_no_evidence() -> None:
    """Mismo caso que arriba, para PREPROCESSED_STREAM (COPY): source_file
    es None por contrato para este location_kind, nunca sustituido por
    candidate.source_file."""
    decision = _decision()
    from_copy = _data_item(source_file=None, line=5, location_kind=LocationKind.PREPROCESSED_STREAM)
    symbol_table = build_symbol_table([from_copy])
    enriched, evidence = _enrich_decision_with_declared_value_evidence(
        decision, [], _candidate(), _PROGRAM_NAME, symbol_table
    )
    assert evidence == []


def test_mutation_after_declaration_never_invalidates_declared_value_evidence() -> None:
    """Caso obligatorio (Fase 15B3-C5-B, seccion 23): una escritura
    posterior (MOVE/SET/COMPUTE/...) NUNCA invalida declared_value -- C5-B
    no afirma PROVEN_CONSTANT_VALUE, solo DECLARED_INITIAL_VALUE. El
    CanonicalDataItem sigue reportando su VALUE declarado sin importar
    cuantas veces se reescriba el DataItem en PROCEDURE DIVISION (esta
    funcion nunca inspecciona variables_written de ningun statement)."""
    decision = _decision(
        expression="MONTO > WS-LIMITE",
        normalized_expression="MONTO > WS-LIMITE",
        operands=["MONTO", "WS-LIMITE"],
    )
    data_item = _data_item(name="WS-LIMITE", qualified_name="WS-LIMITE", declared_value="1000")
    symbol_table = build_symbol_table([data_item])
    enriched, evidence = _enrich_decision_with_declared_value_evidence(
        decision, [], _candidate(condition="MONTO > WS-LIMITE"), _PROGRAM_NAME, symbol_table
    )
    assert len(evidence) == 1
    assert evidence[0].details is not None
    assert evidence[0].details["declared_value"] == "1000"
    assert evidence[0].details["effective_runtime_value_proven"] is False
    # La condition original permanece intacta: nunca "MONTO > 1000".
    assert enriched.expression == "MONTO > WS-LIMITE"


def test_same_data_item_referenced_by_two_decisions_shares_evidence_id() -> None:
    """(A) Misma declaracion, dos Decisions distintas -> mismo evidence_id."""
    symbol_table = build_symbol_table([_data_item()])
    candidate = _candidate()
    _, evidence_1 = _enrich_decision_with_declared_value_evidence(
        _decision(), [], candidate, _PROGRAM_NAME, symbol_table
    )
    _, evidence_2 = _enrich_decision_with_declared_value_evidence(
        _decision(
            expression="WS-RIESGO-MAXIMO < 9",
            normalized_expression="WS-RIESGO-MAXIMO < 9",
            operands=["WS-RIESGO-MAXIMO"],
        ),
        [],
        _candidate(decision_id="prog::paragraph::MAIN-PARA::decision::20::2"),
        _PROGRAM_NAME,
        symbol_table,
    )
    assert evidence_1[0].evidence_id == evidence_2[0].evidence_id


def test_same_data_item_name_different_programs_produces_different_evidence_ids() -> None:
    """(B) Caso obligatorio (correccion pre-commit, seccion 8): dos
    programas distintos declaran el mismo simple/qualified_name local
    (WS-LIMITE) -- incluso con el MISMO source_file (escenario sintetico
    deliberado para probar que la identidad NUNCA depende solo de
    source_file), los evidence_id deben ser distintos porque program_name
    difiere."""
    decision = _decision(
        expression="X > WS-LIMITE",
        normalized_expression="X > WS-LIMITE",
        operands=["WS-LIMITE"],
    )
    data_item = _data_item(name="WS-LIMITE", qualified_name="WS-LIMITE", declared_value="10")
    symbol_table = build_symbol_table([data_item])

    _, evidence_a = _enrich_decision_with_declared_value_evidence(
        decision, [], _candidate(), "PROGRAM-A", symbol_table
    )
    _, evidence_b = _enrich_decision_with_declared_value_evidence(
        decision, [], _candidate(), "PROGRAM-B", symbol_table
    )
    assert len(evidence_a) == 1
    assert len(evidence_b) == 1
    assert evidence_a[0].evidence_id != evidence_b[0].evidence_id


def test_declared_value_evidence_id_is_deterministic_and_candidate_independent() -> None:
    """(E) Todos los componentes identicos -> mismo ID deterministicamente."""

    def _id(**overrides: object) -> str:
        defaults: dict[str, object] = {
            "source_package_hash": _HASH,
            "program_name": _PROGRAM_NAME,
            "source_file": _SOURCE_FILE,
            "line": 3,
            "qualified_name": "WS-RIESGO-MAXIMO",
        }
        defaults.update(overrides)
        return _declared_value_evidence_id(**defaults)  # type: ignore[arg-type]

    id_1 = _id()
    id_2 = _id()
    assert id_1 == id_2
    assert id_1.startswith("evidence::")
    # Distinto qualified_name -> ID distinto.
    assert id_1 != _id(qualified_name="WS-OTRO")
    # (B) Distinto program_name (mismo qualified_name/source_file/line) -> ID distinto.
    assert id_1 != _id(program_name="OTHER-PROGRAM")
    # (C) Mismo source_package_hash/program_name/qualified_name, distinto
    # source_file -> ID distinto (dos declaraciones fisicas distintas).
    assert id_1 != _id(source_file="01-codigo/cobol/OTHER.cbl")
    # (D) Mismo source_package_hash/program_name/source_file/qualified_name,
    # distinta line -> ID distinto (misma "declaracion por nombre" pero
    # fisicamente otra declaracion).
    assert id_1 != _id(line=99)


def test_evidence_entry_kind_is_reused_type() -> None:
    """No se crea una clase nueva: declared_value_context es un
    EvidenceEntry ordinario."""
    symbol_table = build_symbol_table([_data_item()])
    _, evidence = _enrich_decision_with_declared_value_evidence(
        _decision(), [], _candidate(), _PROGRAM_NAME, symbol_table
    )
    assert isinstance(evidence[0], EvidenceEntry)
