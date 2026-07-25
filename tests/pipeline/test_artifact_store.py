"""Tests unitarios de `atomic_write_json` (Prompt 14b.1): retry acotado
alrededor de `os.replace` exclusivamente para `PermissionError` (el caso
real de Windows -- `MoveFileEx` fallando con `WinError 5` cuando otro
hilo tiene el destino abierto para lectura durante un polling
concurrente, p. ej. la UI leyendo `run.json` mientras el executor lo
persiste). No se depende de provocar bloqueos reales del filesystem:
`os.replace` se monkeypatch-ea para simular las fallas de forma
deterministica; el unico test que se acerca a una condicion de carrera
real (concurrencia con hilos) es deliberadamente no-flaky y no exige que
el race llegue a manifestarse."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.contracts.base import AltamiraBaseModel
from altamira_extractor.pipeline import artifact_store
from altamira_extractor.pipeline.artifact_store import atomic_write_json


class _DummyModel(AltamiraBaseModel):
    value: str


def _no_leftover_temp_files(directory: Path) -> bool:
    return not any(directory.glob("*.tmp"))


def test_atomic_write_json_normal_write_produces_valid_deterministic_json(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.json"
    model = _DummyModel(value="a")

    atomic_write_json(path, model)

    text = path.read_text(encoding="utf-8")
    assert json.loads(text) == {"value": "a"}
    assert text == model.to_stable_json()
    assert _no_leftover_temp_files(tmp_path)


def test_atomic_write_json_retries_once_on_permission_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "run.json"
    real_replace = artifact_store.os.replace
    calls = {"count": 0}

    def flaky_replace(src: Any, dst: Any) -> None:
        calls["count"] += 1
        if calls["count"] == 1:
            raise PermissionError("simulated WinError 5")
        real_replace(src, dst)

    monkeypatch.setattr(artifact_store.os, "replace", flaky_replace)
    monkeypatch.setattr(artifact_store.time, "sleep", lambda seconds: None)

    atomic_write_json(path, _DummyModel(value="b"))

    assert calls["count"] == 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": "b"}
    assert _no_leftover_temp_files(tmp_path)


def test_atomic_write_json_retries_multiple_times_within_limit_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "run.json"
    real_replace = artifact_store.os.replace
    calls = {"count": 0}
    failures_before_success = 4  # dentro del limite de 5 reintentos

    def flaky_replace(src: Any, dst: Any) -> None:
        calls["count"] += 1
        if calls["count"] <= failures_before_success:
            raise PermissionError("simulated WinError 5")
        real_replace(src, dst)

    sleeps: list[float] = []
    monkeypatch.setattr(artifact_store.os, "replace", flaky_replace)
    monkeypatch.setattr(artifact_store.time, "sleep", sleeps.append)

    atomic_write_json(path, _DummyModel(value="c"))

    assert calls["count"] == failures_before_success + 1
    assert len(sleeps) == failures_before_success
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": "c"}
    assert _no_leftover_temp_files(tmp_path)


def test_atomic_write_json_exhausts_retries_and_propagates_permission_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "run.json"
    path.write_text('{"value": "previous"}', encoding="utf-8")

    def always_fails(src: Any, dst: Any) -> None:
        raise PermissionError("simulated WinError 5, always")

    monkeypatch.setattr(artifact_store.os, "replace", always_fails)
    monkeypatch.setattr(artifact_store.time, "sleep", lambda seconds: None)

    with pytest.raises(PermissionError):
        atomic_write_json(path, _DummyModel(value="new"))

    # El destino anterior permanece intacto: nunca se corrompe ni se
    # borra cuando la operacion falla definitivamente.
    assert path.read_text(encoding="utf-8") == '{"value": "previous"}'
    assert _no_leftover_temp_files(tmp_path)


def test_atomic_write_json_does_not_retry_other_os_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "run.json"
    calls = {"count": 0}

    def not_a_permission_error(src: Any, dst: Any) -> None:
        calls["count"] += 1
        raise OSError("disk full or similar, not a permission race")

    monkeypatch.setattr(artifact_store.os, "replace", not_a_permission_error)

    with pytest.raises(OSError):
        atomic_write_json(path, _DummyModel(value="d"))

    assert calls["count"] == 1
    assert _no_leftover_temp_files(tmp_path)


def test_atomic_write_json_backoff_sequence_matches_the_documented_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "run.json"
    real_replace = artifact_store.os.replace
    calls = {"count": 0}

    def flaky_replace(src: Any, dst: Any) -> None:
        calls["count"] += 1
        if calls["count"] <= 5:
            raise PermissionError("simulated")
        real_replace(src, dst)

    sleeps: list[float] = []
    monkeypatch.setattr(artifact_store.os, "replace", flaky_replace)
    # No se ralentiza realmente la suite: time.sleep queda mockeado,
    # solo se registra la secuencia de duraciones pedidas.
    monkeypatch.setattr(artifact_store.time, "sleep", sleeps.append)

    atomic_write_json(path, _DummyModel(value="e"))

    assert sleeps == [0.01, 0.02, 0.04, 0.08, 0.16]


def test_atomic_write_json_concurrent_reader_never_sees_partial_or_corrupt_json(
    tmp_path: Path,
) -> None:
    """Reproduce conceptualmente la carrera real (un hilo leyendo
    `run.json` repetidamente mientras otro escribe), sin monkeypatch:
    codigo real de `atomic_write_json` de punta a punta. No se depende
    de que esto dispare un `PermissionError` real (el gate determinista
    son los tests de arriba) -- unicamente se exige que el contenido
    observado siempre sea JSON valido, que el estado final corresponda a
    la ultima escritura y que no queden temporales."""
    path = tmp_path / "run.json"
    atomic_write_json(path, _DummyModel(value="0"))

    write_count = 25
    stop = threading.Event()
    read_errors: list[Exception] = []
    observed_values: list[str] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                text = path.read_text(encoding="utf-8")
                observed_values.append(json.loads(text)["value"])
            except (OSError, json.JSONDecodeError, KeyError) as exc:
                read_errors.append(exc)

    def writer() -> None:
        for i in range(1, write_count + 1):
            atomic_write_json(path, _DummyModel(value=str(i)))

    reader_thread = threading.Thread(target=reader)
    writer_thread = threading.Thread(target=writer)
    reader_thread.start()
    writer_thread.start()
    writer_thread.join(timeout=30)
    stop.set()
    reader_thread.join(timeout=5)

    assert not writer_thread.is_alive()
    assert not reader_thread.is_alive()
    assert not read_errors, f"el lector encontro contenido invalido/parcial: {read_errors}"
    assert json.loads(path.read_text(encoding="utf-8"))["value"] == str(write_count)
    assert _no_leftover_temp_files(tmp_path)
