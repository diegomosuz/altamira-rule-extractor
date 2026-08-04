"""Constructor de generaciones UNIFIED (Fase 14B Parte 7,
`feat/controlled-unified-materialization`).

Puro: recibe UNICAMENTE objetos ya validados (`UnifiedActivationEvaluationArtifact`
de Fase 14A, `UnifiedShadowDownstreamArtifact` de Fase 13,
`UnifiedMaterializationAuthorization`) -- nunca toca el filesystem,
nunca invoca al proveedor fake nuevamente, nunca regenera ni
reinterpreta `ContextPackage`/`RuleDraft`/`GuardrailReport`: materializa
EXACTAMENTE la proyeccion ya validada, filtrada a `approved_group_ids`.

Cada grupo aprobado debe, simultaneamente:

1. existir en `evaluation.unified_references` con `level=RULE` (un
   resultado unified YA ejecutado por Fase 13, nunca un candidato
   estructural sin regla);
2. tener `guardrail_status=PASSED` en esa misma referencia;
3. existir en `downstream.group_results` con `execution_status=
   EXECUTED`;
4. tener, en el `UnifiedShadowGuardrailRecord` real referenciado por
   ese grupo, `status=PASSED`.

Cualquier grupo que no cumpla las cuatro condiciones simultaneamente
nunca se materializa -- Parte 5, invariantes 18-20."""

from __future__ import annotations

import hashlib

from ..contracts.unified_activation_evaluation import (
    UnifiedActivationComparisonLevel,
    UnifiedActivationEvaluationArtifact,
)
from ..contracts.unified_activation_materialization import (
    MaterializedActivationLane,
    MaterializedFileReference,
    MaterializedGenerationKind,
    MaterializedGenerationManifest,
    MaterializedUnifiedCandidatesFile,
    MaterializedUnifiedContextPackagesFile,
    MaterializedUnifiedGuardrailsFile,
    MaterializedUnifiedRuleDraftsFile,
    compute_generation_id,
)
from ..contracts.unified_materialization_authorization import (
    UnifiedMaterializationAction,
    UnifiedMaterializationAuthorization,
)
from ..contracts.unified_shadow_downstream import (
    UnifiedShadowDownstreamArtifact,
    UnifiedShadowDownstreamExecutionStatus,
    UnifiedShadowGuardrailStatus,
)
from .errors import UnifiedMaterializationError

_LOGICAL_NAME_CANDIDATES = "candidates"
_LOGICAL_NAME_CONTEXT_PACKAGES = "context-packages"
_LOGICAL_NAME_RULE_DRAFTS = "rule-drafts"
_LOGICAL_NAME_GUARDRAILS = "guardrails"

_KIND_BY_ACTION = {
    UnifiedMaterializationAction.ACTIVATE_UNIFIED_CANARY: (
        MaterializedGenerationKind.UNIFIED_CANARY
    ),
    UnifiedMaterializationAction.ACTIVATE_UNIFIED_PRIMARY: (
        MaterializedGenerationKind.UNIFIED_PRIMARY
    ),
}


class UnifiedGenerationFiles:
    """Contenedor puro de los cuatro archivos de datos de una
    generacion UNIFIED, ya serializados (`to_stable_json`, UTF-8) --
    el store (Fase 14B Parte 8) los escribe tal cual, nunca los
    reinterpreta."""

    __slots__ = ("candidates", "context_packages", "guardrails", "rule_drafts")

    def __init__(
        self,
        *,
        candidates: MaterializedUnifiedCandidatesFile,
        context_packages: MaterializedUnifiedContextPackagesFile,
        rule_drafts: MaterializedUnifiedRuleDraftsFile,
        guardrails: MaterializedUnifiedGuardrailsFile,
    ) -> None:
        self.candidates = candidates
        self.context_packages = context_packages
        self.rule_drafts = rule_drafts
        self.guardrails = guardrails

    def bytes_by_logical_name(self) -> dict[str, bytes]:
        return {
            _LOGICAL_NAME_CANDIDATES: self.candidates.to_stable_json().encode("utf-8"),
            _LOGICAL_NAME_CONTEXT_PACKAGES: self.context_packages.to_stable_json().encode("utf-8"),
            _LOGICAL_NAME_RULE_DRAFTS: self.rule_drafts.to_stable_json().encode("utf-8"),
            _LOGICAL_NAME_GUARDRAILS: self.guardrails.to_stable_json().encode("utf-8"),
        }


def build_unified_generation(
    *,
    evaluation: UnifiedActivationEvaluationArtifact,
    downstream: UnifiedShadowDownstreamArtifact,
    authorization: UnifiedMaterializationAuthorization,
    run_id: str,
    source_package_hash: str,
    activation_evaluation_hash: str,
    authorization_hash: str,
    fallback_generation_id: str,
) -> tuple[MaterializedGenerationManifest, UnifiedGenerationFiles]:
    """Punto de entrada PURO. Lanza `UnifiedMaterializationError` si
    `approved_group_ids` esta vacio, si algun grupo aprobado no existe
    o no cumple las cuatro condiciones del docstring del modulo --
    nunca materializa una generacion parcial."""
    kind = _KIND_BY_ACTION.get(authorization.action)
    if kind is None:
        raise UnifiedMaterializationError(
            f"build_unified_generation no aplica a action={authorization.action.value}"
        )
    if not authorization.approved_group_ids:
        raise UnifiedMaterializationError(
            f"action={authorization.action.value} exige al menos un approved_group_id"
        )
    if evaluation.candidate_v1_artifact_hash is None:
        raise UnifiedMaterializationError(
            "no se puede materializar unified sin candidate_v1_artifact_hash resoluble "
            "en la evaluacion de Fase 14A"
        )

    unified_reference_by_group = {r.group_id: r for r in evaluation.unified_references}
    group_result_by_id = {gr.group_id: gr for gr in downstream.group_results}
    context_by_record_id = {c.record_id: c for c in downstream.context_packages}
    draft_by_record_id = {d.record_id: d for d in downstream.rule_drafts}
    guardrail_by_record_id = {g.record_id: g for g in downstream.guardrail_results}

    candidates = []
    context_packages = []
    rule_drafts = []
    guardrails = []

    for group_id in authorization.approved_group_ids:
        reference = unified_reference_by_group.get(group_id)
        if reference is None:
            raise UnifiedMaterializationError(
                f"grupo aprobado {group_id!r} no existe en la evaluacion de Fase 14A"
            )
        if (
            reference.level != UnifiedActivationComparisonLevel.RULE
            or reference.guardrail_status != UnifiedShadowGuardrailStatus.PASSED
        ):
            raise UnifiedMaterializationError(
                f"grupo aprobado {group_id!r} no tiene una regla unified con guardrail PASSED"
            )

        group_result = group_result_by_id.get(group_id)
        if group_result is None:
            raise UnifiedMaterializationError(
                f"grupo aprobado {group_id!r} no existe en el downstream de Fase 13"
            )
        if group_result.execution_status != UnifiedShadowDownstreamExecutionStatus.EXECUTED:
            raise UnifiedMaterializationError(
                f"grupo aprobado {group_id!r} no fue EXECUTED "
                f"(execution_status={group_result.execution_status.value})"
            )
        if (
            group_result.context_package_record_id is None
            or group_result.rule_draft_record_id is None
            or group_result.guardrail_record_id is None
        ):
            raise UnifiedMaterializationError(
                f"grupo aprobado {group_id!r}: registros downstream incompletos"
            )

        context_record = context_by_record_id.get(group_result.context_package_record_id)
        draft_record = draft_by_record_id.get(group_result.rule_draft_record_id)
        guardrail_record = guardrail_by_record_id.get(group_result.guardrail_record_id)
        if context_record is None or draft_record is None or guardrail_record is None:
            raise UnifiedMaterializationError(
                f"grupo aprobado {group_id!r}: registros downstream referenciados ausentes"
            )
        if guardrail_record.status != UnifiedShadowGuardrailStatus.PASSED:
            raise UnifiedMaterializationError(
                f"grupo aprobado {group_id!r}: guardrail real no PASSED "
                f"(status={guardrail_record.status.value})"
            )

        candidates.append(reference)
        context_packages.append(context_record)
        rule_drafts.append(draft_record)
        guardrails.append(guardrail_record)

    candidates.sort(key=lambda r: r.reference_id)
    context_packages.sort(key=lambda r: r.record_id)
    rule_drafts.sort(key=lambda r: r.record_id)
    guardrails.sort(key=lambda r: r.record_id)

    files_bundle = UnifiedGenerationFiles(
        candidates=MaterializedUnifiedCandidatesFile(run_id=run_id, candidates=candidates),
        context_packages=MaterializedUnifiedContextPackagesFile(
            run_id=run_id, context_packages=context_packages
        ),
        rule_drafts=MaterializedUnifiedRuleDraftsFile(run_id=run_id, rule_drafts=rule_drafts),
        guardrails=MaterializedUnifiedGuardrailsFile(run_id=run_id, guardrails=guardrails),
    )
    file_bytes_by_logical_name = files_bundle.bytes_by_logical_name()
    file_hashes = {
        name: hashlib.sha256(data).hexdigest() for name, data in file_bytes_by_logical_name.items()
    }
    record_count_by_logical_name = {
        _LOGICAL_NAME_CANDIDATES: len(candidates),
        _LOGICAL_NAME_CONTEXT_PACKAGES: len(context_packages),
        _LOGICAL_NAME_RULE_DRAFTS: len(rule_drafts),
        _LOGICAL_NAME_GUARDRAILS: len(guardrails),
    }

    generation_id = compute_generation_id(
        lane=MaterializedActivationLane.UNIFIED,
        kind=kind,
        run_id=run_id,
        file_hashes=file_hashes,
    )

    files = [
        MaterializedFileReference(
            logical_name=logical_name,
            relative_path=f"activation/generations/{generation_id}/{logical_name}.json",
            sha256=file_hashes[logical_name],
            byte_size=len(file_bytes_by_logical_name[logical_name]),
            record_count=record_count_by_logical_name[logical_name],
        )
        for logical_name in sorted(file_hashes)
    ]

    manifest = MaterializedGenerationManifest(
        generation_id=generation_id,
        run_id=run_id,
        lane=MaterializedActivationLane.UNIFIED,
        kind=kind,
        source_package_hash=source_package_hash,
        activation_evaluation_hash=activation_evaluation_hash,
        authorization_hash=authorization_hash,
        candidate_v1_artifact_hash=evaluation.candidate_v1_artifact_hash,
        unified_candidates_shadow_hash=evaluation.unified_candidates_shadow_hash,
        validation_report_hash=evaluation.validation_report_hash,
        downstream_artifact_hash=evaluation.downstream_artifact_hash,
        approved_group_ids=sorted(authorization.approved_group_ids),
        files=files,
        fallback_generation_id=fallback_generation_id,
    )
    return manifest, files_bundle


__all__ = ["UnifiedGenerationFiles", "build_unified_generation"]
