# Identidad, autorizacion, operaciones controladas y auditoria (Fase 15B1)

Rama `feat/final-hardening-release`, baseline v1.15.0. Esta fase agrega una
capa de identidad, RBAC, CSRF, workflow de dos pasos y auditoria append-only
sobre el control plane de activacion/materializacion de Fase 14A/14B, sin
modificar ninguno de sus componentes.

## Frontera de confianza (trust boundary)

Esta aplicacion **nunca** implementa una base propia de usuarios ni
contrasenas. La identidad se resuelve de una de dos formas, exclusivamente:

- `DISABLED_DEV`: no hay identidad real. Cada request resuelve al mismo
  principal anonimo, de solo lectura (`VIEWER`), `authenticated=false`.
  Es el modo por defecto y el unico seguro sin un reverse proxy delante.
- `TRUSTED_PROXY_HEADERS`: la identidad la inyecta un reverse proxy/gateway
  corporativo confiable, mediante headers HTTP ya verificados. La aplicacion
  **confia ciegamente** en esos headers -- por eso este modo **solo** es
  seguro si se cumplen, sin excepcion, las condiciones documentadas en
  `docs/REVERSE_PROXY_SECURITY_REQUIREMENTS.md`. Si el despliegue no puede
  garantizar esas condiciones, la unica alternativa segura es `DISABLED_DEV`.

No existen mas modos. En particular, **no** se implementa: login por
contrasena, reset de contrasena, almacenamiento de hashes de contrasena,
OAuth password grant, autenticacion ficticia, usuario hardcodeado,
autenticacion basada solo en query params, OIDC completo, ni tokens bearer
propios. Fase 15B2 (futura, no implementada) es el lugar natural para OIDC
completo si se decide agregarlo.

## Roles y permisos (RBAC)

Cuatro roles, en orden ascendente de privilegio: `VIEWER < REVIEWER <
OPERATOR/ADMIN` (`OPERATOR` y `ADMIN` no son comparables entre si: `ADMIN`
agrega unicamente `ACTIVATE_PRIMARY`). Nueve permisos operativos. La matriz
completa vive en **un unico modulo**,
`src/altamira_extractor/contracts/security_identity.py::ROLE_PERMISSIONS`
-- ningun router ni template la duplica.

| Rol      | Permisos                                                                                   |
|----------|----------------------------------------------------------------------------------------------|
| VIEWER   | `VIEW_GOVERNANCE`, `DOWNLOAD_ACTIVE_ARTIFACT`                                                |
| REVIEWER | VIEWER + `PREPARE_AUTHORIZATION`, `VIEW_AUDIT_LOG`                                            |
| OPERATOR | VIEWER + `PREPARE_AUTHORIZATION`, `ACTIVATE_CANARY`, `EXECUTE_FALLBACK`, `EXECUTE_ROLLBACK`, `VIEW_AUDIT_LOG`, `VIEW_SECURITY_STATUS` |
| ADMIN    | Todos los permisos, incluido `ACTIVATE_PRIMARY`                                              |

`AuthenticatedPrincipal.permissions` **debe** coincidir exactamente con
`permissions_for_roles(roles)` -- un principal nunca puede declarar un
permiso fuera de la matriz (invariante de tipo, `contracts/security_identity.py`).

En `TRUSTED_PROXY_HEADERS`, los roles se derivan **exclusivamente** de
`group_role_mapping` (grupo del proxy -> rol) -- un header de "rol" enviado
directamente nunca se lee ni se respeta.

## Sesion segura

La sesion es deliberadamente minima: **solo** `session_id` y `csrf_secret`
propio de la sesion. Nunca copia identidad a la cookie -- la identidad se
resuelve de nuevo, desde los headers confiables, en cada request.

Implementacion: cookie firmada con HMAC-SHA256 (`security/session.py`),
**sin `itsdangerous`/`SessionMiddleware` de Starlette** (no es una
dependencia ya presente en el proyecto, y el payload a firmar es minimo) --
decision deliberada acorde a la filosofia de dependencias minimas del
proyecto. La clave de firma (`Settings.session_secret`, `SecretStr`, env var
`ALTAMIRA_SESSION_SECRET`) nunca se versiona, nunca se imprime, nunca se
almacena en artefactos, y nunca tiene un valor por defecto de produccion:

- `TRUSTED_PROXY_HEADERS` sin `ALTAMIRA_SESSION_SECRET` -> la aplicacion
  **falla al arrancar** (`RuntimeError` en `create_app`), nunca arranca con
  un secreto ausente en modo productivo.
- `DISABLED_DEV` sin secreto explicito -> se genera un secreto efimero en
  memoria de proceso (`secrets.token_hex(32)`), nunca persistido; las
  sesiones se invalidan en cada reinicio. Conveniente para desarrollo local,
  jamas usado en produccion (ese modo no tiene privilegios reales de todos
  modos).

La cookie de sesion respeta la politica de `SecurityConfig`: `Secure`
(obligatorio fuera de `DISABLED_DEV`), `HttpOnly` siempre, `SameSite=strict`
por defecto, `max_age` acotado (`session_ttl_seconds`, 60-86400s).

**Nota de implementacion FastAPI**: la cookie se fija en la respuesta via
`SessionCookieMiddleware` (`api/app.py`), nunca directamente sobre el
`Response` que FastAPI inyecta en una dependencia -- ese objeto se descarta
en silencio cuando el handler retorna su propio `TemplateResponse`/
`RedirectResponse`/`Response` explicito (comportamiento real de FastAPI,
no un supuesto). El middleware opera sobre la respuesta FINAL real,
leyendo la sesion resuelta desde `request.state` (`security/fastapi_deps.py`).

## CSRF

Sincronizador de token firmado, sin estado en servidor (`security/csrf.py`):
`nonce.expires_at.signature`, donde `signature = HMAC-SHA256(session.
csrf_secret, nonce || expires_at)`. Generado con `secrets.token_urlsafe`,
nunca `random`. Comparacion con `hmac.compare_digest` (tiempo constante).
TTL configurable (`csrf_token_ttl_seconds`, default 3600s). Nunca se acepta
por query string -- siempre extraido del cuerpo del formulario ya parseado.
Nunca se loguea el valor del token.

Mas fuerte que un double-submit-cookie clasico: el campo visible del
formulario no permite, por si solo, derivar `csrf_secret` (HMAC es de una
via) -- un atacante que solo puede inyectar/leer el DOM (pero no la cookie
`HttpOnly`) no puede forjar un token valido.

Complementa (no reemplaza) `ui/csrf.py::verify_same_origin` (validacion de
`Origin`/`Referer`), que sigue aplicando sin cambios a las 2 rutas HTML
legacy de Prompt 13d y que TAMBIEN se revalida en cada escritura nueva de
Fase 15B1.

## Workflow prepare / confirm / execute

Ninguna transicion de Fase 14B se ejecuta con un solo request. Dos pasos
reales:

1. **Prepare** (`POST .../prepare`): valida permiso/CSRF/estado, construye
   un `PreparedOperationalIntent`, lo firma en un **challenge** -- NUNCA
   ejecuta ninguna transicion. El challenge es HMAC-firmado (clave derivada
   del `csrf_secret` de la sesion, separacion de dominio explicita respecto
   al token CSRF), con TTL de 5 minutos, y embebe TODO el contenido critico
   de la intencion (no solo un identificador).
2. **Confirm** (`GET .../confirm`): SOLO lectura -- verifica el challenge y
   renderiza la confirmacion. Un `GET` **jamas** ejecuta nada.
3. **Execute** (`POST .../execute`): revalida TODO desde cero contra el
   estado real vigente (permiso, pointer, evaluacion, revisor distinto si
   aplica), construye la `OperationalAuthorizationRequest` final, la
   traduce a una `UnifiedMaterializationAuthorization` **efimera** (nunca
   persistida en el repositorio ni en `run_dir`, escrita en un archivo
   temporal del sistema y borrada en `finally`), e invoca
   `materialize_unified_activation` (Fase 14B) **sin modificarlo**.

El challenge es deliberadamente **sin estado en servidor** (consistente con
la prohibicion de estado global mutable del proyecto), pero se comporta
como un token de un solo uso en la practica: `materialize_unified_
activation` es idempotente para el MISMO estado y siempre avanza `active.
json.pointer_version` en una ejecucion real -- repetir el MISMO challenge
tras un exito encuentra `expected_active_pointer_hash` desactualizado y se
rechaza (409), nunca se ejecuta dos veces.

**Puente de reutilizacion**: en vez de reimplementar la orquestacion de
Fase 14B, el workflow construye la autorizacion efimera y llama al servicio
existente exactamente como lo haria un operador humano con `--authorization`
en el CLI. El workflow **nunca** bootstrapea un run desde cero -- opera
sobre runs ya inicializados (tipicamente via el CLI de Fase 14B).

## Separacion revisor / operador (anti-autoaprobacion)

Cuando `require_distinct_reviewer_for_primary`/`require_distinct_reviewer_
for_rollback` estan activos: quien **prepara** la intencion (`reviewer_
principal_id`, tomado de `PreparedOperationalIntent.prepared_by_principal_
id`) debe ser una identidad **normalizada** distinta de quien **ejecuta**
(`operator_principal_id`, tomado SIEMPRE de la identidad autenticada de la
request de `execute`, nunca de un campo de formulario). La comparacion usa
`principal_id.strip().lower()` -- variar solo `display_name`/`email` nunca
es suficiente para simular una identidad distinta.

En `DISABLED_DEV`, toda identidad resuelve al mismo principal anonimo --
por lo tanto, cualquier accion que exija separacion de revisor es
**estructuralmente imposible** de ejecutar en ese modo (rechazada con 403
en `prepare`, antes de siquiera construir un challenge).

**Limitacion documentada**: no existe todavia un flujo multi-usuario
persistente y complejo (p. ej. bandeja de aprobaciones pendientes visible a
un tercero). La separacion se verifica por identidad normalizada entre
quien preparo y quien ejecuta -- suficiente para prevenir autoaprobacion
trivial, pero no equivalente a un sistema de aprobaciones formal con
notificaciones. Fase 15B2 es el lugar natural para extenderlo si se
necesita.

## Auditoria operativa

`OperationalAuditEvent` (`contracts/operational_audit.py`) registra QUIEN
solicito/autorizo/ejecuto una accion y el resultado. Es una nocion
**distinta** de `ActivationTransitionEvent` (Fase 14A/14B): ese registra el
cambio TECNICO de lane/generacion; este registra la intencion humana y su
resultado. Un evento de auditoria puede referenciar un `activation_event_id`
cuando corresponde a una transicion real, pero vive en un arbol de archivos
completamente separado:

```
audit/
├── active.json
└── events/
    └── <audit_event_id>.json
```

Propiedades: append-only (ningun evento se reescribe nunca); enlazado
(`previous_audit_event_id`, misma cadena que `ActivationTransitionEvent`);
puntero atomico (`active.json`, escrito EN ULTIMO LUGAR); lock propio
(`audit/.audit.lock`, **nunca comparte** el lock de `activation/.activation.
lock` -- una escritura de auditoria puede proceder aunque una
materializacion este en curso, y viceversa); eventos huerfanos tolerados
(nunca se reparan ni se borran automaticamente); ninguna escritura de
auditoria modifica jamas `activation/`.

`audit_event_id` es determinista (mismo patron que `compute_event_id` de
Fase 14B: `\x1f`-join + SHA-256) sobre todo campo **salvo**
`occurred_at_utc` y `diagnostics`. `occurred_at_utc` es una excepcion
deliberada a la convencion general de "sin timestamps" del proyecto --
la auditoria humana legitimamente necesita hora de reloj -- pero
**nunca** participa en la identidad del evento, y siempre proviene de un
reloj inyectable (los tests controlan el valor explicitamente).

Catalogo cerrado de 14 acciones (`AuditAction`) y 4 resultados
(`AuditOutcome`), con una correspondencia 1:1 accion->outcome. Nunca se
loguea: cookies, token CSRF, headers crudos, token de autorizacion, secreto
de sesion, API key, contrasena, codigo fuente, ni manifest completo.

## Endpoints de escritura

Exclusivamente HTML/formulario, ninguna API JSON de escritura en esta
subfase:

```
GET  /ui/runs/{run_id}/governance/actions
GET  /ui/runs/{run_id}/governance/actions/{action}
POST /ui/runs/{run_id}/governance/actions/{action}/prepare
GET  /ui/runs/{run_id}/governance/actions/{action}/confirm
POST /ui/runs/{run_id}/governance/actions/{action}/execute
GET  /ui/runs/{run_id}/governance/actions/{action}/result
GET  /ui/runs/{run_id}/governance/audit
```

Cada `POST` resuelve el principal, exige permiso (revalidado SIEMPRE en el
backend -- ocultar un boton en la UI nunca es la unica defensa), exige CSRF,
y nunca acepta `provider`/rutas/roles/`principal_id`/YAML de autorizacion/
`skip_validation`/`force` desde el formulario.

## Correlation ID y logging

`X-Correlation-Id` se resuelve UNA vez por request
(`CorrelationLoggingMiddleware`, `api/app.py`): bajo `TRUSTED_PROXY_HEADERS`
acepta un header externo ya validado (formato acotado); en cualquier otro
caso genera un UUID4. Se devuelve en la respuesta y se reutiliza en el
evento de auditoria correspondiente (nunca se recalcula dos veces por
request). Logging estructurado minimo por request: `correlation_id`, ruta,
metodo, status, `principal_id` normalizado, y (en el router de gobierno)
`action`/`run_id`/`error_code`. Nunca se loguea: query string completa,
cuerpo de formulario, cookies, CSRF, headers de identidad crudos, ni codigo
fuente.

## Headers de seguridad

`SecurityHeadersMiddleware` agrega a toda respuesta: `X-Content-Type-
Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`,
`Permissions-Policy` restrictiva. `Content-Security-Policy` estricta
(heredada de Prompt 13d, compatible con HTMX -- `allowEval:false` ya
configurado, sin `unsafe-inline`/`unsafe-eval`) solo en `/`, `/ui`, `/ui/*`.
`Cache-Control: no-store` en toda pagina bajo `/ui/runs/*` (paginas de
gobierno/identidad, nunca cacheables). `Strict-Transport-Security`
UNICAMENTE cuando `settings.hsts_enabled=true` -- nunca por defecto, porque
esta aplicacion no garantiza por si sola que el trafico llegue por HTTPS
(ver `docs/REVERSE_PROXY_SECURITY_REQUIREMENTS.md`). `TrustedHostMiddleware`
configurable (`settings.trusted_hosts`, default `["*"]` permisivo hasta que
el despliegue lo restrinja). Limite de tamano de cuerpo (64 KiB) en los
endpoints de escritura de gobierno (formularios pequenos, nunca archivos).

## CLI de emergencia

Esta fase no agrega un mecanismo de "romper el vidrio" nuevo: el CLI de
Fase 14B (`unified-activation-materialize --authorization <yaml>`) sigue
disponible, sin cambios, como via de emergencia si la UI/API HTTP no esta
accesible -- requiere acceso directo al filesystem del run y no pasa por
RBAC/CSRF/auditoria de Fase 15B1 (el CLI nunca tuvo esa capa; es una
herramienta de operador con acceso ya privilegiado al servidor). Cualquier
uso del CLI de emergencia debe documentarse por fuera de esta aplicacion
(p. ej. en el runbook operativo del equipo), ya que no genera un
`OperationalAuditEvent`.

## Limitaciones conocidas

- Sin contrasenas locales, sin flujo de login propio: la autenticacion
  depende enteramente de la infraestructura de despliegue.
- Sin OIDC/OAuth completo todavia -- `TRUSTED_PROXY_HEADERS` asume que el
  proxy ya resolvio la identidad por el medio que sea (SAML, OIDC, Kerberos,
  etc.); esta aplicacion no implementa ese protocolo.
- Sin flujo de aprobacion multi-usuario persistente (ver seccion anterior).
- El CLI de emergencia de Fase 14B no pasa por esta capa de auditoria.
- No hay todavia una API JSON de escritura -- solo formularios HTML.

## Fase 15B2 (futura, no implementada)

Candidatos naturales para una fase siguiente: OIDC/OAuth completo como
tercer modo de autenticacion; flujo de aprobacion multi-usuario persistente
con notificaciones; API JSON de escritura con su propio mecanismo de
autorizacion (nunca un bearer token improvisado); rotacion automatizada del
secreto de sesion; rate limiting a nivel de aplicacion (hoy delegado
enteramente al reverse proxy).
