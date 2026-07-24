"""Excepciones de dominio del pipeline RECEIVED..INVENTORIED.

Cada una representa un motivo de fallo explicito (python.md: "Excepciones
de dominio explicitas"). Nunca se atrapan de forma generica: el llamador
decide como traducirlas a StageExecution.error.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base de todas las excepciones de dominio del pipeline de ingesta."""


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
