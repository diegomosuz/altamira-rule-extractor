package com.altamira.extractor.parser.model;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.List;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalProgram
 * (Python).
 *
 * <p>schemaVersion (Fase 3 de la ampliacion semantica -- soporte nivel
 * 88 -- ver docs/LEVEL_88_SUPPORT.md) es condicional, calculado por
 * {@code CanonicalProgramExtractor}: {@code "1.0"} para un programa que
 * no usa NINGUNA extension de nivel 88 (conditionNames vacia, y ningun
 * CanonicalStatement con conditionNameTarget/referencedConditionNames
 * poblados); {@code "1.1"} en cuanto CUALQUIERA de esas extensiones esta
 * realmente presente; {@code "1.2"} en cuanto aparece CALL/LINKAGE (Fase
 * 6); {@code "1.3"} en cuanto aparece algun {@code
 * StatementKind.PROGRAM_TERMINATION} (GOBACK/STOP RUN/EXIT PROGRAM, Fase
 * 7b -- ver docs/INTERPROCEDURAL_PROPAGATION.md), version tipica de
 * cualquier programa COBOL completo real. conditionNames se anota
 * {@code @JsonInclude(NON_EMPTY)}: para un programa "1.0" la clave ni
 * siquiera aparece en el JSON (nunca {@code "condition_names": []}), asi
 * que el documento completo es byte a byte identico al formato previo a
 * la Fase 3. sourceFile es la identidad relativa del programa principal,
 * siempre conocida (nunca nullable, a diferencia de los elementos
 * anidados).
 *
 * <p>conditionNames es deliberadamente una lista separada (nunca un campo
 * dentro de CanonicalDataItem): preserva byte a byte dataItems para
 * programas con o sin nivel 88 (cada CanonicalDataItem de nivel 88 sigue
 * existiendo exactamente como antes, sin cambios), y anade como maximo una
 * unica clave nueva por programa en vez de una clave nueva por cada data
 * item.
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
        @JsonInclude(JsonInclude.Include.NON_EMPTY) List<CanonicalConditionName> conditionNames,
        List<CanonicalParagraph> paragraphs,
        List<String> warnings,
        List<String> unsupportedConstructs,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) List<CanonicalLinkageDataItem> linkageDataItems,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) List<CanonicalEntryParameter> entryParameters,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) String entryReturningDataItem) {

    /**
     * Constructor de compatibilidad para tests historicos que construyen
     * {@code CanonicalProgram} sin los tres campos de la Fase 6 (LINKAGE
     * SECTION/PROCEDURE DIVISION USING ausentes): delega en el
     * constructor canonico con listas vacias y
     * {@code entryReturningDataItem=null}.
     */
    public CanonicalProgram(
            String schemaVersion,
            String programName,
            String sourceFile,
            String sourceHash,
            String sourcePackageHash,
            SourceFormat sourceFormat,
            String encoding,
            List<CanonicalDataItem> dataItems,
            List<CanonicalConditionName> conditionNames,
            List<CanonicalParagraph> paragraphs,
            List<String> warnings,
            List<String> unsupportedConstructs) {
        this(
                schemaVersion, programName, sourceFile, sourceHash, sourcePackageHash,
                sourceFormat, encoding, dataItems, conditionNames, paragraphs, warnings,
                unsupportedConstructs, List.of(), List.of(), null);
    }
}
