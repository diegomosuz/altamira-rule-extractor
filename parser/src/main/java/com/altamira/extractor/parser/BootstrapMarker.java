package com.altamira.extractor.parser;

/**
 * Marcador minimo del bootstrap del modulo parser.
 *
 * <p>No implementa parsing COBOL ni integra ProLeap todavia; solo existe
 * para que el modulo Maven tenga codigo real que compilar y testear en
 * esta etapa (ver docs/CLAUDE_CODE_RUNBOOK.md, Prompt 4 para el parser
 * real).
 */
public final class BootstrapMarker {

    private BootstrapMarker() {
    }

    public static String moduleName() {
        return "altamira-extractor-parser";
    }
}
