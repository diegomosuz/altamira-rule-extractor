package com.altamira.extractor.parser;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class BootstrapMarkerTest {

    @Test
    void moduleNameIsStable() {
        assertEquals("altamira-extractor-parser", BootstrapMarker.moduleName());
    }
}
