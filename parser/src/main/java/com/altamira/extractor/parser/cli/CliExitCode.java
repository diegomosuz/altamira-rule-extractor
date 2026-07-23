package com.altamira.extractor.parser.cli;

/** Codigos de salida documentados del CLI (ver .claude/rules/java-parser.md). */
public enum CliExitCode {
    SUCCESS(0),
    INVALID_ARGUMENTS(2),
    PARSE_ERROR(3),
    IO_ERROR(4),
    INTERNAL_ERROR(5);

    private final int code;

    CliExitCode(int code) {
        this.code = code;
    }

    public int code() {
        return code;
    }
}
