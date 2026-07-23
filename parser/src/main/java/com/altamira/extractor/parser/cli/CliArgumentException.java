package com.altamira.extractor.parser.cli;

/** Argumentos de linea de comandos invalidos (exit code 2). */
public final class CliArgumentException extends Exception {

    public CliArgumentException(String message) {
        super(message);
    }
}
