package com.altamira.extractor.parser.cli;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** Prueba los codigos de salida documentados invocando Main.run in-process (sin `java -jar`). */
class MainRunTest {

    private static final String VALID_HASH = "a".repeat(64);
    private static final Path FIXTURES = Path.of("src", "test", "resources", "fixtures");

    private int run(PrintStream err, String... args) {
        return Main.run(args, err);
    }

    @Test
    void exitCodeZeroOnSuccessAndWritesOnlyJsonToOutputFile(@TempDir Path tmp) throws Exception {
        Path output = tmp.resolve("out.json");
        ByteArrayOutputStream errBuffer = new ByteArrayOutputStream();
        int exitCode = run(new PrintStream(errBuffer, true, StandardCharsets.UTF_8),
                "parse",
                "--input", FIXTURES.resolve("comprehensive.cbl").toString(),
                "--output", output.toString(),
                "--source-package-hash", VALID_HASH,
                "--format", "FIXED");

        assertEquals(CliExitCode.SUCCESS.code(), exitCode);
        assertTrue(Files.isRegularFile(output));
        String json = Files.readString(output, StandardCharsets.UTF_8);
        assertTrue(json.startsWith("{"));
        assertTrue(json.endsWith("}\n"), "debe terminar en newline");
        String stderr = errBuffer.toString(StandardCharsets.UTF_8);
        assertFalse(stderr.contains("\"program_name\""), "el JSON no debe aparecer en stderr");
    }

    @Test
    void exitCodeTwoOnMissingRequiredArgument() {
        ByteArrayOutputStream errBuffer = new ByteArrayOutputStream();
        int exitCode = run(new PrintStream(errBuffer, true, StandardCharsets.UTF_8),
                "parse", "--input", "in.cbl");
        assertEquals(CliExitCode.INVALID_ARGUMENTS.code(), exitCode);
    }

    @Test
    void exitCodeThreeOnInvalidCobolSyntax(@TempDir Path tmp) {
        Path output = tmp.resolve("out.json");
        ByteArrayOutputStream errBuffer = new ByteArrayOutputStream();
        int exitCode = run(new PrintStream(errBuffer, true, StandardCharsets.UTF_8),
                "parse",
                "--input", FIXTURES.resolve("invalid-syntax.cbl").toString(),
                "--output", output.toString(),
                "--source-package-hash", VALID_HASH);
        assertEquals(CliExitCode.PARSE_ERROR.code(), exitCode);
        assertFalse(Files.exists(output), "no debe quedar output parcial");
    }

    @Test
    void exitCodeThreeOnExplicitFreeFormat(@TempDir Path tmp) {
        // FREE se acepta sintacticamente (no es exit 2); falla en tiempo de
        // parseo porque ProLeap 2.4.0 no tiene un formato libre real.
        Path output = tmp.resolve("out.json");
        ByteArrayOutputStream errBuffer = new ByteArrayOutputStream();
        int exitCode = run(new PrintStream(errBuffer, true, StandardCharsets.UTF_8),
                "parse",
                "--input", FIXTURES.resolve("comprehensive.cbl").toString(),
                "--output", output.toString(),
                "--source-package-hash", VALID_HASH,
                "--format", "FREE");
        assertEquals(CliExitCode.PARSE_ERROR.code(), exitCode);
        assertFalse(Files.exists(output), "no debe quedar output parcial");
        assertTrue(errBuffer.toString(StandardCharsets.UTF_8)
                .contains("FREE source format is not supported by the configured ProLeap version."));
    }

    @Test
    void exitCodeThreeOnShortFreeFormatAutoFormat(@TempDir Path tmp) {
        // Todas las lineas de este fixture tienen menos de 80 columnas: el
        // criterio antiguo (solo longitud) lo habria aceptado por error.
        Path output = tmp.resolve("out.json");
        ByteArrayOutputStream errBuffer = new ByteArrayOutputStream();
        int exitCode = run(new PrintStream(errBuffer, true, StandardCharsets.UTF_8),
                "parse",
                "--input", FIXTURES.resolve("auto-free-short.cbl").toString(),
                "--output", output.toString(),
                "--source-package-hash", VALID_HASH,
                "--format", "AUTO");
        assertEquals(CliExitCode.PARSE_ERROR.code(), exitCode);
        assertFalse(Files.exists(output), "no debe quedar output parcial");
    }

    @Test
    void exitCodeThreeOnAmbiguousAutoFormat(@TempDir Path tmp) {
        Path output = tmp.resolve("out.json");
        ByteArrayOutputStream errBuffer = new ByteArrayOutputStream();
        int exitCode = run(new PrintStream(errBuffer, true, StandardCharsets.UTF_8),
                "parse",
                "--input", FIXTURES.resolve("auto-column1-code.cbl").toString(),
                "--output", output.toString(),
                "--source-package-hash", VALID_HASH,
                "--format", "AUTO");
        assertEquals(CliExitCode.PARSE_ERROR.code(), exitCode);
        assertFalse(Files.exists(output), "no debe quedar output parcial");
    }

    @Test
    void exitCodeThreeOnMissingCopybook(@TempDir Path tmp) {
        Path output = tmp.resolve("out.json");
        ByteArrayOutputStream errBuffer = new ByteArrayOutputStream();
        int exitCode = run(new PrintStream(errBuffer, true, StandardCharsets.UTF_8),
                "parse",
                "--input", FIXTURES.resolve("missing-copybook.cbl").toString(),
                "--output", output.toString(),
                "--source-package-hash", VALID_HASH,
                "--copybook-dir", FIXTURES.resolve("copybooks").toString());
        assertEquals(CliExitCode.PARSE_ERROR.code(), exitCode);
    }

    @Test
    void exitCodeFourOnMissingInput(@TempDir Path tmp) {
        Path output = tmp.resolve("out.json");
        ByteArrayOutputStream errBuffer = new ByteArrayOutputStream();
        int exitCode = run(new PrintStream(errBuffer, true, StandardCharsets.UTF_8),
                "parse",
                "--input", tmp.resolve("does-not-exist.cbl").toString(),
                "--output", output.toString(),
                "--source-package-hash", VALID_HASH);
        assertEquals(CliExitCode.IO_ERROR.code(), exitCode);
    }

    @Test
    void exitCodeFourWhenOutputIsNotWritable(@TempDir Path tmp) throws Exception {
        Path outputAsDirectory = tmp.resolve("out-is-a-directory.json");
        Files.createDirectory(outputAsDirectory);
        ByteArrayOutputStream errBuffer = new ByteArrayOutputStream();
        int exitCode = run(new PrintStream(errBuffer, true, StandardCharsets.UTF_8),
                "parse",
                "--input", FIXTURES.resolve("comprehensive.cbl").toString(),
                "--output", outputAsDirectory.toString(),
                "--source-package-hash", VALID_HASH);
        assertEquals(CliExitCode.IO_ERROR.code(), exitCode);
    }

    @Test
    void debugFlagPrintsStackTraceOnError(@TempDir Path tmp) {
        Path output = tmp.resolve("out.json");
        ByteArrayOutputStream withDebug = new ByteArrayOutputStream();
        run(new PrintStream(withDebug, true, StandardCharsets.UTF_8),
                "parse",
                "--input", FIXTURES.resolve("invalid-syntax.cbl").toString(),
                "--output", output.toString(),
                "--source-package-hash", VALID_HASH,
                "--debug");

        ByteArrayOutputStream withoutDebug = new ByteArrayOutputStream();
        run(new PrintStream(withoutDebug, true, StandardCharsets.UTF_8),
                "parse",
                "--input", FIXTURES.resolve("invalid-syntax.cbl").toString(),
                "--output", output.toString(),
                "--source-package-hash", VALID_HASH);

        String withDebugText = withDebug.toString(StandardCharsets.UTF_8);
        String withoutDebugText = withoutDebug.toString(StandardCharsets.UTF_8);
        assertTrue(withDebugText.contains("at com.altamira.extractor.parser"),
                "con --debug debe incluir stack trace");
        assertFalse(withoutDebugText.contains("at com.altamira.extractor.parser"),
                "sin --debug no debe incluir stack trace completo");
    }
}
