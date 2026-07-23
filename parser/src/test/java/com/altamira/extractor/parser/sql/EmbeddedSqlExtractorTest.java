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
}
