"""Excepciones de dominio de la API HTTP (Prompt 13b).

Cada una lleva su propio `status_code`/`code`/`message` publico y
sanitizado (nunca stacktrace, path absoluto, query, prompt, cuerpo LLM,
header ni credencial). `api/app.py` las traduce a la envolvente estable
`{"error": {"code": ..., "message": ...}}`; nunca se devuelve `str(exc)`
de un `PipelineError` interno tal cual — cada situacion anticipada se
traduce explicitamente aqui."""

from __future__ import annotations


class ApiError(Exception):
    """Base de toda excepcion HTTP de la API. `message` es siempre texto
    publico apto para el cliente."""

    def __init__(self, message: str, *, status_code: int, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


class InvalidIdentifierError(ApiError):
    """`run_id`/`candidate_id` con formato invalido, o parametros de query
    (`limit`/`offset`) fuera de rango. 422."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=422, code="invalid_identifier")


class RunNotFoundError(ApiError):
    """`run_id` sin `run.json` valido en `runs_dir`. 404."""

    def __init__(self, message: str = "run no encontrado") -> None:
        super().__init__(message, status_code=404, code="run_not_found")


class CandidateNotFoundError(ApiError):
    """`candidate_id` ausente del manifest correspondiente para este run.
    404."""

    def __init__(self, message: str = "candidato no encontrado") -> None:
        super().__init__(message, status_code=404, code="candidate_not_found")


class StageNotReachedError(ApiError):
    """La `StageExecution` requerida no existe, no es `SUCCEEDED`, o el run
    esta `FAILED`. 409."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409, code="stage_not_reached")


class RunAlreadyActiveError(ApiError):
    """Ya hay una ejecucion en curso para este `run_id` en el executor
    local. 409."""

    def __init__(self, message: str = "el run ya tiene una ejecucion activa") -> None:
        super().__init__(message, status_code=409, code="run_active")


class RunNotResumableError(ApiError):
    """El run ya esta `COMPLETED`: `resume` no aplica. 409."""

    def __init__(self, message: str = "el run ya esta COMPLETED") -> None:
        super().__init__(message, status_code=409, code="run_not_resumable")


class ExecutorAtCapacityError(ApiError):
    """El executor local alcanzo `settings.api_max_workers`. 503. El run
    (si ya existe) permanece intacto y reanudable: `run_id` se expone en
    la respuesta (fuera de la envolvente `error`) para que el cliente
    pueda reintentar via `resume` mas tarde."""

    def __init__(self, run_id: str) -> None:
        super().__init__(
            "capacidad del executor local agotada; el run quedo persistido y puede "
            "reanudarse mas tarde",
            status_code=503,
            code="executor_at_capacity",
        )
        self.run_id = run_id


class EmptyUploadError(ApiError):
    """El upload no tiene bytes. 400."""

    def __init__(self, message: str = "el upload esta vacio") -> None:
        super().__init__(message, status_code=400, code="empty_upload")


class UploadTooLargeError(ApiError):
    """El upload excede `settings.max_package_size_bytes`. 413."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=413, code="upload_too_large")


class ArtifactCorruptedError(ApiError):
    """Un manifest o artefacto esta presente pero es inconsistente
    (symlink, subdirectorio, hash que no coincide, archivo declarado
    ausente, JSON invalido, id interno que no coincide con el
    solicitado). Nunca reparado ni reconstruido por la API. 500."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=500, code="artifact_corrupted")


class ServiceUnavailableError(ApiError):
    """Un recurso local (filesystem de `runs_dir`/`incoming_dir`)
    temporalmente no utilizable durante una operacion sincrona (p. ej.
    fallo de escritura en RECEIVED). 503."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=503, code="service_unavailable")


class SecurityMisconfiguredError(ApiError):
    """`config/security.yaml` esta ausente o es invalido (cierre Fase
    15B1, "DISABLED_DEV explicito") -- fail-closed: ninguna ruta que
    dependa de `get_principal`/`get_session`/`get_security_config`
    entrega datos, y NUNCA se construye un principal anonimo por
    defecto. 503 (el proceso esta vivo -- `/health` sigue
    respondiendo -- pero no listo para servir trafico protegido)."""

    def __init__(
        self, message: str = "configuracion de seguridad ausente o invalida"
    ) -> None:
        super().__init__(message, status_code=503, code="security_misconfigured")


class UnauthenticatedError(ApiError):
    """`authentication_mode=TRUSTED_PROXY_HEADERS` sin identidad valida
    (marker header ausente/incorrecto, user header ausente, identidad
    malformada). 401. Nunca ocurre en `DISABLED_DEV` (el principal
    anonimo siempre resuelve, `authenticated=False` pero sin excepcion)."""

    def __init__(self, message: str = "identidad no verificada") -> None:
        super().__init__(message, status_code=401, code="unauthenticated")


class ForbiddenError(ApiError):
    """El principal esta autenticado (o es el anonimo de `DISABLED_DEV`)
    pero no tiene el `OperationalPermission` requerido para la accion
    solicitada. 403. El backend SIEMPRE revalida esto, incluso si la UI
    ya oculto el boton correspondiente."""

    def __init__(self, message: str = "permiso insuficiente") -> None:
        super().__init__(message, status_code=403, code="forbidden")


class OperationalPreconditionError(ApiError):
    """El estado real del run cambio entre `prepare` y `confirm`/
    `execute` (pointer, readiness, grupo aprobado, accion, target
    generation, challenge expirado/reutilizado/de otra sesion,
    principal distinto) -- la operacion se aborta ANTES de tocar
    `activation/`. 409."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409, code="operational_precondition_failed")


class RequestBodyTooLargeError(ApiError):
    """Un endpoint de formulario (no de upload de paquete -- ver
    `UploadTooLargeError` para eso) recibio un cuerpo mayor al limite
    esperado para sus campos (Fase 15B1 Parte 14: gobierno operativo,
    formularios pequenos, nunca archivos). 413."""

    def __init__(
        self, message: str = "el cuerpo de la solicitud excede el limite permitido"
    ) -> None:
        super().__init__(message, status_code=413, code="request_body_too_large")
