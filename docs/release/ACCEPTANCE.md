# Acceptance test (Fase 15B4-B)

Dos perfiles **sin alternativa ambigua** — nunca "usar fake LLM o saltar la
etapa" (ver `docs/release/RELEASE_PROFILES.md` para la clasificación
completa). Elegir el perfil correspondiente al despliegue real del cliente
antes de ejecutar.

## Perfil A — DETERMINISTIC_OFFLINE

**Stage final esperado: `CONTEXTS_BUILT`.** Sin proveedor LLM.

1. `GET /health` → 200.
2. `GET /ready` → 200, `ready=true`.
3. Subir un paquete `.zip` sintético versionado conocido (p. ej.
   `examples/PAQUETE_SINTETICO_PRESTAMOS_EMPRESAS_5_REGLAS.zip`).
4. Confirmar progresión del pipeline hasta `CONTEXTS_BUILT` (`GET
   /api/runs/{run_id}`).
5. `candidate_count > 0` en `artifacts/06-candidates.json`.
6. ContextPackages generados en `artifacts/07-context/`, válidos contra
   `schemas/context-package.schema.json`.
7. **No** esperar `RuleDraft`/`Guardrail`/`COMPLETED` — su ausencia en este
   perfil es el resultado correcto, no un fallo.
8. Restart básico: reiniciar el pod `app` (`kubectl delete pod`), confirmar
   que Neo4j y el PVC persisten, invocar `POST
   /api/runs/{run_id}/resume` sobre el run del paso 3-4, confirmar que
   reanuda correctamente (mismo stage final, sin reprocesar lo ya
   completado — ver idempotencia por etapa documentada en
   `pipeline/runner.py`).

## Perfil B — FULL_LLM

**Stage final esperado: `COMPLETED`.** Requiere proveedor LLM configurado y
alcanzable.

Pasos 1-6 idénticos al perfil A, más:

7. `RuleDraft` generado por candidato (`artifacts/08-rule-drafts/`).
8. `Guardrail` verdict `EVIDENCE_VALIDATED` por candidato
   (`artifacts/09-guardrails/`).
9. Artefacto final de reglas descargable (`artifacts/10-rules/` /
   `GET /api/runs/{run_id}/download`).
10. Restart básico: igual que el paso 8 del perfil A, confirmando que
    reanuda hasta `COMPLETED`.

**Este perfil nunca se ejecuta contra un proveedor real durante
qualification/CI** — usar exclusivamente el arnés fake/stub hermético ya
usado en todo el desarrollo (`tests/hermetic_llm_support.py`,
`build_hermetic_settings` + `hermetic_llm_and_network_guard`). Producción
real del cliente requiere un proveedor aprobado y alcanzable, fuera del
alcance de este documento.

## Calificación de volumen del cliente (obligatoria antes de PROD)

No certificado en este release — el corpus real medido (Fase C8) es pequeño
(máx. 408 LOC/programa único). Antes de producción, ejecutar al menos un
paquete representativo del cliente (preferiblemente >2000 LOC o comparable
al volumen real esperado) y medir:

- tiempo total de ejecución end-to-end;
- pico de memoria del pod `app`;
- pico de memoria de `neo4j`;
- `candidate_count` resultante;
- `ContextPackage` count resultante.

Sin esta medición, los valores de `resources.requests/limits` de
`deploy/k3s/*.yaml` permanecen como baseline inicial sin validar a escala.
