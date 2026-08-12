"""Integracion minima de los detectores V2 en el flujo productivo de
CANDIDATES_DETECTED (Fase 15B3-B, "realineacion minima del motor de
extraccion de reglas"; Fase 15B3-B1, "cierre correctivo"; Fase 15B3-C1,
"generalizacion de reglas de decision, estado y flags").

Alcance deliberado: `V2RuleType.RETURN_CODE_RULE`,
`V2RuleType.LEVEL_88_RETURN_CODE_RULE` (con `support=DETERMINISTIC`) y,
desde Fase 15B3-C1, `V2RuleType.STATE_CHANGE_RULE` (con
`support=PARTIAL`, su unico valor posible -- ver mas abajo) -- las tres
familias V2 cuya evidencia esta anclada a una `Decision` (misma
granularidad que Q0/V1) y por lo tanto son compatibles, sin modificar
`context_package_builder.py`, con las queries Q4/Q5a (que exigen un
`Decision` real con ese `decision_id`).

## `V2_STATE_CHANGE` -> `STATE_TRANSITION` (Fase 15B3-C1)

`v2_detectors.detect_state_change` (sin modificar) NUNCA produce
`support=DETERMINISTIC`: por diseno deliberado de Fase 7, siempre
`support=PARTIAL` con `detector_score=STATE_CHANGE_DETECTOR_SCORE`
(constante `0.7`), independientemente de que el valor este completamente
demostrado -- la funcion NUNCA produce un candidato `BLOCKED` (filtra
`fact.fact_kind not in _LITERAL_FACT_KINDS or fact.literal is None`
ANTES de construir cualquier candidato), asi que todo `V2ShadowCandidate`
que devuelve ya tiene un literal estructuralmente probado. `PARTIAL` en
este detector especifico describe EXCLUSIVAMENTE "relevancia funcional
no evaluada" -- nunca "valor no probado" (a diferencia de `BLOCKED` en
`V2_RETURN_CODE_PROPAGATION`/`V2_LEVEL_88_RETURN_CODE`, donde SI existe
incertidumbre real sobre el valor).

La modificacion minima (auditoria 15B3-C1) es evaluar esa relevancia
funcional AQUI, en memoria, DESPUES de que `detect_state_change` ya
produjo su resultado -- reutilizando integramente
`v2_detectors._data_item_semantic_tag` (mismo mecanismo que ya usa
`V2_LEVEL_88_RETURN_CODE` para su propio target) contra el semantic_tag
YA asignado por `SemanticTagger`/`config/semantic-tags.yml` (nunca LLM,
NLP, embeddings ni heuristica de texto libre). Solo se promueve cuando
el target tiene `semantic_tag in {status, status_flag}`
(`_STATE_TRANSITION_SEMANTIC_TAGS`) -- ver `_convert_v2_candidate`. Un
target sin esa relevancia demostrada permanece invisible fuera de
`diagnostics/v2-candidates-shadow.json`, exactamente igual que antes de
esta fase: `detect_state_change` en si mismo NO se modifica, su
artefacto diagnostico NO cambia, `docs/V2_DETECTORS_SHADOW_MODE.md`
("V2_STATE_CHANGE nunca garantiza relevancia funcional") sigue siendo
cierto tal cual esta escrito -- lo que cambia es que ESTE modulo, aparte
de ese artefacto, ahora sabe reconocer los casos donde la relevancia SI
esta demostrada.

`rule_family=STATE_TRANSITION` aqui usa el MISMO criterio que el
adaptador de Fase 9 (`candidate_source_adapters.adapt_v2_candidates` /
`is_functional_state_transition_tag`) -- `semantic_tag in {status,
status_flag}` -- pero DELIBERADAMENTE como dos copias independientes,
nunca una importada desde la otra (Fase 15B3-C8-FIX-1, correccion de
layering: este modulo es pipeline PRODUCTIVO, `candidate_source_
adapters.py` es tooling de QUALIFICATION/Fase 9 -- el productivo nunca
depende de qualification, aunque ambos deban seguir la misma regla de
negocio). `_STATE_TRANSITION_SEMANTIC_TAGS` (mas abajo) es la copia
LOCAL de este modulo; la equivalencia con la copia de
`candidate_source_adapters.py` esta garantizada por
`tests/pipeline/test_enhanced_candidate_integration.py::
test_state_transition_semantic_tags_match_qualification_adapter` (test
de paridad explicito, nunca una importacion cruzada). `_LOCAL_RULE_
FAMILY_BY_TYPE` sigue siendo un dict local (este modulo ademas resuelve
`decision_id`/`condition` reales contra el grafo, cosa que el adaptador
de Fase 9 no hace).

Deliberadamente fuera de alcance: `InterproceduralRuleType.
BY_REFERENCE_RULE` (anclado a un `call_site_id`, no a una `Decision`,
ver informe 15B3-B2-A).

Reutiliza sin modificar: `v2_detector_context.build_v2_detector_context`,
`v2_detectors.detect_return_code_propagation`/
`detect_level_88_return_code`/`detect_state_change`/
`_data_item_semantic_tag`, `semantic_effects_analyzer.
analyze_semantic_effects`, `semantic_propagation_analyzer.
analyze_semantic_propagation`. Nunca persiste
`diagnostics/v2-candidates-shadow.json` ni ningun otro artefacto
diagnostico.

## Identidad funcional (Fase 15B3-B1, corrige el uso de `decision_id`
## como identidad completa)

`functional_identity_key` es la UNICA fuente de identidad para decidir
si dos candidatos (de cualquier fuente) describen "la misma regla":
`(program_id, paragraph_id, decision_id, condition, effect,
rule_family)`. `decision_id` solo no basta: dos candidatos sobre la
MISMA decision pueden describir efectos o familias distintas (ver
reglas B/C mas abajo), y ambos son reglas legitimamente distintas.

Reglas (probadas en `tests/pipeline/test_enhanced_candidate_integration.py`):

A. Misma decision + misma condicion + mismo efecto + misma familia ->
   una sola regla (se fusiona evidencia, ver `_merge_group`).
B. Misma decision + efecto diferente -> reglas distintas.
C. Misma decision + familia diferente -> reglas distintas.
D. V1 y V2 describen la misma regla (mismo key) -> una sola regla en el
   artefacto de salida: se reutiliza el `RuleCandidate` V1 completo via
   `model_copy` (nunca se muta el objeto original en su lugar) con
   `evidence_ids` fusionados; `candidate_id`/`decision_id`/`condition`/
   `outcome_code`/`detector_id` original de V1 permanecen intactos.
   Los `detector_id` de las fuentes V2 que corroboran se registran en
   un warning (el contrato `RuleCandidate.detector_id: str` es un unico
   valor, no admite una lista -- "conservar cuando el contrato lo
   permita" se cumple documentando la corroboracion sin forzar el tipo).
E. El orden de entrada (orden de ejecucion de detectores, orden de
   candidatos V1) nunca afecta `candidate_id`, el orden final, la
   evidencia unida ni los warnings: toda estructura intermedia se
   normaliza via `sorted()`/`set()` antes de construir el resultado
   (ver `_merge_candidates`).

## CALCULATION incondicional (Fase 15B3-C2-B2)

`V2RuleType.CALCULATION_RULE` con `decision_id=None` (calculo aritmetico
SIN `IF`/`EVALUATE` envolvente, ver `v2_detectors.detect_calculation`)
se productiviza via `_convert_unconditional_calculation`, un camino
DELIBERADAMENTE separado de `_convert_v2_candidate`: nunca consulta un
nodo Decision (no existe), nunca fabrica un `decision_id`/`condition`
sintetico -- el `RuleCandidate` resultante tiene ambos en `None`
("ser incondicional" se deriva EXCLUSIVAMENTE de esa ausencia, nunca de
un texto como `"TRUE"`/`"UNCONDITIONAL"`). Su identidad funcional usa
`functional_identity_key_for_unconditional_calculation` (NO
`functional_identity_key`, que exige `decision_id`/`condition` no
vacios): `program_id`/`paragraph_id`/`source_statement_id`/`target`/
`formula`, sin `rule_family` variable (siempre CALCULATION aqui).
`program_id` se recibe EXPLICITO (correccion pre-commit 15B3-C2-B2,
seccion 1) -- garantiza unicidad cross-program de forma auditable,
nunca implicita en el formato de `paragraph_id`. Nunca se fusiona con
un candidato V1 (V1 jamas produce `rule_family=CALCULATION`, ver
`candidate_source_adapters._V1_RULE_FAMILY`)."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass

from ..contracts.candidate import CandidateArtifact, RuleCandidate
from ..contracts.candidate_promotion_assessment import CandidateSource, UnifiedRuleFamily
from ..contracts.canonical import CanonicalProgram
from ..contracts.semantic_graph import SemanticGraph
from ..contracts.v2_shadow_candidates import V2CandidateSupport, V2RuleType, V2ShadowCandidate
from .errors import CandidateDetectionError
from .semantic_effects_analyzer import analyze_semantic_effects
from .semantic_propagation_analyzer import analyze_semantic_propagation
from .v2_detector_context import V2DetectorContext, build_v2_detector_context
from .v2_detectors import (
    _data_item_semantic_tag,
    detect_calculation,
    detect_level_88_return_code,
    detect_return_code_propagation,
    detect_state_change,
)

# Mapping LOCAL de este modulo (deliberadamente distinto del de Fase 9,
# `candidate_source_adapters._V2_RULE_FAMILY_BY_TYPE` -- ver docstring).
_LOCAL_RULE_FAMILY_BY_TYPE: dict[V2RuleType, UnifiedRuleFamily] = {
    V2RuleType.RETURN_CODE_RULE: UnifiedRuleFamily.RETURN_CODE,
    V2RuleType.LEVEL_88_RETURN_CODE_RULE: UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
    V2RuleType.STATE_CHANGE_RULE: UnifiedRuleFamily.STATE_TRANSITION,
    V2RuleType.CALCULATION_RULE: UnifiedRuleFamily.CALCULATION,
}
_PROMOTABLE_RULE_TYPES = (
    V2RuleType.RETURN_CODE_RULE,
    V2RuleType.LEVEL_88_RETURN_CODE_RULE,
    V2RuleType.STATE_CHANGE_RULE,
    V2RuleType.CALCULATION_RULE,
)
_PROMOTABLE_RULE_FAMILIES = frozenset(
    {
        UnifiedRuleFamily.RETURN_CODE,
        UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
        UnifiedRuleFamily.STATE_TRANSITION,
        UnifiedRuleFamily.CALCULATION,
    }
)

# Fase 15B3-C1, seccion 6: unico criterio de relevancia funcional para
# promover STATE_CHANGE_RULE -- reutiliza los tags ya declarados en
# config/semantic-tags.yml (allowed_tags), nunca un enum nuevo. Copia
# LOCAL deliberada (Fase 15B3-C8-FIX-1, correccion de layering): este
# modulo es pipeline PRODUCTIVO y NUNCA debe depender de
# `candidate_source_adapters.py` (tooling de QUALIFICATION/Fase 9), pese
# a que ambos apliquen exactamente el mismo criterio -- paridad
# garantizada por
# `test_enhanced_candidate_integration.py::
# test_state_transition_semantic_tags_match_qualification_adapter`,
# nunca por una importacion compartida.
_STATE_TRANSITION_SEMANTIC_TAGS = frozenset({"status", "status_flag"})


def _program_id_from_paragraph_id(paragraph_id: str) -> str:
    """`paragraph_id = {program_id}::paragraph::{name}` (`identifiers.py`).
    Extraccion textual, nunca recalculo del ID -- mismo patron ya
    establecido en `candidate_source_adapters.py`/
    `interprocedural_rule_comparator.py` para `program_name`."""
    prefix, separator, _ = paragraph_id.partition("::paragraph::")
    return prefix if separator else paragraph_id


def functional_identity_key(
    *,
    paragraph_id: str,
    decision_id: str,
    condition: str,
    effect: str,
    rule_family: UnifiedRuleFamily,
) -> str:
    """Identidad funcional de una regla (ver docstring del modulo).
    SHA-256 truncado sobre una concatenacion canonica `\\x1f`-separada de
    campos ya deterministicos -- nunca timestamp, indice, texto LLM,
    path absoluto ni orden de diccionario no normalizado."""
    program_id = _program_id_from_paragraph_id(paragraph_id)
    canonical = "\x1f".join(
        [program_id, paragraph_id, decision_id, condition, effect, rule_family.value]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def functional_identity_key_for_unconditional_calculation(
    *, program_id: str, paragraph_id: str, source_statement_id: str, target: str, formula: str
) -> str:
    """Identidad funcional de un CALCULATION incondicional (Fase
    15B3-C2-B2) -- NUNCA una extension de `functional_identity_key`
    (esa firma permanece intacta para el camino condicionado, sin
    `decision_id`/`condition` que aportar aqui, ambos None por diseno).

    `program_id` se recibe EXPLICITO (correccion pre-commit 15B3-C2-B2:
    antes se derivaba implicitamente de `paragraph_id` dentro de esta
    funcion via `_program_id_from_paragraph_id` -- funcionalmente
    equivalente, dado que `paragraph_id = {program_id}::paragraph::
    {name}` ya lo embebe como prefijo, pero la garantia de unicidad
    cross-program quedaba implicita en el FORMATO de un solo string en
    vez de ser un campo propio, auditable y testeado de forma
    independiente). `source_statement_id` reemplaza a `decision_id` como
    ancla de identidad, tomado DIRECTAMENTE del campo real
    `V2ShadowCandidate.anchor_statement_id` (a su vez
    `CanonicalStatement.statement_id`) -- nunca derivado/parseado de un
    `effect_id` ni de `evidence_ids`.

    Propiedades probadas en `test_enhanced_candidate_integration.py`:
    (A) mismo program+statement+target+formula -> mismo id (funcion
    pura); (B) dos statements distintos con la misma formula -> ids
    distintos; (C) sentencia multi-target -> un id por target; (D) dos
    corridas identicas -> mismo id; (E) DOS PROGRAMAS distintos con
    paragraph/statement/target/formula localmente identicos -> ids
    distintos (`program_id` es el campo que lo garantiza explicitamente,
    nunca una inferencia sobre el formato de `paragraph_id`)."""
    canonical = "\x1f".join(
        [
            program_id,
            paragraph_id,
            source_statement_id,
            target,
            formula,
            UnifiedRuleFamily.CALCULATION.value,
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _v1_functional_key(candidate: RuleCandidate) -> str:
    # V1 (Q0) es siempre RETURN_CODE: decision_id/condition nunca son
    # None para estos candidatos (RuleCandidate._check_decision_anchor_
    # by_family lo exige) -- CALCULATION incondicional (Fase 15B3-C2-B2,
    # unica familia que admite ambos en None) nunca llega aqui, esta
    # funcion solo se llama sobre v1_candidates.candidates (Q0).
    assert candidate.decision_id is not None  # noqa: S101 - invariante del contrato V1
    assert candidate.condition is not None  # noqa: S101 - invariante del contrato V1
    return functional_identity_key(
        paragraph_id=candidate.paragraph_id,
        decision_id=candidate.decision_id,
        condition=candidate.condition,
        effect=candidate.outcome_code or "",
        rule_family=candidate.rule_family,
    )


@dataclass(frozen=True)
class _ConvertedCandidate:
    """Una observacion V2 ya validada contra el grafo (nodo Decision/
    Paragraph reales), lista para fusionarse por `key` con otras
    observaciones o con un `RuleCandidate` V1 existente."""

    key: str
    paragraph_id: str
    paragraph_name: str
    # str | None (Fase 15B3-C2-B2): None unicamente para CALCULATION
    # incondicional (ver `_convert_unconditional_calculation`) -- para
    # cualquier otra familia siguen siendo no-None, sin cambios.
    decision_id: str | None
    condition: str | None
    outcome_code: str | None
    rule_family: UnifiedRuleFamily
    detector_id: str
    detector_version: str
    detector_score: float
    line_start: int
    source_file: str
    evidence_ids: tuple[str, ...]
    source_v2_candidate_id: str


def _convert_v2_candidate(
    v2_candidate: V2ShadowCandidate,
    ctx: V2DetectorContext,
) -> tuple[_ConvertedCandidate | None, str | None]:
    """Valida UN `V2ShadowCandidate` ya filtrado (DETERMINISTIC,
    `decision_id` no nulo, rule_type promovible) contra el grafo.
    Devuelve `(None, motivo)` cuando el grafo no expone lo minimo que
    Q4/Q5a exigiran despues -- nunca fabrica un valor sintetico."""
    assert v2_candidate.decision_id is not None  # noqa: S101 - invariante del llamador

    decision_node = ctx.decision_node_by_id.get(v2_candidate.decision_id)
    paragraph_node = ctx.paragraph_node_by_key.get((v2_candidate.program, v2_candidate.paragraph))
    if decision_node is None or paragraph_node is None:
        return None, (
            f"candidato V2 {v2_candidate.candidate_id!r} descartado: no se encontro el nodo "
            f"Decision/Paragraph correspondiente en el grafo (decision_id="
            f"{v2_candidate.decision_id!r})"
        )

    condition = decision_node.properties.get("expression")
    if not isinstance(condition, str) or not condition.strip():
        return None, (
            f"candidato V2 {v2_candidate.candidate_id!r} descartado: el nodo Decision "
            f"{v2_candidate.decision_id!r} no expone 'expression' no vacia"
        )

    line_start = paragraph_node.properties.get("line_start")
    source_file = paragraph_node.properties.get("source_file")
    if not isinstance(line_start, int) or not isinstance(source_file, str) or not source_file:
        return None, (
            f"candidato V2 {v2_candidate.candidate_id!r} descartado: el nodo Paragraph "
            f"{paragraph_node.id!r} no expone line_start/source_file validos"
        )

    rule_family = _LOCAL_RULE_FAMILY_BY_TYPE.get(v2_candidate.rule_type)
    if rule_family not in _PROMOTABLE_RULE_FAMILIES:
        return None, (
            f"candidato V2 {v2_candidate.candidate_id!r} descartado: rule_family "
            f"{rule_family} no esta en el alcance de esta fase"
        )

    if rule_family == UnifiedRuleFamily.STATE_TRANSITION:
        # Fase 15B3-C1, seccion 5/6: unico gate de relevancia funcional --
        # nunca LLM/NLP/heuristica de texto libre, solo el semantic_tag ya
        # asignado por SemanticTagger. Sin esto, CUALQUIER escritura
        # determinista (V2_STATE_CHANGE) se promoveria -- prohibido
        # explicitamente.
        target_tag = _data_item_semantic_tag(
            ctx, program=v2_candidate.program, qualified_name=v2_candidate.target_qualified_name
        )
        if target_tag not in _STATE_TRANSITION_SEMANTIC_TAGS:
            return None, (
                f"candidato V2 {v2_candidate.candidate_id!r} descartado: target "
                f"{v2_candidate.target_qualified_name!r} tiene semantic_tag {target_tag!r} "
                f"(se requiere uno de {sorted(_STATE_TRANSITION_SEMANTIC_TAGS)} para "
                "STATE_TRANSITION); relevancia funcional no demostrada, permanece shadow"
            )

    evidence_ids = tuple(
        sorted({*v2_candidate.semantic_effect_ids, *v2_candidate.propagation_fact_ids})
    )
    if rule_family == UnifiedRuleFamily.CALCULATION:
        # Fase 15B3-C2-B1, seccion 16: `functional_identity_key` no recibe
        # el target como argumento separado -- `resolved_literal` (usado
        # por las demas familias) es SIEMPRE None para un calculo (no hay
        # literal, hay formula). Sin incluir el target explicitamente en
        # `effect`, dos targets de la MISMA Decision (p.ej.
        # `COMPUTE A B = X + Y`) colisionarian (mismo paragraph_id/
        # decision_id/condition/rule_family, effect="" en ambos) y se
        # fusionarian incorrectamente en un unico RuleCandidate. La formula
        # (statement.expression si existe -- COMPUTE --, si no
        # statement.source_text -- ADD/SUBTRACT/MULTIPLY/DIVIDE, sin nodo
        # de expresion dedicado) se incluye para que la MISMA Decision +
        # MISMO target + MISMA formula si deduplique correctamente.
        target_key, formula_text = _calculation_target_and_formula(v2_candidate, ctx)
        effect = f"target={target_key}\x1fformula={formula_text}"
    else:
        effect = v2_candidate.resolved_literal or ""
    key = functional_identity_key(
        paragraph_id=paragraph_node.id,
        decision_id=v2_candidate.decision_id,
        condition=condition,
        effect=effect,
        rule_family=rule_family,
    )
    return (
        _ConvertedCandidate(
            key=key,
            paragraph_id=paragraph_node.id,
            paragraph_name=v2_candidate.paragraph,
            decision_id=v2_candidate.decision_id,
            condition=condition,
            outcome_code=v2_candidate.resolved_literal,
            rule_family=rule_family,
            detector_id=v2_candidate.detector_id,
            detector_version=v2_candidate.detector_version,
            detector_score=v2_candidate.detector_score,
            line_start=line_start,
            source_file=source_file,
            evidence_ids=evidence_ids,
            source_v2_candidate_id=v2_candidate.candidate_id,
        ),
        None,
    )


def _calculation_target_and_formula(
    v2_candidate: V2ShadowCandidate, ctx: V2DetectorContext
) -> tuple[str, str]:
    """Target/formula reales de un CALCULATION (compartido entre el
    camino condicionado y el incondicional -- unica fuente para no
    duplicar la logica de fallback `expression`/`source_text`, ver
    docstring de `_convert_v2_candidate`)."""
    origin_statement = ctx.statement_by_id.get(v2_candidate.anchor_statement_id)
    formula_text = ""
    if origin_statement is not None:
        formula_text = (origin_statement.expression or origin_statement.source_text or "").strip()
    target_key = v2_candidate.target_qualified_name or v2_candidate.target_variable
    return target_key, formula_text


def _convert_unconditional_calculation(
    v2_candidate: V2ShadowCandidate,
    ctx: V2DetectorContext,
) -> tuple[_ConvertedCandidate | None, str | None]:
    """Analogo a `_convert_v2_candidate`, pero para un CALCULATION_RULE
    SIN Decision envolvente (Fase 15B3-C2-B2, `decision_id=None`).
    DELIBERADAMENTE separado en vez de ramificar dentro de
    `_convert_v2_candidate`: nunca consulta `ctx.decision_node_by_id`
    (no hay Decision que buscar), nunca produce una `condition` (queda
    `None`) -- la unica evidencia estructural que necesita es el nodo
    Paragraph real (line_start/source_file, identico al camino
    condicionado) y la formula/target ya demostrados por el propio
    `V2ShadowCandidate` (SemanticEffect COMPUTE_VALUE real, construido
    por `v2_detectors.detect_calculation`). Nunca fabrica un
    `decision_id`/`condition` sintetico: ambos permanecen `None` en el
    `_ConvertedCandidate` resultante."""
    assert v2_candidate.decision_id is None  # noqa: S101 - invariante del llamador
    assert v2_candidate.rule_type == V2RuleType.CALCULATION_RULE  # noqa: S101 - idem

    paragraph_node = ctx.paragraph_node_by_key.get((v2_candidate.program, v2_candidate.paragraph))
    if paragraph_node is None:
        return None, (
            f"candidato V2 {v2_candidate.candidate_id!r} descartado: no se encontro el nodo "
            "Paragraph correspondiente en el grafo"
        )

    line_start = paragraph_node.properties.get("line_start")
    source_file = paragraph_node.properties.get("source_file")
    if not isinstance(line_start, int) or not isinstance(source_file, str) or not source_file:
        return None, (
            f"candidato V2 {v2_candidate.candidate_id!r} descartado: el nodo Paragraph "
            f"{paragraph_node.id!r} no expone line_start/source_file validos"
        )

    target_key, formula_text = _calculation_target_and_formula(v2_candidate, ctx)
    # source_statement_id: tomado DIRECTAMENTE del campo real
    # V2ShadowCandidate.anchor_statement_id (CanonicalStatement.
    # statement_id via detect_calculation) -- nunca parseado de un
    # effect_id ni de evidence_ids (ver correccion pre-commit
    # 15B3-C2-B2, seccion 2). evidence_ids se construye DESPUES,
    # independientemente de `key`: reordenarlo nunca afecta la
    # identidad (ver test_unconditional_calculation_identity_
    # independent_of_evidence_ids_order).
    key = functional_identity_key_for_unconditional_calculation(
        program_id=_program_id_from_paragraph_id(paragraph_node.id),
        paragraph_id=paragraph_node.id,
        source_statement_id=v2_candidate.anchor_statement_id,
        target=target_key,
        formula=formula_text,
    )
    evidence_ids = tuple(
        sorted({*v2_candidate.semantic_effect_ids, *v2_candidate.propagation_fact_ids})
    )
    return (
        _ConvertedCandidate(
            key=key,
            paragraph_id=paragraph_node.id,
            paragraph_name=v2_candidate.paragraph,
            decision_id=None,
            condition=None,
            outcome_code=None,
            rule_family=UnifiedRuleFamily.CALCULATION,
            detector_id=v2_candidate.detector_id,
            detector_version=v2_candidate.detector_version,
            detector_score=v2_candidate.detector_score,
            line_start=line_start,
            source_file=source_file,
            evidence_ids=evidence_ids,
            source_v2_candidate_id=v2_candidate.candidate_id,
        ),
        None,
    )


_CORROBORATION_FAMILIES = frozenset(
    {UnifiedRuleFamily.LEVEL_88_RETURN_CODE, UnifiedRuleFamily.RETURN_CODE}
)


def _corroboration_group_key(item: _ConvertedCandidate) -> str:
    """Ancla comun, EXCLUSIVA del par LEVEL_88_RETURN_CODE/RETURN_CODE
    (Fase 15B4-CANDIDATE-QUALITY-2): reutiliza `functional_identity_key`
    real (nunca una funcion de hash nueva), sustituyendo unicamente el
    componente `rule_family` por un valor fijo compartido -- asi ambos
    miembros de un mismo hecho (misma paragraph_id/decision_id/
    condition/outcome_code) calculan la MISMA ancla sin importar cual de
    las dos familias tengan. Nunca se usa fuera de
    `_corroborate_level88_return_code_pairs`."""
    return functional_identity_key(
        paragraph_id=item.paragraph_id,
        decision_id=item.decision_id or "",
        condition=item.condition or "",
        effect=item.outcome_code or "",
        rule_family=UnifiedRuleFamily.LEVEL_88_RETURN_CODE,
    )


def _corroborate_level88_return_code_pairs(
    converted: list[_ConvertedCandidate],
) -> tuple[list[_ConvertedCandidate], list[str]]:
    """Excepcion productiva ESTRECHA (Fase 15B4-CANDIDATE-QUALITY-2,
    corrige la duplicacion demostrada en Fase 15B4-CANDIDATE-QUALITY-1):
    un candidato `RETURN_CODE` (via `V2_RETURN_CODE_PROPAGATION`) se
    omite del resultado UNICAMENTE cuando existe un candidato
    `LEVEL_88_RETURN_CODE` con la MISMA paragraph_id/decision_id/
    condition/outcome_code Y `evidence_ids` EXACTAMENTE iguales -- nunca
    por similitud, nunca para ninguna otra pareja de `rule_family`. El
    candidato `LEVEL_88_RETURN_CODE` (representacion mas especifica del
    mecanismo `SET condicion-88 TO TRUE` -> `CONDITION_LITERAL` ->
    padre `return_code`) se conserva intacto -- su `key`/`candidate_id`
    NUNCA cambian -- como unica fuente de identidad del hecho fusionado.
    `functional_identity_key` global permanece sin tocar; esta funcion
    nunca se aplica a ninguna otra combinacion de familias."""
    by_key: dict[str, list[_ConvertedCandidate]] = defaultdict(list)
    other: list[_ConvertedCandidate] = []
    for item in converted:
        if item.rule_family in _CORROBORATION_FAMILIES and item.decision_id is not None:
            by_key[_corroboration_group_key(item)].append(item)
        else:
            other.append(item)

    kept: list[_ConvertedCandidate] = list(other)
    warnings: list[str] = []
    for key in sorted(by_key):
        members = by_key[key]
        level88 = [m for m in members if m.rule_family == UnifiedRuleFamily.LEVEL_88_RETURN_CODE]
        return_code = [m for m in members if m.rule_family == UnifiedRuleFamily.RETURN_CODE]
        absorbed: set[str] = set()
        for primary in level88:
            for corroborator in return_code:
                if (
                    corroborator.source_v2_candidate_id not in absorbed
                    and corroborator.evidence_ids == primary.evidence_ids
                ):
                    absorbed.add(corroborator.source_v2_candidate_id)
                    warnings.append(
                        f"candidato {primary.source_v2_candidate_id!r} (LEVEL_88_RETURN_CODE) "
                        f"corroborado por {corroborator.detector_id} "
                        f"({corroborator.source_v2_candidate_id!r}); RETURN_CODE redundante "
                        "omitido, evidence_ids identicos"
                    )
                    break
        kept.extend(level88)
        kept.extend(m for m in return_code if m.source_v2_candidate_id not in absorbed)
    return kept, sorted(set(warnings))


def _merge_new_group(
    members: list[_ConvertedCandidate], *, source_package_hash: str
) -> RuleCandidate:
    """Regla A (sin equivalente V1): todos los miembros comparten `key`
    por construccion. `primary` se elige por criterio deterministico
    (nunca por orden de llegada, regla E) para fijar
    `detector_id`/`detector_version`/`detector_score`."""
    primary = min(members, key=lambda m: (m.detector_id, m.source_v2_candidate_id))
    evidence_ids = sorted(
        {evidence_id for member in members for evidence_id in member.evidence_ids}
    )
    return RuleCandidate(
        candidate_id=f"candidate::enhanced::{source_package_hash}::{primary.key}",
        paragraph_id=primary.paragraph_id,
        paragraph_name=primary.paragraph_name,
        decision_id=primary.decision_id,
        detector_id=primary.detector_id,
        detector_version=primary.detector_version,
        detector_score=primary.detector_score,
        condition=primary.condition,
        outcome_code=primary.outcome_code,
        rule_type=None,
        line_start=primary.line_start,
        source_file=primary.source_file,
        source_package_hash=source_package_hash,
        candidate_source=CandidateSource.V2,
        rule_family=primary.rule_family,
        evidence_ids=evidence_ids,
    )


def _merge_into_v1(
    v1_candidate: RuleCandidate, members: list[_ConvertedCandidate]
) -> tuple[RuleCandidate, str]:
    """Regla D: produce una COPIA (`model_copy`, nunca mutacion en sitio)
    del `RuleCandidate` V1 con `evidence_ids` fusionados. Todo campo
    identificador (`candidate_id`, `decision_id`, `condition`,
    `outcome_code`, `detector_id`, ...) permanece exactamente igual al
    original."""
    evidence_ids = sorted(
        {*v1_candidate.evidence_ids, *(eid for m in members for eid in m.evidence_ids)}
    )
    merged = v1_candidate.model_copy(update={"evidence_ids": evidence_ids})
    detector_ids = sorted({member.detector_id for member in members})
    warning = (
        f"candidato V1 {v1_candidate.candidate_id!r} corroborado por deteccion ampliada "
        f"(detectores: {', '.join(detector_ids)}); evidence_ids combinados, campos "
        "identificadores de V1 sin modificar"
    )
    return merged, warning


def _merge_candidates(
    converted: list[_ConvertedCandidate],
    v1_candidates: CandidateArtifact,
    *,
    source_package_hash: str,
) -> tuple[list[RuleCandidate], list[str]]:
    """Funcion pura de fusion/deduplicacion (regla E: el orden de
    `converted`/`v1_candidates.candidates` nunca afecta el resultado).
    Devuelve `(candidatos_a_agregar_o_reemplazar, warnings)`: cada
    entrada de la primera lista tiene `candidate_id` igual al de un
    `RuleCandidate` V1 existente (reemplazo por version con evidencia
    fusionada, regla D) o un `candidate_id` nuevo (regla A, sin
    equivalente V1). Antes de agrupar (Fase 15B4-CANDIDATE-QUALITY-2):
    `_corroborate_level88_return_code_pairs` omite un `RETURN_CODE`
    redundante cuando un `LEVEL_88_RETURN_CODE` ya demuestra el MISMO
    hecho con `evidence_ids` identicos -- excepcion estrecha, nunca
    aplicable a otra pareja de familias."""
    converted, corroboration_warnings = _corroborate_level88_return_code_pairs(converted)

    groups: dict[str, list[_ConvertedCandidate]] = defaultdict(list)
    for item in converted:
        groups[item.key].append(item)

    v1_by_key: dict[str, RuleCandidate] = {
        _v1_functional_key(candidate): candidate for candidate in v1_candidates.candidates
    }

    result: dict[str, RuleCandidate] = {}
    warnings: list[str] = list(corroboration_warnings)
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda m: (m.detector_id, m.source_v2_candidate_id))
        v1_match = v1_by_key.get(key)
        if v1_match is not None:
            merged, warning = _merge_into_v1(v1_match, members)
            result[merged.candidate_id] = merged
            warnings.append(warning)
        else:
            new_candidate = _merge_new_group(members, source_package_hash=source_package_hash)
            result[new_candidate.candidate_id] = new_candidate

    ordered = sorted(result.values(), key=lambda candidate: candidate.candidate_id)
    return ordered, sorted(set(warnings))


def _in_memory_canonical_hash(canonical_programs: list[CanonicalProgram]) -> str:
    """SHA-256 deterministico sobre `(source_file, source_hash)` de los
    `CanonicalProgram` ya cargados en memoria por el llamador --
    satisface `SemanticEffectsArtifact.source_artifact_hashes['artifacts/
    02-canonical']` sin releer el filesystem."""
    entries = sorted((program.source_file, program.source_hash) for program in canonical_programs)
    digest_source = "\n".join(f"{path}:{file_hash}" for path, file_hash in entries)
    return hashlib.sha256(digest_source.encode("utf-8")).hexdigest()


def detect_enhanced_candidates(
    *,
    canonical_programs: list[CanonicalProgram],
    semantic_graph: SemanticGraph,
    v1_candidates: CandidateArtifact,
    run_id: str,
    source_package_hash: str,
) -> tuple[list[RuleCandidate], list[str]]:
    """Ejecuta `V2_RETURN_CODE_PROPAGATION`/`V2_LEVEL_88_RETURN_CODE`/
    `V2_STATE_CHANGE`/`V2_CALCULATION` en memoria y fusiona el resultado con
    `v1_candidates` via `_merge_candidates`. Devuelve `(candidatos_a_
    agregar_o_reemplazar, warnings)` -- el llamador (`candidates_detected_
    stage.py`) debe indexar por `candidate_id` y reemplazar/agregar,
    nunca simplemente concatenar (una entrada de reemplazo comparte
    `candidate_id` con su V1 original)."""
    try:
        source_artifact_hashes = {
            "artifacts/02-canonical": _in_memory_canonical_hash(canonical_programs)
        }
        semantic_effects = analyze_semantic_effects(
            canonical_programs=canonical_programs,
            run_id=run_id,
            source_package_hash=source_package_hash,
            source_artifact_hashes=source_artifact_hashes,
        )
        semantic_propagation = analyze_semantic_propagation(
            canonical_programs=canonical_programs,
            semantic_effects=semantic_effects,
            run_id=run_id,
            source_package_hash=source_package_hash,
            source_artifact_hashes=source_artifact_hashes,
        )
        ctx = build_v2_detector_context(
            canonical_programs=canonical_programs,
            semantic_graph=semantic_graph,
            v1_candidates=v1_candidates,
            semantic_effects=semantic_effects,
            semantic_propagation=semantic_propagation,
        )
    except Exception as exc:  # noqa: BLE001 - se reclasifica como error de dominio explicito
        raise CandidateDetectionError(
            "deteccion ampliada (enhanced_candidates_enabled=true) fallo al construir el "
            f"contexto V2 en memoria: {exc}"
        ) from exc

    v2_candidates = sorted(
        (
            *detect_return_code_propagation(ctx),
            *detect_level_88_return_code(ctx),
            *detect_state_change(ctx),
            *detect_calculation(ctx),
        ),
        key=lambda candidate: candidate.candidate_id,
    )

    converted: list[_ConvertedCandidate] = []
    discard_warnings: list[str] = []
    for v2_candidate in v2_candidates:
        if v2_candidate.rule_type not in _PROMOTABLE_RULE_TYPES:
            continue
        if v2_candidate.rule_type == V2RuleType.STATE_CHANGE_RULE:
            # V2_STATE_CHANGE nunca produce BLOCKED (detect_state_change
            # filtra fact_kind/literal ANTES de construir un candidato) --
            # support=PARTIAL aqui es su UNICO valor posible y describe
            # exclusivamente "relevancia funcional no evaluada en V2",
            # nunca incertidumbre sobre el valor. La relevancia se evalua
            # a continuacion, dentro de _convert_v2_candidate (semantic_tag
            # del target), nunca aqui -- un unico punto de decision.
            if v2_candidate.support != V2CandidateSupport.PARTIAL:
                continue
        elif v2_candidate.rule_type == V2RuleType.CALCULATION_RULE:
            # V2_CALCULATION (Fase 15B3-C2-B1): support=PARTIAL aqui es su
            # UNICO valor posible, con un significado LOCAL DISTINTO al de
            # STATE_CHANGE_RULE -- "formula estructuralmente completa,
            # valor numerico runtime no evaluado" (nunca "relevancia no
            # evaluada": una formula aritmetica siempre es relevante por
            # construccion, sin necesitar un semantic_tag gate -- ver
            # docstring de detect_calculation). DETERMINISTIC no se usa
            # porque su validador exige resolved_literal/
            # propagation_fact_ids, que una formula nunca tiene.
            if v2_candidate.support != V2CandidateSupport.PARTIAL:
                continue
        elif v2_candidate.support != V2CandidateSupport.DETERMINISTIC:
            continue
        if v2_candidate.decision_id is None:
            if v2_candidate.rule_type == V2RuleType.CALCULATION_RULE:
                # Fase 15B3-C2-B2: un calculo incondicional (sin Decision
                # envolvente) SI se productiviza -- camino DEDICADO
                # (`_convert_unconditional_calculation`, nunca
                # `_convert_v2_candidate`, que exige un nodo Decision
                # real). Ya no hay warning de descarte para este caso: la
                # senal "no silent loss" de 15B3-C2-B1 (correccion
                # pre-commit) queda obsoleta porque el candidato ahora
                # SI llega a 06-candidates.json.
                item, discard_reason = _convert_unconditional_calculation(v2_candidate, ctx)
                if item is None:
                    if discard_reason is not None:
                        discard_warnings.append(discard_reason)
                    continue
                converted.append(item)
            # Cualquier otro rule_type con decision_id=None (p. ej.
            # STATE_CHANGE_RULE fuera de alcance) permanece invisible,
            # sin warning -- comportamiento anterior a esta fase, sin
            # cambios.
            continue
        item, discard_reason = _convert_v2_candidate(v2_candidate, ctx)
        if item is None:
            if discard_reason is not None:
                discard_warnings.append(discard_reason)
            continue
        converted.append(item)

    merged_candidates, merge_warnings = _merge_candidates(
        converted, v1_candidates, source_package_hash=source_package_hash
    )
    return merged_candidates, sorted(set(discard_warnings) | set(merge_warnings))
