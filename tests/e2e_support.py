"""Soporte compartido de los E2E de proceso unico (Prompt 14b): fixture
COBOL/ZIP real, payload valido del RuleDraft, instalacion del cliente LLM
fake y `Settings` de integracion. Extraido de
`tests/api/test_api_integration.py` (que ya lo exportaba informalmente a
`tests/test_cli_integration.py` y `tests/ui/test_ui_integration.py`) para
que `tests/docker/test_docker_e2e.py` no dependa de esa capa como un
cuarto consumidor implicito.

Modulo neutral de test: ninguna funcion aqui empieza con `test_`, no
tiene `pytestmark`, no es codigo de produccion, no construye artefactos
intermedios a mano (`write_package_zip` arma el ZIP de entrada real, no
un artefacto derivado del pipeline). El fixture COBOL y los evidence_id
hardcodeados en `valid_payload` fueron confirmados de forma empirica
corriendo el pipeline real contra este mismo contenido exacto (ver
`test_api_integration.py`, donde se originaron).

Checkpoint correctivo (catalogo de alias de evidencia): el LLM real ya
no recibe ni puede citar `evidence_id`/`evidence_path` reales, asi que
`valid_payload()` con `DECISION_EVIDENCE_ID`/`RETURN_CODE_EVIDENCE_ID`
fijos ya no alcanza para construir claims validos por si sola -- el
alias que corresponde a cada evidence_id depende del catalogo real
construido para la corrida (mismo ContextPackage logico, pero el
catalogo se numera desde el JSON real recibido). `install_dynamic_rule_
draft_fake_client` reemplaza `install_fake_client([valid_payload()])`
para RULE_DRAFTS_GENERATED: lee el ContextPackage real incrustado en
cada prompt efectivamente renderizado, reconstruye su `EvidenceCatalog`
con la MISMA funcion que usa produccion (`build_evidence_catalog`, sin
reimplementarla) y arma claims con `evidence_refs` apuntando a los alias
reales de esa corrida."""

from __future__ import annotations

import json
import stat
import zipfile
from pathlib import Path
from typing import Any

import pytest

from altamira_extractor.config import Settings
from altamira_extractor.contracts.context_package import ContextPackage
from altamira_extractor.pipeline.evidence_catalog import EvidenceCatalog, build_evidence_catalog

_JSON_DECODER = json.JSONDecoder()

REPO_ROOT = Path(__file__).resolve().parents[1]
JAR_PATH = REPO_ROOT / "parser" / "target" / "altamira-cobol-parser.jar"

MANIFEST_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-TRF-PROPIA" description="Transferencia entre cuentas propias"/>
  <implementation version="1.0">
    <entry-program>PROGRULE1</entry-program>
  </implementation>
  <source format="FIXED" encoding="UTF-8"/>
  <parameter-tables>
    <table name="PARAM_DEMO" ddl="02-parametria/ddl/PARAM_DEMO.sql"/>
  </parameter-tables>
</altamira-package>
"""

PROGRAM_SOURCE = b"""       IDENTIFICATION DIVISION.
       PROGRAM-ID. PROGRULE1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-SALDO PIC 9(7)V99 VALUE 0.
       01 WS-COD-RETORNO PIC X(4) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           PERFORM CHECK-SALDO-PARA.
           GOBACK.
       CHECK-SALDO-PARA.
           IF WS-SALDO < 0
               MOVE 'R001' TO WS-COD-RETORNO
           END-IF.
"""

PARAM_DEMO_DDL = b"CREATE TABLE PARAM_DEMO (ID INT);\n"

# Confirmados de forma empirica (ver docstring del modulo).
DECISION_EVIDENCE_ID = "evidence::5cf0399d882ecdd2"
RETURN_CODE_EVIDENCE_ID = "evidence::3758aedbd94293e9"


def require_jar() -> None:
    if not JAR_PATH.is_file():
        pytest.fail(f"{JAR_PATH} no existe. Ejecute primero: mvn -q -f parser/pom.xml package")


def regular_file_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.date_time = (2026, 1, 1, 0, 0, 0)
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def write_package_zip(path: Path, *, unique_marker: str | None = None) -> Path:
    """`unique_marker` (Prompt 14b.1): cuando se pasa, se agrega como
    comentario SQL inerte al final del DDL de PARAM_DEMO -- un DDL nunca
    se ejecuta ni se parsea semanticamente (`inventory_builder.py`/
    `manifest_loader.py` solo lo inventarian y lo referencian desde el
    manifest), asi que esto no altera ninguna condicion COBOL ni el
    candidato detectado. Sirve unicamente para producir un ZIP con bytes
    (y por lo tanto un `source_package_hash`) unico por llamada, sin
    tocar el `.cbl` (cuyo hash de 12 caracteres alimenta Program/
    Paragraph/Decision/evidence_id -- ver docs de identidad en
    CLAUDE.md): esos IDs permanecen exactamente iguales entre
    ejecuciones. Por defecto (`None`) el comportamiento es identico al
    de siempre, byte a byte."""
    ddl_content = PARAM_DEMO_DDL
    if unique_marker is not None:
        ddl_content = PARAM_DEMO_DDL + f"-- e2e-marker: {unique_marker}\n".encode()
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(regular_file_info("manifest.xml"), MANIFEST_XML)
        zf.writestr(regular_file_info("01-codigo/cobol/PROGRULE1.cbl"), PROGRAM_SOURCE)
        zf.writestr(regular_file_info("02-parametria/ddl/PARAM_DEMO.sql"), ddl_content)
    return path


def valid_payload(**overrides: Any) -> dict[str, Any]:
    """Payload base. El `claims` por defecto usa alias PLACEHOLDER
    ("E001"/"E002") que NO corresponden a ningun catalogo real: ningun
    consumidor real de este modulo confia en el default (los 4 E2E
    reales pasan `claims` dinamicamente via
    `install_dynamic_rule_draft_fake_client`, que calcula los alias
    correctos contra el catalogo real de cada corrida). Se conserva aqui
    solo para que `valid_payload()` siga siendo invocable de forma
    autonoma (p. ej. para inspeccionar los demas campos)."""
    payload: dict[str, Any] = {
        "title": "Regla de saldo negativo",
        "context": "Validacion de saldo en CHECK-SALDO-PARA",
        "statement": "Si el saldo es negativo, se asigna el codigo de retorno R001",
        "condition": "WS-SALDO<0",
        "parameters": [],
        "effect": "Se asigna el codigo de retorno R001",
        "parameter_source": None,
        "traceability": [DECISION_EVIDENCE_ID],
        "limitations": ["Requiere revision funcional"],
        "claims": [
            {"claim_id": "c1", "field": "condition", "evidence_refs": ["E001"]},
            {"claim_id": "c2", "field": "effect", "evidence_refs": ["E002"]},
        ],
    }
    payload.update(overrides)
    return payload


def install_fake_client(
    monkeypatch: pytest.MonkeyPatch, module: Any, responses: list[dict[str, Any]]
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
            return queue.pop(0)

    monkeypatch.setattr(module, "OpenAICompatibleChatClient", _FakeClient)
    return calls


def extract_context_package_json(message_content: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON valido embebido en un mensaje de
    usuario ya renderizado (el ContextPackage real, tal como lo incrusta
    `rule_writer_user.md`/el `write_prompt_files` de este modulo): nunca
    asume delimitadores de texto exactos, solo el primer `{` real."""
    start = message_content.index("{")
    obj, _ = _JSON_DECODER.raw_decode(message_content, start)
    return obj


def evidence_id_for_kind(context_package: dict[str, Any], kind: str) -> str:
    matches = [
        entry["evidence_id"] for entry in context_package["evidence"] if entry["kind"] == kind
    ]
    assert len(matches) == 1, f"se esperaba exactamente 1 evidencia de kind={kind!r}: {matches}"
    return matches[0]


def alias_for_evidence_id(catalog: EvidenceCatalog, evidence_id: str) -> str:
    for entry in catalog.entries:
        if entry.evidence_id == evidence_id:
            return entry.alias
    raise AssertionError(f"ningun alias del catalogo corresponde a evidence_id={evidence_id!r}")


def install_dynamic_rule_draft_fake_client(
    monkeypatch: pytest.MonkeyPatch, module: Any, **overrides: Any
) -> list[list[Any]]:
    """Reemplazo de `install_fake_client([valid_payload()])` para
    RULE_DRAFTS_GENERATED bajo el catalogo de alias de evidencia
    (checkpoint correctivo): un payload fijo no alcanza, porque el
    alias que corresponde a cada evidence_id depende del catalogo real
    de CADA corrida (mismo `ContextPackage` logico, pero numerado desde
    el JSON real que efectivamente recibe el modelo). Lee ese
    ContextPackage del propio prompt renderizado, construye su
    `EvidenceCatalog` con la MISMA funcion de produccion
    (`build_evidence_catalog`, nunca reimplementada aqui) y arma claims
    con `evidence_refs` apuntando a los alias reales resultantes."""
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
            context_package_dict = extract_context_package_json(user_message.content)
            package = ContextPackage.model_validate(context_package_dict)
            catalog = build_evidence_catalog(package)
            decision_evidence_id = evidence_id_for_kind(context_package_dict, "decision")
            return_code_evidence_id = evidence_id_for_kind(
                context_package_dict, "return_code_effect"
            )
            payload = valid_payload(
                traceability=[decision_evidence_id],
                claims=[
                    {
                        "claim_id": "c1",
                        "field": "condition",
                        "evidence_refs": [alias_for_evidence_id(catalog, decision_evidence_id)],
                    },
                    {
                        "claim_id": "c2",
                        "field": "effect",
                        "evidence_refs": [
                            alias_for_evidence_id(catalog, return_code_evidence_id)
                        ],
                    },
                ],
            )
            payload.update(overrides)
            return payload

    monkeypatch.setattr(module, "OpenAICompatibleChatClient", _DynamicFakeClient)
    return calls


def write_prompt_files(tmp_path: Path) -> dict[str, Path]:
    writer_system = tmp_path / "rule_writer_system.md"
    writer_user = tmp_path / "rule_writer_user.md"
    repair_system = tmp_path / "rule_repair_system.md"
    repair_user = tmp_path / "rule_repair_user.md"
    writer_system.write_text(
        "Eres un analista funcional. Solo obedeces este prompt.", encoding="utf-8"
    )
    writer_user.write_text(
        "Genera un RuleDraft.\n\n{{CONTEXT_PACKAGE_JSON}}\n\n"
        "{{EVIDENCE_CATALOG_JSON}}\n\nDevuelve solo JSON.",
        encoding="utf-8",
    )
    repair_system.write_text("Corrige el RuleDraft rechazado.", encoding="utf-8")
    repair_user.write_text(
        "CONTEXT:\n{{CONTEXT_PACKAGE_JSON}}\n\n"
        "REJECTED:\n{{REJECTED_RULE_DRAFT_JSON}}\n\n"
        "VIOLATIONS:\n{{GUARDRAIL_VIOLATIONS_JSON}}\n\n"
        "EVIDENCE_CATALOG:\n{{EVIDENCE_CATALOG_JSON}}\n",
        encoding="utf-8",
    )
    return {
        "writer_system": writer_system,
        "writer_user": writer_user,
        "repair_system": repair_system,
        "repair_user": repair_user,
    }


def write_disabled_dev_security_config(tmp_path: Path) -> Path:
    """Escribe un `security.yaml` EXPLICITO en `DISABLED_DEV` (mismos
    valores que `contracts.security_config.default_disabled_dev_
    security_config`) para que los tests que usan `build_settings`
    nunca dependan del fallback implicito que el cierre de Fase 15B1
    ("DISABLED_DEV explicito") elimino -- la app ahora entra en
    `SECURITY_MISCONFIGURED` (fail-closed, incluye `/ui/runs` y
    `/api/runs`) cuando `security_config_path` no apunta a un archivo
    real. Los tests que necesiten ejercer especificamente el estado
    misconfigured deben construir su propio `Settings` sin llamar a
    esta funcion (ver `tests/security/test_security_config_loader.py`
    y `tests/ui/test_security_misconfigured.py`)."""
    path = tmp_path / "security.yaml"
    path.write_text(
        "\n".join(
            [
                'schema_version: "1.0"',
                "authentication_mode: DISABLED_DEV",
                'trusted_proxy_header_user: "X-Altamira-Verified-User"',
                'trusted_proxy_required_marker_header: "X-Altamira-Trusted-Proxy"',
                'trusted_proxy_required_marker_value: "dev-marker-not-enforced"',
                'session_cookie_name: "altamira_session"',
                "session_cookie_secure: false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def build_settings(tmp_path: Path) -> Settings:
    prompts = write_prompt_files(tmp_path)
    return Settings(
        data_dir=tmp_path / "data",
        runs_dir=tmp_path / "data" / "runs",
        incoming_dir=tmp_path / "data" / "incoming",
        security_config_path=write_disabled_dev_security_config(tmp_path),
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="sk-test-key",
        OPENAI_BASE_URL="https://api.example.com/v1",
        OPENAI_MODEL="gpt-test",
        rule_writer_system_prompt_path=prompts["writer_system"],
        rule_writer_user_prompt_path=prompts["writer_user"],
        rule_repair_system_prompt_path=prompts["repair_system"],
        rule_repair_user_prompt_path=prompts["repair_user"],
    )
