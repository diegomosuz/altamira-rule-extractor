package com.altamira.extractor.parser.util;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/** SHA-256 hexadecimal en minusculas (mismo formato que Sha256Hex en Python). */
public final class Sha256 {

    private Sha256() {
    }

    public static String ofFile(Path file) throws IOException {
        MessageDigest digest = newDigest();
        try (var input = Files.newInputStream(file)) {
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) != -1) {
                digest.update(buffer, 0, read);
            }
        }
        return HexFormat.of().formatHex(digest.digest());
    }

    private static MessageDigest newDigest() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            // SHA-256 es obligatorio en toda implementacion de la JVM (JCA standard names).
            throw new IllegalStateException("SHA-256 no disponible en esta JVM", e);
        }
    }
}
