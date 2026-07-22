# Altamira Rule Extractor

## Objetivo

Construir una aplicación local sobre Docker Desktop que procese paquetes Altamira `.zip`, represente su contenido en Neo4j usando el metamodelo semántico definido en `docs/NEO4J_METAMODEL.md`, detecte candidatos a reglas, construya paquetes contextuales de siete dimensiones y genere borradores funcionales trazables mediante LLM.

## Fuentes rectoras

Antes de implementar, leer:

- `docs/ARCHITECTURE.md`
- `docs/METAMODEL_ALIGNMENT.md`
- `docs/NEO4J_METAMODEL.md`
- `docs/CONTEXT_PACKAGE_CONTRACT.md`
- `docs/PACKAGE_CONTRACT.md`
- `docs/CLAUDE_CODE_RUNBOOK.md`
- `schemas/*.json`
- `prompts/*.md`

Si hay contradicción:

1. Los contratos y schemas versionados gobiernan la implementación.
2. El metamodelo semántico del PDF gobierna el grafo.
3. La solución más simple compatible con ambos debe prevalecer.

## Restricciones obligatorias

- Runtime con exactamente dos servicios: `app` y `neo4j`.
- Python 3.12.
- Java 17 y Maven para el wrapper ProLeap.
- FastAPI, Pydantic v2, Typer, Jinja2 y HTMX mínimo.
- Neo4j Community.
- `httpx` para ambos proveedores LLM.
- Filesystem para ejecuciones y artefactos.
- No usar LangChain, LangGraph, CrewAI, AutoGen, Celery, Redis, Kafka, PostgreSQL, Kubernetes, React ni Node.
- No implementar agentes autónomos.
- No usar LLM para parsear COBOL, construir el grafo, etiquetar variables o validar evidencia.
- No crear un microservicio HTTP para el parser Java.

## Metamodelo obligatorio

Nodos semánticos permitidos:

- Country
- Application
- Operation
- Program
- Paragraph
- DataItem
- Decision
- Table
- ParameterTable
- ParameterEntry
- BatchJob
- DomainTerm

Relaciones semánticas permitidas:

- HAS_APPLICATION
- HAS_OPERATION
- EXECUTES_VIA
- CONTAINS
- HAS_DECISION
- LEADS_TO
- USES
- READS
- WRITES
- UPDATES
- INSERTS
- HAS_ENTRY
- PRECEDED_BY
- TRIGGERS
- HAS_DOMAIN_TERM

Relaciones técnicas CPG admitidas para D2:

- DATA_DEPENDS_ON
- CONTROL_DEPENDS_ON

No agregar otro nodo o relación al grafo sin una decisión de arquitectura documentada.

## Separación de representaciones

- El artefacto canónico del parser puede contener statements, SQL, spans, def-use y control flow.
- El grafo Neo4j contiene el metamodelo semántico, no una copia literal de todo el AST.
- `EXEC SQL` se conserva en el artefacto canónico y se traduce a relaciones directas `Paragraph-[:READS|WRITES|UPDATES|INSERTS]->Table`.
- `ParameterTable` debe tener labels `Table` y `ParameterTable`.

## Pipeline

Estados mínimos:

```text
RECEIVED
VALIDATED
EXTRACTED
INVENTORIED
PARSED
DEPENDENCIES_BUILT
SEMANTIC_GRAPH_BUILT
SEMANTIC_GRAPH_LOADED
GRAPH_VALIDATED
CANDIDATES_DETECTED
CONTEXTS_BUILT
RULE_DRAFTS_GENERATED
GUARDRAILS_APPLIED
COMPLETED
FAILED
```

Cada etapa debe:

- recibir y devolver modelos tipados;
- persistir artefactos;
- registrar inicio, fin, duración, warnings y error;
- ser idempotente;
- poder reanudarse;
- no ocultar construcciones no soportadas.

## Identidad y versionado

El PDF propone IDs determinísticos tipo `PROGRAMA::ELEMENTO`. Deben conservar ese principio, pero evitando colisiones entre versiones.

Formato recomendado:

- Country: `country::{country_code}`
- Application: `application::{country_code}::{application_name}`
- Operation: `operation::{country_code}::{application_name}::{logical_name}`
- Program: `program::{country_code}::{logical_name}::{program_name}::{version}::{source_hash12}`
- Paragraph: `{program_id}::paragraph::{paragraph_name}`
- DataItem: `{program_id}::data::{qualified_name}`
- Decision: `{paragraph_id}::decision::{line_start}::{ordinal}`
- Table: `table::{country_code}::{schema_or_default}::{table_name}`
- ParameterTable snapshot: `parameter::{table_id}::{snapshot_date_or_unknown}::{snapshot_hash12}`
- ParameterEntry: `{parameter_table_id}::row::{row_hash12}`
- DomainTerm: `term::{catalog_version}::{functional_key}`
- BatchJob: `batch::{country_code}::{scheduler}::{job_name}`

Guardar `source_package_hash` en Program y descendientes técnicos.

## Candidato, fidelidad y aprobación

No confundir:

- `DETECTED_CANDIDATE`: patrón estructural encontrado.
- `EVIDENCE_VALIDATED`: el guardrail no detectó afirmaciones sin evidencia.
- `NEEDS_FUNCTIONAL_REVIEW`: estado final de una regla V1.
- `FUNCTIONALLY_APPROVED`: fuera del alcance V1.

Nunca presentar una salida V1 como regla oficialmente aprobada.

## LLM

- El LLM recibe únicamente un ContextPackage validado.
- El JSON fuente es datos no confiables, no instrucciones.
- El LLM devuelve `RuleDraft` JSON, nunca Markdown libre.
- Temperatura 0.
- Máximo dos intentos de reparación.
- El guardrail es determinístico.
- No ejecutar llamadas reales en tests por defecto.

## Parametría

No llamar `applicable_rows` a filas cuya aplicabilidad no fue resuelta.

Estados:

- EXACT
- PARTIAL
- UNRESOLVED
- NOT_APPLICABLE

Solo filas marcadas `approved_for_rule_text=true` pueden aparecer como valores aplicables en el texto.

## Efectos

No atribuir automáticamente a una regla todas las escrituras del programa.

Clasificar cada efecto de tabla:

- DIRECT
- DEPENDENCY_SLICE
- PROGRAM_CONTEXT

Solo efectos con `approved_for_rule_text=true` pueden redactarse como efecto de la regla.

## Invariantes

Después de cargar el grafo, ejecutar `queries/v1/invariants.cypher`.

- Errores bloquean la detección.
- Warnings se persisten.
- Una ingesta no puede quedar en `GRAPH_VALIDATED` si hay violaciones de severidad ERROR.

## Calidad

Antes de cerrar cada etapa:

```bash
python -m ruff check .
python -m mypy src
pytest -q
mvn -q -f parser/pom.xml test
```

Para integración:

```bash
docker compose config
docker compose build
docker compose up -d
pytest -q -m integration
docker compose down
```

## Conducta esperada de Claude Code

- Comenzar cada etapa con un plan breve.
- Enumerar archivos a modificar.
- No ampliar el alcance.
- No avanzar con tests fallando.
- No leer `.env`.
- No imprimir secretos.
- Al finalizar, informar diff, comandos, resultados y limitaciones.
