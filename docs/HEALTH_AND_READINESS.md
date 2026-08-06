# Health y Readiness (cierre Fase 15B1 + extensión Fase 15B2-B)

## `GET /health`: liveness pura, sin cambios en esta fase

200 mientras el proceso pueda responder. Nunca consulta Neo4j, un
proveedor LLM, `runs_dir`, ni `config/security.yaml` — una dependencia
externa caída no debe convertir el proceso en "not alive". Auditado en
esta fase (`api/routers/health.py::get_health`) y confirmado sin
ninguna modificación de comportamiento.

## `GET /ready`: readiness de PROCESO acotada

`/ready` extiende (cierre Fase 15B1: solo verificaba
`config/security.yaml`) a cuatro chequeos, evaluados en este orden de
prioridad (el primero que falla determina `reason`):

1. **`security_configuration`** — `config/security.yaml` cargado
   correctamente (comportamiento original, sin cambios).
2. **`parser_jar`** — `settings.parser_jar_path` existe como archivo
   regular (nunca symlink).
3. **`data_root`** — `settings.runs_dir` existe como directorio, o su
   directorio padre existe (instalación nueva, cero runs todavía).
   Basado deliberadamente en `runs_dir`, nunca en `data_dir` (que
   muchos despliegues/tests dejan en su default relativo sin crear
   nunca).
4. **`executor`** — el `RunExecutor` local quedó inicializado en el
   `lifespan`.

Ninguno de los cuatro depende de Neo4j, de un proveedor LLM, de que
existan runs, ni de que metrics/ground-truth estén habilitados — esos
son fallos por-request o recursos externos, nunca motivo para que el
proceso se reporte "not ready".

`ReadinessResponse.ready`/`reason` se preservan **exactamente** en su
forma original (`reason: "security_misconfigured" | None` sigue siendo
el único valor posible cuando la config de seguridad es el chequeo que
falla, primero en la prioridad) — los 3 tests preexistentes de
`tests/ui/test_security_misconfigured.py` verifican esto sin
modificación. Se agrega `checks: list[ReadinessCheckResult]` (detalle
completo de los 4 chequeos, siempre presente) como campo nuevo, nunca
reemplaza al campo `reason` original.

### Nueva dependencia implícita: el JAR del parser debe existir

A diferencia del comportamiento previo a este cierre, `/ready` ahora
depende de que `parser/target/altamira-cobol-parser.jar` exista (`mvn
-f parser/pom.xml package` ya ejecutado). `parser/target/` está
excluido de git (`.gitignore`): un checkout nuevo o una imagen Docker
construida sin ejecutar Maven primero reportará `ready=false` con
`reason=parser_jar_missing` hasta que el JAR se construya — este es el
comportamiento **correcto y deseado** (el proceso genuinamente no puede
procesar paquetes COBOL sin el JAR), documentado aquí explícitamente
como el único cambio de comportamiento observable de esta extensión.

## `GET /api/operations/component-diagnostics`: diagnóstico protegido

Ver `docs/OBSERVABILITY.md` para el detalle completo (permiso
requerido, los 11 componentes, qué nunca se expone). A diferencia de
`/ready` (binario, sin autenticación, pensado para
orquestadores/probes), este endpoint requiere
`OperationalPermission.VIEW_SECURITY_STATUS` y da detalle por
componente para un operador humano.
