package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.CanonicalParagraph;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.CanonicalSqlAccess;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.StatementKind;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * Fase 15B3-C3-B, seccion 8 (obligatorio): prueba explicitamente que
 * {@code CanonicalStatement.variablesRead}/{@code variablesWritten} de un
 * {@code EXEC_SQL} SELECT INTO reflejan la direccion demostrada por
 * {@code EmbeddedSqlExtractor} -- no solo {@code CanonicalSqlAccess}. Sin
 * este paso, {@code CanonicalParagraph} nunca agrega estas variables
 * (ver {@code CanonicalProgramExtractor.collectOrderedUnique}) y
 * {@code dependency_builder.py} nunca puede construir DATA_DEPENDS_ON a
 * traves de SQL (auditoria 15B3-C3-A).
 */
class SqlDirectedDataFlowTest {

    private static CanonicalProgram parseOnce() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("sql-directed-data-flow.cbl"), RequestedFormat.FIXED, List.of(),
                StandardCharsets.UTF_8);
        return new CanonicalProgramExtractor().extract(
                Fixtures.path("sql-directed-data-flow.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/sql-directed-data-flow.cbl",
                "b".repeat(64),
                "a".repeat(64));
    }

    @Test
    void selectIntoStatementWritesOutputVariableAndReadsPredicateVariable() throws Exception {
        CanonicalProgram program = parseOnce();

        var consultar = program.paragraphs().stream()
                .filter(p -> p.name().equals("CONSULTAR-SALDO-PARA"))
                .findFirst()
                .orElseThrow();

        List<CanonicalStatement> execSql = consultar.statements().stream()
                .filter(s -> s.kind() == StatementKind.EXEC_SQL)
                .toList();
        assertEquals(1, execSql.size());
        CanonicalStatement statement = execSql.get(0);

        assertEquals(List.of("WS-CUENTA"), statement.variablesRead());
        assertEquals(List.of("WS-SALDO"), statement.variablesWritten());

        assertEquals(1, statement.sqlAccess().size());
        CanonicalSqlAccess access = statement.sqlAccess().get(0);
        assertEquals("CUENTAS", access.table());
        assertEquals(List.of("WS-CUENTA"), access.inputHostVariables());
        assertEquals(List.of("WS-SALDO"), access.outputHostVariables());
        assertEquals(List.of("WS-CUENTA"), access.predicateHostVariables());
    }

    @Test
    void paragraphAggregatesVariablesReadWrittenFromExecSqlStatement() throws Exception {
        CanonicalProgram program = parseOnce();

        CanonicalParagraph consultar = program.paragraphs().stream()
                .filter(p -> p.name().equals("CONSULTAR-SALDO-PARA"))
                .findFirst()
                .orElseThrow();

        assertTrue(consultar.variablesRead().contains("WS-CUENTA"));
        assertTrue(consultar.variablesWritten().contains("WS-SALDO"));
    }

    /**
     * Correccion pre-commit posterior a la entrega inicial de C3-B: un
     * SELECT con una expresion en la lista de columnas
     * ({@code :WS-FACTOR * SALDO}) deja WS-FACTOR fuera de INTO/WHERE --
     * {@code CanonicalSqlAccess.inputHostVariables}/{@code
     * outputHostVariables} solo reflejan WHERE/INTO (sin cambios en
     * {@code EmbeddedSqlExtractor}), pero {@code
     * StatementExtractor.convertExecSql} debe rechazar la promocion a
     * {@code CanonicalStatement.variablesRead}/{@code variablesWritten}
     * porque WS-FACTOR (legacy {@code hostVariables}) nunca queda
     * contabilizado en esa union -- publicar variablesRead/variablesWritten
     * parciales fabricaria un DATA_DEPENDS_ON incompleto.
     */
    @Test
    void selectExpressionWithUnresolvedHostVariableNeverPromotesPartialDirection() throws Exception {
        CanonicalProgram program = parseOnce();

        var consultarFactor = program.paragraphs().stream()
                .filter(p -> p.name().equals("CONSULTAR-FACTOR-PARA"))
                .findFirst()
                .orElseThrow();

        List<CanonicalStatement> execSql = consultarFactor.statements().stream()
                .filter(s -> s.kind() == StatementKind.EXEC_SQL)
                .toList();
        assertEquals(1, execSql.size());
        CanonicalStatement statement = execSql.get(0);

        // El gate de completitud (StatementExtractor.convertExecSql) deja
        // ambas listas vacias -- nunca una direccion parcial.
        assertTrue(statement.variablesRead().isEmpty());
        assertTrue(statement.variablesWritten().isEmpty());

        // CanonicalSqlAccess conserva la direccion parcial ya demostrada
        // por EmbeddedSqlExtractor (sin cambios en esa clase): el gate
        // vive unicamente en la promocion hacia CanonicalStatement.
        assertEquals(1, statement.sqlAccess().size());
        CanonicalSqlAccess access = statement.sqlAccess().get(0);
        assertEquals(List.of("WS-ID"), access.inputHostVariables());
        assertEquals(List.of("WS-RESULTADO"), access.outputHostVariables());
        assertTrue(access.hostVariables().contains("WS-FACTOR"));
        assertFalse(access.inputHostVariables().contains("WS-FACTOR"));
        assertFalse(access.outputHostVariables().contains("WS-FACTOR"));

        // El paragraph tampoco agrega estas variables (agnostico al gate:
        // solo agrega lo que ya esta en el CanonicalStatement).
        assertFalse(consultarFactor.variablesRead().contains("WS-ID"));
        assertFalse(consultarFactor.variablesWritten().contains("WS-RESULTADO"));
    }
}
