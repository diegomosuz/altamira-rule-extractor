# Instalación en K3s (Fase 15B4-B)

## Prerrequisito obligatorio: reverse proxy externo

Este producto **no** implementa autenticación nativa completa ni rate
limiting (decisión de alcance V1, ver `docs/SECURITY_AUTHORIZATION_AND_AUDIT.md`).
`altamira-app:8000` **nunca** debe exponerse directamente a una red no
confiable. El cliente debe operar un reverse proxy/Ingress **ya existente**
que cumpla `docs/REVERSE_PROXY_SECURITY_REQUIREMENTS.md` (13 requisitos,
incluyendo terminación TLS, inyección de headers de identidad verificados, y
un header marcador compartido). Este release **no** crea un Ingress
controller ni un sidecar nginx — el `Service` de `deploy/k3s/app-deployment.yaml`
es `ClusterIP`, nunca `NodePort`/`LoadBalancer`.

## Instalación offline (imágenes pre-importadas)

Los manifests asumen `imagePullPolicy: IfNotPresent` y **nunca** intentan
`docker pull` desde un registry público. Dos mecanismos posibles, a elección
del cliente:

**A. Importar directamente a containerd de K3s** (sin registry):

```
k3s ctr images import altamira-rule-extractor-images.tar
# o, con containerd standalone:
ctr images import altamira-rule-extractor-images.tar
```

**B. Registry interno ya existente del cliente**: cargar el tarball con
`docker load`, re-etiquetar hacia el registry interno (`docker tag` +
`docker push` al registry del cliente, **nunca** a un registry público), y
referenciar esa ruta en los manifests (`image:` en `app-deployment.yaml`/
`neo4j-statefulset.yaml`) antes de aplicar.

Este release no introduce ni requiere un registry propio.

## Procedimiento (orden)

```
kubectl apply -f deploy/k3s/namespace.yaml
kubectl apply -f deploy/k3s/configmap.yaml
# copiar secret.example.yaml, reemplazar TODOS los placeholders, aplicar la copia:
kubectl apply -f <secret-real>.yaml
kubectl apply -f deploy/k3s/app-pvc.yaml
kubectl apply -f deploy/k3s/neo4j-statefulset.yaml
kubectl apply -f deploy/k3s/app-deployment.yaml
```

Neo4j debe alcanzar `readinessProbe` OK antes de que la app pueda completar
un run real (ver `docs/release/RELEASE_PROFILES.md` — `/ready` de la app
**nunca** depende de Neo4j, por diseño; un run puede aceptarse y fallar
limpiamente en `SEMANTIC_GRAPH_LOADED` si Neo4j aún no está listo, y
reanudarse después vía `POST /api/runs/{run_id}/resume`).

## Configuración

`deploy/k3s/configmap.yaml` contiene únicamente las variables que requieren
override en K3s (paths, Neo4j host/URI no secreto, concurrencia,
`enhanced_candidates_enabled`). El resto de los ~55 campos de `Settings`
conserva su default seguro ya definido en `src/altamira_extractor/config.py`
— no se trasladan indiscriminadamente.

## Security Profile — ver `docs/release/RELEASE_PROFILES.md`

**`config/security.yaml` es obligatorio** (su ausencia deja `/ready` en 503
permanente, `reason=security_misconfigured`) y **vive en
`secret.example.yaml`, nunca en `configmap.yaml`** — el default de release
es `TRUSTED_PROXY_HEADERS` (nunca `DISABLED_DEV`, que queda exclusivamente
para desarrollo local). Esto aplica **igual** en los dos perfiles de
producto (`DETERMINISTIC_OFFLINE`/`FULL_LLM`, ver
`docs/release/RELEASE_PROFILES.md`) — el perfil offline nunca implica
autenticación reducida.

## Secrets

Ver `deploy/k3s/secret.example.yaml` — nunca aplicar ese archivo tal cual.
Contiene tanto los secretos "planos" (`NEO4J_PASSWORD`,
`ALTAMIRA_SESSION_SECRET`) como el bloque completo de `security.yaml`
(incluyendo `trusted_proxy_required_marker_value`, el secreto compartido con
el reverse proxy del cliente). Mínimos por perfil de producto — **la
autenticación es igual en ambos**:

- **DETERMINISTIC_OFFLINE**: `NEO4J_PASSWORD`, `ALTAMIRA_SESSION_SECRET`,
  `security.yaml` completo.
- **FULL_LLM**: los tres anteriores + `OPENAI_API_KEY` **o**
  `PWC_GENAI_API_KEY` (nunca ambas).

Los placeholders de `NEO4J_PASSWORD`/`ALTAMIRA_SESSION_SECRET` en el archivo
de ejemplo son deliberadamente demasiado cortos para pasar sus validaciones
reales — aplicar el template sin reemplazarlos produce un fallo de arranque
visible (`CrashLoopBackOff`), nunca un secreto débil quedando activo en
silencio (ver `docs/release/RELEASE_PROFILES.md`, "Security Profile").

## Recursos, probes y persistencia

Ver comentarios inline en `deploy/k3s/app-deployment.yaml` /
`neo4j-statefulset.yaml` — valores de CPU/memoria son **baseline inicial a
validar**, nunca capacidad certificada (ver
`docs/release/QA_TO_PROD_AND_ROLLBACK.md`, sección de calificación de
volumen).
