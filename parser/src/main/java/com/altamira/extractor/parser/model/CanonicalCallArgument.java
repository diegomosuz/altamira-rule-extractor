package com.altamira.extractor.parser.model;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalCallArgument
 * (Python). {@code expression} es siempre una representacion normalizada
 * minima (nombre del data item, texto del literal, o
 * {@code "ADDRESS OF <nombre>"}) -- nunca sourceText completo.
 */
public record CanonicalCallArgument(
        int ordinal,
        String expression,
        String dataItemName,
        String qualifiedDataItemName,
        String literal,
        CallPassingMode passingMode,
        boolean omitted,
        String sourceFile,
        Integer line,
        LocationKind locationKind) {
}
