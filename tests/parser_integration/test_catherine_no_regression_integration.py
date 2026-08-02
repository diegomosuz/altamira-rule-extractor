"""No-regresion de Fase 7/7b sobre los paquetes reales Catherine (original
y corregido, `examples/PAQUETE_SINTETICO_CATHERINE*.zip`, nunca
modificados): confirma que `semantic-interprocedural-propagation`
(servicio, no CLI -- mismas funciones que invoca la CLI) agrega
EXCLUSIVAMENTE `diagnostics/interprocedural-propagation.json` al
run_dir, sin alterar NINGUN otro archivo preexistente (run.json,
artifacts/01-10, ni ningun otro diagnostics/*.json), byte a byte
(SHA-256).

Usa el fake/stub oficial del repositorio
(`e2e_support.install_dynamic_rule_draft_fake_client`), que calcula los
`evidence_refs` dinamicamente contra el catalogo de evidencia REAL de
cada corrida -- nunca el `_FakeClient` simplificado de un driver ad hoc.
Parchea `OpenAICompatibleChatClient` en los DOS puntos de uso reales
(`rule_drafts_generated_stage`/`guardrails_applied_stage`, mismo patron
que `tests/docker/test_docker_e2e.py`), nunca un proveedor real.

No corre en la suite por defecto (marcado `integration`, requiere JAR +
Neo4j real)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import altamira_extractor.pipeline.guardrails_applied_stage as guardrails_stage_module
import altamira_extractor.pipeline.rule_drafts_generated_stage as rule_drafts_stage_module
from altamira_extractor.pipeline.interprocedural_propagation_service import (
    compute_interprocedural_propagation_artifact,
    write_interprocedural_propagation_artifact,
)
from altamira_extractor.pipeline.runner import run_ingestion

from ..e2e_support import build_settings, install_dynamic_rule_draft_fake_client, require_jar

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
CATHERINE_ORIGINAL_ZIP = REPO_ROOT / "examples" / "PAQUETE_SINTETICO_CATHERINE.zip"
CATHERINE_CORRECTED_ZIP = (
    REPO_ROOT / "examples" / "PAQUETE_SINTETICO_CATHERINE_CORREGIDO_APP_ACTUAL.zip"
)


def _snapshot(directory: Path) -> dict[str, str]:
    return {
        p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def _run_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, zip_path: Path
) -> tuple[str, int]:
    """Retorna (current_stage, call_site_count del diagnostico Fase 7).
    Nunca exige COMPLETED: solo PARSED (unico requisito real del
    servicio Fase 7) mas la comparacion byte a byte antes/despues.

    Si el fake oficial no logra completar TODOS los candidatos reales de
    un paquete (p. ej. un candidato sin evidencia `return_code_effect`
    -- limitacion PREEXISTENTE del fake/fixture, ajena a Fase 7/7b, ver
    `test_official_fake_client_limitation_is_preexisting_not_a_fase7_regression`
    mas abajo), `run_ingestion` puede propagar una excepcion sin devolver
    `RunState` (el fallo ocurre dentro de una etapa intermedia, nunca
    dentro de Fase 7, que ni siquiera se invoco todavia). En ese caso se
    localiza el run_dir por diferencia de directorios (unico nuevo bajo
    `settings.runs_dir`) y se continua la verificacion leyendo
    `run.json` directamente del disco -- la comparacion byte a byte de
    Fase 7 sigue siendo valida sobre lo que el pipeline SI alcanzo a
    persistir antes del fallo."""
    require_jar()
    original_bytes = zip_path.read_bytes()

    install_dynamic_rule_draft_fake_client(monkeypatch, rule_drafts_stage_module)
    # Guardrail: mismo fake dinamico -- si el RuleDraft es rechazado y el
    # guardrail intenta reparar, reutiliza el mismo mecanismo de alias
    # reales (nunca un proveedor real); mismo patron que
    # tests/docker/test_docker_e2e.py.
    install_dynamic_rule_draft_fake_client(monkeypatch, guardrails_stage_module)

    settings = build_settings(tmp_path)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    runs_before = {p.name for p in settings.runs_dir.iterdir() if p.is_dir()}

    current_stage: str
    try:
        state = run_ingestion(zip_path, settings)
        run_dir = settings.runs_dir / state.run_id
        current_stage = state.current_stage.value
    except Exception as exc:  # noqa: BLE001 -- ver docstring: limitacion preexistente del fake
        runs_after = {p.name for p in settings.runs_dir.iterdir() if p.is_dir()}
        new_run_ids = runs_after - runs_before
        assert len(new_run_ids) == 1, (
            f"no se pudo localizar un unico run_dir nuevo tras la excepcion "
            f"({exc!r}): {new_run_ids}"
        )
        run_dir = settings.runs_dir / next(iter(new_run_ids))
        run_json = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        current_stage = run_json["current_stage"]
        succeeded_from_disk = {
            s["stage"] for s in run_json["stages"] if s["status"] == "SUCCEEDED"
        }
        assert "PARSED" in succeeded_from_disk, (
            f"PARSED no tuvo exito segun run.json en disco tras la excepcion: {succeeded_from_disk}"
        )

    run_id = run_dir.name

    before = _snapshot(run_dir)
    artifact = compute_interprocedural_propagation_artifact(run_dir, run_id)
    write_interprocedural_propagation_artifact(run_dir, artifact)
    after = _snapshot(run_dir)

    new_paths = set(after) - set(before)
    removed_paths = set(before) - set(after)
    changed_paths = {p for p in (set(before) & set(after)) if before[p] != after[p]}

    assert new_paths == {"diagnostics/interprocedural-propagation.json"}, new_paths
    assert removed_paths == set(), removed_paths
    assert changed_paths == set(), changed_paths

    assert zip_path.read_bytes() == original_bytes, f"{zip_path} fue modificado in situ"

    return current_stage, artifact.summary.call_site_count


@pytest.mark.integration
def test_catherine_original_no_regression(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    final_stage, call_sites = _run_no_regression(monkeypatch, tmp_path, CATHERINE_ORIGINAL_ZIP)
    print(f"Catherine original: current_stage={final_stage} call_sites={call_sites}")


@pytest.mark.integration
def test_catherine_corrected_no_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    final_stage, call_sites = _run_no_regression(monkeypatch, tmp_path, CATHERINE_CORRECTED_ZIP)
    print(f"Catherine corregido: current_stage={final_stage} call_sites={call_sites}")


@pytest.mark.integration
def test_official_fake_client_limitation_is_preexisting_not_a_fase7_regression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Prueba focalizada (auditoria de cierre, Parte 4): confirma que al
    menos un candidato real de Catherine corregido produce un
    ContextPackage SIN evidencia de kind='return_code_effect' -- la
    causa exacta por la que el fake oficial
    (`install_dynamic_rule_draft_fake_client`) no logra completar
    RULE_DRAFTS_GENERATED para ese paquete. Esto ocurre enteramente
    DENTRO de CONTEXTS_BUILT/RULE_DRAFTS_GENERATED (etapas V1
    preexistentes, nunca tocadas por Fase 7/7b) y ANTES de que
    `semantic-interprocedural-propagation` se invoque siquiera -- prueba
    directa de que la limitacion es del propio fixture/pipeline V1, no
    una regresion introducida por esta fase."""
    require_jar()

    context_packages_without_return_code_effect: list[str] = []
    context_packages_seen: list[str] = []

    class _InspectingFakeClient:
        def __init__(self, profile: object, **kwargs: object) -> None:
            self.profile = profile

        async def __aenter__(self) -> _InspectingFakeClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def complete(self, messages: list[object]) -> dict[str, object]:
            from tests.e2e_support import extract_context_package_json, valid_payload

            user_message = next(m for m in messages if m.role == "user")  # type: ignore[attr-defined]
            context_package_dict = extract_context_package_json(user_message.content)  # type: ignore[attr-defined]
            candidate_id = context_package_dict["candidate"]["candidate_id"]
            context_packages_seen.append(candidate_id)
            kinds = {entry["kind"] for entry in context_package_dict["evidence"]}
            if "return_code_effect" not in kinds:
                context_packages_without_return_code_effect.append(candidate_id)
            # Payload deliberadamente MINIMO (no intenta ensamblar
            # evidence_refs reales): esta prueba nunca intenta llegar a
            # RULE_DRAFTS_GENERATED con exito, solo inspeccionar
            # ContextPackage reales antes de que el guardrail/repair
            # entre en juego.
            return valid_payload()

    monkeypatch.setattr(
        rule_drafts_stage_module, "OpenAICompatibleChatClient", _InspectingFakeClient
    )
    monkeypatch.setattr(
        guardrails_stage_module, "OpenAICompatibleChatClient", _InspectingFakeClient
    )

    settings = build_settings(tmp_path)
    try:
        run_ingestion(CATHERINE_CORRECTED_ZIP, settings)
    except Exception:  # noqa: BLE001 -- el payload minimo puede fallar guardrails; irrelevante aqui
        pass

    assert context_packages_seen, "ningun ContextPackage llego a RULE_DRAFTS_GENERATED"
    assert context_packages_without_return_code_effect, (
        "se esperaba encontrar al menos un candidato real de Catherine corregido sin "
        "evidencia kind='return_code_effect' -- si esto ya no ocurre, la limitacion "
        "documentada en este test ya no aplica y el fake oficial deberia completar el "
        "pipeline sin el manejo especial de _run_no_regression"
    )
    print(
        f"candidatos inspeccionados: {len(context_packages_seen)}; "
        f"sin return_code_effect: {context_packages_without_return_code_effect}"
    )
