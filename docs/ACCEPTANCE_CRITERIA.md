# Criterios de aceptación V1

## Ingesta

- ZIP válido procesado.
- ZIP inseguro rechazado.
- Manifest validado.
- Inventario persistido.
- Encodings registrados.

## Código

- Program, Paragraph, DataItem y Decision extraídos.
- COPY/REPLACE cubiertos por fixtures.
- EXEC SQL produce relaciones directas a Table.
- Dependencias DATA_DEPENDS_ON y CONTROL_DEPENDS_ON disponibles.

## Grafo

- Solo nodos y relaciones permitidos.
- ParameterTable con labels Table y ParameterTable.
- DomainTerm explícito.
- IDs versionados.
- Carga idempotente.
- Invariantes sin errores.

## Extracción

- Q0 detecta el fixture de referencia.
- Q1-Q7 producen un ContextPackage válido.
- D6 queda NOT_AVAILABLE en V1.
- Aplicabilidad paramétrica explícita.
- Efectos clasificados por atribución.

## LLM

- Perfil OpenAI.
- Perfil gateway PwC.
- Sin llamadas externas en tests.
- RuleDraft JSON.
- Guardrail rechaza número, tabla, código, batch y parámetro inventados.
- El texto no afirma efectos PROGRAM_CONTEXT como directos.

## Salida

- Markdown trazable.
- Estado NEEDS_FUNCTIONAL_REVIEW.
- UI, API y CLI.
- ZIP de resultados.

## Infraestructura

- Exactamente dos servicios.
- `docker compose up --build`.
- Sin secretos en Git o logs.
