"""Adaptador de grupos unified para el read model de gobierno operativo
(Fase 15A Parte 6, `feat/operational-governance-ui`).

Lee UNICAMENTE los 4 archivos materializados (`candidates.json`,
`context-packages.json`, `rule-drafts.json`, `guardrails.json`) de UNA
generacion UNIFIED ya persistida (Fase 14B) -- nunca reconstruye,
reinterpreta, ni invoca ningun proveedor. Es una proyeccion pura: cada
campo de `GovernanceUnifiedGroupSummary` proviene DIRECTAMENTE de un
registro real ya validado, unido por `group_id`. Nunca elige un member
"ganador" (mismo principio que Fase 11/13): `member_ids`/`source_
candidate_ids`/`evidence_ids`/`provenance_references` se preservan tal
cual. Una ausencia legitima (p. ej. ningun `UnifiedShadowContextPackage
Record` para un `group_id`, porque ese grupo aun no alcanzo Fase 13) se
representa con `None`/lista vacia -- nunca se inventa un valor.

Solo lectura: usa `UnifiedActivationStore.validate_file_reference` (ya
revalida hash/tamano contra el filesystem real) para leer los 4
archivos -- ninguna escritura, ningun mkdir, ningun rename."""

from __future__ import annotations

from ..contracts.operational_governance import GovernanceUnifiedGroupSummary
from ..contracts.unified_activation_materialization import (
    MaterializedActivationLane,
    MaterializedGenerationManifest,
    MaterializedUnifiedCandidatesFile,
    MaterializedUnifiedContextPackagesFile,
    MaterializedUnifiedGuardrailsFile,
    MaterializedUnifiedRuleDraftsFile,
)
from ..contracts.unified_shadow_downstream import UnifiedShadowContextPackageRecord
from .errors import UnifiedActivationStoreError
from .unified_activation_store import UnifiedActivationStore
from .unified_active_lane_router import (
    LOGICAL_NAME_CANDIDATES,
    LOGICAL_NAME_CONTEXT_PACKAGES,
    LOGICAL_NAME_GUARDRAILS,
    LOGICAL_NAME_RULE_DRAFTS,
)


class OperationalGovernanceGroupAdapterError(Exception):
    """Un archivo materializado de la generacion existe segun el
    manifiesto pero es ilegible/corrupto al parsearlo como el contrato
    de proyeccion unified esperado. El llamador (reader, Parte 4) lo
    convierte en un issue tipado -- nunca se propaga como excepcion no
    controlada hasta la API/UI."""


def _read_projection_file(
    store: UnifiedActivationStore,
    manifest: MaterializedGenerationManifest,
    *,
    logical_name: str,
) -> bytes | None:
    file_reference = next((f for f in manifest.files if f.logical_name == logical_name), None)
    if file_reference is None:
        return None
    try:
        path = store.validate_file_reference(file_reference)
    except UnifiedActivationStoreError as exc:
        raise OperationalGovernanceGroupAdapterError(
            f"{logical_name}: archivo ausente o hash no reconciliado"
        ) from exc
    return path.read_bytes()


def build_unified_group_summaries(
    store: UnifiedActivationStore, manifest: MaterializedGenerationManifest
) -> list[GovernanceUnifiedGroupSummary]:
    """Proyecta los grupos unified de `manifest` (debe ser una
    generacion `lane=UNIFIED` ya persistida) -- una generacion `V1`
    siempre produce una lista vacia (ausencia legitima, nunca un
    error). Lanza `OperationalGovernanceGroupAdapterError` si alguno de
    los 4 archivos referenciados por el manifiesto existe pero esta
    corrupto/ilegible como su contrato de proyeccion esperado -- el
    reader decide como representar eso como issue."""
    if manifest.lane != MaterializedActivationLane.UNIFIED:
        return []

    candidates_bytes = _read_projection_file(store, manifest, logical_name=LOGICAL_NAME_CANDIDATES)
    if candidates_bytes is None:
        return []
    try:
        candidates_file = MaterializedUnifiedCandidatesFile.model_validate_json(candidates_bytes)
    except ValueError as exc:
        raise OperationalGovernanceGroupAdapterError(
            f"{LOGICAL_NAME_CANDIDATES}: contenido invalido"
        ) from exc

    context_bytes = _read_projection_file(
        store, manifest, logical_name=LOGICAL_NAME_CONTEXT_PACKAGES
    )
    context_by_group: dict[str, UnifiedShadowContextPackageRecord] = {}
    if context_bytes is not None:
        try:
            context_file = MaterializedUnifiedContextPackagesFile.model_validate_json(context_bytes)
        except ValueError as exc:
            raise OperationalGovernanceGroupAdapterError(
                f"{LOGICAL_NAME_CONTEXT_PACKAGES}: contenido invalido"
            ) from exc
        context_by_group = {record.group_id: record for record in context_file.context_packages}

    # rule-drafts/guardrails se leen para confirmar legibilidad (un
    # archivo corrupto referenciado por el manifiesto debe producir un
    # issue), pero `GovernanceUnifiedGroupSummary` no necesita su
    # contenido -- `rule_draft_record_id`/`guardrail_status` ya viven
    # en `UnifiedActivationUnifiedReference` (Fase 14A), la fuente
    # unica de verdad para esos dos campos.
    drafts_bytes = _read_projection_file(store, manifest, logical_name=LOGICAL_NAME_RULE_DRAFTS)
    if drafts_bytes is not None:
        try:
            MaterializedUnifiedRuleDraftsFile.model_validate_json(drafts_bytes)
        except ValueError as exc:
            raise OperationalGovernanceGroupAdapterError(
                f"{LOGICAL_NAME_RULE_DRAFTS}: contenido invalido"
            ) from exc

    guardrails_bytes = _read_projection_file(store, manifest, logical_name=LOGICAL_NAME_GUARDRAILS)
    if guardrails_bytes is not None:
        try:
            MaterializedUnifiedGuardrailsFile.model_validate_json(guardrails_bytes)
        except ValueError as exc:
            raise OperationalGovernanceGroupAdapterError(
                f"{LOGICAL_NAME_GUARDRAILS}: contenido invalido"
            ) from exc

    summaries: list[GovernanceUnifiedGroupSummary] = []
    for reference in candidates_file.candidates:
        context_record = context_by_group.get(reference.group_id)
        review_decision_ids: list[str] = []
        evidence_aliases: list[str] = []
        context_package_record_id: str | None = None
        if context_record is not None:
            review_decision_ids = list(context_record.review_decision_ids)
            evidence_aliases = list(context_record.evidence_aliases)
            context_package_record_id = context_record.record_id
        summaries.append(
            GovernanceUnifiedGroupSummary(
                group_id=reference.group_id,
                rule_family=reference.rule_family,
                program=reference.program,
                target=reference.target,
                output_literal=reference.output_literal,
                member_ids=list(reference.member_ids),
                source_candidate_ids=list(reference.source_candidate_ids),
                review_decision_ids=review_decision_ids,
                context_package_record_id=context_package_record_id,
                rule_draft_record_id=reference.rule_draft_record_id,
                guardrail_status=reference.guardrail_status.value,
                evidence_ids=list(reference.evidence_ids),
                evidence_aliases=evidence_aliases,
                provenance_references=list(reference.provenance_references),
            )
        )
    summaries.sort(key=lambda summary: summary.group_id)
    return summaries


__all__ = ["OperationalGovernanceGroupAdapterError", "build_unified_group_summaries"]
