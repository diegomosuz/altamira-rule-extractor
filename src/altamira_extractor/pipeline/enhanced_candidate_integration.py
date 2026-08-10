"""Integracion minima de los detectores V2 en el flujo productivo de
CANDIDATES_DETECTED (Fase 15B3-B, "realineacion minima del motor de
extraccion de reglas"; Fase 15B3-B1, "cierre correctivo").

Alcance deliberado: unicamente `V2RuleType.RETURN_CODE_RULE` y
`V2RuleType.LEVEL_88_RETURN_CODE_RULE`, con `support=DETERMINISTIC` y
`decision_id` resuelto contra un nodo `Decision` real -- las dos unicas
familias V2 cuya evidencia esta anclada a una `Decision` (misma
granularidad que Q0/V1) y por lo tanto son compatibles, sin modificar
`context_package_builder.py`, con las queries Q4/Q5a (que exigen un
`Decision` real con ese `decision_id`).

Deliberadamente fuera de alcance: `V2RuleType.STATE_CHANGE_RULE` (nunca
DETERMINISTIC, nunca promovible) e `InterproceduralRuleType.
BY_REFERENCE_RULE` (anclado a un `call_site_id`, no a una `Decision`).

Reutiliza sin modificar: `v2_detector_context.build_v2_detector_context`,
`v2_detectors.detect_return_code_propagation`/
`detect_level_88_return_code`, `semantic_effects_analyzer.
analyze_semantic_effects`, `semantic_propagation_analyzer.
analyze_semantic_propagation`, y el mapping ya existente
`candidate_source_adapters._V2_RULE_FAMILY_BY_TYPE`. Nunca persiste
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
   (ver `_merge_candidates`)."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass

from ..contracts.candidate import CandidateArtifact, RuleCandidate
from ..contracts.candidate_promotion_assessment import CandidateSource, UnifiedRuleFamily
from ..contracts.canonical import CanonicalProgram
from ..contracts.semantic_graph import SemanticGraph
from ..contracts.v2_shadow_candidates import V2CandidateSupport, V2RuleType, V2ShadowCandidate
from .candidate_source_adapters import _V2_RULE_FAMILY_BY_TYPE
from .errors import CandidateDetectionError
from .semantic_effects_analyzer import analyze_semantic_effects
from .semantic_propagation_analyzer import analyze_semantic_propagation
from .v2_detector_context import V2DetectorContext, build_v2_detector_context
from .v2_detectors import detect_level_88_return_code, detect_return_code_propagation

_PROMOTABLE_RULE_TYPES = (V2RuleType.RETURN_CODE_RULE, V2RuleType.LEVEL_88_RETURN_CODE_RULE)
_PROMOTABLE_RULE_FAMILIES = frozenset(
    {UnifiedRuleFamily.RETURN_CODE, UnifiedRuleFamily.LEVEL_88_RETURN_CODE}
)


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


def _v1_functional_key(candidate: RuleCandidate) -> str:
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
    decision_id: str
    condition: str
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

    rule_family = _V2_RULE_FAMILY_BY_TYPE.get(v2_candidate.rule_type)
    if rule_family not in _PROMOTABLE_RULE_FAMILIES:
        return None, (
            f"candidato V2 {v2_candidate.candidate_id!r} descartado: rule_family "
            f"{rule_family} no esta en el alcance de esta fase"
        )

    evidence_ids = tuple(
        sorted({*v2_candidate.semantic_effect_ids, *v2_candidate.propagation_fact_ids})
    )
    key = functional_identity_key(
        paragraph_id=paragraph_node.id,
        decision_id=v2_candidate.decision_id,
        condition=condition,
        effect=v2_candidate.resolved_literal or "",
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
    equivalente V1)."""
    groups: dict[str, list[_ConvertedCandidate]] = defaultdict(list)
    for item in converted:
        groups[item.key].append(item)

    v1_by_key: dict[str, RuleCandidate] = {
        _v1_functional_key(candidate): candidate for candidate in v1_candidates.candidates
    }

    result: dict[str, RuleCandidate] = {}
    warnings: list[str] = []
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
    """Ejecuta `V2_RETURN_CODE_PROPAGATION`/`V2_LEVEL_88_RETURN_CODE` en
    memoria (nunca STATE_CHANGE_RULE) y fusiona el resultado con
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
        (*detect_return_code_propagation(ctx), *detect_level_88_return_code(ctx)),
        key=lambda candidate: candidate.candidate_id,
    )

    converted: list[_ConvertedCandidate] = []
    discard_warnings: list[str] = []
    for v2_candidate in v2_candidates:
        if v2_candidate.support != V2CandidateSupport.DETERMINISTIC:
            continue
        if v2_candidate.rule_type not in _PROMOTABLE_RULE_TYPES:
            continue
        if v2_candidate.decision_id is None:
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
