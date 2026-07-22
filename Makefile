# Targets de calidad del bootstrap (ver CLAUDE.md, seccion "Calidad").
# Asumen un entorno con Python 3.12 y JDK 17 + Maven ya disponibles
# (venv local con esas versiones, o el contenedor/CI que las provea).
.PHONY: lint typecheck test parser-test compose-check

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
