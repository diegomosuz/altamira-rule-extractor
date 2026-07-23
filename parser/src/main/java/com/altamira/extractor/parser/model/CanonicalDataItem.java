package com.altamira.extractor.parser.model;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalDataItem
 * (Python). sourceFile/line son nullable: ver LocationKind.
 */
public record CanonicalDataItem(
        String name,
        String qualifiedName,
        int level,
        String pic,
        String usage,
        String sourceFile,
        Integer line,
        LocationKind locationKind) {
}
