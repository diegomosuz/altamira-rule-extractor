package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.json.CanonicalProgramWriter;
import com.altamira.extractor.parser.model.CanonicalDataItem;
import com.altamira.extractor.parser.model.CanonicalProgram;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * Parsea fixtures/declared-value.cbl una sola vez y verifica declared_value
 * (Fase 15B3-C5-B): VALUE simple numerico/alfanumerico, constante
 * figurativa, ausencia de VALUE, y que multiples VALUE/THRU nunca producen
 * un declared_value simple falso/parcial.
 */
class DeclaredValueExtractionTest {

    private static CanonicalProgram program;

    @BeforeAll
    static void parseOnce() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("declared-value.cbl"), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8);
        program = new CanonicalProgramExtractor().extract(
                Fixtures.path("declared-value.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/declared-value.cbl",
                "b".repeat(64),
                "a".repeat(64));
    }

    private static CanonicalDataItem dataItem(String name) {
        return program.dataItems().stream()
                .filter(item -> item.name().equals(name))
                .findFirst()
                .orElseThrow(() -> new AssertionError("data item not found: " + name));
    }

    @Test
    void numericSimpleValueIsCaptured() {
        assertEquals("1000", dataItem("WS-LIMITE").declaredValue());
    }

    @Test
    void alphanumericSimpleValueIsCaptured() {
        assertEquals("A", dataItem("WS-ESTADO").declaredValue());
    }

    @Test
    void figurativeConstantIsCaptured() {
        assertEquals("SPACE", dataItem("WS-COD-RETORNO").declaredValue());
        assertEquals("ZERO", dataItem("WS-TOTAL").declaredValue());
    }

    @Test
    void absenceOfValueProducesNull() {
        assertNull(dataItem("WS-CONTADOR").declaredValue());
    }

    @Test
    void multipleValueNeverProducesSimpleDeclaredValue() {
        assertNull(dataItem("WS-MULTI").declaredValue());
        assertTrue(program.unsupportedConstructs().stream()
                .anyMatch(note -> note.contains("WS-MULTI") && note.contains("DATA_ITEM_VALUE")));
    }

    @Test
    void thruNeverProducesSimpleDeclaredValue() {
        assertNull(dataItem("WS-RANGO").declaredValue());
        assertTrue(program.unsupportedConstructs().stream()
                .anyMatch(note -> note.contains("WS-RANGO") && note.contains("DATA_ITEM_VALUE")));
    }

    @Test
    void jsonIncludesDeclaredValueOnlyPerContract(@TempDir Path tmp) throws IOException {
        Path output = tmp.resolve("out.json");
        new CanonicalProgramWriter().write(program, output);
        String json = Files.readString(output, StandardCharsets.UTF_8);

        assertTrue(json.contains("\"declared_value\" : \"1000\""), "WS-LIMITE debe serializar declared_value");
        assertTrue(json.contains("\"declared_value\" : null"), "WS-CONTADOR debe serializar declared_value null");
    }
}
