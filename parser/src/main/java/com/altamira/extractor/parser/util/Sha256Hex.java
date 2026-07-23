package com.altamira.extractor.parser.util;

import java.util.regex.Pattern;

/** Validacion de --source-package-hash: sha256 hex, 64 caracteres, minusculas. */
public final class Sha256Hex {

    private static final Pattern PATTERN = Pattern.compile("^[a-f0-9]{64}$");

    private Sha256Hex() {
    }

    public static boolean isValid(String value) {
        return value != null && PATTERN.matcher(value).matches();
    }
}
