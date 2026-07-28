"""Modernizacion visual de la UI: branding corporativo, eliminacion de
mensajes de prototipo, ausencia de recursos externos, etiquetas de
estado en espanol, garantias de accesibilidad/responsive minimas y
funcionamiento sin JavaScript de las acciones criticas.

Ninguno de estos tests reemplaza a los de `test_escaping.py`/
`test_security_payloads.py`/`test_csrf.py` (comportamiento de seguridad,
sin cambios): estos cubren especificamente el alcance nuevo de la
modernizacion visual."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from altamira_extractor.config import Settings
from altamira_extractor.contracts.enums import PipelineStage, StageStatus
from altamira_extractor.ui import STATIC_DIR, TEMPLATES_DIR
from altamira_extractor.ui.presentation import status_label

from ..api.conftest import (
    CANDIDATE_ID,
    RUN_ID,
    build_run_completed,
    build_run_state,
    build_run_up_to_candidates_detected,
    build_run_up_to_guardrails_applied,
    stage_execution,
    write_run_state,
)

_FORBIDDEN_PROTOTYPE_STRINGS = (
    "Borradores generados automáticamente, requieren revisión funcional",
    "Sin autenticación en V1",
    "Sin autenticacion en V1",
    "restrinja el acceso de red externamente",
    "Este documento es un borrador generado automáticamente y requiere "
    "revisión funcional.",
)

_ALL_TEMPLATE_TEXTS = [
    p.read_text(encoding="utf-8") for p in sorted(TEMPLATES_DIR.glob("*.html"))
]


# --- branding y firma ---


def test_signature_appears_in_global_layout(client: TestClient) -> None:
    response = client.get("/ui/upload")
    assert response.status_code == 200
    assert "Desarrollada por IA Factory Argentina" in response.text


def test_signature_appears_on_runs_dashboard(client: TestClient) -> None:
    response = client.get("/ui/runs")
    assert response.status_code == 200
    assert "Desarrollada por IA Factory Argentina" in response.text


def test_pwc_brand_text_present_without_external_logo(client: TestClient) -> None:
    response = client.get("/ui/upload")
    assert "PwC" in response.text
    assert "Altamira Rule Extractor" in response.text
    # Nunca un logo remoto: ninguna referencia a una URL externa de imagen.
    assert "http://" not in response.text.split("<body")[1].split("</body>")[0].replace(
        "http://testserver", ""
    )


def test_footer_never_shows_technical_warning() -> None:
    base_html = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
    for forbidden in _FORBIDDEN_PROTOTYPE_STRINGS:
        assert forbidden not in base_html


# --- mensajes de prototipo eliminados en TODOS los templates ---


def test_no_prototype_or_v1_messages_in_any_template() -> None:
    for text in _ALL_TEMPLATE_TEXTS:
        for forbidden in _FORBIDDEN_PROTOTYPE_STRINGS:
            assert forbidden not in text


def test_no_bare_v1_reference_in_any_rendered_page(client: TestClient, settings: Settings) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    for path in (
        "/ui/upload",
        "/ui/runs",
        f"/ui/runs/{RUN_ID}",
        f"/ui/runs/{RUN_ID}/download",
    ):
        response = client.get(path)
        assert " V1" not in response.text
        assert ">V1<" not in response.text


# --- sin recursos externos / CDN ---


def test_app_css_has_no_external_urls() -> None:
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert "http://" not in css
    assert "https://" not in css
    assert "@import" not in css


def test_app_js_has_no_external_urls_or_eval() -> None:
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "http://" not in js
    assert "https://" not in js
    assert "eval(" not in js
    assert ".innerHTML" not in js


def test_app_js_is_served_locally(client: TestClient) -> None:
    response = client.get("/static/app.js")
    assert response.status_code == 200
    content_type = response.headers["content-type"]
    assert "javascript" in content_type or "text" in content_type


def test_no_google_fonts_or_cdn_domains_anywhere() -> None:
    haystacks = [
        (STATIC_DIR / "app.css").read_text(encoding="utf-8"),
        (STATIC_DIR / "app.js").read_text(encoding="utf-8"),
        *_ALL_TEMPLATE_TEXTS,
    ]
    for text in haystacks:
        assert "fonts.googleapis" not in text
        assert "cdn." not in text
        assert "unpkg" not in text
        assert "jsdelivr" not in text


# --- etiquetas de estado ---


def test_status_label_translates_known_contractual_values() -> None:
    assert status_label("SUCCEEDED") == "Completado"
    assert status_label("FAILED") == "Fallido"
    assert status_label("RUNNING") == "En ejecucion"
    assert status_label("PENDING") == "Pendiente"
    assert status_label("SKIPPED") == "Omitido"
    assert status_label("EVIDENCE_VALIDATED") == "Evidencia validada"
    assert status_label("NEEDS_FUNCTIONAL_REVIEW") == "Pendiente de validacion funcional"


def test_status_label_falls_back_to_raw_value_for_unknown_input() -> None:
    assert status_label("SOME_FUTURE_STATUS") == "SOME_FUTURE_STATUS"


def test_badges_show_friendly_label_and_raw_technical_value(
    client: TestClient, settings: Settings
) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}")
    assert "Completado" in response.text
    assert "COMPLETED" in response.text


def test_needs_functional_review_shown_with_professional_label(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/candidates/{CANDIDATE_ID}/rule")
    assert "Pendiente de validacion funcional" in response.text
    assert "NEEDS_FUNCTIONAL_REVIEW" in response.text


# --- filtros client-side no alteran los datos servidos ---


def test_search_and_filter_controls_never_reduce_server_rendered_rows(
    client: TestClient, settings: Settings
) -> None:
    stages = [stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED)]
    for suffix in ("aaaaaaaa", "bbbbbbbb", "cccccccc"):
        run_id = f"2026010{suffix[0]}T000000000000-{suffix}"
        state = build_run_state(run_id, stages=stages, current_stage=PipelineStage.RECEIVED)
        write_run_state(settings.runs_dir / run_id, state)

    response = client.get("/ui/runs")
    assert response.status_code == 200
    # Los tres runs siguen presentes en el HTML servido: el filtrado es
    # exclusivamente client-side (JS oculta filas, nunca el servidor deja
    # de enviarlas) -- ningun dato desaparece de la respuesta real.
    assert "aaaaaaaa" in response.text
    assert "bbbbbbbb" in response.text
    assert "cccccccc" in response.text
    assert 'data-table-search="runs-table"' in response.text


def test_candidates_search_input_present_without_hiding_rows(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_candidates_detected(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/candidates")
    assert CANDIDATE_ID in response.text
    tbody_html = response.text.split("<tbody")[1].split("</tbody>")[0]
    assert " hidden>" not in tbody_html
    assert " hidden " not in tbody_html


# --- funciona sin JavaScript para acciones criticas ---


def test_upload_submit_button_never_disabled_by_default_server_side(
    client: TestClient,
) -> None:
    # El boton de subida nunca depende de JavaScript para habilitarse: el
    # HTML servido no lo marca `disabled` (la validacion nativa la cubre
    # `required` en el input file). JS solo lo mejora visualmente.
    response = client.get("/ui/upload")
    assert "upload-submit" in response.text
    submit_html = response.text.split('class="btn btn-primary upload-submit"')[1][:80]
    assert "disabled" not in submit_html
    assert "required" in response.text


def test_resume_action_is_a_plain_form_post_not_a_js_only_action(
    client: TestClient, settings: Settings
) -> None:
    build_run_up_to_guardrails_applied(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}")
    expected_form = f'<form method="post" action="http://testserver/ui/runs/{RUN_ID}/resume">'
    assert expected_form in response.text


def test_download_action_is_a_plain_link_not_a_js_only_action(
    client: TestClient, settings: Settings
) -> None:
    build_run_completed(settings.runs_dir / RUN_ID, RUN_ID)
    response = client.get(f"/ui/runs/{RUN_ID}/download")
    assert f'href="http://testserver/api/runs/{RUN_ID}/download"' in response.text


# --- responsive ---


def test_css_declares_responsive_breakpoints() -> None:
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert "@media" in css
    assert "max-width" in css


def test_css_respects_prefers_reduced_motion() -> None:
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css


def test_viewport_meta_present(client: TestClient) -> None:
    response = client.get("/ui/upload")
    assert 'name="viewport"' in response.text


# --- renderiza con datos vacios, completos y fallidos ---


def test_run_status_renders_for_a_failed_run(client: TestClient, settings: Settings) -> None:
    run_dir = settings.runs_dir / RUN_ID
    stages = [
        stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED),
        stage_execution(
            PipelineStage.VALIDATED, StageStatus.FAILED, error="paquete invalido"
        ),
    ]
    state = build_run_state(RUN_ID, stages=stages, current_stage=PipelineStage.FAILED)
    write_run_state(run_dir, state)

    response = client.get(f"/ui/runs/{RUN_ID}")
    assert response.status_code == 200
    assert "paquete invalido" in response.text
    assert ">Reanudar<" in response.text
    assert "Fallido" in response.text


def test_run_status_renders_with_no_summary_counts_yet(
    client: TestClient, settings: Settings
) -> None:
    run_dir = settings.runs_dir / RUN_ID
    stages = [stage_execution(PipelineStage.RECEIVED, StageStatus.SUCCEEDED)]
    state = build_run_state(RUN_ID, stages=stages, current_stage=PipelineStage.RECEIVED)
    write_run_state(run_dir, state)

    response = client.get(f"/ui/runs/{RUN_ID}")
    assert response.status_code == 200
    assert "--" in response.text


def test_runs_dashboard_renders_with_zero_runs(client: TestClient) -> None:
    response = client.get("/ui/runs")
    assert response.status_code == 200
    assert "No hay ejecuciones registradas." in response.text
    # Las tarjetas de resumen siguen presentes con 0, nunca rompen con
    # una lista vacia.
    assert ">0<" in response.text


# --- version en el footer, solo si es confiable ---


def test_footer_version_comes_from_declared_app_version(
    client: TestClient,
) -> None:
    response = client.get("/ui/upload")
    assert "Version 1.0" in response.text


def test_no_hardcoded_release_tag_string_in_templates() -> None:
    # La version mostrada proviene de `app.version` (FastAPI), nunca de
    # un string de release hardcodeado en un template.
    for text in _ALL_TEMPLATE_TEXTS:
        assert "v1.0.5" not in text.lower()


# --- estructura de archivos de la modernizacion ---


def test_no_new_framework_or_build_tool_files_introduced() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    forbidden_files = (
        "package.json",
        "vite.config.js",
        "vite.config.ts",
        "tailwind.config.js",
        "webpack.config.js",
    )
    for filename in forbidden_files:
        assert not (repo_root / filename).exists()
