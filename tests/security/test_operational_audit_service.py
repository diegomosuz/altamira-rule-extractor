"""Tests de `security/operational_audit_service.py` (Fase 15B1 Parte
16, seccion AUDITORIA 66-82, `feat/final-hardening-release`)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from altamira_extractor.contracts.operational_audit import AuditAction, OperationalAuditEvent
from altamira_extractor.contracts.security_config import ApplicationRole, AuthenticationMode
from altamira_extractor.security.operational_audit_service import (
    OperationalAuditChainError,
    OperationalAuditLockError,
    OperationalAuditStore,
    OperationalAuditStoreError,
    read_audit_chain,
    record_audit_event,
)


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run-1"
    run_dir.mkdir()
    return run_dir


def _fixed_clock() -> datetime:
    return datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)


def _record(run_dir: Path, *, principal_id: str = "alice") -> OperationalAuditEvent:
    return record_audit_event(
        run_dir,
        "run-1",
        action=AuditAction.LOGIN_CONTEXT_RESOLVED,
        principal_id=principal_id,
        roles=[ApplicationRole.VIEWER],
        authentication_mode=AuthenticationMode.DISABLED_DEV,
        correlation_id="corr-1",
        clock=_fixed_clock,
    )


# 66. El primer evento tiene sequence=1 y previous_audit_event_id=None.
def test_first_event_has_sequence_one(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    event = _record(run_dir)
    assert event.sequence == 1
    assert event.previous_audit_event_id is None


# 67. El segundo evento enlaza al primero por audit_event_id.
def test_events_are_linked_in_sequence(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    first = _record(run_dir, principal_id="alice")
    second = _record(run_dir, principal_id="bob")
    assert second.sequence == 2
    assert second.previous_audit_event_id == first.audit_event_id


# 68. occurred_at_utc proviene del reloj inyectado -- deterministico en tests.
def test_occurred_at_utc_uses_injected_clock(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    event = _record(run_dir)
    assert event.occurred_at_utc == "2026-08-04T12:00:00.000000Z"


# 69. read_audit_chain retorna la cadena confirmada en orden.
def test_read_audit_chain_returns_confirmed_order(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    first = _record(run_dir)
    second = _record(run_dir)
    chain = read_audit_chain(run_dir)
    assert [e.audit_event_id for e in chain] == [first.audit_event_id, second.audit_event_id]


# 70. audit/ es un arbol completamente separado de activation/ -- nunca se crea/toca activation/.
def test_audit_tree_never_touches_activation_tree(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    _record(run_dir)
    assert (run_dir / "audit" / "active.json").is_file()
    assert (run_dir / "audit" / "events").is_dir()
    assert not (run_dir / "activation").exists()


# 71. El lock de auditoria es independiente -- se puede sostener junto a un archivo de lock
# de activation/ simulado sin contencion cruzada.
def test_audit_lock_independent_from_activation_lock_file(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    store = OperationalAuditStore(run_dir)
    with store.lock():
        assert store.lock_is_held()
        (run_dir / "activation").mkdir(exist_ok=True)
        (run_dir / "activation" / ".activation.lock").write_text("12345")
        (run_dir / "activation" / ".activation.lock").unlink()
    assert not store.lock_is_held()


# 72. Contencion real del lock de auditoria -> OperationalAuditLockError.
def test_audit_lock_contention_raises(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    store = OperationalAuditStore(run_dir)
    (run_dir / "audit").mkdir(parents=True, exist_ok=True)
    lock_path = run_dir / "audit" / ".audit.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    try:
        with pytest.raises(OperationalAuditLockError):
            with store.lock():
                pass
    finally:
        lock_path.unlink()


# 73. Un audit_event_id ya existente con contenido DISTINTO se rechaza (colision detectada).
def test_collision_with_different_content_rejected(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    first = _record(run_dir)
    store = OperationalAuditStore(run_dir)
    forged = OperationalAuditEvent(
        audit_event_id=first.audit_event_id,
        run_id="run-1",
        sequence=1,
        previous_audit_event_id=None,
        action=AuditAction.LOGIN_CONTEXT_RESOLVED,
        outcome=first.outcome,
        principal_id="mallory",
        authentication_mode=AuthenticationMode.DISABLED_DEV,
        correlation_id="corr-1",
    )
    with pytest.raises(OperationalAuditStoreError):
        store.append_event(forged)


# 74. Un evento huerfano (persistido pero nunca apuntado) se tolera -- no aparece en la
# cadena confirmada y no rompe la lectura.
def test_orphan_event_tolerated_and_excluded_from_chain(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    real = _record(run_dir)
    orphan = OperationalAuditEvent(
        audit_event_id="audit-" + "0" * 64,
        run_id="run-1",
        sequence=99,
        previous_audit_event_id="audit-" + "f" * 64,
        action=AuditAction.LOGIN_CONTEXT_RESOLVED,
        outcome=real.outcome,
        principal_id="ghost",
        authentication_mode=AuthenticationMode.DISABLED_DEV,
        correlation_id="corr-x",
    )
    (run_dir / "audit" / "events" / f"{orphan.audit_event_id}.json").write_text(
        orphan.to_stable_json(), encoding="utf-8"
    )
    chain = read_audit_chain(run_dir)
    assert [e.audit_event_id for e in chain] == [real.audit_event_id]


# 75. Un enlace roto en la cadena confirmada se reporta -- nunca se repara automaticamente.
def test_broken_link_in_confirmed_chain_reports_error(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    _record(run_dir)
    store = OperationalAuditStore(run_dir)
    pointer_path = run_dir / "audit" / "active.json"
    pointer_data = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer_data["latest_audit_event_id"] = "audit-" + "d" * 64
    pointer_path.write_text(json.dumps(pointer_data), encoding="utf-8")
    with pytest.raises(OperationalAuditChainError):
        store.read_chain()


# 76. Nunca se borra un evento -- no existe metodo de borrado en el store.
def test_store_never_exposes_a_delete_method() -> None:
    assert not hasattr(OperationalAuditStore, "delete_event")
    assert not hasattr(OperationalAuditStore, "remove_event")


# 77. run_dir invalido/ausente -> OperationalAuditStoreError.
def test_invalid_run_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(OperationalAuditStoreError):
        OperationalAuditStore(tmp_path / "does-not-exist")
