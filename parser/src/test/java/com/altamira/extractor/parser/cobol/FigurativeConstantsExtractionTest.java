package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.CanonicalConditionName;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.StatementKind;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.regex.Pattern;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Constantes figurativas (SPACES, ZERO, HIGH-VALUES, LOW-VALUES, QUOTES,
 * ALL literal) sobre fixtures/figurative-constants.cbl: {@code
 * ValueReferences.canonicalLiteralText} nunca debe filtrar {@code
 * Object.toString()} de {@code FigurativeConstantImpl} (nombre de clase +
 * hash de identidad, no reproducible entre JVMs -- ver
 * docs/LEVEL_88_SUPPORT.md). Cubre tanto el sending-area de MOVE (via
 * {@link ValueReferences#literalTextIfPure}) como el VALUE de una
 * condicion nivel 88 (via {@code CanonicalProgramExtractor#literalTextOf},
 * que delega en el mismo helper).
 */
class FigurativeConstantsExtractionTest {

    /** Forma de un Object.toString() sin override: NombreDeClase@hexHash. */
    private static final Pattern OBJECT_TO_STRING_SHAPE = Pattern.compile("[A-Za-z0-9.$]+@[0-9a-fA-F]+");

    private static CanonicalProgram program;

    @BeforeAll
    static void parseOnce() throws Exception {
        program = parseFixture();
    }

    private static CanonicalProgram parseFixture() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("figurative-constants.cbl"), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8);
        return new CanonicalProgramExtractor().extract(
                Fixtures.path("figurative-constants.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/figurative-constants.cbl",
                "b".repeat(64),
                "a".repeat(64));
    }

    private static List<CanonicalStatement> statements() {
        return program.paragraphs().get(0).statements();
    }

    private static CanonicalStatement moveTo(String targetDataItem) {
        return statements().stream()
                .filter(s -> s.kind() == StatementKind.MOVE)
                .filter(s -> s.targetDataItems().contains(targetDataItem))
                .findFirst()
                .orElseThrow(() -> new AssertionError("no MOVE targeting " + targetDataItem));
    }

    private static CanonicalConditionName condition(String name) {
        return program.conditionNames().stream()
                .filter(c -> c.name().equals(name))
                .findFirst()
                .orElseThrow(() -> new AssertionError("condition not found: " + name));
    }

    // --- MOVE de constantes figurativas: literal canonico, nunca Object.toString ---

    @Test
    void moveSpacesProducesCanonicalSpaceLiteral() {
        assertEquals("SPACE", moveTo("WS-ALPHA").assignedLiteral());
    }

    @Test
    void moveZerosAndZeroesBothProduceCanonicalZeroLiteral() {
        List<CanonicalStatement> movesToNum = statements().stream()
                .filter(s -> s.kind() == StatementKind.MOVE)
                .filter(s -> s.targetDataItems().contains("WS-NUM"))
                .toList();
        assertEquals(2, movesToNum.size(), "MOVE ZEROS y MOVE ZEROES, dos statements distintos");
        assertTrue(movesToNum.stream().allMatch(s -> "ZERO".equals(s.assignedLiteral())));
    }

    @Test
    void moveHighValueAndHighValuesBothProduceCanonicalHighValueLiteral() {
        List<CanonicalStatement> movesToHigh = statements().stream()
                .filter(s -> s.kind() == StatementKind.MOVE)
                .filter(s -> s.targetDataItems().contains("WS-HIGH"))
                .toList();
        assertEquals(2, movesToHigh.size());
        assertTrue(movesToHigh.stream().allMatch(s -> "HIGH-VALUE".equals(s.assignedLiteral())));
    }

    @Test
    void moveLowValueAndLowValuesBothProduceCanonicalLowValueLiteral() {
        List<CanonicalStatement> movesToLow = statements().stream()
                .filter(s -> s.kind() == StatementKind.MOVE)
                .filter(s -> s.targetDataItems().contains("WS-LOW"))
                .toList();
        assertEquals(2, movesToLow.size());
        assertTrue(movesToLow.stream().allMatch(s -> "LOW-VALUE".equals(s.assignedLiteral())));
    }

    @Test
    void moveQuotesProducesCanonicalQuoteLiteral() {
        assertEquals("QUOTE", moveTo("WS-QUOTE").assignedLiteral());
    }

    @Test
    void moveAllLiteralIsUnaffectedOrdinaryLiteral() {
        // ALL 'X' nunca pasa por FigurativeConstant en el ASG de ProLeap
        // (confirmado con el probe Java): sigue siendo un literal
        // ordinario, comportamiento sin cambios.
        assertEquals("X", moveTo("WS-ALLX").assignedLiteral());
    }

    @Test
    void noAssignedLiteralHasObjectToStringShape() {
        for (CanonicalStatement statement : statements()) {
            String literal = statement.assignedLiteral();
            if (literal != null) {
                assertFalse(OBJECT_TO_STRING_SHAPE.matcher(literal).matches(),
                        "assigned_literal con forma de Object.toString(): " + literal);
            }
            assertFalse(literal != null && literal.contains("FigurativeConstantImpl"),
                    "assigned_literal filtra el nombre de clase interno de ProLeap: " + literal);
        }
    }

    // --- Nivel 88 con constante figurativa: VALUE, SET condicion TO TRUE ---

    @Test
    void level88ConditionWithSpaceFigurativeConstantIsCanonical() {
        CanonicalConditionName estadoVacio = condition("ESTADO-VACIO");
        assertEquals(1, estadoVacio.values().size());
        assertEquals("SPACE", estadoVacio.values().get(0).value());
        assertNull(estadoVacio.values().get(0).throughValue());
    }

    @Test
    void level88ConditionWithZeroFigurativeConstantIsCanonical() {
        CanonicalConditionName contadorCero = condition("CONTADOR-CERO");
        assertEquals(1, contadorCero.values().size());
        assertEquals("ZERO", contadorCero.values().get(0).value());
    }

    @Test
    void setConditionToTrueWithFigurativeConstantValueIsStructurallyRepresented() {
        CanonicalStatement set = statements().stream()
                .filter(s -> s.kind() == StatementKind.SET)
                .filter(s -> "ESTADO-VACIO".equals(s.conditionNameTarget()))
                .findFirst().orElseThrow();
        assertEquals(Boolean.TRUE, set.conditionSetValue());
        // El SET nunca se reescribe como MOVE ni arrastra un literal
        // "SPACE" sintetico en variablesWritten/targetDataItems.
        assertEquals(List.of("ESTADO-VACIO"), set.targetDataItems());
    }

    @Test
    void noConditionValueHasObjectToStringShape() {
        for (CanonicalConditionName condition : program.conditionNames()) {
            for (var value : condition.values()) {
                assertFalse(OBJECT_TO_STRING_SHAPE.matcher(value.value()).matches(),
                        "condition value con forma de Object.toString(): " + value.value());
            }
        }
    }

    // --- Determinismo en el mismo proceso (record equality) -------------

    @Test
    void parsingTwiceInSameProcessProducesIdenticalCanonicalProgram() throws Exception {
        CanonicalProgram second = parseFixture();
        assertEquals(program, second);
    }

    // --- Determinismo entre procesos JVM distintos -----------------------

    @Test
    void crossProcessInvocationsProduceByteIdenticalCanonicalJson(@TempDir Path tmp) throws Exception {
        Path output1 = tmp.resolve("run1.json");
        Path output2 = tmp.resolve("run2.json");
        runParseInNewJvmProcess(Fixtures.path("figurative-constants.cbl"), output1);
        runParseInNewJvmProcess(Fixtures.path("figurative-constants.cbl"), output2);

        byte[] bytes1 = Files.readAllBytes(output1);
        byte[] bytes2 = Files.readAllBytes(output2);
        assertArrayEquals(bytes1, bytes2, "dos procesos JVM independientes deben producir bytes identicos");

        String json = new String(bytes1, StandardCharsets.UTF_8);
        assertFalse(json.contains("FigurativeConstantImpl"));
        assertFalse(OBJECT_TO_STRING_SHAPE.matcher(json).find(),
                "el JSON canonico no debe contener ninguna forma de Object.toString()");
    }

    /**
     * Lanza un proceso Java nuevo e independiente (nunca {@code
     * Main.run(...)} in-process: eso comparte la JVM del test runner, lo
     * que no demuestra nada sobre reproducibilidad entre heaps distintos)
     * reutilizando el classpath del propio test runner -- funciona bajo
     * {@code mvn test} sin depender de que la fase {@code package} ya haya
     * producido el JAR ejecutable.
     */
    private static void runParseInNewJvmProcess(Path input, Path output) throws IOException, InterruptedException {
        String javaHome = System.getProperty("java.home");
        String javaBin = Path.of(javaHome, "bin", "java").toString();
        String classpath = System.getProperty("java.class.path");
        ProcessBuilder builder = new ProcessBuilder(
                javaBin, "-cp", classpath, "com.altamira.extractor.parser.cli.Main",
                "parse",
                "--input", input.toString(),
                "--output", output.toString(),
                "--source-package-hash", "a".repeat(64),
                "--format", "FIXED");
        builder.redirectErrorStream(true);
        Process process = builder.start();
        byte[] combinedOutput = process.getInputStream().readAllBytes();
        int exitCode = process.waitFor();
        assertEquals(0, exitCode, "subprocess 'parse' debe terminar con exito. Salida:\n"
                + new String(combinedOutput, StandardCharsets.UTF_8));
    }
}
