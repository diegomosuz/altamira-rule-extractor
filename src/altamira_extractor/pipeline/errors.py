"""Excepciones de dominio del pipeline RECEIVED..INVENTORIED.

Cada una representa un motivo de fallo explicito (python.md: "Excepciones
de dominio explicitas"). Nunca se atrapan de forma generica: el llamador
decide como traducirlas a StageExecution.error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts.enums import PipelineStage


class PipelineError(Exception):
    """Base de todas las excepciones de dominio del pipeline de ingesta."""


class AtomicWriteError(PipelineError, OSError):
    """`atomic_write_json`/`_replace_with_retry` (pipeline/artifact_store.py)
    agotaron el deadline monotonico reintentando exclusivamente errores de
    reemplazo reconocidos como transitorios en Windows (WinError 5/32/33:
    acceso denegado, violacion de uso compartido, violacion de bloqueo) sin
    lograr `os.replace`. Nunca se lanza para un error permanente (esos
    propagan sin envolver, con el tipo original) ni para otro `OSError` no
    relacionado con el reemplazo (nunca se reintenta). Hereda tambien de
    `OSError` a proposito: cada llamador existente de `atomic_write_json`
    que ya capturaba `except OSError` (p. ej. `_run_inventoried` en
    runner.py) sigue capturandola sin cambios -- ningun sitio de llamada
    disperso necesita actualizarse. El mensaje incluye la ruta destino
    (nunca el contenido del artefacto ni un secreto) y preserva la
    excepcion original via `raise ... from`."""


class PackageValidationError(PipelineError):
    """El paquete ZIP no cumple integridad, seguridad o estructura minima."""


class ZipSecurityError(PackageValidationError):
    """Una entrada del ZIP viola una regla de seguridad (path, tipo, cifrado, tamano)."""


class ManifestValidationError(PackageValidationError):
    """manifest.xml no es valido contra el XSD, el contrato Pydantic o el ZIP."""


class ExtractionError(PipelineError):
    """La extraccion segura entrada-por-entrada fallo."""


class RunConflictError(PipelineError):
    """Se intento reutilizar un run_id con un estado incompatible."""


class RunStatePersistenceError(PipelineError):
    """`run.json` no pudo persistirse para una transicion terminal
    (SUCCEEDED o FAILED de una etapa) tras agotar la politica critica de
    reemplazo atomico (ver `AtomicWriteError`). Se lanza SIEMPRE desde
    `runner._persist_run_state` (nunca desde mas de un lugar disperso),
    despues de intentar -- best effort -- escribir un artefacto durable
    de emergencia adyacente a `run.json` (ver
    `contracts/run_state_recovery.py`). `emergency_artifact_path` indica
    si ese intento tuvo exito (`Path`) o tambien fallo (`None`): en este
    ultimo caso no existe NINGUNA evidencia durable del desenlace del
    run, y quien la atrapa (`api/executor.py`) debe registrar CRITICAL en
    vez de WARNING. El mensaje nunca incluye prompts, credenciales,
    contenido de RunState ni stacktrace completo -- solo run_id, la etapa
    afectada y el tipo de la causa tecnica."""

    def __init__(
        self,
        message: str,
        *,
        run_id: str,
        stage: PipelineStage,
        transition: str,
        emergency_artifact_path: Path | None,
        cause: BaseException,
    ) -> None:
        super().__init__(message)
        self.run_id = run_id
        self.stage = stage
        self.transition = transition
        self.emergency_artifact_path = emergency_artifact_path
        self.cause = cause


class ParserUnavailableError(PipelineError):
    """El JAR del parser o el runtime Java no estan disponibles.

    Fatal a nivel de etapa PARSED completa: aborta antes de procesar
    cualquier programa (o interrumpe el resto de la cola), nunca se marca
    como fallo recuperable de un programa individual.
    """


class ParserContractViolationError(PipelineError):
    """El parser Java (o los datos de origen) violaron el contrato
    documentado: exit code fatal (2/4/5), stdout inesperado, exit 0 sin
    archivo de salida, JSON invalido, o una verificacion cruzada contra
    Inventory/RunState no coincide (source_file, source_hash,
    source_package_hash, contencion de paths). Fatal a nivel de etapa
    PARSED completa, igual que ParserUnavailableError.
    """


class DependencyBuildError(PipelineError):
    """Precondicion de DEPENDENCIES_BUILT incumplida: PARSED no esta
    realmente completo (StageExecution ausente/duplicada/no SUCCEEDED),
    falta un CanonicalProgram esperado segun Inventory, o alguno no valida
    o es inconsistente con Inventory/RunState (source_file, source_hash,
    source_package_hash). Fatal para la etapa completa: no hay reintento
    parcial posible cuando el prerequisito mismo esta roto.
    """


class SemanticConfigError(PipelineError):
    """`config/semantic-tags.yml` o `config/domain-glossary.example.yml`
    ausentes, con YAML mal formado, con estructura invalida (campos
    extra, regex invalida, confidence fuera de rango, rule_id/
    functional_key/domain_term_id duplicado, un semantic_tag asignado a
    mas de un DomainTerm) son errores de configuracion controlada: nunca
    se degradan a warning ni a un resultado parcial silencioso.
    """


class SemanticEnrichmentBuildError(PipelineError):
    """Precondicion de SEMANTIC_ENRICHMENT_BUILT incumplida: DEPENDENCIES_BUILT
    no esta realmente completo, falta/invalida un CanonicalProgram, o un
    DDL/CSV declarado en Manifest.parameter_tables no se puede verificar
    con integridad (path ausente, fuera de work/extracted, hash
    inconsistente con Inventory). Fatal para la etapa completa.
    """


class SemanticGraphBuildError(PipelineError):
    """Precondicion de SEMANTIC_GRAPH_BUILT incumplida: SEMANTIC_ENRICHMENT_BUILT
    no esta realmente completo, falta/invalida un CanonicalProgram, o
    `03-dependencies.json`/`03b-semantic-enrichment.json` no validan, no
    coinciden en run_id/source_package_hash con el run actual, o alguna de
    sus referencias (from_paragraph_id/to_paragraph_id, data_item_id) no
    resuelve contra el universo de Program/Paragraph/DataItem reconstruido
    desde los CanonicalProgram actuales. Fatal para la etapa completa: una
    referencia huerfana indica artefactos desincronizados, no ambiguedad
    legitima que se pueda tratar con un warning.
    """


class Neo4jError(PipelineError):
    """Base de errores de conexion/ejecucion contra Neo4j. Nunca incluye
    password, URI con userinfo, parametros completos de consulta ni
    stacktrace en su mensaje (.claude/rules/security.md)."""


class Neo4jConfigurationError(Neo4jError):
    """`neo4j_uri` vacio o con esquema no soportado (se espera `bolt://` o
    `neo4j://`), detectado localmente antes de intentar conectar."""


class Neo4jAuthenticationError(Neo4jError):
    """El servidor rechazo las credenciales (`neo4j.exceptions.AuthError`).
    El mensaje nunca incluye la password."""


class Neo4jUnavailableError(Neo4jError):
    """El servidor no esta disponible o no acepta conexiones
    (`neo4j.exceptions.ServiceUnavailable`)."""


class Neo4jTimeoutError(Neo4jError):
    """La conexion o una transaccion excedio el tiempo configurado."""


class Neo4jUnsupportedVersionError(Neo4jError):
    """El servidor conectado no es Neo4j major version 5 (unica version
    soportada, coincide con `docker-compose.blueprint.yml`)."""


class Neo4jQueryError(Neo4jError):
    """Una consulta Cypher concreta fallo (sintaxis, tipo, restriccion).
    El mensaje nunca incluye los parametros ni el texto completo de la
    consulta cuando estos pudieran contener `source_text`/filas
    parametricas."""


class GraphLoadError(PipelineError):
    """Precondicion de SEMANTIC_GRAPH_LOADED incumplida (SEMANTIC_GRAPH_BUILT
    no completo, `04-semantic-graph.json` ausente/invalido), o la
    verificacion previa al commit de la carga transaccional detecto una
    inconsistencia (conteos, IDs, edge_keys, o labels de ParameterTable
    que no coinciden con el `SemanticGraph` recien cargado). Fatal para la
    etapa completa: la transaccion completa hace rollback, nunca queda una
    carga parcial marcada como exitosa.
    """


class GraphValidationError(PipelineError):
    """Precondicion de GRAPH_VALIDATED incumplida: SEMANTIC_GRAPH_LOADED no
    completo, drift detectado entre `04-semantic-graph.json` y el estado
    real de Neo4j (hash/conteos/IDs/edge_keys distintos del nodo
    `AltamiraGraphLoad` activo), el hash de `config/semantic-tags.yml` ya
    no coincide con el registrado en `03b-semantic-enrichment.json`, un
    error de ejecucion de `invariants.cypher`, o al menos un invariante de
    severidad ERROR incumplido. Fatal para la etapa completa: la
    reparacion pertenece a SEMANTIC_GRAPH_LOADED, no a esta etapa.
    """


class CandidateDetectionError(PipelineError):
    """Precondicion de CANDIDATES_DETECTED incumplida: GRAPH_VALIDATED no
    completo, `04-semantic-graph.json`/`05-invariants.json` ausentes o
    invalidos, `graph_validated=false`/`error_count>0`, drift detectado
    contra el `AltamiraGraphLoad` activo, `config/semantic-tags.yml`
    modificado desde SEMANTIC_ENRICHMENT_BUILT o sin el tag `return_code`,
    `queries/v1/q0_candidates.cypher` ausente/vacio, un error de ejecucion
    de Q0, o filas de Q0 inconsistentes para la misma identidad de
    candidato. Fatal para la etapa completa: CandidateDetector nunca
    repara, recarga ni revalida el grafo — esas responsabilidades
    pertenecen a SEMANTIC_GRAPH_LOADED/GRAPH_VALIDATED.
    """


class LlmClientError(PipelineError):
    """Base de errores del cliente HTTP OpenAI-compatible (Prompt 11).

    El mensaje puede contener: proveedor, status HTTP, un codigo de error
    interno sanitizado y el numero de intentos. Nunca debe contener: API
    key, header Authorization, headers completos, body de request/response,
    el contenido de los mensajes enviados al modelo, una URI con userinfo
    ni un stacktrace (.claude/rules/security.md)."""


class LlmConfigurationError(LlmClientError):
    """El perfil LLM no se pudo resolver: `LLM_PROVIDER` ausente o invalido,
    o falta/esta vacio `base_url`/`api_key`/`model` del proveedor
    seleccionado, o `timeout_seconds`/`http_retries`/`temperature` fuera
    del rango permitido. Se detecta localmente, antes de abrir cualquier
    conexion HTTP (nunca se descubre recien en el primer request)."""


class LlmAuthenticationError(LlmClientError):
    """El proveedor rechazo las credenciales (HTTP 401/403). No
    reintentable: un reintento no cambia el resultado."""


class LlmRateLimitError(LlmClientError):
    """HTTP 429 agotó los reintentos configurados (`LLM_HTTP_RETRIES`)."""


class LlmUnavailableError(LlmClientError):
    """El proveedor no esta disponible: HTTP 502/503/504 o
    `httpx.ConnectError` agotaron los reintentos configurados."""


class LlmTimeoutError(LlmClientError):
    """Un timeout agoto los reintentos (`httpx.ConnectTimeout`/
    `httpx.PoolTimeout`), o un `httpx.ReadTimeout`/`httpx.WriteTimeout`
    ocurrio (nunca reintentable: la respuesta queda incierta — el
    proveedor pudo haber recibido o procesado la solicitud; reintentar
    arriesgaria duplicar una generacion paga)."""


class LlmRequestError(LlmClientError):
    """Un status HTTP 4xx/5xx no cubierto por las categorias anteriores
    (400/404/409/422/500/otros), o un error de protocolo HTTP
    (`httpx.RemoteProtocolError`). Nunca reintentable."""


class LlmResponseFormatError(LlmClientError):
    """El envelope HTTP 2xx no tiene la forma minima esperada: falta
    `choices`, esta vacio, falta `message`, o `message.content` no es un
    string. Los proveedores compatibles pueden agregar campos extra
    (`usage`, `id`, `created`, etc.) sin que esto sea un error — solo se
    exige la estructura minima. Nunca reintentable: la respuesta ya fue
    recibida (2xx)."""


class LlmResponseParsingError(LlmClientError):
    """`message.content` no es, en su totalidad (tras recortar unicamente
    whitespace externo), un objeto JSON estrictamente valido: incluye
    texto antes/despues, fences Markdown, una raiz que no es un objeto
    (array/string/numero/booleano/null), claves duplicadas, o
    NaN/Infinity/-Infinity. El cliente nunca intenta reparar la
    respuesta; esa responsabilidad es del repair loop de Prompt 12. Nunca
    reintentable: la respuesta ya fue recibida (2xx)."""


class ContextBuildError(PipelineError):
    """Precondicion de CONTEXTS_BUILT incumplida, o fallo durante la
    construccion/persistencia de `artifacts/07-context/`: CANDIDATES_DETECTED
    no completo, `04-semantic-graph.json`/`05-invariants.json`/
    `06-candidates.json` ausentes o invalidos, drift detectado (antes o
    durante la transaccion de lectura de Q1-Q7), alguna de las nueve
    queries ausente/no es un archivo regular/vacia/con un numero de
    placeholders `__DEPENDENCY_DEPTH__` distinto del esperado, un error
    Cypher, cardinalidad invalida de Q1/Q4 (cero o mas de una fila),
    JSON invalido en `operands_json`/filas parametricas, un predicado no
    soportado por la gramatica V1 que impida clasificar aplicabilidad,
    un limite configurado excedido (nunca se trunca en silencio), un
    ContextPackage que no valida contra Pydantic o contra
    `context-package.schema.json`, o un fallo de escritura/intercambio/
    restauracion del directorio `07-context/`. Fatal para la etapa
    completa: ContextPackageBuilder nunca repara drift, nunca reejecuta
    Q0/CandidateDetector ni `invariants.cypher`.
    """


class PromptTemplateError(PipelineError):
    """Una plantilla de `prompts/` esta ausente, no es un archivo
    regular, esta vacia, tiene un numero de placeholders `{{...}}`
    distinto del esperado, o falta un placeholder a sustituir. Traducida
    siempre por el llamador (`RuleDraftGenerationError`/`GuardrailError`)
    antes de propagarse fuera de la etapa."""


class RuleDraftGenerationError(PipelineError):
    """Precondicion de RULE_DRAFTS_GENERATED incumplida, o fallo durante
    la generacion/persistencia de `artifacts/08-rule-drafts/`:
    CONTEXTS_BUILT no completo, `context-manifest.json`/algun
    `ContextPackage` ausente o con `context_hash` inconsistente, alguna
    plantilla de prompt (`rule_writer_system.md`/`rule_writer_user.md`/
    `rule_repair_system.md`/`rule_repair_user.md`) ausente/no es un
    archivo regular/vacia/con un numero de placeholders distinto del
    esperado, el `ContextPackage` serializado excede
    `max_context_package_json_chars`, un error del cliente LLM
    (`LlmClientError`, en la llamada inicial o en cualquier intento de
    reparacion: nunca reintentable a este nivel), o un candidato agoto
    `settings.llm_repair_attempts` intentos de reparacion sin producir un
    `RuleDraft` estructuralmente valido (envelope/JSON/fences/claves
    duplicadas/raiz/campos faltantes-extra/tipos/Pydantic/schema). Una
    respuesta inicial estructuralmente invalida YA NO aborta de
    inmediato: primero se agota el ciclo de reparacion acotado (mismos
    prompts versionados que GUARDRAILS_APPLIED), y solo si tambien falla
    la etapa aborta. Fatal para la etapa COMPLETA (atomica: todo o
    nada) — un solo candidato que agote su reparacion descarta el
    directorio temporal entero y nunca promueve ni un solo draft, nunca
    alcanza GUARDRAILS_APPLIED. El mensaje nunca incluye el payload
    completo del modelo, el body de la respuesta, el prompt efectivo
    completo, el ContextPackage ni credenciales — solo candidate_id,
    numero de intentos y los campos (loc) afectados por categoria.

    `validation_errors` (opcional, vacio por defecto) expone los mismos
    detalles estructurados loc/type/msg de `RuleDraftAssemblyError` para
    quien necesite inspeccionarlos programaticamente (p. ej. tests) sin
    parsear el mensaje de texto."""

    def __init__(self, message: str, *, validation_errors: tuple[Any, ...] = ()) -> None:
        super().__init__(message)
        self.validation_errors = validation_errors


class GuardrailError(PipelineError):
    """Precondicion de GUARDRAILS_APPLIED incumplida, o fallo durante la
    validacion/reparacion/persistencia de `artifacts/09-guardrails/`:
    RULE_DRAFTS_GENERATED no completo, `rule-draft-manifest.json`/algun
    `RuleDraft` ausente o con hash inconsistente, correspondencia 1:1
    rota contra `context-manifest.json`, alguna plantilla de reparacion
    (`rule_repair_system.md`/`rule_repair_user.md`) ausente/invalida, un
    error del cliente LLM durante una reparacion (`LlmClientError`), o
    CUALQUIER candidato agoto `LLM_REPAIR_ATTEMPTS` sin alcanzar
    EVIDENCE_VALIDATED. Fatal para la etapa COMPLETA: GUARDRAILS_APPLIED
    es fail-fast (se detiene en el primer REJECTED definitivo, nunca
    procesa los candidatos restantes) y atomica (nunca promueve un
    `artifacts/09-guardrails/` parcial; la salida canonica anterior, si
    existia, se conserva intacta). El mensaje nunca incluye el body de
    la respuesta, el prompt efectivo completo, el ContextPackage, el
    RuleDraft completo ni credenciales.

    `diagnostics` (checkpoint correctivo: observabilidad de fallos de
    reparacion; opcional, `None` por defecto) expone un resumen
    sanitizado -- candidate_id, program, paragraph, cantidad de
    intentos, ultimo veredicto, violations (violation_id/rule/field/
    message), claims con sus evidence_ids/evidence_paths reales y los
    hashes de respuesta ya calculados -- del ULTIMO candidato que agoto
    `LLM_REPAIR_ATTEMPTS`. Nunca incluye prompts completos, respuestas
    crudas ni credenciales: el llamador (`guardrails_applied_stage.
    run_guardrails_applied_stage`) lo persiste en un artefacto NO
    contractual, fuera de `artifacts/09-guardrails/`, para que un fallo
    sea diagnosticable sin depender unicamente de este mensaje de texto."""

    def __init__(self, message: str, *, diagnostics: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class MarkdownRenderError(PipelineError):
    """Precondicion de la transicion GUARDRAILS_APPLIED -> COMPLETED
    incumplida (Prompt 13a), o fallo durante el renderizado/persistencia
    de `artifacts/10-rules/`: GUARDRAILS_APPLIED no completo (ausente,
    duplicada o no SUCCEEDED), `guardrail-manifest.json` ausente/invalido/
    con `source_package_hash` inconsistente, algun archivo declarado en
    `records` ausente/no es un archivo regular/es un symlink, un archivo
    o subdirectorio no referenciado presente en `artifacts/09-guardrails/`
    o `artifacts/10-rules/`, un hash de artefacto o de Markdown
    inconsistente, un `relative_filename` que no coincide con
    `sha256(candidate_id)+".md"`, o un fallo de escritura/intercambio/
    restauracion del directorio `10-rules/`. Fatal para la etapa
    completa: MarkdownRenderer nunca invoca al modelo, nunca reejecuta
    DeterministicGuardrail y nunca lee Neo4j ni ContextPackage. El
    mensaje nunca incluye el contenido completo de un RuleDraft/claims/
    evidence_paths, paths absolutos ni stacktrace.
    """


class SemanticCoverageError(PipelineError):
    """Fallo de `semantic_coverage_service.py` (Fase 1 de la ampliacion
    semantica, no-contractual, diagnostico bajo demanda): un artefacto
    V1 requerido para calcular `SemanticCoverageReport`
    (`artifacts/02-canonical/`, `artifacts/03-dependencies.json`,
    `artifacts/04-semantic-graph.json`, `artifacts/06-candidates.json`)
    esta ausente, no es JSON valido, o no valida contra su propio
    contrato Pydantic. `RunNotFoundError`/`StageNotReachedError`
    (`api/errors.py`, via `read_run_state`/`require_stage_succeeded`)
    cubren la ausencia del run o que no alcanzo CANDIDATES_DETECTED --
    esta excepcion es exclusivamente para los cuatro artefactos de
    entrada del analizador. El mensaje nunca incluye una ruta absoluta
    (solo el nombre relativo del artefacto afectado) ni el contenido del
    JSON invalido. Nunca se persiste un `diagnostics/semantic-coverage.
    json` parcial cuando esta excepcion se lanza."""


class SemanticEffectsError(PipelineError):
    """Fallo de `semantic_effects_service.py` (Fase 2 de la ampliacion
    semantica, no-contractual, diagnostico bajo demanda): el run no
    existe, `run.json` es invalido, el run no alcanzo PARSED (SUCCEEDED),
    o `artifacts/02-canonical/` esta ausente, vacio, no es JSON valido, o
    no valida contra `CanonicalProgram`. El mensaje nunca incluye una
    ruta absoluta (solo el nombre relativo del artefacto afectado) ni el
    contenido del JSON invalido. Nunca se persiste un
    `diagnostics/semantic-effects.json` parcial cuando esta excepcion se
    lanza."""


class SemanticPropagationError(PipelineError):
    """Fallo de `semantic_propagation_analyzer.py`/`semantic_propagation_
    service.py` (Fase 4 de la ampliacion semantica, no-contractual,
    diagnostico bajo demanda): el run no existe, `run.json` es invalido,
    el run no alcanzo PARSED (SUCCEEDED), `artifacts/02-canonical/` esta
    ausente/vacio/invalido, o los programas de `SemanticEffectsArtifact`
    (calculado en memoria) no coinciden con `CanonicalProgram[]`. El
    mensaje nunca incluye una ruta absoluta ni el contenido de un JSON
    invalido. Nunca se persiste un `diagnostics/semantic-propagation.json`
    parcial cuando esta excepcion se lanza."""


class V2ShadowCandidatesError(PipelineError):
    """Fallo de `v2_shadow_candidates_service.py`/`v2_shadow_detector.py`/
    `v2_detector_context.py` (Fase 5 de la ampliacion semantica,
    no-contractual, diagnostico bajo demanda, shadow mode): el run no
    existe, `run.json` es invalido, el run no alcanzo PARSED/
    SEMANTIC_GRAPH_BUILT/CANDIDATES_DETECTED (SUCCEEDED),
    `artifacts/02-canonical/`/`artifacts/04-semantic-graph.json`/
    `artifacts/06-candidates.json` estan ausentes/vacios/invalidos, o los
    artefactos cargados son inconsistentes entre si (p. ej. programas de
    `SemanticGraph` que no corresponden a ningun `CanonicalProgram`
    actual). El mensaje nunca incluye una ruta absoluta ni el contenido
    de un JSON invalido. Nunca se persiste un `diagnostics/
    v2-candidates-shadow.json` parcial cuando esta excepcion se lanza."""


class InterproceduralCallLinkageError(PipelineError):
    """Fallo de `interprocedural_call_linkage_service.py`/
    `interprocedural_call_linkage_analyzer.py` (Fase 6 de la ampliacion
    semantica, fundacion interprocedural CALL/LINKAGE, no-contractual,
    diagnostico bajo demanda): el run no existe, `run.json` es invalido,
    el run no alcanzo PARSED (SUCCEEDED), `artifacts/02-canonical/` esta
    ausente/vacio/invalido, o los `CanonicalProgram` cargados son
    inconsistentes entre si. El mensaje nunca incluye una ruta absoluta
    ni el contenido de un JSON invalido. Nunca se persiste un
    `diagnostics/interprocedural-call-linkage.json` parcial cuando esta
    excepcion se lanza."""


class InterproceduralPropagationError(PipelineError):
    """Fallo de `interprocedural_propagation_service.py`/
    `interprocedural_propagation_analyzer.py` (Fase 7 de la ampliacion
    semantica, propagacion interprocedural conservadora en shadow mode,
    no-contractual, diagnostico bajo demanda): el run no existe,
    `run.json` es invalido, el run no alcanzo PARSED (SUCCEEDED),
    `artifacts/02-canonical/` esta ausente/vacio/invalido, o los
    artefactos calculados en memoria (`SemanticEffectsArtifact`/
    `SemanticPropagationArtifact`/`InterproceduralCallLinkageArtifact`)
    son inconsistentes entre si. El mensaje nunca incluye una ruta
    absoluta ni el contenido de un JSON invalido. Nunca se persiste un
    `diagnostics/interprocedural-propagation.json` parcial cuando esta
    excepcion se lanza."""


class InterproceduralRuleCandidatesError(PipelineError):
    """Fallo de `interprocedural_rule_candidates_service.py`/
    `interprocedural_rule_detector.py` (Fase 8 de la ampliacion
    semantica, detectores de reglas interprocedurales en shadow mode,
    no-contractual, diagnostico bajo demanda): el run no existe,
    `run.json` es invalido, el run no alcanzo PARSED (SUCCEEDED),
    `artifacts/02-canonical/` esta ausente/vacio/invalido, o los
    artefactos calculados en memoria o cargados desde disco
    (`SemanticEffectsArtifact`/`SemanticPropagationArtifact`/
    `InterproceduralCallLinkageArtifact`/
    `InterproceduralPropagationArtifact`/`CandidateArtifact`/
    `V2ShadowCandidatesArtifact`/`SemanticEnrichmentArtifact`) son
    inconsistentes entre si. El mensaje nunca incluye una ruta absoluta
    ni el contenido de un JSON invalido. Nunca se persiste un
    `diagnostics/interprocedural-rule-candidates-shadow.json` parcial
    cuando esta excepcion se lanza."""


class CandidatePromotionAssessmentError(PipelineError):
    """Fallo de `candidate_promotion_assessment_service.py`/
    `candidate_promotion_assessment_analyzer.py` (Fase 9 de la
    ampliacion semantica, catalogo unificado de candidatos y evaluacion
    de promocion, no-contractual, diagnostico bajo demanda): el run no
    existe, `run.json` es invalido, el run no alcanzo PARSED
    (SUCCEEDED), o no se pudo determinar `source_package_hash` porque
    ninguna de las tres fuentes (`CandidateArtifact` V1,
    `V2ShadowCandidatesArtifact`, `InterproceduralRuleCandidatesArtifact`)
    estuvo disponible. La ausencia INDIVIDUAL de cualquiera de las tres
    fuentes NUNCA lanza esta excepcion (se marca
    `SourceAvailability.NOT_AVAILABLE`/`INVALID` y el analisis continua
    con las fuentes restantes). El mensaje nunca incluye una ruta
    absoluta ni el contenido de un JSON invalido. Nunca se persiste un
    `diagnostics/candidate-promotion-assessment.json` parcial cuando
    esta excepcion se lanza."""
