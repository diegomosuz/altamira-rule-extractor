package com.altamira.extractor.parser.model;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalLinkageDataItem
 * (Python). Modelado por una ruta separada de {@code CanonicalDataItem}
 * (que sigue representando exclusivamente WORKING-STORAGE): LINKAGE
 * SECTION describe la interfaz potencial de un programa, nunca se mezcla
 * con {@code CanonicalProgram.dataItems}.
 */
public record CanonicalLinkageDataItem(
        String name,
        String qualifiedName,
        int level,
        String parentQualifiedName,
        String pic,
        String usage,
        String sourceFile,
        Integer line,
        LocationKind locationKind) {
}
