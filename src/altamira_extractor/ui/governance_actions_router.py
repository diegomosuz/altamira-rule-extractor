"""Endpoints de escritura del gobierno operativo (Fase 15B1 Parte 11,
`feat/final-hardening-release`).

Exclusivamente HTML/formulario -- ninguna API JSON de escritura en esta
subfase:

```
GET  /ui/runs/{run_id}/governance/actions
GET  /ui/runs/{run_id}/governance/actions/{action}
POST /ui/runs/{run_id}/governance/actions/{action}/prepare
GET  /ui/runs/{run_id}/governance/actions/{action}/confirm
POST /ui/runs/{run_id}/governance/actions/{action}/execute
GET  /ui/runs/{run_id}/governance/actions/{action}/result
GET  /ui/runs/{run_id}/governance/audit
```

Cada `POST` resuelve el principal (`security/fastapi_deps.py`), exige
autenticacion/permiso (`security/operational_workflow.py`, que SIEMPRE
revalida -- ocultar un boton en la UI nunca es la unica defensa), exige
CSRF de un solo uso (`security/csrf.py`), valida `run_id`/`action` via
enums cerrados, y NUNCA acepta `provider`/rutas/roles/`principal_id`/
YAML de autorizacion/`skip_validation`/`force` desde el formulario --
esos campos ni siquiera existen en los modelos `Form(...)` de abajo.

Un fallo al registrar un evento de auditoria (`OperationalAuditError`,
p. ej. contencion de lock o I/O) se loguea pero NUNCA oculta al
usuario el resultado de una lectura ya autorizada, ni revierte una
materializacion de Fase 14B ya confirmada (esa transaccion es
independiente y ya esta persistida bajo `activation/` cuando se llega
al punto de auditar el exito)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..api.errors import (
    ApiError,
    InvalidIdentifierError,
    RequestBodyTooLargeError,
    RunNotFoundError,
)
from ..api.validation import validate_run_id
from ..config import Settings
from ..contracts.operational_audit import AuditAction
from ..contracts.operational_authorization_request import OperationalAction
from ..contracts.security_config import OperationalPermission, SecurityConfig
from ..contracts.security_identity import AuthenticatedPrincipal
from ..contracts.unified_materialization_authorization import UnifiedMaterializationReasonCode
from ..pipeline.operational_governance_reader import (
    OperationalGovernanceReadError,
    build_operational_governance_overview,
)
from ..security.correlation import get_correlation_id
from ..security.csrf import (
    CSRF_FORM_FIELD_NAME,
    CsrfRejectedError,
    generate_csrf_token,
    require_csrf,
)
from ..security.fastapi_deps import get_principal, get_security_config, get_session
from ..security.operational_audit_service import (
    OperationalAuditError,
    read_audit_chain,
    record_audit_event,
)
from ..security.operational_challenge import verify_challenge
from ..security.operational_workflow import (
    CandidateUnifiedGroup,
    OperationalWorkflowError,
    distinct_reviewer_required_for,
    execute_operational_action,
    list_candidate_unified_groups,
    prepare_operational_action,
    read_active_pointer_hash,
    read_challenge_for_confirm,
    required_permission_for,
)
from ..security.session import SessionData
from .deps import get_settings, get_templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)

_CHALLENGE_QUERY_PARAM = "challenge_token"

_REQUESTED_AUDIT_ACTION: dict[OperationalAction, AuditAction] = {
    OperationalAction.ACTIVATE_UNIFIED_CANARY: AuditAction.ACTIVATION_CANARY_REQUESTED,
    OperationalAction.ACTIVATE_UNIFIED_PRIMARY: AuditAction.ACTIVATION_PRIMARY_REQUESTED,
    OperationalAction.FALLBACK_TO_V1: AuditAction.FALLBACK_REQUESTED,
    OperationalAction.ROLLBACK_TO_PREVIOUS: AuditAction.ROLLBACK_REQUESTED,
    OperationalAction.ROLLBACK_TO_GENERATION: AuditAction.ROLLBACK_REQUESTED,
}
_SUCCEEDED_AUDIT_ACTION: dict[OperationalAction, AuditAction] = {
    OperationalAction.ACTIVATE_UNIFIED_CANARY: AuditAction.ACTIVATION_CANARY_SUCCEEDED,
    OperationalAction.ACTIVATE_UNIFIED_PRIMARY: AuditAction.ACTIVATION_PRIMARY_SUCCEEDED,
    OperationalAction.FALLBACK_TO_V1: AuditAction.FALLBACK_SUCCEEDED,
    OperationalAction.ROLLBACK_TO_PREVIOUS: AuditAction.ROLLBACK_SUCCEEDED,
    OperationalAction.ROLLBACK_TO_GENERATION: AuditAction.ROLLBACK_SUCCEEDED,
}


_MAX_FORM_BODY_BYTES = 65_536


def _check_form_body_size(request: Request) -> None:
    """Limite de tamano de cuerpo (Fase 15B1 Parte 14) -- estos
    endpoints son SIEMPRE formularios pequenos (motivo, referencia,
    checkboxes de grupos, tokens), nunca archivos. 64 KiB es generoso
    para el maximo razonable de grupos aprobados y sigue acotando un
    POST abusivo. Se basa en `Content-Length` (declarado por el
    cliente) -- una defensa de primera linea, no la unica: Starlette ya
    limita el tamano total leido en memoria al parsear `multipart`/
    `urlencoded`."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError:
            return
        if declared_size > _MAX_FORM_BODY_BYTES:
            raise RequestBodyTooLargeError()


def _run_dir(settings: Settings, run_id: str) -> Path:
    validate_run_id(run_id)
    run_dir = settings.runs_dir / run_id
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise RunNotFoundError()
    return run_dir


def _validate_action(action: str) -> OperationalAction:
    try:
        return OperationalAction(action)
    except ValueError as exc:
        raise InvalidIdentifierError(f"action invalida: {action!r}") from exc


def _validate_reason_code(value: str) -> UnifiedMaterializationReasonCode:
    try:
        return UnifiedMaterializationReasonCode(value)
    except ValueError as exc:
        raise InvalidIdentifierError(f"reason_code invalido: {value!r}") from exc


def _record(
    run_dir: Path,
    run_id: str,
    *,
    action: AuditAction,
    principal: AuthenticatedPrincipal,
    correlation_id: str,
    operational_action: OperationalAction | None = None,
    activation_event_id: str | None = None,
    reason_code: UnifiedMaterializationReasonCode | None = None,
    error_code: str | None = None,
) -> str | None:
    """Retorna el `audit_event_id` persistido, o `None` si el registro
    de auditoria fallo -- un fallo aqui se loguea pero NUNCA oculta al
    usuario el resultado de una operacion ya autorizada/ejecutada."""
    try:
        event = record_audit_event(
            run_dir,
            run_id,
            action=action,
            principal_id=principal.principal_id,
            roles=principal.roles,
            authentication_mode=principal.authentication_mode,
            correlation_id=correlation_id,
            operational_action=operational_action,
            activation_event_id=activation_event_id,
            reason_code=reason_code,
            error_code=error_code,
        )
    except OperationalAuditError:
        logger.exception("fallo al registrar evento de auditoria operativa (run_id=%s)", run_id)
        return None

    logger.info(
        "operational audit event recorded",
        extra={
            "correlation_id": correlation_id,
            "run_id": run_id,
            "action": action.value,
            "operational_action": operational_action.value if operational_action else None,
            "error_code": error_code,
            "audit_event_id": event.audit_event_id,
        },
    )
    return event.audit_event_id


def _visible_actions(principal: AuthenticatedPrincipal) -> list[OperationalAction]:
    """SOLO afecta que botones se muestran -- el backend SIEMPRE
    revalida el permiso especifico en `prepare`/`execute` (Parte 12B)."""
    if not principal.has_permission(OperationalPermission.PREPARE_AUTHORIZATION):
        return []
    return [
        action
        for action in OperationalAction
        if principal.has_permission(required_permission_for(action))
    ]


@router.get(
    "/runs/{run_id}/governance/actions", response_class=HTMLResponse, name="ui_governance_actions"
)
def governance_actions_ui(
    request: Request,
    run_id: str,
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    security_config: SecurityConfig = Depends(get_security_config),
) -> HTMLResponse:
    run_dir = _run_dir(settings, run_id)
    correlation_id = get_correlation_id(request)
    _record(
        run_dir,
        run_id,
        action=AuditAction.LOGIN_CONTEXT_RESOLVED,
        principal=principal,
        correlation_id=correlation_id,
    )
    _pointer, pointer_hash = read_active_pointer_hash(run_dir)
    context = {
        "run_id": run_id,
        "principal": principal,
        "pointer_initialized": pointer_hash is not None,
        "visible_actions": _visible_actions(principal),
        "all_actions": list(OperationalAction),
        "required_permission": {
            a.value: required_permission_for(a).value for a in OperationalAction
        },
    }
    return templates.TemplateResponse(request, "governance_actions.html", context)


@router.get(
    "/runs/{run_id}/governance/actions/{action}",
    response_class=HTMLResponse,
    name="ui_governance_action_form",
)
def governance_action_form_ui(
    request: Request,
    run_id: str,
    action: str,
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    security_config: SecurityConfig = Depends(get_security_config),
    session: SessionData = Depends(get_session),
) -> HTMLResponse:
    run_dir = _run_dir(settings, run_id)
    parsed_action = _validate_action(action)
    correlation_id = get_correlation_id(request)
    if not principal.has_permission(OperationalPermission.PREPARE_AUTHORIZATION):
        _record(
            run_dir,
            run_id,
            action=AuditAction.ACCESS_DENIED,
            principal=principal,
            correlation_id=correlation_id,
            error_code="forbidden",
        )
        raise ApiError("permiso insuficiente", status_code=403, code="forbidden")

    try:
        overview = build_operational_governance_overview(run_dir, run_id)
    except OperationalGovernanceReadError as exc:
        raise RunNotFoundError() from exc

    groups: list[CandidateUnifiedGroup] = []
    if parsed_action in (
        OperationalAction.ACTIVATE_UNIFIED_CANARY,
        OperationalAction.ACTIVATE_UNIFIED_PRIMARY,
    ):
        try:
            groups = list_candidate_unified_groups(run_dir)
        except OperationalWorkflowError:
            groups = []

    _pointer, pointer_hash = read_active_pointer_hash(run_dir)
    csrf_token = generate_csrf_token(session, security_config)
    context = {
        "run_id": run_id,
        "action": parsed_action,
        "principal": principal,
        "distinct_reviewer_required": distinct_reviewer_required_for(
            parsed_action, security_config
        ),
        "pointer_hash": pointer_hash,
        "reason_codes": list(UnifiedMaterializationReasonCode),
        "groups": groups,
        "generations": [g.generation_id for g in overview.generations],
        "csrf_field_name": CSRF_FORM_FIELD_NAME,
        "csrf_token": csrf_token,
    }
    return templates.TemplateResponse(request, "governance_action_form.html", context)


@router.post(
    "/runs/{run_id}/governance/actions/{action}/prepare",
    name="ui_governance_prepare",
    dependencies=[Depends(_check_form_body_size)],
)
async def governance_prepare_ui(
    request: Request,
    run_id: str,
    action: str,
    reason_code: str = Form(...),
    review_reference: str = Form(...),
    approved_group_ids: list[str] = Form(default=[]),
    target_generation_id: str = Form(default=""),
    settings: Settings = Depends(get_settings),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    security_config: SecurityConfig = Depends(get_security_config),
    session: SessionData = Depends(get_session),
) -> Response:
    run_dir = _run_dir(settings, run_id)
    parsed_action = _validate_action(action)
    correlation_id = get_correlation_id(request)

    form = await request.form()
    submitted_csrf = form.get(CSRF_FORM_FIELD_NAME)
    try:
        require_csrf(
            request,
            session=session,
            submitted_token=submitted_csrf if isinstance(submitted_csrf, str) else None,
        )
    except CsrfRejectedError:
        _record(
            run_dir,
            run_id,
            action=AuditAction.CSRF_REJECTED,
            principal=principal,
            correlation_id=correlation_id,
            error_code="csrf_rejected",
        )
        raise

    parsed_reason_code = _validate_reason_code(reason_code)

    try:
        _intent, challenge_token = prepare_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            action=parsed_action,
            principal=principal,
            security_config=security_config,
            session=session,
            reason_code=parsed_reason_code,
            review_reference=review_reference,
            approved_group_ids=list(approved_group_ids),
            target_generation_id=target_generation_id or None,
        )
    except ApiError as exc:
        if exc.status_code == 403:
            _record(
                run_dir,
                run_id,
                action=AuditAction.ACCESS_DENIED,
                principal=principal,
                correlation_id=correlation_id,
                error_code=exc.code,
            )
        else:
            _record(
                run_dir,
                run_id,
                action=AuditAction.AUTHORIZATION_REJECTED,
                principal=principal,
                correlation_id=correlation_id,
                operational_action=parsed_action,
                reason_code=parsed_reason_code,
                error_code=exc.code,
            )
        raise

    _record(
        run_dir,
        run_id,
        action=AuditAction.AUTHORIZATION_PREPARED,
        principal=principal,
        correlation_id=correlation_id,
        operational_action=parsed_action,
        reason_code=parsed_reason_code,
    )

    url = request.app.url_path_for(
        "ui_governance_confirm", run_id=run_id, action=parsed_action.value
    )
    location = f"{url}?{_CHALLENGE_QUERY_PARAM}={challenge_token}"
    return Response(status_code=303, headers={"Location": location})


@router.get(
    "/runs/{run_id}/governance/actions/{action}/confirm",
    response_class=HTMLResponse,
    name="ui_governance_confirm",
)
def governance_confirm_ui(
    request: Request,
    run_id: str,
    action: str,
    challenge_token: str,
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    security_config: SecurityConfig = Depends(get_security_config),
    session: SessionData = Depends(get_session),
) -> HTMLResponse:
    """SOLO lectura -- verifica el challenge y renderiza; nunca ejecuta
    ninguna transicion (un `GET` jamas ejecuta, Parte 8)."""
    _run_dir(settings, run_id)
    _validate_action(action)
    intent = read_challenge_for_confirm(challenge_token, session)

    csrf_token = generate_csrf_token(session, security_config)
    context = {
        "run_id": run_id,
        "intent": intent,
        "principal": principal,
        "challenge_token": challenge_token,
        "csrf_field_name": CSRF_FORM_FIELD_NAME,
        "csrf_token": csrf_token,
    }
    return templates.TemplateResponse(request, "governance_action_confirm.html", context)


@router.post(
    "/runs/{run_id}/governance/actions/{action}/execute",
    name="ui_governance_execute",
    dependencies=[Depends(_check_form_body_size)],
)
async def governance_execute_ui(
    request: Request,
    run_id: str,
    action: str,
    challenge_token: str = Form(...),
    settings: Settings = Depends(get_settings),
    principal: AuthenticatedPrincipal = Depends(get_principal),
    security_config: SecurityConfig = Depends(get_security_config),
    session: SessionData = Depends(get_session),
) -> Response:
    run_dir = _run_dir(settings, run_id)
    parsed_action = _validate_action(action)
    correlation_id = get_correlation_id(request)

    form = await request.form()
    submitted_csrf = form.get(CSRF_FORM_FIELD_NAME)
    try:
        require_csrf(
            request,
            session=session,
            submitted_token=submitted_csrf if isinstance(submitted_csrf, str) else None,
        )
    except CsrfRejectedError:
        _record(
            run_dir,
            run_id,
            action=AuditAction.CSRF_REJECTED,
            principal=principal,
            correlation_id=correlation_id,
            error_code="csrf_rejected",
        )
        raise

    # Revalidacion previa SOLO para conocer accion/reason_code a
    # auditar en el evento *_REQUESTED -- `execute_operational_action`
    # vuelve a revalidar TODO desde cero (Parte 8), esta lectura no se
    # reutiliza para autorizar nada.
    peeked_intent = verify_challenge(challenge_token, session)
    if peeked_intent is not None and peeked_intent.action != parsed_action:
        # El segmento {action} de la URL es solo de presentacion/enrutado
        # -- lo que realmente se ejecuta es SIEMPRE `intent.action`
        # (firmado en el challenge desde `prepare`). Un desajuste aqui
        # nunca debe auditarse bajo una accion distinta a la real.
        raise ApiError(
            "la accion de la URL no coincide con la accion preparada en el challenge",
            status_code=409,
            code="operational_precondition_failed",
        )
    if peeked_intent is not None:
        _record(
            run_dir,
            run_id,
            action=_REQUESTED_AUDIT_ACTION[peeked_intent.action],
            principal=principal,
            correlation_id=correlation_id,
            operational_action=peeked_intent.action,
            reason_code=peeked_intent.reason_code,
        )

    try:
        result = execute_operational_action(
            run_dir=run_dir,
            run_id=run_id,
            challenge_token=challenge_token,
            principal=principal,
            session=session,
        )
    except ApiError as exc:
        if exc.status_code == 403:
            _record(
                run_dir,
                run_id,
                action=AuditAction.ACCESS_DENIED,
                principal=principal,
                correlation_id=correlation_id,
                error_code=exc.code,
            )
        else:
            failed_operational_action = (
                peeked_intent.action if peeked_intent is not None else parsed_action
            )
            _record(
                run_dir,
                run_id,
                action=AuditAction.OPERATION_FAILED,
                principal=principal,
                correlation_id=correlation_id,
                operational_action=failed_operational_action,
                reason_code=peeked_intent.reason_code if peeked_intent is not None else None,
                error_code=exc.code,
            )
        raise

    audit_event_id = _record(
        run_dir,
        run_id,
        action=_SUCCEEDED_AUDIT_ACTION[parsed_action],
        principal=principal,
        correlation_id=correlation_id,
        operational_action=parsed_action,
        activation_event_id=result.event_id,
        reason_code=peeked_intent.reason_code if peeked_intent is not None else None,
    )

    url = request.app.url_path_for(
        "ui_governance_result", run_id=run_id, action=parsed_action.value
    )
    query = (
        f"generation_id={result.generation_id}&active_lane={result.active_lane.value}"
        f"&pointer_version={result.pointer_version}&event_id={result.event_id}"
        f"&idempotent={'true' if result.idempotent else 'false'}"
        f"&audit_event_id={audit_event_id or ''}"
    )
    return Response(status_code=303, headers={"Location": f"{url}?{query}"})


@router.get(
    "/runs/{run_id}/governance/actions/{action}/result",
    response_class=HTMLResponse,
    name="ui_governance_result",
)
def governance_result_ui(
    request: Request,
    run_id: str,
    action: str,
    generation_id: str,
    active_lane: str,
    pointer_version: int,
    event_id: str,
    idempotent: bool,
    audit_event_id: str = "",
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
    principal: AuthenticatedPrincipal = Depends(get_principal),
) -> HTMLResponse:
    _run_dir(settings, run_id)
    parsed_action = _validate_action(action)
    context = {
        "run_id": run_id,
        "action": parsed_action,
        "principal": principal,
        "generation_id": generation_id,
        "active_lane": active_lane,
        "pointer_version": pointer_version,
        "event_id": event_id,
        "idempotent": idempotent,
        "audit_event_id": audit_event_id or None,
    }
    return templates.TemplateResponse(request, "governance_action_result.html", context)


@router.get(
    "/runs/{run_id}/governance/audit", response_class=HTMLResponse, name="ui_governance_audit"
)
def governance_audit_ui(
    request: Request,
    run_id: str,
    settings: Settings = Depends(get_settings),
    templates: Jinja2Templates = Depends(get_templates),
    principal: AuthenticatedPrincipal = Depends(get_principal),
) -> HTMLResponse:
    run_dir = _run_dir(settings, run_id)
    if not principal.has_permission(OperationalPermission.VIEW_AUDIT_LOG):
        raise ApiError("permiso insuficiente", status_code=403, code="forbidden")
    try:
        events = read_audit_chain(run_dir)
    except OperationalAuditError as exc:
        raise ApiError(
            "la cadena de auditoria esta rota o es ilegible", status_code=500, code="audit_corrupt"
        ) from exc
    context = {"run_id": run_id, "principal": principal, "events": events}
    return templates.TemplateResponse(request, "governance_audit.html", context)


__all__ = ["router"]
