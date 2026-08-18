# Altamira Rule Extractor - diseño revisado según metamodelo semántico

> **Documento histórico**: describe el diseño y arquitectura V1
> originales (bootstrap del proyecto). El producto evolucionó
> significativamente desde entonces (K3s, seguridad/RBAC, UI de
> gobernanza, familias de candidatos V2/interprocedural, etc.) — para
> el estado actual ver `docs/ARCHITECTURE.md` y `README.md`.

Este paquete reemplaza la versión anterior del diseño.

La arquitectura está alineada con:

1. La especificación V1 de extracción end-to-end desde paquetes Altamira.
2. El metamodelo semántico del documento `Modelado de reglas de negocio`.

## Decisión central

La solución no requiere una red de agentes autónomos. Se implementa como un pipeline auditable y reanudable, con un único componente generativo:

```text
ZIP
 -> validación
 -> extracción segura
 -> análisis COBOL
 -> representación CPG reducida
 -> metamodelo semántico
 -> Neo4j
 -> invariantes
 -> candidatos
 -> siete dimensiones
 -> paquete contextual
 -> redacción LLM
 -> guardrail determinístico
 -> Markdown
```

## Runtime local

Solo dos servicios Docker:

- `app`: FastAPI, CLI, UI mínima, pipeline Python y parser Java ProLeap.
- `neo4j`: grafo semántico.

## Uso con Claude Code

1. Cree un repositorio Git vacío.
2. Copie todo el contenido de este paquete en la raíz.
3. Copie los documentos fuente en `docs/source/`.
4. Cree `.env` a partir de `.env.example`.
5. Nunca coloque claves reales en Git o en prompts.
6. Abra Claude Code en la raíz.
7. Ejecute `/context` y confirme que cargó `CLAUDE.md`.
8. Siga `docs/CLAUDE_CODE_RUNBOOK.md` desde el Prompt 0.
9. No avance de etapa si los tests de la etapa actual fallan.

## Importante

Una salida del LLM que supera el guardrail significa:

- el texto está respaldado por el paquete contextual;
- no se detectaron invenciones según las validaciones implementadas.

No significa:

- que el candidato sea definitivamente una regla de negocio;
- que la interpretación funcional haya sido aprobada por el banco.

Toda salida V1 queda con estado `NEEDS_FUNCTIONAL_REVIEW`.
