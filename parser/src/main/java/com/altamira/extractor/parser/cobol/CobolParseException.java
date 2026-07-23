package com.altamira.extractor.parser.cobol;

/** Error de preprocesamiento o parseo COBOL (CLI exit code 3). */
public final class CobolParseException extends Exception {

    public CobolParseException(String message) {
        super(message);
    }

    public CobolParseException(String message, Throwable cause) {
        super(message, cause);
    }
}
