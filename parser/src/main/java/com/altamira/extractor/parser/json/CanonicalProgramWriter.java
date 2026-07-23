package com.altamira.extractor.parser.json;

import com.altamira.extractor.parser.model.CanonicalProgram;
import com.fasterxml.jackson.core.util.DefaultIndenter;
import com.fasterxml.jackson.core.util.DefaultPrettyPrinter;
import com.fasterxml.jackson.databind.MapperFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.json.JsonMapper;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

/**
 * Serializa CanonicalProgram a JSON estable (claves ordenadas
 * alfabeticamente, UTF-8, 2 espacios de indentacion, newline final, sin
 * timestamps ni valores no deterministicos) y lo escribe atomicamente:
 * archivo temporal unico en el mismo directorio del destino, flush,
 * fsync-equivalente (FileChannel.force), y promocion via
 * Files.move(ATOMIC_MOVE). El temporal se elimina si algo falla antes de
 * la promocion.
 */
public final class CanonicalProgramWriter {

    private static final DefaultPrettyPrinter PRETTY_PRINTER = createPrettyPrinter();

    private final ObjectMapper mapper;

    public CanonicalProgramWriter() {
        // ObjectMapper.configure(MapperFeature, boolean) esta deprecado desde
        // Jackson 2.17 en favor del patron builder inmutable (MapperBuilder),
        // que expone el mismo configure(...) sin deprecar.
        this.mapper = JsonMapper.builder()
                .propertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
                .configure(MapperFeature.SORT_PROPERTIES_ALPHABETICALLY, true)
                .build();
    }

    public void write(CanonicalProgram program, Path output) throws IOException {
        byte[] jsonBytes = toStableJsonBytes(program);

        Path parent = output.toAbsolutePath().getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
        Path tmp = Files.createTempFile(parent, "." + output.getFileName() + ".", ".tmp");
        try {
            try (OutputStream out = Files.newOutputStream(tmp)) {
                out.write(jsonBytes);
                out.flush();
            }
            Files.move(tmp, output, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } finally {
            Files.deleteIfExists(tmp);
        }
    }

    byte[] toStableJsonBytes(CanonicalProgram program) throws IOException {
        String json = mapper.writer(PRETTY_PRINTER).writeValueAsString(program) + "\n";
        return json.getBytes(StandardCharsets.UTF_8);
    }

    private static DefaultPrettyPrinter createPrettyPrinter() {
        DefaultPrettyPrinter printer = new DefaultPrettyPrinter();
        DefaultIndenter indenter = new DefaultIndenter("  ", "\n");
        printer.indentObjectsWith(indenter);
        printer.indentArraysWith(indenter);
        return printer;
    }
}
