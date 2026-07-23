package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.SourceFormat;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class ProLeapCobolParserTest {

    private final ProLeapCobolParser parser = new ProLeapCobolParser();

    @Test
    void parsesFixedFormatExplicitly() throws Exception {
        var result = parser.parse(
                Fixtures.path("comprehensive.cbl"), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8);
        assertEquals(SourceFormat.FIXED, result.resolvedFormat());
        assertEquals("COMPREH1", result.programUnit().getIdentificationDivision()
                .getProgramIdParagraph().getName());
    }

    @Test
    void explicitFreeFormatIsRejectedNotSupported() {
        // FREE se acepta sintacticamente como valor de --format (no es un
        // error de argumentos: ver ArgumentParserTest), pero ProLeap 2.4.0
        // no tiene un formato libre real que honrarlo, asi que el parseo
        // debe fallar explicitamente en vez de reinterpretar el archivo
        // como VARIABLE.
        CobolParseException exception = assertThrows(CobolParseException.class, () -> parser.parse(
                Fixtures.path("comprehensive.cbl"), RequestedFormat.FREE, List.of(), StandardCharsets.UTF_8));
        assertEquals(
                "FREE source format is not supported by the configured ProLeap version.",
                exception.getMessage());
    }

    @Test
    void autoDetectsFixedFormatWithoutSequenceNumbers() throws Exception {
        var result = parser.parse(
                Fixtures.path("comprehensive.cbl"), RequestedFormat.AUTO, List.of(), StandardCharsets.UTF_8);
        assertEquals(SourceFormat.FIXED, result.resolvedFormat());
        assertTrue(result.formatDetectionWarnings().stream().anyMatch(w -> w.contains("AUTO")));
    }

    @Test
    void autoDetectsFixedFormatWithSequenceNumbers() throws Exception {
        var result = parser.parse(
                Fixtures.path("auto-fixed-with-sequence.cbl"), RequestedFormat.AUTO, List.of(),
                StandardCharsets.UTF_8);
        assertEquals(SourceFormat.FIXED, result.resolvedFormat());
        assertEquals("SEQNUM1", result.programUnit().getIdentificationDivision()
                .getProgramIdParagraph().getName());
    }

    @Test
    void autoIgnoresFixedFormatStarComments() throws Exception {
        var result = parser.parse(
                Fixtures.path("auto-fixed-comment-star.cbl"), RequestedFormat.AUTO, List.of(),
                StandardCharsets.UTF_8);
        assertEquals(SourceFormat.FIXED, result.resolvedFormat());
    }

    @Test
    void autoIgnoresFreeFormatArrowComments() throws Exception {
        // Se prueba resolveAutoFormat en aislamiento (no parser.parse
        // completo): el heuristico de deteccion debe ignorar la linea "*>"
        // y no rechazarla, independientemente de si ProLeap tolera esa
        // convencion de comentario al parsear en modo FIXED (no la
        // tolera: es una cuestion de preprocesamiento distinta de la
        // deteccion de formato, ya cubierta por otros tests).
        List<String> lines = Files.readAllLines(
                Fixtures.path("auto-fixed-comment-arrow.cbl"), StandardCharsets.UTF_8);
        SourceFormat format = ProLeapCobolParser.resolveAutoFormat(lines, new ArrayList<>());
        assertEquals(SourceFormat.FIXED, format);
    }

    @Test
    void autoAcceptsFixedDespiteLongCommentLine() throws Exception {
        // La longitud de linea nunca es, por si sola, motivo de rechazo:
        // una linea de comentario larga se ignora igual que una corta.
        var result = parser.parse(
                Fixtures.path("auto-fixed-long-comment.cbl"), RequestedFormat.AUTO, List.of(),
                StandardCharsets.UTF_8);
        assertEquals(SourceFormat.FIXED, result.resolvedFormat());
    }

    @Test
    void autoRejectsShortFreeFormatFileDespiteNoLongLines() {
        // Todas las lineas de fixtures/auto-free-short.cbl tienen menos de
        // 80 columnas: el criterio antiguo basado solo en longitud las
        // habria aceptado incorrectamente como FIXED. El codigo empieza en
        // columna 1 en cada linea, incompatible con el layout fixed-format.
        CobolParseException exception = assertThrows(CobolParseException.class, () -> parser.parse(
                Fixtures.path("auto-free-short.cbl"), RequestedFormat.AUTO, List.of(), StandardCharsets.UTF_8));
        assertTrue(exception.getMessage().contains("AUTO"));
        assertTrue(exception.getMessage().contains("--format"));
    }

    @Test
    void autoRejectsCodeStartingAtColumnOne() {
        CobolParseException exception = assertThrows(CobolParseException.class, () -> parser.parse(
                Fixtures.path("auto-column1-code.cbl"), RequestedFormat.AUTO, List.of(), StandardCharsets.UTF_8));
        assertTrue(exception.getMessage().contains("linea 7"));
    }

    @Test
    void autoRejectsTabsInSequenceOrIndicatorArea() {
        CobolParseException exception = assertThrows(CobolParseException.class, () -> parser.parse(
                Fixtures.path("auto-tabs.cbl"), RequestedFormat.AUTO, List.of(), StandardCharsets.UTF_8));
        assertTrue(exception.getMessage().contains("linea 5"));
    }

    @Test
    void autoRejectsEmptyFile() {
        CobolParseException exception = assertThrows(CobolParseException.class, () -> parser.parse(
                Fixtures.path("auto-empty.cbl"), RequestedFormat.AUTO, List.of(), StandardCharsets.UTF_8));
        assertTrue(exception.getMessage().contains("AUTO"));
    }

    @Test
    void autoRejectsCommentsOnlyFile() {
        CobolParseException exception = assertThrows(CobolParseException.class, () -> parser.parse(
                Fixtures.path("auto-comments-only.cbl"), RequestedFormat.AUTO, List.of(), StandardCharsets.UTF_8));
        assertTrue(exception.getMessage().contains("AUTO"));
    }

    @Test
    void autoDetectsFixedWithUtf8Bom() throws Exception {
        // Files.readAllLines no descarta el BOM: sin el fix acotado en
        // resolveAutoFormat, este archivo (FIXED estructuralmente valido)
        // seria rechazado solo porque la linea 1 arranca con U+FEFF.
        var result = parser.parse(
                Fixtures.path("auto-fixed-utf8-bom.cbl"), RequestedFormat.AUTO, List.of(),
                StandardCharsets.UTF_8);
        assertEquals(SourceFormat.FIXED, result.resolvedFormat());
        assertEquals("BOMUTF1", result.programUnit().getIdentificationDivision()
                .getProgramIdParagraph().getName());
    }

    @Test
    void autoDetectsFixedWithoutUtf8BomEquivalently() throws Exception {
        // Mismo contenido que auto-fixed-utf8-bom.cbl, sin el BOM: debe
        // resolver exactamente igual (FIXED, mismo PROGRAM-ID).
        var result = parser.parse(
                Fixtures.path("auto-fixed-utf8-no-bom.cbl"), RequestedFormat.AUTO, List.of(),
                StandardCharsets.UTF_8);
        assertEquals(SourceFormat.FIXED, result.resolvedFormat());
        assertEquals("BOMUTF1", result.programUnit().getIdentificationDivision()
                .getProgramIdParagraph().getName());
    }

    @Test
    void autoStillRejectsIncompatibleContentAfterBom() {
        // El BOM solo se ignora al evaluar la linea 1; una linea posterior
        // que viola el layout fixed-format (codigo en columna 1) sigue
        // rechazando AUTO igual que sin BOM.
        CobolParseException exception = assertThrows(CobolParseException.class, () -> parser.parse(
                Fixtures.path("auto-bom-incompatible.cbl"), RequestedFormat.AUTO, List.of(),
                StandardCharsets.UTF_8));
        assertTrue(exception.getMessage().contains("AUTO"));
    }

    @Test
    void autoRejectsBomOnlyCommentsFile() {
        // BOM seguido unicamente de comentarios: sigue sin haber ninguna
        // linea de codigo significativa, AUTO debe fallar igual que
        // auto-comments-only.cbl (sin BOM).
        CobolParseException exception = assertThrows(CobolParseException.class, () -> parser.parse(
                Fixtures.path("auto-bom-comments-only.cbl"), RequestedFormat.AUTO, List.of(),
                StandardCharsets.UTF_8));
        assertTrue(exception.getMessage().contains("AUTO"));
    }

    @Test
    void resolvesCopyFromCopybookDir() throws Exception {
        var result = parser.parse(
                Fixtures.path("copy-main.cbl"), RequestedFormat.FIXED, List.of(Fixtures.copybookDir()),
                StandardCharsets.UTF_8);
        assertEquals("COPYMAIN1", result.programUnit().getIdentificationDivision()
                .getProgramIdParagraph().getName());
    }

    @Test
    void resolvesNestedCopy() throws Exception {
        var result = parser.parse(
                Fixtures.path("copy-nested-main.cbl"), RequestedFormat.FIXED,
                List.of(Fixtures.copybookDir()), StandardCharsets.UTF_8);
        assertEquals("COPYNEST1", result.programUnit().getIdentificationDivision()
                .getProgramIdParagraph().getName());
    }

    @Test
    void resolvesCopyReplacing() throws Exception {
        var result = parser.parse(
                Fixtures.path("copy-replacing-main.cbl"), RequestedFormat.FIXED,
                List.of(Fixtures.copybookDir()), StandardCharsets.UTF_8);
        assertEquals("COPYREPL1", result.programUnit().getIdentificationDivision()
                .getProgramIdParagraph().getName());
    }

    @Test
    void throwsCobolParseExceptionOnInvalidSyntax() {
        CobolParseException exception = assertThrows(CobolParseException.class, () -> parser.parse(
                Fixtures.path("invalid-syntax.cbl"), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8));
        assertTrue(exception.getMessage().toLowerCase().contains("syntax")
                || exception.getMessage().toLowerCase().contains("mismatched"));
    }

    @Test
    void throwsCobolParseExceptionOnMissingCopybook() {
        CobolParseException exception = assertThrows(CobolParseException.class, () -> parser.parse(
                Fixtures.path("missing-copybook.cbl"), RequestedFormat.FIXED,
                List.of(Fixtures.copybookDir()), StandardCharsets.UTF_8));
        assertTrue(exception.getMessage().contains("DOES-NOT-EXIST"));
    }

    @Test
    void throwsIoExceptionOnMissingInput() {
        Path missing = Fixtures.path("does-not-exist.cbl");
        assertThrows(IOException.class, () -> parser.parse(
                missing, RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8));
    }
}
