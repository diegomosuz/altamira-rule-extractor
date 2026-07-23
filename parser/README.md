# Altamira COBOL Parser (wrapper ProLeap)

Documentación local de este módulo Maven. No sustituye ni modifica
`CLAUDE.md`, `.claude/rules/java-parser.md` ni ningún documento en
`docs/`.

## Objetivo

JAR ejecutable que recibe **un** programa COBOL y produce JSON
compatible con el contrato Pydantic `CanonicalProgram`
(`src/altamira_extractor/contracts/canonical.py`). No procesa paquetes
completos (eso es responsabilidad de una integración Python futura, no
implementada todavía) y no expone tipos de ProLeap fuera del paquete
`cobol/` del propio módulo.

## Comando completo

```
java -jar parser/target/altamira-cobol-parser.jar parse \
  --input <archivo-cobol> \
  --output <archivo-json> \
  --source-package-hash <sha256> \
  --source-file <ruta-relativa-opcional> \
  --copybook-dir <directorio-opcional-repetible> \
  --format AUTO|FIXED|FREE|TANDEM \
  --encoding <encoding-java> \
  --debug
```

## Flags

| Flag | Obligatorio | Descripción |
|---|---|---|
| `parse` | sí | único comando soportado |
| `--input` | sí | ruta al archivo `.cbl` |
| `--output` | sí | ruta del JSON de salida |
| `--source-package-hash` | sí | SHA-256 hex (64 caracteres, minúsculas) del paquete de origen |
| `--source-file` | no | identidad relativa a persistir en `source_file`; si falta, se usa el basename de `--input` |
| `--copybook-dir` | no, repetible | directorio(s) donde buscar copybooks para COPY |
| `--format` | no (default `AUTO`) | ver semántica de formatos abajo |
| `--encoding` | no (default `UTF-8`) | nombre de encoding Java válido |
| `--debug` | no | imprime stack trace completo en stderr ante error |

Mensajes operativos (propios y del logging interno de ProLeap) van por
stderr. stdout queda completamente vacío en todos los casos. El JSON se
escribe únicamente en `--output`, de forma atómica (archivo temporal
único en el mismo directorio, flush, `Files.move` con `ATOMIC_MOVE`); el
temporal se elimina si algo falla y nunca queda un `--output` parcial.

## Códigos de salida

| Código | Significado |
|---|---|
| 0 | éxito |
| 2 | argumentos de CLI inválidos |
| 3 | error de preprocesamiento o parseo COBOL (incluye COPY faltante y `--format FREE`) |
| 4 | error de entrada/salida |
| 5 | error interno inesperado |

## Formatos realmente soportados

Verificado contra el código fuente real de
`io.proleap.cobol.preprocessor.CobolPreprocessor.CobolSourceFormatEnum`
(no contra su nombre): `VARIABLE` — el único candidato en ProLeap 2.4.0
a representar "formato libre" — exige exactamente la misma disposición
de columnas que `FIXED` (1-6 secuencia, 7 indicador, 8-72 código); su
única diferencia real es que no trunca el contenido a 80 columnas. **No
es COBOL free-format moderno (ISO/IEC 2002).**

- **FIXED**: soportado.
- **TANDEM**: soportado únicamente cuando se solicita explícitamente
  (nunca se infiere automáticamente: su indicador está en la columna 1,
  no la 7, y una detección equivocada sería muy costosa).
- **FREE**: **no soportado por la versión de ProLeap configurada.** Se
  acepta sintácticamente como valor de `--format` (no es un error de
  argumentos, exit code 2), pero el parseo falla explícitamente con
  exit code 3 y el mensaje exacto:
  `FREE source format is not supported by the configured ProLeap version.`
  No se reinterpreta el archivo como `VARIABLE` ni se le atribuye
  soporte que no existe.
- **AUTO**: exige **evidencia estructural** real de `FIXED`, no solo
  ausencia de líneas largas. Solo puede resolver `FIXED`. Nunca resuelve
  `FREE` ni `TANDEM`. Ante cualquier ambigüedad, falla con exit code 3
  pidiendo `--format FIXED` o `--format TANDEM` explícito — nunca adivina
  ni reinterpreta silenciosamente el archivo como `VARIABLE`.

  Se ignoran (no se evalúan ni cuentan como evidencia): líneas vacías o
  compuestas solo de espacios; comentarios fixed-format (`*` o `/` en
  columna 7); comentarios que comienzan con `*>`. Toda otra línea es
  "significativa" y debe cumplir, sin excepción:
  - longitud mínima de 7 columnas;
  - columnas 1-6 vacías o compuestas únicamente por dígitos y espacios
    (área de secuencia);
  - columna 7 con indicador válido: espacio, `-`, `D` o `d`;
  - contenido COBOL no vacío a partir de la columna 8;
  - ausencia de tabs en las columnas 1-7.

  Debe existir al menos una línea significativa que cumpla lo anterior
  (un archivo vacío o compuesto solo por comentarios no aporta evidencia
  y AUTO falla). Si **cualquier** línea significativa viola el layout —
  código en columna 1, tab en el área de secuencia/indicador, etc. — AUTO
  falla de inmediato, aunque el archivo sea corto. La longitud de línea
  (&gt;80 columnas) es a lo sumo una señal adicional; **nunca** el
  criterio único: una línea de comentario larga se ignora igual que una
  corta, y una línea de código corta que ya viola el layout se rechaza
  igual que una larga.

## Trazabilidad y COPY

Se usa el preprocesador real de ProLeap para COPY, REPLACE y COPY
REPLACING (no hay preprocesador propio). ProLeap solo expone el stream
ya expandido: no hay forma de demostrar de qué archivo físico vino cada
línea tras una expansión de COPY. Por eso, cuando el programa contiene
al menos un COPY, **todos** sus elementos (`DataItem`, `Paragraph`,
`Statement`, `SqlAccess`) quedan marcados `location_kind =
PREPROCESSED_STREAM` con `source_file = null` — nunca se atribuyen al
archivo principal — y se agrega un único warning de programa
explicando la limitación (no uno por elemento). `CanonicalProgram.source_file`
en sí (la identidad del programa principal) siempre es conocido y se
mantiene poblado.

## Subconjunto EXEC SQL

`sql/EmbeddedSqlExtractor` es un extractor determinístico acotado por
expresiones regulares para **SELECT, INSERT, UPDATE, DELETE** de una
sola sentencia: tabla(s) (tolera alias sin coma), host variables
(`:NOMBRE`), `predicate_text` (cláusula WHERE). **No es un parser
SQL/DB2 completo**: no entiende JOIN, subconsultas ni CTEs. Mapeo de
operación: `SELECT→READS`, `INSERT→INSERTS`, `UPDATE→UPDATES`,
`DELETE→WRITES` (el enum `TableAccessOperation` no tiene un valor
`DELETES` propio). Cuando el texto no coincide con este subconjunto: se
conserva el texto crudo en `source_text`, se agrega a
`unsupported_constructs`, y `sql_access` queda vacío — nunca se inventa
tabla, operación ni variables.

## ProLeap: coordenadas y riesgo de disponibilidad

```xml
<groupId>com.github.uwol</groupId>
<artifactId>proleap-cobol-parser</artifactId>
<version>b045d8093aab535a21deee4b4da4f3133d30d028</version>
```

Resuelto vía **JitPack** (`https://jitpack.io`), no Maven Central
(verificado con `mvn dependency:get` real contra
`repo.maven.apache.org` antes de optar por JitPack). El SHA corresponde
al tag `v2.4.0` del repositorio `uwol/proleap-cobol-parser` (confirmado
contra la API real de GitHub), usado en vez del tag móvil por
inmutabilidad.

**Riesgo de disponibilidad de build**: JitPack es infraestructura de
terceros que compila bajo demanda desde GitHub. Si JitPack cae o el
commit deja de estar disponible, `mvn package` falla hasta que se
mitigue (vendorizar el JAR o migrar a un fork publicado en Maven
Central). No se agregó infraestructura adicional para mitigar esto.

Dependencias transitivas no declaradas por ProLeap en su propio
`pom.xml` pero necesarias en runtime (descubiertas con smoke tests
reales, no en teoría): `com.google.guava:guava` (usada por
`CobolLine`) y `org.codehaus.plexus:plexus-utils` (usada para COPY
REPLACING). `log4j-api`/`log4j-core` se fijan a `2.20.0` vía
`dependencyManagement` (ProLeap arrastra `2.8.2`, afectado por
CVE-2021-44228 y relacionados).

## Ausencia de servidor HTTP

Este módulo es exclusivamente un JAR de línea de comandos. No abre
puertos, no expone endpoints HTTP ni ningún otro servicio de red.

## Build y tests con Maven

Java 17. Ejecutar siempre en contenedores efímeros (nunca instalar
Java/Maven en el host):

```
mvn -q -f parser/pom.xml clean test
mvn -q -f parser/pom.xml package
mvn -f parser/pom.xml dependency:tree
```

`clean` es importante: sin él pueden quedar `.class` huérfanos en
`target/` de fuentes ya eliminadas.

## Orden de ejecución de tests (incluye la validación de contrato en Python)

1. `mvn -q -f parser/pom.xml clean test` — 67 tests JUnit (unitarios;
   ningún test de proceso `java -jar` corre dentro de Maven, no se usa
   Failsafe).
2. `mvn -q -f parser/pom.xml package` — genera
   `parser/target/altamira-cobol-parser.jar`.
3. Verificación manual real: `java -jar altamira-cobol-parser.jar parse ...`
   sobre un fixture.
4. `pytest -q -m "not integration"` (Python 3.12) — regresión del resto
   del repositorio, no toca el JAR.
5. `pytest -q -m integration` — **requiere el JAR ya construido (paso 2)
   y un runtime Java 17 disponible como `java` en PATH**; ejecuta el JAR
   real vía `subprocess` y valida su JSON contra `CanonicalProgram`
   (`tests/parser_integration/test_canonical_program_contract.py`).
   Maven no depende de Python: esta validación vive enteramente del
   lado Python.
