package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
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
 * Regresion real (Fase 3, checkpoint correctivo v1.18.3): {@code
 * ParserRuleContext#getText()} concatena unicamente el texto de tokens
 * VISIBLES sin ningun separador (el espacio del COBOL fuente vive en el
 * canal oculto de ANTLR), produciendo expresiones pegadas como {@code
 * "SQLCODENOT=0"} en vez de {@code "SQLCODE NOT = 0"} para IF/EVALUATE
 * subject/COMPUTE. Cubre la matriz minima exigida (checkpoint seccion
 * 11): ningun limite de palabra clave/operador desaparece, ningun
 * espacio se inventa DENTRO de un literal entre comillas, y {@code
 * legacyExpression} reconstruye exactamente el texto pegado que {@code
 * getText()} habria producido antes de esta correccion (unica entrada
 * confiable para el mecanismo de compatibilidad de candidate_id, ver
 * {@code enhanced_candidate_integration.py}).
 */
class TokenBoundaryExpressionTest {

    private static CanonicalProgram program;

    @BeforeAll
    static void parseOnce() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("token_boundary_expressions.cbl"), RequestedFormat.FIXED, List.of(),
                StandardCharsets.UTF_8);
        program = new CanonicalProgramExtractor().extract(
                Fixtures.path("token_boundary_expressions.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/token_boundary_expressions.cbl",
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

    // --- 1-2: NOT = (el caso original que motivo esta fase) ---

    @Test
    void ifSqlcodeNotEqualsZeroPreservesTokenBoundaries() {
        CanonicalStatement ifStmt = firstOfKind(paragraph("IF-SQLCODE-NOT-PARA"), StatementKind.IF);
        assertEquals("SQLCODE NOT = 0", ifStmt.expression());
        assertEquals("SQLCODE NOT = 0", ifStmt.normalizedExpression());
        assertEquals("SQLCODENOT=0", ifStmt.legacyExpression());
    }

    @Test
    void ifFlagNotEqualsQuotedLiteralPreservesTokenBoundaries() {
        CanonicalStatement ifStmt = firstOfKind(paragraph("IF-FLAG-NOT-PARA"), StatementKind.IF);
        assertEquals("WS-REINTENTO-OK NOT = 'S'", ifStmt.expression());
        assertEquals("WS-REINTENTO-OK NOT = 'S'", ifStmt.normalizedExpression());
        assertEquals("WS-REINTENTO-OKNOT='S'", ifStmt.legacyExpression());
    }

    // --- 3-7: operadores relacionales simples ---

    @Test
    void ifEqualsPreservesTokenBoundaries() {
        assertEquals("A = 1", firstOfKind(paragraph("IF-EQ-PARA"), StatementKind.IF).expression());
    }

    @Test
    void ifGreaterThanPreservesTokenBoundaries() {
        assertEquals("A > 1", firstOfKind(paragraph("IF-GT-PARA"), StatementKind.IF).expression());
    }

    @Test
    void ifGreaterOrEqualPreservesTokenBoundaries() {
        assertEquals("A >= 1", firstOfKind(paragraph("IF-GE-PARA"), StatementKind.IF).expression());
    }

    @Test
    void ifLessThanPreservesTokenBoundaries() {
        assertEquals("A < 1", firstOfKind(paragraph("IF-LT-PARA"), StatementKind.IF).expression());
    }

    @Test
    void ifLessOrEqualPreservesTokenBoundaries() {
        assertEquals("A <= 1", firstOfKind(paragraph("IF-LE-PARA"), StatementKind.IF).expression());
    }

    // --- 8-9: condiciones compuestas AND/OR ---

    @Test
    void ifAndPreservesTokenBoundaries() {
        CanonicalStatement ifStmt = firstOfKind(paragraph("IF-AND-PARA"), StatementKind.IF);
        assertEquals("A > 0 AND B < 10", ifStmt.expression());
        assertTrue(ifStmt.expression().contains(" AND "), "AND debe seguir siendo una palabra delimitada");
    }

    @Test
    void ifOrPreservesTokenBoundaries() {
        CanonicalStatement ifStmt = firstOfKind(paragraph("IF-OR-PARA"), StatementKind.IF);
        assertEquals("A = 1 OR B = 2", ifStmt.expression());
        assertTrue(ifStmt.expression().contains(" OR "), "OR debe seguir siendo una palabra delimitada");
    }

    // --- 10-12: EVALUATE subject / WHEN branch / EVALUATE TRUE ---

    @Test
    void evaluateBareSubjectIsSingleTokenUnaffected() {
        CanonicalStatement evaluate = firstOfKind(paragraph("EVALUATE-BARE-PARA"), StatementKind.EVALUATE);
        assertEquals("SQLCODE", evaluate.expression());
        assertEquals("SQLCODE", evaluate.legacyExpression());
    }

    @Test
    void evaluateWhenPlus100BranchConditionRemainsCleanComparison() {
        // Preserva EXACTAMENTE el fix v1.18.2 (Ciclo 4): la correccion de
        // limites de token en el sujeto (Fase 3) nunca debe regresar a
        // "SQLCODE" bare ni glue el operador de comparacion.
        CanonicalParagraph p = paragraph("EVALUATE-PLUS100-PARA");
        CanonicalStatement whenPlus100 = p.statements().stream()
                .filter(s -> s.kind() == StatementKind.MOVE && "1".equals(s.assignedLiteral()))
                .findFirst()
                .orElseThrow();
        assertEquals("SQLCODE = 100", whenPlus100.branchCondition());
        assertFalse(whenPlus100.branchCondition().contains("SQLCODENOT"));
    }

    @Test
    void evaluateTrueWithConditionNameFallsBackToBareSubjectNeverGlued() {
        CanonicalStatement evaluate =
                firstOfKind(paragraph("EVALUATE-TRUE-CONDNAME-PARA"), StatementKind.EVALUATE);
        assertEquals("TRUE", evaluate.expression());
        CanonicalParagraph p = paragraph("EVALUATE-TRUE-CONDNAME-PARA");
        CanonicalStatement whenCondName = p.statements().stream()
                .filter(s -> s.kind() == StatementKind.MOVE && "1".equals(s.assignedLiteral()))
                .findFirst()
                .orElseThrow();
        // condition-name no es una comparacion contra literal puro:
        // buildBranchCondition() devuelve null y el llamador cae al
        // fallback existente (sujeto crudo del EVALUATE, "TRUE") -- mismo
        // comportamiento que EvaluateBranchConditionTest ya prueba desde
        // Ciclo 4 v1.18.2, sin cambios por esta fase.
        assertEquals("TRUE", whenCondName.branchCondition());
    }

    // --- 13-14: COMPUTE ---

    @Test
    void computeMultiplyPreservesTokenBoundaries() {
        CanonicalStatement compute = firstOfKind(paragraph("COMPUTE-MULTIPLY-PARA"), StatementKind.COMPUTE);
        assertEquals("B * C", compute.expression());
        assertEquals("B * C", compute.normalizedExpression());
        assertEquals("B*C", compute.legacyExpression());
    }

    @Test
    void computeParenthesizedPreservesTokenBoundariesIfAcceptedByGrammar() {
        CanonicalStatement compute = firstOfKind(paragraph("COMPUTE-PAREN-PARA"), StatementKind.COMPUTE);
        assertEquals("( B + C ) / D", compute.expression());
        assertEquals("(B+C)/D", compute.legacyExpression());
    }

    // --- 15: literal entre comillas con espacio interno ---

    @Test
    void quotedLiteralWithInternalSpaceIsNeverAlteredByTokenSeparators() {
        CanonicalStatement ifStmt =
                firstOfKind(paragraph("IF-QUOTED-LITERAL-SPACE-PARA"), StatementKind.IF);
        assertEquals("WS-NOTA = 'HELLO WORLD'", ifStmt.expression());
        // Nunca colapsa ni normaliza el espacio INTERNO al literal (es
        // texto de un unico token, no un separador insertado por el
        // renderer): 'HELLO WORLD' debe seguir teniendo exactamente un
        // espacio, igual que en el fuente.
        assertTrue(ifStmt.expression().contains("'HELLO WORLD'"));
        assertEquals("WS-NOTA='HELLO WORLD'", ifStmt.legacyExpression());
    }

    // --- seccion 12: no reinterpretacion semantica ---

    @Test
    void neverConvertsNotEqualsIntoDiamondOperator() {
        CanonicalStatement ifStmt = firstOfKind(paragraph("IF-SQLCODE-NOT-PARA"), StatementKind.IF);
        assertFalse(
                ifStmt.expression().contains("<>"),
                "NOT = nunca debe convertirse a <> (eso solo ocurre en buildBranchCondition para WHEN NOT, "
                        + "un mecanismo distinto y ya existente, no en la expresion cruda de IF)");
    }

    @Test
    void neverRemovesParenthesesFromComputeExpression() {
        CanonicalStatement compute = firstOfKind(paragraph("COMPUTE-PAREN-PARA"), StatementKind.COMPUTE);
        assertTrue(compute.expression().contains("("));
        assertTrue(compute.expression().contains(")"));
    }

    @Test
    void neverFoldsPlus100ToBareLiteralOutsideExistingEvaluateBranchLogic() {
        // El sujeto EVALUATE (expression) nunca debe contener el literal de
        // ninguna rama WHEN -- esa fusion es responsabilidad exclusiva y ya
        // existente de buildBranchCondition (branch_condition), nunca de
        // esta correccion de limites de token.
        CanonicalStatement evaluate =
                firstOfKind(paragraph("EVALUATE-PLUS100-PARA"), StatementKind.EVALUATE);
        assertEquals("SQLCODE", evaluate.expression());
        assertFalse(evaluate.expression().contains("100"));
    }
}
