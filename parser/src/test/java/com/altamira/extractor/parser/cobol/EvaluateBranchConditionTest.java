package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.BranchKind;
import com.altamira.extractor.parser.model.CanonicalParagraph;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.StatementKind;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

/**
 * Regresion real (Ciclo 4, v1.18.2): {@code EVALUATE SQLCODE WHEN +100}
 * perdia el literal de comparacion antes de llegar a {@code Decision}/
 * {@code ContextPackage} -- {@code branch_condition} quedaba poblado con un
 * dump crudo de {@code WhenPhrase.getCtx().getText()} (incluye el statement
 * anidado completo, ej. {@code "WHEN+100MOVE'N'TOWS-ESTADO-OPERACION"}),
 * nunca con el predicado aislado {@code "SQLCODE = +100"}. Cubre la matriz
 * minima exigida: literal numerico simple, literal string, multiples ramas
 * WHEN del mismo EVALUATE (cada una con su propio literal, sin conflacion),
 * WHEN OTHER (nunca lleva branch_condition, sin cambios), WHEN NOT (operador
 * {@code <>}), y EVALUATE TRUE con condiciones compuestas/condition-name
 * (fuera del alcance del predicado limpio: debe conservar el texto crudo
 * existente en vez de perder branch_condition o inventar un operador).
 */
class EvaluateBranchConditionTest {

    private static CanonicalProgram program;

    @BeforeAll
    static void parseOnce() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("evaluate_branch_condition.cbl"), RequestedFormat.FIXED, List.of(),
                StandardCharsets.UTF_8);
        program = new CanonicalProgramExtractor().extract(
                Fixtures.path("evaluate_branch_condition.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/evaluate_branch_condition.cbl",
                "b".repeat(64),
                "a".repeat(64));
    }

    private static CanonicalParagraph paragraph(String name) {
        return program.paragraphs().stream()
                .filter(p -> p.name().equals(name))
                .findFirst()
                .orElseThrow(() -> new AssertionError("paragraph not found: " + name));
    }

    private static List<CanonicalStatement> statementsOfKind(CanonicalParagraph p, StatementKind kind) {
        return p.statements().stream().filter(s -> s.kind() == kind).toList();
    }

    @Test
    void numericLiteralBranchProducesCleanComparisonNotRawDump() {
        CanonicalParagraph p = paragraph("SQLCODE-EVALUATE-PARA");
        List<CanonicalStatement> moves = statementsOfKind(p, StatementKind.MOVE);
        CanonicalStatement whenPlus100 = moves.stream()
                .filter(s -> "N".equals(s.assignedLiteral()))
                .findFirst()
                .orElseThrow(() -> new AssertionError("no MOVE 'N' found"));
        assertEquals("SQLCODE = 100", whenPlus100.branchCondition());
        assertFalse(
                whenPlus100.branchCondition().contains("MOVE"),
                "branch_condition no debe incluir el statement anidado: "
                        + whenPlus100.branchCondition());
    }

    @Test
    void distinctWhenBranchesOfSameEvaluateKeepOwnConditionNeverConflated() {
        CanonicalParagraph p = paragraph("SQLCODE-EVALUATE-PARA");
        List<CanonicalStatement> moves = statementsOfKind(p, StatementKind.MOVE);

        CanonicalStatement whenZero = moves.stream()
                .filter(s -> "A".equals(s.assignedLiteral()))
                .findFirst()
                .orElseThrow();
        CanonicalStatement whenPlus100 = moves.stream()
                .filter(s -> "N".equals(s.assignedLiteral()))
                .findFirst()
                .orElseThrow();
        CanonicalStatement whenOther = moves.stream()
                .filter(s -> "E".equals(s.assignedLiteral()))
                .findFirst()
                .orElseThrow();

        assertEquals("SQLCODE = 0", whenZero.branchCondition());
        assertEquals("SQLCODE = 100", whenPlus100.branchCondition());
        assertEquals(BranchKind.WHEN, whenZero.branchKind());
        assertEquals(BranchKind.WHEN, whenPlus100.branchKind());
        assertEquals(
                BranchKind.WHEN_OTHER, whenOther.branchKind(),
                "WHEN OTHER nunca lleva branch_condition (sin cambios por esta correccion)");
        assertNotNull(whenOther.assignedLiteral());
        assertFalse(
                whenZero.branchCondition().equals(whenPlus100.branchCondition()),
                "dos ramas WHEN distintas del mismo EVALUATE no deben compartir branch_condition");
    }

    @Test
    void whenOtherNeverGetsABranchConditionField() {
        CanonicalParagraph p = paragraph("SQLCODE-EVALUATE-PARA");
        CanonicalStatement whenOther = statementsOfKind(p, StatementKind.MOVE).stream()
                .filter(s -> "E".equals(s.assignedLiteral()))
                .findFirst()
                .orElseThrow();
        assertEquals(null, whenOther.branchCondition());
    }

    @Test
    void stringLiteralBranchProducesCleanComparison() {
        CanonicalParagraph p = paragraph("STATUS-EVALUATE-PARA");
        CanonicalStatement whenA = statementsOfKind(p, StatementKind.MOVE).stream()
                .filter(s -> s.branchKind() == BranchKind.WHEN)
                .findFirst()
                .orElseThrow();
        assertEquals("WS-STATUS = A", whenA.branchCondition());
    }

    @Test
    void whenNotUsesNotEqualOperator() {
        CanonicalParagraph p = paragraph("NOT-EVALUATE-PARA");
        CanonicalStatement whenNotA = statementsOfKind(p, StatementKind.MOVE).stream()
                .filter(s -> s.branchKind() == BranchKind.WHEN)
                .findFirst()
                .orElseThrow();
        assertEquals("WS-STATUS <> A", whenNotA.branchCondition());
    }

    @Test
    void evaluateTrueWithComplexConditionsFallsBackToBareSubjectNeverNullNeverInventsOperator() {
        // Rama con condicion compuesta/condition-name (no un literal puro):
        // buildBranchCondition() devuelve null (no se puede aislar limpio) y
        // el fallback usado es el sujeto crudo del EVALUATE ("TRUE") -- el
        // MISMO valor que Decision.expression ya expone hoy para esta rama
        // (ver decision_node.properties.get("expression") en
        // enhanced_candidate_integration.py), nunca el dump ANTLR crudo de
        // toda la WhenPhrase: un consumidor downstream (RuleCandidate.
        // condition/ContextPackageDecision) no podria distinguir ese dump de
        // un predicado limpio por forma, y quedaria peor que el
        // comportamiento actual pre-correccion en vez de, como minimo,
        // igual de bueno.
        CanonicalParagraph p = paragraph("TRUE-EVALUATE-PARA");
        List<CanonicalStatement> whenBranches = statementsOfKind(p, StatementKind.MOVE).stream()
                .filter(s -> s.branchKind() == BranchKind.WHEN)
                .toList();
        assertEquals(2, whenBranches.size());
        for (CanonicalStatement branch : whenBranches) {
            assertEquals(
                    "TRUE", branch.branchCondition(),
                    "fallback debe ser el sujeto crudo del EVALUATE, nunca null ni un dump ANTLR");
        }
    }
}
