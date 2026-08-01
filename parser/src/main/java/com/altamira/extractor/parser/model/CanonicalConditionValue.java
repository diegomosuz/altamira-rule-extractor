package com.altamira.extractor.parser.model;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalConditionValue
 * (Python). Un unico VALUE (o intervalo VALUE ... THRU ...) de una
 * condicion nivel 88. throughValue es null para un VALUE simple; no-null
 * unicamente cuando ProLeap expone un intervalo THRU real
 * (ValueInterval.getToValueStmt() != null). sourceFile/line son nullable:
 * ver LocationKind.
 */
public record CanonicalConditionValue(
        String value,
        String throughValue,
        String sourceFile,
        Integer line,
        LocationKind locationKind) {
}
