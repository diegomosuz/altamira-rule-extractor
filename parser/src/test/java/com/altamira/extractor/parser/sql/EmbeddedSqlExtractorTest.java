package com.altamira.extractor.parser.sql;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.TableAccessOperation;
import java.util.List;
import org.junit.jupiter.api.Test;

class EmbeddedSqlExtractorTest {

    private final EmbeddedSqlExtractor extractor = new EmbeddedSqlExtractor();

    @Test
    void selectWithHostVariablesAndPredicate() {
        var result = extractor.extract(
                "SELECT SALDO INTO :WS-SALDO FROM CUENTAS WHERE ID_CUENTA = :WS-CUENTA-ID");
        assertTrue(result.interpreted());
        assertEquals(1, result.accesses().size());
        var access = result.accesses().get(0);
        assertEquals("CUENTAS", access.table());
        assertEquals(TableAccessOperation.READS, access.operation());
        assertTrue(access.hostVariables().containsAll(List.of("WS-SALDO", "WS-CUENTA-ID")));
        assertTrue(access.predicateText().contains("WHERE"));
    }

    @Test
    void selectWithMultipleTablesProducesOneAccessPerTable() {
        var result = extractor.extract("SELECT A.X FROM CUENTAS A, MOVIMIENTOS B WHERE A.ID = B.ID");
        assertTrue(result.interpreted());
        assertEquals(2, result.accesses().size());
        assertEquals("CUENTAS", result.accesses().get(0).table());
        assertEquals("MOVIMIENTOS", result.accesses().get(1).table());
    }

    @Test
    void insertMapsToInserts() {
        var result = extractor.extract(
                "INSERT INTO MOVIMIENTOS (ID_CUENTA, MONTO) VALUES (:WS-CUENTA-ID, :WS-MONTO)");
        assertTrue(result.interpreted());
        var access = result.accesses().get(0);
        assertEquals("MOVIMIENTOS", access.table());
        assertEquals(TableAccessOperation.INSERTS, access.operation());
        assertTrue(access.hostVariables().containsAll(List.of("WS-CUENTA-ID", "WS-MONTO")));
    }

    @Test
    void updateMapsToUpdates() {
        var result = extractor.extract("UPDATE CUENTAS SET SALDO = :WS-SALDO WHERE ID_CUENTA = :WS-ID");
        assertTrue(result.interpreted());
        var access = result.accesses().get(0);
        assertEquals("CUENTAS", access.table());
        assertEquals(TableAccessOperation.UPDATES, access.operation());
    }

    @Test
    void deleteMapsToWritesNotADedicatedDeleteValue() {
        var result = extractor.extract("DELETE FROM MOVIMIENTOS WHERE ID_CUENTA = :WS-ID");
        assertTrue(result.interpreted());
        var access = result.accesses().get(0);
        assertEquals("MOVIMIENTOS", access.table());
        assertEquals(TableAccessOperation.WRITES, access.operation());
    }

    @Test
    void unsupportedTextIsReportedNotInvented() {
        var result = extractor.extract("EXEC SQL WHENEVER SQLERROR CONTINUE END-EXEC");
        assertFalse(result.interpreted());
        assertTrue(result.accesses().isEmpty());
        assertTrue(result.reason() != null && !result.reason().isBlank());
    }

    @Test
    void blankTextIsUnsupported() {
        var result = extractor.extract("   ");
        assertFalse(result.interpreted());
    }

    // -----------------------------------------------------------------
    // Fase 15B3-C3-B: direccion de host variables (input/output/predicate)
    // -----------------------------------------------------------------

    @Test
    void selectIntoAssignsOutputAndWherePredicateAssignsInput() {
        var result = extractor.extract(
                "SELECT SALDO INTO :WS-SALDO FROM CUENTAS WHERE CUENTA = :WS-CUENTA");
        assertTrue(result.interpreted());
        var access = result.accesses().get(0);
        assertEquals(List.of("WS-CUENTA"), access.inputHostVariables());
        assertEquals(List.of("WS-SALDO"), access.outputHostVariables());
        assertEquals(List.of("WS-CUENTA"), access.predicateHostVariables());
        assertFalse(access.hasIndicatorVariables());
    }

    @Test
    void selectMultiColumnIntoPreservesPositionalOrderAndSelectedColumns() {
        var result = extractor.extract(
                "SELECT SALDO, ESTADO, LIMITE INTO :WS-SALDO, :WS-ESTADO, :WS-LIMITE FROM CUENTAS");
        assertTrue(result.interpreted());
        var access = result.accesses().get(0);
        assertEquals(List.of("WS-SALDO", "WS-ESTADO", "WS-LIMITE"), access.outputHostVariables());
        assertEquals(List.of("SALDO", "ESTADO", "LIMITE"), access.selectedColumns());
        assertEquals(access.selectedColumns().size(), access.outputHostVariables().size());
    }

    @Test
    void selectListWithFunctionNeverProducesSelectedColumns() {
        var result = extractor.extract("SELECT COUNT(*) INTO :WS-TOTAL FROM CUENTAS");
        assertTrue(result.interpreted());
        var access = result.accesses().get(0);
        assertEquals(List.of(), access.selectedColumns());
        // La ausencia de columnas nunca degrada output_host_variables: la
        // direccion INTO sigue siendo estructuralmente segura por si sola.
        assertEquals(List.of("WS-TOTAL"), access.outputHostVariables());
    }

    @Test
    void selectMismatchedColumnCountNeverProducesSelectedColumns() {
        var result = extractor.extract(
                "SELECT SALDO, ESTADO INTO :WS-SALDO FROM CUENTAS");
        assertTrue(result.interpreted());
        var access = result.accesses().get(0);
        assertEquals(List.of(), access.selectedColumns());
    }

    @Test
    void insertValuesAssignsInputNeverOutput() {
        var result = extractor.extract(
                "INSERT INTO MOVIMIENTOS (ID_CUENTA, MONTO) VALUES (:WS-CUENTA-ID, :WS-MONTO)");
        assertTrue(result.interpreted());
        var access = result.accesses().get(0);
        assertEquals(List.of("WS-CUENTA-ID", "WS-MONTO"), access.inputHostVariables());
        assertEquals(List.of(), access.outputHostVariables());
        assertEquals(List.of(), access.predicateHostVariables());
    }

    @Test
    void updateSetAndWhereAssignInputNeverOutput() {
        var result = extractor.extract(
                "UPDATE CUENTAS SET SALDO = :WS-NUEVO-SALDO, ESTADO = :WS-ESTADO "
                        + "WHERE CUENTA = :WS-CUENTA");
        assertTrue(result.interpreted());
        var access = result.accesses().get(0);
        assertTrue(access.inputHostVariables().containsAll(
                List.of("WS-NUEVO-SALDO", "WS-ESTADO", "WS-CUENTA")));
        assertEquals(List.of(), access.outputHostVariables());
        assertEquals(List.of("WS-CUENTA"), access.predicateHostVariables());
    }

    @Test
    void deleteWhereAssignsInputNeverOutput() {
        var result = extractor.extract("DELETE FROM MOVIMIENTOS WHERE ID_CUENTA = :WS-ID");
        assertTrue(result.interpreted());
        var access = result.accesses().get(0);
        assertEquals(List.of("WS-ID"), access.inputHostVariables());
        assertEquals(List.of(), access.outputHostVariables());
        assertEquals(List.of("WS-ID"), access.predicateHostVariables());
    }

    // -----------------------------------------------------------------
    // Fase 15B3-C3-B, seccion 13: JOIN explicito -> unsupported
    // conservador, nunca tabla parcial silenciosa.
    // -----------------------------------------------------------------

    @Test
    void selectWithExplicitJoinIsUnsupportedNeverPartialTable() {
        var result = extractor.extract(
                "SELECT A.X FROM CUENTAS A JOIN MOVIMIENTOS B ON A.ID = B.ID");
        assertFalse(result.interpreted());
        assertTrue(result.accesses().isEmpty(), "JOIN nunca debe producir una tabla parcial");
    }

    @Test
    void selectWithInnerJoinIsUnsupported() {
        var result = extractor.extract(
                "SELECT A.X FROM CUENTAS A INNER JOIN MOVIMIENTOS B ON A.ID = B.ID");
        assertFalse(result.interpreted());
        assertTrue(result.accesses().isEmpty());
    }

    @Test
    void selectWithLeftJoinIsUnsupported() {
        var result = extractor.extract(
                "SELECT A.X FROM CUENTAS A LEFT JOIN MOVIMIENTOS B ON A.ID = B.ID");
        assertFalse(result.interpreted());
        assertTrue(result.accesses().isEmpty());
    }

    @Test
    void selectWithRightJoinIsUnsupported() {
        var result = extractor.extract(
                "SELECT A.X FROM CUENTAS A RIGHT JOIN MOVIMIENTOS B ON A.ID = B.ID");
        assertFalse(result.interpreted());
        assertTrue(result.accesses().isEmpty());
    }

    @Test
    void selectWithFullJoinIsUnsupported() {
        var result = extractor.extract(
                "SELECT A.X FROM CUENTAS A FULL JOIN MOVIMIENTOS B ON A.ID = B.ID");
        assertFalse(result.interpreted());
        assertTrue(result.accesses().isEmpty());
    }

    @Test
    void selectWithCommaSeparatedTablesIsStillSupportedNotConfusedWithJoin() {
        // Regresion: el fix de JOIN no debe afectar el caso ya soportado de
        // multiples tablas separadas por coma (selectWithMultipleTablesProducesOneAccessPerTable).
        var result = extractor.extract("SELECT A.X FROM CUENTAS A, MOVIMIENTOS B WHERE A.ID = B.ID");
        assertTrue(result.interpreted());
        assertEquals(2, result.accesses().size());
    }

    // -----------------------------------------------------------------
    // Fase 15B3-C3-B, seccion 15: variable indicadora (":VAR:IND") nunca
    // recibe direccion -- toda la sentencia degrada a no-resuelto.
    // -----------------------------------------------------------------

    @Test
    void selectWithIndicatorVariableNeverAssignsDirection() {
        var result = extractor.extract(
                "SELECT SALDO INTO :WS-SALDO:WS-IND FROM CUENTAS WHERE CUENTA = :WS-CUENTA");
        assertTrue(result.interpreted());
        var access = result.accesses().get(0);
        assertTrue(access.hasIndicatorVariables());
        assertEquals(List.of(), access.inputHostVariables());
        assertEquals(List.of(), access.outputHostVariables());
        assertEquals(List.of(), access.predicateHostVariables());
        assertEquals(List.of(), access.selectedColumns());
        // host_variables legacy se conserva por compatibilidad.
        assertTrue(access.hostVariables().containsAll(List.of("WS-SALDO", "WS-IND", "WS-CUENTA")));
    }
}
