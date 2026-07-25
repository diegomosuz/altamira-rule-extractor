"""Executor local y concurrencia (Prompt 13b): capacidad acotada,
mismo run activo, liberacion del registro al terminar, valor por
defecto de un unico worker."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import altamira_extractor.api.run_actions as run_actions_module
from altamira_extractor.api.executor import RunExecutor
from altamira_extractor.config import Settings

from ..pipeline.conftest import build_valid_package_zip


def _wait_until(predicate: object, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.02)
    return False


def test_default_max_workers_is_one() -> None:
    assert Settings().api_max_workers == 1


def test_capacity_exceeded_returns_503_and_preserves_run(
    client: TestClient, settings: Settings, tmp_path: Path, monkeypatch: object
) -> None:
    block = threading.Event()
    started = threading.Event()

    def _blocking_run_ingestion(*args: object, **kwargs: object) -> None:
        started.set()
        block.wait(timeout=5)

    monkeypatch.setattr(run_actions_module, "run_ingestion", _blocking_run_ingestion)  # type: ignore[attr-defined]

    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    try:
        with zip_path.open("rb") as fh:
            first = client.post(
                "/api/runs", files={"file": ("a.zip", fh, "application/zip")}
            )
        assert first.status_code == 202
        assert started.wait(timeout=5)

        with zip_path.open("rb") as fh:
            second = client.post(
                "/api/runs", files={"file": ("b.zip", fh, "application/zip")}
            )
        assert second.status_code == 503
        body = second.json()
        assert body["error"]["code"] == "executor_at_capacity"
        second_run_id = body["run_id"]

        run_dir = settings.runs_dir / second_run_id
        assert (run_dir / "run.json").is_file()
        assert (run_dir / "input" / "package.zip").is_file()
    finally:
        block.set()

    # El run rechazado por capacidad puede reanudarse una vez que el
    # unico worker vuelve a estar libre (el primer job ya se libero via
    # block.set() arriba).
    assert _wait_until(
        lambda: client.post(f"/api/runs/{second_run_id}/resume").status_code == 202, timeout=5
    )


def test_resume_on_active_run_returns_409(
    client: TestClient, tmp_path: Path, monkeypatch: object
) -> None:
    block = threading.Event()
    started = threading.Event()

    def _blocking_run_ingestion(*args: object, **kwargs: object) -> None:
        started.set()
        block.wait(timeout=5)

    monkeypatch.setattr(run_actions_module, "run_ingestion", _blocking_run_ingestion)  # type: ignore[attr-defined]

    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    try:
        with zip_path.open("rb") as fh:
            first = client.post(
                "/api/runs", files={"file": ("a.zip", fh, "application/zip")}
            )
        run_id = first.json()["run_id"]
        assert started.wait(timeout=5)

        resume_response = client.post(f"/api/runs/{run_id}/resume")
        assert resume_response.status_code == 409
        assert resume_response.json()["error"]["code"] == "run_active"
    finally:
        block.set()


def test_callback_frees_registry_after_completion(
    client: TestClient, tmp_path: Path, monkeypatch: object
) -> None:
    release = threading.Event()
    finished = threading.Event()

    def _controlled_run_ingestion(*args: object, **kwargs: object) -> None:
        release.wait(timeout=5)
        finished.set()

    monkeypatch.setattr(run_actions_module, "run_ingestion", _controlled_run_ingestion)  # type: ignore[attr-defined]

    zip_path = build_valid_package_zip(tmp_path / "package.zip")
    with zip_path.open("rb") as fh:
        first = client.post("/api/runs", files={"file": ("a.zip", fh, "application/zip")})
    run_id = first.json()["run_id"]

    release.set()
    assert _wait_until(finished.is_set, timeout=5)
    # El callback interno de RunExecutor limpia el registro DESPUES de
    # que `fn()` retorna; se espera un margen breve para esa carrera
    # interna (finished.set() ocurre DENTRO de fn, el registro se limpia
    # justo despues).
    assert _wait_until(
        lambda: client.post(f"/api/runs/{run_id}/resume").status_code != 409, timeout=5
    )


# --- RunExecutor puro (sin HTTP/FastAPI): concurrencia interna acotada ---


def test_failing_future_still_frees_the_registry() -> None:
    executor = RunExecutor(max_workers=1)
    try:
        released = threading.Event()

        def _fails() -> None:
            raise RuntimeError("fallo simulado dentro del job en background")

        assert executor.try_submit("run-a", _fails) == "submitted"
        assert _wait_until(lambda: not executor.is_active("run-a"), timeout=5)

        # El registro quedo libre pese a la excepcion: un segundo submit
        # con el MISMO run_id ya no colisiona.
        def _second() -> None:
            released.set()

        assert executor.try_submit("run-a", _second) == "submitted"
        assert _wait_until(released.is_set, timeout=5)
    finally:
        executor.shutdown(wait=True)


def test_shutdown_rejects_new_submissions() -> None:
    executor = RunExecutor(max_workers=1)
    executor.shutdown(wait=True)

    with pytest.raises(RuntimeError):
        executor.try_submit("run-after-shutdown", lambda: None)


def test_capacity_is_bounded_exactly_at_max_workers_never_queued_silently() -> None:
    executor = RunExecutor(max_workers=2)
    block = threading.Event()
    started = threading.Event()
    try:

        def _blocking() -> None:
            started.set()
            block.wait(timeout=5)

        assert executor.try_submit("run-1", _blocking) == "submitted"
        assert _wait_until(lambda: executor.is_active("run-1"), timeout=5)

        def _blocking2() -> None:
            block.wait(timeout=5)

        assert executor.try_submit("run-2", _blocking2) == "submitted"
        assert _wait_until(lambda: executor.is_active("run-2"), timeout=5)

        # Un tercer submit, con el executor ya a capacidad (2/2 activos):
        # nunca se acepta silenciosamente en la cola interna del
        # ThreadPoolExecutor -- try_submit lo rechaza de inmediato.
        assert executor.try_submit("run-3", lambda: None) == "at_capacity"
    finally:
        block.set()
        executor.shutdown(wait=True)
