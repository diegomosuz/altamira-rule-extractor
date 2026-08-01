package com.altamira.extractor.parser.model;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.List;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalStatement
 * (Python). Representacion plana: IF/EVALUATE anidados y sus ramas son
 * statements hermanos adicionales que apuntan a su padre via
 * parentStatementId y declaran branchKind (THEN/ELSE/WHEN/WHEN_OTHER).
 *
 * <p>statementId es deterministico y unico en todo el CanonicalProgram
 * (no solo dentro del Paragraph): {@code
 * <program_name>::<paragraph_name>::<ordinal>::<kind>} (ver
 * cobol.CanonicalProgramExtractor).
 *
 * <p>Campos de la Fase 3 de la ampliacion semantica (soporte nivel 88),
 * todos opcionales y aditivos:
 * <ul>
 *   <li>{@code conditionNameTarget}: para {@code kind=SET}, el nombre de
 *   la condicion 88 objetivo cuando el target resuelve de forma
 *   inequivoca contra {@code CanonicalProgram.conditionNames} Y el valor
 *   es literalmente TRUE o FALSE (nunca inferido por coincidencia de
 *   texto en un SET ordinario o de indice).</li>
 *   <li>{@code conditionSetValue}: {@code true}/{@code false} cuando
 *   {@code conditionNameTarget} esta resuelto (TO TRUE / TO FALSE),
 *   {@code null} en cualquier otro caso.</li>
 *   <li>{@code referencedConditionNames}: para {@code kind=IF} (la
 *   sentencia IF misma) y para el primer statement hijo de una rama
 *   {@code WHEN} de EVALUATE, los nombres de condicion 88 referenciados
 *   directamente y verificados contra {@code conditionNames} -- nunca
 *   toda variable leida se asume condicion 88.</li>
 * </ul>
 *
 * <p>Los tres campos de nivel 88 se anotan {@code @JsonInclude(NON_EMPTY)}:
 * cuando estan ausentes/vacios (el caso de CUALQUIER statement que no usa
 * esta extension) ni siquiera aparecen como clave en el JSON -- a
 * diferencia de los campos historicos nullable (p. ej. {@code
 * assignedLiteral}/{@code branchCondition}), que siguen serializando
 * {@code null} exactamente como antes de la Fase 3 (ninguna anotacion
 * nueva los afecta).
 */
public record CanonicalStatement(
        String statementId,
        StatementKind kind,
        String sourceText,
        String sourceFile,
        Integer lineStart,
        Integer lineEnd,
        LocationKind locationKind,
        String parentStatementId,
        BranchKind branchKind,
        String branchCondition,
        String expression,
        String normalizedExpression,
        List<String> operands,
        List<String> variablesRead,
        List<String> variablesWritten,
        List<String> targetDataItems,
        String assignedLiteral,
        List<String> targetParagraphs,
        List<CanonicalSqlAccess> sqlAccess,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) String conditionNameTarget,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) Boolean conditionSetValue,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) List<String> referencedConditionNames) {
}
