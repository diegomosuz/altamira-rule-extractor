package com.altamira.extractor.parser.json;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.CanonicalConditionName;
import com.altamira.extractor.parser.model.CanonicalConditionValue;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.LocationKind;
import com.altamira.extractor.parser.model.SourceFormat;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class CanonicalProgramWriterTest {

    private static CanonicalProgram sampleProgram() {
        return new CanonicalProgram(
                "1.0", "SAMPLE1", "sample.cbl", "a".repeat(64), "b".repeat(64), SourceFormat.FIXED,
                "UTF-8", List.of(), List.of(), List.of(), List.of(), List.of());
    }

    @Test
    void writesSortedIndentedUtf8JsonWithTrailingNewline(@TempDir Path tmp) throws IOException {
        Path output = tmp.resolve("out.json");
        new CanonicalProgramWriter().write(sampleProgram(), output);

        String json = Files.readString(output, StandardCharsets.UTF_8);
        // "1.0" (sin nivel 88): condition_names esta vacia y se omite via
        // @JsonInclude(NON_EMPTY) -- data_items vuelve a ser la primera
        // clave alfabetica, exactamente como antes de la Fase 3.
        assertTrue(json.startsWith("{\n  \"data_items\""), "las claves deben ir ordenadas alfabeticamente");
        assertFalse(json.contains("condition_names"), "condition_names vacia no debe aparecer en el JSON \"1.0\"");
        assertTrue(json.endsWith("}\n"));
        assertFalse(json.contains("\r"), "solo LF, nunca CRLF");
    }

    @Test
    void writesConditionNamesKeyWhenPopulated(@TempDir Path tmp) throws IOException {
        CanonicalConditionValue value = new CanonicalConditionValue(
                "0005", null, null, null, LocationKind.UNKNOWN);
        CanonicalConditionName condition = new CanonicalConditionName(
                "COD-X", "WS-COD.COD-X", "WS-COD", "WS-COD", List.of(value), null, null, LocationKind.UNKNOWN);
        CanonicalProgram program = new CanonicalProgram(
                "1.1", "SAMPLE1", "sample.cbl", "a".repeat(64), "b".repeat(64), SourceFormat.FIXED,
                "UTF-8", List.of(), List.of(condition), List.of(), List.of(), List.of());
        Path output = tmp.resolve("out.json");
        new CanonicalProgramWriter().write(program, output);

        String json = Files.readString(output, StandardCharsets.UTF_8);
        assertTrue(json.contains("\"condition_names\""), "condition_names poblada debe aparecer en el JSON");
        assertTrue(json.contains("\"schema_version\" : \"1.1\""));
    }

    @Test
    void writingTwiceProducesByteIdenticalOutput(@TempDir Path tmp) throws IOException {
        Path first = tmp.resolve("first.json");
        Path second = tmp.resolve("second.json");
        CanonicalProgramWriter writer = new CanonicalProgramWriter();
        writer.write(sampleProgram(), first);
        writer.write(sampleProgram(), second);

        assertArrayEquals(Files.readAllBytes(first), Files.readAllBytes(second));
    }

    @Test
    void leavesNoTemporaryFileAfterSuccessfulWrite(@TempDir Path tmp) throws IOException {
        Path output = tmp.resolve("out.json");
        new CanonicalProgramWriter().write(sampleProgram(), output);

        try (Stream<Path> entries = Files.list(tmp)) {
            List<Path> remaining = entries.toList();
            assertEquals(List.of(output), remaining, "solo debe quedar el archivo final, ningun temporal");
        }
    }

    @Test
    void leavesNoTemporaryFileWhenWriteFails(@TempDir Path tmp) throws IOException {
        Path outputAsDirectory = tmp.resolve("out-is-a-directory.json");
        Files.createDirectory(outputAsDirectory);

        assertThrows(IOException.class, () -> new CanonicalProgramWriter().write(sampleProgram(), outputAsDirectory));

        try (Stream<Path> entries = Files.list(tmp)) {
            List<Path> remaining = entries.toList();
            assertEquals(List.of(outputAsDirectory), remaining, "no debe quedar ningun temporal tras el fallo");
        }
    }
}
