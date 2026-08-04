"""Aislamiento de proveedor de Fase 13
(`feat/unified-shadow-downstream-pipeline`) -- auditoria de seguridad
solicitada tras el cierre inicial: demuestra, con evidencia ejecutable
(no solo lectura de codigo), que el flujo downstream shadow NUNCA
inicializa un proveedor LLM real, NUNCA lee configuracion global de
proveedor, NUNCA usa variables de entorno de proveedor, rechaza
cualquier `provider` distinto de `DeterministicFakeDraftProvider`, y
NUNCA realiza trafico de red -- ni siquiera un intento fallido."""

from __future__ import annotations

import ast
import inspect
import socket

import pytest

from altamira_extractor.pipeline import (
    unified_shadow_context_adapter,
    unified_shadow_context_assembler,
    unified_shadow_downstream_executor,
    unified_shadow_downstream_service,
    unified_shadow_draft_generator,
    unified_shadow_guardrail_runner,
)
from altamira_extractor.pipeline.unified_shadow_draft_generator import (
    DeterministicFakeDraftProvider,
    DraftGenerationError,
    generate_shadow_rule_draft,
)

from ._unified_shadow_downstream_fixtures import downstream_golden_path
from .test_unified_shadow_draft_generator import _package, _validator

_FASE13_MODULES = (
    unified_shadow_context_adapter,
    unified_shadow_context_assembler,
    unified_shadow_draft_generator,
    unified_shadow_guardrail_runner,
    unified_shadow_downstream_executor,
    unified_shadow_downstream_service,
)

_FORBIDDEN_PROVIDER_TOKENS = (
    "httpx",
    "openai",
    "OpenAI",
    "llm_client",
    "LlmClient",
    "requests",
    "urllib.request",
    "AsyncClient",
)

_FORBIDDEN_SETTINGS_ATTRIBUTES = (
    "llm_provider",
    "openai_api_key",
    "openai_base_url",
    "openai_model",
    "pwc_gateway",
    "ollama",
)


def _imported_names(module: object) -> set[str]:
    """Extrae, via AST (nunca substring sobre el texto crudo, que
    produce falsos positivos contra prosa de documentacion que
    justamente NIEGA estas dependencias), todos los modulos/nombres
    efectivamente importados por `module`."""
    tree = ast.parse(inspect.getsource(module))  # type: ignore[arg-type]
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(alias.name for alias in node.names)
    return names


class TestNoProviderImports:
    """Verifica, mediante analisis AST real del CODIGO de cada modulo
    de Fase 13 (nunca una suposicion, nunca una busqueda de substring
    que confundiria la prosa de documentacion con codigo real), la
    ausencia estructural de cualquier dependencia de red/proveedor
    LLM."""

    @pytest.mark.parametrize("module", _FASE13_MODULES, ids=lambda m: m.__name__)
    def test_module_never_imports_a_real_provider_dependency(self, module: object) -> None:
        imported = _imported_names(module)
        for token in _FORBIDDEN_PROVIDER_TOKENS:
            assert not any(token in name for name in imported), (
                f"{module.__name__!r} importa algo relacionado con {token!r} -- Fase 13 "  # type: ignore[attr-defined]
                "nunca debe depender de un cliente de red/proveedor LLM real"
            )

    def test_service_only_reads_rule_draft_schema_path_from_settings(self) -> None:
        source = inspect.getsource(unified_shadow_downstream_service)
        for attribute in _FORBIDDEN_SETTINGS_ATTRIBUTES:
            assert f"settings.{attribute}" not in source, (
                f"unified_shadow_downstream_service.py lee settings.{attribute} -- "
                "el servicio solo debe leer settings.rule_draft_schema_path"
            )
        assert "settings.rule_draft_schema_path" in source


class TestOnlyDeterministicFakeAccepted:
    def test_generate_shadow_rule_draft_rejects_a_provider_named_like_a_real_one(self) -> None:
        """Un proveedor cuyo NOMBRE sugiere un backend real (para
        descartar que la verificacion dependa de convenciones de
        naming) es rechazado igual que cualquier otro -- la
        verificacion es SIEMPRE por identidad exacta de tipo."""

        class OpenAICompatibleChatClient(DeterministicFakeDraftProvider):
            pass

        package = _package()
        with pytest.raises(DraftGenerationError):
            generate_shadow_rule_draft(
                package=package,
                provider=OpenAICompatibleChatClient(),
                schema_validator=_validator(),
            )

    def test_deterministic_fake_provider_has_no_network_dependent_attributes(self) -> None:
        provider = DeterministicFakeDraftProvider()
        forbidden_attrs = ("base_url", "api_key", "endpoint", "session", "client")
        for attr in forbidden_attrs:
            assert not hasattr(provider, attr), (
                f"DeterministicFakeDraftProvider expone {attr!r} -- no debe tener ningun "
                "atributo relacionado con configuracion de red/proveedor"
            )


class TestNoNetworkTraffic:
    def test_full_chain_never_opens_a_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Parchea `socket.socket.connect` para lanzar si CUALQUIER
        intento de conexion de red ocurre durante la cadena completa
        (adaptador -> ensamblador -> generador de draft -> guardrails)
        -- no una inspeccion de codigo, sino una prueba EJECUTABLE de
        ausencia de trafico de red."""

        def _forbidden_connect(self: socket.socket, address: object) -> None:
            raise AssertionError(
                f"intento de conexion de red detectado durante la cadena downstream shadow: "
                f"{address!r} -- Fase 13 nunca debe abrir un socket"
            )

        monkeypatch.setattr(socket.socket, "connect", _forbidden_connect)

        dgp = downstream_golden_path()
        group = dgp.unified_shadow.shadow_groups[0]
        members_by_id = {m.member_id: m for m in dgp.unified_shadow.shadow_members}
        view = unified_shadow_context_adapter.adapt_group_to_context_view(
            group, members_by_id=members_by_id
        )
        package = unified_shadow_context_assembler.assemble_shadow_context_package(
            view,
            semantic_graph=dgp.semantic_graph,
            source_package_hash=dgp.unified_shadow.source_package_hash,
        )
        result = generate_shadow_rule_draft(
            package=package,
            provider=DeterministicFakeDraftProvider(),
            schema_validator=_validator(),
        )
        report = unified_shadow_guardrail_runner.run_shadow_guardrails(
            result.rule_draft,
            package,
            group_id=group.unified_shadow_candidate_id,
            source_package_hash=dgp.unified_shadow.source_package_hash,
        )
        assert report.verdict.value == "EVIDENCE_VALIDATED"

    def test_executor_never_opens_a_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _forbidden_connect(self: socket.socket, address: object) -> None:
            raise AssertionError(
                f"intento de conexion de red detectado en el executor: {address!r}"
            )

        monkeypatch.setattr(socket.socket, "connect", _forbidden_connect)

        dgp = downstream_golden_path()
        artifact = unified_shadow_downstream_executor.run_unified_shadow_downstream(
            run_id=dgp.unified_shadow.run_id,
            unified_shadow=dgp.unified_shadow,
            validation_report=dgp.validation_report,
            semantic_graph=dgp.semantic_graph,
            provider=DeterministicFakeDraftProvider(),
            schema_validator=_validator(),
        )
        assert artifact.provider.value == "DETERMINISTIC_FAKE"
        assert artifact.disposition.value == "COMPLETED"
