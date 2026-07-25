package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.CanonicalParagraph;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.StatementKind;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

/**
 * Corrige el bug donde `CanonicalStatement.normalizedExpression()` quedaba
 * SIEMPRE null para IF/EVALUATE/COMPUTE (StatementExtractor pasaba un
 * literal `null` en los tres sitios, incondicionalmente). Parsea
 * fixtures/normalized_expression.cbl (real, via ProLeap) una sola vez;
 * ningun test aqui construye un CanonicalStatement a mano.
 */
class StatementExtractorNormalizedExpressionTest {

    private static CanonicalProgram program;

    @BeforeAll
    static void parseOnce() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("normalized_expression.cbl"), RequestedFormat.FIXED, List.of(),
                StandardCharsets.UTF_8);
        program = new CanonicalProgramExtractor().extract(
                Fixtures.path("normalized_expression.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/normalized_expression.cbl",
                "b".repeat(64),
                "a".repeat(64));
    }

    private static CanonicalParagraph paragraph(String name) {
        return program.paragraphs().stream()
                .filter(p -> p.name().equals(name))
                .findFirst()
                .orElseThrow(() -> new AssertionError("paragraph not found: " + name));
    }

    private static CanonicalStatement firstOfKind(CanonicalParagraph p, StatementKind kind) {
        return p.statements().stream()
                .filter(s -> s.kind() == kind)
                .findFirst()
                .orElseThrow(() -> new AssertionError("no " + kind + " in " + p.name()));
    }

    @Test
    void ifWithSimpleExpressionIsNormalized() {
        CanonicalStatement ifStmt = firstOfKind(paragraph("CHECK-SALDO-PARA"), StatementKind.IF);
        assertNotNull(ifStmt.expression());
        assertEquals(ifStmt.expression().strip(), ifStmt.normalizedExpression());
    }

    @Test
    void evaluateProducesNonNullNormalizedExpression() {
        CanonicalStatement evaluate = firstOfKind(paragraph("ESTADO-EVALUATE-PARA"), StatementKind.EVALUATE);
        assertNotNull(evaluate.expression());
        assertNotNull(evaluate.normalizedExpression());
        assertEquals(evaluate.expression().strip(), evaluate.normalizedExpression());
    }

    @Test
    void evaluateExpressionComesFromSelectNotFromSourceText() {
        // Prueba NEGATIVA de que expression no es (ni deriva de) sourceText:
        // sourceText abarca el EVALUATE completo (todas las ramas WHEN),
        // mientras que expression es unicamente el sujeto del Select
        // (evaluateStmt.getSelect()), ya usado antes para operands().
        CanonicalStatement evaluate = firstOfKind(paragraph("ESTADO-EVALUATE-PARA"), StatementKind.EVALUATE);
        assertEquals("WS-ESTADO", evaluate.expression());
        assertEquals(
                List.of("WS-ESTADO"), evaluate.operands(), "operands() no debe cambiar por esta correccion");
        assertNotNull(evaluate.sourceText());
        assertTrue(evaluate.sourceText().contains("WHEN"), "sourceText si debe abarcar las ramas WHEN");
        assertFalse(evaluate.expression().contains("WHEN"), "expression NO debe abarcar las ramas WHEN");
        assertTrue(
                evaluate.expression().length() < evaluate.sourceText().length(),
                "expression debe ser mas corta que sourceText (no es una copia del statement completo)");
    }

    @Test
    void computeProducesNonNullNormalizedExpression() {
        CanonicalStatement compute = firstOfKind(paragraph("CALCULO-PARA"), StatementKind.COMPUTE);
        assertNotNull(compute.expression());
        assertNotNull(compute.normalizedExpression());
        assertEquals(compute.expression().strip(), compute.normalizedExpression());
    }

    @Test
    void outerWhitespaceIsStrippedFromNormalizedExpression() {
        assertEquals("WS-SALDO < 0", StatementExtractor.normalizeExpression("  WS-SALDO < 0  "));
    }

    @Test
    void quotedLiteralIsPreservedExactlyIncludingInternalWhitespaceAndCase() {
        CanonicalStatement ifStmt = firstOfKind(paragraph("LITERAL-INTERNO-PARA"), StatementKind.IF);
        assertNotNull(ifStmt.normalizedExpression());
        // El literal 'A   B' (con multiples espacios internos) debe
        // conservarse exactamente: ni colapsado ni pasado a mayusculas/
        // minusculas distintas de las originales.
        assertTrue(
                ifStmt.normalizedExpression().contains("'A   B'"),
                "esperaba el literal 'A   B' preservado exactamente, obtuve: "
                        + ifStmt.normalizedExpression());
    }

    @Test
    void nullExpressionStaysNull() {
        assertNull(StatementExtractor.normalizeExpression(null));
    }

    @Test
    void emptyOrWhitespaceOnlyExpressionBecomesNull() {
        assertNull(StatementExtractor.normalizeExpression(""));
        assertNull(StatementExtractor.normalizeExpression("   "));
    }

    @Test
    void moveStatementHasNoExpressionByDesignAndStaysNull() {
        // MOVE nunca tuvo (ni tiene ahora) un campo expression estructural:
        // confirma que la correccion no inventa un valor donde no hay uno.
        CanonicalStatement move = firstOfKind(paragraph("CHECK-SALDO-PARA"), StatementKind.MOVE);
        assertNull(move.expression());
        assertNull(move.normalizedExpression());
    }
}
