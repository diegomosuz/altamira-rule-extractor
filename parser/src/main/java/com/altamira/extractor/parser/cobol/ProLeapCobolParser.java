package com.altamira.extractor.parser.cobol;

import com.altamira.extractor.parser.model.SourceFormat;
import io.proleap.cobol.asg.metamodel.CompilationUnit;
import io.proleap.cobol.asg.metamodel.Program;
import io.proleap.cobol.asg.metamodel.ProgramUnit;
import io.proleap.cobol.asg.metamodel.identification.ProgramIdParagraph;
import io.proleap.cobol.asg.params.CobolDialect;
import io.proleap.cobol.asg.params.CobolParserParams;
import io.proleap.cobol.asg.params.impl.CobolParserParamsImpl;
import io.proleap.cobol.asg.runner.impl.CobolParserRunnerImpl;
import io.proleap.cobol.preprocessor.CobolPreprocessor;
import java.io.IOException;
import java.nio.charset.Charset;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Unico punto de entrada a la API de ProLeap junto con
 * {@link CanonicalProgramExtractor}/{@link StatementExtractor}: son las
 * unicas clases del modulo que importan tipos {@code io.proleap.*}. Nadie
 * fuera del paquete {@code cobol} ve esos tipos.
 *
 * <p>Usa su preprocesador (COPY/REPLACE/COPY REPLACING) via
 * {@link CobolParserParams#setCopyBookDirectories}.
 *
 * <p><b>FREE no esta soportado.</b> Se verifico contra el codigo fuente
 * real de {@code CobolPreprocessor.CobolSourceFormatEnum} que "VARIABLE"
 * (el unico candidato en ProLeap 2.4.0 a representar "formato libre") NO
 * es COBOL free-format moderno (ISO/IEC 2002): exige la misma disposicion
 * de columnas que FIXED (1-6 secuencia, 7 indicador, 8-72 codigo); su
 * unica diferencia real es que no trunca a 80 columnas. Presentar
 * VARIABLE como soporte de FREE seria una afirmacion falsa. Por eso:
 * <ul>
 *   <li>{@code --format FIXED}: soportado.</li>
 *   <li>{@code --format TANDEM}: soportado solo si se pide explicitamente.</li>
 *   <li>{@code --format FREE}: se acepta sintacticamente como valor de
 *       CLI, pero {@link #parse} termina con {@link CobolParseException}
 *       (exit code 3) y el mensaje exacto "FREE source format is not
 *       supported by the configured ProLeap version."</li>
 *   <li>{@code --format AUTO}: solo puede resolver FIXED, y solo cuando
 *       toda linea de codigo significativa es estructuralmente compatible
 *       con el layout fixed-format (columnas 1-6 secuencia, columna 7
 *       indicador valido, codigo desde columna 8, sin tabs) — ver
 *       {@link #resolveAutoFormat}. La longitud de linea (&gt;80
 *       columnas) es una senal adicional, nunca el criterio unico. Nunca
 *       resuelve FREE ni TANDEM. Cuando alguna linea significativa no
 *       cumple el layout, o no existe ninguna linea de codigo valida,
 *       falla con {@link CobolParseException} pidiendo un formato
 *       explicito — nunca reinterpreta silenciosamente el archivo como
 *       VARIABLE.</li>
 * </ul>
 */
public final class ProLeapCobolParser {

    /** Extensiones de copybook admitidas al resolver COPY. */
    private static final List<String> COPYBOOK_EXTENSIONS = List.of(
            "cpy", "CPY", "cbl", "CBL", "cob", "COB", "copy", "COPY", "dcl", "DCL");

    public record Result(
            ProgramUnit programUnit,
            CompilationUnit compilationUnit,
            SourceFormat resolvedFormat,
            List<String> formatDetectionWarnings) {
    }

    public Result parse(
            Path inputFile, RequestedFormat requestedFormat, List<Path> copybookDirs, Charset encoding)
            throws IOException, CobolParseException {
        List<String> rawLines = Files.readAllLines(inputFile, encoding);
        List<String> warnings = new ArrayList<>();
        SourceFormat resolvedFormat = resolveFormat(requestedFormat, rawLines, warnings);
        requireCopybooksResolvable(rawLines, resolvedFormat, copybookDirs, inputFile);

        CobolParserParams params = new CobolParserParamsImpl();
        params.setCharset(encoding);
        params.setDialect(CobolDialect.ANSI85);
        params.setCopyBookDirectories(copybookDirs.stream().map(Path::toFile).toList());
        params.setCopyBookExtensions(COPYBOOK_EXTENSIONS);

        Program program;
        try {
            program = new CobolParserRunnerImpl()
                    .analyzeFile(inputFile.toFile(), toProLeapFormat(resolvedFormat), params);
        } catch (IOException e) {
            throw e;
        } catch (RuntimeException e) {
            throw new CobolParseException(e.getMessage(), e);
        }

        CompilationUnit compilationUnit = firstCompilationUnit(program, inputFile);
        ProgramUnit programUnit = firstProgramUnit(compilationUnit, inputFile, warnings);
        requireProgramId(programUnit, inputFile);

        return new Result(programUnit, compilationUnit, resolvedFormat, warnings);
    }

    private static CompilationUnit firstCompilationUnit(Program program, Path inputFile)
            throws CobolParseException {
        if (program == null || program.getCompilationUnits().isEmpty()) {
            throw new CobolParseException(
                    "ProLeap no produjo ninguna CompilationUnit para " + inputFile);
        }
        return program.getCompilationUnits().get(0);
    }

    private static ProgramUnit firstProgramUnit(
            CompilationUnit compilationUnit, Path inputFile, List<String> warnings)
            throws CobolParseException {
        List<ProgramUnit> units = compilationUnit.getProgramUnits();
        if (units.isEmpty()) {
            throw new CobolParseException("ProLeap no produjo ningun ProgramUnit para " + inputFile);
        }
        if (units.size() > 1) {
            warnings.add(
                    "el archivo contiene multiples program units; solo se extrae el primero ("
                            + units.size() + " encontrados)");
        }
        return units.get(0);
    }

    private static void requireProgramId(ProgramUnit programUnit, Path inputFile)
            throws CobolParseException {
        ProgramIdParagraph programId = programUnit.getIdentificationDivision() != null
                ? programUnit.getIdentificationDivision().getProgramIdParagraph()
                : null;
        if (programId == null || programId.getName() == null || programId.getName().isBlank()) {
            throw new CobolParseException(
                    "no se pudo determinar PROGRAM-ID en " + inputFile
                            + "; el COBOL puede ser sintacticamente invalido");
        }
    }

    /**
     * ProLeap, por defecto, no lanza excepcion ante un COPY cuyo copybook no
     * se encuentra: solo lo registra por stderr internamente y continua con
     * la sentencia sin resolver (verificado empiricamente). Para que
     * "copybook faltante" sea un error visible (exit code 3) en vez de un
     * exito silencioso con datos incompletos, se verifica proactivamente
     * antes de invocar al runner.
     */
    private static void requireCopybooksResolvable(
            List<String> rawLines, SourceFormat format, List<Path> copybookDirs, Path inputFile)
            throws CobolParseException {
        List<Path> searchDirs = new ArrayList<>(copybookDirs);
        Path inputDir = inputFile.toAbsolutePath().getParent();
        if (inputDir != null) {
            searchDirs.add(inputDir);
        }
        for (String name : CopyDetector.extractCopyBookNames(rawLines, format)) {
            if (!copybookExists(name, searchDirs)) {
                throw new CobolParseException(
                        "copybook no encontrado: " + name + " (buscado en " + searchDirs + ")");
            }
        }
    }

    private static boolean copybookExists(String name, List<Path> searchDirs) {
        for (Path dir : searchDirs) {
            for (String extension : COPYBOOK_EXTENSIONS) {
                if (Files.isRegularFile(dir.resolve(name + "." + extension))) {
                    return true;
                }
            }
        }
        return false;
    }

    private static final String FREE_NOT_SUPPORTED_MESSAGE =
            "FREE source format is not supported by the configured ProLeap version.";

    private SourceFormat resolveFormat(
            RequestedFormat requested, List<String> rawLines, List<String> warnings)
            throws CobolParseException {
        switch (requested) {
            case FIXED:
                return SourceFormat.FIXED;
            case TANDEM:
                return SourceFormat.TANDEM;
            case FREE:
                // Se acepta sintacticamente como valor de --format (no es un
                // error de argumentos), pero no hay forma de honrarlo: ProLeap
                // 2.4.0 no tiene un formato libre real. Falla explicitamente
                // en vez de reinterpretar el archivo como VARIABLE.
                throw new CobolParseException(FREE_NOT_SUPPORTED_MESSAGE);
            case AUTO:
            default:
                return resolveAutoFormat(rawLines, warnings);
        }
    }

    private static final int FIXED_SEQUENCE_AREA_LENGTH = 6;
    private static final int FIXED_INDICATOR_COLUMN = 6; // columna 7, 0-based

    /**
     * AUTO solo puede resolver FIXED, y solo cuando existe evidencia
     * estructural real, no solo ausencia de lineas largas. IMPORTANTE: en
     * ProLeap, "VARIABLE" (el unico candidato a representar "formato
     * libre") NO es COBOL free-format moderno (ISO/IEC 2002): exige la
     * misma disposicion de columnas que FIXED segun su propio codigo
     * fuente (CobolPreprocessor.CobolSourceFormatEnum). Como no existe un
     * formato libre real al que recurrir, cuando una linea significativa
     * no es compatible con el layout fixed-format no hay una alternativa
     * razonable: se falla pidiendo --format explicito, en vez de adivinar
     * TANDEM o reinterpretar el archivo silenciosamente.
     *
     * <p>Se ignoran (no se evaluan ni cuentan como evidencia) lineas
     * vacias/solo-espacios y comentarios (columna 7 con '*'/'/' en estilo
     * fixed, o lineas que empiezan con "*&gt;" en estilo free). Toda otra
     * linea es "significativa" y debe cumplir, sin excepcion:
     * <ul>
     *   <li>longitud minima de 7 columnas;</li>
     *   <li>columnas 1-6 vacias o compuestas solo por digitos y espacios
     *       (area de secuencia);</li>
     *   <li>columna 7 con indicador valido: espacio, '-', 'D' o 'd';</li>
     *   <li>contenido no vacio a partir de la columna 8;</li>
     *   <li>ausencia de tabs en las columnas 1-7.</li>
     * </ul>
     * Debe existir al menos una linea significativa (si no hay ninguna —
     * archivo vacio o solo comentarios — tampoco hay evidencia y AUTO
     * falla). La longitud de linea (&gt;80 columnas) ya no es, por si
     * sola, motivo de rechazo ni de aceptacion: una linea de comentario
     * larga se ignora igual que una corta; una linea de codigo que ya
     * viola el layout se rechaza aunque sea corta.
     *
     * <p><b>BOM UTF-8:</b> {@code Files.readAllLines} no descarta un BOM
     * inicial (el caracter U+FEFF queda como parte del contenido de la
     * primera linea), lo que desplaza en uno sus columnas y hacia rechazar
     * como incompatible un archivo FIXED estructuralmente valido
     * (verificado empiricamente: ProLeap si tolera el BOM en su propio
     * preprocesador bajo {@code --format FIXED}/{@code TANDEM} explicito,
     * que no pasa por este metodo). Por eso, unicamente al evaluar la
     * primera linea para esta deteccion estructural, se ignora un BOM
     * inicial si esta presente; no se modifica {@code rawLines} en si
     * (otros usos, como la deteccion de COPY, siguen viendo la linea
     * original), ni los bytes que ProLeap recibe del archivo real.
     */
    // Paquete-visible (no private) unicamente para que ProLeapCobolParserTest
    // pueda verificar la decision del heuristico en aislamiento, sin depender
    // de si ProLeap logra ademas parsear el archivo completo (son cuestiones
    // distintas: esta funcion solo decide que --format pasarle a ProLeap).
    static SourceFormat resolveAutoFormat(List<String> rawLines, List<String> warnings)
            throws CobolParseException {
        boolean hasValidCodeLine = false;
        int lineNumber = 0;
        for (String rawLine : rawLines) {
            lineNumber++;
            String line = lineNumber == 1 ? stripLeadingUtf8BomForAutoDetection(rawLine) : rawLine;
            if (isIgnorableForAutoDetection(line)) {
                continue;
            }
            if (!isFixedFormatCompatible(line)) {
                throw new CobolParseException(
                        "--format AUTO no pudo confirmar FIXED: la linea " + lineNumber
                                + " no es compatible estructuralmente con el layout fixed-format "
                                + "(columnas 1-6 area de secuencia con digitos/espacios, columna 7 "
                                + "con indicador valido [espacio, '-', 'D'], codigo significativo "
                                + "desde la columna 8, sin tabs en columnas 1-7); especifique "
                                + "--format explicitamente (FIXED o TANDEM; FREE no esta soportado "
                                + "por la version de ProLeap configurada).");
            }
            hasValidCodeLine = true;
        }
        if (!hasValidCodeLine) {
            throw new CobolParseException(
                    "--format AUTO no encontro ninguna linea de codigo significativa para "
                            + "confirmar FIXED (archivo vacio o compuesto solo por comentarios); "
                            + "especifique --format explicitamente (FIXED o TANDEM; FREE no esta "
                            + "soportado por la version de ProLeap configurada).");
        }
        warnings.add(
                "--format AUTO detectado como FIXED: toda linea de codigo significativa cumple el "
                        + "layout fixed-format (columnas 1-6 secuencia, columna 7 indicador, codigo "
                        + "desde columna 8, sin tabs). Nunca selecciona FREE ni TANDEM. Especifique "
                        + "--format explicitamente si el resultado es incorrecto.");
        return SourceFormat.FIXED;
    }

    private static final char UTF8_BOM_CHAR = 0xFEFF;

    /**
     * Descarta un BOM UTF-8 inicial (U+FEFF) solo para la evaluacion
     * estructural de la primera linea dentro de {@link #resolveAutoFormat}.
     * No muta la lista {@code rawLines} original ni afecta ningun otro
     * consumidor de esas lineas.
     */
    private static String stripLeadingUtf8BomForAutoDetection(String line) {
        return !line.isEmpty() && line.charAt(0) == UTF8_BOM_CHAR ? line.substring(1) : line;
    }

    /** Lineas vacias, solo-espacios, o comentarios (fixed u free) no aportan ni quitan evidencia. */
    private static boolean isIgnorableForAutoDetection(String line) {
        if (line.isBlank()) {
            return true;
        }
        if (line.stripLeading().startsWith("*>")) {
            return true;
        }
        if (line.length() > FIXED_INDICATOR_COLUMN) {
            char indicator = line.charAt(FIXED_INDICATOR_COLUMN);
            if (indicator == '*' || indicator == '/') {
                return true;
            }
        }
        return false;
    }

    private static boolean isFixedFormatCompatible(String line) {
        if (line.length() <= FIXED_INDICATOR_COLUMN) {
            return false;
        }
        if (line.substring(0, FIXED_INDICATOR_COLUMN + 1).indexOf('\t') >= 0) {
            return false;
        }
        for (int column = 0; column < FIXED_SEQUENCE_AREA_LENGTH; column++) {
            char c = line.charAt(column);
            if (!(Character.isDigit(c) || c == ' ')) {
                return false;
            }
        }
        char indicator = line.charAt(FIXED_INDICATOR_COLUMN);
        if (indicator != ' ' && indicator != '-' && indicator != 'D' && indicator != 'd') {
            return false;
        }
        String contentArea = line.substring(FIXED_INDICATOR_COLUMN + 1);
        return !contentArea.isBlank();
    }

    private static CobolPreprocessor.CobolSourceFormatEnum toProLeapFormat(SourceFormat format) {
        return switch (format) {
            case FIXED -> CobolPreprocessor.CobolSourceFormatEnum.FIXED;
            case TANDEM -> CobolPreprocessor.CobolSourceFormatEnum.TANDEM;
        };
    }
}
