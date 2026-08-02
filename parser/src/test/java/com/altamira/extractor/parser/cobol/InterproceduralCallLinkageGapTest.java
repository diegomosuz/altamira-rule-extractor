package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.BranchKind;
import com.altamira.extractor.parser.model.CallPassingMode;
import com.altamira.extractor.parser.model.CanonicalCallArgument;
import com.altamira.extractor.parser.model.CanonicalEntryParameter;
import com.altamira.extractor.parser.model.CanonicalLinkageDataItem;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.StatementKind;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

/**
 * Cierre de la desviacion de fixtures Java (Fase 6, auditoria de release
 * engineering posterior a la implementacion): parsea fixtures/
 * interprocedural-gap-caller.cbl e interprocedural-gap-callee.cbl (real
 * ProLeap, JAR real) para cubrir los escenarios de la matriz de 20 que
 * InterproceduralCallLinkageExtractionTest.java (CALLERP/CALLEEP) no
 * ejercita de forma aislada con el parser real: BY VALUE, argumentos
 * extra/faltantes, CALL dentro de IF/EVALUATE, ON EXCEPTION/NOT ON
 * EXCEPTION, self-call, LINKAGE con grupo y campos, LINKAGE item no
 * usado como formal, y formal ausente en LINKAGE. El escenario de
 * ciclo de dos programas (GAPCALLER <-> GAPCALLEE) y los escenarios ya
 * cubiertos estructuralmente por estos mismos fixtures se demuestran
 * end-to-end (parser + analizadores Python reales) en la validacion de
 * paquete multiprograma (Parte 3 del informe de cierre), no aqui.
 */
class InterproceduralCallLinkageGapTest {

    private static CanonicalProgram caller;
    private static CanonicalProgram callee;

    @BeforeAll
    static void parseOnce() throws Exception {
        var callerResult = new ProLeapCobolParser().parse(
                Fixtures.path("interprocedural-gap-caller.cbl"), RequestedFormat.FIXED, List.of(),
                StandardCharsets.UTF_8);
        caller = new CanonicalProgramExtractor().extract(
                Fixtures.path("interprocedural-gap-caller.cbl"),
                StandardCharsets.UTF_8,
                callerResult,
                "01-codigo/cobol/interprocedural-gap-caller.cbl",
                "f".repeat(64),
                "a".repeat(64));

        var calleeResult = new ProLeapCobolParser().parse(
                Fixtures.path("interprocedural-gap-callee.cbl"), RequestedFormat.FIXED, List.of(),
                StandardCharsets.UTF_8);
        callee = new CanonicalProgramExtractor().extract(
                Fixtures.path("interprocedural-gap-callee.cbl"),
                StandardCharsets.UTF_8,
                calleeResult,
                "01-codigo/cobol/interprocedural-gap-callee.cbl",
                "1".repeat(64),
                "a".repeat(64));
    }

    private static List<CanonicalStatement> callerStatements() {
        return caller.paragraphs().get(0).statements();
    }

    private static List<CanonicalStatement> callerCalls() {
        return callerStatements().stream().filter(s -> s.kind() == StatementKind.CALL).toList();
    }

    private static CanonicalStatement call(int ordinal) {
        return callerCalls().get(ordinal);
    }

    // --- Escenario 5: BY VALUE ---------------------------------------------

    @Test
    void eightCallStatementsAreCapturedAcrossAllScenarios() {
        assertEquals(8, callerCalls().size());
    }

    @Test
    void byValueArgumentIsCapturedWithValuePassingMode() {
        CanonicalStatement byValueCall = call(0);
        List<CanonicalCallArgument> arguments = byValueCall.callArguments();
        assertEquals(1, arguments.size());
        assertEquals(CallPassingMode.VALUE, arguments.get(0).passingMode());
        assertEquals("WS-A", arguments.get(0).dataItemName());
    }

    // --- Escenario 13/14: argumentos extra / faltantes (captura estructural) ---

    @Test
    void callWithThreeActualArgumentsCapturesAllThreePositionally() {
        // GAPCALLEE solo declara 2 parametros formales (LK-X, WS-LOCAL,
        // ver calleeEntryParametersHasExactlyTwoFormals): este call site
        // pasa 3 actuals, mas que los formales -- la clasificacion
        // EXTRA_ACTUAL es responsabilidad del analizador Python (Fase 12),
        // aqui solo se demuestra la captura estructural real via ProLeap.
        CanonicalStatement extraArgsCall = call(1);
        List<CanonicalCallArgument> arguments = extraArgsCall.callArguments();
        assertEquals(3, arguments.size());
        assertEquals(List.of(1, 2, 3), arguments.stream().map(CanonicalCallArgument::ordinal).toList());
        assertEquals(
                List.of("WS-A", "WS-B", "WS-C"),
                arguments.stream().map(CanonicalCallArgument::dataItemName).toList());
    }

    @Test
    void callWithOneActualArgumentCapturesFewerArgumentsThanCalleeFormals() {
        // Un unico actual contra 2 formales -- la clasificacion
        // MISSING_ACTUAL es responsabilidad del analizador Python.
        CanonicalStatement missingArgCall = call(2);
        assertEquals(1, missingArgCall.callArguments().size());
        assertEquals("WS-A", missingArgCall.callArguments().get(0).dataItemName());
    }

    // --- Escenario 15/16: CALL dentro de IF / EVALUATE ------------------------

    @Test
    void callInsideIfBranchPreservesParentAndBranchKind() {
        CanonicalStatement callInIf = call(3);
        assertNotNull(callInIf.parentStatementId());
        assertEquals(BranchKind.THEN, callInIf.branchKind());
        assertEquals(2, callInIf.callArguments().size());

        CanonicalStatement parent = callerStatements().stream()
                .filter(s -> s.statementId().equals(callInIf.parentStatementId()))
                .findFirst().orElseThrow();
        assertEquals(StatementKind.IF, parent.kind());
    }

    @Test
    void callInsideEvaluateWhenBranchPreservesParentAndBranchKind() {
        CanonicalStatement callInEvaluate = call(4);
        assertNotNull(callInEvaluate.parentStatementId());
        assertEquals(BranchKind.WHEN, callInEvaluate.branchKind());
        assertEquals(2, callInEvaluate.callArguments().size());

        CanonicalStatement parent = callerStatements().stream()
                .filter(s -> s.statementId().equals(callInEvaluate.parentStatementId()))
                .findFirst().orElseThrow();
        assertEquals(StatementKind.EVALUATE, parent.kind());
    }

    // --- Escenario 17: ON EXCEPTION / NOT ON EXCEPTION (API real de ProLeap) ---

    @Test
    void onExceptionClauseIsCapturedAsPresenceIndicatorOnly() {
        CanonicalStatement onExceptionCall = call(5);
        assertTrue(onExceptionCall.callHasOnException());
        assertFalse(Boolean.TRUE.equals(onExceptionCall.callHasNotOnException()));
    }

    @Test
    void notOnExceptionClauseIsCapturedAsPresenceIndicatorOnly() {
        CanonicalStatement notOnExceptionCall = call(6);
        assertTrue(notOnExceptionCall.callHasNotOnException());
        assertFalse(Boolean.TRUE.equals(notOnExceptionCall.callHasOnException()));
    }

    // --- Escenario 18: self-call --------------------------------------------

    @Test
    void selfCallCapturesOwnProgramNameAsLiteralTarget() {
        CanonicalStatement selfCall = call(7);
        assertEquals("GAPCALLER", caller.programName());
        assertEquals("GAPCALLER", selfCall.calledProgramName());
        assertEquals(1, selfCall.callArguments().size());
    }

    // --- Escenario 10: LINKAGE con grupo y campos -----------------------------

    @Test
    void linkageGroupAndChildFieldsAreCapturedWithParentQualifiedName() {
        List<CanonicalLinkageDataItem> linkage = callee.linkageDataItems();
        Optional<CanonicalLinkageDataItem> field1 = linkage.stream()
                .filter(item -> item.name().equals("LK-GROUP-FIELD-1")).findFirst();
        Optional<CanonicalLinkageDataItem> field2 = linkage.stream()
                .filter(item -> item.name().equals("LK-GROUP-FIELD-2")).findFirst();
        assertTrue(field1.isPresent(), "LK-GROUP-FIELD-1 debe estar en linkageDataItems()");
        assertTrue(field2.isPresent(), "LK-GROUP-FIELD-2 debe estar en linkageDataItems()");
        assertNotNull(field1.get().parentQualifiedName());
        assertTrue(field1.get().parentQualifiedName().contains("LK-GROUP"));
        assertNotNull(field2.get().parentQualifiedName());
        assertTrue(field2.get().parentQualifiedName().contains("LK-GROUP"));
    }

    // --- Escenario 12: LINKAGE item no usado como formal -----------------------

    @Test
    void linkageItemNeverReferencedByProcedureDivisionIsStillCaptured() {
        List<CanonicalLinkageDataItem> linkage = callee.linkageDataItems();
        assertTrue(linkage.stream().anyMatch(item -> item.name().equals("LK-UNUSED")));

        List<CanonicalEntryParameter> parameters = callee.entryParameters();
        assertTrue(
                parameters.stream().noneMatch(p -> "LK-UNUSED".equals(p.linkageItemQualifiedName())),
                "LK-UNUSED nunca debe aparecer como formal resuelto: no esta en PROCEDURE DIVISION USING");
    }

    // --- Escenario 11: formal ausente en LINKAGE (API real de ProLeap) --------

    @Test
    void formalParameterNotDeclaredInLinkageSectionIsCapturedButUnresolved() {
        List<CanonicalEntryParameter> parameters = callee.entryParameters();
        assertEquals(2, parameters.size());

        CanonicalEntryParameter resolved = parameters.get(0);
        assertEquals("LK-X", resolved.name());
        assertEquals("LK-X", resolved.linkageItemQualifiedName());

        CanonicalEntryParameter unresolved = parameters.get(1);
        assertEquals("WS-LOCAL", unresolved.name());
        assertNull(
                unresolved.linkageItemQualifiedName(),
                "WS-LOCAL no esta declarado en LINKAGE SECTION: nunca debe resolverse "
                        + "arbitrariamente contra un item de WORKING-STORAGE");
    }

    // --- Escenario 19: ciclo de dos programas (captura estructural del lado B) -

    @Test
    void calleeCallsBackToCallerClosingTheStructuralCycle() {
        // GAPCALLER llama a GAPCALLEE (multiples call sites, ver arriba) y
        // GAPCALLEE llama de vuelta a GAPCALLER: ambos lados del ciclo
        // estan estructuralmente capturados por el parser real. La
        // deteccion del ciclo en si (Tarjan sobre el call graph) es
        // responsabilidad del analizador Python -- demostrada end-to-end
        // en la validacion de paquete multiprograma con estos mismos
        // fixtures (Parte 3 del informe de cierre).
        List<CanonicalStatement> calleeStatements = callee.paragraphs().get(0).statements();
        List<CanonicalStatement> calleeCalls =
                calleeStatements.stream().filter(s -> s.kind() == StatementKind.CALL).toList();
        assertEquals(1, calleeCalls.size());
        assertEquals("GAPCALLER", calleeCalls.get(0).calledProgramName());
    }

    // --- Escenario 20: programa sin CALL/LINKAGE mantiene schema_version -------

    @Test
    void programWithoutCallOrLinkageStaysAtHistoricalSchemaVersion() throws Exception {
        // comprehensive.cbl (PROGRAM-ID COMPREH1) no declara CALL ni
        // LINKAGE SECTION ni nivel 88 (ver Parte 4 del informe de
        // cierre, no-regresion V1/V2): la ausencia de CALL/LINKAGE (Fase
        // 6) y de nivel 88 (Fase 3) se verifica exactamente igual que
        // antes. La version real, sin embargo, es "1.3" y no "1.0": el
        // fixture SI termina en GOBACK (Fase 7b), una senal ortogonal e
        // independiente de CALL/LINKAGE que tambien produce un bump de
        // schema_version -- "1.3" refleja PROGRAM_TERMINATION, no CALL/
        // LINKAGE, y ambas dimensiones se verifican por separado abajo.
        var result = new ProLeapCobolParser().parse(
                Fixtures.path("comprehensive.cbl"), RequestedFormat.FIXED, List.of(),
                StandardCharsets.UTF_8);
        CanonicalProgram program = new CanonicalProgramExtractor().extract(
                Fixtures.path("comprehensive.cbl"),
                StandardCharsets.UTF_8,
                result,
                "01-codigo/cobol/comprehensive.cbl",
                "9".repeat(64),
                "a".repeat(64));
        assertEquals("1.3", program.schemaVersion());
        assertTrue(program.linkageDataItems().isEmpty());
        assertTrue(program.entryParameters().isEmpty());
        assertNull(program.entryReturningDataItem());
        assertTrue(
                program.paragraphs().stream()
                        .flatMap(p -> p.statements().stream())
                        .noneMatch(s -> s.kind() == StatementKind.CALL));
    }
}
