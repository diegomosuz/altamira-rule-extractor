package com.altamira.extractor.parser.cli;

import com.altamira.extractor.parser.cobol.CanonicalProgramExtractor;
import com.altamira.extractor.parser.cobol.CobolParseException;
import com.altamira.extractor.parser.cobol.ProLeapCobolParser;
import com.altamira.extractor.parser.json.CanonicalProgramWriter;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.util.Sha256;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;

/**
 * Entry point. Mensajes operativos por stderr; el unico JSON que se
 * produce va a --output (nunca a stdout). Sin --debug no se imprime
 * stack trace completo.
 */
public final class Main {

    private Main() {
    }

    public static void main(String[] args) {
        System.exit(run(args, System.err));
    }

    public static int run(String[] args, PrintStream err) {
        CliArguments arguments;
        try {
            arguments = new ArgumentParser().parse(args);
        } catch (CliArgumentException e) {
            err.println("argumentos invalidos: " + e.getMessage());
            return CliExitCode.INVALID_ARGUMENTS.code();
        }

        try {
            if (!Files.isReadable(arguments.input())) {
                err.println("error de entrada/salida: no se puede leer --input: " + arguments.input());
                return CliExitCode.IO_ERROR.code();
            }

            err.println("parseando " + arguments.input() + " ...");
            ProLeapCobolParser.Result parseResult = new ProLeapCobolParser().parse(
                    arguments.input(), arguments.format(), arguments.copybookDirs(), arguments.encoding());

            String sourceHash = Sha256.ofFile(arguments.input());
            CanonicalProgram program = new CanonicalProgramExtractor().extract(
                    arguments.input(),
                    arguments.encoding(),
                    parseResult,
                    arguments.sourceFile(),
                    sourceHash,
                    arguments.sourcePackageHash());

            new CanonicalProgramWriter().write(program, arguments.output());
            err.println("OK: " + arguments.output());
            return CliExitCode.SUCCESS.code();
        } catch (CobolParseException e) {
            err.println("error de preprocesamiento o parseo COBOL: " + e.getMessage());
            printDebug(e, arguments.debug(), err);
            return CliExitCode.PARSE_ERROR.code();
        } catch (NoSuchFileException e) {
            err.println("error de entrada/salida: archivo no encontrado: " + e.getFile());
            printDebug(e, arguments.debug(), err);
            return CliExitCode.IO_ERROR.code();
        } catch (IOException e) {
            err.println("error de entrada/salida: " + e.getMessage());
            printDebug(e, arguments.debug(), err);
            return CliExitCode.IO_ERROR.code();
        } catch (RuntimeException e) {
            err.println("error interno inesperado: " + e.getMessage());
            printDebug(e, arguments.debug(), err);
            return CliExitCode.INTERNAL_ERROR.code();
        }
    }

    private static void printDebug(Exception e, boolean debug, PrintStream err) {
        if (debug) {
            e.printStackTrace(err);
        }
    }
}
