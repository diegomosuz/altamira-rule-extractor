package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.CallPassingMode;
import com.altamira.extractor.parser.model.CallTargetKind;
import com.altamira.extractor.parser.model.CanonicalCallArgument;
import com.altamira.extractor.parser.model.CanonicalEntryParameter;
import com.altamira.extractor.parser.model.CanonicalLinkageDataItem;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.StatementKind;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

/**
 * Parsea fixtures/interprocedural-caller.cbl e interprocedural-callee.cbl
 * (real ProLeap, JAR real) una sola vez cada uno y verifica captura de
 * CALL literal/USING/BY REFERENCE/BY CONTENT/RETURNING, CALL dinamico,
 * LINKAGE SECTION y PROCEDURE DIVISION USING/RETURNING (Fase 6 de la
 * ampliacion semantica, fundacion interprocedural CALL/LINKAGE).
 */
class InterproceduralCallLinkageExtractionTest {

    private static CanonicalProgram caller;
    private static CanonicalProgram callee;

    @BeforeAll
    static void parseOnce() throws Exception {
        var callerResult = new ProLeapCobolParser().parse(
                Fixtures.path("interprocedural-caller.cbl"), RequestedFormat.FIXED, List.of(),
                StandardCharsets.UTF_8);
        caller = new CanonicalProgramExtractor().extract(
                Fixtures.path("interprocedural-caller.cbl"),
                StandardCharsets.UTF_8,
                callerResult,
                "01-codigo/cobol/interprocedural-caller.cbl",
                "b".repeat(64),
                "a".repeat(64));

        var calleeResult = new ProLeapCobolParser().parse(
                Fixtures.path("interprocedural-callee.cbl"), RequestedFormat.FIXED, List.of(),
                StandardCharsets.UTF_8);
        callee = new CanonicalProgramExtractor().extract(
                Fixtures.path("interprocedural-callee.cbl"),
                StandardCharsets.UTF_8,
                calleeResult,
                "01-codigo/cobol/interprocedural-callee.cbl",
                "d".repeat(64),
                "a".repeat(64));
    }

    private static List<CanonicalStatement> callerStatements() {
        return caller.paragraphs().get(0).statements();
    }

    private static CanonicalStatement callStatement(int callOrdinal) {
        List<CanonicalStatement> calls = callerStatements().stream()
                .filter(s -> s.kind() == StatementKind.CALL)
                .toList();
        return calls.get(callOrdinal);
    }

    // Ambos fixtures usan CALL/LINKAGE (Fase 6, tier 1.2 como minimo) Y
    // terminan en STOP RUN (Fase 7b, StatementKind.PROGRAM_TERMINATION,
    // que supersede a 1.2 exactamente igual que 1.2 supersede a 1.1) --
    // "1.3" es la version real esperada, no "1.2".
    @Test
    void callerSchemaVersionIs13() {
        assertEquals("1.3", caller.schemaVersion());
    }

    @Test
    void calleeSchemaVersionIs13() {
        assertEquals("1.3", callee.schemaVersion());
    }

    @Test
    void threeCallStatementsAreCaptured() {
        long callCount = callerStatements().stream().filter(s -> s.kind() == StatementKind.CALL).count();
        assertEquals(3, callCount);
    }

    @Test
    void literalCallCapturesTargetNameAndClearsExpression() {
        CanonicalStatement call = callStatement(0);
        assertEquals(CallTargetKind.LITERAL, call.callTargetKind());
        assertEquals("CALLEEP", call.calledProgramName());
        assertNull(call.calledProgramExpression());
    }

    @Test
    void literalCallCapturesByReferenceAndByContentArguments() {
        CanonicalStatement call = callStatement(0);
        List<CanonicalCallArgument> arguments = call.callArguments();
        assertEquals(2, arguments.size());

        CanonicalCallArgument first = arguments.get(0);
        assertEquals(1, first.ordinal());
        assertEquals(CallPassingMode.REFERENCE, first.passingMode());
        assertEquals("WS-INPUT", first.dataItemName());
        assertFalse(first.omitted());

        CanonicalCallArgument second = arguments.get(1);
        assertEquals(2, second.ordinal());
        assertEquals(CallPassingMode.CONTENT, second.passingMode());
        assertEquals("WS-FLAG", second.dataItemName());
    }

    @Test
    void literalCallCapturesReturningDataItem() {
        CanonicalStatement call = callStatement(0);
        assertEquals("WS-RESULT", call.callReturningDataItem());
    }

    @Test
    void literalCallNeverPopulatesGenericWriteTargets() {
        // Fase 6: CALL nunca afirma un efecto de escritura CIERTO a nivel
        // canonico (a diferencia de MOVE/SET/COMPUTE) -- BY REFERENCE y
        // RETURNING solo describen un efecto POTENCIAL, capturado
        // exclusivamente via callArguments/callReturningDataItem.
        CanonicalStatement call = callStatement(0);
        assertTrue(call.targetDataItems().isEmpty());
        assertTrue(call.variablesWritten().isEmpty());
    }

    @Test
    void dynamicCallCapturesExpressionAndClearsProgramName() {
        CanonicalStatement call = callStatement(1);
        assertEquals(CallTargetKind.DYNAMIC, call.callTargetKind());
        assertEquals("WS-PROGRAM-NAME", call.calledProgramExpression());
        assertNull(call.calledProgramName());
        assertEquals(1, call.callArguments().size());
        assertEquals("WS-INPUT", call.callArguments().get(0).dataItemName());
    }

    @Test
    void thirdCallCapturesLiteralTargetForMissingProgram() {
        // La resolucion contra el paquete (RESOLVED_INTERNAL vs.
        // UNRESOLVED_MISSING_PROGRAM) es responsabilidad del analizador
        // Python (Fase 11), nunca del parser Java: aqui solo se verifica
        // que el target literal se capturo estructuralmente.
        CanonicalStatement call = callStatement(2);
        assertEquals(CallTargetKind.LITERAL, call.callTargetKind());
        assertEquals("MISSING-PROG", call.calledProgramName());
        assertTrue(call.callArguments().isEmpty());
        assertNull(call.callReturningDataItem());
    }

    @Test
    void calleeLinkageSectionCapturesAllThreeItemsSeparateFromDataItems() {
        List<CanonicalLinkageDataItem> linkage = callee.linkageDataItems();
        assertEquals(3, linkage.size());
        assertEquals(List.of("LK-INPUT", "LK-FLAG", "LK-RESULT"),
                linkage.stream().map(CanonicalLinkageDataItem::name).toList());
        // WORKING-STORAGE (WS-LOCAL) nunca se mezcla con linkageDataItems.
        assertEquals(1, callee.dataItems().size());
        assertEquals("WS-LOCAL", callee.dataItems().get(0).name());
    }

    @Test
    void calleeEntryParametersPreserveSourceOrderAndResolveAgainstLinkage() {
        List<CanonicalEntryParameter> parameters = callee.entryParameters();
        assertEquals(2, parameters.size());
        assertEquals(1, parameters.get(0).ordinal());
        assertEquals("LK-INPUT", parameters.get(0).name());
        assertEquals("LK-INPUT", parameters.get(0).linkageItemQualifiedName());
        assertEquals(2, parameters.get(1).ordinal());
        assertEquals("LK-FLAG", parameters.get(1).name());
        assertEquals("LK-FLAG", parameters.get(1).linkageItemQualifiedName());
    }

    @Test
    void calleeEntryReturningDataItemIsCaptured() {
        assertEquals("LK-RESULT", callee.entryReturningDataItem());
    }
}
