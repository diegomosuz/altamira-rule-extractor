package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.CanonicalParagraph;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.StatementKind;
import java.nio.charset.StandardCharsets;
import java.util.Comparator;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * Matriz de complejidad (Prompt 15), caso "multiples decisiones por
 * parrafo": reutiliza {@code fixtures/comprehensive.cbl}, cuyo parrafo
 * VALIDAR-MONTO ya contiene un IF anidado -- dos decisiones IF reales en
 * un mismo parrafo -- de forma inequivoca. A diferencia de otros tests
 * que recorren ese IF anidado de forma indirecta (p. ej. para probar
 * branch_kind o location), este test aisla explicitamente el caso de la
 * matriz: dos decisiones distintas, con IDs, ubicaciones y predicados
 * normalizados propios, en orden deterministico, y agregados del
 * parrafo consistentes con la union real de sus statements.
 */
class MultipleDecisionsPerParagraphTest {

    private static CanonicalProgram parseOnce() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("comprehensive.cbl"), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8);
        return new CanonicalProgramExtractor().extract(
                Fixtures.path("comprehensive.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/comprehensive.cbl",
                "b".repeat(64),
                "a".repeat(64));
    }

    private static CanonicalParagraph validarMonto(CanonicalProgram program) {
        return program.paragraphs().stream()
                .filter(p -> p.name().equals("VALIDAR-MONTO"))
                .findFirst()
                .orElseThrow();
    }

    @Test
    void paragraphContainsAtLeastTwoDistinctIfDecisions() throws Exception {
        CanonicalParagraph paragraph = validarMonto(parseOnce());

        List<CanonicalStatement> decisions = paragraph.statements().stream()
                .filter(s -> s.kind() == StatementKind.IF)
                .sorted(Comparator.comparingInt(CanonicalStatement::lineStart))
                .toList();

        assertEquals(2, decisions.size(), "VALIDAR-MONTO debe tener exactamente 2 decisiones IF: "
                + "la exterior (WS-MONTO > WS-LIMITE) y la anidada (WS-CONTADOR > 0)");

        CanonicalStatement outer = decisions.get(0);
        CanonicalStatement inner = decisions.get(1);

        // IDs distintos.
        assertNotEquals(outer.statementId(), inner.statementId());

        // Ubicaciones (statement paths / lineas) distintas.
        assertNotEquals(outer.lineStart(), inner.lineStart());
        assertTrue(outer.lineStart() < inner.lineStart(), "orden deterministico: la exterior precede a la anidada");

        // La anidada declara a la exterior como padre estructural.
        assertEquals(outer.statementId(), inner.parentStatementId());

        // Predicados normalizados distintos y no vacios.
        assertNotEquals(outer.normalizedExpression(), inner.normalizedExpression());
        assertTrue(outer.normalizedExpression().contains("WS-MONTO"));
        assertTrue(outer.normalizedExpression().contains("WS-LIMITE"));
        assertTrue(inner.normalizedExpression().contains("WS-CONTADOR"));
    }

    @Test
    void decisionOrderIsDeterministicAcrossRuns() throws Exception {
        CanonicalParagraph first = validarMonto(parseOnce());
        CanonicalParagraph second = validarMonto(parseOnce());

        List<String> firstOrder = first.statements().stream()
                .filter(s -> s.kind() == StatementKind.IF)
                .map(CanonicalStatement::statementId)
                .toList();
        List<String> secondOrder = second.statements().stream()
                .filter(s -> s.kind() == StatementKind.IF)
                .map(CanonicalStatement::statementId)
                .toList();

        assertEquals(firstOrder, secondOrder);
    }

    @Test
    void paragraphAggregatesAreTheUnionOfItsStatements() throws Exception {
        CanonicalParagraph paragraph = validarMonto(parseOnce());

        List<String> expectedRead = paragraph.statements().stream()
                .flatMap(s -> s.variablesRead().stream())
                .distinct()
                .sorted()
                .toList();
        List<String> expectedWritten = paragraph.statements().stream()
                .flatMap(s -> s.variablesWritten().stream())
                .distinct()
                .sorted()
                .toList();

        List<String> actualRead = paragraph.variablesRead().stream().sorted().toList();
        List<String> actualWritten = paragraph.variablesWritten().stream().sorted().toList();

        assertEquals(expectedRead, actualRead,
                "variablesRead del parrafo debe ser exactamente la union de sus statements, "
                        + "incluidas ambas decisiones IF");
        assertEquals(expectedWritten, actualWritten);

        // Ambas decisiones realmente aportan a la union (no solo los MOVE).
        assertTrue(actualRead.contains("WS-MONTO"));
        assertTrue(actualRead.contains("WS-LIMITE"));
        assertTrue(actualRead.contains("WS-CONTADOR"));
    }
}
