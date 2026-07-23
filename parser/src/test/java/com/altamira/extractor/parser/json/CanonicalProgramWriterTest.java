package com.altamira.extractor.parser.json;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.CanonicalProgram;
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
                "UTF-8", List.of(), List.of(), List.of(), List.of());
    }

    @Test
    void writesSortedIndentedUtf8JsonWithTrailingNewline(@TempDir Path tmp) throws IOException {
        Path output = tmp.resolve("out.json");
        new CanonicalProgramWriter().write(sampleProgram(), output);

        String json = Files.readString(output, StandardCharsets.UTF_8);
        assertTrue(json.startsWith("{\n  \"data_items\""), "las claves deben ir ordenadas alfabeticamente");
        assertTrue(json.endsWith("}\n"));
        assertFalse(json.contains("\r"), "solo LF, nunca CRLF");
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
