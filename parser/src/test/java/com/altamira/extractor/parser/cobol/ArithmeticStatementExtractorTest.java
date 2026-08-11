package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
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
 * Fase 15B3-C2-B1: cobertura estructural real (JAR de ProLeap, sin mocks)
 * de {@code convertAdd}/{@code convertSubtract}/{@code convertMultiply}/
 * {@code convertDivide} sobre {@code fixtures/arithmetic.cbl}. Parsea el
 * fixture una sola vez; ningun test aqui construye un CanonicalStatement a
 * mano.
 */
class ArithmeticStatementExtractorTest {

    private static CanonicalProgram program;

    @BeforeAll
    static void parseOnce() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("arithmetic.cbl"), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8);
        program = new CanonicalProgramExtractor().extract(
                Fixtures.path("arithmetic.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/arithmetic.cbl",
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
    void addBasicCapturesTargetAndRead() {
        CanonicalStatement add = firstOfKind(paragraph("ADD-BASIC-PARA"), StatementKind.ADD);
        assertEquals(List.of("WS-B"), add.targetDataItems());
        assertEquals(List.of("WS-B"), add.variablesWritten());
        assertTrue(add.variablesRead().contains("WS-A"));
        assertNull(add.expression());
        assertNull(add.normalizedExpression());
        assertNull(add.assignedLiteral());
    }

    @Test
    void addMultiCapturesMultipleFromOperands() {
        CanonicalStatement add = firstOfKind(paragraph("ADD-MULTI-PARA"), StatementKind.ADD);
        assertEquals(List.of("WS-C"), add.targetDataItems());
        assertTrue(add.variablesRead().contains("WS-A"));
        assertTrue(add.variablesRead().contains("WS-B"));
    }

    @Test
    void addGivingCapturesDistinctTargetNeverTheToOperand() {
        CanonicalStatement add = firstOfKind(paragraph("ADD-GIVING-PARA"), StatementKind.ADD);
        assertEquals(List.of("WS-C"), add.targetDataItems(), "GIVING debe escribir WS-C, nunca WS-B");
        assertTrue(add.variablesRead().contains("WS-A"));
        assertTrue(add.variablesRead().contains("WS-B"));
    }

    @Test
    void addGivingRoundedRegistersDiagnosticButStillCapturesTarget() {
        CanonicalStatement add = firstOfKind(paragraph("ADD-GIVING-ROUNDED-PARA"), StatementKind.ADD);
        assertEquals(List.of("WS-C"), add.targetDataItems());
        assertTrue(
                program.unsupportedConstructs().stream()
                        .anyMatch(w -> w.contains("ROUNDED") && w.contains("ADD")),
                "esperaba diagnostico ROUNDED para ADD, obtuve: " + program.unsupportedConstructs());
    }

    @Test
    void addOnSizeErrorRegistersDiagnosticButProcessesHappyPath() {
        CanonicalStatement add = firstOfKind(paragraph("ADD-ON-SIZE-ERROR-PARA"), StatementKind.ADD);
        assertEquals(List.of("WS-B"), add.targetDataItems(), "camino feliz ADD sigue interpretado");
        assertTrue(
                program.unsupportedConstructs().stream()
                        .anyMatch(w -> w.contains("ON SIZE ERROR") && w.contains("ADD")),
                "esperaba diagnostico ON SIZE ERROR para ADD, obtuve: " + program.unsupportedConstructs());
    }

    @Test
    void addCorrespondingIsRecognizedNotInterpreted() {
        CanonicalStatement add = firstOfKind(paragraph("ADD-CORRESPONDING-PARA"), StatementKind.ADD);
        assertEquals(List.of(), add.targetDataItems());
        assertEquals(List.of(), add.variablesRead());
        assertTrue(
                program.unsupportedConstructs().stream()
                        .anyMatch(w -> w.contains("ADD CORRESPONDING")),
                "esperaba diagnostico ADD CORRESPONDING, obtuve: " + program.unsupportedConstructs());
    }

    @Test
    void subtractBasicCapturesTargetAndRead() {
        CanonicalStatement subtract = firstOfKind(paragraph("SUBTRACT-BASIC-PARA"), StatementKind.SUBTRACT);
        assertEquals(List.of("WS-B"), subtract.targetDataItems());
        assertTrue(subtract.variablesRead().contains("WS-A"));
        assertNull(subtract.expression());
    }

    @Test
    void subtractGivingCapturesDistinctTarget() {
        CanonicalStatement subtract = firstOfKind(paragraph("SUBTRACT-GIVING-PARA"), StatementKind.SUBTRACT);
        assertEquals(List.of("WS-C"), subtract.targetDataItems());
        assertTrue(subtract.variablesRead().contains("WS-A"));
        assertTrue(subtract.variablesRead().contains("WS-B"));
    }

    @Test
    void subtractCorrespondingIsRecognizedNotInterpreted() {
        CanonicalStatement subtract =
                firstOfKind(paragraph("SUBTRACT-CORRESPONDING-PARA"), StatementKind.SUBTRACT);
        assertEquals(List.of(), subtract.targetDataItems());
        assertTrue(
                program.unsupportedConstructs().stream()
                        .anyMatch(w -> w.contains("SUBTRACT CORRESPONDING")),
                "esperaba diagnostico SUBTRACT CORRESPONDING, obtuve: " + program.unsupportedConstructs());
    }

    @Test
    void multiplyBasicCapturesTarget() {
        CanonicalStatement multiply = firstOfKind(paragraph("MULTIPLY-BASIC-PARA"), StatementKind.MULTIPLY);
        assertEquals(List.of("WS-B"), multiply.targetDataItems());
        assertTrue(multiply.variablesRead().contains("WS-A"));
    }

    @Test
    void multiplyGivingCapturesTargetAndBothOperandsRead() {
        CanonicalStatement multiply = firstOfKind(paragraph("MULTIPLY-GIVING-PARA"), StatementKind.MULTIPLY);
        assertEquals(List.of("WS-C"), multiply.targetDataItems());
        assertTrue(multiply.variablesRead().contains("WS-A"));
        assertTrue(multiply.variablesRead().contains("WS-B"));
    }

    @Test
    void divideIntoCapturesTarget() {
        CanonicalStatement divide = firstOfKind(paragraph("DIVIDE-INTO-PARA"), StatementKind.DIVIDE);
        assertEquals(List.of("WS-B"), divide.targetDataItems());
        assertTrue(divide.variablesRead().contains("WS-A"));
    }

    @Test
    void divideIntoGivingCapturesTarget() {
        CanonicalStatement divide = firstOfKind(paragraph("DIVIDE-INTO-GIVING-PARA"), StatementKind.DIVIDE);
        assertEquals(List.of("WS-C"), divide.targetDataItems());
        assertTrue(divide.variablesRead().contains("WS-A"));
        assertTrue(divide.variablesRead().contains("WS-B"));
    }

    @Test
    void divideByGivingCapturesTarget() {
        CanonicalStatement divide = firstOfKind(paragraph("DIVIDE-BY-GIVING-PARA"), StatementKind.DIVIDE);
        assertEquals(List.of("WS-C"), divide.targetDataItems());
        assertTrue(divide.variablesRead().contains("WS-A"));
        assertTrue(divide.variablesRead().contains("WS-B"));
    }

    @Test
    void divideRemainderCapturesQuotientOnlyNeverTheRemainderTarget() {
        CanonicalStatement divide = firstOfKind(paragraph("DIVIDE-REMAINDER-PARA"), StatementKind.DIVIDE);
        assertEquals(
                List.of("WS-C"), divide.targetDataItems(),
                "el cociente (WS-C) debe ser el unico target -- WS-D (resto) NUNCA se agrega, para "
                        + "evitar writes=[quotient,remainder] ambiguo");
        assertFalse(divide.targetDataItems().contains("WS-D"));
        assertTrue(
                program.unsupportedConstructs().stream()
                        .anyMatch(w -> w.contains("REMAINDER")),
                "esperaba diagnostico REMAINDER, obtuve: " + program.unsupportedConstructs());
    }

    @Test
    void noAbsolutePathsAndArithmeticStatementsAreUniquelyIdentified() {
        CanonicalStatement add = firstOfKind(paragraph("ADD-BASIC-PARA"), StatementKind.ADD);
        assertTrue(add.statementId().startsWith("ARITH001::ADD-BASIC-PARA::"));
    }
}
