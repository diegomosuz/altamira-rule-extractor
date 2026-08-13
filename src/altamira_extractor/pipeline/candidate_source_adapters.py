"""Adaptadores PUROS de las tres fuentes de candidatos hacia
`UnifiedCandidateReference` (Fase 9 de la ampliacion semantica,
`feat/unified-candidate-promotion-assessment`).

Cada adaptador:

- recibe el artefacto original (o `None`, fuente no disponible) y NUNCA
  lo modifica;
- conserva `source_candidate_id` (el `candidate_id` real del origen,
  nunca uno inventado);
- conserva provenance (source_file/line, `source_references`,
  `evidence`/`propagation_fact_ids`/`semantic_effect_ids` segun la
  fuente) como texto sanitizado, nunca `source_text`;
- nunca reinterpreta `source_text` ni vuelve a evaluar una asignacion
  COBOL desde cero;
- nunca fabrica `decision_id`/`call_site_id`: si la fuente no lo expone
  (V1 nunca expone `call_site_id`; V1/V2 nunca exponen `input_literal`;
  Fase 8 nunca expone `decision_id` real, ver `docs/
  INTERPROCEDURAL_RULE_DETECTORS_SHADOW.md`), el campo queda `None`;
- nunca altera el soporte/status original (`original_support` es un
  passthrough textual, nunca normalizado a un enum compartido);
- nunca asigna una `UnifiedRuleFamily` sin un mapping EXPLICITO y
  demostrable -- ver `_V2_RULE_FAMILY_BY_TYPE`/
  `_INTERPROCEDURAL_RULE_FAMILY_BY_TYPE` para el razonamiento exacto de
  cada entrada.

`V2RuleType.STATE_CHANGE_RULE` (Fase 15B3-C8-FIX-1, corrigiendo la
version anterior de este modulo que lo mapeaba incondicionalmente a
`UNKNOWN`): `v2_detectors.detect_state_change` nunca exige un
`semantic_tag` de estado (Fase 7, "cualquier escritura deterministica de
un data item ordinario dentro de una decision"), asi que su equivalencia
ESTRUCTURAL con `STATE_TRANSITION` no es demostrable por si sola -- pero
`pipeline/enhanced_candidate_integration.py` (pipeline PRODUCTIVO) SI
promueve estos candidatos a `RuleCandidate(rule_family=STATE_TRANSITION)`
cuando el target tiene `semantic_tag in {status, status_flag}`. Replicar
ese MISMO criterio aqui (en vez de dejarlo permanentemente `UNKNOWN`) es
lo que permite que Fase 9/`functional-validate` mida DETECTION_CAPABILITY
consistente con lo que el pipeline realmente entrega.

`is_functional_state_transition_tag`/`_STATE_TRANSITION_SEMANTIC_TAGS`
(mas abajo) son la copia LOCAL de este modulo -- Fase 15B3-C8-FIX-1
(correccion de layering) revirtio la version anterior, donde
`enhanced_candidate_integration.py` (pipeline PRODUCTIVO) importaba este
gate DESDE aqui (tooling de QUALIFICATION/Fase 9): esa direccion de
dependencia estaba invertida, aunque el comportamiento fuera identico.
Ambas copias se mantienen en paridad exclusivamente via
`tests/pipeline/test_enhanced_candidate_integration.py::
test_state_transition_semantic_tags_match_qualification_adapter`, nunca
via una importacion compartida. `adapt_v2_candidates` recibe
`semantic_tag_by_data_item` (Mapping puro, nunca Neo4j/filesystem: el
llamador ya lo resolvio) y nunca infiere el tag desde el nombre de la
variable/condition/candidate_id. Sin ese mapping (parametro ausente o
`None`), el comportamiento es idéntico al anterior: siempre `UNKNOWN`,
nunca se adivina."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from ..contracts.candidate import CandidateArtifact
from ..contracts.candidate_promotion_assessment import (
    CandidateSource,
    UnifiedCandidateReference,
    UnifiedRuleFamily,
)
from ..contracts.interprocedural_rule_candidates import (
    InterproceduralRuleCandidatesArtifact,
    InterproceduralRuleType,
)
from ..contracts.v2_shadow_candidates import (
    V2CandidateSupport,
    V2RuleType,
    V2ShadowCandidate,
    V2ShadowCandidatesArtifact,
)

# Q0 (`queries/v1/q0_candidates.cypher`) exige estructuralmente
# `sink.semantic_tag = 'return_code'` en su unico MATCH -- toda fila que
# Q0 devuelve, por construccion de la consulta, describe una asignacion
# de codigo de retorno. `RuleCandidate.rule_type` (poblado desde
# `Decision.rule_type`, ver `pipeline/semantic_graph_builder.py`) es
# SIEMPRE `None` en la practica (ninguna etapa lo deriva todavia, ni
# distingue una Decision IF/MOVE de una Decision de nivel 88 SET-TO-TRUE
# -- ambas producen `rule_type=None`), asi que la familia normalizada de
# V1 se demuestra por la GARANTIA ESTRUCTURAL de la consulta Q0, nunca
# por el campo `rule_type` (que nunca se lee para este proposito, se
# preserva intacto en `original_rule_type`).
_V1_RULE_FAMILY = UnifiedRuleFamily.RETURN_CODE

# `V2RuleType.STATE_CHANGE_RULE` es intraprograma, SIEMPRE
# `support=PARTIAL` (nunca DETERMINISTIC) segun `docs/
# V2_DETECTORS_SHADOW_MODE.md` -- por si solo, "cualquier escritura
# deterministica de un data item ordinario dentro de una decision, sin
# importar relevancia funcional" no es equivalente a `STATE_TRANSITION`.
# `_map_state_change_rule_family` (mas abajo) aplica el MISMO gate de
# relevancia funcional que `enhanced_candidate_integration.py` ya usa en
# el pipeline productivo (`semantic_tag in {status, status_flag}`) antes
# de exponer `STATE_TRANSITION` -- ver docstring del modulo. Sin ese gate
# (mapping ausente en este dict base), el fallback via `.get(...,
# UNKNOWN)` sigue siendo `UNKNOWN`, igual que antes.
#
# `InterproceduralRuleType.STATE_TRANSITION_RULE` es un concepto
# DISTINTO (interprocedural, `DETERMINISTIC` unicamente cuando el target
# tiene ese mismo semantic_tag) -- no se fusiona con este mapping, cada
# fuente conserva su propio adaptador.
_STATE_TRANSITION_SEMANTIC_TAGS = frozenset({"status", "status_flag"})

_V2_RULE_FAMILY_BY_TYPE: dict[V2RuleType, UnifiedRuleFamily] = {
    V2RuleType.RETURN_CODE_RULE: UnifiedRuleFamily.RETURN_CODE,
    V2RuleType.LEVEL_88_RETURN_CODE_RULE: UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
    V2RuleType.CALCULATION_RULE: UnifiedRuleFamily.CALCULATION,
}


def is_functional_state_transition_tag(tag: str | None) -> bool:
    """Unica fuente de verdad de "¿es este semantic_tag un target de
    estado funcionalmente relevante?" -- reutilizada tal cual por
    `enhanced_candidate_integration.py` (pipeline productivo) y por
    `adapt_v2_candidates` (Fase 9/qualification) para que ambos caminos
    apliquen EXACTAMENTE el mismo criterio. `tag=None` (DataItem sin
    semantic_tag, o lookup no disponible) siempre `False`, nunca se
    asume relevancia por ausencia de informacion."""
    return tag in _STATE_TRANSITION_SEMANTIC_TAGS

_INTERPROCEDURAL_RULE_FAMILY_BY_TYPE: dict[InterproceduralRuleType, UnifiedRuleFamily] = {
    InterproceduralRuleType.RETURN_CODE_RULE: UnifiedRuleFamily.RETURN_CODE,
    InterproceduralRuleType.BY_REFERENCE_RULE: UnifiedRuleFamily.BY_REFERENCE_OUTPUT,
    InterproceduralRuleType.STATE_TRANSITION_RULE: UnifiedRuleFamily.STATE_TRANSITION,
}


def _digest(*parts: str) -> str:
    canonical = "\x1f".join(parts)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def unified_reference_id_for(*, source: CandidateSource, source_candidate_id: str) -> str:
    """Unica funcion que genera `unified_reference_id` -- determinista
    (SHA-256, nunca UUID/timestamp/`hash()` de Python), reutilizada por
    los tres adaptadores y por los tests para verificar determinismo sin
    duplicar la formula."""
    return f"unified::{source.value.lower()}::{_digest(source.value, source_candidate_id)}"


def _program_name_from_paragraph_id(paragraph_id: str) -> str | None:
    """Extrae el `program_name` real de un `paragraph_id`/`decision_id`
    Neo4j-shaped por POSICION textual fija -- nunca recalculando el ID
    (formato: `program::{country}::{logical_name}::{program_name}::
    {version}::{hash12}::paragraph::{NAME}[...]`, ver
    `pipeline/identifiers.py::ProgramIdentity.program_id`). `None` si el
    formato no coincide (nunca se adivina). Mismo helper que
    `pipeline/interprocedural_rule_comparator.py::
    _program_name_from_paragraph_id` -- duplicado deliberadamente (nunca
    se extrae a `identifiers.py`, modulo compartido fuera de alcance de
    esta fase) en vez de compartido, igual que el precedente de Fase 8."""
    parts = paragraph_id.split("::")
    if len(parts) < 4 or parts[0] != "program":
        return None
    return parts[3]


def _sorted_unique(values: Sequence[str]) -> list[str]:
    return sorted(set(values))


def adapt_v1_candidates(
    artifact: CandidateArtifact | None, *, source_artifact_hash: str
) -> list[UnifiedCandidateReference]:
    """`artifact is None` (fuente no disponible) siempre produce `[]` --
    nunca fabrica una referencia sintetica. Nunca modifica `artifact`."""
    if artifact is None:
        return []
    references = [
        UnifiedCandidateReference(
            unified_reference_id=unified_reference_id_for(
                source=CandidateSource.V1, source_candidate_id=candidate.candidate_id
            ),
            source=CandidateSource.V1,
            source_candidate_id=candidate.candidate_id,
            source_artifact_hash=source_artifact_hash,
            rule_family=_V1_RULE_FAMILY,
            original_rule_type=candidate.rule_type,
            original_support=candidate.status.value,
            program=_program_name_from_paragraph_id(candidate.paragraph_id),
            paragraph=candidate.paragraph_name,
            decision_id=candidate.decision_id,
            call_site_id=None,
            target=None,
            input_literal=None,
            output_literal=candidate.outcome_code,
            evidence_ids=[],
            barrier_codes=[],
            provenance_references=_sorted_unique(
                [f"{candidate.source_file}::{candidate.line_start}"]
            ),
            diagnostics=[],
        )
        for candidate in artifact.candidates
    ]
    return sorted(references, key=lambda reference: reference.unified_reference_id)


def adapt_productive_candidates(
    artifact: CandidateArtifact | None, *, source_artifact_hash: str
) -> list[UnifiedCandidateReference]:
    """Fase 15B4-CANDIDATE-QUALITY-5C (cierre de
    P1-PRODUCTIVE-ARTIFACT-QUALIFICATION): adapta el `CandidateArtifact`
    REAL de `artifacts/06-candidates.json` -- a diferencia de
    `adapt_v1_candidates` (que asume, correctamente para su epoca de
    origen previa a la integracion productiva V2, que TODO candidato en
    ese artefacto es V1/RETURN_CODE), este adaptador lee
    `candidate.candidate_source`/`candidate.rule_family` REALES de cada
    `RuleCandidate` -- necesario porque, desde `enhanced_candidate_
    integration.py` (Fase 15B3-B en adelante), `06-candidates.json`
    puede contener candidatos V1 Y V2 (LEVEL_88_RETURN_CODE/
    STATE_TRANSITION/CALCULATION) fusionados en el MISMO artefacto real.
    `artifact is None` (fuente no disponible) siempre produce `[]`.
    Nunca modifica `artifact`. Nunca recalcula nada: esta es la UNICA
    fuente de candidatos para la qualification productiva -- ver
    `functional_validation_service.py::compute_functional_validation_
    report`."""
    if artifact is None:
        return []
    references = [
        UnifiedCandidateReference(
            unified_reference_id=unified_reference_id_for(
                source=candidate.candidate_source, source_candidate_id=candidate.candidate_id
            ),
            source=candidate.candidate_source,
            source_candidate_id=candidate.candidate_id,
            source_artifact_hash=source_artifact_hash,
            rule_family=candidate.rule_family,
            original_rule_type=candidate.rule_type,
            original_support=candidate.status.value,
            program=_program_name_from_paragraph_id(candidate.paragraph_id),
            paragraph=candidate.paragraph_name,
            decision_id=candidate.decision_id,
            call_site_id=None,
            target=None,
            input_literal=None,
            output_literal=candidate.outcome_code,
            evidence_ids=_sorted_unique(candidate.evidence_ids),
            barrier_codes=[],
            provenance_references=_sorted_unique(
                [f"{candidate.source_file}::{candidate.line_start}"]
            ),
            diagnostics=[],
        )
        for candidate in artifact.candidates
    ]
    return sorted(references, key=lambda reference: reference.unified_reference_id)


def _v2_rule_family(
    candidate: V2ShadowCandidate,
    *,
    semantic_tag_by_data_item: Mapping[tuple[str, str], str] | None,
) -> UnifiedRuleFamily:
    """Familia unificada de un `V2ShadowCandidate` individual --
    aislado de `_V2_RULE_FAMILY_BY_TYPE` (mapping estatico) unicamente
    para `STATE_CHANGE_RULE`, que exige ademas el gate de
    `is_functional_state_transition_tag` sobre el semantic_tag REAL del
    target (nunca inferido). `semantic_tag_by_data_item` ausente/`None`
    (llamador no lo resolvio, p. ej. `04-semantic-graph.json` no
    disponible) se trata igual que un tag ausente -- nunca `UNKNOWN`
    silenciosamente reinterpretado como relevante."""
    if candidate.rule_type != V2RuleType.STATE_CHANGE_RULE:
        return _V2_RULE_FAMILY_BY_TYPE.get(candidate.rule_type, UnifiedRuleFamily.UNKNOWN)
    if candidate.target_qualified_name is None or semantic_tag_by_data_item is None:
        tag = None
    else:
        tag = semantic_tag_by_data_item.get((candidate.program, candidate.target_qualified_name))
    return (
        UnifiedRuleFamily.STATE_TRANSITION
        if is_functional_state_transition_tag(tag)
        else UnifiedRuleFamily.UNKNOWN
    )


def adapt_v2_candidates(
    artifact: V2ShadowCandidatesArtifact | None,
    *,
    source_artifact_hash: str,
    semantic_tag_by_data_item: Mapping[tuple[str, str], str] | None = None,
) -> list[UnifiedCandidateReference]:
    """`artifact is None` (fuente no disponible) siempre produce `[]`.
    Nunca modifica `artifact`. `diagnostic_codes` (vocabulario propio de
    V2, cadenas libres) se preserva como `barrier_codes` UNICAMENTE
    cuando `support=BLOCKED` (misma semantica que V1/interprocedural:
    barrier = motivo de bloqueo); en cualquier otro soporte,
    `diagnostic_codes` es una nota de calidad/relevancia -- se preserva
    en `diagnostics`, nunca se descarta ni se reinterpreta como
    barrera. `semantic_tag_by_data_item` (Mapping `(program,
    qualified_name) -> semantic_tag`, PURO -- este adaptador nunca toca
    filesystem/Neo4j) alimenta unicamente el gate de
    `STATE_CHANGE_RULE`; ver `_v2_rule_family`."""
    if artifact is None:
        return []
    references: list[UnifiedCandidateReference] = []
    for execution in artifact.executions:
        for candidate in execution.candidates:
            target = candidate.target_qualified_name or candidate.target_variable
            evidence_ids = _sorted_unique(
                [*candidate.semantic_effect_ids, *candidate.propagation_fact_ids]
            )
            provenance = _sorted_unique(
                [
                    f"{ref.program}::{ref.paragraph}::{ref.statement_id or ''}"
                    for ref in candidate.source_references
                ]
            )
            is_blocked = candidate.support == V2CandidateSupport.BLOCKED
            references.append(
                UnifiedCandidateReference(
                    unified_reference_id=unified_reference_id_for(
                        source=CandidateSource.V2, source_candidate_id=candidate.candidate_id
                    ),
                    source=CandidateSource.V2,
                    source_candidate_id=candidate.candidate_id,
                    source_artifact_hash=source_artifact_hash,
                    rule_family=_v2_rule_family(
                        candidate, semantic_tag_by_data_item=semantic_tag_by_data_item
                    ),
                    original_rule_type=candidate.rule_type.value,
                    original_support=candidate.support.value,
                    program=candidate.program,
                    paragraph=candidate.paragraph,
                    decision_id=candidate.decision_id,
                    call_site_id=None,
                    target=target,
                    input_literal=None,
                    output_literal=candidate.resolved_literal,
                    evidence_ids=evidence_ids,
                    barrier_codes=_sorted_unique(candidate.diagnostic_codes) if is_blocked else [],
                    provenance_references=provenance,
                    diagnostics=[] if is_blocked else _sorted_unique(candidate.diagnostic_codes),
                )
            )
    return sorted(references, key=lambda reference: reference.unified_reference_id)


def adapt_interprocedural_candidates(
    artifact: InterproceduralRuleCandidatesArtifact | None, *, source_artifact_hash: str
) -> list[UnifiedCandidateReference]:
    """`artifact is None` (fuente no disponible) siempre produce `[]`.
    Nunca modifica `artifact`. `program` usa `caller_program` (decision
    de diseno documentada: el candidato "pertenece" al programa que
    origina el `CALL`, `callee_program` queda preservado via
    `provenance_references`, nunca se agrega un segundo campo `program`
    no solicitado por el contrato)."""
    if artifact is None:
        return []
    references: list[UnifiedCandidateReference] = []
    for candidate in artifact.candidates:
        evidence_ids = _sorted_unique([evidence.evidence_id for evidence in candidate.evidence])
        provenance = _sorted_unique(
            [
                f"{ref.program}::{ref.paragraph or ''}::{ref.statement_id or ''}"
                for evidence in candidate.evidence
                for ref in evidence.source_references
            ]
            + (
                [f"callee_program::{candidate.callee_program}"]
                if candidate.callee_program is not None
                else []
            )
        )
        references.append(
            UnifiedCandidateReference(
                unified_reference_id=unified_reference_id_for(
                    source=CandidateSource.INTERPROCEDURAL,
                    source_candidate_id=candidate.candidate_id,
                ),
                source=CandidateSource.INTERPROCEDURAL,
                source_candidate_id=candidate.candidate_id,
                source_artifact_hash=source_artifact_hash,
                rule_family=_INTERPROCEDURAL_RULE_FAMILY_BY_TYPE.get(
                    candidate.rule_type, UnifiedRuleFamily.UNKNOWN
                ),
                original_rule_type=candidate.rule_type.value,
                original_support=candidate.support.value,
                program=candidate.caller_program,
                paragraph=candidate.caller_paragraph,
                decision_id=candidate.decision_id,
                call_site_id=candidate.call_site_id,
                target=candidate.target,
                input_literal=candidate.input_literal,
                output_literal=candidate.output_literal,
                evidence_ids=evidence_ids,
                barrier_codes=_sorted_unique([barrier.value for barrier in candidate.barriers]),
                provenance_references=provenance,
                diagnostics=_sorted_unique(candidate.diagnostics),
            )
        )
    return sorted(references, key=lambda reference: reference.unified_reference_id)
