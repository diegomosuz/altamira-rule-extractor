package com.altamira.extractor.parser.cobol;

import java.nio.file.Path;

/** Resuelve rutas de fixtures bajo src/test/resources/fixtures (CWD = modulo parser/ en Surefire). */
final class Fixtures {

    private static final Path ROOT = Path.of("src", "test", "resources", "fixtures");

    private Fixtures() {
    }

    static Path path(String name) {
        return ROOT.resolve(name);
    }

    static Path copybookDir() {
        return ROOT.resolve("copybooks");
    }
}
