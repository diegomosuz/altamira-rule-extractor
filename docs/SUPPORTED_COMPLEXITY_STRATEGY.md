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

## Resultados (Prompt 15)

Fuente única de resultados para los 21 casos de la matriz mínima. Cada
fila apunta a un fixture y un test reales (existentes o agregados en
este checkpoint); no se declara soporte universal ni se usan términos
ambiguos. Rutas relativas a la raíz del repositorio; los tests Java
están bajo `parser/src/test/java/com/altamira/extractor/parser/`, los
Python bajo `tests/`.

| # | Caso | Clasificación | Alcance exacto probado | Fixture | Test | Observaciones / limitaciones |
|---|---|---|---|---|---|---|
| 1 | fixed format | SOPORTADO | Formato por defecto (columnas 1-6 secuencia, 7 indicador, código desde 8); es la precondición de casi todos los demás casos, no un caso aislado. | `parser/src/test/resources/fixtures/comprehensive.cbl` (y la mayoría de fixtures del directorio) | `cobol/CanonicalProgramExtractorTest` (clase completa), `cobol/ProLeapCobolParserTest` | Formato base de referencia. |
| 2 | free format | NO SOPORTADO | `--format FREE` es rechazado con `CobolParseException` ("FREE source format is not supported by the configured ProLeap version.") antes de producir ningún `CanonicalProgram`. | `comprehensive.cbl` (reutilizado, solicitado como FREE) | `cobol/ProLeapCobolParserTest.explicitFreeFormatIsRejectedNotSupported` | ProLeap 2.4.0 no implementa COBOL free-format ISO/IEC 2002 real; `VARIABLE` no es un sustituto válido (ver Javadoc de `ProLeapCobolParser`). Falla visible y determinística, nunca se inventa contenido. |
| 3 | COPY | SOPORTADO | Copybook simple resuelto vía `CobolParserParams.setCopyBookDirectories`. | `copy-main.cbl` + `copybooks/CPYWS01.cpy` | `cobol/CopyLocationTest.copyExpandedElementsAreMarkedPreprocessedStreamNotAttributedToMainFile` | Los elementos expandidos quedan con `source_file=null` (`PREPROCESSED_STREAM`): nunca se atribuyen al programa principal. |
| 4 | COPY anidado | SOPORTADO | Un copybook que a su vez usa COPY de otro copybook. | `copy-nested-main.cbl` + `copybooks/CPYOUTER.cpy` → `CPYINNER.cpy` | `cobol/ProLeapCobolParserTest.resolvesNestedCopy` | — |
| 5 | REPLACE | SOPORTADO | `COPY ... REPLACING`. | `copy-replacing-main.cbl` + `copybooks/CPYGENERIC.cpy` | `cobol/ProLeapCobolParserTest.resolvesCopyReplacing` | — |
| 6 | DCLGEN | SOPORTADO | Únicamente para DCLGEN COBOL resoluble como copybook local mediante las extensiones `.dcl`/`.DCL` ya admitidas en `COPYBOOK_EXTENSIONS`. | `dclgen-main.cbl` + `copybooks/DCLCLI.dcl` **(nuevos, Prompt 15)** | `cobol/DclgenCopyTest` **(nuevo)**: `dclgenCopybookResolvesAndItsFieldBecomesACanonicalDataItem`, `dclgenElementsNeverExposeAnAbsolutePath`, `dclgenResolutionIsDeterministicAcrossRuns`, `dclgenGetsNoSpecialTreatmentBeyondOrdinaryCopy`, `dclgenFieldIsUsableAfterCopy` | No implica conexión a DB2, generación automática del DCLGEN, validación contra catálogo, ni expansión de tipos fuera del contenido COBOL incluido. Mismo mecanismo y misma limitación que cualquier COPY (`source_file=null`, un único warning de programa). |
| 7 | múltiples programas | SOPORTADO | Interpretación cerrada: varios archivos fuente COBOL dentro del mismo paquete. Ambos se inventarían, se parsean, producen `CanonicalProgram` con `program_name`/hashes distintos y deterministas, sin sobrescribirse. | Paquete con `PROG001.cbl` + `PROG002.cbl` **(nuevo, Prompt 15)** | `tests/parser_integration/test_multiple_source_files_integration.py` **(nuevo)**: `test_multiple_source_files_are_all_inventoried_and_parsed_independently`, `test_multiple_source_files_produce_deterministic_hashes_across_runs` | Programas anidados dentro de una única compilation unit (`PROGRAM-ID`/`END PROGRAM` recursivo) **no** forman parte del alcance probado y no deben declararse soportados: el parser extrae exactamente un `ProgramUnit` por invocación. Prueba deliberadamente sin Neo4j ni LLM: solo exige que PARSED tenga éxito. |
| 8 | IF anidado | SOPORTADO | IF dentro de IF, aplanado como statements hermanos con `parentStatementId`/`branchKind`. | `comprehensive.cbl` (párrafo `VALIDAR-MONTO`) | `cobol/CanonicalProgramExtractorTest.nestedIfIsFlattenedWithCorrectParent`; caso aislado en `cobol/MultipleDecisionsPerParagraphTest` **(nuevo, ver fila 12)** | — |
| 9 | EVALUATE | SOPORTADO | `WHEN`/`WHEN OTHER`. | `comprehensive.cbl` | `cobol/CanonicalProgramExtractorTest.evaluateHasWhenAndWhenOtherBranches` | — |
| 10 | PERFORM | SOPORTADO | `PERFORM` simple y `PERFORM ... THRU`. | `comprehensive.cbl` | `cobol/CanonicalProgramExtractorTest.performSimpleAndThruAreCaptured` | — |
| 11 | GO TO | SOPORTADO | `GO TO` con target identificable. | `comprehensive.cbl` | `cobol/CanonicalProgramExtractorTest.goToCapturesTargetParagraph` | — |
| 12 | múltiples decisiones por párrafo | SOPORTADO | Dos decisiones IF reales en un mismo párrafo, con `statementId`/línea/predicado normalizado propios, orden determinístico (orden de aparición en el código fuente) y agregados del párrafo (`variablesRead`/`variablesWritten`) iguales a la unión real de sus statements. | `comprehensive.cbl` (párrafo `VALIDAR-MONTO`, reutilizado) | `cobol/MultipleDecisionsPerParagraphTest` **(nuevo, Prompt 15)**: `paragraphContainsAtLeastTwoDistinctIfDecisions`, `decisionOrderIsDeterministicAcrossRuns`, `paragraphAggregatesAreTheUnionOfItsStatements` | Aislado explícitamente del recorrido indirecto que ya hacía `nestedIfIsFlattenedWithCorrectParent` (fila 8). |
| 13 | EXEC SQL SELECT | SOPORTADO | Subconjunto acotado: `SELECT` de una tabla identificable en `FROM`. | `comprehensive.cbl` (párrafo `CALCULOS-PARA`) | `sql/EmbeddedSqlExtractorTest.selectWithHostVariablesAndPredicate` | `SELECT` sin `FROM` identificable, o multi-tabla ambiguo (coma), queda `unsupported` (ver `EmbeddedSqlExtractor`), nunca inventado. JOIN explícito (Fase 15B3-C3-B, corrección de correctness): antes producía una tabla parcial silenciosa, ahora `unsupported` explícito (`selectWithExplicitJoinIsUnsupportedNeverPartialTable` y variantes por tipo). |
| 14 | INSERT, UPDATE y DELETE | SOPORTADO | Mapeo determinista de operación → `TableAccessOperation`. | `comprehensive.cbl` | `sql/EmbeddedSqlExtractorTest.insertMapsToInserts`, `.updateMapsToUpdates`, `.deleteMapsToWritesNotADedicatedDeleteValue` | `DELETE` mapea a `WRITES`: `TableAccessOperation` no tiene un valor `DELETES` propio (CLAUDE.md solo permite READS/WRITES/UPDATES/INSERTS en el metamodelo). No existe una relación `DELETE` dedicada; no se describe como tal. |
| 15 | host variables | SOPORTADO | Extraídas del propio texto `EXEC SQL` vía el patrón `:IDENTIFICADOR` (lista plana, retrocompatible). Desde la Fase 15B3-C3-B, además se distingue dirección (`input_host_variables`/`output_host_variables`/`predicate_host_variables`) para la forma simple sin JOIN/subconsulta/indicator variable, propagada a `CanonicalStatement.variables_read`/`variables_written` y de ahí a `DATA_DEPENDS_ON`. | `comprehensive.cbl`, `sql-directed-data-flow.cbl` **(nuevo, Fase 15B3-C3-B)** | `sql/EmbeddedSqlExtractorTest.selectWithHostVariablesAndPredicate`; direccion: `.selectIntoAssignsOutputAndWherePredicateAssignsInput` y variantes por verbo; `cobol/SqlDirectedDataFlowTest` **(nuevo)**; `tests/pipeline/test_dependency_builder.py::test_data_dependency_through_exec_sql_select_into` **(nuevo)** | Los host variables provienen del SQL real extraído, nunca de una lista fabricada en el test. Variable indicadora (`:VAR:IND`) nunca recibe dirección (degrada toda la sentencia). |
| 16 | SQLCODE | SOPORTADO | `SQLCODE` declarado en WORKING-STORAGE queda como `CanonicalDataItem` ordinario; una decisión `IF SQLCODE = 100` se extrae igual que cualquier otro IF; su branch (`MOVE ... TO WS-COD-RETORNO`) produce el patrón `LEADS_TO` hacia un DataItem con `semantic_tag=return_code` que Q0 exige. | `sqlcode-decision.cbl` **(nuevo, Prompt 15)** | Java: `cobol/SqlcodeDecisionTest` **(nuevo)**: `sqlcodeIsExtractedAsAnOrdinaryCanonicalDataItem`, `decisionOnSqlcodeIsExtractedLikeAnyOtherIf`, `execSqlPrecedingTheDecisionIsRepresentedWithoutInventingDb2Semantics`. Python: `tests/pipeline/test_semantic_graph_builder.py::test_sqlcode_decision_leads_to_a_return_code_tagged_sink` **(nuevo)** | SQLCODE en sí mismo se etiqueta `sqlcode` en producción (`config/semantic-tags.yml`, regla `sqlcode-exact`), **no** `return_code`: lo que Q0 exige (`queries/v1/q0_candidates.cypher`) es que el *target* de la decisión tenga `semantic_tag=return_code`, no SQLCODE. Se demuestra el patrón estructural completo (`Decision-LEADS_TO->DataItem[return_code]`) sin depender de Neo4j real; la ejecución del propio Cypher Q0 contra un grafo cargado ya está cubierta genéricamente por los tests de integración de detección de candidatos existentes (no específicos de SQLCODE, no duplicados aquí). No se afirma ninguna semántica DB2 (qué significa cada código). Fase 15B3-C3-C-B: cuando SQLCODE NO se declara (patrón real dominante, corpus Catherine) y la Decision sigue inmediatamente a un EXEC SQL operativo en el mismo paragraph sin barreras (`CALL`/`PERFORM`/`GO_TO`/`PROGRAM_TERMINATION`/EXEC SQL `unsupported` intermedio), se agrega evidencia causal (`ContextPackageDecision.evidence_ids`, ver `docs/SEMANTIC_EFFECTS.md`) sin crear DataItem sintético ni tocar la familia/condition/effect de la regla. |
| 17 | tablas sin schema | SOPORTADO | `schema_name=None` cuando no se declara; el ID usa el placeholder `DEFAULT`. | Sintético, construido en el propio test | `tests/pipeline/test_semantic_graph_builder.py::test_sql_quoted_identifier_with_internal_dot` | `table_id = "table::AR::DEFAULT::..."`, `table_node.properties["schema_name"] is None`. |
| 18 | snapshots con valores nulos | NO SOPORTADO *(para semántica NULL real)* | Una celda CSV vacía dentro de una fila completa (`A,,C`) se conserva como cadena vacía de Python; el snapshot se carga igual (`ParseSupportStatus.SUPPORTED`), pero no existe ninguna semántica SQL NULL. | Sintético (`ID,NOMBRE\n1,ANA\n2,\n`), construido en el propio test | `tests/pipeline/test_csv_loader.py::test_empty_cell_in_a_complete_row_is_kept_as_empty_string_not_null`, `::test_empty_cell_row_hash_is_deterministic` **(nuevos, Prompt 15)** | El CSV puede cargarse; la semántica de base de datos NULL no se preserva; cadena vacía **no** equivale a NULL; nunca se convierte en `None`/`null` de JSON, nunca se inventa un valor ni se reutiliza el de otra fila. Ninguna regla debe redactarse atribuyendo un valor inexistente a esta fila. Sin inferencia de tipos ni nueva arquitectura de manejo de nulos. |
| 19 | distintos encodings | SOPORTADO | UTF-8 (con y sin BOM), WINDOWS-1252, ISO-8859-1, más detección de contradicciones BOM-vs-declarado y casos AUTO ambiguos que quedan explícitamente sin resolver. | Bytes sintéticos, construidos en el propio test | `tests/pipeline/test_encoding_detector.py` (20 tests): p. ej. `test_utf8_bom_is_detected_as_utf8`, `test_bom_utf8_contradicting_declared_windows_1252_wins_with_warning`, `test_bom_utf8_contradicting_declared_cp037_wins_with_warning`, `test_ascii_with_declared_windows_1252_is_windows_1252`, `test_declared_windows_1252_with_undefined_byte_leaves_unresolved`, `test_ascii_with_declared_iso_8859_1_is_iso_8859_1`, `test_auto_with_valid_utf8_is_utf8`, `test_auto_non_utf8_with_cp1252_undefined_byte_is_unambiguous_iso_8859_1`, `test_auto_non_utf8_valid_in_both_windows_1252_and_iso_8859_1_stays_ambiguous` | Solo se declaran los encodings efectivamente probados (no "todos los encodings"). CP037 (EBCDIC) declarado explícitamente no se resuelve (no reconocido). AUTO ambiguo entre WINDOWS-1252/ISO-8859-1 sin declaración explícita queda `None` + warning, nunca se asume un valor por defecto. |
| 20 | mismo programa en versiones distintas | SOPORTADO | Mismo `PROGRAM-ID`, versión o contenido (`source_hash`) distintos producen `program_id` distintos y deterministas. | Sintético, construido en el propio test | `tests/pipeline/test_dependency_builder.py::test_two_versions_of_same_program_produce_different_ids` | Coexistencia demostrada a nivel de IDs deterministas y de artefactos de filesystem por `run_id` (cada ejecución persiste bajo `runs/{run_id}/` propio, sin sobrescritura). El grafo Neo4j activo es único por diseño V1 (`neo4j_repository.py`: "una base Neo4j contiene un único grafo semántico Altamira activo"): no hay coexistencia de múltiples paquetes/versiones cargados simultáneamente en la misma base. No se declara versionado semántico ni comparación de diffs entre versiones. |
| 21 | construcciones no soportadas | SOPORTADO *(como mecanismo)* | Cualquier `Statement` fuera de IF/EVALUATE/MOVE/SET/COMPUTE/GO_TO/PERFORM/EXEC_SQL (p. ej. `GOBACK`, `ADD`) se detecta, no se modela como statement propio (`StatementKind.OTHER`, `source_text` conservado) y se registra en `unsupported_constructs` con tipo y ubicación determinísticos. | `comprehensive.cbl` (ya contiene `GOBACK` y `ADD`) | `cobol/CanonicalProgramExtractorTest.unsupportedGobackAndAddAreReportedNotInvented` *(existente, no agregado en este checkpoint)* | Hallazgo de este checkpoint: contrario a la suposición preliminar de que este flujo necesitaba una prueba nueva, **ya estaba probado** antes de Prompt 15 — no se agregó fixture nueva porque no hacía falta. Distinto del caso 2 (FREE): aquí la falla es interna a un programa `FIXED` válido (nunca impide producir `CanonicalProgram`), no un rechazo previo a la extracción. No genera un statement canónico falso ni un candidato/regla inventados. |
