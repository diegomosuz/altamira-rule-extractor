package com.altamira.extractor.parser.cli;

import com.altamira.extractor.parser.cobol.RequestedFormat;
import com.altamira.extractor.parser.util.Sha256Hex;
import java.nio.charset.Charset;
import java.nio.charset.IllegalCharsetNameException;
import java.nio.charset.UnsupportedCharsetException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Parseo manual de argumentos (sin librerias de CLI adicionales): la
 * superficie es pequena y fija (un unico comando {@code parse}, seis
 * flags mas {@code --debug}), no justifica una nueva dependencia.
 */
public final class ArgumentParser {

    public CliArguments parse(String[] args) throws CliArgumentException {
        if (args.length == 0 || !"parse".equals(args[0])) {
            throw new CliArgumentException("comando requerido: parse");
        }

        Path input = null;
        Path output = null;
        String sourcePackageHash = null;
        String sourceFile = null;
        List<Path> copybookDirs = new ArrayList<>();
        RequestedFormat format = RequestedFormat.AUTO;
        String encodingName = "UTF-8";
        boolean debug = false;

        int i = 1;
        while (i < args.length) {
            String arg = args[i];
            switch (arg) {
                case "--input" -> {
                    input = Path.of(requireValue(args, i, "--input"));
                    i += 2;
                }
                case "--output" -> {
                    output = Path.of(requireValue(args, i, "--output"));
                    i += 2;
                }
                case "--source-package-hash" -> {
                    sourcePackageHash = requireValue(args, i, "--source-package-hash");
                    i += 2;
                }
                case "--source-file" -> {
                    sourceFile = requireValue(args, i, "--source-file");
                    i += 2;
                }
                case "--copybook-dir" -> {
                    copybookDirs.add(Path.of(requireValue(args, i, "--copybook-dir")));
                    i += 2;
                }
                case "--format" -> {
                    format = parseFormat(requireValue(args, i, "--format"));
                    i += 2;
                }
                case "--encoding" -> {
                    encodingName = requireValue(args, i, "--encoding");
                    i += 2;
                }
                case "--debug" -> {
                    debug = true;
                    i += 1;
                }
                default -> throw new CliArgumentException("argumento desconocido: " + arg);
            }
        }

        if (input == null) {
            throw new CliArgumentException("--input es obligatorio");
        }
        if (output == null) {
            throw new CliArgumentException("--output es obligatorio");
        }
        if (sourcePackageHash == null) {
            throw new CliArgumentException("--source-package-hash es obligatorio");
        }
        if (!Sha256Hex.isValid(sourcePackageHash)) {
            throw new CliArgumentException(
                    "--source-package-hash debe ser sha256 hexadecimal: 64 caracteres [a-f0-9]");
        }
        if (sourceFile != null && !isRelative(sourceFile)) {
            throw new CliArgumentException("--source-file debe ser una ruta relativa: " + sourceFile);
        }

        Charset encoding;
        try {
            encoding = Charset.forName(encodingName);
        } catch (IllegalCharsetNameException | UnsupportedCharsetException e) {
            throw new CliArgumentException("--encoding invalido: " + encodingName);
        }

        String resolvedSourceFile = sourceFile != null ? sourceFile : input.getFileName().toString();

        return new CliArguments(
                input, output, sourcePackageHash, resolvedSourceFile, copybookDirs, format, encoding, debug);
    }

    private static boolean isRelative(String path) {
        if (path.isBlank()) {
            return false;
        }
        String normalized = path.replace('\\', '/');
        if (normalized.startsWith("/")) {
            return false;
        }
        if (normalized.length() >= 2 && normalized.charAt(1) == ':') {
            return false;
        }
        for (String segment : normalized.split("/")) {
            if (segment.equals("..")) {
                return false;
            }
        }
        return true;
    }

    private static RequestedFormat parseFormat(String value) throws CliArgumentException {
        try {
            return RequestedFormat.valueOf(value.toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException e) {
            throw new CliArgumentException("--format invalido: " + value + " (use AUTO, FIXED, FREE o TANDEM)");
        }
    }

    private static String requireValue(String[] args, int index, String flag) throws CliArgumentException {
        if (index + 1 >= args.length) {
            throw new CliArgumentException(flag + " requiere un valor");
        }
        return args[index + 1];
    }
}
