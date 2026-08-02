package com.altamira.extractor.parser.model;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalEntryParameter
 * (Python). Un parametro formal de {@code PROCEDURE DIVISION USING}.
 * {@code linkageItemQualifiedName} es {@code null} cuando el nombre no
 * resuelve de forma inequivoca contra {@code CanonicalProgram.linkageDataItems}
 * -- nunca se inventa una definicion.
 */
public record CanonicalEntryParameter(
        int ordinal,
        String name,
        String qualifiedName,
        String linkageItemQualifiedName,
        CallPassingMode passingMode,
        String sourceFile,
        Integer line,
        LocationKind locationKind) {
}
