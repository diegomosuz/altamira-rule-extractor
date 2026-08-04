"""Tests del ejecutor PURO de la cadena downstream completa (Fase 13
Parte 9, `feat/unified-shadow-downstream-pipeline`)."""

from __future__ import annotations

import re
from pathlib import Path

import jsonschema
import pytest

from altamira_extractor.contracts.unified_shadow_downstream import (
    UnifiedShadowDownstreamArtifact,
    UnifiedShadowDownstreamDisposition,
    UnifiedShadowDownstreamExecutionStatus,
)
from altamira_extractor.contracts.unified_shadow_validation import (
    UnifiedShadowValidationDisposition,
)
from altamira_extractor.pipeline.rule_draft_assembly import load_rule_draft_schema
from altamira_extractor.pipeline.unified_shadow_downstream_executor import (
    UnifiedShadowDownstreamExecutorError,
    run_unified_shadow_downstream,
)
from altamira_extractor.pipeline.unified_shadow_draft_generator import (
    DeterministicFakeDraftProvider,
)

from ._unified_shadow_downstream_fixtures import (
    downstream_golden_path,
    downstream_golden_path_two_groups,
)
from ._unified_shadow_validation_fixtures import GROUP_ID

_SCHEMA_PATH = Path("schemas/rule-draft.schema.json")


def _validator() -> jsonschema.protocols.Validator:
    schema, _hash = load_rule_draft_schema(_SCHEMA_PATH)
    validator_cls = jsonschema.validators.validator_for(schema)
    return validator_cls(schema)


def _run(**kwargs: object) -> UnifiedShadowDownstreamArtifact:
    dgp = downstream_golden_path()
    base = dict(
        run_id=dgp.unified_shadow.run_id,
        unified_shadow=dgp.unified_shadow,
        validation_report=dgp.validation_report,
        semantic_graph=dgp.semantic_graph,
        provider=DeterministicFakeDraftProvider(),
        schema_validator=_validator(),
    )
    base.update(kwargs)
    return run_unified_shadow_downstream(**base)  # type: ignore[arg-type]


class TestHappyPath:
    def test_completed_disposition_with_one_executed_group(self) -> None:
        artifact = _run()

        assert artifact.disposition == UnifiedShadowDownstreamDisposition.COMPLETED
        assert artifact.summary.executed_group_count == 1
        assert artifact.summary.guardrail_passed_count == 1
        assert artifact.group_results[0].group_id == GROUP_ID
        assert (
            artifact.group_results[0].execution_status
            == UnifiedShadowDownstreamExecutionStatus.EXECUTED
        )

    def test_provider_is_always_deterministic_fake(self) -> None:
        artifact = _run()
        assert artifact.provider.value == "DETERMINISTIC_FAKE"

    def test_execution_is_deterministic_across_two_runs(self) -> None:
        artifact_1 = _run()
        artifact_2 = _run()

        assert artifact_1.to_stable_json() == artifact_2.to_stable_json()


class TestSourceConsistencyValidation:
    def test_run_id_mismatch_raises_technical_error(self) -> None:
        with pytest.raises(UnifiedShadowDownstreamExecutorError):
            _run(run_id="not-the-real-run-id")

    def test_unified_shadow_run_id_mismatch_raises(self) -> None:
        dgp = downstream_golden_path()
        mutated = dgp.unified_shadow.model_copy(update={"run_id": "other-run-id"})
        with pytest.raises(UnifiedShadowDownstreamExecutorError):
            _run(unified_shadow=mutated)

    def test_source_package_hash_mismatch_raises(self) -> None:
        dgp = downstream_golden_path()
        other_hash = "b" * 64
        mutated = dgp.validation_report.model_copy(update={"source_package_hash": other_hash})
        with pytest.raises(UnifiedShadowDownstreamExecutorError):
            _run(validation_report=mutated)

    def test_stale_unified_candidates_shadow_hash_raises(self) -> None:
        dgp = downstream_golden_path()
        mutated = dgp.validation_report.model_copy(
            update={"unified_candidates_shadow_hash": "c" * 64}
        )
        with pytest.raises(UnifiedShadowDownstreamExecutorError):
            _run(validation_report=mutated)

    def test_candidate_v1_artifact_hash_mismatch_raises(self) -> None:
        dgp = downstream_golden_path()
        mutated = dgp.validation_report.model_copy(update={"candidate_v1_artifact_hash": "d" * 64})
        with pytest.raises(UnifiedShadowDownstreamExecutorError):
            _run(validation_report=mutated)

    def test_member_id_referenced_by_validation_but_absent_in_unified_shadow_raises(self) -> None:
        dgp = downstream_golden_path()
        gv = dgp.validation_report.group_validations[0].model_copy(
            update={"member_ids": ["member::does-not-exist"]}
        )
        mutated_report = dgp.validation_report.model_copy(update={"group_validations": [gv]})
        with pytest.raises(UnifiedShadowDownstreamExecutorError):
            _run(validation_report=mutated_report)


class TestValidationDispositionPolicy:
    @pytest.mark.parametrize(
        "disposition",
        [
            UnifiedShadowValidationDisposition.REVIEW_REQUIRED,
            UnifiedShadowValidationDisposition.BLOCKED,
            UnifiedShadowValidationDisposition.NOT_EVALUATED,
        ],
    )
    def test_non_qualified_validation_disposition_produces_not_executed(
        self, disposition: UnifiedShadowValidationDisposition
    ) -> None:
        dgp = downstream_golden_path()
        mutated_report = dgp.validation_report.model_copy(update={"disposition": disposition})

        artifact = _run(validation_report=mutated_report)

        assert artifact.disposition == UnifiedShadowDownstreamDisposition.NOT_EXECUTED
        assert all(
            gr.execution_status == UnifiedShadowDownstreamExecutionStatus.SKIPPED_NOT_ELIGIBLE
            for gr in artifact.group_results
        )
        assert artifact.context_packages == []
        assert artifact.rule_drafts == []
        assert artifact.guardrail_results == []

    def test_qualified_with_warnings_still_executes_eligible_groups(self) -> None:
        dgp = downstream_golden_path()
        mutated_report = dgp.validation_report.model_copy(
            update={"disposition": UnifiedShadowValidationDisposition.QUALIFIED_WITH_WARNINGS}
        )

        artifact = _run(validation_report=mutated_report)

        assert artifact.disposition == UnifiedShadowDownstreamDisposition.COMPLETED

    def test_structurally_not_eligible_group_is_skipped_even_if_disposition_qualified(
        self,
    ) -> None:
        dgp = downstream_golden_path()
        gv = dgp.validation_report.group_validations[0].model_copy(
            update={"downstream_shadow_eligible": False}
        )
        mutated_report = dgp.validation_report.model_copy(update={"group_validations": [gv]})

        artifact = _run(validation_report=mutated_report)

        assert artifact.disposition == UnifiedShadowDownstreamDisposition.NOT_EXECUTED
        assert (
            artifact.group_results[0].execution_status
            == UnifiedShadowDownstreamExecutionStatus.SKIPPED_NOT_ELIGIBLE
        )


class TestPerGroupFailureIsolation:
    def test_one_group_context_assembly_failure_does_not_affect_the_other(self) -> None:
        dgp = downstream_golden_path_two_groups()

        artifact = run_unified_shadow_downstream(
            run_id=dgp.unified_shadow.run_id,
            unified_shadow=dgp.unified_shadow,
            validation_report=dgp.validation_report,
            semantic_graph=dgp.semantic_graph,
            provider=DeterministicFakeDraftProvider(),
            schema_validator=_validator(),
        )

        by_id = {gr.group_id: gr for gr in artifact.group_results}
        assert by_id[GROUP_ID].execution_status == UnifiedShadowDownstreamExecutionStatus.EXECUTED
        assert (
            by_id["group::candidate-2"].execution_status
            == UnifiedShadowDownstreamExecutionStatus.CONTEXT_ASSEMBLY_FAILED
        )
        assert by_id["group::candidate-2"].diagnostics != []
        assert artifact.disposition == UnifiedShadowDownstreamDisposition.BLOCKED
        assert artifact.summary.context_package_count == 1
        assert artifact.summary.rule_draft_count == 1

    def test_draft_generation_failure_is_isolated_and_produces_blocked(self) -> None:
        artifact = _run(provider=DeterministicFakeDraftProvider(inject_unresolvable_alias=True))

        assert artifact.disposition == UnifiedShadowDownstreamDisposition.BLOCKED
        assert (
            artifact.group_results[0].execution_status
            == UnifiedShadowDownstreamExecutionStatus.DRAFT_GENERATION_FAILED
        )
        assert artifact.group_results[0].diagnostics != []
        assert artifact.rule_drafts == []


class TestArtifactHasNoTimestamps:
    """Auditoria de seguridad de cierre de Fase 13, Parte 3: el
    artefacto persistido no debe contener NINGUNA clave/valor temporal,
    en ningun nivel -- ni derivado de `run_id`, ni de la hora actual."""

    _FORBIDDEN_KEYS = (
        '"evaluated_at"',
        '"created_at"',
        '"updated_at"',
        '"generated_at"',
        '"timestamp"',
        '"datetime"',
    )
    _ISO_DATETIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")

    def test_full_serialization_has_no_temporal_key(self) -> None:
        artifact = _run()
        serialized = artifact.to_stable_json()
        for forbidden_key in self._FORBIDDEN_KEYS:
            assert forbidden_key not in serialized, f"clave temporal encontrada: {forbidden_key}"

    def test_full_serialization_has_no_key_ending_in__at(self) -> None:
        artifact = _run()
        payload = artifact.model_dump(mode="json")

        def _walk(node: object) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    assert not key.endswith("_at"), f"clave temporal encontrada: {key!r}"
                    _walk(value)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(payload)

    def test_full_serialization_has_no_iso_datetime_value(self) -> None:
        artifact = _run()
        serialized = artifact.to_stable_json()
        assert self._ISO_DATETIME_PATTERN.search(serialized) is None

    def test_two_executions_produce_byte_identical_output(self) -> None:
        artifact_1 = _run()
        artifact_2 = _run()
        assert artifact_1.to_stable_json() == artifact_2.to_stable_json()

    def test_changing_the_system_clock_does_not_change_the_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Parchea `datetime.datetime.now`/`utcnow` para retornar un
        valor absurdo (anio 2999) -- si el resultado dependiera, aunque
        sea indirectamente, de la hora del sistema, esto lo revelaria."""
        import datetime as datetime_module

        class _FrozenFutureDatetime(datetime_module.datetime):
            @classmethod
            def now(cls, tz: object = None) -> datetime_module.datetime:  # type: ignore[override]
                return cls(2999, 1, 1, tzinfo=tz)  # type: ignore[arg-type]

            @classmethod
            def utcnow(cls) -> _FrozenFutureDatetime:
                return cls(2999, 1, 1)

        baseline = _run().to_stable_json()

        monkeypatch.setattr(datetime_module, "datetime", _FrozenFutureDatetime)
        with_frozen_future_clock = _run().to_stable_json()

        assert baseline == with_frozen_future_clock

    def test_no_fase13_module_calls_datetime_now_or_utcnow_or_time_time(self) -> None:
        """Analiza el AST real (nunca substring sobre texto crudo, que
        confundiria la prosa de documentacion -- p. ej. `unified_shadow_
        guardrail_runner.py` describe en su docstring que NO usa estas
        llamadas -- con una llamada real) buscando nodos `Call` cuyo
        atributo sea `now`/`utcnow`/`time`."""
        import ast
        import inspect

        from altamira_extractor.pipeline import (
            unified_shadow_context_adapter,
            unified_shadow_context_assembler,
            unified_shadow_downstream_executor,
            unified_shadow_downstream_service,
            unified_shadow_draft_generator,
            unified_shadow_guardrail_runner,
        )

        forbidden_attrs = {"now", "utcnow", "time"}
        for module in (
            unified_shadow_context_adapter,
            unified_shadow_context_assembler,
            unified_shadow_draft_generator,
            unified_shadow_guardrail_runner,
            unified_shadow_downstream_executor,
            unified_shadow_downstream_service,
        ):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in forbidden_attrs
                ):
                    raise AssertionError(
                        f"{module.__name__} invoca .{node.func.attr}() en linea "
                        f"{node.lineno} -- prohibido en Fase 13"
                    )
