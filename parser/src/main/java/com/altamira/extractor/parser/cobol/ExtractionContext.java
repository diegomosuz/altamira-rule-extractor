package com.altamira.extractor.parser.cobol;

import com.altamira.extractor.parser.model.LocationKind;
import java.util.ArrayList;
import java.util.List;
import org.antlr.v4.runtime.CommonTokenStream;

/**
 * Estado compartido durante la extraccion de un CanonicalProgram: nombre
 * del programa (para statement_id), si el programa completo se trata como
 * EXACT o PREPROCESSED_STREAM (decidido una sola vez por
 * {@link CopyDetector}, ver CanonicalProgramExtractor), las lineas del
 * stream preprocesado (para slicing de source_text), y listas mutables de
 * warnings/unsupported_constructs acumuladas durante todo el recorrido.
 *
 * <p>{@code tokens} (Fase 3 v1.18.3, checkpoint correctivo de limites de
 * token): el {@link CommonTokenStream} REAL ya producido por el propio
 * parseo (expuesto por ProLeap via {@code CompilationUnit#getTokens()},
 * nunca un re-parseo) -- unica fuente que {@link StatementExtractor}
 * necesita para renderizar expresiones respetando limites de token (ver
 * {@code StatementExtractor#renderTokenRange}), en vez de {@code
 * ParserRuleContext#getText()}, que concatena texto de tokens visibles
 * sin ningun separador (el espacio del COBOL fuente vive en el canal
 * oculto de ANTLR).
 */
final class ExtractionContext {

    final String programName;
    final LocationKind programLocationKind;
    final String sourceFileForExact;
    final List<String> preprocessedLines;
    final CommonTokenStream tokens;
    final List<String> warnings = new ArrayList<>();
    final List<String> unsupportedConstructs = new ArrayList<>();

    private int ordinal;

    ExtractionContext(
            String programName,
            LocationKind programLocationKind,
            String sourceFileForExact,
            List<String> preprocessedLines,
            CommonTokenStream tokens) {
        this.programName = programName;
        this.programLocationKind = programLocationKind;
        this.sourceFileForExact = sourceFileForExact;
        this.preprocessedLines = preprocessedLines;
        this.tokens = tokens;
    }

    int nextOrdinal() {
        return ordinal++;
    }

    String sliceSourceText(int lineStart, int lineEnd) {
        int from = Math.max(0, lineStart - 1);
        int to = Math.min(preprocessedLines.size(), lineEnd);
        if (from >= to) {
            return "";
        }
        return String.join("\n", preprocessedLines.subList(from, to));
    }

    void unsupported(String note) {
        unsupportedConstructs.add(note);
    }
}
