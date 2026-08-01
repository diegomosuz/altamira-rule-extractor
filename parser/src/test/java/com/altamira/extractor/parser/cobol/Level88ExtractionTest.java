package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.CanonicalConditionName;
import com.altamira.extractor.parser.model.CanonicalConditionValue;
import com.altamira.extractor.parser.model.CanonicalParagraph;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.StatementKind;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

/**
 * Parsea fixtures/level-88.cbl una sola vez y verifica captura de
 * condition-names (Fase 3 de la ampliacion semantica): VALUE simple,
 * multiples VALUE, THRU, condiciones bajo grupos distintos, SET TO
 * TRUE/FALSE, IF/EVALUATE con referencias verificadas, y que SET
 * ordinario (sobre un item que NO es condicion 88) nunca se confunde.
 */
class Level88ExtractionTest {

    private static CanonicalProgram program;

    @BeforeAll
    static void parseOnce() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("level-88.cbl"), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8);
        program = new CanonicalProgramExtractor().extract(
                Fixtures.path("level-88.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/level-88.cbl",
                "b".repeat(64),
                "a".repeat(64));
    }

    private static CanonicalConditionName condition(String name) {
        return program.conditionNames().stream()
                .filter(c -> c.name().equals(name))
                .findFirst()
                .orElseThrow(() -> new AssertionError("condition not found: " + name));
    }

    private static List<CanonicalStatement> statements() {
        return program.paragraphs().get(0).statements();
    }

    @Test
    void singleValueConditionIsCaptured() {
        CanonicalConditionName condition = condition("COD-OPERACION-VALIDA");
        assertEquals("WS-COD-RETORNO", condition.parentName());
        assertEquals("WS-COD-RETORNO", condition.parentQualifiedName());
        assertEquals("WS-COD-RETORNO.COD-OPERACION-VALIDA", condition.qualifiedName());
        assertEquals(1, condition.values().size());
        CanonicalConditionValue value = condition.values().get(0);
        assertEquals("0000", value.value());
        assertNull(value.throughValue());
    }

    @Test
    void multipleValueConditionProducesOneValuePerLiteral() {
        CanonicalConditionName condition = condition("COD-MULTI-VALOR");
        List<String> values = condition.values().stream().map(CanonicalConditionValue::value).toList();
        assertEquals(List.of("01", "02", "03"), values);
        assertTrue(condition.values().stream().allMatch(v -> v.throughValue() == null));
    }

    @Test
    void thruConditionCapturesFromAndThroughValues() {
        CanonicalConditionName condition = condition("COD-RANGO-ERROR");
        assertEquals(1, condition.values().size());
        CanonicalConditionValue value = condition.values().get(0);
        assertEquals("0010", value.value());
        assertEquals("0019", value.throughValue());
    }

    @Test
    void multipleConditionsUnderSameParentAreAllCaptured() {
        assertEquals("WS-COD-RETORNO", condition("COD-OPERACION-VALIDA").parentName());
        assertEquals("WS-COD-RETORNO", condition("COD-CAMPO-INVALIDO").parentName());
        assertEquals("WS-COD-RETORNO", condition("COD-RANGO-ERROR").parentName());
    }

    @Test
    void conditionsUnderDifferentGroupsResolveDistinctParents() {
        CanonicalConditionName a = condition("SUBCAMPO-A-ACTIVO");
        CanonicalConditionName b = condition("SUBCAMPO-B-ACTIVO");
        assertEquals("WS-SUBCAMPO-A", a.parentName());
        assertEquals("WS-GRUPO-A.WS-SUBCAMPO-A", a.parentQualifiedName());
        assertEquals("WS-SUBCAMPO-B", b.parentName());
        assertEquals("WS-GRUPO-B.WS-SUBCAMPO-B", b.parentQualifiedName());
    }

    @Test
    void setConditionToTrueIsStructurallyRepresented() {
        CanonicalStatement set = statements().stream()
                .filter(s -> s.kind() == StatementKind.SET)
                .filter(s -> "COD-CAMPO-INVALIDO".equals(s.conditionNameTarget()))
                .findFirst().orElseThrow();
        assertEquals(Boolean.TRUE, set.conditionSetValue());
        // No se reescribe como MOVE: variablesWritten/targetDataItems se
        // conservan exactamente como antes.
        assertEquals(List.of("COD-CAMPO-INVALIDO"), set.variablesWritten());
        assertEquals(List.of("COD-CAMPO-INVALIDO"), set.targetDataItems());
    }

    @Test
    void setConditionToFalseIsStructurallyRepresented() {
        CanonicalStatement set = statements().stream()
                .filter(s -> s.kind() == StatementKind.SET)
                .filter(s -> "COD-OPERACION-VALIDA".equals(s.conditionNameTarget()))
                .findFirst().orElseThrow();
        assertEquals(Boolean.FALSE, set.conditionSetValue());
    }

    @Test
    void ordinarySetIsNeverConfusedWithConditionName() {
        CanonicalStatement set = statements().stream()
                .filter(s -> s.kind() == StatementKind.SET)
                .filter(s -> s.targetDataItems().equals(List.of("WS-INDICE")))
                .findFirst().orElseThrow();
        assertNull(set.conditionNameTarget());
        assertNull(set.conditionSetValue());
        assertEquals("1", set.assignedLiteral());
    }

    @Test
    void ifWithDirectConditionNameReferenceIsVerified() {
        CanonicalStatement ifStatement = statements().stream()
                .filter(s -> s.kind() == StatementKind.IF)
                .findFirst().orElseThrow();
        assertEquals(List.of("COD-CAMPO-INVALIDO"), ifStatement.referencedConditionNames());
    }

    @Test
    void evaluateWhenBranchesReferenceVerifiedConditionNames() {
        CanonicalStatement evaluate = statements().stream()
                .filter(s -> s.kind() == StatementKind.EVALUATE)
                .findFirst().orElseThrow();
        List<CanonicalStatement> children = statements().stream()
                .filter(s -> evaluate.statementId().equals(s.parentStatementId()))
                .toList();

        boolean foundValidBranch = children.stream()
                .anyMatch(s -> s.referencedConditionNames().contains("COD-OPERACION-VALIDA"));
        boolean foundRangeBranch = children.stream()
                .anyMatch(s -> s.referencedConditionNames().contains("COD-RANGO-ERROR"));
        assertTrue(foundValidBranch, "el branch WHEN COD-OPERACION-VALIDA debe referenciar la condicion");
        assertTrue(foundRangeBranch, "el branch WHEN COD-RANGO-ERROR debe referenciar la condicion");
    }

    @Test
    void setConditionNeverPropagatesTheConditionsDeclaredValue() {
        // SET COD-CAMPO-INVALIDO TO TRUE nunca debe arrastrar el VALUE
        // declarado de la condicion ('0005') hacia assignedLiteral: el
        // literal capturado es siempre el texto crudo TRUE/FALSE de
        // ProLeap, nunca el valor semantico que representa la condicion.
        CanonicalStatement set = statements().stream()
                .filter(s -> s.kind() == StatementKind.SET)
                .filter(s -> "COD-CAMPO-INVALIDO".equals(s.conditionNameTarget()))
                .findFirst().orElseThrow();
        assertEquals("true", set.assignedLiteral());
        assertFalse("0005".equals(set.assignedLiteral()));
        assertFalse(program.conditionNames().isEmpty());
    }

    @Test
    void parsingTwiceProducesIdenticalCanonicalProgram() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("level-88.cbl"), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8);
        CanonicalProgram second = new CanonicalProgramExtractor().extract(
                Fixtures.path("level-88.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/level-88.cbl",
                "b".repeat(64),
                "a".repeat(64));
        assertEquals(program, second);
    }
}
