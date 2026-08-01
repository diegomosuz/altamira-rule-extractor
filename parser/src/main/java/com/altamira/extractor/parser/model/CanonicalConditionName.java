package com.altamira.extractor.parser.model;

import java.util.List;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalConditionName
 * (Python). Representa una condicion nivel 88 (condition-name) declarada
 * bajo un data item padre. values nunca esta vacia: si ProLeap no expone
 * ningun VALUE demostrable, la condicion se omite del artefacto y se
 * registra en unsupported_constructs en su lugar (ver
 * CanonicalProgramExtractor) -- nunca se inventa un valor.
 *
 * <p>parentName/parentQualifiedName identifican el data item cuyo VALUE
 * satisface la condicion, resuelto via
 * DataDescriptionEntry.getParentDataDescriptionEntryGroup() (verificado
 * empiricamente: para una condicion 88 devuelve el data item padre
 * inmediato, no un ancestro de grupo mas lejano). sourceFile/line son
 * nullable: ver LocationKind.
 */
public record CanonicalConditionName(
        String name,
        String qualifiedName,
        String parentName,
        String parentQualifiedName,
        List<CanonicalConditionValue> values,
        String sourceFile,
        Integer line,
        LocationKind locationKind) {
}
