package com.altamira.extractor.parser.model;

import java.util.List;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalParagraph
 * (Python). variablesRead/variablesWritten/sqlAccess deben calcularse
 * exclusivamente a partir de {@code statements} (ver
 * cobol.CanonicalProgramExtractor#aggregateParagraph), nunca construirse
 * de forma independiente: el contrato Python rechaza cualquier
 * divergencia entre ambas representaciones.
 */
public record CanonicalParagraph(
        String name,
        String sourceText,
        String sourceFile,
        Integer lineStart,
        Integer lineEnd,
        LocationKind locationKind,
        List<CanonicalStatement> statements,
        List<String> variablesRead,
        List<String> variablesWritten,
        List<CanonicalSqlAccess> sqlAccess) {
}
