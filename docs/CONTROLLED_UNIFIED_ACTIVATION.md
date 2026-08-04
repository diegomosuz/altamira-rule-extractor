# Control plane de activación unificada, canary y comparación V1/unified (Fase 14A)

Rama: `feat/controlled-unified-activation`. Baseline: v1.12.0.

## Propósito

Fases 9-13 construyeron, en shadow mode, el camino completo hasta un
resultado unified comparable: candidatos unificados (Fase 11),
validación diferencial (Fase 12) y ejecución downstream real —
`ContextPackage`/`RuleDraft`/`GuardrailReport` — (Fase 13). Ninguna de
esas fases decide **qué hacer** con ese resultado: si debe activarse
como canary, si debe compararse formalmente contra V1, ni bajo qué
condiciones. Fase 14A cierra ese hueco con un **control plane**: un
componente puramente **declarativo y de solo lectura** que, a partir de
una configuración explícita provista por un operador, determina el modo
operativo solicitado, selecciona canary de forma determinística,
compara el pipeline V1 con el downstream unified shadow **únicamente
mediante igualdad demostrable de campos estructurales**, y produce una
evaluación de activación — `diagnostics/unified-activation-evaluation.json`.

## Separación 14A / 14B

Fase 14A es **exclusivamente control plane y dry-run**. Aunque el
contrato admite un cuarto modo,
`UNIFIED_PRIMARY_WITH_V1_FALLBACK` (para que el contrato sea estable
hacia una fase futura), esta fase **nunca**:

- altera ningún artefacto `artifacts/01-10`;
- selecciona `unified` como productor oficial de reglas;
- publica ni promueve ninguna regla;
- invoca un proveedor LLM real;
- ejecuta un fallback productivo real;
- modifica `RunState` ni agrega un `PipelineStage`;
- modifica `runner.py`.

`materialization_enabled` es **siempre** `false` — invariante de
**tipo** (`Literal[False]`), no solo de valor por defecto: ningún YAML
puede cambiar esta restricción (ver más abajo). Toda decisión
productiva real (activar `unified` como lane efectivo, ejecutar un
fallback real, materializar candidatos/drafts/reglas) permanece **fuera
de alcance**, reservada a una Fase 14B futura.

## Modos operativos

`UnifiedActivationMode` (`contracts/unified_activation_config.py`):

| Modo | Significado | `effective_lane` en 14A |
|---|---|---|
| `V1_ONLY` (por defecto, más seguro) | V1 sigue siendo el único productor; ni siquiera exige que existan artefactos unified. | `V1` |
| `SHADOW_COMPARE` | V1 permanece primario; se compara formalmente contra el downstream unified real. | `V1` |
| `UNIFIED_CANARY` | Evalúa si un paquete calificaría para un canary unified — **dry-run**, nunca lo activa. | `V1` |
| `UNIFIED_PRIMARY_WITH_V1_FALLBACK` | Evalúa si `unified` calificaría como productor primario — **solo dry-run en 14A**. | `V1` |

`effective_lane` es **siempre `V1`**, sin excepción, para los cuatro
modos — invariante verificada tanto por un `model_validator` del
contrato (`UnifiedActivationEvaluationArtifact::
_check_effective_lane_never_unified`) como por el propio código de
política, que jamás construye una respuesta con otro valor.

## Requested lane, effective lane y fallback lane

Tres campos, tres preguntas distintas:

- **`requested_lane`**: qué lane pediría el modo, en principio
  (`V1` para `V1_ONLY`; `UNIFIED_SHADOW` para los otros tres).
- **`effective_lane`**: qué lane se usa **realmente** — en 14A,
  siempre `V1`.
- **`fallback_lane`**: a qué lane se recurriría si `unified` fallara —
  `V1` cuando `fallback_policy=FALLBACK_TO_V1` en un modo canary/
  primary, `NONE` en cualquier otro caso.

## Selección determinística de canary

`pipeline/unified_activation_canary_selector.py::select_canary`. Reglas
(ninguna excepción):

- la **denylist** siempre excluye, sin importar qué más indique la
  configuración — incluso si el mismo hash aparece **también** en la
  allowlist: el contrato permite deliberadamente ese solapamiento (no
  es un error de configuración) precisamente para poder demostrar, en
  tiempo de ejecución, que la denylist prevalece sobre una inclusión
  explícita (`matched_allowlist=True` y `matched_denylist=True` se
  reportan ambos, pero `selected` es siempre `False`);
- la **allowlist explícita** incluye cuando la estrategia lo permite
  (`EXPLICIT_ALLOWLIST`/`ALLOWLIST_OR_HASH_BUCKET`);
- el **bucket** se deriva **únicamente** del `source_package_hash`
  estable (`int(sha256(hash).hexdigest()[:8], 16) % 100`) —
  `canary_percentage=0` no selecciona nada vía bucket,
  `canary_percentage=100` selecciona todo excepto la denylist;
- el mismo hash produce **siempre** la misma decisión; `run_id`
  **nunca** influye en la selección para el mismo paquete;
- **nunca** usa `random`, **nunca** usa `hash()` nativo de Python,
  **nunca** usa un timestamp — verificado tanto por assertions directas
  como por un análisis estático AST del propio módulo (`tests/pipeline/
  test_unified_activation_canary_selector.py`).

## Comparación V1 / unified

`pipeline/unified_activation_comparator.py::compare_references` compara
**únicamente** mediante igualdad demostrable de campos estructurales —
**nunca** fuzzy matching, distancia de edición, embeddings, LLM ni
comparación por nombres parciales. El ancla de agrupación es
`(program, output_literal)` — los únicos dos campos que ambos lados
exponen siempre (V1 nunca expone `target`). Siete resultados posibles
(`UnifiedActivationComparisonKind`):

- **`EXACT_EQUIVALENT`**: mismo ancla, mismo `level`, sin contradicción
  en `family`/`target`/`statement`.
- **`UNIFIED_ADDITIVE`**: unified detectó algo sin equivalente V1 (V1
  fue efectivamente evaluado).
- **`V1_ONLY`**: V1 detectó algo sin equivalente unified (unified fue
  efectivamente evaluado).
- **`RELATED`**: mismo `program` únicamente, sin ancla completa
  compartida.
- **`CONFLICTING`**: mismo ancla y `level`, pero contradicción
  demostrable en `family`/`target`/`statement`.
- **`NOT_COMPARABLE`**: mismo ancla, `level` distinto (p. ej. un
  candidato estructural V1 contra una regla unified ya aprobada por
  guardrail) — nunca se fuerza una equivalencia entre niveles
  distintos.
- **`NOT_EVALUATED`**: la fuente opuesta nunca se evaluó — nunca se
  afirma "V1 no tiene esto" si V1 nunca se revisó.

Cada par semántico se serializa **una sola vez**. Los adaptadores
(`pipeline/unified_activation_reference_adapters.py`) distinguen dos
niveles por fuente (`UnifiedActivationComparisonLevel`): `CANDIDATE`
(estructural) vs. `RULE` (ya aprobado por guardrail, del lado V1;
ejecutado por Fase 13, del lado unified) — nunca se compara un
candidato estructural contra una regla ya aprobada como si fueran
equivalentes sin declarar ese nivel.

## Disposición de disponibilidad y decisión de activación

`UnifiedActivationReadinessDisposition` (`V1_ONLY_READY`,
`READY_FOR_SHADOW_COMPARISON`, `READY_FOR_UNIFIED_CANARY`,
`READY_FOR_PRIMARY_TRIAL`, `REVIEW_REQUIRED`, `BLOCKED`,
`NOT_EVALUATED`) y `UnifiedActivationDecision` (`KEEP_V1`,
`RUN_SHADOW_COMPARISON`, `SELECT_UNIFIED_CANARY_DRY_RUN`,
`SELECT_UNIFIED_PRIMARY_DRY_RUN`, `FALLBACK_TO_V1_PLANNED`,
`DO_NOT_ACTIVATE`) son producidos por `pipeline/
unified_activation_policy.py`, **sin puntaje, sin heurística** — una
tabla de gates explícita por modo:

- **`READY_FOR_UNIFIED_CANARY`** significa: si un operador decidiera
  activar un canary real (Fase 14B), este paquete calificaría hoy —
  **nunca** que el canary está activo.
- **`READY_FOR_PRIMARY_TRIAL`** significa: `unified` calificaría como
  candidato a productor primario — **nunca** que lo es.

`UNIFIED_CANARY`/`UNIFIED_PRIMARY_WITH_V1_FALLBACK` exigen (además de
canary seleccionado): disposición de validación calificada
(`QUALIFIED_FOR_DOWNSTREAM_SHADOW`/`QUALIFIED_WITH_WARNINGS`),
disposición de downstream `COMPLETED` (o `COMPLETED_WITH_REJECTIONS`
solo si `allow_completed_with_rejections=true`), cero fallos técnicos
(sin interruptor — siempre obligatorio) y cero rechazos de guardrail
(salvo permiso explícito). `UNIFIED_PRIMARY_WITH_V1_FALLBACK` exige,
adicionalmente: cero `CONFLICTING` y cero `V1_ONLY` **a nivel `RULE`**
(un resultado V1 ya aprobado sin representación unified bloquea
primary, pero un `V1_ONLY` a nivel `CANDIDATE` no). Cuando falta algún
prerequisito, la disposición es `NOT_EVALUATED` (nunca un error
técnico); cuando un gate falla activamente, es `BLOCKED`.

## Configuración (`--config`)

`contracts/unified_activation_config.py::UnifiedActivationConfig`. El
YAML es **enteramente externo**: el operador lo apunta con `--config`,
**nunca se copia** al repositorio ni al directorio del run — solo su
`config_hash` (calculado sobre la representación **normalizada**,
`to_stable_json()` del contrato **ya validado**, nunca sobre los bytes
crudos del YAML: dos archivos formateados distinto pero lógicamente
idénticos producen el mismo hash) se registra en el artefacto
persistido. Ver `config/unified-activation.example.yaml` — un ejemplo
seguro, versionado, en modo `V1_ONLY`, sin secretos.

Campos relevantes: `mode`, `provider_policy` (**únicamente**
`DETERMINISTIC_FAKE_ONLY` es aceptado en 14A —
`PRODUCT_PROVIDER_EXPLICITLY_AUTHORIZED` existe en el enum para
estabilidad futura, pero siempre falla la validación hoy),
`canary_strategy`/`canary_percentage`/`package_hash_allowlist`/
`package_hash_denylist`, `fallback_policy`, los interruptores de gate
(`require_validation_disposition`/`require_downstream_disposition`/
`require_all_guardrails_passed`/`allow_completed_with_rejections`),
`comparison_required` y `materialization_enabled: Literal[False]`. El
contrato **nunca** admite rutas, claves, endpoint ni modelo — esa
configuración pertenece exclusivamente a `Settings`/variables de
entorno productivas.

## CLI

```
python -m altamira_extractor.cli unified-activation-evaluate <run_id> --config <path> [--json]
```

Requiere que `run_id` haya alcanzado `PARSED` (`SUCCEEDED`) — el único
requisito real común a los cuatro modos. Carga **únicamente** los
artefactos ya existentes en disco (`artifacts/06-candidates.json`,
`artifacts/09-guardrails/`, `artifacts/10-rules/`,
`diagnostics/unified-candidates-shadow.json`, `diagnostics/unified-
shadow-validation-report.json`, `diagnostics/unified-shadow-
downstream.json`) — su ausencia se traduce en un hallazgo representable
(`NOT_EVALUATED`/`BLOCKED`), **nunca** en una regeneración ni en un
crash. Persiste **exclusivamente**
`diagnostics/unified-activation-evaluation.json`, vía
`atomic_write_json`. **Nunca** acepta proveedor, API key, endpoint,
modelo, bandera de materialización, porcentaje de canary ni decisiones
humanas por línea de comandos — todo proviene **exclusivamente** del
YAML validado.

Exit code 0 cuando el artefacto se genera exitosamente — incluso si la
disposición es `BLOCKED`/`NOT_EVALUATED` (una evaluación
contractualmente válida, no un error); exit code distinto de cero
únicamente ante un fallo técnico real (run inexistente, etapa
insuficiente, YAML ausente/inválido/incompatible con el esquema, ruta
de configuración insegura, fallo de escritura) — sin traceback, sin
ruta absoluta, sin archivo parcial.

## Seguridad

- El YAML real nunca se copia al repositorio ni al directorio del run.
- La ruta de configuración se sanea (symlink rechazado explícitamente).
- Ningún proveedor real se inicializa jamás — verificado no solo por
  ausencia funcional, sino **estáticamente** (análisis AST de los
  módulos del control plane, confirmando que ninguno importa un
  cliente de proveedor real).
- Cero llamadas de red, cero lectura de `.env`, cero impresión de
  secretos.
- El contrato de configuración nunca admite rutas absolutas, claves,
  endpoint ni modelo.

## Limitaciones

- La comparación es puramente estructural — no evalúa corrección
  funcional ni calidad lingüística; `READY_FOR_*` es una señal técnica,
  nunca una aprobación funcional.
- No hay puntaje ni heurística: toda decisión es una tabla de gates
  explícita, documentada en `pipeline/unified_activation_policy.py`.
- `UNIFIED_PRIMARY_WITH_V1_FALLBACK` solo puede evaluarse en dry-run en
  esta fase — activarlo de verdad, ejecutar un fallback productivo real
  o materializar cualquier resultado permanece fuera de alcance.

## Próxima fase (Fase 14B)

Fase 14A nunca decide activar nada — únicamente evalúa si **podría**
activarse. La decisión de construir una activación productiva real
(seleccionar `unified` como lane efectivo, ejecutar un fallback real,
materializar candidatos/drafts/reglas) permanece fuera de alcance,
reservada a una fase futura (Fase 14B). Esa fase ya está implementada:
ver `docs/CONTROLLED_UNIFIED_MATERIALIZATION.md`, que consume la
evaluación producida aquí como precondición de toda materialización.

## Ver también

- `docs/CONTROLLED_UNIFIED_MATERIALIZATION.md` (Fase 14B):
  materialización controlada, routing atómico, fallback y rollback
  reales, construidos sobre la evaluación de este control plane.
- `docs/OPERATIONAL_GOVERNANCE_UI.md` (Fase 15A): proyecta la
  disposición de readiness de este control plane en la UI/API de
  gobierno operativo read-only.
- `docs/UNIFIED_SHADOW_DOWNSTREAM_PIPELINE.md` (Fase 13): ejecución
  downstream real (`ContextPackage`/`RuleDraft`/`GuardrailReport`)
  cuyo resultado este control plane compara contra V1.
- `docs/UNIFIED_SHADOW_DIFFERENTIAL_VALIDATION.md` (Fase 12): reporte
  de validación diferencial cuya disposición este control plane exige
  como gate de canary/primary.
- `docs/UNIFIED_CANDIDATES_SHADOW.md` (Fase 11): artefacto unificado de
  candidatos en shadow mode.
