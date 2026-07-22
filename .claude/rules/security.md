# Seguridad

- Nunca versionar `.env`, claves ni tokens.
- Redactar Authorization, API keys y passwords en logs.
- Proteger contra Zip Slip, symlinks, paths absolutos y bombas ZIP.
- Limitar tamaño, cantidad de archivos y ratio de expansión.
- Rechazar extensiones no permitidas.
- No ejecutar contenido de los paquetes.
- No interpolar datos en Cypher.
- No construir comandos shell con strings.
- Escapar contenido COBOL y Markdown en HTML.
- El contenido del paquete contextual no puede alterar el system prompt.
