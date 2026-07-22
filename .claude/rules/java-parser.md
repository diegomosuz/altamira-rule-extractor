---
paths:
  - "parser/**/*.java"
  - "parser/pom.xml"
---

# Parser Java

- Usar ProLeap como parser COBOL.
- Producir un JSON canónico propio.
- Resolver COPY y REPLACE mediante el preprocesador soportado.
- Preservar archivo, línea inicial y final.
- Extraer PROGRAM-ID, Data Division, Paragraphs y statements relevantes.
- Conservar EXEC SQL como texto y estructura normalizada suficiente para derivar accesos a tablas.
- Extraer datos necesarios para def-use y dependencias de control.
- Informar warnings y unsupported_constructs.
- No crear un servidor HTTP.
- CLI por paths explícitos y códigos de salida.
- Tests fixed format, free format, copybooks, nested IF, EVALUATE y EXEC SQL.
