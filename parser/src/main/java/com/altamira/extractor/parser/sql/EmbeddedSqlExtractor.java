package com.altamira.extractor.parser.sql;

import com.altamira.extractor.parser.model.TableAccessOperation;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
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
 *
 * <p><b>Direccion de host variables (Fase 15B3-C3-B)</b>: ademas de la
 * lista plana historica {@code hostVariables} (retrocompatible, sin
 * cambios), esta clase ahora distingue -- UNICAMENTE para la forma simple
 * y no ambigua de cada verbo -- que variables son entrada
 * ({@code inputHostVariables}), cuales son salida
 * ({@code outputHostVariables}) y cuales provienen especificamente de la
 * clausula WHERE ({@code predicateHostVariables}). La segmentacion sigue
 * siendo por delimitadores de palabra clave (INTO/SET/VALUES/WHERE), NUNCA
 * gramatica SQL: sigue sin haber AST, sin JOIN real, sin subconsultas. Ante
 * cualquier variable indicadora (":VAR:IND", adyacente sin separador) toda
 * la sentencia degrada a direccion no resuelta (nunca infiere
 * incorrectamente cual mitad del par es entrada/salida) -- ver
 * {@code hasIndicatorVariables}.
 */
public final class EmbeddedSqlExtractor {

    public record Access(
            String table,
            TableAccessOperation operation,
            String predicateText,
            List<String> hostVariables,
            List<String> inputHostVariables,
            List<String> outputHostVariables,
            List<String> predicateHostVariables,
            List<String> selectedColumns,
            boolean hasIndicatorVariables) {
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
    // Variable indicadora (":VAR:IND", sin separador entre ambas) -- Fase
    // 15B3-C3-B, seccion 15: nunca se le asigna direccion, solo se detecta
    // su presencia para degradar la sentencia completa a no-resuelto.
    private static final Pattern INDICATOR_VARIABLE_PAIR =
            Pattern.compile(":[A-Za-z][A-Za-z0-9-]*:[A-Za-z][A-Za-z0-9-]*");
    // Cualquier variante de JOIN explicito en la clausula FROM: la sentencia
    // completa se declara no interpretada (nunca tabla parcial silenciosa,
    // ver EmbeddedSqlExtractorTest y correccion de C3-A).
    private static final Pattern JOIN_KEYWORD = Pattern.compile(
            "(?is)\\b(INNER\\s+|LEFT\\s+(OUTER\\s+)?|RIGHT\\s+(OUTER\\s+)?|FULL\\s+(OUTER\\s+)?)?JOIN\\b");
    private static final Pattern SELECT_LIST = Pattern.compile("(?is)\\bSELECT\\s+(.+?)(?=\\bINTO\\b|\\bFROM\\b)");
    private static final Pattern INTO_CLAUSE = Pattern.compile("(?is)\\bINTO\\s+(.+?)(?=\\bFROM\\b)");
    private static final Pattern SET_CLAUSE = Pattern.compile("(?is)\\bSET\\s+(.+?)(?=\\bWHERE\\b|$)");
    private static final Pattern VALUES_CLAUSE = Pattern.compile("(?is)\\bVALUES\\s*\\((.+?)\\)\\s*$");
    // Identificador SQL simple, opcionalmente calificado por una tabla/alias
    // ("SALDO" o "A.SALDO") -- cualquier otra forma (funcion, expresion,
    // alias AS, DISTINCT, "*") se rechaza sin intentar reconstruirla.
    private static final Pattern SIMPLE_IDENTIFIER =
            Pattern.compile("^[A-Za-z][A-Za-z0-9_$#@]*(\\.[A-Za-z][A-Za-z0-9_$#@]*)?$");

    public Result extract(String rawSqlText) {
        if (rawSqlText == null || rawSqlText.isBlank()) {
            return Result.unsupported("texto EXEC SQL vacio");
        }

        Matcher insertMatcher = INSERT_INTO.matcher(rawSqlText);
        if (insertMatcher.find()) {
            return insertAccess(rawSqlText, insertMatcher.group(1));
        }

        Matcher updateMatcher = UPDATE_TABLE.matcher(rawSqlText);
        if (updateMatcher.find()) {
            return updateAccess(rawSqlText, updateMatcher.group(1));
        }

        Matcher deleteMatcher = DELETE_FROM.matcher(rawSqlText);
        if (deleteMatcher.find()) {
            return deleteAccess(rawSqlText, deleteMatcher.group(1));
        }

        if (SELECT_KEYWORD.matcher(rawSqlText).find()) {
            return selectAccess(rawSqlText);
        }

        return Result.unsupported("no coincide con el subconjunto SELECT/INSERT/UPDATE/DELETE soportado");
    }

    private Result selectAccess(String rawSqlText) {
        Matcher fromMatcher = FROM_CLAUSE.matcher(rawSqlText);
        if (!fromMatcher.find()) {
            return Result.unsupported("SELECT sin clausula FROM identificable");
        }
        String fromSegment = fromMatcher.group(1);
        if (JOIN_KEYWORD.matcher(fromSegment).find()) {
            return Result.unsupported(
                    "SELECT con JOIN explicito no se interpreta (subconjunto acotado sin gramatica SQL): "
                            + "nunca se reporta una tabla parcial como si fuera el resultado completo");
        }

        boolean indicator = hasIndicatorVariables(rawSqlText);
        String predicate = predicateTextOf(rawSqlText);
        List<String> allHostVariables = hostVariablesOf(rawSqlText);
        List<String> predicateHostVariables = indicator ? List.of() : hostVariablesOf(whereSegmentOf(rawSqlText));
        List<String> outputHostVariables = indicator ? List.of() : intoHostVariablesOf(rawSqlText);
        List<String> selectedColumns =
                indicator ? List.of() : selectedColumnsOf(rawSqlText, outputHostVariables.size());

        List<Access> accesses = new ArrayList<>();
        for (String segment : fromSegment.split(",")) {
            Matcher identifierMatcher = LEADING_IDENTIFIER.matcher(segment);
            if (identifierMatcher.find()) {
                accesses.add(new Access(
                        identifierMatcher.group(1), TableAccessOperation.READS, predicate, allHostVariables,
                        predicateHostVariables, outputHostVariables, predicateHostVariables, selectedColumns,
                        indicator));
            }
        }
        if (accesses.isEmpty()) {
            return Result.unsupported("SELECT sin ninguna tabla identificable en FROM");
        }
        return Result.of(accesses);
    }

    private Result insertAccess(String rawSqlText, String table) {
        if (table == null || table.isBlank()) {
            return Result.unsupported("no se pudo identificar la tabla de forma segura");
        }
        boolean indicator = hasIndicatorVariables(rawSqlText);
        List<String> inputHostVariables = indicator ? List.of() : valuesHostVariablesOf(rawSqlText);
        Access access = new Access(
                table, TableAccessOperation.INSERTS, predicateTextOf(rawSqlText), hostVariablesOf(rawSqlText),
                inputHostVariables, List.of(), List.of(), List.of(), indicator);
        return Result.of(List.of(access));
    }

    private Result updateAccess(String rawSqlText, String table) {
        if (table == null || table.isBlank()) {
            return Result.unsupported("no se pudo identificar la tabla de forma segura");
        }
        boolean indicator = hasIndicatorVariables(rawSqlText);
        List<String> predicateHostVariables = indicator ? List.of() : hostVariablesOf(whereSegmentOf(rawSqlText));
        List<String> setHostVariables = indicator ? List.of() : hostVariablesOf(setSegmentOf(rawSqlText));
        List<String> inputHostVariables =
                indicator ? List.of() : mergeOrderedUnique(setHostVariables, predicateHostVariables);
        Access access = new Access(
                table, TableAccessOperation.UPDATES, predicateTextOf(rawSqlText), hostVariablesOf(rawSqlText),
                inputHostVariables, List.of(), predicateHostVariables, List.of(), indicator);
        return Result.of(List.of(access));
    }

    private Result deleteAccess(String rawSqlText, String table) {
        if (table == null || table.isBlank()) {
            return Result.unsupported("no se pudo identificar la tabla de forma segura");
        }
        boolean indicator = hasIndicatorVariables(rawSqlText);
        List<String> predicateHostVariables = indicator ? List.of() : hostVariablesOf(whereSegmentOf(rawSqlText));
        Access access = new Access(
                table, TableAccessOperation.WRITES, predicateTextOf(rawSqlText), hostVariablesOf(rawSqlText),
                predicateHostVariables, List.of(), predicateHostVariables, List.of(), indicator);
        return Result.of(List.of(access));
    }

    private static boolean hasIndicatorVariables(String rawSqlText) {
        return INDICATOR_VARIABLE_PAIR.matcher(rawSqlText).find();
    }

    private static String whereSegmentOf(String rawSqlText) {
        Matcher matcher = WHERE_CLAUSE.matcher(rawSqlText);
        return matcher.find() ? matcher.group(1) : "";
    }

    private static String setSegmentOf(String rawSqlText) {
        Matcher matcher = SET_CLAUSE.matcher(rawSqlText);
        return matcher.find() ? matcher.group(1) : "";
    }

    private static List<String> intoHostVariablesOf(String rawSqlText) {
        Matcher matcher = INTO_CLAUSE.matcher(rawSqlText);
        if (!matcher.find()) {
            return List.of();
        }
        // Orden RAW (nunca deduplicado): output_host_variables debe conservar
        // correspondencia posicional exacta con selected_columns.
        List<String> names = new ArrayList<>();
        Matcher hostVarMatcher = HOST_VARIABLE.matcher(matcher.group(1));
        while (hostVarMatcher.find()) {
            names.add(hostVarMatcher.group(1));
        }
        return names;
    }

    private static List<String> valuesHostVariablesOf(String rawSqlText) {
        Matcher matcher = VALUES_CLAUSE.matcher(rawSqlText.strip());
        if (!matcher.find()) {
            return List.of();
        }
        return hostVariablesOf(matcher.group(1));
    }

    private static List<String> selectedColumnsOf(String rawSqlText, int expectedCount) {
        if (expectedCount == 0) {
            return List.of();
        }
        Matcher matcher = SELECT_LIST.matcher(rawSqlText);
        if (!matcher.find()) {
            return List.of();
        }
        List<String> columns = new ArrayList<>();
        for (String segment : matcher.group(1).split(",")) {
            String trimmed = segment.strip();
            if (!SIMPLE_IDENTIFIER.matcher(trimmed).matches()) {
                // Funcion, expresion, alias, DISTINCT, "*": nunca se
                // reconstruye un pairing dudoso -- sin columnas, mejor que
                // columnas incorrectas.
                return List.of();
            }
            columns.add(trimmed);
        }
        if (columns.size() != expectedCount) {
            return List.of();
        }
        return columns;
    }

    private static List<String> mergeOrderedUnique(List<String> first, List<String> second) {
        LinkedHashSet<String> merged = new LinkedHashSet<>(first);
        merged.addAll(second);
        return new ArrayList<>(merged);
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
        LinkedHashSet<String> names = new LinkedHashSet<>();
        Matcher matcher = HOST_VARIABLE.matcher(rawSqlText);
        while (matcher.find()) {
            names.add(matcher.group(1));
        }
        return new ArrayList<>(names);
    }
}
