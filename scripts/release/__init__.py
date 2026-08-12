"""Tooling de release (Fase 15B4-B), exclusivamente externo al proceso
de build/runtime de la aplicacion -- nunca importado por
`src/altamira_extractor/**`. Cada modulo es invocable como script
independiente (`python -m scripts.release.<modulo>`) y expone tambien
sus funciones para composicion desde otro script. Ninguno hace commit,
push, tag ni maneja secretos reales.
"""
