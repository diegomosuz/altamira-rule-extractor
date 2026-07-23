package com.altamira.extractor.parser.model;

import java.util.List;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalProgram
 * (Python). schemaVersion es siempre "1.0". sourceFile es la identidad
 * relativa del programa principal, siempre conocida (nunca nullable, a
 * diferencia de los elementos anidados).
 */
public record CanonicalProgram(
        String schemaVersion,
        String programName,
        String sourceFile,
        String sourceHash,
        String sourcePackageHash,
        SourceFormat sourceFormat,
        String encoding,
        List<CanonicalDataItem> dataItems,
        List<CanonicalParagraph> paragraphs,
        List<String> warnings,
        List<String> unsupportedConstructs) {
}
