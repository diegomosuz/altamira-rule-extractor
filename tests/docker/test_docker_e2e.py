"""E2E Docker sin internet (Prompt 14b, aislamiento por hash: Prompt
14b.1): corre `run_ingestion` directamente -- nunca via API/CLI/UI, que
ya tienen su propio E2E de proceso unico en `tests/api/`,
`tests/test_cli_integration.py` y `tests/ui/` -- dentro de la imagen
`test` del Dockerfile (ver `Dockerfile`, stage `test`), contra el
`neo4j` real del proyecto Compose temporal que crea
`scripts/docker_e2e.py`, en una red Docker `--internal` (sin salida a
Internet). Ejercita el pipeline completo con datos reales: ZIP real ->
parser Java 17 real -> grafo semantico -> invariantes -> candidato (Q0) ->
ContextPackage (Q1-Q7) -> LLM fake -> guardrail deterministico ->
Markdown. Mismo fixture COBOL (mismo `.cbl`, nunca modificado) que
`tests/e2e_support.py`. Los `evidence_id` de esta ejecucion, en cambio,
NO son los constantes de `e2e_support.DECISION_EVIDENCE_ID`/
`RETURN_CODE_EVIDENCE_ID`: esos solo son validos para el ZIP de
contenido fijo que usan los otros 3 E2E de proceso unico (con
`source_package_hash` estable). Aqui `write_package_zip(...,
unique_marker=...)` varia los bytes por ejecucion a proposito (ver mas
abajo), lo que hace variar `source_package_hash` y, con el,
`candidate_id` (`candidate_id_for(source_package_hash=...)`) y por lo
tanto cada `evidence_id` (`context_package_builder._evidence_id` hashea
`candidate_id` como parte de su payload). El fake client de
RULE_DRAFTS_GENERATED obtiene esos evidence_id dinamicamente leyendo el
ContextPackage real dentro de los mensajes que recibe, en vez de
hardcodearlos. Todo directorio de escritura (runs/incoming/prompts/
artifacts) cuelga de `tmp_path`: nunca `/app/data`, `/app/tests` ni la
raiz del repositorio.

Aislamiento Neo4j por `source_package_hash` (Prompt 14b.1): la suite
`integration` completa comparte una unica instancia Neo4j entre tests
(`pytest -m integration`), asi que este test NUNCA puede afirmar que la
base este globalmente vacia -- otros tests ya pudieron haber cargado
grafos validos antes o despues. En cambio, `write_package_zip(...,
unique_marker=...)` produce un ZIP con bytes unicos por ejecucion (un
comentario SQL inerte agregado al DDL de PARAM_DEMO, que
`inventory_builder.py`/`manifest_loader.py` solo inventarian y
referencian, nunca ejecutan ni parsean semanticamente), lo que le da a
cada ejecucion un `source_package_hash` que nunca fue visto antes. Las
consultas antes/despues y la limpieza final quedan parametrizadas por
ESE hash exclusivo (nunca concatenado en el Cypher, nunca un `MATCH (n)`
sin filtrar), asi que el test puede correr dos veces consecutivas contra
la misma instancia sin depender de -- ni destruir -- el estado dejado
por otros tests.

Proteccion contra un cliente HTTP real, en dos frentes distintos:

A. Las etapas SI usan el fake: `install_fake_client` parchea
   `OpenAICompatibleChatClient` en los dos puntos de uso reales
   (`rule_drafts_generated_stage`/`guardrails_applied_stage`); se afirma
   que ambos contadores de llamadas reflejan lo esperado.
B. El cliente productivo NO puede usarse inadvertidamente: se parchea
   tambien la clase que ambos puntos de uso importan
   (`pipeline.llm_client.OpenAICompatibleChatClient`) con una
   implementacion "poison" que revienta con `AssertionError` si algo
   llega a instanciarla, y registra cada intento en un contador que debe
   permanecer en cero. No se usa un timeout de red como mecanismo de
   deteccion: intentar contactar un host externo real contradiria el
   requisito de E2E sin Internet y ademas seria una prueba fragil (una
   red interna puede permitir resolucion DNS y bloquear solo el routing
   de salida)."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import neo4j
import pytest

import altamira_extractor.pipeline.guardrails_applied_stage as guardrails_stage_module
import altamira_extractor.pipeline.llm_client as llm_client_module
import altamira_extractor.pipeline.rule_drafts_generated_stage as rule_drafts_stage_module
from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import CandidateArtifact
from altamira_extractor.contracts.context_manifest import ContextDirectoryManifest
from altamira_extractor.contracts.context_package import ContextPackage
from altamira_extractor.contracts.enums import (
    CandidateStatus,
    EvidenceValidationStatus,
    FunctionalReviewStatus,
    GuardrailVerdict,
    PipelineStage,
    Severity,
    StageStatus,
)
from altamira_extractor.contracts.guardrail_candidate import GuardrailCandidateArtifact
from altamira_extractor.contracts.guardrail_manifest import GuardrailDirectoryManifest
from altamira_extractor.contracts.invariants import InvariantArtifact
from altamira_extractor.contracts.rules_manifest import RulesDirectoryManifest
from altamira_extractor.pipeline.evidence_catalog import build_evidence_catalog
from altamira_extractor.pipeline.markdown_renderer import _MANDATORY_DISCLAIMER
from altamira_extractor.pipeline.runner import run_ingestion

from ..e2e_support import (
    build_settings,
    install_fake_client,
    require_jar,
    valid_payload,
    write_package_zip,
)

_JSON_DECODER = json.JSONDecoder()


def _extract_context_package_json(message_content: str) -> dict[str, Any]:
    """El template de `rule_writer_user.md` (`e2e_support.write_prompt_files`)
    incrusta el JSON del `ContextPackage` real entre prosa fija
    ("Genera un RuleDraft.", "Devuelve solo JSON."). En vez de asumir esas
    cadenas exactas (fragil si el template cambia), se decodifica el
    primer objeto JSON valido que aparece en el mensaje."""
    start = message_content.index("{")
    obj, _ = _JSON_DECODER.raw_decode(message_content, start)
    return obj


def _evidence_id_for_kind(context_package: dict[str, Any], kind: str) -> str:
    matches = [
        entry["evidence_id"] for entry in context_package["evidence"] if entry["kind"] == kind
    ]
    assert len(matches) == 1, f"se esperaba exactamente 1 evidencia de kind={kind!r}: {matches}"
    return matches[0]


def _install_dynamic_rule_draft_fake_client(
    monkeypatch: pytest.MonkeyPatch, module: Any
) -> list[list[Any]]:
    """Version de `e2e_support.install_fake_client` especifica de este
    E2E: en vez de responder con un payload fijo desde una cola, lee el
    ContextPackage real recibido en cada llamada y arma el RuleDraft
    referenciando ALIAS reales del catalogo de evidencia de esta
    ejecucion (checkpoint correctivo: `evidence_refs`, nunca
    `evidence_id`/`evidence_path` reales -- ver docstring del modulo:
    ambos son dinamicos porque `source_package_hash` varia por ejecucion
    a proposito). `build_evidence_catalog` es la MISMA funcion que usa
    produccion, nunca reimplementada aqui."""
    calls: list[list[Any]] = []

    class _DynamicFakeClient:
        def __init__(self, profile: Any, **kwargs: Any) -> None:
            self.profile = profile

        async def __aenter__(self) -> _DynamicFakeClient:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def complete(self, messages: list[Any]) -> dict[str, Any]:
            calls.append(messages)
            user_message = next(message for message in messages if message.role == "user")
            context_package_dict = _extract_context_package_json(user_message.content)
            package = ContextPackage.model_validate(context_package_dict)
            catalog = build_evidence_catalog(package)
            decision_evidence_id = _evidence_id_for_kind(context_package_dict, "decision")
            return_code_evidence_id = _evidence_id_for_kind(
                context_package_dict, "return_code_effect"
            )
            decision_alias = next(
                entry.alias
                for entry in catalog.entries
                if entry.evidence_id == decision_evidence_id
            )
            return_code_alias = next(
                entry.alias
                for entry in catalog.entries
                if entry.evidence_id == return_code_evidence_id
            )
            return valid_payload(
                traceability=[decision_evidence_id],
                claims=[
                    {
                        "claim_id": "c1",
                        "field": "condition",
                        "evidence_refs": [decision_alias],
                    },
                    {
                        "claim_id": "c2",
                        "field": "effect",
                        "evidence_refs": [return_code_alias],
                    },
                ],
            )

    monkeypatch.setattr(module, "OpenAICompatibleChatClient", _DynamicFakeClient)
    return calls

pytestmark = pytest.mark.integration

# Las 15 etapas concretas del recorrido esperado (CLAUDE.md, seccion
# "Pipeline"). No se asume que basta con `current_stage == COMPLETED`:
# el contrato de `PipelineStage`/`StageStatus` admite en principio otros
# valores (p. ej. un futuro SKIPPED) que esta lista deliberadamente NO
# acepta para ninguna de estas 15 etapas concretas.
_EXPECTED_STAGE_ORDER = [
    PipelineStage.RECEIVED,
    PipelineStage.VALIDATED,
    PipelineStage.EXTRACTED,
    PipelineStage.INVENTORIED,
    PipelineStage.PARSED,
    PipelineStage.DEPENDENCIES_BUILT,
    PipelineStage.SEMANTIC_ENRICHMENT_BUILT,
    PipelineStage.SEMANTIC_GRAPH_BUILT,
    PipelineStage.SEMANTIC_GRAPH_LOADED,
    PipelineStage.GRAPH_VALIDATED,
    PipelineStage.CANDIDATES_DETECTED,
    PipelineStage.CONTEXTS_BUILT,
    PipelineStage.RULE_DRAFTS_GENERATED,
    PipelineStage.GUARDRAILS_APPLIED,
    PipelineStage.COMPLETED,
]

# Cypher parametrizado unicamente por `source_package_hash` (nunca
# concatenado): el patron de propiedad `{source_package_hash: $...}`
# solo coincide con nodos que realmente tengan esa propiedad -- Country/
# Application/Operation/DomainTerm (identidades compartidas entre
# paquetes/versiones, ver CLAUDE.md "Identidad y versionado") no la
# llevan y por lo tanto nunca se tocan aqui.
_COUNT_BY_SOURCE_PACKAGE_HASH_QUERY = (
    "MATCH (n {source_package_hash: $source_package_hash}) RETURN count(n) AS c"
)
_DELETE_BY_SOURCE_PACKAGE_HASH_QUERY = (
    "MATCH (n {source_package_hash: $source_package_hash}) DETACH DELETE n"
)


def _compute_source_package_hash(zip_path: Path) -> str:
    """Misma logica que `pipeline.runner._copy_and_hash` (SHA-256 sobre
    los bytes crudos del ZIP tal como quedan en disco): no se reimporta
    esa funcion privada porque ademas copia el archivo a un destino (un
    efecto secundario innecesario aqui) -- pero el algoritmo en si,
    `hashlib.sha256(bytes).hexdigest()` sobre el contenido exacto del
    ZIP, es identico, no una reimplementacion divergente."""
    return hashlib.sha256(zip_path.read_bytes()).hexdigest()


def _install_poisoned_transport(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Segunda propiedad de proteccion (ver docstring del modulo): si
    `OpenAICompatibleChatClient` real llegara a instanciarse, revienta de
    inmediato y queda registrado aqui. La prueba afirma al final que
    esta lista permanece vacia."""
    calls: list[object] = []

    class _PoisonedTransportClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            calls.append((args, kwargs))
            raise AssertionError(
                "OpenAICompatibleChatClient real instanciado durante el E2E Docker sin "
                "internet: ambos puntos de uso deberian estar parchados con el fake client"
            )

    monkeypatch.setattr(llm_client_module, "OpenAICompatibleChatClient", _PoisonedTransportClient)
    return calls


def _neo4j_driver(settings: Settings) -> neo4j.Driver:
    return neo4j.GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )


def _count_nodes_for_hash(settings: Settings, source_package_hash: str) -> int:
    driver = _neo4j_driver(settings)
    try:
        with driver.session(database=settings.neo4j_database) as session:
            row = session.run(
                _COUNT_BY_SOURCE_PACKAGE_HASH_QUERY, source_package_hash=source_package_hash
            ).single()
    finally:
        driver.close()
    return int(row["c"])


def _delete_nodes_for_hash(settings: Settings, source_package_hash: str) -> None:
    """Limpieza acotada exclusivamente al `source_package_hash` de ESTA
    ejecucion -- nunca un `MATCH (n) DETACH DELETE n` global. Best
    effort: si Neo4j ya no esta disponible (p. ej. el propio test fallo
    por eso), no oculta el error original de la prueba."""
    driver = _neo4j_driver(settings)
    try:
        with driver.session(database=settings.neo4j_database) as session:
            session.run(
                _DELETE_BY_SOURCE_PACKAGE_HASH_QUERY, source_package_hash=source_package_hash
            )
    finally:
        driver.close()


def test_docker_e2e_full_pipeline_reaches_completed_without_internet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    require_jar()
    poison_calls = _install_poisoned_transport(monkeypatch)

    settings = build_settings(tmp_path)
    # evidence_id dinamico (ver docstring del modulo): a diferencia de
    # los otros 3 E2E, aqui no se puede usar `valid_payload()` con los
    # evidence_id constantes de e2e_support -- source_package_hash varia
    # por ejecucion a proposito, y con el, candidate_id/evidence_id.
    fake_calls = _install_dynamic_rule_draft_fake_client(monkeypatch, rule_drafts_stage_module)
    # El fixture ya pasa el guardrail en el primer intento (ahora con
    # evidence_id reales, no los constantes): ninguna reparacion
    # deberia invocarse.
    repair_calls = install_fake_client(monkeypatch, guardrails_stage_module, [])

    # ZIP con bytes unicos por ejecucion (ver docstring del modulo): el
    # `source_package_hash` resultante nunca fue cargado antes en esta
    # instancia Neo4j, sin importar que otros tests ya hayan dejado
    # grafos validos de OTROS paquetes.
    zip_path = write_package_zip(tmp_path / "package.zip", unique_marker=uuid.uuid4().hex)
    source_package_hash = _compute_source_package_hash(zip_path)

    pre_count = _count_nodes_for_hash(settings, source_package_hash)
    assert pre_count == 0, (
        f"el source_package_hash {source_package_hash} generado para esta ejecucion ya "
        f"tenia {pre_count} nodos en Neo4j (coincidencia con una ejecucion anterior)"
    )

    try:
        state = run_ingestion(zip_path, settings)

        assert state.current_stage == PipelineStage.COMPLETED, state
        assert state.source_package_hash == source_package_hash
        stage_names = [execution.stage for execution in state.stages]
        assert stage_names == _EXPECTED_STAGE_ORDER
        for expected_stage in _EXPECTED_STAGE_ORDER:
            matching = [
                execution for execution in state.stages if execution.stage == expected_stage
            ]
            assert len(matching) == 1, f"{expected_stage} deberia aparecer exactamente una vez"
            assert matching[0].status == StageStatus.SUCCEEDED
        assert not any(execution.status == StageStatus.FAILED for execution in state.stages)

        assert len(fake_calls) > 0
        assert len(repair_calls) == 0
        assert len(poison_calls) == 0

        run_dir = settings.runs_dir / state.run_id
        assert run_dir.resolve().is_relative_to(tmp_path.resolve()), (
            "el run debe haberse creado exclusivamente debajo de tmp_path"
        )
        artifacts_dir = run_dir / "artifacts"

        # PARSED con evidencia real (punto 16 de la autorizacion de
        # 14b): no basta con que la etapa figure SUCCEEDED -- se lee el
        # CanonicalProgram real producido por el parser Java y se
        # confirma que el IF del fixture (ver e2e_support.PROGRAM_SOURCE,
        # nunca modificado por el marcador unico) tiene
        # expression/normalized_expression reales.
        canonical_dir = artifacts_dir / "02-canonical"
        canonical_path = next(canonical_dir.glob("**/*.json"))
        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
        if_statement = next(
            stmt
            for paragraph in canonical["paragraphs"]
            for stmt in paragraph["statements"]
            if stmt["kind"] == "IF"
        )
        assert if_statement["expression"] is not None
        assert if_statement["normalized_expression"] is not None
        assert if_statement["normalized_expression"] == if_statement["expression"].strip()

        invariants = InvariantArtifact.model_validate_json(
            (artifacts_dir / "05-invariants.json").read_text(encoding="utf-8")
        )
        assert invariants.source_package_hash == source_package_hash
        assert invariants.graph_validated is True
        assert invariants.error_count == 0
        assert invariants.node_count > 0
        assert invariants.relationship_count > 0

        candidates = CandidateArtifact.model_validate_json(
            (artifacts_dir / "06-candidates.json").read_text(encoding="utf-8")
        )
        assert candidates.source_package_hash == source_package_hash
        assert len(candidates.candidates) == 1
        candidate = candidates.candidates[0]
        assert candidate.status == CandidateStatus.DETECTED_CANDIDATE
        assert candidate.outcome_code == "R001"

        # Aislamiento Neo4j (despues): el grafo de ESTE
        # source_package_hash exclusivo si existe.
        post_count = _count_nodes_for_hash(settings, source_package_hash)
        assert post_count > 0, (
            f"no se encontro ningun nodo con source_package_hash={source_package_hash} "
            "en Neo4j tras la ingesta"
        )

        # Correspondencia candidato <-> manifests (punto 13 de la
        # autorizacion de 14b): para el candidato seleccionado, aparece
        # EXACTAMENTE una vez en cada manifest, con filenames relativos
        # contenidos en su directorio y hashes fisicos que coinciden con
        # el contenido real en disco. No se exige que TODOS los
        # candidatos sean identicos entre manifests (aqui solo hay uno),
        # solo que la cadena de este candidato este completa.
        context_dir = artifacts_dir / "07-context"
        context_manifest = ContextDirectoryManifest.model_validate_json(
            (context_dir / "context-manifest.json").read_text(encoding="utf-8")
        )
        assert context_manifest.source_package_hash == source_package_hash
        context_matches = [
            record
            for record in context_manifest.context_records
            if record.candidate_id == candidate.candidate_id
        ]
        assert len(context_matches) == 1
        context_record = context_matches[0]
        expected_context_filename = (
            hashlib.sha256(candidate.candidate_id.encode("utf-8")).hexdigest() + ".json"
        )
        assert context_record.relative_filename == expected_context_filename

        context_package_path = context_dir / context_record.relative_filename
        assert context_package_path.is_file()
        assert not context_package_path.is_symlink()
        assert context_package_path.resolve().parent == context_dir.resolve()
        context_package = ContextPackage.model_validate_json(
            context_package_path.read_text(encoding="utf-8")
        )
        assert context_package.candidate.candidate_id == candidate.candidate_id
        actual_context_hash = hashlib.sha256(
            context_package.to_stable_json().encode("utf-8")
        ).hexdigest()
        assert actual_context_hash == context_record.context_hash

        guardrail_dir = artifacts_dir / "09-guardrails"
        guardrail_manifest = GuardrailDirectoryManifest.model_validate_json(
            (guardrail_dir / "guardrail-manifest.json").read_text(encoding="utf-8")
        )
        assert guardrail_manifest.source_package_hash == source_package_hash
        guardrail_matches = [
            record
            for record in guardrail_manifest.records
            if record.candidate_id == candidate.candidate_id
        ]
        assert len(guardrail_matches) == 1
        guardrail_record = guardrail_matches[0]
        assert (
            guardrail_record.final_evidence_validation_status
            == EvidenceValidationStatus.EVIDENCE_VALIDATED
        )
        assert guardrail_record.repair_attempts_used == 0

        guardrail_artifact_path = guardrail_dir / guardrail_record.relative_filename
        assert guardrail_artifact_path.is_file()
        assert not guardrail_artifact_path.is_symlink()
        assert guardrail_artifact_path.resolve().parent == guardrail_dir.resolve()
        guardrail_artifact = GuardrailCandidateArtifact.model_validate_json(
            guardrail_artifact_path.read_text(encoding="utf-8")
        )
        assert guardrail_artifact.candidate_id == candidate.candidate_id
        assert guardrail_artifact.source_package_hash == source_package_hash
        actual_guardrail_hash = hashlib.sha256(
            guardrail_artifact.to_stable_json().encode("utf-8")
        ).hexdigest()
        assert actual_guardrail_hash == guardrail_record.guardrail_artifact_hash

        # Resultado funcional (punto 14 de la autorizacion de 14b): el
        # veredicto real (via GuardrailCandidateArtifact, no un campo
        # inexistente en GuardrailRecord), el estado final del
        # RuleDraft, la revision funcional obligatoria (V1 solo define
        # este unico valor), y ninguna violacion ERROR (el contrato ya
        # lo exige al validar, esto es una segunda confirmacion
        # explicita, no la unica).
        assert guardrail_artifact.guardrail_report.verdict == GuardrailVerdict.EVIDENCE_VALIDATED
        assert (
            guardrail_artifact.final_rule_draft.evidence_validation_status
            == EvidenceValidationStatus.EVIDENCE_VALIDATED
        )
        assert (
            guardrail_artifact.final_rule_draft.functional_review_status
            == FunctionalReviewStatus.NEEDS_FUNCTIONAL_REVIEW
        )
        assert not any(
            violation.severity == Severity.ERROR
            for violation in guardrail_artifact.guardrail_report.violations
        )

        rules_dir = artifacts_dir / "10-rules"
        rules_manifest = RulesDirectoryManifest.model_validate_json(
            (rules_dir / "rules-manifest.json").read_text(encoding="utf-8")
        )
        assert rules_manifest.source_package_hash == source_package_hash
        rules_matches = [
            record
            for record in rules_manifest.records
            if record.candidate_id == candidate.candidate_id
        ]
        assert len(rules_matches) == 1
        rules_record = rules_matches[0]
        expected_markdown_filename = (
            hashlib.sha256(candidate.candidate_id.encode("utf-8")).hexdigest() + ".md"
        )
        assert rules_record.relative_filename == expected_markdown_filename

        markdown_path = rules_dir / rules_record.relative_filename
        assert markdown_path.is_file()
        assert not markdown_path.is_symlink()
        assert markdown_path.resolve().parent == rules_dir.resolve()

        markdown_bytes = markdown_path.read_bytes()
        assert hashlib.sha256(markdown_bytes).hexdigest() == rules_record.markdown_hash
        # .decode("utf-8") sin errors="replace": revienta con
        # UnicodeDecodeError si el contenido no fuera UTF-8 valido.
        markdown_text = markdown_bytes.decode("utf-8")
        assert "\r" not in markdown_text
        assert markdown_text.endswith("\n")
        assert not markdown_text.endswith("\n\n")
        assert all(line == line.rstrip(" \t") for line in markdown_text.split("\n"))

        # Disclaimer importado de la constante real del renderer (aunque
        # sea privada/sin exportar formalmente): nunca un literal que
        # podria divergir con el tiempo.
        assert _MANDATORY_DISCLAIMER in markdown_text

        # El renderer proyecta estos dos campos de forma literal, nunca
        # los inventa ni normaliza (ver
        # markdown_renderer.render_markdown). Se comprueba la linea
        # EXACTA que produce, no una palabra aislada: un RuleDraft de un
        # LLM real podria coincidencialmente contener palabras similares
        # en texto libre ("aprobada", "rechazado", etc.) sin que eso
        # implique nada sobre el estado real de la regla.
        # FunctionalReviewStatus define un unico miembro en V1
        # (NEEDS_FUNCTIONAL_REVIEW): no existe ninguna otra frase que el
        # renderer pueda introducir para ese campo.
        assert (
            f"> Estado de evidencia: {EvidenceValidationStatus.EVIDENCE_VALIDATED.value}"
            in markdown_text
        )
        expected_review_line = (
            "> Estado de revisión funcional: "
            f"{FunctionalReviewStatus.NEEDS_FUNCTIONAL_REVIEW.value}"
        )
        assert expected_review_line in markdown_text
    finally:
        # Limpieza acotada (Prompt 14b.1): como maximo los nodos de ESTE
        # source_package_hash exclusivo, nunca `MATCH (n) DETACH DELETE
        # n`. Corre incluso si alguna asercion fallo arriba, para que el
        # test pueda repetirse contra la misma instancia Neo4j sin
        # depender de limpieza manual.
        _delete_nodes_for_hash(settings, source_package_hash)


def _install_invalid_then_repaired_rule_draft_fake_client(
    monkeypatch: pytest.MonkeyPatch, module: Any
) -> list[list[Any]]:
    """Fase 15B4-HOTFIX-1: reproduce herméticamente el fallo real
    (`claims.0.field (enum)` agotando la reparación estructural) contra
    el pipeline COMPLETO -- Java real, Neo4j real, sin LLM real. La
    respuesta inicial usa un valor de `field` fuera del `ClaimField`
    enum (`"outcome"`, nunca un miembro real); la respuesta de
    reparación usa un miembro válido (`"condition"`), demostrando que el
    ciclo de reparación converge en el primer intento ahora que ambos
    prompts reciben `ALLOWED_CLAIM_FIELDS_JSON` explícito. El
    `ContextPackage`/catálogo de evidencia solo se puede extraer del
    mensaje de la llamada INICIAL (`rule_writer_user.md` incrusta el
    ContextPackage completo); el prompt de reparación nunca lo repite
    -- los alias ya resueltos se reutilizan tal cual en la respuesta de
    reparación."""
    calls: list[list[Any]] = []
    state: dict[str, str] = {}

    class _InvalidThenRepairedFakeClient:
        def __init__(self, profile: Any, **kwargs: Any) -> None:
            self.profile = profile

        async def __aenter__(self) -> _InvalidThenRepairedFakeClient:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def complete(self, messages: list[Any]) -> dict[str, Any]:
            calls.append(messages)
            if len(calls) == 1:
                user_message = next(m for m in messages if m.role == "user")
                context_package_dict = _extract_context_package_json(user_message.content)
                package = ContextPackage.model_validate(context_package_dict)
                catalog = build_evidence_catalog(package)
                decision_evidence_id = _evidence_id_for_kind(context_package_dict, "decision")
                return_code_evidence_id = _evidence_id_for_kind(
                    context_package_dict, "return_code_effect"
                )
                state["decision_evidence_id"] = decision_evidence_id
                state["decision_alias"] = next(
                    entry.alias
                    for entry in catalog.entries
                    if entry.evidence_id == decision_evidence_id
                )
                state["return_code_alias"] = next(
                    entry.alias
                    for entry in catalog.entries
                    if entry.evidence_id == return_code_evidence_id
                )
                return valid_payload(
                    traceability=[state["decision_evidence_id"]],
                    claims=[
                        {
                            "claim_id": "c1",
                            "field": "outcome",
                            "evidence_refs": [state["decision_alias"]],
                        },
                        {
                            "claim_id": "c2",
                            "field": "effect",
                            "evidence_refs": [state["return_code_alias"]],
                        },
                    ],
                )
            return valid_payload(
                traceability=[state["decision_evidence_id"]],
                claims=[
                    {
                        "claim_id": "c1",
                        "field": "condition",
                        "evidence_refs": [state["decision_alias"]],
                    },
                    {
                        "claim_id": "c2",
                        "field": "effect",
                        "evidence_refs": [state["return_code_alias"]],
                    },
                ],
            )

    monkeypatch.setattr(module, "OpenAICompatibleChatClient", _InvalidThenRepairedFakeClient)
    return calls


def test_docker_e2e_invalid_claim_field_enum_then_repaired_reaches_completed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fase 15B4-HOTFIX-1: reproducción end-to-end (sin proveedor real)
    del incidente observado en la prueba manual -- `claims.0.field` con
    un valor fuera del enum en la respuesta inicial. Antes del fix, el
    prompt de escritura inicial nunca enumeraba los valores permitidos
    de `field`; después del fix (`ALLOWED_CLAIM_FIELDS_JSON`, derivado
    del `ClaimField` real, en ambos prompts) un único intento de
    reparación converge y la corrida completa llega a COMPLETED."""
    require_jar()
    poison_calls = _install_poisoned_transport(monkeypatch)

    settings = build_settings(tmp_path)
    fake_calls = _install_invalid_then_repaired_rule_draft_fake_client(
        monkeypatch, rule_drafts_stage_module
    )
    guardrail_calls = install_fake_client(monkeypatch, guardrails_stage_module, [])

    zip_path = write_package_zip(tmp_path / "package.zip", unique_marker=uuid.uuid4().hex)
    source_package_hash = _compute_source_package_hash(zip_path)

    pre_count = _count_nodes_for_hash(settings, source_package_hash)
    assert pre_count == 0, (
        f"el source_package_hash {source_package_hash} generado para esta ejecucion ya "
        f"tenia {pre_count} nodos en Neo4j (coincidencia con una ejecucion anterior)"
    )

    try:
        state = run_ingestion(zip_path, settings)

        assert state.current_stage == PipelineStage.COMPLETED, state
        rule_drafts_execution = next(
            execution
            for execution in state.stages
            if execution.stage == PipelineStage.RULE_DRAFTS_GENERATED
        )
        assert rule_drafts_execution.status == StageStatus.SUCCEEDED
        assert any(
            "reparado tras 1 intento" in warning for warning in rule_drafts_execution.warnings
        )

        # Llamada inicial (rechazada por enum invalido) + exactamente 1
        # intento de reparacion -- nunca agota los 2 disponibles, nunca
        # cae a fail-closed.
        assert len(fake_calls) == 2
        assert len(guardrail_calls) == 0
        assert len(poison_calls) == 0
    finally:
        _delete_nodes_for_hash(settings, source_package_hash)
