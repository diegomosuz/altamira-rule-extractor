package com.altamira.extractor.parser.model;

import java.util.List;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalSqlAccess
 * (Python).
 */
public record CanonicalSqlAccess(
        String table,
        TableAccessOperation operation,
        String predicateText,
        List<String> hostVariables,
        String sourceFile,
        Integer lineStart,
        Integer lineEnd,
        LocationKind locationKind) {
}
