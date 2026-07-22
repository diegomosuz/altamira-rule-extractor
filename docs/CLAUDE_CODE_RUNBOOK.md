# Runbook exacto para Claude Code

Ejecute un prompt por vez. Revise diff y tests. No permita que Claude Code continúe automáticamente.

## Prompt 0 - análisis

```text
Lee CLAUDE.md, todos los documentos de docs/, schemas/, prompts/, config/ y queries/.

No escribas código.

Entrega:
1. arquitectura entendida;
2. metamodelo exacto;
3. separación entre representación canónica y grafo semántico;
4. plan incremental;
5. riesgos;
6. criterios de aceptación.

Confirma expresamente que:
- habrá solo dos servicios;
- no habrá agentes autónomos;
- DomainTerm será nodo;
- ParameterTable tendrá labels Table y ParameterTable;
- DATA_DEPENDS_ON y CONTROL_DEPENDS_ON serán relaciones Paragraph->Paragraph;
- habrá validación de invariantes.
```

## Prompt 1 - bootstrap

```text
Implementa solo el bootstrap.

Crea pyproject.toml Python 3.12, src/, tests/, parser/ Maven Java 17, Makefile, README, logging JSON, configuración Pydantic y estructura de artefactos.

No implementes pipeline, parser ni Neo4j.

Ejecuta ruff, mypy, pytest y mvn test.
```

## Prompt 2 - contratos

```text
Implementa modelos Pydantic y validadores para:
Manifest, Inventory, RunState, CanonicalProgram, ParagraphDependency,
SemanticGraph, RuleCandidate, ContextPackage, RuleDraft y GuardrailReport.

Respeta schemas/. Agrega serialización estable y tests positivos/negativos.
No implementes lógica.
```

## Prompt 3 - ZIP, manifest e inventario

```text
Implementa RECEIVED, VALIDATED, EXTRACTED e INVENTORIED.

Incluye SHA-256, run_id, Zip Slip, symlinks, bomba ZIP, límites, whitelist,
extracción segura, encoding e XSD.

CLI:
python -m altamira_extractor.cli ingest <zip>

Agrega fixtures maliciosos.
```

## Prompt 4 - parser Java

```text
Implementa parser-cli.jar con ProLeap.

Debe producir CanonicalProgram con:
PROGRAM-ID, DataItems, Paragraphs, source spans, IF, EVALUATE, MOVE, SET,
COMPUTE, GO TO, PERFORM, EXEC SQL, lecturas/escrituras de variables,
branches, warnings y unsupported_constructs.

Usa COPY y REPLACE del preprocesador.
No construyas Neo4j.
```

## Prompt 5 - integración parser

```text
Integra el JAR desde Python con subprocess sin shell, timeout y validación.
Procesa todos los .cbl y persiste artifacts/02-canonical/.
Implementa PARSED, reanudación e idempotencia.
```

## Prompt 6 - dependencias CPG reducidas

```text
Implementa DependencyBuilder a nivel Paragraph.

Deriva:
- DATA_DEPENDS_ON: origen que define/escribe datos consumidos por destino.
- CONTROL_DEPENDS_ON: origen que condiciona, performa o transfiere control al destino.

Persiste artifacts/03-dependencies.json.
Incluye evidence, variables, control_construct y confidence.
No crees nodos statement en Neo4j.
```

## Prompt 7 - DDL, CSV, semantic tags y DomainTerm

```text
Implementa ParameterLoader, SemanticTagger y DomainTermMapper.

- DDL: subconjunto CREATE TABLE.
- CSV: raw y normalizado.
- semantic_tag desde config/semantic-tags.yml.
- DomainTerm desde config/domain-glossary.example.yml.
- HAS_DOMAIN_TERM se construirá después.

No uses LLM.
```

## Prompt 8 - grafo semántico

```text
Implementa SemanticGraphBuilder siguiendo exactamente docs/NEO4J_METAMODEL.md.

Reglas:
- no SqlStatement node;
- SQL -> relaciones directas Paragraph->Table;
- ParameterTable = Table + ParameterTable;
- incluir DomainTerm;
- incluir dependencias Paragraph->Paragraph;
- IDs versionados;
- source_package_hash.

Persiste artifacts/04-semantic-graph.json.
```

## Prompt 9 - Neo4j e invariantes

```text
Implementa Neo4jRepository.

- constraints;
- índices;
- MERGE parametrizado;
- carga idempotente;
- limpieza por source_package_hash;
- queries/v1/invariants.cypher;
- artifacts/05-invariants.json.

GRAPH_VALIDATED solo si no hay ERROR.
Agrega integración con Neo4j.
```

## Prompt 10 - Q0 y Q1-Q7

```text
Implementa las queries de queries/v1/.

- Q0 devuelve candidatos, no reglas confirmadas.
- Q2 usa dependencias hasta profundidad configurable.
- Q3 separa aplicabilidad EXACT/PARTIAL/UNRESOLVED.
- Q5 clasifica DIRECT/DEPENDENCY_SLICE/PROGRAM_CONTEXT.
- Q6 admite resultado vacío.
- Q7 requiere DomainTerm.

Construye ContextPackage y valida schema.
```

## Prompt 11 - proveedor LLM dual

```text
Implementa OpenAICompatibleChatClient con httpx.AsyncClient.

Soporta openai y pwc_gateway mediante configuración.
Incluye timeout, retry 429/5xx, temperatura 0, headers seguros y parsing JSON.
No uses LiteLLM ni SDK específico.
Tests con MockTransport.
```

## Prompt 12 - prompts y guardrail

```text
Integra prompts/.

El LLM devuelve RuleDraft JSON.
Implementa guardrail determinístico para:
schema, evidence_paths, evidence_ids, números, fechas, tablas, códigos,
filas approved_for_rule_text, efectos approved_for_rule_text, batch vacío,
identificadores desconocidos y prompt injection.

Máximo dos reparaciones.
Resultado exitoso: EVIDENCE_VALIDATED + NEEDS_FUNCTIONAL_REVIEW.
```

## Prompt 13 - renderer, API y UI

```text
Implementa MarkdownRenderer, FastAPI, Typer y UI Jinja2/HTMX.

Pantallas:
upload, runs, estado, candidatos, contexto, regla, guardrail y descarga.

No React ni Node.
Escapa contenido fuente.
```

## Prompt 14 - Docker y E2E

```text
Implementa Dockerfile multi-stage y docker-compose.yml.

Exactamente app y neo4j.
Compila parser con Maven y copia JAR.
Python 3.12 + JRE 17.
Volúmenes, healthchecks y usuario no root cuando sea viable.

Crea E2E sin internet:
ZIP -> grafo -> invariantes -> candidato -> contexto -> LLM fake -> guardrail -> Markdown.
```

## Prompt 15 - complejidad

```text
Implementa la matriz de fixtures de docs/SUPPORTED_COMPLEXITY_STRATEGY.md.

Cada caso debe quedar como soportado, parcial o no soportado.
No declares soporte universal.
No amplíes arquitectura.
```

## Prompt final - auditoría

```text
Audita sin agregar funcionalidades:

- cumplimiento del metamodelo;
- solo dos servicios;
- no nodos o relaciones extra;
- DomainTerm;
- ParameterTable dual label;
- dependencias;
- invariantes;
- idempotencia;
- versionado;
- parametría;
- atribución de efectos;
- proveedor dual;
- guardrail;
- estados de revisión;
- ausencia de secretos;
- E2E.

Ejecuta toda la suite y corrige defectos.
Entrega evidencia concreta.
```
