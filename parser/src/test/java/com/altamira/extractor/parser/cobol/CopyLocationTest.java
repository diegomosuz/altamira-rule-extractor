package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.CanonicalDataItem;
import com.altamira.extractor.parser.model.CanonicalParagraph;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.LocationKind;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.Test;

/**
 * Cuando el programa usa COPY, ProLeap solo expone el stream ya expandido:
 * no se puede demostrar de que archivo fisico vino cada linea. Verifica
 * que la extraccion nunca atribuye esas lineas al programa principal
 * (source_file null, location_kind=PREPROCESSED_STREAM) y que se agrega
 * un unico warning de programa, no uno por elemento.
 */
class CopyLocationTest {

    @Test
    void copyExpandedElementsAreMarkedPreprocessedStreamNotAttributedToMainFile() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("copy-main.cbl"), RequestedFormat.FIXED, List.of(Fixtures.copybookDir()),
                StandardCharsets.UTF_8);
        CanonicalProgram program = new CanonicalProgramExtractor().extract(
                Fixtures.path("copy-main.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/copy-main.cbl",
                "b".repeat(64),
                "a".repeat(64));

        for (CanonicalDataItem item : program.dataItems()) {
            assertEquals(LocationKind.PREPROCESSED_STREAM, item.locationKind());
            assertNull(item.sourceFile(), "no debe atribuirse al programa principal: " + item.name());
        }
        for (CanonicalParagraph paragraph : program.paragraphs()) {
            assertEquals(LocationKind.PREPROCESSED_STREAM, paragraph.locationKind());
            assertNull(paragraph.sourceFile());
        }

        long copyWarnings = program.warnings().stream().filter(w -> w.contains("COPY")).count();
        assertEquals(1, copyWarnings, "un unico warning de programa, no uno por elemento");
    }

    @Test
    void programWithoutCopyStaysExact() throws Exception {
        // comprehensive.cbl no usa COPY; FIXED (no FREE, no soportado) para
        // no acoplar este test de trazabilidad a la semantica de formato.
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("comprehensive.cbl"), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8);
        CanonicalProgram program = new CanonicalProgramExtractor().extract(
                Fixtures.path("comprehensive.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/comprehensive.cbl",
                "b".repeat(64),
                "a".repeat(64));

        assertTrue(program.warnings().stream().noneMatch(w -> w.contains("COPY")));
        for (CanonicalParagraph paragraph : program.paragraphs()) {
            assertEquals(LocationKind.EXACT, paragraph.locationKind());
        }
    }
}
