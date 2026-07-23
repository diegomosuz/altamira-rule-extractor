package com.altamira.extractor.parser.cobol;

/** Valor crudo de --format, tal como lo recibe el CLI. AUTO se resuelve
 * antes de construir un CanonicalProgram (ver ProLeapCobolParser): nunca
 * se persiste en el JSON de salida. */
public enum RequestedFormat {
    AUTO,
    FIXED,
    FREE,
    TANDEM
}
