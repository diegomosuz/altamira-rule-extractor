# Altamira Rule Extractor — bootstrap operativo

Estado actual: **bootstrap tecnico** (Prompt 1 del runbook). No hay
todavia pipeline, parser COBOL, Neo4j, API ni UI implementados — ver
`docs/CLAUDE_CODE_RUNBOOK.md` para la secuencia completa de etapas y
`docs/ARCHITECTURE.md` para el diseno objetivo.

## Requisitos

- Python 3.12 (`requires-python` en `pyproject.toml`).
- Java 17 + Maven (para el modulo `parser/`).
- Docker Desktop (para validar en contenedores efimeros y, mas
  adelante, para levantar `app` + `neo4j`).

## Estructura

```text
src/altamira_extractor/   Paquete Python (config, logging; el pipeline se agrega en etapas siguientes)
tests/                     Tests unitarios (pytest)
parser/                    Modulo Maven Java 17 (wrapper ProLeap; sin logica COBOL todavia)
queries/v1/                Consultas Cypher de referencia (Q0-Q7, invariantes)
schemas/                   JSON Schema / XSD de los contratos versionados
prompts/                   Prompts del LLM (rule writer / rule repair)
config/                    Catalogos de configuracion (semantic-tags, domain-glossary)
data/                      Runtime local: incoming/ y runs/ (vacio en el repo, con .gitkeep)
```

## Comandos de calidad

Requieren Python 3.12 y JDK 17 + Maven disponibles en el entorno que
ejecuta los comandos (venv local con esas versiones, o un contenedor
que las provea):

```bash
python -m ruff check .
python -m mypy src
pytest -q
mvn -q -f parser/pom.xml test
```

Equivalentes con `make`: `make lint`, `make typecheck`, `make test`,
`make parser-test`.

### Validacion en contenedores efimeros (sin instalar nada en el host)

Si el host no tiene Python 3.12 o JDK 17/Maven instalados, se pueden
correr los mismos checks en contenedores oficiales desechables, sin
modificar el sistema:

```bash
docker run --rm --mount type=bind,source="$(pwd)",target=/workspace -w /workspace \
  python:3.12-slim sh -lc "pip install -e '.[dev]' && python -m ruff check . && python -m mypy src && pytest -q"

docker run --rm --mount type=bind,source="$(pwd)",target=/workspace -w /workspace \
  maven:3.9-eclipse-temurin-17 mvn -q -f parser/pom.xml test
```

## Configuracion

`src/altamira_extractor/config.py` expone `Settings` (Pydantic
Settings), poblada desde variables de entorno con prefijo
`ALTAMIRA_` y, opcionalmente, un archivo `.env` (no versionado, no
incluido en este repo). Ninguna clave o secreto se hardcodea ni se
loguea: `logging_setup.py` redacta automaticamente campos con nombres
sensibles (`password`, `api_key`, `token`, `secret`,
`authorization`).

## Notas

- `docker-compose.yml` (con los servicios `app` y `neo4j`) se agrega
  en una etapa posterior (Prompt 14); `docker-compose.blueprint.yml`
  es la referencia de diseno, no un archivo activo todavia.
- Ver `README_START_HERE.md` para el contexto de diseno original del
  paquete y `docs/CLAUDE_CODE_RUNBOOK.md` para la disciplina de
  ejecucion etapa por etapa.
