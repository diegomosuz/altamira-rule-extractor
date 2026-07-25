"""Payloads maliciosos (Prompt 13d, seccion 28): title, statement,
source_text, predicate_text, DomainTerm, error, filename original --
ninguno debe aparecer sin escapar en ninguna pantalla."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from altamira_extractor.config import Settings
from altamira_extractor.contracts.context_package import (
    ApplicableParameterRow,
    DomainGlossaryEntry,
    ParameterTableContext,
)
from altamira_extractor.contracts.enums import ApplicabilityStatus, Severity
from altamira_extractor.contracts.guardrail import GuardrailViolation

from ..api.conftest import (
    CANDIDATE_ID,
    RUN_ID,
    build_candidate,
    build_context_package,
    build_final_draft,
    build_run_up_to_candidates_detected,
    build_run_up_to_contexts_built,
    build_run_up_to_guardrails_applied,
    write_candidates_artifact,
    write_context_directory,
)
from ..pipeline.conftest import build_valid_package_zip

SCRIPT_PAYLOAD = "<script>alert(1)</script>"
IMG_PAYLOAD = "<img src=x onerror=alert(1)>"
JS_LINK_PAYLOAD = "javascript:alert(document.cookie)"


def _assert_never_raw(html: str, *payloads: str) -> None:
    for payload in payloads:
        assert payload not in html, f"payload sin escapar: {payload!r}"


def _assert_never_a_link(html: str, payload: str) -> None:
    # javascript: solo es peligroso como VALOR de href/src; como texto
    # plano dentro de un parrafo es una cadena inerte que el usuario ve
    # tal cual (eso es precisamente "mostrado como texto seguro").
    assert f'href="{payload}"' not in html
    assert f"href='{payload}'" not in html
    assert f'src="{payload}"' not in html


def test_candidate_condition_and_outcome_are_escaped(
    client: TestClient, settings: Settings
) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_candidates_detected(run_dir, RUN_ID)

    malicious = build_candidate().model_copy(
        update={"condition": SCRIPT_PAYLOAD, "outcome_code": IMG_PAYLOAD}
    )
    write_candidates_artifact(run_dir, [malicious])

    response = client.get(f"/ui/runs/{RUN_ID}/candidates")
    assert response.status_code == 200
    _assert_never_raw(response.text, SCRIPT_PAYLOAD, IMG_PAYLOAD)
    assert "&lt;script&gt;" in response.text


def test_context_source_text_predicates_and_domain_term_are_escaped(
    client: TestClient, settings: Settings
) -> None:
    run_dir = settings.runs_dir / RUN_ID
    build_run_up_to_contexts_built(run_dir, RUN_ID)

    base_package = build_context_package()
    tampered_code_slice = [
        entry.model_copy(update={"source_text": SCRIPT_PAYLOAD})
        for entry in base_package.code_slice
    ]
    tampered_data_context = base_package.data_context.model_copy(
        update={
            "parameter_tables": [
                ParameterTableContext(
                    name="PARAM_DEMO",
                    applicability_status=ApplicabilityStatus.PARTIAL,
                    predicates=[IMG_PAYLOAD],
                    resolved_predicates=[],
                    unresolved_predicates=[IMG_PAYLOAD],
                    applicable_rows=[
                        ApplicableParameterRow(
                            parameter_entry_id="pe-1", values={"COL": SCRIPT_PAYLOAD}
                        )
                    ],
                    context_rows=[],
                    evidence_ids=["ev-1"],
                )
            ]
        }
    )
    tampered_glossary = [
        DomainGlossaryEntry(
            data_item_id="di-1",
            technical_name="WS-COD",
            semantic_tag="return_code",
            domain_term_id="term-1",
            functional_name=SCRIPT_PAYLOAD,
            definition=JS_LINK_PAYLOAD,
            entity_type="field",
            source_kind="catalog",
            authoritative_source="glossary.yml",
            confidence=1.0,
            evidence_ids=["ev-1"],
        )
    ]
    tampered = base_package.model_copy(
        update={
            "code_slice": tampered_code_slice,
            "data_context": tampered_data_context,
            "domain_glossary": tampered_glossary,
        }
    )
    write_context_directory(run_dir, [tampered])

    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/context")
    assert response.status_code == 200
    _assert_never_raw(response.text, SCRIPT_PAYLOAD, IMG_PAYLOAD)
    _assert_never_a_link(response.text, JS_LINK_PAYLOAD)
    assert "&lt;script&gt;" in response.text


def test_rule_title_and_statement_are_escaped(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    draft = build_final_draft(title=SCRIPT_PAYLOAD).model_copy(
        update={"statement": IMG_PAYLOAD, "context": JS_LINK_PAYLOAD, "effect": SCRIPT_PAYLOAD}
    )
    build_run_up_to_guardrails_applied(run_dir, RUN_ID, final_draft=draft)

    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/rule")
    assert response.status_code == 200
    _assert_never_raw(response.text, SCRIPT_PAYLOAD, IMG_PAYLOAD)
    _assert_never_a_link(response.text, JS_LINK_PAYLOAD)
    assert "&lt;script&gt;" in response.text


def test_guardrail_violation_message_is_escaped(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    violation = GuardrailViolation(
        violation_id="v1",
        rule="R1",
        field="condition",
        message=SCRIPT_PAYLOAD,
        severity=Severity.WARNING,
    )
    build_run_up_to_guardrails_applied(run_dir, RUN_ID, violations=[violation])

    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/guardrail")
    assert response.status_code == 200
    _assert_never_raw(response.text, SCRIPT_PAYLOAD)
    assert "&lt;script&gt;" in response.text


def test_upload_original_filename_never_reaches_any_page(
    client: TestClient, settings: Settings
) -> None:
    zip_path = build_valid_package_zip(settings.data_dir.parent / "package.zip")
    with zip_path.open("rb") as fh:
        response = client.post(
            "/ui/runs",
            files={"file": (f"{SCRIPT_PAYLOAD}.zip", fh, "application/zip")},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    run_id = response.headers["location"].rsplit("/", 1)[-1]

    status_response = client.get(f"/ui/runs/{run_id}")
    _assert_never_raw(status_response.text, SCRIPT_PAYLOAD)
    assert "input/package.zip" in status_response.text


def test_error_message_is_escaped_on_error_page(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import altamira_extractor.ui.router as ui_router_module
    from altamira_extractor.api.errors import StageNotReachedError
    from altamira_extractor.contracts.run_state import RunState

    def _boom(run_dir: object) -> RunState:
        raise StageNotReachedError(SCRIPT_PAYLOAD)

    monkeypatch.setattr(ui_router_module, "read_run_state", _boom)
    build_run_up_to_contexts_built(settings.runs_dir / RUN_ID, RUN_ID)

    response = client.get(f"/ui/runs/{RUN_ID}")
    assert response.status_code == 409
    _assert_never_raw(response.text, SCRIPT_PAYLOAD)
    assert "&lt;script&gt;" in response.text
