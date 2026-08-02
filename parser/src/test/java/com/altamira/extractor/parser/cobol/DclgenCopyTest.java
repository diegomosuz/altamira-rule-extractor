package com.altamira.extractor.parser.cobol;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.model.CanonicalDataItem;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.LocationKind;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.Test;

/**
 * Matriz de complejidad (Prompt 15), caso DCLGEN: un DCLGEN de DB2 es, a
 * ojos de este parser, simplemente un copybook con extension {@code
 * .dcl}/{@code .DCL} -- ya admitida en {@code COPYBOOK_EXTENSIONS} (ver
 * {@link ProLeapCobolParser}) -- que el mecanismo COPY existente resuelve
 * sin ningun tratamiento especial. Este test demuestra exactamente eso,
 * nada mas: no hay conexion a DB2, no hay generacion automatica del
 * DCLGEN, no hay validacion contra catalogo, no hay expansion de tipos
 * mas alla de lo que el propio contenido COBOL incluido declara.
 */
class DclgenCopyTest {

    private static CanonicalProgram parseOnce() throws Exception {
        var parseResult = new ProLeapCobolParser().parse(
                Fixtures.path("dclgen-main.cbl"), RequestedFormat.FIXED, List.of(Fixtures.copybookDir()),
                StandardCharsets.UTF_8);
        return new CanonicalProgramExtractor().extract(
                Fixtures.path("dclgen-main.cbl"),
                StandardCharsets.UTF_8,
                parseResult,
                "01-codigo/cobol/dclgen-main.cbl",
                "b".repeat(64),
                "a".repeat(64));
    }

    @Test
    void dclgenCopybookResolvesAndItsFieldBecomesACanonicalDataItem() throws Exception {
        CanonicalProgram program = parseOnce();

        Optional<CanonicalDataItem> nombre = program.dataItems().stream()
                .filter(item -> item.name().equals("DCLCLI-NOMBRE"))
                .findFirst();
        assertTrue(nombre.isPresent(), "el campo del DCLGEN debe quedar incluido como DataItem canonico");
        assertEquals("DCLCLI-ROW.DCLCLI-NOMBRE", nombre.get().qualifiedName());
        assertEquals("X(30)", nombre.get().pic());

        Optional<CanonicalDataItem> id = program.dataItems().stream()
                .filter(item -> item.name().equals("DCLCLI-ID"))
                .findFirst();
        assertTrue(id.isPresent(), "los demas campos del DCLGEN tambien deben incluirse");
    }

    @Test
    void dclgenElementsNeverExposeAnAbsolutePath() throws Exception {
        // Mismo mecanismo/limitacion que cualquier COPY (ver
        // CopyLocationTest): ProLeap solo expone el stream ya expandido,
        // asi que no se puede -- ni se intenta -- atribuir estos
        // elementos a un archivo fisico. sourceFile queda null, nunca
        // con la ruta absoluta real del copybook resuelto en disco.
        CanonicalProgram program = parseOnce();

        for (CanonicalDataItem item : program.dataItems()) {
            assertEquals(LocationKind.PREPROCESSED_STREAM, item.locationKind());
            assertNull(item.sourceFile(), "no debe exponer ninguna ruta, absoluta o relativa: " + item.name());
        }
    }

    @Test
    void dclgenResolutionIsDeterministicAcrossRuns() throws Exception {
        CanonicalProgram first = parseOnce();
        CanonicalProgram second = parseOnce();

        assertEquals(first.dataItems(), second.dataItems());
        assertEquals(first.paragraphs(), second.paragraphs());
        assertEquals(first.warnings(), second.warnings());
        assertEquals(first.unsupportedConstructs(), second.unsupportedConstructs());
    }

    @Test
    void dclgenGetsNoSpecialTreatmentBeyondOrdinaryCopy() throws Exception {
        // Un unico warning de programa (identico al de cualquier COPY
        // comun, ver CopyLocationTest.copyExpandedElementsAreMarked...):
        // nada especifico de DCLGEN se agrega a warnings ni a
        // unsupportedConstructs.
        CanonicalProgram program = parseOnce();

        long copyWarnings = program.warnings().stream().filter(w -> w.contains("COPY")).count();
        assertEquals(1, copyWarnings, "un unico warning de programa, igual que cualquier COPY comun");
        assertFalse(
                program.warnings().stream().anyMatch(w -> w.toUpperCase(java.util.Locale.ROOT).contains("DCLGEN")),
                "no debe existir tratamiento especial ni mensaje propio para DCLGEN");

        // El fixture termina en GOBACK, igual que practicamente todos los
        // demas (ver comprehensive.cbl/CanonicalProgramExtractorTest).
        // Desde la Fase 7b, GOBACK se clasifica estructuralmente como
        // kind=PROGRAM_TERMINATION (StatementExtractor#convertGoback) y
        // YA NO se reporta como no soportado -- esto es identico para
        // CUALQUIER programa, nada especifico de DCLGEN. Lo que este
        // test confirma es que no aparece NINGUNA entrada inesperada.
        assertEquals(0, program.unsupportedConstructs().size());
    }

    @Test
    void dclgenFieldIsUsableAfterCopy() throws Exception {
        // "al menos un campo COBOL utilizable despues del COPY": el MOVE
        // del fixture lee DCLCLI-NOMBRE realmente -- se ve reflejado en
        // variablesRead del parrafo, no solo en dataItems.
        CanonicalProgram program = parseOnce();

        var mainPara = program.paragraphs().stream()
                .filter(p -> p.name().equals("MAIN-PARA"))
                .findFirst()
                .orElseThrow();
        assertTrue(mainPara.variablesRead().contains("DCLCLI-NOMBRE"));
    }
}
