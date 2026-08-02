package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.json.CanonicalProgramWriter;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.ProgramTerminationKind;
import com.altamira.extractor.parser.model.StatementKind;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Distincion estructural GOBACK/STOP RUN/EXIT PROGRAM (Fase 7b de la
 * ampliacion semantica, real ProLeap/JAR real, ver
 * docs/INTERPROCEDURAL_PROPAGATION.md). Cada {@code @Test} parsea su
 * propio fixture minimo (nunca comparte {@code @BeforeAll}: los fixtures
 * de esta clase son deliberadamente pequenos y de proposito unico).
 */
class ProgramTerminationClassificationTest {

    private static CanonicalProgram parse(String fixture, String programName) throws Exception {
        var result = new ProLeapCobolParser().parse(
                Fixtures.path(fixture), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8);
        return new CanonicalProgramExtractor().extract(
                Fixtures.path(fixture), StandardCharsets.UTF_8, result,
                "01-codigo/cobol/" + fixture, "c".repeat(64), "a".repeat(64));
    }

    private static List<CanonicalStatement> statementsOf(CanonicalProgram program) {
        return program.paragraphs().stream().flatMap(p -> p.statements().stream()).toList();
    }

    // --- 1/2/3: GOBACK/EXIT PROGRAM/STOP RUN se clasifican estructuralmente ---

    @Test
    void gobackIsClassifiedAsProgramTerminationGoback() throws Exception {
        CanonicalProgram program = parse("program-termination-kinds.cbl", "TERMKINDS");
        List<CanonicalStatement> gobacks = statementsOf(program).stream()
                .filter(s -> s.kind() == StatementKind.PROGRAM_TERMINATION
                        && s.programTerminationKind() == ProgramTerminationKind.GOBACK)
                .toList();
        assertEquals(1, gobacks.size());
        // Statement condicional (dentro del IF WS-FLAG = 'A'): parentStatementId
        // poblado -- la clasificacion estructural no depende de si es
        // incondicional o no (eso es responsabilidad de Fase 7, no de este nivel).
        assertTrue(gobacks.get(0).parentStatementId() != null);
    }

    @Test
    void exitProgramIsClassifiedAsProgramTerminationExitProgram() throws Exception {
        CanonicalProgram program = parse("program-termination-kinds.cbl", "TERMKINDS");
        List<CanonicalStatement> exits = statementsOf(program).stream()
                .filter(s -> s.kind() == StatementKind.PROGRAM_TERMINATION
                        && s.programTerminationKind() == ProgramTerminationKind.EXIT_PROGRAM)
                .toList();
        assertEquals(1, exits.size());
        assertTrue(exits.get(0).parentStatementId() != null);
    }

    @Test
    void stopRunIsClassifiedAsProgramTerminationStopRun() throws Exception {
        CanonicalProgram program = parse("program-termination-kinds.cbl", "TERMKINDS");
        List<CanonicalStatement> stops = statementsOf(program).stream()
                .filter(s -> s.kind() == StatementKind.PROGRAM_TERMINATION
                        && s.programTerminationKind() == ProgramTerminationKind.STOP_RUN)
                .toList();
        assertEquals(1, stops.size());
        // Unico statement top-level e incondicional del fixture (final del
        // paragraph, fuera de cualquier IF).
        assertNull(stops.get(0).parentStatementId());
    }

    @Test
    void mixedFixtureSchemaVersionIs13() throws Exception {
        CanonicalProgram program = parse("program-termination-kinds.cbl", "TERMKINDS");
        assertEquals("1.3", program.schemaVersion());
    }

    // --- STOP <literal> (no RUN) se clasifica UNKNOWN, nunca inventado como STOP_RUN ---

    @Test
    void stopLiteralIsClassifiedAsProgramTerminationUnknown() throws Exception {
        CanonicalProgram program = parse("program-termination-stop-literal.cbl", "STOPLIT");
        List<CanonicalStatement> stops = statementsOf(program).stream()
                .filter(s -> s.kind() == StatementKind.PROGRAM_TERMINATION)
                .toList();
        assertEquals(1, stops.size());
        assertEquals(ProgramTerminationKind.UNKNOWN, stops.get(0).programTerminationKind());
        assertTrue(
                program.unsupportedConstructs().stream()
                        .anyMatch(w -> w.contains("STOP") && w.contains("PROGRAM_TERMINATION")),
                "STOP sin RUN debe reportarse como unsupported, nunca clasificado en silencio como STOP_RUN");
    }

    // --- EXIT simple (sin PROGRAM) NUNCA es un terminador de programa ---

    @Test
    void bareExitStaysOtherNeverProgramTermination() throws Exception {
        CanonicalProgram program = parse("program-termination-bare-exit.cbl", "BAREEXIT");
        List<CanonicalStatement> programTerminations = statementsOf(program).stream()
                .filter(s -> s.kind() == StatementKind.PROGRAM_TERMINATION)
                .toList();
        // Unicamente el STOP RUN final es PROGRAM_TERMINATION; el EXIT
        // simple de EXIT-PARA permanece kind=OTHER (marcador no-operativo,
        // nunca retorna ni termina nada).
        assertEquals(1, programTerminations.size());
        assertEquals(ProgramTerminationKind.STOP_RUN, programTerminations.get(0).programTerminationKind());

        List<CanonicalStatement> otherStatements = statementsOf(program).stream()
                .filter(s -> s.kind() == StatementKind.OTHER)
                .toList();
        assertEquals(1, otherStatements.size());
    }

    // --- 4: nunca se usa source_text para clasificar ---

    @Test
    void classificationIgnoresTextualDecoysInCommentsAndIdentifiers() throws Exception {
        // WS-GOBACK-FLAG (nombre de dato) y un comentario que menciona
        // literalmente "STOP RUN"/"EXIT PROGRAM" nunca deben influir en la
        // clasificacion de los statements reales: solo el GOBACK genuino al
        // final se clasifica PROGRAM_TERMINATION; el MOVE que solo
        // MENCIONA "GOBACK" en el nombre del dato permanece kind=MOVE.
        CanonicalProgram program = parse("program-termination-text-decoys.cbl", "TERMTEXT");
        List<CanonicalStatement> statements = statementsOf(program);

        List<CanonicalStatement> moves = statements.stream()
                .filter(s -> s.kind() == StatementKind.MOVE)
                .toList();
        assertEquals(1, moves.size());
        assertTrue(moves.get(0).targetDataItems().contains("WS-GOBACK-FLAG"));

        List<CanonicalStatement> terminations = statements.stream()
                .filter(s -> s.kind() == StatementKind.PROGRAM_TERMINATION)
                .toList();
        assertEquals(1, terminations.size());
        assertEquals(ProgramTerminationKind.GOBACK, terminations.get(0).programTerminationKind());
    }

    // --- 16: programas sin estas sentencias conservan schema y bytes historicos ---

    @Test
    void programWithoutAnyTerminatorKeepsHistoricalSchemaVersionAndOmitsNewField(@TempDir Path tempDir)
            throws Exception {
        CanonicalProgram program = parse("program-termination-none.cbl", "NOTERM");
        assertEquals("1.0", program.schemaVersion());
        assertTrue(statementsOf(program).stream().noneMatch(s -> s.kind() == StatementKind.PROGRAM_TERMINATION));
        assertTrue(statementsOf(program).stream().allMatch(s -> s.programTerminationKind() == null));

        Path output = tempDir.resolve("no-terminator.json");
        new CanonicalProgramWriter().write(program, output);
        String json = Files.readString(output, StandardCharsets.UTF_8);
        assertFalse(
                json.contains("program_termination_kind"),
                "un programa sin GOBACK/STOP RUN/EXIT PROGRAM no debe agregar la clave nueva al JSON");
    }
}
