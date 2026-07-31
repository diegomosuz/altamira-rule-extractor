"""Tests unitarios de `atomic_write_json` (Prompt 14b.1, endurecido tras
el defecto real de estabilizacion de baseline): retry acotado por
DEADLINE monotonico alrededor de `os.replace`, exclusivamente para
errores de reemplazo reconocidos como transitorios en Windows (WinError
5/32/33 -- ver `_is_transient_replace_error`). No se depende de provocar
bloqueos reales del filesystem para la mayoria de los casos: `os.replace`
y, cuando hace falta control fino del deadline, tambien `time.monotonic`/
`time.sleep`, se monkeypatch-ean para simular las fallas de forma
deterministica y rapida (reloj falso, sin esperas reales). El unico test
que se acerca a una condicion de carrera real (concurrencia con hilos)
es deliberadamente no-flaky, captura explicitamente cualquier excepcion
del hilo escritor (nunca depende de `PytestUnhandledThreadExceptionWarning`
como mecanismo de deteccion) y no exige que el race llegue a
manifestarse para pasar."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.contracts.base import AltamiraBaseModel
from altamira_extractor.pipeline import artifact_store
from altamira_extractor.pipeline.artifact_store import atomic_write_json
from altamira_extractor.pipeline.errors import AtomicWriteError
from altamira_extractor.pipeline.runner import _RUN_STATE_CRITICAL_DEADLINE_SECONDS


class _DummyModel(AltamiraBaseModel):
    value: str


def _no_leftover_temp_files(directory: Path) -> bool:
    return not any(directory.glob("*.tmp"))


def _transient_permission_error(
    message: str = "simulated", *, winerror: int = 5
) -> PermissionError:
    """Un `PermissionError` real de Windows siempre trae `.winerror`
    poblado por el interprete; al monkeypatch-ear `os.replace` con una
    excepcion construida a mano hay que fijarlo explicitamente o
    `_is_transient_replace_error` (que solo mira `.winerror`) lo trataria
    como un error permanente -- exactamente lo que se quiere verificar en
    los tests que SI esperan reintento."""
    exc = PermissionError(f"[WinError {winerror}] {message}")
    exc.winerror = winerror  # type: ignore[attr-defined]
    return exc


class _FakeClock:
    """Reloj monotonico falso para probar la politica de deadline sin
    esperas reales: cada `sleep(seconds)` avanza el reloj exactamente
    `seconds` (nunca duerme de verdad), y `monotonic()` refleja ese
    avance -- permite verificar deadlines/backoff de forma determinista
    y rapida (sin flakiness por temporizacion real del host)."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


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
            raise _transient_permission_error()
        real_replace(src, dst)

    monkeypatch.setattr(artifact_store.os, "replace", flaky_replace)
    monkeypatch.setattr(artifact_store.time, "sleep", lambda seconds: None)

    atomic_write_json(path, _DummyModel(value="b"))

    assert calls["count"] == 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": "b"}
    assert _no_leftover_temp_files(tmp_path)


def test_atomic_write_json_retries_multiple_times_within_deadline_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "run.json"
    real_replace = artifact_store.os.replace
    calls = {"count": 0}
    failures_before_success = 4  # muy por debajo del deadline por defecto (1.0s)

    def flaky_replace(src: Any, dst: Any) -> None:
        calls["count"] += 1
        if calls["count"] <= failures_before_success:
            raise _transient_permission_error()
        real_replace(src, dst)

    clock = _FakeClock()
    monkeypatch.setattr(artifact_store.os, "replace", flaky_replace)
    monkeypatch.setattr(artifact_store.time, "sleep", clock.sleep)
    monkeypatch.setattr(artifact_store.time, "monotonic", clock.monotonic)

    atomic_write_json(path, _DummyModel(value="c"))

    assert calls["count"] == failures_before_success + 1
    assert len(clock.sleeps) == failures_before_success
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": "c"}
    assert _no_leftover_temp_files(tmp_path)


def test_atomic_write_json_raises_atomic_write_error_when_deadline_exhausted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Checkpoint correctivo: el defecto real descubierto en Windows
    nativo era exactamente esto -- una contencion sostenida agota
    cualquier presupuesto acotado. Ya no se propaga un `PermissionError`
    crudo: se envuelve en `AtomicWriteError` (dominio propio, tambien
    `OSError` por compatibilidad -- ver docstring de la excepcion),
    preservando la causa original via `__cause__`, y el destino previo
    nunca se toca."""
    path = tmp_path / "run.json"
    path.write_text('{"value": "previous"}', encoding="utf-8")

    def always_fails(src: Any, dst: Any) -> None:
        raise _transient_permission_error("always")

    clock = _FakeClock()
    monkeypatch.setattr(artifact_store.os, "replace", always_fails)
    monkeypatch.setattr(artifact_store.time, "sleep", clock.sleep)
    monkeypatch.setattr(artifact_store.time, "monotonic", clock.monotonic)

    with pytest.raises(AtomicWriteError) as excinfo:
        atomic_write_json(path, _DummyModel(value="new"), max_wait_seconds=0.3)

    assert isinstance(excinfo.value, OSError)  # compatibilidad con `except OSError` existente
    assert isinstance(excinfo.value.__cause__, PermissionError)
    assert path.name in str(excinfo.value)
    assert "value" not in str(excinfo.value)  # nunca contenido del artefacto

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

    with pytest.raises(OSError) as excinfo:
        atomic_write_json(path, _DummyModel(value="d"))

    assert not isinstance(excinfo.value, AtomicWriteError)
    assert calls["count"] == 1
    assert _no_leftover_temp_files(tmp_path)


def test_atomic_write_json_permission_error_without_transient_winerror_propagates_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Un `PermissionError` real de POSIX (o un WinError no reconocido
    como transitorio) nunca se reintenta ni se envuelve: se propaga tal
    cual, de inmediato, en el primer intento -- nunca se trata "por si
    acaso" como la carrera transitoria de Windows."""
    path = tmp_path / "run.json"
    calls = {"count": 0}

    def permanent_permission_error(src: Any, dst: Any) -> None:
        calls["count"] += 1
        raise PermissionError("permiso denegado real, no relacionado con contencion")

    monkeypatch.setattr(artifact_store.os, "replace", permanent_permission_error)

    with pytest.raises(PermissionError) as excinfo:
        atomic_write_json(path, _DummyModel(value="d"))

    assert not isinstance(excinfo.value, AtomicWriteError)
    assert calls["count"] == 1  # nunca reintentado
    assert _no_leftover_temp_files(tmp_path)


def test_atomic_write_json_backoff_is_exponential_capped_and_never_exceeds_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "run.json"
    real_replace = artifact_store.os.replace
    calls = {"count": 0}
    failures_before_success = 6  # supera el _MAX_BACKOFF_SECONDS: se repite el tope (0.16)

    def flaky_replace(src: Any, dst: Any) -> None:
        calls["count"] += 1
        if calls["count"] <= failures_before_success:
            raise _transient_permission_error()
        real_replace(src, dst)

    clock = _FakeClock()
    monkeypatch.setattr(artifact_store.os, "replace", flaky_replace)
    monkeypatch.setattr(artifact_store.time, "sleep", clock.sleep)
    monkeypatch.setattr(artifact_store.time, "monotonic", clock.monotonic)

    atomic_write_json(path, _DummyModel(value="e"), max_wait_seconds=5.0)

    assert clock.sleeps == [0.01, 0.02, 0.04, 0.08, 0.16, 0.16]
    assert sum(clock.sleeps) < 5.0  # nunca excede el deadline total configurado


def test_atomic_write_json_concurrent_reader_never_sees_partial_or_corrupt_json(
    tmp_path: Path,
) -> None:
    """Reproduce conceptualmente la carrera real (un hilo leyendo
    `run.json` repetidamente mientras otro escribe), sin monkeypatch:
    codigo real de `atomic_write_json` de punta a punta. No se depende
    de que esto dispare un `PermissionError` real (el gate determinista
    son los tests de arriba) -- unicamente se exige que el contenido
    observado siempre sea JSON valido, que el estado final corresponda a
    la ultima escritura y que no queden temporales.

    checkpoint correctivo (estabilizacion de baseline, portabilidad
    Windows): el LECTOR de este test (no el escritor productivo, que ya
    implementa `_replace_with_retry` con reintentos acotados) puede
    observar un `PermissionError` transitorio de milisegundos en
    Windows: `os.replace` exige abrir el destino en modo exclusivo por
    una fraccion de segundo, y una lectura concurrente sin
    `FILE_SHARE_DELETE` lo bloquea brevemente -- exactamente la misma
    carrera documentada en `artifact_store._replace_with_retry`, vista
    desde el lado del lector en vez del escritor. Un reintento acotado
    (deadline corto y monotonico) tolera unicamente esa condicion
    transitoria: `json.JSONDecodeError`, `KeyError` y cualquier
    `PermissionError` que persista mas alla del deadline se siguen
    registrando en `read_errors` sin excepcion -- nunca se oculta
    contenido parcial/corrupto ni un error permanente."""
    path = tmp_path / "run.json"
    atomic_write_json(path, _DummyModel(value="0"))

    write_count = 25
    stop = threading.Event()
    read_errors: list[Exception] = []
    writer_errors: list[BaseException] = []
    observed_values: list[str] = []
    # Ventana de tolerancia deliberadamente corta: mayor que el peor caso
    # documentado de `_replace_with_retry` (~310 ms) para no confundir
    # "el reemplazo todavia esta en curso" con un fallo real, pero acotada
    # para seguir detectando un PermissionError genuinamente permanente.
    _READ_RETRY_DEADLINE_SECONDS = 0.5

    def reader() -> None:
        while not stop.is_set():
            deadline = time.monotonic() + _READ_RETRY_DEADLINE_SECONDS
            while True:
                try:
                    text = path.read_text(encoding="utf-8")
                    observed_values.append(json.loads(text)["value"])
                    break
                except PermissionError as exc:
                    if time.monotonic() >= deadline:
                        read_errors.append(exc)
                        break
                    continue
                except (OSError, json.JSONDecodeError, KeyError) as exc:
                    read_errors.append(exc)
                    break

    def writer() -> None:
        # Checkpoint correctivo: el defecto real era exactamente que este
        # hilo podia fallar SILENCIOSAMENTE (Python solo emite un
        # PytestUnhandledThreadExceptionWarning, nunca falla la prueba) --
        # ahora cualquier excepcion se captura explicitamente en una lista
        # protegida por el GIL (append/lectura de lista son atomicos en
        # CPython) para que el hilo principal la convierta en un fallo de
        # asercion real, nunca en un warning silencioso.
        try:
            for i in range(1, write_count + 1):
                # max_wait_seconds explicito: este test simula EXACTAMENTE
                # el escenario que runner.py enfrenta con run.json (UI
                # sondeando concurrentemente), asi que usa el mismo
                # deadline critico (3.0s, ver runner._RUN_STATE_CRITICAL_
                # DEADLINE_SECONDS) en vez del deadline generico de un
                # artefacto ordinario -- fiel a la carga real, no un
                # numero ampliado ad-hoc porque una corrida fallo.
                atomic_write_json(
                    path,
                    _DummyModel(value=str(i)),
                    max_wait_seconds=_RUN_STATE_CRITICAL_DEADLINE_SECONDS,
                )
        except Exception as exc:  # noqa: BLE001 -- capturado a proposito, ver comentario arriba
            writer_errors.append(exc)

    reader_thread = threading.Thread(target=reader)
    writer_thread = threading.Thread(target=writer)
    reader_thread.start()
    writer_thread.start()
    writer_thread.join(timeout=30)
    stop.set()
    reader_thread.join(timeout=5)

    assert not writer_thread.is_alive()
    assert not reader_thread.is_alive()
    assert not writer_errors, f"el hilo escritor fallo silenciosamente: {writer_errors}"
    assert not read_errors, f"el lector encontro contenido invalido/parcial: {read_errors}"
    assert json.loads(path.read_text(encoding="utf-8"))["value"] == str(write_count)
    assert _no_leftover_temp_files(tmp_path)
