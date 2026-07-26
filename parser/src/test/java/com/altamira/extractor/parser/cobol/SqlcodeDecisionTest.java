package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.CanonicalDataItem;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.StatementKind;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.Test;

/**
 * Matriz de complejidad (Prompt 15), caso SQLCODE: demuestra unicamente
 * lo que el modelo canonico realmente representa cuando SQLCODE se
 * declara como DataItem de WORKING-STORAGE (el registro especial
 * implicito de DB2 no forma parte de este alcance -- ver observacion en
 * docs/SUPPORTED_COMPLEXITY_STRATEGY.md) y se usa como condicion de una
 * decision posterior a un EXEC SQL. No se afirma ninguna semantica DB2
 * (que codigo significa exito/row-not-found/etc.): solo se comprueba
 * que el DataItem y la Decision quedan representados igual que
 * cualquier otro DataItem/IF.
 */
class SqlcodeDecisionTest {

    private static CanonicalProgram parseOnce() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("sqlcode-decision.cbl"), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8);
        return new CanonicalProgramExtractor().extract(
                Fixtures.path("sqlcode-decision.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/sqlcode-decision.cbl",
                "b".repeat(64),
                "a".repeat(64));
    }

    @Test
    void sqlcodeIsExtractedAsAnOrdinaryCanonicalDataItem() throws Exception {
        CanonicalProgram program = parseOnce();

        Optional<CanonicalDataItem> sqlcode = program.dataItems().stream()
                .filter(item -> item.name().equals("SQLCODE"))
                .findFirst();
        assertTrue(sqlcode.isPresent(), "SQLCODE declarado en WORKING-STORAGE debe quedar como DataItem canonico");
        assertEquals(1, sqlcode.get().level());
    }

    @Test
    void decisionOnSqlcodeIsExtractedLikeAnyOtherIf() throws Exception {
        CanonicalProgram program = parseOnce();

        var mainPara = program.paragraphs().stream()
                .filter(p -> p.name().equals("MAIN-PARA"))
                .findFirst()
                .orElseThrow();

        List<CanonicalStatement> ifs = mainPara.statements().stream()
                .filter(s -> s.kind() == StatementKind.IF)
                .toList();
        assertEquals(1, ifs.size());
        CanonicalStatement decision = ifs.get(0);
        assertTrue(decision.normalizedExpression().contains("SQLCODE"));
        assertTrue(decision.operands().contains("SQLCODE"));

        // El branch de la decision escribe el codigo de retorno: la
        // misma forma estructural (MOVE literal a un DataItem) que
        // cualquier otra decision con efecto de retorno ya cubierta por
        // el fixture de referencia (PROGRULE1 en tests/e2e_support.py).
        assertTrue(mainPara.variablesWritten().contains("WS-COD-RETORNO"));
    }

    @Test
    void execSqlPrecedingTheDecisionIsRepresentedWithoutInventingDb2Semantics() throws Exception {
        CanonicalProgram program = parseOnce();

        var mainPara = program.paragraphs().stream()
                .filter(p -> p.name().equals("MAIN-PARA"))
                .findFirst()
                .orElseThrow();

        List<CanonicalStatement> execSql = mainPara.statements().stream()
                .filter(s -> s.kind() == StatementKind.EXEC_SQL)
                .toList();
        assertEquals(1, execSql.size());
        assertEquals(1, execSql.get(0).sqlAccess().size());
        assertEquals("CUENTAS", execSql.get(0).sqlAccess().get(0).table());
        assertEquals(
                com.altamira.extractor.parser.model.TableAccessOperation.READS,
                execSql.get(0).sqlAccess().get(0).operation());

        // Nada en unsupportedConstructs ni en warnings menciona SQLCODE:
        // no se inventa ninguna interpretacion de codigos DB2.
        assertTrue(program.unsupportedConstructs().stream()
                .noneMatch(w -> w.toUpperCase(java.util.Locale.ROOT).contains("SQLCODE")));
    }
}
