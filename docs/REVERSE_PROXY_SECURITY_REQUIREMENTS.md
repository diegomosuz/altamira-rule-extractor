# Requisitos de seguridad del reverse proxy (`TRUSTED_PROXY_HEADERS`)

Fase 15B1, `feat/final-hardening-release`. Este documento es **obligatorio**
para cualquier despliegue que active `authentication_mode: TRUSTED_PROXY_
HEADERS` en `config/security.yaml`. La aplicacion **confia ciegamente** en
los headers de identidad que recibe -- si el proxy no cumple, sin
excepcion, todos los requisitos de esta pagina, ese modo deja de ser
seguro y el despliegue debe permanecer en `DISABLED_DEV`.

## 1. Acceso directo bloqueado

La aplicacion (contenedor `app`, puerto 8000) **nunca** debe ser alcanzable
directamente desde una red no confiable -- solo el reverse proxy puede
conectarse a ella. Cualquier ruta de red que permita a un cliente externo
llegar directamente al puerto de la aplicacion, evitando el proxy, anula
por completo la seguridad de `TRUSTED_PROXY_HEADERS` (un cliente podria
enviar los headers de identidad el mismo).

Recomendacion: red interna dedicada (Docker network / VPC privada) entre
el proxy y la aplicacion, sin exposicion publica del puerto de la
aplicacion; el proxy es el unico miembro de esa red con una interfaz
publica.

## 2. Headers entrantes del cliente eliminados (stripping)

El proxy **debe** eliminar, de toda request entrante de un cliente, CUALQUIER
header que coincida con los nombres configurados como headers de identidad
(`trusted_proxy_header_user`, `trusted_proxy_header_email`, `trusted_proxy_
header_groups`, `trusted_proxy_required_marker_header`) **antes** de
resolver la identidad y volver a inyectarlos con el valor verificado. Si no
se eliminan los headers originales del cliente, un cliente malicioso puede
enviar sus propios valores y el proxy podria (dependiendo de su
configuracion) no sobrescribirlos correctamente.

Patron recomendado (nginx):

```nginx
proxy_set_header X-Verified-User "";
proxy_set_header X-Verified-Groups "";
proxy_set_header X-Trusted-Proxy "";
# ... luego, tras autenticar, fijar el valor real:
proxy_set_header X-Verified-User $verified_user;
proxy_set_header X-Verified-Groups $verified_groups;
proxy_set_header X-Trusted-Proxy $configured_marker_value;
```

## 3. TLS terminado en el proxy

El proxy debe terminar TLS (HTTPS real) antes de reenviar trafico a la
aplicacion. La cookie de sesion de esta aplicacion se fija con `Secure=true`
en todo modo distinto de `DISABLED_DEV` (`SecurityConfig._check_secure_
cookie_outside_dev`) -- si el trafico entre el cliente y el proxy no es
HTTPS real, el navegador nunca enviara esa cookie de vuelta y las sesiones
se romperan (fallo visible, no un fallo de seguridad silencioso).

`settings.hsts_enabled=true` (que agrega `Strict-Transport-Security`) SOLO
debe activarse cuando el operador puede garantizar que **todo** el trafico
hacia el dominio publico pasa por HTTPS -- activarlo sin esa garantia puede
bloquear el acceso legitimo de clientes que todavia usan HTTP.

## 4. Headers de identidad inyectados por el proxy

El proxy debe inyectar, tras autenticar al usuario por el mecanismo que sea
(SAML, OIDC, Kerberos, certificados de cliente, etc. -- fuera del alcance de
esta aplicacion), los siguientes headers con valores ya verificados:

- Header de usuario (nombre configurable, `trusted_proxy_header_user` en
  `config/security.yaml`): identificador unico y estable del usuario.
- Header de correo (opcional, `trusted_proxy_header_email`).
- Header de grupos (opcional, `trusted_proxy_header_groups`): lista
  separada por comas de los grupos del usuario -- estos grupos se mapean a
  roles via `group_role_mapping`, la aplicacion **nunca** lee un header de
  "rol" directamente.

Todos los valores deben ser ASCII imprimible, sin CR/LF (la aplicacion los
rechaza si los detecta, pero el proxy nunca deberia generarlos), y de
longitud acotada (la aplicacion rechaza valores excesivamente largos como
defensa adicional).

## 5. Header marcador (marker header)

Ademas del header de usuario, el proxy debe inyectar un **segundo** header
marcador (`trusted_proxy_required_marker_header`) con un valor secreto
compartido (`trusted_proxy_required_marker_value`, configurado como
`SecretStr` en `config/security.yaml`, **nunca** versionado). Este header
es una comprobacion de sanidad adicional: confirma que la aplicacion
realmente esta corriendo detras del proxy esperado, no solo que alguien
envio un header de usuario (que por si solo podria ser spoofeado si el
despliegue esta mal configurado).

El valor del marcador debe rotarse periodicamente (ver seccion 13) y nunca
debe coincidir con ningun valor de ejemplo de `config/security.example.
yaml` (ese archivo documenta explicitamente que su valor NUNCA debe usarse
en produccion).

## 6. Trusted hosts

`settings.trusted_hosts` (equivalente a `TrustedHostMiddleware` de
Starlette) debe fijarse al hostname real de produccion -- el default
`["*"]` es deliberadamente permisivo para no romper desarrollo local, pero
un despliegue productivo **debe** restringirlo.

## 7. Limites de request

El proxy debe aplicar, como minimo:

- Limite de tamano de cuerpo por request (la aplicacion ya aplica un limite
  adicional de 64 KiB especificamente en los endpoints de escritura de
  gobierno, pero el proxy es la primera linea de defensa contra payloads
  masivos).
- Limite de tamano de header (headers de identidad excesivamente largos ya
  se rechazan en la aplicacion, pero limitar en el proxy evita que la
  request llegue siquiera).

## 8. Timeouts

El proxy debe fijar timeouts de conexion/lectura/escritura razonables hacia
la aplicacion (p. ej. 30-60s) para evitar conexiones colgadas que agoten
recursos del `RunExecutor` local (ver `docs/CLAUDE_CODE_RUNBOOK.md` /
`api/app.py` sobre el modelo de concurrencia de un unico proceso).

## 9. Rate limiting

Esta aplicacion **no** implementa rate limiting a nivel de aplicacion (Fase
15B2 candidato, ver `docs/SECURITY_AUTHORIZATION_AND_AUDIT.md`). El proxy
debe aplicar rate limiting, especialmente sobre los endpoints de escritura
(`/ui/runs/*/governance/actions/*/prepare` y `.../execute`), para mitigar
abuso incluso desde una identidad autenticada legitima.

## 10. Politica de red (network policy)

En un despliegue orquestado (Kubernetes, etc.), aplicar una `NetworkPolicy`
(o equivalente) que restrinja el trafico de ingreso a la aplicacion
exclusivamente desde el proxy -- no solo confiar en la topologia de red
declarada, sino hacerla cumplir a nivel de red.

## 11. Redaccion de logs

El proxy **nunca** debe loguear el valor completo de los headers de
identidad ni del header marcador en texto plano en logs de acceso
persistentes y ampliamente legibles -- aplicar la misma disciplina de
redaccion que esta aplicacion aplica internamente (ver
`docs/SECURITY_AUTHORIZATION_AND_AUDIT.md`, seccion de logging).

## 12. Endpoints de salud

`GET /health` de esta aplicacion no requiere identidad y no debe exigirse
que pase por la logica de `TRUSTED_PROXY_HEADERS` -- el proxy puede (y
deberia) usarlo para su propio healthcheck sin inyectar headers de
identidad sinteticos.

## 13. Procedimiento de rotacion del secreto de sesion

`ALTAMIRA_SESSION_SECRET` (la clave HMAC que firma la cookie de sesion) y
`trusted_proxy_required_marker_value` (el valor del header marcador) son
dos secretos independientes que deben rotarse periodicamente:

1. Generar un nuevo valor con una fuente criptografica (`secrets.
   token_hex(32)` o equivalente) -- nunca un valor predecible.
2. Actualizar el secreto en el almacen de configuracion del despliegue
   (variable de entorno para `ALTAMIRA_SESSION_SECRET`; `config/security.
   yaml` -- fuera del repositorio -- para el marcador).
3. Reiniciar la aplicacion. Rotar `ALTAMIRA_SESSION_SECRET` invalida
   inmediatamente TODAS las sesiones activas (los usuarios deben volver a
   ser identificados por el proxy en su siguiente request -- transparente
   si el proxy ya los tiene autenticados) y todos los challenges/tokens
   CSRF en vuelo (cualquier formulario abierto en ese momento debe
   recargarse). Esto es esperado y seguro: nunca se degrada a aceptar la
   clave anterior "por un tiempo".
4. Nunca versionar, imprimir, ni almacenar estos secretos en artefactos de
   ejecucion (`runs/`, logs, diagnosticos).
