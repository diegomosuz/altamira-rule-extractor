# Targets de calidad (ver CLAUDE.md, seccion "Calidad").
# Asumen un entorno con Python 3.12 y JDK 17 + Maven ya disponibles
# (venv local con esas versiones, o el contenedor/CI que las provea).
.PHONY: lint typecheck test parser-test compose-check \
	docker-build docker-up docker-down docker-logs docker-smoke

lint:
	python -m ruff check .

typecheck:
	python -m mypy src

test:
	pytest -q

parser-test:
	mvn -q -f parser/pom.xml test

compose-check:
	docker compose config

# Envoltorios minimos de Docker Compose (Prompt 14a). El E2E
# contenedorizado (docker-e2e) es Prompt 14b, no implementado todavia.
docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f app

docker-smoke:
	python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5); print('OK')"
