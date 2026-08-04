"""Tests del selector deterministico de canary (Fase 14A Parte 3,
`feat/controlled-unified-activation`)."""

from __future__ import annotations

import ast
import inspect

from altamira_extractor.contracts.unified_activation_config import (
    UnifiedActivationConfig,
    UnifiedActivationMode,
    UnifiedCanarySelectionStrategy,
)
from altamira_extractor.pipeline import unified_activation_canary_selector
from altamira_extractor.pipeline.unified_activation_canary_selector import select_canary

HASH_A = "a" * 64
HASH_B = "b" * 64


def _config(**overrides: object) -> UnifiedActivationConfig:
    base: dict[str, object] = {"mode": UnifiedActivationMode.V1_ONLY}
    base.update(overrides)
    return UnifiedActivationConfig(**base)  # type: ignore[arg-type]


def test_denylist_prevails_over_bucket_selection() -> None:
    """La denylist prevalece sobre el otro mecanismo de seleccion
    (bucket 100%, que de otro modo seleccionaria TODO)."""
    config = _config(
        canary_strategy=UnifiedCanarySelectionStrategy.ALLOWLIST_OR_HASH_BUCKET,
        canary_percentage=100,
        package_hash_denylist=[HASH_A],
    )
    result = select_canary(config, source_package_hash=HASH_A, run_id="run-1")
    assert result.selected is False
    assert result.matched_denylist is True


class TestDenylistPrecedenceOverOverlap:
    """El contrato permite deliberadamente que un `source_package_hash`
    aparezca en `package_hash_allowlist` Y `package_hash_denylist`
    simultaneamente (ver `contracts/unified_activation_config.py::
    TestDenylistPrevails`) -- estos tests demuestran la precedencia
    REAL en tiempo de ejecucion: denylist -> allowlist -> bucket ->
    ninguna seleccion."""

    def test_overlap_with_explicit_allowlist_strategy_denylist_wins(self) -> None:
        config = _config(
            canary_strategy=UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST,
            package_hash_allowlist=[HASH_A],
            package_hash_denylist=[HASH_A],
        )
        result = select_canary(config, source_package_hash=HASH_A, run_id="run-1")
        assert result.selected is False
        assert result.matched_allowlist is True
        assert result.matched_denylist is True
        assert result.reason == "denylisted"

    def test_overlap_with_allowlist_or_hash_bucket_strategy_denylist_wins(self) -> None:
        config = _config(
            canary_strategy=UnifiedCanarySelectionStrategy.ALLOWLIST_OR_HASH_BUCKET,
            package_hash_allowlist=[HASH_A],
            package_hash_denylist=[HASH_A],
        )
        result = select_canary(config, source_package_hash=HASH_A, run_id="run-1")
        assert result.selected is False
        assert result.matched_allowlist is True
        assert result.matched_denylist is True
        assert result.reason == "denylisted"

    def test_denylist_prevails_over_allowlist_and_bucket_simultaneously(self) -> None:
        """Mismo hash en ambas listas, ADEMAS con `canary_percentage=
        100` (que por si solo seleccionaria todo via bucket): la
        denylist sigue prevaleciendo sobre AMBOS mecanismos a la vez."""
        config = _config(
            canary_strategy=UnifiedCanarySelectionStrategy.ALLOWLIST_OR_HASH_BUCKET,
            canary_percentage=100,
            package_hash_allowlist=[HASH_A],
            package_hash_denylist=[HASH_A],
        )
        result = select_canary(config, source_package_hash=HASH_A, run_id="run-1")
        assert result.selected is False
        assert result.matched_allowlist is True
        assert result.matched_denylist is True
        assert result.bucket is None
        assert result.reason == "denylisted"

    def test_overlap_result_independent_of_run_id(self) -> None:
        config = _config(
            canary_strategy=UnifiedCanarySelectionStrategy.ALLOWLIST_OR_HASH_BUCKET,
            canary_percentage=100,
            package_hash_allowlist=[HASH_A],
            package_hash_denylist=[HASH_A],
        )
        outcomes = {
            (
                select_canary(config, source_package_hash=HASH_A, run_id=run_id).selected,
                select_canary(config, source_package_hash=HASH_A, run_id=run_id).matched_allowlist,
                select_canary(config, source_package_hash=HASH_A, run_id=run_id).matched_denylist,
            )
            for run_id in ("run-a", "run-b", "totally-different-run-id", "")
        }
        assert outcomes == {(False, True, True)}


def test_allowlist_selects() -> None:
    config = _config(
        canary_strategy=UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST,
        package_hash_allowlist=[HASH_A],
    )
    result = select_canary(config, source_package_hash=HASH_A, run_id="run-1")
    assert result.selected is True
    assert result.matched_allowlist is True


def test_hash_not_in_allowlist_not_selected() -> None:
    config = _config(
        canary_strategy=UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST,
        package_hash_allowlist=[HASH_B],
    )
    result = select_canary(config, source_package_hash=HASH_A, run_id="run-1")
    assert result.selected is False
    assert result.matched_allowlist is False


def test_bucket_0_selects_nothing() -> None:
    config = _config(
        canary_strategy=UnifiedCanarySelectionStrategy.PACKAGE_HASH_BUCKET,
        canary_percentage=0,
    )
    result = select_canary(config, source_package_hash=HASH_A, run_id="run-1")
    assert result.selected is False


def test_bucket_100_selects_everything() -> None:
    config = _config(
        canary_strategy=UnifiedCanarySelectionStrategy.PACKAGE_HASH_BUCKET,
        canary_percentage=100,
    )
    result = select_canary(config, source_package_hash=HASH_A, run_id="run-1")
    assert result.selected is True
    assert result.bucket is not None
    assert 0 <= result.bucket <= 99


def test_selection_is_deterministic_for_same_hash() -> None:
    config = _config(
        canary_strategy=UnifiedCanarySelectionStrategy.PACKAGE_HASH_BUCKET,
        canary_percentage=50,
    )
    result_1 = select_canary(config, source_package_hash=HASH_A, run_id="run-1")
    result_2 = select_canary(config, source_package_hash=HASH_A, run_id="run-2")
    assert result_1.selected == result_2.selected
    assert result_1.bucket == result_2.bucket


def test_run_id_never_alters_selection() -> None:
    config = _config(
        canary_strategy=UnifiedCanarySelectionStrategy.PACKAGE_HASH_BUCKET,
        canary_percentage=50,
    )
    outcomes = {
        select_canary(config, source_package_hash=HASH_A, run_id=run_id).selected
        for run_id in ("run-a", "run-b", "totally-different-run-id", "")
    }
    assert len(outcomes) == 1


def test_allowlist_or_hash_bucket_selects_via_either() -> None:
    config = _config(
        canary_strategy=UnifiedCanarySelectionStrategy.ALLOWLIST_OR_HASH_BUCKET,
        canary_percentage=0,
        package_hash_allowlist=[HASH_A],
    )
    result = select_canary(config, source_package_hash=HASH_A, run_id="run-1")
    assert result.selected is True
    assert result.matched_allowlist is True


def test_package_hash_bucket_strategy_ignores_allowlist() -> None:
    config = _config(
        canary_strategy=UnifiedCanarySelectionStrategy.PACKAGE_HASH_BUCKET,
        canary_percentage=0,
        package_hash_allowlist=[HASH_A],
    )
    result = select_canary(config, source_package_hash=HASH_A, run_id="run-1")
    assert result.selected is False
    assert result.matched_allowlist is False


def test_selector_module_never_calls_random_or_native_hash() -> None:
    """Analiza el AST real -- nunca `random`, nunca `hash()` nativo de
    Python (cuyo valor varia entre procesos), nunca `datetime.now()`."""
    tree = ast.parse(inspect.getsource(unified_activation_canary_selector))
    forbidden_names = {"random", "hash"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_names, f"llamada prohibida: {node.func.id}()"
        if isinstance(node, ast.Attribute) and node.attr in {"now", "utcnow"}:
            raise AssertionError(f"llamada prohibida: .{node.attr}()")


def test_selector_never_imports_random_module() -> None:
    tree = ast.parse(inspect.getsource(unified_activation_canary_selector))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "random" for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "random"
