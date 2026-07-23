package com.altamira.extractor.parser.model;

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
        List<CanonicalSqlAccess> sqlAccess) {
}
