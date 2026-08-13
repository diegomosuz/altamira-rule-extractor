# Known Limitations (Fase 15B4-B)

Ninguno de estos puntos es un bug — son límites de alcance deliberados y
evidenciados a lo largo del desarrollo (Fases C4/C6/C7/C8/15B4), documentados
aquí para que el cliente/QA los conozca antes de aceptar el release.

## Alcance semántico diferido

- Semántica de File I/O (C4) diferida.
- Semántica estructurada de CICS (C6) diferida — `EXEC CICS` se conserva
  como bloque opaco (`kind=OTHER`), sin decodificar comandos individuales.
- Productización interprocedural (C7) diferida — detección existe en modo
  shadow/diagnóstico, nunca promovida a `06-candidates.json`.
- `CALL` dinámico no soportado.
- `OCCURS` no soportado.
- `SEARCH` no soportado.
- `REDEFINES` no soportado.
- `Level 78` no soportado.

## Modo enhanced

- Desde Fase 15B4-CANDIDATE-QUALITY-5E, `enhanced_candidates_enabled=true`
  es el default del código — ver `docs/release/RELEASE_PROFILES.md`
  ("Enhanced mode", incluye la discrepancia conocida y no resuelta con los
  manifests `deploy/k3s/`, que siguen fijando `false` explícitamente).
  `enhanced_candidates_enabled=false` sigue disponible como override
  explícito (modo legacy/conservador, V1/Q0-only).
- **HISTORICAL / SUPERSEDED**: el "near-duplicate" en patrones donde una
  misma Decision escribe múltiples campos en la misma rama (hasta ~48% de
  sobre-conteo medido en el caso peor real, `CATHERINE_CORREGIDO`) fue
  evaluado en Fase 15B3-C8-FIX-2-A y posteriormente corregido en Fase
  15B4-CANDIDATE-QUALITY-3B (`suppress_superseded_v1_return_code_ghosts`,
  `pipeline/enhanced_candidate_integration.py`). Medición vigente (Fase
  5E, corpus real): `CATHERINE_CORREGIDO` con `enhanced_candidates_enabled=
  true` produce 14 candidatos `RETURN_CODE` (idéntico al baseline V1-only,
  nunca 27) y 0 duplicados no explicados en todo el corpus de
  verificación. Ya no es una limitación vigente de este release.

## Volumen

- Máximo de volumen **no certificado** más allá del baseline medido (Fase
  C8: hasta 408 LOC en un único programa real). Ver
  `docs/release/ACCEPTANCE.md`, "Calificación de volumen del cliente".

## Arquitectura de despliegue

- **Réplica única obligatoria** de la app — `RunExecutor` coordina
  concurrencia únicamente en memoria, dentro de un solo proceso. Nunca
  escalar horizontalmente (`replicas>1`) ni subir `--workers` sin rediseñar
  esa coordinación (fuera de alcance de este release).
- **Reanudación manual tras restart** — un reinicio del pod `app` pierde el
  registro en memoria de "run activo" (el estado del run en sí, en
  filesystem, nunca se pierde); requiere invocar `POST
  /api/runs/{run_id}/resume` explícitamente. Sin reanudación automática al
  arranque del pod en este release.
- **Sin autenticación nativa completa** — requiere un reverse proxy externo
  ya existente del cliente, conforme a
  `docs/REVERSE_PROXY_SECURITY_REQUIREMENTS.md`. Ver
  `docs/release/INSTALL_K3S.md`.
- **`config/security.yaml` es obligatorio** — su ausencia deja `/ready` en
  503 permanente. Este release lo provee vía `Secret`
  (`deploy/k3s/secret.example.yaml`) en modo `TRUSTED_PROXY_HEADERS` por
  defecto (`DISABLED_DEV` queda reservado exclusivamente para desarrollo
  local, ver `docs/release/RELEASE_PROFILES.md`, "Security Profile").

## Perfiles de producto

- **DETERMINISTIC_OFFLINE termina en `CONTEXTS_BUILT`** — nunca genera
  `RuleDraft`/`Guardrail`/exportación final. Esto es el resultado correcto
  de ese perfil, no una limitación temporal a resolver.
- **FULL_LLM requiere un proveedor alcanzable** — sin red de salida hacia el
  proveedor configurado, el pipeline se detiene limpiamente en
  `RULE_DRAFTS_GENERATED` (nunca corrompe estado, siempre reanudable una vez
  restaurada la conectividad).

## Reproducibilidad de build

- `maven:3.9-eclipse-temurin-17` y `python:3.12-slim-bookworm` (imágenes
  base de build) son referencias flotantes (`FLOATING_PATCH`) — no fijadas
  por digest en este release. La identidad inmutable del release final está
  dada por el digest de la imagen `app` ya construida (capturado en
  `release-metadata.json`), no por sus imágenes base de build.
- `neo4j` está fijado por digest exacto en `deploy/k3s/neo4j-statefulset.yaml`
  (`neo4j@sha256:362542416de6c09a971484d1893878016cc3b5cdec166e54b1c824a220ecd6b9`,
  correspondiente a Neo4j 5.26.28 community, determinado localmente sin
  acceso a red — ver comentario inline en ese manifest). La correspondencia
  exacta tag-público↔digest **no** se verificó contra un registry real en
  esta fase.
