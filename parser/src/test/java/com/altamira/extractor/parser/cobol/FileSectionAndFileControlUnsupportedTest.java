package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.CanonicalDataItem;
import com.altamira.extractor.parser.model.CanonicalProgram;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;

/**
 * Fase 15B4-CANDIDATE-QUALITY-5B (cierre de P1-FD-FILE-CONTROL-SILENT):
 * FILE SECTION/FD y FILE-CONTROL/SELECT deben quedar trazados
 * explicitamente en {@code unsupported_constructs} -- nunca productivizados
 * (ningun {@code CanonicalDataItem} nuevo, ninguna entidad de grafo),
 * nunca silenciosos.
 */
class FileSectionAndFileControlUnsupportedTest {

    private static CanonicalProgram extract(String fixtureName, String programId) throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path(fixtureName), RequestedFormat.FIXED, List.of(), StandardCharsets.UTF_8);
        return new CanonicalProgramExtractor().extract(
                Fixtures.path(fixtureName),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/" + fixtureName,
                "b".repeat(64),
                "a".repeat(64));
    }

    @Test
    void fileSectionAndFileControlAreDetectedAndTracedNeverProductivized() throws Exception {
        CanonicalProgram program = extract("file-section-and-control.cbl", "FILETEST1");

        List<String> fileSectionDiagnostics = program.unsupportedConstructs().stream()
                .filter(w -> w.startsWith("UNSUPPORTED_FILE_SECTION"))
                .toList();
        List<String> fileControlDiagnostics = program.unsupportedConstructs().stream()
                .filter(w -> w.startsWith("UNSUPPORTED_FILE_CONTROL"))
                .toList();

        assertEquals(1, fileSectionDiagnostics.size(), program.unsupportedConstructs().toString());
        assertTrue(fileSectionDiagnostics.get(0).contains("CLIENT-FILE"));
        assertEquals(1, fileControlDiagnostics.size(), program.unsupportedConstructs().toString());
        assertTrue(fileControlDiagnostics.get(0).contains("CLIENT-FILE"));

        // Nunca se fabrica un CanonicalDataItem para los campos bajo FD, y
        // WORKING-STORAGE sigue extrayendose con normalidad (sin mezcla).
        Set<String> dataItemNames = program.dataItems().stream()
                .map(CanonicalDataItem::name)
                .collect(Collectors.toSet());
        assertFalse(dataItemNames.contains("CR-ID"));
        assertFalse(dataItemNames.contains("CR-NOMBRE"));
        assertFalse(dataItemNames.contains("CLIENT-RECORD"));
        assertTrue(dataItemNames.contains("WS-SALDO"));
        assertTrue(dataItemNames.contains("WS-COD-RETORNO"));

        // Caso E: OPEN/READ/CLOSE en Procedure Division siguen trazados por
        // el mecanismo preexistente (kind=OTHER, ver StatementExtractor
        // convertOther) -- este fix no lo modifica ni lo reemplaza.
        long otherStatementDiagnostics = program.unsupportedConstructs().stream()
                .filter(w -> w.contains("kind=OTHER") && w.contains("MAIN-PARA"))
                .count();
        assertTrue(otherStatementDiagnostics >= 3, program.unsupportedConstructs().toString());
    }

    @Test
    void programWithoutFileSectionOrFileControlHasNoNewDiagnostics() throws Exception {
        // comprehensive.cbl no declara ENVIRONMENT DIVISION ni FILE SECTION.
        CanonicalProgram program = extract("comprehensive.cbl", "COMPREH1");

        assertTrue(program.unsupportedConstructs().stream()
                .noneMatch(w -> w.startsWith("UNSUPPORTED_FILE_SECTION")));
        assertTrue(program.unsupportedConstructs().stream()
                .noneMatch(w -> w.startsWith("UNSUPPORTED_FILE_CONTROL")));
    }

    @Test
    void fileSectionDiagnosticsNeverLeakAcrossPrograms() throws Exception {
        CanonicalProgram withFile = extract("file-section-and-control.cbl", "FILETEST1");
        CanonicalProgram without = extract("comprehensive.cbl", "COMPREH1");

        assertTrue(withFile.unsupportedConstructs().stream()
                .anyMatch(w -> w.startsWith("UNSUPPORTED_FILE_SECTION")));
        assertTrue(without.unsupportedConstructs().stream()
                .noneMatch(w -> w.startsWith("UNSUPPORTED_FILE_SECTION") || w.startsWith("UNSUPPORTED_FILE_CONTROL")));
    }
}
