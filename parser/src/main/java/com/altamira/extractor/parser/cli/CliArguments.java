package com.altamira.extractor.parser.cli;

import com.altamira.extractor.parser.cobol.RequestedFormat;
import java.nio.charset.Charset;
import java.nio.file.Path;
import java.util.List;

/** Argumentos ya parseados y validados del comando {@code parse}. */
public record CliArguments(
        Path input,
        Path output,
        String sourcePackageHash,
        String sourceFile,
        List<Path> copybookDirs,
        RequestedFormat format,
        Charset encoding,
        boolean debug) {
}
