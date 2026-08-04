"""Servicio read-only del read model de gobierno operativo (Fase 15A
Parte 4-5, `feat/operational-governance-ui`).

Construye `OperationalGovernanceOverview` en memoria a partir de
`run.json`, `activation/active.json`, `activation/generations/*`,
`activation/events/*` y (cuando existe) `diagnostics/unified-
activation-evaluation.json` -- SIN modificar ninguno de ellos: cero
`mkdir`, cero escritura atomica, cero `unlink`/`rename`/`replace`, cero
lock, cero transicion, cero fallback ejecutable, cero rollback.

Resuelve artifacts EXCLUSIVAMENTE via `unified_active_lane_router.
resolve_active_artifact` -- NUNCA `resolve_with_fallback` (Fase 15A
Parte 4, items 16-17: un GET jamas ejecuta un fallback real). Reutiliza
`ActiveArtifactResolver` unicamente por su `.store` (patron opt-in ya
establecido en Fase 14B) -- nunca se llama a `.resolve()`/
`.resolve_path()` de esa clase, porque internamente invocan el
fallback ejecutable.

Un error en UN componente (una generacion corrupta, un evento
huerfano, un archivo no reconciliado, un grupo unified ilegible) se
convierte en un `GovernanceIssue` tipado sin perder el resto del
overview -- salvo que el run mismo (`run.json`) sea ilegible, unico
caso en que se propaga `OperationalGovernanceReadError`."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..contracts.operational_governance import (
    GovernanceActivationReadinessSummary,
    GovernanceArtifactStatus,
    GovernanceArtifactSummary,
    GovernanceEventChainStatus,
    GovernanceEventSummary,
    GovernanceGenerationReachability,
    GovernanceGenerationSummary,
    GovernanceIntegrityStatus,
    GovernanceIssue,
    GovernanceIssueCode,
    GovernanceIssueSeverity,
    GovernanceUnifiedGroupSummary,
    OperationalGovernanceOverview,
    OperationalGovernanceStatus,
)
from ..contracts.run_state import RunState
from ..contracts.unified_activation_evaluation import UnifiedActivationEvaluationArtifact
from ..contracts.unified_activation_materialization import (
    ActivationResolutionStatus,
    ActivationTransitionEvent,
    ActiveActivationPointer,
    MaterializedActivationLane,
    MaterializedGenerationManifest,
)
from .active_artifact_resolver import ActiveArtifactResolver
from .errors import UnifiedActivationStoreError
from .operational_governance_group_adapter import (
    OperationalGovernanceGroupAdapterError,
    build_unified_group_summaries,
)
from .unified_activation_store import UnifiedActivationStore
from .unified_active_lane_router import KNOWN_LOGICAL_NAMES, resolve_active_artifact

_EVALUATION_RELATIVE_PATH = "diagnostics/unified-activation-evaluation.json"


class OperationalGovernanceReadError(Exception):
    """El run mismo es ilegible (`run_dir` ausente o `run.json`
    corrupto) -- UNICO caso en que el reader no puede producir ningun
    overview, ni siquiera uno `BLOCKED`."""


_ISSUE_MESSAGES: dict[GovernanceIssueCode, str] = {
    GovernanceIssueCode.ACTIVATION_NOT_INITIALIZED: (
        "la activacion (Fase 14B) todavia no se inicializo para este run"
    ),
    GovernanceIssueCode.ACTIVE_POINTER_INVALID: (
        "activation/active.json existe pero es ilegible o invalido"
    ),
    GovernanceIssueCode.ACTIVE_GENERATION_MISSING: (
        "la generacion activa referenciada por active.json no existe en disco"
    ),
    GovernanceIssueCode.ACTIVE_MANIFEST_INVALID: (
        "el manifiesto de la generacion activa es ilegible o invalido"
    ),
    GovernanceIssueCode.ACTIVE_MANIFEST_HASH_MISMATCH: (
        "el hash del manifiesto de la generacion activa no coincide con active.json"
    ),
    GovernanceIssueCode.ACTIVE_FILE_MISSING: (
        "un archivo de la generacion activa referenciado por su manifiesto no existe en disco"
    ),
    GovernanceIssueCode.ACTIVE_FILE_HASH_MISMATCH: (
        "un archivo de la generacion activa no reconcilia con el hash declarado en su manifiesto"
    ),
    GovernanceIssueCode.FALLBACK_GENERATION_MISSING: (
        "la generacion de fallback referenciada por active.json no existe en disco"
    ),
    GovernanceIssueCode.FALLBACK_NOT_V1: "la generacion de fallback declarada no es V1",
    GovernanceIssueCode.EVENT_CHAIN_EMPTY: "la cadena de eventos confirmada esta vacia",
    GovernanceIssueCode.EVENT_CHAIN_BROKEN: (
        "la cadena de eventos confirmada tiene un enlace roto o inconsistente"
    ),
    GovernanceIssueCode.EVENT_CHAIN_CYCLE: (
        "se detecto un ciclo al reconstruir la cadena de eventos confirmada"
    ),
    GovernanceIssueCode.EVENT_SEQUENCE_INVALID: (
        "la secuencia de un evento en la cadena confirmada no decrece en uno"
    ),
    GovernanceIssueCode.EVENT_POINTER_MISMATCH: (
        "el evento mas reciente no confirma la generacion activa declarada por active.json"
    ),
    GovernanceIssueCode.ORPHAN_GENERATION: (
        "existe una generacion persistida sin ninguna referencia activa ni historica"
    ),
    GovernanceIssueCode.ORPHAN_EVENT: (
        "existe un evento persistido no alcanzable desde active.json.latest_event_id"
    ),
    GovernanceIssueCode.ARTIFACT_NOT_AVAILABLE_IN_LANE: (
        "el artefacto solicitado no existe legitimamente en el lane activo"
    ),
    GovernanceIssueCode.ARTIFACT_CORRUPT: (
        "un artefacto del lane activo esta corrupto o no reconcilia con su manifiesto"
    ),
    GovernanceIssueCode.ACTIVATION_EVALUATION_MISSING: (
        "diagnostics/unified-activation-evaluation.json (Fase 14A) no existe para este run"
    ),
    GovernanceIssueCode.FUNCTIONAL_VALIDATION_NOT_AVAILABLE: (
        "la validacion mostrada aqui es estructural: nunca equivale a una validacion funcional"
    ),
    GovernanceIssueCode.USER_AUTHENTICATION_NOT_AVAILABLE: (
        "esta aplicacion no implementa autenticacion de usuario"
    ),
    GovernanceIssueCode.WRITE_OPERATIONS_DISABLED: (
        "las operaciones de activacion/fallback/rollback solo estan disponibles via CLI "
        "con autorizacion explicita -- esta interfaz es exclusivamente de lectura"
    ),
}


def _issue(
    code: GovernanceIssueCode, severity: GovernanceIssueSeverity, references: list[str]
) -> GovernanceIssue:
    ordered_refs = sorted(set(references))
    suffix = "|".join(ordered_refs) if ordered_refs else "-"
    return GovernanceIssue(
        issue_id=f"issue::{code.value}::{suffix}",
        severity=severity,
        code=code,
        message=_ISSUE_MESSAGES[code],
        references=ordered_refs,
    )


def _dedupe_issues(issues: list[GovernanceIssue]) -> list[GovernanceIssue]:
    """Dos rutas de deteccion distintas pueden legitimamente producir
    el MISMO hallazgo -- p. ej. una generacion V1 que es simultaneamente
    su propia `fallback_generation_id` (el caso normal) y esta
    corrupta: el chequeo de la generacion activa y el chequeo de la
    generacion de fallback detectan la misma corrupcion real y
    producen el mismo `issue_id` determinista. `OperationalGovernance
    Overview` exige `issues` sin duplicados (Parte 3): se deduplica
    aqui, preservando la PRIMERA aparicion, en vez de exigirle a cada
    punto de deteccion que sepa de los demas."""
    seen: set[str] = set()
    deduped: list[GovernanceIssue] = []
    for issue in issues:
        if issue.issue_id in seen:
            continue
        seen.add(issue.issue_id)
        deduped.append(issue)
    return deduped


def _structural_disclosure_issues() -> list[GovernanceIssue]:
    """Issues SIEMPRE presentes, independientes del estado del run --
    la UI (Fase 15A Parte 8, seccion I) exige mostrar estos avisos de
    forma visible en TODO momento, nunca solo cuando algo falla."""
    return [
        _issue(
            GovernanceIssueCode.USER_AUTHENTICATION_NOT_AVAILABLE, GovernanceIssueSeverity.INFO, []
        ),
        _issue(GovernanceIssueCode.WRITE_OPERATIONS_DISABLED, GovernanceIssueSeverity.INFO, []),
        _issue(
            GovernanceIssueCode.FUNCTIONAL_VALIDATION_NOT_AVAILABLE,
            GovernanceIssueSeverity.INFO,
            [],
        ),
    ]


def _hash_manifest(manifest: MaterializedGenerationManifest) -> str:
    return hashlib.sha256(manifest.to_stable_json().encode("utf-8")).hexdigest()


def _read_run_state(run_dir: Path) -> RunState:
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise OperationalGovernanceReadError(f"run inexistente: {run_dir.name!r}")
    run_json_path = run_dir / "run.json"
    if run_json_path.is_symlink() or not run_json_path.is_file():
        raise OperationalGovernanceReadError("run.json ausente")
    try:
        return RunState.model_validate_json(run_json_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise OperationalGovernanceReadError("run.json invalido") from exc


def _generation_integrity(
    store: UnifiedActivationStore, generation_id: str
) -> tuple[MaterializedGenerationManifest | None, GovernanceIntegrityStatus]:
    if not store.generation_exists(generation_id):
        return None, GovernanceIntegrityStatus.MISSING
    try:
        manifest = store.read_generation_manifest(generation_id)
    except UnifiedActivationStoreError:
        return None, GovernanceIntegrityStatus.INVALID
    return manifest, GovernanceIntegrityStatus.VALID


def _enumerate_generation_ids(store: UnifiedActivationStore) -> list[str]:
    generations_dir = store.activation_dir / "generations"
    if not generations_dir.is_dir():
        return []
    ids: list[str] = []
    for entry in sorted(generations_dir.iterdir()):
        if entry.is_symlink() or not entry.is_dir():
            continue
        if entry.name.startswith(".tmp-"):
            continue
        ids.append(entry.name)
    return ids


def _enumerate_event_ids(store: UnifiedActivationStore) -> list[str]:
    events_dir = store.activation_dir / "events"
    if not events_dir.is_dir():
        return []
    ids: list[str] = []
    for entry in sorted(events_dir.iterdir()):
        if entry.is_symlink() or not entry.is_file() or entry.suffix != ".json":
            continue
        ids.append(entry.stem)
    return ids


def _walk_confirmed_chain(
    store: UnifiedActivationStore,
    pointer: ActiveActivationPointer,
    run_id: str,
    issues: list[GovernanceIssue],
) -> tuple[list[ActivationTransitionEvent], GovernanceEventChainStatus]:
    """Reconstruccion segura (Fase 15A Parte 5): `visited` evita
    loops; valida `sequence` decreciente en uno, `run_id`, y que el
    evento mas reciente confirme la generacion activa. Nunca repara,
    nunca borra -- solo clasifica."""
    chain: list[ActivationTransitionEvent] = []
    visited: set[str] = set()
    status = GovernanceEventChainStatus.VALID
    current_id: str | None = pointer.latest_event_id
    expected_sequence: int | None = None

    while current_id is not None:
        if current_id in visited:
            issues.append(
                _issue(
                    GovernanceIssueCode.EVENT_CHAIN_CYCLE,
                    GovernanceIssueSeverity.BLOCKING,
                    [current_id],
                )
            )
            status = GovernanceEventChainStatus.CYCLIC
            break
        visited.add(current_id)

        if not store.event_exists(current_id):
            issues.append(
                _issue(
                    GovernanceIssueCode.EVENT_CHAIN_BROKEN,
                    GovernanceIssueSeverity.BLOCKING,
                    [current_id],
                )
            )
            status = GovernanceEventChainStatus.BROKEN
            break
        try:
            event = store.read_event(current_id)
        except UnifiedActivationStoreError:
            issues.append(
                _issue(
                    GovernanceIssueCode.EVENT_CHAIN_BROKEN,
                    GovernanceIssueSeverity.BLOCKING,
                    [current_id],
                )
            )
            status = GovernanceEventChainStatus.BROKEN
            break

        if event.run_id != run_id:
            issues.append(
                _issue(
                    GovernanceIssueCode.EVENT_CHAIN_BROKEN,
                    GovernanceIssueSeverity.BLOCKING,
                    [current_id],
                )
            )
            status = GovernanceEventChainStatus.BROKEN
            break

        if expected_sequence is not None and event.sequence != expected_sequence:
            issues.append(
                _issue(
                    GovernanceIssueCode.EVENT_SEQUENCE_INVALID,
                    GovernanceIssueSeverity.ERROR,
                    [current_id],
                )
            )
            if status == GovernanceEventChainStatus.VALID:
                status = GovernanceEventChainStatus.BROKEN

        chain.append(event)
        expected_sequence = event.sequence - 1
        current_id = event.previous_event_id

    if status == GovernanceEventChainStatus.VALID and chain and chain[-1].sequence != 1:
        issues.append(
            _issue(
                GovernanceIssueCode.EVENT_CHAIN_BROKEN,
                GovernanceIssueSeverity.ERROR,
                [chain[-1].event_id],
            )
        )
        status = GovernanceEventChainStatus.BROKEN

    if (
        status == GovernanceEventChainStatus.VALID
        and chain
        and chain[0].to_generation_id != pointer.active_generation_id
    ):
        issues.append(
            _issue(
                GovernanceIssueCode.EVENT_POINTER_MISMATCH,
                GovernanceIssueSeverity.ERROR,
                [chain[0].event_id],
            )
        )
        status = GovernanceEventChainStatus.BROKEN

    if not chain and status == GovernanceEventChainStatus.VALID:
        # Solo una cadena LEGITIMAMENTE vacia (nunca alcanzable en la
        # practica una vez que existe un puntero -- `latest_event_id`
        # siempre exige un evento real) se reclasifica como EMPTY. Si
        # `chain` quedo vacia porque el PRIMER evento fallo (ciclo,
        # ausencia, run_id incorrecto), `status` ya es BROKEN/CYCLIC y
        # NUNCA se sobrescribe: perder esa clasificacion ocultaria la
        # razon real detras de un rotulo generico.
        status = GovernanceEventChainStatus.EMPTY
        issues.append(
            _issue(GovernanceIssueCode.EVENT_CHAIN_EMPTY, GovernanceIssueSeverity.BLOCKING, [])
        )

    return chain, status


def _classify_reachability(
    generation_id: str,
    pointer: ActiveActivationPointer,
    confirmed_chain: list[ActivationTransitionEvent],
) -> GovernanceGenerationReachability:
    if generation_id == pointer.active_generation_id:
        return GovernanceGenerationReachability.ACTIVE
    if (
        pointer.previous_generation_id is not None
        and generation_id == pointer.previous_generation_id
    ):
        return GovernanceGenerationReachability.PREVIOUS
    if generation_id == pointer.fallback_generation_id:
        return GovernanceGenerationReachability.FALLBACK
    historical_ids: set[str] = set()
    for event in confirmed_chain:
        historical_ids.add(event.to_generation_id)
        if event.from_generation_id is not None:
            historical_ids.add(event.from_generation_id)
    if generation_id in historical_ids:
        return GovernanceGenerationReachability.HISTORICAL
    return GovernanceGenerationReachability.ORPHAN


def _build_generation_summary(
    store: UnifiedActivationStore,
    generation_id: str,
    pointer: ActiveActivationPointer,
    confirmed_chain: list[ActivationTransitionEvent],
    issues: list[GovernanceIssue],
) -> GovernanceGenerationSummary:
    manifest, integrity = _generation_integrity(store, generation_id)
    reachability = _classify_reachability(generation_id, pointer, confirmed_chain)

    if reachability == GovernanceGenerationReachability.ORPHAN:
        issues.append(
            _issue(
                GovernanceIssueCode.ORPHAN_GENERATION,
                GovernanceIssueSeverity.WARNING,
                [generation_id],
            )
        )

    if manifest is None:
        return GovernanceGenerationSummary(
            generation_id=generation_id,
            reachability=reachability,
            manifest_integrity=integrity,
            file_count=0,
        )

    files = sorted(f.logical_name for f in manifest.files)
    return GovernanceGenerationSummary(
        generation_id=generation_id,
        lane=manifest.lane,
        kind=manifest.kind,
        reachability=reachability,
        manifest_integrity=integrity,
        manifest_hash=_hash_manifest(manifest),
        approved_group_ids=list(manifest.approved_group_ids),
        file_count=len(files),
        files=files,
        fallback_generation_id=manifest.fallback_generation_id,
    )


def _build_artifact_summary(
    store: UnifiedActivationStore,
    run_dir: Path,
    run_id: str,
    logical_name: str,
    active_manifest: MaterializedGenerationManifest | None,
    issues: list[GovernanceIssue],
) -> GovernanceArtifactSummary:
    resolution = resolve_active_artifact(store, run_id=run_id, logical_name=logical_name)

    if resolution.status == ActivationResolutionStatus.RESOLVED:
        file_reference = None
        if active_manifest is not None:
            file_reference = next(
                (f for f in active_manifest.files if f.logical_name == logical_name), None
            )
        return GovernanceArtifactSummary(
            logical_name=logical_name,
            status=GovernanceArtifactStatus.AVAILABLE,
            resolved_lane=resolution.resolved_lane,
            generation_id=resolution.generation_id,
            relative_path=resolution.relative_path,
            sha256=resolution.sha256,
            byte_size=file_reference.byte_size if file_reference is not None else None,
            record_count=file_reference.record_count if file_reference is not None else None,
            schema_version=file_reference.schema_version if file_reference is not None else None,
            downloadable=True,
        )

    if resolution.status == ActivationResolutionStatus.NOT_AVAILABLE_IN_LANE:
        issues.append(
            _issue(
                GovernanceIssueCode.ARTIFACT_NOT_AVAILABLE_IN_LANE,
                GovernanceIssueSeverity.INFO,
                [logical_name, resolution.generation_id],
            )
        )
        return GovernanceArtifactSummary(
            logical_name=logical_name,
            status=GovernanceArtifactStatus.NOT_AVAILABLE_IN_LANE,
            resolved_lane=resolution.resolved_lane,
            generation_id=resolution.generation_id,
            downloadable=False,
        )

    # BLOCKED: subclasificar MISSING vs CORRUPT re-verificando el
    # filesystem real (solo lectura -- open/read_bytes, nunca escritura).
    artifact_status = GovernanceArtifactStatus.BLOCKED
    if resolution.relative_path is not None:
        candidate_path = run_dir / resolution.relative_path
        if not candidate_path.is_file() or candidate_path.is_symlink():
            artifact_status = GovernanceArtifactStatus.MISSING
            issues.append(
                _issue(
                    GovernanceIssueCode.ACTIVE_FILE_MISSING,
                    GovernanceIssueSeverity.ERROR,
                    [logical_name, resolution.generation_id],
                )
            )
        else:
            artifact_status = GovernanceArtifactStatus.CORRUPT
            issues.append(
                _issue(
                    GovernanceIssueCode.ACTIVE_FILE_HASH_MISMATCH,
                    GovernanceIssueSeverity.ERROR,
                    [logical_name, resolution.generation_id],
                )
            )
    else:
        issues.append(
            _issue(
                GovernanceIssueCode.ARTIFACT_CORRUPT,
                GovernanceIssueSeverity.ERROR,
                [logical_name, resolution.generation_id],
            )
        )

    return GovernanceArtifactSummary(
        logical_name=logical_name,
        status=artifact_status,
        resolved_lane=resolution.resolved_lane,
        generation_id=resolution.generation_id,
        relative_path=resolution.relative_path,
        sha256=resolution.sha256,
        downloadable=False,
    )


def _load_readiness(
    run_dir: Path, run_id: str, issues: list[GovernanceIssue]
) -> GovernanceActivationReadinessSummary | None:
    path = run_dir / _EVALUATION_RELATIVE_PATH
    if path.is_symlink() or not path.is_file():
        issues.append(
            _issue(
                GovernanceIssueCode.ACTIVATION_EVALUATION_MISSING, GovernanceIssueSeverity.INFO, []
            )
        )
        return None
    try:
        evaluation = UnifiedActivationEvaluationArtifact.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except ValueError:
        issues.append(
            _issue(
                GovernanceIssueCode.ACTIVATION_EVALUATION_MISSING,
                GovernanceIssueSeverity.WARNING,
                [],
            )
        )
        return None
    if evaluation.run_id != run_id:
        issues.append(
            _issue(
                GovernanceIssueCode.ACTIVATION_EVALUATION_MISSING,
                GovernanceIssueSeverity.WARNING,
                [],
            )
        )
        return None

    summary = evaluation.summary
    canary_selected = evaluation.canary_selection.selected if evaluation.canary_selection else None
    return GovernanceActivationReadinessSummary(
        mode=evaluation.mode.value,
        requested_lane=evaluation.requested_lane.value,
        effective_lane=evaluation.effective_lane.value,
        fallback_lane=evaluation.fallback_lane.value,
        readiness_disposition=evaluation.readiness_disposition.value,
        activation_decision=evaluation.activation_decision.value,
        canary_selected=canary_selected,
        materialization_enabled=evaluation.materialization_enabled,
        exact_equivalent_count=summary.exact_equivalent_count,
        unified_additive_count=summary.unified_additive_count,
        v1_only_count=summary.v1_only_count,
        conflicting_count=summary.conflicting_count,
        blocking_issue_count=summary.blocking_issue_count,
    )


def _build_unified_groups(
    store: UnifiedActivationStore,
    manifest: MaterializedGenerationManifest | None,
    issues: list[GovernanceIssue],
) -> list[GovernanceUnifiedGroupSummary]:
    if manifest is None or manifest.lane != MaterializedActivationLane.UNIFIED:
        return []
    try:
        return build_unified_group_summaries(store, manifest)
    except OperationalGovernanceGroupAdapterError:
        issues.append(
            _issue(
                GovernanceIssueCode.ARTIFACT_CORRUPT,
                GovernanceIssueSeverity.ERROR,
                [manifest.generation_id],
            )
        )
        return []


def _compute_status(
    *,
    pointer: ActiveActivationPointer | None,
    active_manifest_integrity: GovernanceIntegrityStatus,
    active_lane: MaterializedActivationLane | None,
    event_chain_status: GovernanceEventChainStatus,
    issues: list[GovernanceIssue],
    orphan_generation_count: int,
    orphan_event_count: int,
) -> OperationalGovernanceStatus:
    if pointer is None:
        return OperationalGovernanceStatus.NOT_INITIALIZED
    if active_manifest_integrity != GovernanceIntegrityStatus.VALID:
        return OperationalGovernanceStatus.BLOCKED
    if any(issue.severity == GovernanceIssueSeverity.BLOCKING for issue in issues):
        return OperationalGovernanceStatus.BLOCKED
    degraded = (
        event_chain_status != GovernanceEventChainStatus.VALID
        or orphan_generation_count > 0
        or orphan_event_count > 0
        or any(issue.severity == GovernanceIssueSeverity.ERROR for issue in issues)
    )
    if degraded:
        return OperationalGovernanceStatus.DEGRADED
    if active_lane == MaterializedActivationLane.UNIFIED:
        return OperationalGovernanceStatus.HEALTHY_UNIFIED
    return OperationalGovernanceStatus.HEALTHY_V1


def build_operational_governance_overview(
    run_dir: Path, run_id: str
) -> OperationalGovernanceOverview:
    """Punto de entrada unico del reader (Fase 15A Parte 4). Solo
    lectura de principio a fin: nunca crea `activation/`, nunca
    adquiere el lock, nunca transiciona, nunca ejecuta fallback."""
    state = _read_run_state(run_dir)
    issues: list[GovernanceIssue] = _structural_disclosure_issues()

    store = ActiveArtifactResolver(run_dir, run_id=run_id).store

    pointer: ActiveActivationPointer | None
    try:
        pointer = store.read_active_pointer()
    except UnifiedActivationStoreError:
        pointer = None
        issues.append(
            _issue(GovernanceIssueCode.ACTIVE_POINTER_INVALID, GovernanceIssueSeverity.BLOCKING, [])
        )

    if pointer is None:
        if not any(issue.code == GovernanceIssueCode.ACTIVE_POINTER_INVALID for issue in issues):
            issues.append(
                _issue(
                    GovernanceIssueCode.ACTIVATION_NOT_INITIALIZED, GovernanceIssueSeverity.INFO, []
                )
            )
        issues = sorted(_dedupe_issues(issues), key=lambda issue: issue.issue_id)
        pointer_is_corrupt = any(
            issue.code == GovernanceIssueCode.ACTIVE_POINTER_INVALID for issue in issues
        )
        status = (
            OperationalGovernanceStatus.BLOCKED
            if pointer_is_corrupt
            else OperationalGovernanceStatus.NOT_INITIALIZED
        )
        return OperationalGovernanceOverview(
            run_id=run_id,
            run_stage=state.current_stage.value,
            # `activation/active.json` EXISTE pero es ilegible (corrupto)
            # cuenta como "inicializada" -- su sola presencia demuestra
            # que una transicion real ya ocurrio; `activation_
            # initialized=False` se reserva exclusivamente para la
            # ausencia LEGITIMA (el archivo nunca se escribio).
            activation_initialized=pointer_is_corrupt,
            status=status,
            event_chain_status=GovernanceEventChainStatus.EMPTY,
            event_chain_length=0,
            generation_count=0,
            confirmed_event_count=0,
            orphan_generation_count=0,
            orphan_event_count=0,
            active_manifest_integrity=GovernanceIntegrityStatus.NOT_APPLICABLE,
            issues=issues,
        )

    active_manifest, active_integrity = _generation_integrity(store, pointer.active_generation_id)
    if active_manifest is None:
        code = (
            GovernanceIssueCode.ACTIVE_GENERATION_MISSING
            if active_integrity == GovernanceIntegrityStatus.MISSING
            else GovernanceIssueCode.ACTIVE_MANIFEST_INVALID
        )
        issues.append(
            _issue(code, GovernanceIssueSeverity.BLOCKING, [pointer.active_generation_id])
        )
    elif _hash_manifest(active_manifest) != pointer.active_generation_manifest_hash:
        active_integrity = GovernanceIntegrityStatus.INVALID
        issues.append(
            _issue(
                GovernanceIssueCode.ACTIVE_MANIFEST_HASH_MISMATCH,
                GovernanceIssueSeverity.BLOCKING,
                [pointer.active_generation_id],
            )
        )
        active_manifest = None

    fallback_manifest, fallback_integrity = _generation_integrity(
        store, pointer.fallback_generation_id
    )
    if fallback_manifest is None:
        code = (
            GovernanceIssueCode.FALLBACK_GENERATION_MISSING
            if fallback_integrity == GovernanceIntegrityStatus.MISSING
            else GovernanceIssueCode.ACTIVE_MANIFEST_INVALID
        )
        issues.append(_issue(code, GovernanceIssueSeverity.ERROR, [pointer.fallback_generation_id]))
    elif fallback_manifest.lane != MaterializedActivationLane.V1:
        issues.append(
            _issue(
                GovernanceIssueCode.FALLBACK_NOT_V1,
                GovernanceIssueSeverity.ERROR,
                [pointer.fallback_generation_id],
            )
        )

    confirmed_chain, chain_status = _walk_confirmed_chain(store, pointer, run_id, issues)

    generation_ids = _enumerate_generation_ids(store)
    generations = [
        _build_generation_summary(store, gid, pointer, confirmed_chain, issues)
        for gid in generation_ids
    ]
    generations.sort(key=lambda g: g.generation_id)
    orphan_generation_count = sum(
        1 for g in generations if g.reachability == GovernanceGenerationReachability.ORPHAN
    )

    confirmed_ids = {event.event_id for event in confirmed_chain}
    event_ids = _enumerate_event_ids(store)
    events: list[GovernanceEventSummary] = []
    for event_id in event_ids:
        if event_id in confirmed_ids:
            continue  # se agregan mas abajo, en el orden de la cadena confirmada
        try:
            event = store.read_event(event_id)
        except UnifiedActivationStoreError:
            continue  # evento ilegible: no se puede representar de forma segura, se omite
        issues.append(
            _issue(GovernanceIssueCode.ORPHAN_EVENT, GovernanceIssueSeverity.WARNING, [event_id])
        )
        events.append(
            GovernanceEventSummary(
                event_id=event.event_id,
                sequence=event.sequence,
                action=event.action.value,
                from_generation_id=event.from_generation_id,
                to_generation_id=event.to_generation_id,
                resulting_lane=event.resulting_lane,
                previous_event_id=event.previous_event_id,
                confirmed=False,
            )
        )
    for event in confirmed_chain:
        events.append(
            GovernanceEventSummary(
                event_id=event.event_id,
                sequence=event.sequence,
                action=event.action.value,
                from_generation_id=event.from_generation_id,
                to_generation_id=event.to_generation_id,
                resulting_lane=event.resulting_lane,
                previous_event_id=event.previous_event_id,
                confirmed=True,
            )
        )
    events.sort(key=lambda e: (e.sequence, e.event_id))
    orphan_event_count = sum(1 for e in events if not e.confirmed)
    confirmed_event_count = sum(1 for e in events if e.confirmed)

    artifacts = [
        _build_artifact_summary(store, run_dir, run_id, logical_name, active_manifest, issues)
        for logical_name in sorted(KNOWN_LOGICAL_NAMES)
    ]

    readiness = _load_readiness(run_dir, run_id, issues)
    unified_groups = _build_unified_groups(store, active_manifest, issues)

    active_lane = active_manifest.lane if active_manifest is not None else pointer.active_lane
    status = _compute_status(
        pointer=pointer,
        active_manifest_integrity=active_integrity,
        active_lane=active_lane,
        event_chain_status=chain_status,
        issues=issues,
        orphan_generation_count=orphan_generation_count,
        orphan_event_count=orphan_event_count,
    )

    issues = sorted(_dedupe_issues(issues), key=lambda issue: issue.issue_id)

    return OperationalGovernanceOverview(
        run_id=run_id,
        run_stage=state.current_stage.value,
        activation_initialized=True,
        status=status,
        active_lane=pointer.active_lane,
        active_generation_id=pointer.active_generation_id,
        active_generation_kind=active_manifest.kind if active_manifest is not None else None,
        pointer_version=pointer.pointer_version,
        previous_generation_id=pointer.previous_generation_id,
        fallback_generation_id=pointer.fallback_generation_id,
        latest_event_id=pointer.latest_event_id,
        event_chain_status=chain_status,
        event_chain_length=confirmed_event_count,
        generation_count=len(generations),
        confirmed_event_count=confirmed_event_count,
        orphan_generation_count=orphan_generation_count,
        orphan_event_count=orphan_event_count,
        active_manifest_integrity=active_integrity,
        readiness=readiness,
        artifacts=artifacts,
        generations=generations,
        events=events,
        unified_groups=unified_groups,
        issues=issues,
    )


__all__ = ["OperationalGovernanceReadError", "build_operational_governance_overview"]
