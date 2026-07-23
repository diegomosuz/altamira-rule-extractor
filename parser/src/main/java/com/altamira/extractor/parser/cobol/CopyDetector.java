package com.altamira.extractor.parser.cobol;

import com.altamira.extractor.parser.model.SourceFormat;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Deteccion lexica simple de sentencias COPY en el fuente crudo (antes de
 * invocar a ProLeap), ignorando lineas de comentario. No es un
 * preprocesador: no resuelve nada, solo decide si el programa completo
 * debe tratarse como PREPROCESSED_STREAM (ver CanonicalProgramExtractor) y
 * cuales copybooks se referencian (ver
 * ProLeapCobolParser#requireCopybooksResolvable: ProLeap por defecto no
 * lanza error ante un copybook faltante, solo lo registra por stderr y
 * continua, asi que la deteccion proactiva vive aqui).
 *
 * <p>FIXED/TANDEM: columna 7 (indice 6) con '*' o '/' marca la linea
 * entera como comentario. FREE (VARIABLE en ProLeap): todo lo que sigue a
 * "*&gt;" en una linea se ignora al buscar COPY.
 */
final class CopyDetector {

    private static final Pattern COPY_WORD = Pattern.compile("\\bCOPY\\b", Pattern.CASE_INSENSITIVE);
    private static final Pattern COPY_NAME =
            Pattern.compile("\\bCOPY\\s+([A-Za-z0-9$#@_-]+)", Pattern.CASE_INSENSITIVE);
    private static final int FIXED_INDICATOR_COLUMN = 6; // columna 7, 0-based

    private CopyDetector() {
    }

    static boolean containsCopyStatement(List<String> rawLines, SourceFormat format) {
        for (String line : rawLines) {
            if (codePortion(line, format) != null && COPY_WORD.matcher(codePortion(line, format)).find()) {
                return true;
            }
        }
        return false;
    }

    static List<String> extractCopyBookNames(List<String> rawLines, SourceFormat format) {
        List<String> names = new ArrayList<>();
        for (String line : rawLines) {
            String code = codePortion(line, format);
            if (code == null) {
                continue;
            }
            Matcher matcher = COPY_NAME.matcher(code);
            while (matcher.find()) {
                names.add(matcher.group(1));
            }
        }
        return names;
    }

    private static String codePortion(String line, SourceFormat format) {
        return format == SourceFormat.FIXED || format == SourceFormat.TANDEM
                ? fixedCodePortion(line)
                : freeCodePortion(line);
    }

    private static String fixedCodePortion(String line) {
        if (line.length() <= FIXED_INDICATOR_COLUMN) {
            return line;
        }
        char indicator = line.charAt(FIXED_INDICATOR_COLUMN);
        if (indicator == '*' || indicator == '/') {
            return null; // linea de comentario completa
        }
        return line;
    }

    private static String freeCodePortion(String line) {
        int idx = line.indexOf("*>");
        return idx < 0 ? line : line.substring(0, idx);
    }
}
