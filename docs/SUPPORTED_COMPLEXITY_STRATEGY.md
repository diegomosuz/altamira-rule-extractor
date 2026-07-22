# Estrategia para complejidad COBOL real

No es posible garantizar soporte universal sin paquetes reales de calibración.

La estrategia correcta es incremental y regresiva:

```text
caso real
 -> fixture mínimo reproducible
 -> adaptación del parser o derivador
 -> test
 -> documentación
```

## Matriz mínima

- fixed format;
- free format;
- COPY;
- COPY anidado;
- REPLACE;
- DCLGEN;
- múltiples programas;
- IF anidado;
- EVALUATE;
- PERFORM;
- GO TO;
- múltiples decisiones por párrafo;
- EXEC SQL SELECT;
- INSERT, UPDATE y DELETE;
- host variables;
- SQLCODE;
- tablas sin schema;
- snapshots con valores nulos;
- distintos encodings;
- mismo programa en versiones distintas;
- construcciones no soportadas.

## Comportamiento

- Soportado: artefacto válido y tests.
- Parcial: se extrae evidencia con warning.
- No soportado: falla visible o candidato no generado; nunca se inventa.
