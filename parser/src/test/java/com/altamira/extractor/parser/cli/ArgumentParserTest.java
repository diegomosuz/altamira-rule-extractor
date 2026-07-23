package com.altamira.extractor.parser.cli;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.altamira.extractor.parser.cobol.RequestedFormat;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;

class ArgumentParserTest {

    private final ArgumentParser parser = new ArgumentParser();
    private static final String VALID_HASH = "a".repeat(64);

    @Test
    void parsesMinimalValidArguments() throws Exception {
        CliArguments args = parser.parse(new String[] {
                "parse", "--input", "in.cbl", "--output", "out.json",
                "--source-package-hash", VALID_HASH,
        });
        assertEquals(Path.of("in.cbl"), args.input());
        assertEquals(Path.of("out.json"), args.output());
        assertEquals(VALID_HASH, args.sourcePackageHash());
        assertEquals("in.cbl", args.sourceFile(), "default: basename de --input");
        assertEquals(RequestedFormat.AUTO, args.format());
        assertEquals("UTF-8", args.encoding().name());
        assertTrue(args.copybookDirs().isEmpty());
        assertEquals(false, args.debug());
    }

    @Test
    void requiresParseCommand() {
        assertThrows(CliArgumentException.class,
                () -> parser.parse(new String[] {"--input", "in.cbl"}));
    }

    @Test
    void requiresInput() {
        assertThrows(CliArgumentException.class, () -> parser.parse(new String[] {
                "parse", "--output", "out.json", "--source-package-hash", VALID_HASH,
        }));
    }

    @Test
    void requiresOutput() {
        assertThrows(CliArgumentException.class, () -> parser.parse(new String[] {
                "parse", "--input", "in.cbl", "--source-package-hash", VALID_HASH,
        }));
    }

    @Test
    void requiresSourcePackageHash() {
        assertThrows(CliArgumentException.class, () -> parser.parse(new String[] {
                "parse", "--input", "in.cbl", "--output", "out.json",
        }));
    }

    @Test
    void rejectsSourcePackageHashWithWrongLength() {
        assertThrows(CliArgumentException.class, () -> parser.parse(new String[] {
                "parse", "--input", "in.cbl", "--output", "out.json",
                "--source-package-hash", "abc123",
        }));
    }

    @Test
    void rejectsSourcePackageHashWithUppercase() {
        assertThrows(CliArgumentException.class, () -> parser.parse(new String[] {
                "parse", "--input", "in.cbl", "--output", "out.json",
                "--source-package-hash", "A".repeat(64),
        }));
    }

    @Test
    void acceptsExplicitRelativeSourceFile() throws Exception {
        CliArguments args = parser.parse(new String[] {
                "parse", "--input", "/abs/path/in.cbl", "--output", "out.json",
                "--source-package-hash", VALID_HASH,
                "--source-file", "01-codigo/cobol/in.cbl",
        });
        assertEquals("01-codigo/cobol/in.cbl", args.sourceFile());
    }

    @Test
    void rejectsAbsoluteSourceFile() {
        assertThrows(CliArgumentException.class, () -> parser.parse(new String[] {
                "parse", "--input", "in.cbl", "--output", "out.json",
                "--source-package-hash", VALID_HASH,
                "--source-file", "/etc/passwd",
        }));
    }

    @Test
    void rejectsSourceFileWithParentTraversal() {
        assertThrows(CliArgumentException.class, () -> parser.parse(new String[] {
                "parse", "--input", "in.cbl", "--output", "out.json",
                "--source-package-hash", VALID_HASH,
                "--source-file", "../escape.cbl",
        }));
    }

    @Test
    void acceptsRepeatedCopybookDir() throws Exception {
        CliArguments args = parser.parse(new String[] {
                "parse", "--input", "in.cbl", "--output", "out.json",
                "--source-package-hash", VALID_HASH,
                "--copybook-dir", "dir1",
                "--copybook-dir", "dir2",
        });
        assertEquals(2, args.copybookDirs().size());
    }

    @Test
    void parsesEachFormatValue() throws Exception {
        for (RequestedFormat format : RequestedFormat.values()) {
            CliArguments args = parser.parse(new String[] {
                    "parse", "--input", "in.cbl", "--output", "out.json",
                    "--source-package-hash", VALID_HASH,
                    "--format", format.name(),
            });
            assertEquals(format, args.format());
        }
    }

    @Test
    void rejectsInvalidFormat() {
        assertThrows(CliArgumentException.class, () -> parser.parse(new String[] {
                "parse", "--input", "in.cbl", "--output", "out.json",
                "--source-package-hash", VALID_HASH,
                "--format", "EBCDIC",
        }));
    }

    @Test
    void rejectsInvalidEncoding() {
        assertThrows(CliArgumentException.class, () -> parser.parse(new String[] {
                "parse", "--input", "in.cbl", "--output", "out.json",
                "--source-package-hash", VALID_HASH,
                "--encoding", "not-a-real-encoding",
        }));
    }

    @Test
    void parsesDebugFlag() throws Exception {
        CliArguments args = parser.parse(new String[] {
                "parse", "--input", "in.cbl", "--output", "out.json",
                "--source-package-hash", VALID_HASH,
                "--debug",
        });
        assertTrue(args.debug());
    }

    @Test
    void rejectsUnknownArgument() {
        assertThrows(CliArgumentException.class, () -> parser.parse(new String[] {
                "parse", "--input", "in.cbl", "--output", "out.json",
                "--source-package-hash", VALID_HASH,
                "--bogus-flag",
        }));
    }
}
