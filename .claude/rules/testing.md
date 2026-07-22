# Testing

- Tests unitarios sin Docker para lógica pura.
- Integración marcada `integration`.
- E2E con proveedor LLM fake.
- Ninguna llamada externa en la suite por defecto.
- Cada bug COBOL debe producir un fixture de regresión.
- Verificar idempotencia.
- Verificar dos versiones del mismo programa.
- Verificar invariantes del grafo.
- Verificar que Q2 incorpora dependencias.
- Verificar que DomainTerm es un nodo explícito.
- Verificar que una fila UNRESOLVED no aparece como valor aplicable.
- Verificar que un efecto PROGRAM_CONTEXT no se afirma como efecto directo.
