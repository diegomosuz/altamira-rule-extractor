"""Tests de orquestacion de GUARDRAILS_APPLIED (Prompt 12): atomicidad
por etapa, fail-fast en el primer REJECTED definitivo, repair loop
acotado, fast-path de idempotencia y persistencia de
artifacts/09-guardrails/ sin sobrescribir artifacts/08-rule-drafts/.

Cliente LLM inyectado por monkeypatch (mismo patron que Prompt 11): sin
`httpx` real, sin red."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.context_manifest import (
    ContextDirectoryManifest,
    ContextRecord,
    QueryRecord,
)
from altamira_extractor.contracts.context_package import (
    BatchContext,
    CodeSliceEntry,
    Completeness,
    ContextPackage,
    ContextPackageCandidate,
    ContextPackageDecision,
    ContextPackageOperation,
    ContextPackageScope,
    DataContext,
    Effects,
    EvidenceEntry,
)
from altamira_extractor.contracts.enums import (
    BatchContextStatus,
    CompletenessStatus,
    EvidenceValidationStatus,
    InclusionReason,
    PipelineStage,
    StageStatus,
)
from altamira_extractor.contracts.guardrail_candidate import GuardrailCandidateArtifact
from altamira_extractor.contracts.guardrail_manifest import GuardrailDirectoryManifest
from altamira_extractor.contracts.rule_draft import Claim, ClaimField, RuleDraft
from altamira_extractor.contracts.rule_draft_manifest import (
    RuleDraftDirectoryManifest,
    RuleDraftRecord,
)
from altamira_extractor.contracts.run_state import StageExecution
from altamira_extractor.pipeline import guardrails_applied_stage as stage_module
from altamira_extractor.pipeline.errors import GuardrailError, LlmTimeoutError
from altamira_extractor.pipeline.evidence_catalog import build_evidence_catalog
from altamira_extractor.pipeline.guardrails_applied_stage import run_guardrails_applied_stage

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_RULE_REPAIR_SYSTEM_PATH = REPO_ROOT / "prompts" / "rule_repair_system.md"
REAL_RULE_REPAIR_USER_PATH = REPO_ROOT / "prompts" / "rule_repair_user.md"

_HASH_A = "a" * 64
_RUN_ID = "run-1"


def _stage(stage: PipelineStage, status: StageStatus) -> StageExecution:
    now = datetime.now(UTC)
    kwargs: dict[str, Any] = {"stage": stage, "status": status}
    if status in (StageStatus.SUCCEEDED, StageStatus.FAILED):
        kwargs["started_at"] = now
        kwargs["finished_at"] = now
    if status == StageStatus.FAILED:
        kwargs["error"] = "fallo simulado"
    return StageExecution(**kwargs)


def _succeeded_stages() -> list[StageExecution]:
    return [_stage(PipelineStage.RULE_DRAFTS_GENERATED, StageStatus.SUCCEEDED)]


def _package(candidate_id: str, decision_id: str = "dec-1") -> ContextPackage:
    evidence = EvidenceEntry(
        evidence_id="ev-1",
        kind="decision",
        source_file="cobol/PROG1.cbl",
        line_start=10,
        line_end=10,
        source_package_hash=_HASH_A,
    )
    return ContextPackage(
        schema_version="2.0",
        candidate=ContextPackageCandidate(
            candidate_id=candidate_id,
            decision_id=decision_id,
            detector_id="det",
            detector_version="1.0",
            detector_score=1.0,
        ),
        scope=ContextPackageScope(
            country="AR",
            application="Transferencias",
            operation=ContextPackageOperation(logical_name="OP1", description=None),
            program="PROG1",
            program_version="1",
            paragraph="MAIN",
            source_file="cobol/PROG1.cbl",
            line_start=10,
            line_end=10,
            source_package_hash=_HASH_A,
        ),
        code_slice=[
            CodeSliceEntry(
                paragraph_id="p1",
                paragraph="MAIN",
                source_file="cobol/PROG1.cbl",
                source_text="IF WS-COD = 'R001'",
                line_start=10,
                line_end=10,
                inclusion_reason=InclusionReason.CANDIDATE,
                evidence_ids=["ev-1"],
            )
        ],
        data_context=DataContext(parameter_tables=[], transactional_tables_read=[]),
        decision=ContextPackageDecision(
            expression="WS-COD = 'R001'",
            normalized_expression="WS-COD = 'R001'",
            operands=[],
            rule_type=None,
            outcome_code="R001",
            evidence_ids=["ev-1"],
        ),
        effects=Effects(return_codes=[], table_effects=[]),
        batch_context=BatchContext(status=BatchContextStatus.NOT_AVAILABLE, downstream_jobs=[]),
        domain_glossary=[],
        evidence=[evidence],
        completeness=Completeness(
            D1=CompletenessStatus.COMPLETE,
            D2=CompletenessStatus.COMPLETE,
            D3=CompletenessStatus.NOT_AVAILABLE,
            D4=CompletenessStatus.COMPLETE,
            D5=CompletenessStatus.NOT_AVAILABLE,
            D6=CompletenessStatus.NOT_AVAILABLE,
            D7=CompletenessStatus.NOT_AVAILABLE,
        ),
    )


def _package_with_extra_evidence(candidate_id: str) -> ContextPackage:
    """Variante de `_package()` con una segunda evidencia (`ev-2`) citada
    por un `code_slice` adicional -- usada para confirmar que el
    catalogo de alias de un candidato es independiente del de otro
    dentro de la misma corrida."""
    package = _package(candidate_id)
    extra_evidence = EvidenceEntry(
        evidence_id="ev-2",
        kind="code_slice",
        source_file="cobol/PROG1.cbl",
        line_start=30,
        line_end=30,
        source_package_hash=_HASH_A,
    )
    extra_slice = CodeSliceEntry(
        paragraph_id="p2",
        paragraph="OTHER",
        source_file="cobol/PROG1.cbl",
        source_text="ADD 1 TO WS-CONTADOR",
        line_start=30,
        line_end=30,
        inclusion_reason=InclusionReason.DATA_DEPENDENCY,
        evidence_ids=["ev-2"],
    )
    return package.model_copy(
        update={
            "evidence": [*package.evidence, extra_evidence],
            "code_slice": [*package.code_slice, extra_slice],
        }
    )


def _filename(candidate_id: str) -> str:
    return hashlib.sha256(candidate_id.encode("utf-8")).hexdigest() + ".json"


def _valid_draft(*, evidence_id: str = "ev-1") -> RuleDraft:
    # evidence_paths usa el path de CONTENEDOR ("$.decision", no un campo
    # escalar interno): asi coincide con la granularidad que
    # `build_evidence_catalog` usa para sus entradas, y `evidence_id="ev-1"`
    # (el caso valido) resuelve a un alias real cuando
    # `redact_rule_draft_for_prompt` lo redacta para el prompt de
    # reparacion de guardrail.
    return RuleDraft(
        schema_version="2.0",
        title="Titulo",
        context="Contexto",
        statement="Enunciado",
        condition="WS-COD = 'R001'",
        parameters=[],
        effect="Efecto",
        parameter_source=None,
        traceability=["Evidencia trazada mediante el catalogo de alias del candidato"],
        limitations=["Requiere revision funcional"],
        claims=[
            Claim(
                claim_id="c1",
                field=ClaimField.CONDITION,
                evidence_paths=["$.decision"],
                evidence_ids=[evidence_id],
            )
        ],
        evidence_validation_status=EvidenceValidationStatus.PENDING,
    )


def _write_context_directory(context_dir: Path, packages: list[ContextPackage]) -> None:
    context_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for package in packages:
        filename = _filename(package.candidate.candidate_id)
        (context_dir / filename).write_text(package.to_stable_json(), encoding="utf-8")
        records.append(
            ContextRecord(
                candidate_id=package.candidate.candidate_id,
                paragraph_id=package.scope.paragraph,
                decision_id=package.candidate.decision_id,
                relative_filename=filename,
                context_hash=hashlib.sha256(
                    package.to_stable_json().encode("utf-8")
                ).hexdigest(),
            )
        )
    records.sort(key=lambda r: r.candidate_id)
    manifest = ContextDirectoryManifest(
        run_id=_RUN_ID,
        source_package_hash=_HASH_A,
        semantic_graph_hash="b" * 64,
        candidate_artifact_hash="c" * 64,
        q0_query_hash="d" * 64,
        invariants_query_hash="e" * 64,
        context_schema_hash="f" * 64,
        dependency_depth=4,
        max_code_slice_paragraphs=200,
        max_transactional_tables=100,
        max_parameter_entries_per_context=1000,
        query_records=[
            QueryRecord(
                logical_query=name,  # type: ignore[arg-type]
                relative_path=f"queries/v1/{name.lower()}.cypher",
                template_hash="0" * 64,
                effective_query_hash="0" * 64,
            )
            for name in ("Q1", "Q2", "Q3A", "Q3B", "Q4", "Q5A", "Q5B", "Q6", "Q7")
        ],
        context_records=records,
        context_count=len(records),
        warnings=[],
    )
    (context_dir / "context-manifest.json").write_text(manifest.to_stable_json(), encoding="utf-8")


def _write_rule_draft_directory(
    rule_draft_dir: Path, context_dir: Path, drafts: dict[str, RuleDraft]
) -> None:
    rule_draft_dir.mkdir(parents=True, exist_ok=True)
    context_manifest = ContextDirectoryManifest.model_validate_json(
        (context_dir / "context-manifest.json").read_text(encoding="utf-8")
    )
    context_hash_by_id = {r.candidate_id: r.context_hash for r in context_manifest.context_records}

    records = []
    for candidate_id, draft in drafts.items():
        filename = _filename(candidate_id)
        (rule_draft_dir / filename).write_text(draft.to_stable_json(), encoding="utf-8")
        records.append(
            RuleDraftRecord(
                candidate_id=candidate_id,
                context_hash=context_hash_by_id[candidate_id],
                relative_filename=filename,
                rule_draft_hash=hashlib.sha256(draft.to_stable_json().encode("utf-8")).hexdigest(),
                writer_user_effective_hash="0" * 64,
                response_hash="0" * 64,
            )
        )
    records.sort(key=lambda r: r.candidate_id)
    manifest = RuleDraftDirectoryManifest(
        run_id=_RUN_ID,
        source_package_hash=_HASH_A,
        context_manifest_hash=hashlib.sha256(
            (context_dir / "context-manifest.json").read_bytes()
        ).hexdigest(),
        rule_draft_schema_hash="1" * 64,
        provider="openai",
        model="gpt-test",
        writer_system_template_hash="2" * 64,
        writer_user_template_hash="3" * 64,
        records=records,
        draft_count=len(records),
        warnings=[],
    )
    (rule_draft_dir / "rule-draft-manifest.json").write_text(
        manifest.to_stable_json(), encoding="utf-8"
    )


def _write_repair_prompt_files(tmp_path: Path) -> tuple[Path, Path]:
    system_path = tmp_path / "rule_repair_system.md"
    user_path = tmp_path / "rule_repair_user.md"
    system_path.write_text("Corrige el RuleDraft rechazado.", encoding="utf-8")
    # checkpoint correctivo: los marcadores de prosa coinciden EXACTAMENTE
    # con los del prompt productivo real (prompts/rule_repair_user.md:
    # "REJECTED_RULE_DRAFT:"/"GUARDRAIL_VIOLATIONS:"), no con nombres
    # arbitrarios de fixture -- asi la extraccion por marcador que hacen
    # los tests de este archivo se ejercita contra el mismo texto que
    # produciria el prompt real, en vez de una convencion solo-de-test
    # que podia divergir en silencio.
    user_path.write_text(
        "CONTEXT_PACKAGE:\n{{CONTEXT_PACKAGE_JSON}}\n\n"
        "EVIDENCE_CATALOG:\n{{EVIDENCE_CATALOG_JSON}}\n\n"
        "REJECTED_RULE_DRAFT:\n{{REJECTED_RULE_DRAFT_JSON}}\n\n"
        "GUARDRAIL_VIOLATIONS:\n{{GUARDRAIL_VIOLATIONS_JSON}}\n",
        encoding="utf-8",
    )
    return system_path, user_path


def _decision_alias() -> str:
    catalog = build_evidence_catalog(_package("cand-1"))
    alias = catalog.find_alias("ev-1", "$.decision")
    assert alias is not None
    return alias


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    system_path, user_path = _write_repair_prompt_files(tmp_path)
    defaults: dict[str, Any] = {
        "data_dir": tmp_path / "data",
        "runs_dir": tmp_path / "data" / "runs",
        "incoming_dir": tmp_path / "data" / "incoming",
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "sk-test-key",
        "OPENAI_BASE_URL": "https://api.example.com/v1",
        "OPENAI_MODEL": "gpt-test",
        "rule_repair_system_prompt_path": system_path,
        "rule_repair_user_prompt_path": user_path,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _valid_repair_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Titulo reparado",
        "context": "Contexto",
        "statement": "Enunciado reparado",
        "condition": "WS-COD = 'R001'",
        "parameters": [],
        "effect": "Efecto",
        "parameter_source": None,
        "traceability": ["Evidencia trazada mediante el catalogo de alias del candidato"],
        "limitations": ["Requiere revision funcional"],
        "claims": [
            {
                "claim_id": "c1",
                "field": "condition",
                "evidence_refs": [_decision_alias()],
            }
        ],
    }
    payload.update(overrides)
    return payload


def _still_bad_repair_payload() -> dict[str, Any]:
    """Respuesta de reparacion que sigue siendo invalida (checkpoint
    correctivo: un alias inexistente, nunca un evidence_id/evidence_path
    directo -- el modelo ya no puede inventar esos valores, solo un
    alias que no resuelve contra el catalogo)."""
    return _valid_repair_payload(
        claims=[{"claim_id": "c1", "field": "condition", "evidence_refs": ["E999"]}]
    )


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch, responses: list[dict[str, Any] | BaseException]
) -> list[list[Any]]:
    calls: list[list[Any]] = []
    queue = list(responses)

    class _FakeClient:
        def __init__(self, profile: Any, **kwargs: Any) -> None:
            self.profile = profile

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def complete(self, messages: list[Any]) -> dict[str, Any]:
            calls.append(messages)
            if not queue:
                raise AssertionError("el fake client se llamo mas veces de lo esperado")
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _FakeClient)
    return calls


class _PoisonClient:
    """Construir la instancia es inofensivo (no abre conexiones de red);
    lo que nunca debe ocurrir es invocar `.complete()`."""

    def __init__(self, profile: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _PoisonClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def complete(self, messages: list[Any]) -> dict[str, Any]:
        raise AssertionError("no deberia llamarse al modelo LLM")


def _base_kwargs(
    tmp_path: Path, *, packages: list[ContextPackage], drafts: dict[str, RuleDraft]
) -> dict[str, Any]:
    context_dir = tmp_path / "07-context"
    rule_draft_dir = tmp_path / "08-rule-drafts"
    _write_context_directory(context_dir, packages)
    _write_rule_draft_directory(rule_draft_dir, context_dir, drafts)
    return {
        "run_id": _RUN_ID,
        "source_package_hash": _HASH_A,
        "run_stages": _succeeded_stages(),
        "context_dir": context_dir,
        "rule_draft_dir": rule_draft_dir,
        "guardrail_dir": tmp_path / "09-guardrails",
        "settings": _settings(tmp_path),
    }


# --- precondicion / drift ---


def test_missing_rule_drafts_generated_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": _valid_draft()}
    )
    kwargs["run_stages"] = []
    with pytest.raises(GuardrailError):
        run_guardrails_applied_stage(**kwargs)


def test_rule_drafts_generated_not_succeeded_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": _valid_draft()}
    )
    kwargs["run_stages"] = [_stage(PipelineStage.RULE_DRAFTS_GENERATED, StageStatus.FAILED)]
    with pytest.raises(GuardrailError):
        run_guardrails_applied_stage(**kwargs)


def test_rule_draft_hash_drift_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": _valid_draft()}
    )
    tampered = _valid_draft().model_copy(update={"title": "Tampered"})
    (kwargs["rule_draft_dir"] / _filename("cand-1")).write_text(
        tampered.to_stable_json(), encoding="utf-8"
    )
    with pytest.raises(GuardrailError):
        run_guardrails_applied_stage(**kwargs)


# --- flujo feliz sin reparacion ---


def test_happy_path_no_violations_no_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": _valid_draft()}
    )

    warnings = run_guardrails_applied_stage(**kwargs)

    assert warnings == ["1 guardrail(s)"]
    manifest = GuardrailDirectoryManifest.model_validate_json(
        (kwargs["guardrail_dir"] / "guardrail-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.guardrail_count == 1
    record = manifest.records[0]
    assert record.repair_attempts_used == 0
    assert record.final_evidence_validation_status == EvidenceValidationStatus.EVIDENCE_VALIDATED


def test_08_stays_pending_and_09_final_draft_is_evidence_validated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    initial_draft = _valid_draft()
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": initial_draft}
    )

    run_guardrails_applied_stage(**kwargs)

    # artifacts/08-rule-drafts/ nunca se toca: sigue exactamente PENDING,
    # byte-identico al que ya estaba antes de correr la etapa.
    draft_in_08 = RuleDraft.model_validate_json(
        (kwargs["rule_draft_dir"] / _filename("cand-1")).read_text(encoding="utf-8")
    )
    assert draft_in_08.evidence_validation_status == EvidenceValidationStatus.PENDING
    assert draft_in_08 == initial_draft

    manifest = GuardrailDirectoryManifest.model_validate_json(
        (kwargs["guardrail_dir"] / "guardrail-manifest.json").read_text(encoding="utf-8")
    )
    record = manifest.records[0]
    artifact = GuardrailCandidateArtifact.model_validate_json(
        (kwargs["guardrail_dir"] / record.relative_filename).read_text(encoding="utf-8")
    )

    # final_rule_draft en 09 SI refleja el veredicto real.
    assert artifact.final_rule_draft.evidence_validation_status == (
        EvidenceValidationStatus.EVIDENCE_VALIDATED
    )
    assert artifact.final_rule_draft.functional_review_status.value == "NEEDS_FUNCTIONAL_REVIEW"
    assert artifact.guardrail_report.verdict.value == "EVIDENCE_VALIDATED"

    # El hash inicial referencia exactamente el archivo PENDING de 08.
    expected_initial_hash = hashlib.sha256(
        draft_in_08.to_stable_json().encode("utf-8")
    ).hexdigest()
    assert artifact.initial_rule_draft_hash == expected_initial_hash
    assert record.initial_rule_draft_hash == expected_initial_hash

    # El hash final corresponde al draft CON el estado final (EVIDENCE_
    # VALIDATED), no al PENDING: sin reparaciones, el contenido funcional
    # es identico, pero el hash difiere porque el campo de estado cambio.
    expected_final_hash = hashlib.sha256(
        artifact.final_rule_draft.to_stable_json().encode("utf-8")
    ).hexdigest()
    assert artifact.final_rule_draft_hash == expected_final_hash
    assert artifact.final_rule_draft_hash != artifact.initial_rule_draft_hash


def test_empty_candidates_no_llm_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    kwargs = _base_kwargs(tmp_path, packages=[], drafts={})

    warnings = run_guardrails_applied_stage(**kwargs)

    assert warnings == ["0 guardrail(s)"]


# --- repair loop ---


def test_repair_succeeds_within_budget(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bad_draft = _valid_draft(evidence_id="ev-nonexistent")
    calls = _install_fake_client(monkeypatch, [_valid_repair_payload()])
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": bad_draft}
    )

    warnings = run_guardrails_applied_stage(**kwargs)

    assert warnings == ["1 guardrail(s)"]
    assert len(calls) == 1
    manifest = GuardrailDirectoryManifest.model_validate_json(
        (kwargs["guardrail_dir"] / "guardrail-manifest.json").read_text(encoding="utf-8")
    )
    record = manifest.records[0]
    assert record.repair_attempts_used == 1
    assert len(record.repair_response_hashes) == 1


def test_repair_prompt_rejected_draft_never_exposes_real_evidence_id_or_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """checkpoint correctivo (catalogo de alias): `current_draft.
    to_stable_json()` ya NO se envia directo al modelo durante una
    reparacion de guardrail -- `redact_rule_draft_for_prompt` lo
    convierte primero a `evidence_refs` (alias), para que el propio
    ejemplo de "borrador rechazado" nunca filtre el evidence_id/
    evidence_path real (lo que reintroduciria el problema original en
    esta capa). El ContextPackage completo SI viaja en este prompt (por
    diseno, siempre lo hizo: la reparacion de guardrail necesita
    contexto funcional completo) -- por eso la asercion se limita a la
    seccion REJECTED_RULE_DRAFT, no al mensaje completo.

    checkpoint correctivo (estabilizacion de baseline): los marcadores
    de extraccion coinciden con los del prompt productivo real
    (`REJECTED_RULE_DRAFT:`/`GUARDRAIL_VIOLATIONS:`, ver
    `_write_repair_prompt_files` arriba), y el mensaje se normaliza
    `\\r\\n` -> `\\n` antes de buscarlos: `render_prompt` escribe el
    texto del template tal cual lo lee `prompt_loader.load_prompt_template`
    (via `Path.read_bytes()`, sin traduccion de saltos de linea), y
    `_write_repair_prompt_files` escribe el archivo con `Path.write_text`
    -- en Windows, el modo texto por defecto traduce cada `\\n` de la
    cadena Python a `\\r\\n` en disco, asi que el contenido efectivo
    puede llegar con `\\r\\n` sin que ningun paso lo normalice de vuelta.
    Normalizar aqui hace la busqueda de marcadores portable sin depender
    de la codificacion de saltos de linea de la plataforma que ejecuta
    el test."""
    bad_draft = _valid_draft(evidence_id="ev-nonexistent")
    alias = _decision_alias()
    calls = _install_fake_client(monkeypatch, [_valid_repair_payload()])
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": bad_draft}
    )

    run_guardrails_applied_stage(**kwargs)

    repair_user_message = calls[0][1].content
    normalized_message = repair_user_message.replace("\r\n", "\n")

    rejected_marker = "REJECTED_RULE_DRAFT:\n"
    violations_marker = "\n\nGUARDRAIL_VIOLATIONS:"
    assert rejected_marker in normalized_message, (
        f"marcador {rejected_marker!r} ausente del prompt de reparacion renderizado"
    )
    assert violations_marker in normalized_message, (
        f"marcador {violations_marker!r} ausente del prompt de reparacion renderizado"
    )

    rejected_section = normalized_message.split(rejected_marker, 1)[1].split(
        violations_marker, 1
    )[0]
    assert "evidence_ids" not in rejected_section
    assert "evidence_paths" not in rejected_section
    assert "ev-nonexistent" not in rejected_section
    assert "evidence_refs" in rejected_section
    # el catalogo de alias si viaja (checkpoint correctivo activo) -- se
    # busca en el mensaje normalizado completo, no solo en la seccion
    # rechazada, porque el catalogo vive en su propio bloque separado.
    assert alias in normalized_message


def test_successful_repair_preserves_produced_hash_and_final_hash_differs_by_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad_draft = _valid_draft(evidence_id="ev-nonexistent")
    _install_fake_client(monkeypatch, [_valid_repair_payload()])
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": bad_draft}
    )

    run_guardrails_applied_stage(**kwargs)

    manifest = GuardrailDirectoryManifest.model_validate_json(
        (kwargs["guardrail_dir"] / "guardrail-manifest.json").read_text(encoding="utf-8")
    )
    record = manifest.records[0]
    artifact = GuardrailCandidateArtifact.model_validate_json(
        (kwargs["guardrail_dir"] / record.relative_filename).read_text(encoding="utf-8")
    )

    assert len(artifact.repair_history) == 1
    attempt = artifact.repair_history[0]
    assert attempt.structurally_valid is True
    assert attempt.produced_rule_draft_hash is not None

    # El hash producido por la reparacion (PENDING) se conserva tal cual
    # en el historial -- nunca se reemplaza ni se recalcula con el
    # estado final.
    pending_final = artifact.final_rule_draft.model_copy(
        update={"evidence_validation_status": EvidenceValidationStatus.PENDING}
    )
    pending_final_hash = hashlib.sha256(pending_final.to_stable_json().encode("utf-8")).hexdigest()
    assert attempt.produced_rule_draft_hash == pending_final_hash

    # final_rule_draft_hash es DISTINTO del hash producido por la
    # reparacion porque el estado determinístico (EVIDENCE_VALIDATED) se
    # aplico DESPUES del veredicto -- el contenido funcional es el mismo,
    # solo el campo de estado cambia.
    assert artifact.final_rule_draft_hash != attempt.produced_rule_draft_hash
    assert artifact.final_rule_draft.evidence_validation_status == (
        EvidenceValidationStatus.EVIDENCE_VALIDATED
    )

    # artifacts/08-rule-drafts/ nunca cambia: sigue siendo el draft
    # original invalido, PENDING.
    draft_in_08 = RuleDraft.model_validate_json(
        (kwargs["rule_draft_dir"] / _filename("cand-1")).read_text(encoding="utf-8")
    )
    assert draft_in_08 == bad_draft
    assert draft_in_08.evidence_validation_status == EvidenceValidationStatus.PENDING


def test_repair_structural_failure_then_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad_draft = _valid_draft(evidence_id="ev-nonexistent")
    calls = _install_fake_client(
        monkeypatch, [{"malformed": "missing required fields"}, _valid_repair_payload()]
    )
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": bad_draft}
    )
    kwargs["settings"] = _settings(tmp_path)

    warnings = run_guardrails_applied_stage(**kwargs)

    assert warnings == ["1 guardrail(s)"]
    assert len(calls) == 2
    manifest = GuardrailDirectoryManifest.model_validate_json(
        (kwargs["guardrail_dir"] / "guardrail-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.records[0].repair_attempts_used == 2


def test_exhausting_repairs_without_success_is_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad_draft = _valid_draft(evidence_id="ev-nonexistent")
    still_bad_payload = _still_bad_repair_payload()
    _install_fake_client(monkeypatch, [still_bad_payload, still_bad_payload])
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": bad_draft}
    )

    with pytest.raises(GuardrailError):
        run_guardrails_applied_stage(**kwargs)

    assert not kwargs["guardrail_dir"].exists()


# --- checkpoint correctivo: observabilidad de fallos de reparacion ---


def _failure_diagnostics_path(kwargs: dict[str, Any]) -> Path:
    return kwargs["guardrail_dir"].parent / "guardrails-failure-diagnostics.json"


def test_exhausted_repair_persists_failure_diagnostics_without_promoting_guardrail_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """checkpoint correctivo (observabilidad de fallos de reparacion):
    cuando un candidato agota LLM_REPAIR_ATTEMPTS, un artefacto de
    diagnostico NO contractual (hermano de `09-guardrails/`, nunca
    dentro) sobrevive con la violation real -- sin comprometer la
    atomicidad del directorio contractual, que sigue sin promoverse."""
    bad_draft = _valid_draft(evidence_id="ev-nonexistent")
    still_bad_payload = _still_bad_repair_payload()
    _install_fake_client(monkeypatch, [still_bad_payload, still_bad_payload])
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": bad_draft}
    )

    with pytest.raises(GuardrailError):
        run_guardrails_applied_stage(**kwargs)

    assert not kwargs["guardrail_dir"].exists()
    diagnostics_path = _failure_diagnostics_path(kwargs)
    assert diagnostics_path.is_file()
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))

    assert diagnostics["candidate_id"] == "cand-1"
    assert diagnostics["attempt_count"] == 2
    assert diagnostics["last_verdict"] == "REJECTED"
    assert diagnostics["response_hashes"]
    assert len(diagnostics["response_hashes"]) == 2
    assert diagnostics["violations"]
    violation = diagnostics["violations"][0]
    assert {"violation_id", "rule", "field", "message", "severity"} <= violation.keys()
    assert diagnostics["claims"]

    # nunca se persiste el prompt efectivo ni la respuesta cruda: solo
    # identificadores, hashes y mensajes ya sanitizados por el guardrail.
    raw = json.dumps(diagnostics)
    assert "EVIDENCE_CATALOG" not in raw
    assert "Corrige el RuleDraft rechazado" not in raw


def test_stale_failure_diagnostics_are_cleared_when_next_run_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Un diagnostico de una corrida ANTERIOR fallida no debe sobrevivir
    indefinidamente: en cuanto se reintenta (con exito o no), el
    artefacto se limpia primero y solo se reescribe si ESA corrida
    tambien falla."""
    bad_draft = _valid_draft(evidence_id="ev-nonexistent")
    still_bad_payload = _still_bad_repair_payload()
    _install_fake_client(monkeypatch, [still_bad_payload, still_bad_payload])
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": bad_draft}
    )
    with pytest.raises(GuardrailError):
        run_guardrails_applied_stage(**kwargs)
    assert _failure_diagnostics_path(kwargs).is_file()

    # Segunda corrida: el draft ahora es valido (sin violaciones) -- el
    # diagnostico obsoleto de la corrida anterior debe desaparecer.
    _write_rule_draft_directory(
        kwargs["rule_draft_dir"], kwargs["context_dir"], {"cand-1": _valid_draft()}
    )
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    run_guardrails_applied_stage(**kwargs)

    assert not _failure_diagnostics_path(kwargs).is_file()
    assert kwargs["guardrail_dir"].exists()


def test_exhausted_repair_never_promotes_partial_guardrail_dir_even_with_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Doble verificacion de atomicidad: ni el directorio contractual ni
    # ningun temporal huerfano sobreviven, incluso con el diagnostico
    # nuevo escribiendose al lado.
    bad_draft = _valid_draft(evidence_id="ev-nonexistent")
    still_bad_payload = _still_bad_repair_payload()
    _install_fake_client(monkeypatch, [still_bad_payload, still_bad_payload])
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": bad_draft}
    )

    with pytest.raises(GuardrailError):
        run_guardrails_applied_stage(**kwargs)

    assert not kwargs["guardrail_dir"].exists()
    assert list(tmp_path.glob("09-guardrails.tmp-*")) == []
    assert _failure_diagnostics_path(kwargs).is_file()


# --- fail-fast: no procesa candidatos restantes ---


def test_fail_fast_stops_before_remaining_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad_draft_1 = _valid_draft(evidence_id="ev-nonexistent")
    valid_draft_2 = _valid_draft()
    still_bad_payload = _still_bad_repair_payload()
    # cand-1 agota 2 reparaciones (siempre invalido); cand-2 nunca deberia
    # ser procesado (fail-fast). Si el fake client se llamara una tercera
    # vez, la cola vacia dispara un AssertionError.
    _install_fake_client(monkeypatch, [still_bad_payload, still_bad_payload])
    kwargs = _base_kwargs(
        tmp_path,
        packages=[_package("cand-1"), _package("cand-2", decision_id="dec-2")],
        drafts={"cand-1": bad_draft_1, "cand-2": valid_draft_2},
    )

    with pytest.raises(GuardrailError):
        run_guardrails_applied_stage(**kwargs)

    assert not kwargs["guardrail_dir"].exists()
    assert list(tmp_path.glob("09-guardrails.tmp-*")) == []


# --- salida canonica anterior se conserva ---


def test_previous_valid_output_preserved_when_new_run_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": _valid_draft()}
    )
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    run_guardrails_applied_stage(**kwargs)
    first_bytes = (kwargs["guardrail_dir"] / "guardrail-manifest.json").read_bytes()

    # Segunda corrida: cambia el draft de cand-1 (fuerza regeneracion) a
    # uno con violacion, y las reparaciones fallan siempre.
    kwargs2 = dict(kwargs)
    bad_draft = _valid_draft(evidence_id="ev-nonexistent")
    _write_rule_draft_directory(
        kwargs["rule_draft_dir"], kwargs["context_dir"], {"cand-1": bad_draft}
    )
    still_bad_payload = _still_bad_repair_payload()
    _install_fake_client(monkeypatch, [still_bad_payload, still_bad_payload])

    with pytest.raises(GuardrailError):
        run_guardrails_applied_stage(**kwargs2)

    manifest_path = kwargs["guardrail_dir"] / "guardrail-manifest.json"
    assert manifest_path.read_bytes() == first_bytes


def test_infrastructure_error_is_fatal_no_repair_consumed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad_draft = _valid_draft(evidence_id="ev-nonexistent")
    _install_fake_client(monkeypatch, [LlmTimeoutError("boom")])
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": bad_draft}
    )

    with pytest.raises(GuardrailError):
        run_guardrails_applied_stage(**kwargs)

    assert not kwargs["guardrail_dir"].exists()


# --- fast-path de idempotencia ---


def test_second_run_with_no_changes_skips_llm_entirely(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": _valid_draft()}
    )
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    first_warnings = run_guardrails_applied_stage(**kwargs)
    assert first_warnings == ["1 guardrail(s)"]

    second_warnings = run_guardrails_applied_stage(**kwargs)
    assert second_warnings == ["1 guardrail(s) (sin cambios)"]


def test_fast_path_does_not_require_previous_runstate_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": _valid_draft()}
    )
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    run_guardrails_applied_stage(**kwargs)

    warnings = run_guardrails_applied_stage(**kwargs)
    assert warnings == ["1 guardrail(s) (sin cambios)"]


def test_fast_path_rejects_extra_unreferenced_file_and_regenerates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": _valid_draft()}
    )
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    run_guardrails_applied_stage(**kwargs)

    # Archivo .json extra en 09-guardrails/, no referenciado por
    # guardrail-manifest.json: el fast-path debe rechazar la reutilizacion
    # (defensa en profundidad, ninguna llamada LLM requerida porque el
    # draft original no tiene violaciones).
    (kwargs["guardrail_dir"] / "extra-not-in-manifest.json").write_text("{}", encoding="utf-8")

    warnings = run_guardrails_applied_stage(**kwargs)

    assert warnings == ["1 guardrail(s)"]
    assert not (kwargs["guardrail_dir"] / "extra-not-in-manifest.json").exists()


# --- archivos no referenciados en 08-rule-drafts/ (precondicion) ---


def test_extra_unreferenced_file_in_rule_draft_dir_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": _valid_draft()}
    )
    (kwargs["rule_draft_dir"] / "extra-not-in-manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(GuardrailError):
        run_guardrails_applied_stage(**kwargs)


# --- correspondencia 1:1 entre 08 y 09, consistencia repair_* ---


def test_all_candidates_from_08_appear_exactly_once_in_09(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    kwargs = _base_kwargs(
        tmp_path,
        packages=[_package("cand-1"), _package("cand-2", decision_id="dec-2")],
        drafts={"cand-1": _valid_draft(), "cand-2": _valid_draft()},
    )

    run_guardrails_applied_stage(**kwargs)

    manifest = GuardrailDirectoryManifest.model_validate_json(
        (kwargs["guardrail_dir"] / "guardrail-manifest.json").read_text(encoding="utf-8")
    )
    candidate_ids = [record.candidate_id for record in manifest.records]
    assert sorted(candidate_ids) == ["cand-1", "cand-2"]
    assert len(candidate_ids) == len(set(candidate_ids))
    for record in manifest.records:
        assert (kwargs["guardrail_dir"] / record.relative_filename).is_file()


def test_multiple_candidates_have_independent_evidence_catalogs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cand-1 y cand-2 tienen catalogos con distinta cantidad de entradas
    (cand-2 cita evidencia adicional propia): cada candidato repara
    contra SU propio catalogo, construido una vez y reutilizado en sus
    propios intentos -- sin que la evidencia de un candidato perturbe la
    numeracion de alias del otro."""
    package_1 = _package("cand-1")
    package_2 = _package_with_extra_evidence("cand-2")
    bad_draft_1 = _valid_draft(evidence_id="ev-nonexistent")
    bad_draft_2 = RuleDraft(
        schema_version="2.0",
        title="Titulo",
        context="Contexto",
        statement="Enunciado",
        condition="WS-COD = 'R001'",
        parameters=[],
        effect="Efecto",
        parameter_source=None,
        traceability=["Evidencia trazada mediante el catalogo de alias del candidato"],
        limitations=["Requiere revision funcional"],
        claims=[
            Claim(
                claim_id="c1",
                field=ClaimField.CONDITION,
                evidence_paths=["$.code_slice[1]"],
                evidence_ids=["ev-nonexistent"],
            )
        ],
        evidence_validation_status=EvidenceValidationStatus.PENDING,
    )

    catalog_2 = build_evidence_catalog(package_2)
    extra_alias = catalog_2.find_alias("ev-2", "$.code_slice[1]")
    assert extra_alias is not None

    calls = _install_fake_client(
        monkeypatch,
        [
            _valid_repair_payload(),
            _valid_repair_payload(
                claims=[
                    {"claim_id": "c1", "field": "condition", "evidence_refs": [extra_alias]}
                ]
            ),
        ],
    )
    kwargs = _base_kwargs(
        tmp_path,
        packages=[package_1, package_2],
        drafts={"cand-1": bad_draft_1, "cand-2": bad_draft_2},
    )

    warnings = run_guardrails_applied_stage(**kwargs)

    assert len(calls) == 2
    assert warnings == ["2 guardrail(s)"]
    manifest = GuardrailDirectoryManifest.model_validate_json(
        (kwargs["guardrail_dir"] / "guardrail-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.guardrail_count == 2


def test_final_guardrail_artifact_never_contains_evidence_refs_or_aliases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """El `GuardrailCandidateArtifact` persistido en
    `artifacts/09-guardrails/` es siempre el contrato real: nunca
    conserva `evidence_refs` ni un alias `E00N` (el catalogo es un
    protocolo de conversacion en memoria, nunca se persiste)."""
    bad_draft = _valid_draft(evidence_id="ev-nonexistent")
    alias = _decision_alias()
    _install_fake_client(monkeypatch, [_valid_repair_payload()])
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": bad_draft}
    )

    run_guardrails_applied_stage(**kwargs)

    manifest = GuardrailDirectoryManifest.model_validate_json(
        (kwargs["guardrail_dir"] / "guardrail-manifest.json").read_text(encoding="utf-8")
    )
    record = manifest.records[0]
    artifact_text = (kwargs["guardrail_dir"] / record.relative_filename).read_text(
        encoding="utf-8"
    )
    assert "evidence_refs" not in artifact_text
    assert alias not in artifact_text
    assert re.search(r"\bE\d{3}\b", artifact_text) is None
    assert "ev-1" in artifact_text


def test_manifest_repair_response_hashes_match_persisted_artifact_history(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad_draft = _valid_draft(evidence_id="ev-nonexistent")
    _install_fake_client(monkeypatch, [_valid_repair_payload()])
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": bad_draft}
    )

    run_guardrails_applied_stage(**kwargs)

    manifest = GuardrailDirectoryManifest.model_validate_json(
        (kwargs["guardrail_dir"] / "guardrail-manifest.json").read_text(encoding="utf-8")
    )
    record = manifest.records[0]
    artifact = GuardrailCandidateArtifact.model_validate_json(
        (kwargs["guardrail_dir"] / record.relative_filename).read_text(encoding="utf-8")
    )

    assert record.repair_attempts_used == len(artifact.repair_history)
    assert record.repair_response_hashes == [
        attempt.response_hash
        for attempt in artifact.repair_history
        if attempt.response_hash is not None
    ]


def test_artifact_source_package_hash_matches_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # RuleDraft no tiene campo source_package_hash propio (confirmado en
    # altamira_extractor.contracts.rule_draft): la coherencia relevante es
    # entre el wrapper GuardrailCandidateArtifact.source_package_hash y
    # GuardrailDirectoryManifest.source_package_hash, garantizada por
    # construccion (mismo parametro enhebrado en run_guardrails_applied_
    # stage -> _resolve_all_candidates -> _resolve_one_candidate).
    assert "source_package_hash" not in RuleDraft.model_fields
    assert "candidate_id" not in RuleDraft.model_fields

    monkeypatch.setattr(stage_module, "OpenAICompatibleChatClient", _PoisonClient)
    kwargs = _base_kwargs(
        tmp_path, packages=[_package("cand-1")], drafts={"cand-1": _valid_draft()}
    )

    run_guardrails_applied_stage(**kwargs)

    manifest = GuardrailDirectoryManifest.model_validate_json(
        (kwargs["guardrail_dir"] / "guardrail-manifest.json").read_text(encoding="utf-8")
    )
    record = manifest.records[0]
    artifact = GuardrailCandidateArtifact.model_validate_json(
        (kwargs["guardrail_dir"] / record.relative_filename).read_text(encoding="utf-8")
    )
    assert artifact.source_package_hash == manifest.source_package_hash


# --- placeholder real (checkpoint correctivo: defecto preexistente) ---


def test_real_repair_prompts_load_without_placeholder_error(tmp_path: Path) -> None:
    """Carga los archivos REALES del repositorio (nunca un fixture
    ficticio creado dentro del test): confirma que
    `_load_repair_prompts` -- con el placeholder corregido a
    `{{GUARDRAIL_VIOLATIONS_JSON}}` singular, coincidiendo con el
    contenido real de prompts/rule_repair_user.md -- no levanta
    `PromptTemplateError`. Antes de esta correccion, la constante del
    codigo era plural (`GUARDRAILS_VIOLATIONS_JSON`) y nunca coincidia
    con el archivo real, asi que GUARDRAILS_APPLIED fallaba en cuanto
    necesitaba reparar contra los prompts reales."""
    assert REAL_RULE_REPAIR_SYSTEM_PATH.is_file()
    assert REAL_RULE_REPAIR_USER_PATH.is_file()
    settings = _settings(
        tmp_path,
        rule_repair_system_prompt_path=REAL_RULE_REPAIR_SYSTEM_PATH,
        rule_repair_user_prompt_path=REAL_RULE_REPAIR_USER_PATH,
    )

    system_text, system_hash, user_text, user_hash = stage_module._load_repair_prompts(settings)

    assert system_text
    assert user_text
    assert len(system_hash) == 64
    assert len(user_hash) == 64


def test_guardrails_applied_does_not_fail_with_placeholder_error_using_real_prompts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ejercita `run_guardrails_applied_stage` end-to-end (fake client,
    sin red) apuntando a los prompts de reparacion REALES del
    repositorio, con un draft inicial que SI dispara una reparacion de
    guardrail: confirma que la etapa nunca levanta `PromptTemplateError`
    por el placeholder (el defecto preexistente que este checkpoint
    corrige) y llega a EVIDENCE_VALIDATED normalmente."""
    kwargs = _base_kwargs(
        tmp_path,
        packages=[_package("cand-1")],
        drafts={"cand-1": _valid_draft(evidence_id="ev-does-not-exist")},
    )
    kwargs["settings"] = _settings(
        tmp_path,
        rule_repair_system_prompt_path=REAL_RULE_REPAIR_SYSTEM_PATH,
        rule_repair_user_prompt_path=REAL_RULE_REPAIR_USER_PATH,
    )
    _install_fake_client(monkeypatch, [_valid_repair_payload()])

    run_guardrails_applied_stage(**kwargs)

    manifest = GuardrailDirectoryManifest.model_validate_json(
        (kwargs["guardrail_dir"] / "guardrail-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest.guardrail_count == 1
