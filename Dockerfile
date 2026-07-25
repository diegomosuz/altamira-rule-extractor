# syntax=docker/dockerfile:1
#
# Multi-stage: parser-build (Maven/JDK 17, produce el JAR shaded del
# parser COBOL) -> python-build (construye un wheel instalable) ->
# runtime (Python 3.12 + JRE 17 headless, sin herramientas de build).
#
# Resolucion de rutas: config.py (`_discover_repo_root`) localiza
# schemas/queries/prompts/config/parser caminando hacia arriba desde su
# propio archivo hasta encontrar pyproject.toml -- eso rompe con una
# instalacion normal en site-packages (el archivo instalado nunca tiene
# pyproject.toml como ancestro). En vez de modificar config.py (fuera de
# alcance de este prompt) o inventar una convencion de variables de
# entorno nueva, el stage runtime instala el wheel dos veces: una normal
# (site-packages, resuelve TODAS las dependencias runtime) y otra con
# `--no-deps --target=/app` (coloca altamira_extractor directamente bajo
# /app). `PYTHONPATH=/app` antepone esa segunda copia en sys.path, y
# `/app/pyproject.toml` (copiado, sin secretos) hace que
# `_discover_repo_root()` encuentre /app como raiz -- exactamente la
# misma convencion relativa que ya usa Settings, sin variables nuevas.

########################################
# Stage: parser-build
########################################
FROM maven:3.9-eclipse-temurin-17 AS parser-build

WORKDIR /build

# Solo pom.xml y fuentes: nunca el arbol completo del repo.
COPY parser/pom.xml parser/pom.xml
COPY parser/src parser/src

RUN --mount=type=cache,target=/root/.m2 \
    mvn -q -f parser/pom.xml package

########################################
# Stage: python-build
########################################
FROM python:3.12-slim-bookworm AS python-build

WORKDIR /src

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip wheel \
    && pip wheel --no-deps --wheel-dir /dist .

########################################
# Stage: runtime
########################################
FROM python:3.12-slim-bookworm AS runtime

# JRE 17 headless (no el JDK completo: no se compila nada en runtime).
# Fijado a slim-bookworm deliberadamente: variantes "slim" mas recientes
# (trixie) solo ofrecen paquetes openjdk-21+ via apt, no 17.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 altamira \
    && useradd --uid 10001 --gid altamira --no-create-home --shell /usr/sbin/nologin altamira

WORKDIR /app

ENV PYTHONPATH=/app \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 1) Instalacion normal: resuelve e instala TODAS las dependencias
#    runtime declaradas en pyproject.toml (fastapi, uvicorn, jinja2,
#    neo4j, httpx, pydantic, etc.) en site-packages.
# 2) Reinstalacion --no-deps --target=/app: coloca altamira_extractor
#    directamente bajo /app (ver docstring del archivo). No reinstala
#    dependencias de nuevo (--no-deps), solo el propio paquete.
COPY --from=python-build /dist/*.whl /tmp/dist/
RUN pip install /tmp/dist/*.whl \
    && pip install --no-deps --target=/app /tmp/dist/*.whl \
    && rm -rf /tmp/dist

# Recursos externos que Settings resuelve relativos a la raiz del
# "repositorio" (via _discover_repo_root): confirmados por inspeccion,
# ninguno mas se referencia desde config.py.
COPY pyproject.toml ./pyproject.toml
COPY schemas ./schemas
COPY queries ./queries
COPY prompts ./prompts
COPY config ./config
COPY --from=parser-build /build/parser/target/altamira-cobol-parser.jar ./parser/target/altamira-cobol-parser.jar

# Smoke de build (no se repite en cada arranque del contenedor):
# confirma JRE operativo y que el JAR shaded ejecuta su Main-Class real.
# El parser no expone --help; con cero argumentos su propio
# ArgumentParser responde de forma controlada con exit code 2
# (CliExitCode.INVALID_ARGUMENTS) y el mensaje "comando requerido:
# parse" -- si el shading/manifest estuviera roto, fallaria antes con
# NoClassDefFoundError o "no main manifest attribute" (exit distinto).
RUN test -f parser/target/altamira-cobol-parser.jar \
    && java -version \
    && set +e; \
    output="$(java -jar parser/target/altamira-cobol-parser.jar 2>&1)"; \
    code=$?; \
    set -e; \
    if [ "$code" -ne 2 ] || ! printf '%s' "$output" | grep -q "comando requerido: parse"; then \
        echo "smoke del JAR fallo (exit=$code): $output"; \
        exit 1; \
    fi

# Directorios de datos en runtime (ver docs/ARCHITECTURE.md "4.
# Almacenamiento local"): creados con dueno correcto para el caso de
# ejecutar la imagen sin bind mount. Un bind mount del host sobre
# /app/data (como hace docker-compose.yml) REEMPLAZA estos permisos por
# los del host -- en Linux, ese directorio debe ser escribible por el
# UID/GID 10001 (ver README.md).
RUN mkdir -p /app/data/incoming /app/data/runs \
    && chown -R altamira:altamira /app/data

USER altamira

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=10 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"]

# Un unico worker: RunExecutor coordina concurrencia solo dentro de un
# proceso (ver docstring de api/app.py::create_app/app_factory).
CMD ["uvicorn", "altamira_extractor.api.app:app_factory", "--factory", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
