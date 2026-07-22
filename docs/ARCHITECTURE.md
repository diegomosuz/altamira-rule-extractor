# Arquitectura revisada

## 1. Vista general

```text
Usuario
  |
  +-- UI HTML/HTMX
  +-- REST API
  +-- CLI Typer
          |
          v
   ApplicationService
          |
          v
     PipelineRunner
          |
          +-- PackageValidator
          +-- SafeExtractor
          +-- ManifestLoader
          +-- InventoryBuilder
          +-- ProLeapParserClient
          +-- DependencyBuilder
          +-- ParameterLoader
          +-- SemanticTagger
          +-- DomainTermMapper
          +-- SemanticGraphBuilder
          +-- Neo4jRepository
          +-- GraphInvariantValidator
          +-- CandidateDetector
          +-- ContextPackageBuilder
          +-- LlmRuleWriter
          +-- DeterministicGuardrail
          +-- MarkdownRenderer
          |
          +-- FilesystemArtifactStore
```

Docker:

```text
+------------------------------------------------+
| app                                            |
| Python 3.12                                    |
| FastAPI + Typer + Jinja2 + HTMX                |
| Pipeline                                       |
| JRE 17 + parser-cli.jar                        |
+------------------------+-----------------------+
                         |
                         | Bolt
                         v
+------------------------------------------------+
| neo4j Community                                |
+------------------------------------------------+
```

## 2. Por qué no es un multiagente

No existen decisiones autónomas de planificación ni negociación entre agentes. Cada paso tiene input y output definidos.

El LLM solo realiza una transformación lingüística:

```text
ContextPackage -> RuleDraft
```

Todo lo demás es ingeniería determinística.

## 3. Componentes

### 3.1 ApplicationService

Interfaz común para API, UI y CLI.

Casos de uso:

- crear ejecución;
- iniciar o reanudar;
- consultar estado;
- listar candidatos;
- obtener contexto;
- obtener borrador;
- descargar resultados.

### 3.2 PipelineRunner

Máquina de estados persistida en `run.json`.

La ejecución por API se realiza en un executor local de concurrencia limitada. No se agrega una cola externa. Ante reinicio, la ejecución puede reanudarse manualmente desde la última etapa válida.

### 3.3 PackageValidator y SafeExtractor

- SHA-256.
- protección Zip Slip;
- rechazo de symlinks;
- límites configurables;
- whitelist;
- estructura mínima;
- extracción segura;
- encoding detectado y registrado.

### 3.4 ManifestLoader

Valida `manifest.xml` y obtiene la jerarquía C3 mínima suministrada por el paquete.

No infiere silenciosamente país, operativa o versión desde el nombre del ZIP cuando faltan en el manifest.

### 3.5 ProLeapParserClient

Invoca un JAR local:

```text
java -jar parser-cli.jar parse-package ...
```

El parser produce una representación canónica, no el grafo final.

### 3.6 CanonicalProgram

Contiene:

- identificación del programa;
- DataItems;
- Paragraphs;
- statements relevantes;
- source spans;
- lecturas y escrituras de variables;
- branches y transferencias;
- EXEC SQL;
- warnings;
- unsupported constructs.

### 3.7 DependencyBuilder

Construye un CPG reducido a nivel párrafo:

- `DATA_DEPENDS_ON`: un párrafo produce datos consumidos por otro.
- `CONTROL_DEPENDS_ON`: un párrafo condiciona o dirige la ejecución de otro.

No intenta representar cada token o statement como nodo Neo4j.

### 3.8 ParameterLoader

Procesa DDL y CSV.

Produce:

- Table;
- ParameterTable;
- ParameterEntry;
- snapshot_date;
- metadatos de columnas;
- normalización conservando valor original.

### 3.9 SemanticTagger

Vocabulario controlado y determinístico.

Cada resultado contiene:

- tag;
- confidence;
- evidence;
- catalog_version.

### 3.10 DomainTermMapper

Materializa D7:

- crea DomainTerm;
- vincula DataItem mediante HAS_DOMAIN_TERM;
- conserva fuente, definición, versión y confianza.

En V1 la fuente puede ser un catálogo YAML curado. En versiones futuras puede provenir del repositorio Altamira o de un glosario corporativo.

### 3.11 SemanticGraphBuilder

Traduce los artefactos canónicos al metamodelo documentado.

No agrega nodos de conveniencia ajenos al contrato.

### 3.12 Neo4jRepository

- constraints;
- índices;
- MERGE parametrizado;
- carga por lotes;
- idempotencia;
- consultas versionadas;
- limpieza por `source_package_hash`.

### 3.13 GraphInvariantValidator

Ejecuta invariantes después de la carga.

Ejemplos:

- Paragraph sin source_text;
- Decision sin LEADS_TO;
- relación a nodo inexistente;
- DataItem con semantic_tag fuera del catálogo;
- ParameterEntry sin ParameterTable;
- Program sin jerarquía;
- entidades técnicas sin source_package_hash;
- dependencia entre versiones incompatibles.

### 3.14 CandidateDetector

Registro de detectores, con uno activo en V1:

```text
ReturnCodeDecisionDetector
```

Patrón:

```text
Paragraph
 -[:HAS_DECISION]->
Decision
 -[:LEADS_TO]->
DataItem {semantic_tag: return_code}
```

Q0 propone candidatos. No confirma reglas.

### 3.15 ContextPackageBuilder

Construye D1-D7 y calcula completitud.

Debe resolver:

- code slice;
- acceso a tablas;
- aplicabilidad paramétrica;
- condición;
- efectos;
- batch;
- términos funcionales;
- evidencia.

### 3.16 LlmRuleWriter

Cliente OpenAI-compatible para:

- OpenAI personal;
- gateway PwC.

Una sola implementación HTTP con dos perfiles de configuración.

### 3.17 DeterministicGuardrail

Valida:

- schema;
- evidence paths;
- evidence IDs;
- números;
- fechas;
- tablas;
- códigos;
- términos;
- parámetros aprobados;
- fuerza de atribución de efectos;
- ausencia de batch inventado;
- instrucciones inyectadas desde source_text.

### 3.18 MarkdownRenderer

Convierte RuleDraft validado a una plantilla estable.

El renderer no agrega información.

## 4. Almacenamiento local

```text
data/
  incoming/
  runs/
    <run_id>/
      run.json
      input/package.zip
      work/extracted/
      artifacts/
        01-inventory.json
        02-canonical/
        03-dependencies.json
        04-semantic-graph.json
        05-invariants.json
        06-candidates.json
        07-context/
        08-rule-drafts/
        09-guardrails/
        10-rules/
      logs/pipeline.jsonl
```

## 5. API mínima

- `POST /api/runs`
- `GET /api/runs`
- `GET /api/runs/{run_id}`
- `POST /api/runs/{run_id}/resume`
- `GET /api/runs/{run_id}/candidates`
- `GET /api/runs/{run_id}/candidates/{candidate_id}/context`
- `GET /api/runs/{run_id}/candidates/{candidate_id}/rule`
- `GET /api/runs/{run_id}/download`
- `GET /health`

## 6. Principios

- minimalidad suficiente;
- separación estructura/semántica;
- etiquetado controlado;
- identificación estable y versionada;
- multi-país;
- temporalidad explícita;
- determinismo primero;
- trazabilidad por construcción;
- fallo visible;
- extensibilidad solo donde existe variabilidad real.
