package com.altamira.extractor.parser.sql;

import com.altamira.extractor.parser.model.TableAccessOperation;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Extractor determinista de un subconjunto acotado de EXEC SQL: SELECT,
 * INSERT, UPDATE, DELETE de una sola sentencia, con tabla(s), host
 * variables (":NOMBRE") y predicate_text (clausula WHERE) cuando pueden
 * identificarse con seguridad via expresiones regulares simples sobre
 * palabras clave SQL bien definidas. No es un parser DB2/SQL completo: no
 * entiende JOIN, subconsultas, CTEs, ni gramatica SQL general. Cuando el
 * texto no coincide con este subconjunto, no inventa tabla ni operacion:
 * devuelve un resultado no interpretado y el texto crudo se conserva de
 * todos modos en CanonicalStatement.source_text.
 *
 * <p>Mapeo de operacion: SELECT-&gt;READS, INSERT-&gt;INSERTS,
 * UPDATE-&gt;UPDATES, DELETE-&gt;WRITES (el enum TableAccessOperation no
 * tiene un valor DELETES propio; ver CLAUDE.md, que solo permite
 * READS/WRITES/UPDATES/INSERTS en el metamodelo).
 */
public final class EmbeddedSqlExtractor {

    public record Access(
            String table, TableAccessOperation operation, String predicateText, List<String> hostVariables) {
    }

    public record Result(List<Access> accesses, boolean interpreted, String reason) {
        static Result unsupported(String reason) {
            return new Result(List.of(), false, reason);
        }

        static Result of(List<Access> accesses) {
            return new Result(accesses, true, null);
        }
    }

    private static final Pattern INSERT_INTO = Pattern.compile("(?is)\\bINSERT\\s+INTO\\s+([A-Za-z][\\w$#@.]*)");
    private static final Pattern UPDATE_TABLE = Pattern.compile("(?is)\\bUPDATE\\s+([A-Za-z][\\w$#@.]*)");
    private static final Pattern DELETE_FROM = Pattern.compile("(?is)\\bDELETE\\s+FROM\\s+([A-Za-z][\\w$#@.]*)");
    private static final Pattern SELECT_KEYWORD = Pattern.compile("(?is)\\bSELECT\\b");
    // Captura el texto crudo entre FROM y WHERE (o fin): las tablas se extraen
    // por segmento en un segundo paso para tolerar alias sin coma
    // ("FROM CUENTAS A, MOVIMIENTOS B") sin intentar parsear SQL completo.
    private static final Pattern FROM_CLAUSE = Pattern.compile("(?is)\\bFROM\\s+(.+?)(?=\\bWHERE\\b|$)");
    private static final Pattern LEADING_IDENTIFIER = Pattern.compile("^\\s*([A-Za-z][\\w$#@.]*)");
    private static final Pattern WHERE_CLAUSE = Pattern.compile("(?is)\\bWHERE\\b(.*)$");
    private static final Pattern HOST_VARIABLE = Pattern.compile(":([A-Za-z][A-Za-z0-9-]*)");

    public Result extract(String rawSqlText) {
        if (rawSqlText == null || rawSqlText.isBlank()) {
            return Result.unsupported("texto EXEC SQL vacio");
        }

        Matcher insertMatcher = INSERT_INTO.matcher(rawSqlText);
        if (insertMatcher.find()) {
            return singleTable(rawSqlText, insertMatcher.group(1), TableAccessOperation.INSERTS);
        }

        Matcher updateMatcher = UPDATE_TABLE.matcher(rawSqlText);
        if (updateMatcher.find()) {
            return singleTable(rawSqlText, updateMatcher.group(1), TableAccessOperation.UPDATES);
        }

        Matcher deleteMatcher = DELETE_FROM.matcher(rawSqlText);
        if (deleteMatcher.find()) {
            return singleTable(rawSqlText, deleteMatcher.group(1), TableAccessOperation.WRITES);
        }

        if (SELECT_KEYWORD.matcher(rawSqlText).find()) {
            Matcher fromMatcher = FROM_CLAUSE.matcher(rawSqlText);
            if (!fromMatcher.find()) {
                return Result.unsupported("SELECT sin clausula FROM identificable");
            }
            List<String> hostVariables = hostVariablesOf(rawSqlText);
            String predicate = predicateTextOf(rawSqlText);
            List<Access> accesses = new ArrayList<>();
            for (String segment : fromMatcher.group(1).split(",")) {
                Matcher identifierMatcher = LEADING_IDENTIFIER.matcher(segment);
                if (identifierMatcher.find()) {
                    accesses.add(new Access(
                            identifierMatcher.group(1), TableAccessOperation.READS, predicate, hostVariables));
                }
            }
            if (accesses.isEmpty()) {
                return Result.unsupported("SELECT sin ninguna tabla identificable en FROM");
            }
            return Result.of(accesses);
        }

        return Result.unsupported("no coincide con el subconjunto SELECT/INSERT/UPDATE/DELETE soportado");
    }

    private Result singleTable(String rawSqlText, String table, TableAccessOperation operation) {
        if (table == null || table.isBlank()) {
            return Result.unsupported("no se pudo identificar la tabla de forma segura");
        }
        Access access = new Access(
                table, operation, predicateTextOf(rawSqlText), hostVariablesOf(rawSqlText));
        return Result.of(List.of(access));
    }

    private static String predicateTextOf(String rawSqlText) {
        Matcher matcher = WHERE_CLAUSE.matcher(rawSqlText);
        if (!matcher.find()) {
            return null;
        }
        String predicate = ("WHERE " + matcher.group(1)).replaceAll("\\s+", " ").trim();
        return predicate.isEmpty() ? null : predicate;
    }

    private static List<String> hostVariablesOf(String rawSqlText) {
        Set<String> names = new LinkedHashSet<>();
        Matcher matcher = HOST_VARIABLE.matcher(rawSqlText);
        while (matcher.find()) {
            names.add(matcher.group(1));
        }
        return new ArrayList<>(names);
    }
}
