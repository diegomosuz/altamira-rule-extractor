"""Tests de `UnifiedActivationConfig` (Fase 14A Parte 2,
`feat/controlled-unified-activation`)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from altamira_extractor.contracts.unified_activation_config import (
    UnifiedActivationConfig,
    UnifiedActivationMode,
    UnifiedActivationProviderPolicy,
    UnifiedCanarySelectionStrategy,
    UnifiedFallbackPolicy,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


class TestValidModeConfigs:
    def test_v1_only_valid(self) -> None:
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY)
        assert config.mode == UnifiedActivationMode.V1_ONLY
        assert config.materialization_enabled is False

    def test_shadow_compare_valid(self) -> None:
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.SHADOW_COMPARE)
        assert config.mode == UnifiedActivationMode.SHADOW_COMPARE

    def test_unified_canary_valid(self) -> None:
        config = UnifiedActivationConfig(
            mode=UnifiedActivationMode.UNIFIED_CANARY,
            fallback_policy=UnifiedFallbackPolicy.FALLBACK_TO_V1,
        )
        assert config.mode == UnifiedActivationMode.UNIFIED_CANARY

    def test_primary_with_fallback_valid(self) -> None:
        config = UnifiedActivationConfig(
            mode=UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK,
            fallback_policy=UnifiedFallbackPolicy.FALLBACK_TO_V1,
        )
        assert config.mode == UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK


class TestProviderPolicyRejection:
    def test_real_provider_policy_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedActivationConfig(
                mode=UnifiedActivationMode.V1_ONLY,
                provider_policy=(
                    UnifiedActivationProviderPolicy.PRODUCT_PROVIDER_EXPLICITLY_AUTHORIZED
                ),
            )


class TestMaterializationInvariant:
    def test_materialization_true_rejected_at_type_level(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedActivationConfig.model_validate(
                {"mode": "V1_ONLY", "materialization_enabled": True}
            )

    def test_materialization_default_is_false(self) -> None:
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY)
        assert config.materialization_enabled is False


class TestCanaryPercentage:
    def test_negative_percentage_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY, canary_percentage=-1)

    def test_over_100_percentage_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY, canary_percentage=101)

    def test_0_and_100_are_valid_boundaries(self) -> None:
        UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY, canary_percentage=0)
        UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY, canary_percentage=100)


class TestHashListNormalization:
    def test_duplicate_hashes_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedActivationConfig(
                mode=UnifiedActivationMode.V1_ONLY,
                package_hash_allowlist=[HASH_A, HASH_A],
            )

    def test_unsorted_hash_list_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedActivationConfig.model_validate(
                {
                    "mode": "V1_ONLY",
                    "package_hash_allowlist": [HASH_B, HASH_A],
                }
            )

    def test_sorted_unique_hash_list_accepted(self) -> None:
        config = UnifiedActivationConfig(
            mode=UnifiedActivationMode.V1_ONLY,
            package_hash_allowlist=sorted([HASH_A, HASH_B]),
        )
        assert config.package_hash_allowlist == sorted([HASH_A, HASH_B])


class TestDenylistPrevails:
    """El contrato permite deliberadamente que un mismo hash aparezca
    en ambas listas -- es la unica forma de demostrar, en tiempo de
    ejecucion (`pipeline/unified_activation_canary_selector.py`), que
    la denylist prevalece incluso sobre una inclusion explicita. Un
    solapamiento NUNCA es un error de configuracion."""

    def test_allowlist_denylist_overlap_accepted(self) -> None:
        config = UnifiedActivationConfig(
            mode=UnifiedActivationMode.V1_ONLY,
            package_hash_allowlist=[HASH_A],
            package_hash_denylist=[HASH_A],
        )
        assert config.package_hash_allowlist == [HASH_A]
        assert config.package_hash_denylist == [HASH_A]

    def test_disjoint_allowlist_and_denylist_accepted(self) -> None:
        config = UnifiedActivationConfig(
            mode=UnifiedActivationMode.V1_ONLY,
            package_hash_allowlist=[HASH_A],
            package_hash_denylist=[HASH_B],
        )
        assert config.package_hash_allowlist == [HASH_A]
        assert config.package_hash_denylist == [HASH_B]

    def test_normalization_still_enforced_within_each_list_when_overlapping(self) -> None:
        """El solapamiento entre listas es valido, pero cada lista
        sigue exigiendo orden/unicidad INTERNA -- ambas invariantes son
        independientes: duplicados dentro de una lista se rechazan
        incluso cuando esa misma lista se superpone con la otra."""
        with pytest.raises(ValidationError):
            UnifiedActivationConfig(
                mode=UnifiedActivationMode.V1_ONLY,
                package_hash_allowlist=[HASH_A, HASH_A],
                package_hash_denylist=[HASH_A],
            )

    def test_config_hash_deterministic_regardless_of_field_declaration_order(self) -> None:
        """`to_stable_json()` (base de `config_hash`, ver
        `pipeline/unified_activation_service.py::_load_config`) debe
        producir bytes identicos sin importar el orden en que se
        declaren los campos al construir el modelo -- las listas de
        hash superpuestas incluidas."""
        config_a = UnifiedActivationConfig(
            mode=UnifiedActivationMode.UNIFIED_CANARY,
            package_hash_allowlist=[HASH_A],
            package_hash_denylist=[HASH_A],
            fallback_policy=UnifiedFallbackPolicy.FALLBACK_TO_V1,
        )
        config_b = UnifiedActivationConfig(
            fallback_policy=UnifiedFallbackPolicy.FALLBACK_TO_V1,
            package_hash_denylist=[HASH_A],
            mode=UnifiedActivationMode.UNIFIED_CANARY,
            package_hash_allowlist=[HASH_A],
        )
        assert config_a.to_stable_json() == config_b.to_stable_json()


class TestCanaryModesRequireFallback:
    def test_canary_without_fallback_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedActivationConfig(
                mode=UnifiedActivationMode.UNIFIED_CANARY,
                fallback_policy=UnifiedFallbackPolicy.NO_FALLBACK,
            )

    def test_primary_without_fallback_rejected(self) -> None:
        with pytest.raises(ValidationError):
            UnifiedActivationConfig(
                mode=UnifiedActivationMode.UNIFIED_PRIMARY_WITH_V1_FALLBACK,
                fallback_policy=UnifiedFallbackPolicy.FAIL_CLOSED,
            )

    def test_v1_only_never_requires_fallback(self) -> None:
        config = UnifiedActivationConfig(
            mode=UnifiedActivationMode.V1_ONLY,
            fallback_policy=UnifiedFallbackPolicy.NO_FALLBACK,
        )
        assert config.fallback_policy == UnifiedFallbackPolicy.NO_FALLBACK


class TestNoOperationalConfigFields:
    def test_config_has_no_path_key_or_endpoint_fields(self) -> None:
        fields = set(UnifiedActivationConfig.model_fields)
        forbidden_substrings = ("path", "key", "endpoint", "model", "token", "secret")
        for field_name in fields:
            lowered = field_name.lower()
            for forbidden in forbidden_substrings:
                assert forbidden not in lowered, f"{field_name!r} parece configuracion operativa"


class TestCanaryStrategyDefault:
    def test_default_strategy_is_explicit_allowlist(self) -> None:
        config = UnifiedActivationConfig(mode=UnifiedActivationMode.V1_ONLY)
        assert config.canary_strategy == UnifiedCanarySelectionStrategy.EXPLICIT_ALLOWLIST
