package com.altamira.extractor.parser.model;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalDataItem
 * (Python). sourceFile/line son nullable: ver LocationKind. declaredValue
 * es nullable: solo se puebla para una clausula VALUE simple (un unico
 * ValueInterval, sin THRU); nunca el valor efectivo/runtime (Fase
 * 15B3-C5-B).
 */
public record CanonicalDataItem(
        String name,
        String qualifiedName,
        int level,
        String pic,
        String usage,
        String declaredValue,
        String sourceFile,
        Integer line,
        LocationKind locationKind) {
}
