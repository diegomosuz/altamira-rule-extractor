package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.BranchKind;
import com.altamira.extractor.parser.model.CanonicalDataItem;
import com.altamira.extractor.parser.model.CanonicalParagraph;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.LocationKind;
import com.altamira.extractor.parser.model.StatementKind;
import com.altamira.extractor.parser.model.TableAccessOperation;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

/** Parsea fixtures/comprehensive.cbl una sola vez y verifica todas las construcciones cubiertas. */
class CanonicalProgramExtractorTest {

    private static CanonicalProgram program;

    @BeforeAll
    static void parseOnce() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("comprehensive.cbl"), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8);
        program = new CanonicalProgramExtractor().extract(
                Fixtures.path("comprehensive.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/comprehensive.cbl",
                "b".repeat(64),
                "a".repeat(64));
    }

    private static CanonicalParagraph paragraph(String name) {
        return program.paragraphs().stream()
                .filter(p -> p.name().equals(name))
                .findFirst()
                .orElseThrow(() -> new AssertionError("paragraph not found: " + name));
    }

    @Test
    void programIdentification() {
        assertEquals("COMPREH1", program.programName());
        assertEquals("01-codigo/cobol/comprehensive.cbl", program.sourceFile());
        assertEquals(64, program.sourceHash().length());
        assertEquals("a".repeat(64), program.sourcePackageHash());
        assertEquals(com.altamira.extractor.parser.model.SourceFormat.FIXED, program.sourceFormat());
        assertEquals("UTF-8", program.encoding());
    }

    @Test
    void noCopyMeansExactLocationEverywhere() {
        for (CanonicalDataItem item : program.dataItems()) {
            assertEquals(LocationKind.EXACT, item.locationKind());
            assertNotNull(item.sourceFile());
        }
        for (CanonicalParagraph p : program.paragraphs()) {
            assertEquals(LocationKind.EXACT, p.locationKind());
        }
    }

    @Test
    void dataItemsHaveLevelsPicUsageAndGroups() {
        CanonicalDataItem group = program.dataItems().stream()
                .filter(i -> i.name().equals("WS-REGISTRO")).findFirst().orElseThrow();
        assertEquals(1, group.level());
        assertEquals("WS-REGISTRO", group.qualifiedName());

        CanonicalDataItem monto = program.dataItems().stream()
                .filter(i -> i.name().equals("WS-MONTO")).findFirst().orElseThrow();
        assertEquals(5, monto.level());
        assertEquals("WS-REGISTRO.WS-MONTO", monto.qualifiedName());
        assertEquals("9(7)V99", monto.pic());
        assertNotNull(monto.usage());

        CanonicalDataItem codigo = program.dataItems().stream()
                .filter(i -> i.name().equals("WS-CODIGO")).findFirst().orElseThrow();
        assertEquals("X(3)", codigo.pic());
    }

    @Test
    void paragraphsAreOrderedBySourcePosition() {
        List<String> names = program.paragraphs().stream().map(CanonicalParagraph::name).toList();
        assertEquals(List.of("MAIN-PARA", "VALIDAR-MONTO", "CALCULOS-PARA", "CALCULOS-PARA-FIN", "FIN-PARA"), names);
    }

    @Test
    void ifStatementHasThenAndElseBranches() {
        CanonicalParagraph validar = paragraph("VALIDAR-MONTO");
        CanonicalStatement outerIf = validar.statements().stream()
                .filter(s -> s.kind() == StatementKind.IF && s.parentStatementId() == null)
                .findFirst().orElseThrow();
        assertTrue(outerIf.operands().contains("WS-MONTO"));
        assertTrue(outerIf.operands().contains("WS-LIMITE"));

        List<CanonicalStatement> children = validar.statements().stream()
                .filter(s -> outerIf.statementId().equals(s.parentStatementId()))
                .toList();
        assertTrue(children.stream().anyMatch(s -> s.branchKind() == BranchKind.THEN));
        assertTrue(children.stream().anyMatch(s -> s.branchKind() == BranchKind.ELSE));
    }

    @Test
    void nestedIfIsFlattenedWithCorrectParent() {
        CanonicalParagraph validar = paragraph("VALIDAR-MONTO");
        List<CanonicalStatement> ifs = validar.statements().stream()
                .filter(s -> s.kind() == StatementKind.IF)
                .toList();
        assertEquals(2, ifs.size(), "debe haber un IF externo y uno anidado");

        CanonicalStatement outerIf = ifs.stream().filter(s -> s.parentStatementId() == null).findFirst().orElseThrow();
        CanonicalStatement innerIf = ifs.stream().filter(s -> s != outerIf).findFirst().orElseThrow();
        // el IF anidado debe colgar de una rama THEN del externo, no ser hijo directo del paragraph
        assertNotNull(innerIf.parentStatementId());
        assertEquals(BranchKind.THEN, innerIf.branchKind());
    }

    @Test
    void evaluateHasWhenAndWhenOtherBranches() {
        CanonicalParagraph validar = paragraph("VALIDAR-MONTO");
        CanonicalStatement evaluate = validar.statements().stream()
                .filter(s -> s.kind() == StatementKind.EVALUATE)
                .findFirst().orElseThrow();

        List<CanonicalStatement> children = validar.statements().stream()
                .filter(s -> evaluate.statementId().equals(s.parentStatementId()))
                .toList();
        long whenCount = children.stream().filter(s -> s.branchKind() == BranchKind.WHEN).count();
        long whenOtherCount = children.stream().filter(s -> s.branchKind() == BranchKind.WHEN_OTHER).count();
        assertEquals(2, whenCount, "dos WHEN con valor (ALT, NOR)");
        assertEquals(1, whenOtherCount);
    }

    @Test
    void moveSupportsMultipleTargets() {
        CanonicalParagraph calculos = paragraph("CALCULOS-PARA");
        CanonicalStatement move = calculos.statements().stream()
                .filter(s -> s.kind() == StatementKind.MOVE)
                .findFirst().orElseThrow();
        assertEquals(List.of("WS-DESTINO-A", "WS-DESTINO-B", "WS-DESTINO-C"), move.targetDataItems());
        assertEquals(move.targetDataItems(), move.variablesWritten());
    }

    @Test
    void setStatementCapturesTargetAndLiteral() {
        CanonicalParagraph calculos = paragraph("CALCULOS-PARA");
        CanonicalStatement set = calculos.statements().stream()
                .filter(s -> s.kind() == StatementKind.SET)
                .findFirst().orElseThrow();
        assertEquals(List.of("WS-INDICE"), set.targetDataItems());
        assertEquals("1", set.assignedLiteral());
    }

    @Test
    void computeCapturesTargetAndExpression() {
        CanonicalParagraph calculos = paragraph("CALCULOS-PARA");
        CanonicalStatement compute = calculos.statements().stream()
                .filter(s -> s.kind() == StatementKind.COMPUTE)
                .findFirst().orElseThrow();
        assertEquals(List.of("WS-SALDO"), compute.targetDataItems());
        assertNotNull(compute.expression());
        assertTrue(compute.variablesRead().contains("WS-MONTO"));
        assertTrue(compute.variablesRead().contains("WS-LIMITE"));
    }

    @Test
    void goToCapturesTargetParagraph() {
        CanonicalParagraph main = paragraph("MAIN-PARA");
        CanonicalStatement goTo = main.statements().stream()
                .filter(s -> s.kind() == StatementKind.GO_TO)
                .findFirst().orElseThrow();
        assertEquals(List.of("FIN-PARA"), goTo.targetParagraphs());
    }

    @Test
    void performSimpleAndThruAreCaptured() {
        CanonicalParagraph main = paragraph("MAIN-PARA");
        List<CanonicalStatement> performs = main.statements().stream()
                .filter(s -> s.kind() == StatementKind.PERFORM)
                .toList();
        assertEquals(2, performs.size());

        assertTrue(performs.stream().anyMatch(p -> p.targetParagraphs().equals(List.of("VALIDAR-MONTO"))));
        assertTrue(performs.stream().anyMatch(
                p -> p.targetParagraphs().equals(List.of("CALCULOS-PARA", "CALCULOS-PARA-FIN"))));
    }

    @Test
    void execSqlSelectInsertUpdateDeleteAreClassified() {
        CanonicalParagraph calculos = paragraph("CALCULOS-PARA");
        List<CanonicalStatement> sqlStatements = calculos.statements().stream()
                .filter(s -> s.kind() == StatementKind.EXEC_SQL)
                .toList();
        assertEquals(5, sqlStatements.size(), "SELECT, INSERT, UPDATE, DELETE, SELECT multi-tabla");

        Set<TableAccessOperation> operations = sqlStatements.stream()
                .flatMap(s -> s.sqlAccess().stream())
                .map(com.altamira.extractor.parser.model.CanonicalSqlAccess::operation)
                .collect(Collectors.toCollection(HashSet::new));
        assertTrue(operations.contains(TableAccessOperation.READS));
        assertTrue(operations.contains(TableAccessOperation.INSERTS));
        assertTrue(operations.contains(TableAccessOperation.UPDATES));
        assertTrue(operations.contains(TableAccessOperation.WRITES)); // DELETE -> WRITES

        boolean hasHostVariable = sqlStatements.stream()
                .flatMap(s -> s.sqlAccess().stream())
                .anyMatch(a -> a.hostVariables().contains("WS-CUENTA-ID"));
        assertTrue(hasHostVariable);
    }

    @Test
    void selectWithMultipleTablesProducesMultipleAccesses() {
        CanonicalParagraph calculos = paragraph("CALCULOS-PARA");
        CanonicalStatement multiTableSelect = calculos.statements().stream()
                .filter(s -> s.kind() == StatementKind.EXEC_SQL
                        && s.sqlAccess().size() > 1)
                .findFirst().orElseThrow();
        List<String> tables = multiTableSelect.sqlAccess().stream()
                .map(com.altamira.extractor.parser.model.CanonicalSqlAccess::table)
                .toList();
        assertEquals(List.of("CUENTAS", "MOVIMIENTOS"), tables);
    }

    @Test
    void paragraphAggregatesMatchStatementUnion() {
        for (CanonicalParagraph p : program.paragraphs()) {
            Set<String> expectedRead = new java.util.LinkedHashSet<>();
            Set<String> expectedWritten = new java.util.LinkedHashSet<>();
            for (CanonicalStatement s : p.statements()) {
                expectedRead.addAll(s.variablesRead());
                expectedWritten.addAll(s.variablesWritten());
            }
            assertEquals(List.copyOf(expectedRead), p.variablesRead());
            assertEquals(List.copyOf(expectedWritten), p.variablesWritten());
        }
    }

    @Test
    void statementIdsAreUniqueAcrossWholeProgram() {
        Set<String> ids = new HashSet<>();
        for (CanonicalParagraph p : program.paragraphs()) {
            for (CanonicalStatement s : p.statements()) {
                assertTrue(ids.add(s.statementId()), "statement_id duplicado: " + s.statementId());
                assertTrue(s.statementId().startsWith("COMPREH1::" + p.name() + "::"));
            }
        }
    }

    @Test
    void unsupportedGobackAndAddAreReportedNotInvented() {
        assertFalse(program.unsupportedConstructs().isEmpty());
        assertTrue(program.unsupportedConstructs().stream().anyMatch(w -> w.contains("Goback")));
        assertTrue(program.unsupportedConstructs().stream().anyMatch(w -> w.contains("Add")));
    }

    @Test
    void noAbsolutePathsAnywhereInOutput() {
        assertNoAbsolutePath(program.sourceFile());
        for (CanonicalDataItem item : program.dataItems()) {
            assertNoAbsolutePath(item.sourceFile());
        }
        for (CanonicalParagraph p : program.paragraphs()) {
            assertNoAbsolutePath(p.sourceFile());
            for (CanonicalStatement s : p.statements()) {
                assertNoAbsolutePath(s.sourceFile());
            }
        }
    }

    private static void assertNoAbsolutePath(String value) {
        if (value == null) {
            return;
        }
        assertFalse(value.startsWith("/"), "path absoluto detectado: " + value);
        assertFalse(value.matches("^[A-Za-z]:.*"), "path absoluto Windows detectado: " + value);
    }

    @Test
    void parsingTwiceProducesIdenticalCanonicalProgram() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("comprehensive.cbl"), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8);
        CanonicalProgram second = new CanonicalProgramExtractor().extract(
                Fixtures.path("comprehensive.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/comprehensive.cbl",
                "b".repeat(64),
                "a".repeat(64));
        assertEquals(program, second);
    }
}
