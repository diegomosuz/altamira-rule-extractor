"""Servicio de auditoria operativa append-only (Fase 15B1,
`feat/final-hardening-release`).

Arbol `audit/` COMPLETAMENTE separado de `activation/` (Fase 14B/
`pipeline/unified_activation_store.py`): lock propio
(`audit/.audit.lock`), puntero propio (`audit/active.json`), eventos
propios (`audit/events/<audit_event_id>.json`). Este modulo NUNCA abre
ni escribe bajo `activation/`, y `UnifiedActivationStore` nunca escribe
bajo `audit/` -- las dos escrituras pueden proceder de forma
independiente sin contencion cruzada.

Reutiliza `pipeline/artifact_store.py::atomic_write_json` (ya probado,
con reintento acotado) para toda escritura -- nunca reimplementa la
escritura atomica ni el patron de lock (mismo `os.O_CREAT | os.O_EXCL`
que `UnifiedActivationStore.lock()`, pero sobre un archivo distinto)."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from ..contracts.operational_audit import (
    AuditAction,
    OperationalAuditActivePointer,
    OperationalAuditEvent,
    compute_audit_event_id,
    outcome_for_action,
)
from ..contracts.operational_authorization_request import OperationalAction
from ..contracts.security_config import ApplicationRole, AuthenticationMode
from ..contracts.unified_materialization_authorization import UnifiedMaterializationReasonCode
from ..pipeline.artifact_store import atomic_write_json

_AUDIT_DIR_NAME = "audit"
_EVENTS_DIR_NAME = "events"
_ACTIVE_POINTER_FILENAME = "active.json"
_LOCK_FILENAME = ".audit.lock"


class OperationalAuditError(Exception):
    """Fallo del subsistema de auditoria operativa: `run_dir` invalido,
    `audit_event_id` con forma insegura, evento persistido corrupto, o
    `audit/active.json` corrupto. El mensaje nunca incluye una ruta
    absoluta."""


class OperationalAuditLockError(OperationalAuditError):
    """`audit/.audit.lock` ya esta tomado por otro proceso, o no pudo
    liberarse limpiamente. NUNCA se asume "stale" automaticamente --
    misma politica que `UnifiedActivationLockError` (Fase 14B), pero
    sobre un lock completamente independiente."""


class OperationalAuditStoreError(OperationalAuditError):
    """Un `audit_event_id` con contenido DISTINTO ya existe
    (colision), el evento releido no coincide con el escrito, o
    `audit/active.json`/un evento persistido esta corrupto."""


class OperationalAuditChainError(OperationalAuditError):
    """La cadena alcanzable desde `audit/active.json` tiene un ciclo o
    un enlace roto (`previous_audit_event_id` que no resuelve a un
    evento persistido). Nunca se repara: solo se reporta."""


def _validate_event_id_component(value: str) -> None:
    """Defensa contra path traversal via `audit_event_id` -- aunque es
    siempre content-addressed (nunca provisto por el cliente), se
    revalida igual antes de construir una ruta de archivo."""
    if not value or "/" in value or "\\" in value or value in (".", "..") or "\x00" in value:
        raise OperationalAuditStoreError(f"audit_event_id invalido: {value!r}")


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _format_occurred_at_utc(moment: datetime) -> str:
    utc_moment = moment.astimezone(UTC)
    return utc_moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc_moment.microsecond:06d}Z"


class OperationalAuditStore:
    """Store append-only de UN run. Nunca comparte estado entre
    instancias/runs distintos; nunca escribe fuera de `audit/`."""

    def __init__(self, run_dir: Path) -> None:
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise OperationalAuditStoreError(f"run_dir invalido o inexistente: {run_dir.name!r}")
        self._run_dir = run_dir
        self._audit_dir = run_dir / _AUDIT_DIR_NAME
        self._events_dir = self._audit_dir / _EVENTS_DIR_NAME
        self._active_pointer_path = self._audit_dir / _ACTIVE_POINTER_FILENAME
        self._lock_path = self._audit_dir / _LOCK_FILENAME

    @property
    def audit_dir(self) -> Path:
        return self._audit_dir

    # ------------------------------------------------------------------
    # Lock single-writer (independiente del de `activation/`)
    # ------------------------------------------------------------------

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Adquiere `audit/.audit.lock` por exclusion mutua real. Se
        libera SIEMPRE en `finally`. Nunca toca
        `activation/.activation.lock`: una escritura de auditoria puede
        proceder aunque una materializacion este en curso, y viceversa."""
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise OperationalAuditLockError(
                "audit/.audit.lock ya esta tomado -- otra escritura de auditoria esta en curso, "
                "o un proceso anterior no lo libero"
            ) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            self._release_lock()
            raise
        try:
            yield
        finally:
            self._release_lock()

    def _release_lock(self) -> None:
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            pass

    def lock_is_held(self) -> bool:
        return self._lock_path.is_file()

    # ------------------------------------------------------------------
    # Puntero activo
    # ------------------------------------------------------------------

    def read_active_pointer(self) -> OperationalAuditActivePointer | None:
        """`None` UNICAMENTE cuando `audit/active.json` nunca se
        escribio -- nunca para un archivo corrupto, que lanza
        `OperationalAuditStoreError`."""
        if self._active_pointer_path.is_symlink() or not self._active_pointer_path.is_file():
            return None
        try:
            return OperationalAuditActivePointer.model_validate_json(
                self._active_pointer_path.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            raise OperationalAuditStoreError("audit/active.json corrupto") from exc

    def next_sequence(self) -> tuple[int, str | None]:
        """`(sequence, previous_audit_event_id)` para el PROXIMO
        evento a anexar. Debe llamarse dentro de `lock()`."""
        pointer = self.read_active_pointer()
        if pointer is None:
            return 1, None
        return pointer.pointer_version + 1, pointer.latest_audit_event_id

    # ------------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------------

    def _event_path(self, audit_event_id: str) -> Path:
        _validate_event_id_component(audit_event_id)
        return self._events_dir / f"{audit_event_id}.json"

    def read_event(self, audit_event_id: str) -> OperationalAuditEvent | None:
        path = self._event_path(audit_event_id)
        if path.is_symlink() or not path.is_file():
            return None
        try:
            return OperationalAuditEvent.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise OperationalAuditStoreError(
                f"evento de auditoria corrupto: {audit_event_id}"
            ) from exc

    def append_event(self, event: OperationalAuditEvent) -> OperationalAuditEvent:
        """Debe llamarse UNICAMENTE dentro de `lock()`. Persiste el
        evento PRIMERO (append-only, nunca reescrito) y el puntero
        DESPUES -- si el proceso muere entre ambas escrituras, el
        evento queda huerfano pero SEGURO: nunca se repara ni se borra
        automaticamente, y nunca se confunde con la cadena confirmada
        porque el puntero nunca llego a apuntarlo (misma semantica que
        `UnifiedActivationStore`, Fase 14B)."""
        existing = self.read_event(event.audit_event_id)
        if existing is not None and existing != event:
            raise OperationalAuditStoreError(
                f"audit_event_id ya existe con contenido distinto: {event.audit_event_id}"
            )
        if existing is None:
            event_path = self._event_path(event.audit_event_id)
            atomic_write_json(event_path, event)
            persisted = self.read_event(event.audit_event_id)
            if persisted != event:
                raise OperationalAuditStoreError(
                    f"el evento releido no coincide con el escrito: {event.audit_event_id}"
                )

        pointer = OperationalAuditActivePointer(
            run_id=event.run_id,
            pointer_version=event.sequence,
            latest_audit_event_id=event.audit_event_id,
            event_count=event.sequence,
        )
        atomic_write_json(self._active_pointer_path, pointer)
        return event

    def read_chain(self) -> list[OperationalAuditEvent]:
        """Cadena CONFIRMADA, del primero al ultimo, siguiendo
        `previous_audit_event_id` desde `audit/active.json`. Detecta
        ciclos y enlaces rotos -- NUNCA repara, solo reporta. Eventos
        huerfanos (persistidos pero nunca alcanzados por el puntero) se
        toleran y simplemente no aparecen en el resultado."""
        pointer = self.read_active_pointer()
        if pointer is None:
            return []
        chain: list[OperationalAuditEvent] = []
        seen: set[str] = set()
        current_id: str | None = pointer.latest_audit_event_id
        while current_id is not None:
            if current_id in seen:
                raise OperationalAuditChainError(
                    f"ciclo detectado en la cadena de auditoria en {current_id}"
                )
            seen.add(current_id)
            event = self.read_event(current_id)
            if event is None:
                raise OperationalAuditChainError(
                    f"enlace roto en la cadena de auditoria: {current_id} no existe"
                )
            chain.append(event)
            current_id = event.previous_audit_event_id
        chain.reverse()
        return chain


def record_audit_event(
    run_dir: Path,
    run_id: str,
    *,
    action: AuditAction,
    principal_id: str,
    roles: list[ApplicationRole],
    authentication_mode: AuthenticationMode,
    correlation_id: str,
    operational_action: OperationalAction | None = None,
    activation_event_id: str | None = None,
    reason_code: UnifiedMaterializationReasonCode | None = None,
    error_code: str | None = None,
    diagnostics: list[str] | None = None,
    clock: Callable[[], datetime] = _default_clock,
) -> OperationalAuditEvent:
    """Punto de entrada unico para anexar un evento -- adquiere el lock
    de `audit/`, calcula `sequence`/`previous_audit_event_id`, computa
    el `audit_event_id` content-addressed (excluyendo el timestamp) y
    persiste. `clock` es inyectable (Parte 9): los tests deben pasar
    uno explicito y determinista, nunca depender de la hora real."""
    outcome = outcome_for_action(action)
    store = OperationalAuditStore(run_dir)
    with store.lock():
        sequence, previous_audit_event_id = store.next_sequence()
        audit_event_id = compute_audit_event_id(
            run_id=run_id,
            sequence=sequence,
            action=action,
            outcome=outcome,
            principal_id=principal_id,
            correlation_id=correlation_id,
            previous_audit_event_id=previous_audit_event_id,
            operational_action=operational_action,
            activation_event_id=activation_event_id,
            reason_code=reason_code,
            error_code=error_code,
        )
        event = OperationalAuditEvent(
            audit_event_id=audit_event_id,
            run_id=run_id,
            sequence=sequence,
            previous_audit_event_id=previous_audit_event_id,
            action=action,
            outcome=outcome,
            principal_id=principal_id,
            roles=roles,
            authentication_mode=authentication_mode,
            correlation_id=correlation_id,
            operational_action=operational_action,
            activation_event_id=activation_event_id,
            reason_code=reason_code,
            error_code=error_code,
            diagnostics=sorted(set(diagnostics)) if diagnostics else [],
            occurred_at_utc=_format_occurred_at_utc(clock()),
        )
        return store.append_event(event)


def read_audit_chain(run_dir: Path) -> list[OperationalAuditEvent]:
    """Lector de solo lectura para UI/API -- reutilizado por
    `pipeline/operational_governance_reader.py`-style consumidores."""
    return OperationalAuditStore(run_dir).read_chain()


__all__ = [
    "OperationalAuditChainError",
    "OperationalAuditError",
    "OperationalAuditLockError",
    "OperationalAuditStore",
    "OperationalAuditStoreError",
    "read_audit_chain",
    "record_audit_event",
]
