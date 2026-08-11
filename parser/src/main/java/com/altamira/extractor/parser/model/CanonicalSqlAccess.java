package com.altamira.extractor.parser.model;

import java.util.List;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalSqlAccess
 * (Python).
 *
 * <p>{@code inputHostVariables}/{@code outputHostVariables}/
 * {@code predicateHostVariables}/{@code selectedColumns} (Fase 15B3-C3-B):
 * aditivos, retrocompatibles con {@code hostVariables} (que se conserva sin
 * cambios). Poblados solo cuando {@link com.altamira.extractor.parser.sql.
 * EmbeddedSqlExtractor} puede demostrar la direccion/columna de forma
 * estructuralmente segura; vacios en cualquier otro caso (nunca inferidos
 * de forma dudosa). {@code hasIndicatorVariables} senala que la sentencia
 * contiene una variable indicadora (":VAR:IND"): en ese caso los cuatro
 * campos nuevos quedan vacios deliberadamente (ver
 * {@code SQL_INDICATOR_VARIABLE_DIRECTION_UNSUPPORTED} en
 * {@code semantic_effects_analyzer.py}).
 */
public record CanonicalSqlAccess(
        String table,
        TableAccessOperation operation,
        String predicateText,
        List<String> hostVariables,
        List<String> inputHostVariables,
        List<String> outputHostVariables,
        List<String> predicateHostVariables,
        List<String> selectedColumns,
        boolean hasIndicatorVariables,
        String sourceFile,
        Integer lineStart,
        Integer lineEnd,
        LocationKind locationKind) {
}
