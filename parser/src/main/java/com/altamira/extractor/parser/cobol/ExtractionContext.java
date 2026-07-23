package com.altamira.extractor.parser.cobol;

import com.altamira.extractor.parser.model.LocationKind;
import java.util.ArrayList;
import java.util.List;

/**
 * Estado compartido durante la extraccion de un CanonicalProgram: nombre
 * del programa (para statement_id), si el programa completo se trata como
 * EXACT o PREPROCESSED_STREAM (decidido una sola vez por
 * {@link CopyDetector}, ver CanonicalProgramExtractor), las lineas del
 * stream preprocesado (para slicing de source_text), y listas mutables de
 * warnings/unsupported_constructs acumuladas durante todo el recorrido.
 */
final class ExtractionContext {

    final String programName;
    final LocationKind programLocationKind;
    final String sourceFileForExact;
    final List<String> preprocessedLines;
    final List<String> warnings = new ArrayList<>();
    final List<String> unsupportedConstructs = new ArrayList<>();

    private int ordinal;

    ExtractionContext(
            String programName,
            LocationKind programLocationKind,
            String sourceFileForExact,
            List<String> preprocessedLines) {
        this.programName = programName;
        this.programLocationKind = programLocationKind;
        this.sourceFileForExact = sourceFileForExact;
        this.preprocessedLines = preprocessedLines;
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
