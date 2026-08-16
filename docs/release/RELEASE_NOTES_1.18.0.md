# Release notes 1.18.0

Release MINOR (funcionalidad nueva retrocompatible + una corrección de
severidad alta). Publicado sobre `v1.17.0`.

## Corregido

- **Pérdida silenciosa determinista candidato → ContextPackage**:
  `ContextPackageBuilder` tomaba `outcome_code`, y de forma simétrica el
  efecto de código de retorno (D5), desde la fila del grafo (Q4/Q5a) en
  lugar de `RuleCandidate`, ya autoritativo. Para candidatos V1 esto era
  inofensivo por tautología (misma fuente); para familias V2 que
  resuelven el valor en memoria (`V2_LEVEL_88_RETURN_CODE`,
  `V2_RETURN_CODE_PROPAGATION`, `STATE_TRANSITION`), el hecho
  determinista se perdía silenciosamente entre `06-candidates.json` y
  `07-context/`.
- El candidato es ahora la fuente autoritativa incondicional para este
  hecho; se agregó un validador determinista (`fail-closed`) en el
  límite candidato → contexto que impide persistir un `ContextPackage`
  semánticamente incompleto, y una suite de preservación parametrizada
  que cubre las cinco familias productivas actuales.
- Verificado contra una re-ejecución real y completa del paquete
  sintético Catherine y contra el corpus completo de paquetes sintéticos
  versionados: cero pérdida no explicada, cero conflicto de autoridad,
  cero deriva de identidad.

## Agregado / mejorado

- **Identidad FIERN en la interfaz**: encabezado con la marca PwC
  ampliada (marca de texto ya existente en la aplicación, con estilo
  CSS propio — el repositorio no contiene ni contenía un asset de
  imagen oficial de PwC) junto al nombre del producto, FIERN, y su
  nombre completo, "Framework Inteligente para la Extracción de Reglas
  de Negocio".
- **Página "Acerca de..."**: documentación funcional orientada al
  analista de negocio (qué es FIERN, qué paquete se sube, fases del
  pipeline en lenguaje funcional, qué es un candidato/contexto,
  comportamiento con y sin enriquecimiento LLM configurado, estados de
  ejecución, identidad por hash e implicancias de "Limpiar job"), con
  navegación de regreso.
- **Progreso real del pipeline**: porcentaje global y por etapa en el
  detalle de la ejecución, derivado exclusivamente del estado ya
  persistido del pipeline (nunca de tiempo transcurrido ni de una
  etapa con contador inventado). Una etapa marcada en ejecución que
  quedó huérfana tras un reinicio del proceso se presenta explícitamente
  como interrumpida (nunca como progreso activo inexistente).
- **Detección de paquetes duplicados**: identidad exclusivamente por el
  hash SHA-256 exacto ya existente (nunca por nombre de archivo). Subir
  de nuevo un paquete con el mismo contenido exacto crea una referencia
  liviana a la ejecución original en lugar de reprocesar desde cero;
  distingue explícitamente si la ejecución original está en curso,
  completada o fallida (una referencia a una ejecución fallida nunca se
  presenta como "ya procesado" exitosamente).
- **"Limpiar job"**: elimina de forma permanente, con confirmación
  explícita, el rastro completo de una ejecución (filesystem +, quien
  corresponda, el grafo semántico en Neo4j que esa ejecución posee
  actualmente — nunca datos de otra ejecución). Bloqueada server-side
  para una ejecución activa. Limpiar la ejecución autoritativa de un
  paquete arrastra sus referencias duplicadas y permite reprocesar el
  mismo paquete desde cero.

## Compatibilidad

- Extensiones retrocompatibles de `run.json` (nuevo campo opcional,
  nuevo tipo de registro de etapa ya contemplado por el contrato
  existente): los runs generados por v1.17.0 se leen y presentan sin
  cambios.
- Sin cambios en la semántica determinista de extracción, candidatos,
  contexto, borrador de regla, guardrail, ni en la generación de
  identificadores de candidato/regla/grafo.

## Versión

- `pyproject.toml`, `parser/pom.xml` y
  `src/altamira_extractor/__init__.py::__version__`: `1.18.0`.
- Tag de release (creado en una fase posterior, nunca aquí):
  `v1.18.0`.
