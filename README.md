# Altamira Rule Extractor

Extractor de reglas de negocio Altamira: procesa paquetes COBOL `.zip`,
construye un grafo semantico en Neo4j, detecta candidatos a regla,
arma paquetes contextuales de siete dimensiones y genera borradores de
regla trazables mediante un LLM (guardrail deterministico, revision
funcional obligatoria). Expone API JSON, UI HTML server-rendered
(Jinja2/HTMX) y un CLI Typer local. Ver `docs/ARCHITECTURE.md` para el
diseno completo y `docs/CLAUDE_CODE_RUNBOOK.md` para la secuencia de
implementacion por etapas.

## Arquitectura resumida

Exactamente dos servicios Docker:

- **`app`**: Python 3.12 (FastAPI + Typer + Jinja2/HTMX) y JRE 17 +
  `parser/target/altamira-cobol-parser.jar` (wrapper ProLeap sobre
  COBOL). Un unico proceso Uvicorn.
- **`neo4j`**: Neo4j 5 Community, el grafo semantico.

Sin agentes autonomos, sin colas externas (Redis/Celery/Kafka), sin
Postgres, sin Kubernetes. El pipeline persiste su estado y artefactos
en el filesystem (`data/`), no en una base adicional.

## Requisitos

- Docker Desktop (Windows/macOS) o Docker Engine + el plugin Compose
  (Linux) -- es la unica forma soportada de correr la aplicacion
  completa.
- Para desarrollo/tests fuera de Docker: Python 3.12 y Java 17 + Maven
  (ver "Comandos de calidad" mas abajo).

## Puesta en marcha con Docker Compose

### 1. Preparar `.env`

```bash
cp .env.example .env
```

Editar `.env` y reemplazar **obligatoriamente** `NEO4J_PASSWORD` (nunca
dejar el valor de ejemplo en un entorno real; la imagen oficial de
Neo4j exige minimo 8 caracteres). `.env` nunca se versiona ni entra al
contexto de build de Docker (ver `.gitignore`/`.dockerignore`).

`.env.example` distingue dos grupos de variables:

- **De Docker Compose** (`NEO4J_IMAGE`): solo afectan que imagen se usa
  para el servicio `neo4j`, Compose ya tiene un default seguro si se
  omite.
- **De la aplicacion** (`Settings`, `src/altamira_extractor/config.py`):
  `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD`/`NEO4J_DATABASE` (compartidas
  tambien con Compose para `NEO4J_AUTH`), mas los limites `ALTAMIRA_*` y
  la configuracion del proveedor LLM.

Las credenciales del proveedor LLM (`LLM_PROVIDER`, `OPENAI_*`/
`PWC_GENAI_*`) pueden quedar sin configurar: ninguna etapa determinista
del pipeline (`RECEIVED`..`CONTEXTS_BUILT`) las necesita, y `Settings()`
nunca las exige al construirse -- solo se validan al llegar a
`RULE_DRAFTS_GENERATED`. `LLM_TEMPERATURE` debe permanecer en `0`: es
la unica temperatura que el sistema acepta (determinismo del LLM,
`CLAUDE.md`), cualquier otro valor hace fallar `Settings()` a proposito.

### 2. Crear el directorio `data`

```bash
mkdir -p data
```

El servicio `app` monta `./data:/app/data` -- ahi vive todo el estado
del pipeline (`data/incoming/`, `data/runs/<run_id>/...`), nunca dentro
de la imagen.

**Permisos no-root en Linux**: el proceso dentro del contenedor corre
como el usuario dedicado `altamira` (UID/GID `10001`, nunca `root`). Un
bind mount del host sobre `/app/data` reemplaza los permisos que la
imagen ya establecio internamente por los del directorio del host: en
Linux, `./data` debe ser escribible por ese UID/GID, por ejemplo:

```bash
sudo chown -R 10001:10001 data
```

En Docker Desktop (Windows/macOS) esto normalmente no hace falta por
como cada backend traduce permisos de bind mounts.

### 3. Validar, construir y levantar

```bash
docker compose config
docker compose build
docker compose up -d
docker compose ps
```

`docker compose up -d` espera a que `neo4j` este `healthy`
(`depends_on: condition: service_healthy`) antes de arrancar `app`.
Ambos servicios deben quedar `healthy` en `docker compose ps`.

### 4. Acceder a la aplicacion

- UI: <http://localhost:8000/ui/runs>
- OpenAPI (Swagger UI): <http://localhost:8000/docs>
- Liveness: <http://localhost:8000/health> (`{"status": "ok"}`)
- Neo4j Browser (con el puerto publicado por `docker-compose.yml`):
  <http://localhost:7474>

### 5. Logs

```bash
docker compose logs -f app
docker compose logs -f neo4j
```

### 6. Apagar

```bash
docker compose down
```

Los datos persisten: `./data` (bind mount, en el host) y el volumen
nombrado `neo4j_data` (el grafo de Neo4j) sobreviven a `docker compose
down`. Para descartar tambien el grafo de Neo4j: `docker compose down
-v` (esto es destructivo -- solo si el contenido de `neo4j_data` es
realmente descartable).

## Limitaciones operativas V1 (deliberadas, documentadas)

- **Un unico worker Uvicorn**: `RunExecutor` (`api/executor.py`)
  coordina concurrencia de runs con un registro en memoria *por
  proceso*. Con mas de un worker cada proceso tendria su propio
  registro desincronizado del resto, rompiendo las garantias de "un
  solo run activo a la vez" y "capacidad acotada". El `Dockerfile` fija
  `--workers 1` explicitamente; no cambiar sin resolver antes esa
  limitacion (agregar mas infraestructura, p. ej. Redis, esta fuera de
  alcance V1).
- **Sin autenticacion**: ni la API JSON ni la UI tienen login o control
  de acceso en V1. Cualquiera con acceso de red al puerto 8000 puede
  subir paquetes y ver artefactos. **Restrinja el acceso de red
  externamente** (firewall, red interna/VPN, nunca exponer el puerto
  directamente a Internet) hasta que exista un mecanismo de
  autenticacion real.
- **Sin CORS**: la UI es same-origin (servida por el mismo `app`), no
  se agrega `CORSMiddleware`.

## Comandos de calidad (desarrollo local, sin Docker)

Requieren Python 3.12 y JDK 17 + Maven disponibles en el entorno:

```bash
python -m ruff check .
python -m mypy src
pytest -q -m "not integration"
mvn -q -f parser/pom.xml test
```

Equivalentes con `make`: `make lint`, `make typecheck`, `make test`,
`make parser-test`.

### Validacion en contenedores efimeros (sin instalar nada en el host)

```bash
docker run --rm --mount type=bind,source="$(pwd)",target=/workspace -w /workspace \
  python:3.12-slim sh -lc "pip install -e '.[dev]' && python -m ruff check . && python -m mypy src && pytest -q -m 'not integration'"

docker run --rm --mount type=bind,source="$(pwd)",target=/workspace -w /workspace \
  maven:3.9-eclipse-temurin-17 mvn -q -f parser/pom.xml test
```

Los tests marcados `integration` requieren Neo4j real y Java 17 +
el JAR compilado disponibles en el entorno que ejecuta `pytest` (no
necesariamente dentro del contenedor `app`) -- ver `.claude/rules/testing.md`.

## Estructura

```text
src/altamira_extractor/   Paquete Python: config, pipeline, api/ (FastAPI), ui/ (Jinja2/HTMX), cli.py (Typer)
tests/                     Tests (pytest): unitarios + integracion (marcados `integration`)
parser/                    Modulo Maven Java 17 (wrapper ProLeap: produce parser/target/altamira-cobol-parser.jar)
queries/v1/                Consultas Cypher versionadas (Q0-Q7, invariantes)
schemas/                   JSON Schema / XSD de los contratos versionados
prompts/                   Prompts del LLM (rule writer / rule repair)
config/                    Catalogos de configuracion (semantic-tags, domain-glossary)
data/                      Runtime local: incoming/ y runs/ (vacio en el repo, con .gitkeep; montado en /app/data)
Dockerfile                 Multi-stage: parser-build (Maven/JDK 17) -> python-build (wheel) -> runtime (Python 3.12 + JRE 17)
docker-compose.yml         Exactamente los servicios app y neo4j
```

## Configuracion

`src/altamira_extractor/config.py` expone `Settings` (Pydantic
Settings), poblada desde variables de entorno con prefijo
`ALTAMIRA_` (mas un pequeno grupo sin prefijo ya establecido:
`NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD`/`NEO4J_DATABASE`,
`LLM_PROVIDER`, `OPENAI_*`, `PWC_GENAI_*`) y, opcionalmente, un archivo
`.env` (no versionado, no incluido en este repo salvo `.env.example`).
Ninguna clave o secreto se hardcodea ni se loguea: `logging_setup.py`
redacta automaticamente campos con nombres sensibles (`password`,
`api_key`, `token`, `secret`, `authorization`).

## Notas

- Ver `README_START_HERE.md` para el contexto de diseno original del
  paquete y `docs/CLAUDE_CODE_RUNBOOK.md` para la disciplina de
  ejecucion etapa por etapa.
- El E2E contenedorizado (subir un ZIP real contra el `app` empaquetado
  y verificar el recorrido completo hasta la descarga) es un checkpoint
  posterior (Prompt 14b) -- no implementado todavia.
