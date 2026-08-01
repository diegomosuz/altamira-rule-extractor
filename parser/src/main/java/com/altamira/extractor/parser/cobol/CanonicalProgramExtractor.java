package com.altamira.extractor.parser.cobol;

import com.altamira.extractor.parser.model.CanonicalConditionName;
import com.altamira.extractor.parser.model.CanonicalConditionValue;
import com.altamira.extractor.parser.model.CanonicalDataItem;
import com.altamira.extractor.parser.model.CanonicalParagraph;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.CanonicalSqlAccess;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.LocationKind;
import io.proleap.cobol.asg.metamodel.ProgramUnit;
import io.proleap.cobol.asg.metamodel.data.DataDivision;
import io.proleap.cobol.asg.metamodel.data.datadescription.DataDescriptionEntry;
import io.proleap.cobol.asg.metamodel.data.datadescription.DataDescriptionEntryCondition;
import io.proleap.cobol.asg.metamodel.data.datadescription.DataDescriptionEntryGroup;
import io.proleap.cobol.asg.metamodel.data.datadescription.ValueClause;
import io.proleap.cobol.asg.metamodel.data.datadescription.ValueInterval;
import io.proleap.cobol.asg.metamodel.data.workingstorage.WorkingStorageSection;
import io.proleap.cobol.asg.metamodel.procedure.Paragraph;
import io.proleap.cobol.asg.metamodel.procedure.ProcedureDivision;
import io.proleap.cobol.asg.metamodel.valuestmt.ValueStmt;
import java.io.IOException;
import java.nio.charset.Charset;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Function;
import org.antlr.v4.runtime.ParserRuleContext;

/**
 * Camina el ASG de ProLeap (Program/CompilationUnit/ProgramUnit) y produce
 * un CanonicalProgram. Junto con {@link ProLeapCobolParser} y
 * {@link StatementExtractor}, son las unicas clases del modulo que
 * importan tipos {@code io.proleap.*}.
 */
public final class CanonicalProgramExtractor {

    public CanonicalProgram extract(
            Path inputFile,
            Charset encoding,
            ProLeapCobolParser.Result parseResult,
            String sourceFileIdentity,
            String sourceHash,
            String sourcePackageHash) throws IOException {

        ProgramUnit programUnit = parseResult.programUnit();
        String programName = programUnit.getIdentificationDivision().getProgramIdParagraph().getName();

        List<String> rawLines = Files.readAllLines(inputFile, encoding);
        boolean copyDetected = CopyDetector.containsCopyStatement(rawLines, parseResult.resolvedFormat());
        LocationKind programLocationKind = copyDetected ? LocationKind.PREPROCESSED_STREAM : LocationKind.EXACT;

        ExtractionContext ctx = new ExtractionContext(
                programName,
                programLocationKind,
                programLocationKind == LocationKind.EXACT ? sourceFileIdentity : null,
                parseResult.compilationUnit().getLines());
        ctx.warnings.addAll(parseResult.formatDetectionWarnings());
        if (copyDetected) {
            ctx.warnings.add(
                    "el programa contiene COPY; ProLeap expone el stream ya expandido sin indicar el "
                            + "archivo fisico de origen de cada linea, asi que los elementos afectados "
                            + "quedan con location_kind=PREPROCESSED_STREAM y source_file=null (no se "
                            + "atribuyen al programa principal)");
        }

        List<CanonicalDataItem> dataItems = extractDataItems(programUnit, ctx);
        List<CanonicalConditionName> conditionNames = extractConditionNames(programUnit, ctx);
        List<CanonicalParagraph> paragraphs = extractParagraphs(programUnit, ctx, conditionNames);
        String schemaVersion = schemaVersionFor(conditionNames, paragraphs);

        return new CanonicalProgram(
                schemaVersion,
                programName,
                sourceFileIdentity,
                sourceHash,
                sourcePackageHash,
                parseResult.resolvedFormat(),
                encoding.name(),
                dataItems,
                conditionNames,
                paragraphs,
                List.copyOf(ctx.warnings),
                List.copyOf(ctx.unsupportedConstructs));
    }

    /**
     * {@code "1.0"} para un programa que no usa NINGUNA extension de
     * nivel 88 (Fase 3): produce el mismo JSON canonico, byte a byte,
     * que el parser previo a esa fase (conditionNames vacia se omite del
     * JSON via {@code @JsonInclude(NON_EMPTY)}, y ningun CanonicalStatement
     * tiene conditionNameTarget/referencedConditionNames poblados, asi
     * que esos campos tampoco aparecen). {@code "1.1"} en cuanto
     * cualquiera de esas tres senales esta realmente presente en el
     * programa. Ver docs/LEVEL_88_SUPPORT.md.
     */
    private static String schemaVersionFor(
            List<CanonicalConditionName> conditionNames, List<CanonicalParagraph> paragraphs) {
        if (!conditionNames.isEmpty()) {
            return "1.1";
        }
        boolean anyStatementUsesLevel88 = paragraphs.stream()
                .flatMap(paragraph -> paragraph.statements().stream())
                .anyMatch(statement -> statement.conditionNameTarget() != null
                        || !statement.referencedConditionNames().isEmpty());
        return anyStatementUsesLevel88 ? "1.1" : "1.0";
    }

    // --- Data Division -------------------------------------------------

    private List<CanonicalDataItem> extractDataItems(ProgramUnit programUnit, ExtractionContext ctx) {
        List<CanonicalDataItem> items = new ArrayList<>();
        DataDivision dataDivision = programUnit.getDataDivision();
        if (dataDivision == null || dataDivision.getWorkingStorageSection() == null) {
            return items;
        }
        WorkingStorageSection wss = dataDivision.getWorkingStorageSection();
        for (DataDescriptionEntry entry : wss.getDataDescriptionEntries()) {
            CanonicalDataItem item = convertDataItem(entry, ctx);
            if (item != null) {
                items.add(item);
            }
        }
        return items;
    }

    private CanonicalDataItem convertDataItem(DataDescriptionEntry entry, ExtractionContext ctx) {
        Integer level = entry.getLevelNumber();
        if (level == null) {
            ctx.unsupported("DataDescriptionEntry sin level number determinable; omitido del artefacto");
            return null;
        }
        String name = dataItemName(entry);
        String qualifiedName = qualifiedNameOf(entry);
        String pic = pictureOf(entry);
        String usage = usageOf(entry);

        ParserRuleContext entryCtx = entry.getCtx();
        if (entryCtx == null || entryCtx.getStart() == null) {
            return new CanonicalDataItem(name, qualifiedName, level, pic, usage, null, null, LocationKind.UNKNOWN);
        }
        int line = entryCtx.getStart().getLine();
        if (ctx.programLocationKind == LocationKind.EXACT) {
            return new CanonicalDataItem(
                    name, qualifiedName, level, pic, usage, ctx.sourceFileForExact, line, LocationKind.EXACT);
        }
        return new CanonicalDataItem(
                name, qualifiedName, level, pic, usage, null, line, LocationKind.PREPROCESSED_STREAM);
    }

    // --- Condiciones nivel 88 (Fase 3 de la ampliacion semantica) -------

    private List<CanonicalConditionName> extractConditionNames(ProgramUnit programUnit, ExtractionContext ctx) {
        List<CanonicalConditionName> conditions = new ArrayList<>();
        DataDivision dataDivision = programUnit.getDataDivision();
        if (dataDivision == null || dataDivision.getWorkingStorageSection() == null) {
            return conditions;
        }
        WorkingStorageSection wss = dataDivision.getWorkingStorageSection();
        for (DataDescriptionEntry entry : wss.getDataDescriptionEntries()) {
            if (entry instanceof DataDescriptionEntryCondition condition) {
                CanonicalConditionName converted = convertConditionName(condition, ctx);
                if (converted != null) {
                    conditions.add(converted);
                }
            }
        }
        return conditions;
    }

    private CanonicalConditionName convertConditionName(
            DataDescriptionEntryCondition condition, ExtractionContext ctx) {
        String name = dataItemName(condition);
        DataDescriptionEntryGroup parent = condition.getParentDataDescriptionEntryGroup();
        if (parent == null) {
            ctx.unsupported(
                    "condicion 88 " + name
                            + " sin data item padre determinable (kind=CONDITION_NAME); omitida del artefacto");
            return null;
        }
        List<CanonicalConditionValue> values = extractConditionValues(condition, ctx);
        if (values.isEmpty()) {
            ctx.unsupported(
                    "condicion 88 " + name
                            + " sin VALUE demostrable (kind=CONDITION_NAME); omitida del artefacto");
            return null;
        }

        String parentName = dataItemName(parent);
        String parentQualifiedName = qualifiedNameOf(parent);
        String qualifiedName = parentQualifiedName + "." + name;

        ParserRuleContext entryCtx = condition.getCtx();
        if (entryCtx == null || entryCtx.getStart() == null) {
            return new CanonicalConditionName(
                    name, qualifiedName, parentName, parentQualifiedName, values, null, null, LocationKind.UNKNOWN);
        }
        int line = entryCtx.getStart().getLine();
        if (ctx.programLocationKind == LocationKind.EXACT) {
            return new CanonicalConditionName(
                    name, qualifiedName, parentName, parentQualifiedName, values, ctx.sourceFileForExact, line,
                    LocationKind.EXACT);
        }
        return new CanonicalConditionName(
                name, qualifiedName, parentName, parentQualifiedName, values, null, line,
                LocationKind.PREPROCESSED_STREAM);
    }

    private List<CanonicalConditionValue> extractConditionValues(
            DataDescriptionEntryCondition condition, ExtractionContext ctx) {
        ValueClause valueClause = condition.getValueClause();
        if (valueClause == null) {
            return List.of();
        }
        List<CanonicalConditionValue> values = new ArrayList<>();
        for (ValueInterval interval : valueClause.getValueIntervals()) {
            String value = literalTextOf(interval.getFromValueStmt());
            if (value == null) {
                // Intervalo sin FROM demostrable: se omite unicamente ese
                // intervalo, nunca se inventa un valor sustituto.
                continue;
            }
            String throughValue = literalTextOf(interval.getToValueStmt());
            values.add(intervalToConditionValue(interval, value, throughValue, ctx));
        }
        return values;
    }

    private CanonicalConditionValue intervalToConditionValue(
            ValueInterval interval, String value, String throughValue, ExtractionContext ctx) {
        ParserRuleContext intervalCtx = interval.getCtx();
        if (intervalCtx == null || intervalCtx.getStart() == null) {
            return new CanonicalConditionValue(value, throughValue, null, null, LocationKind.UNKNOWN);
        }
        int line = intervalCtx.getStart().getLine();
        if (ctx.programLocationKind == LocationKind.EXACT) {
            return new CanonicalConditionValue(value, throughValue, ctx.sourceFileForExact, line, LocationKind.EXACT);
        }
        return new CanonicalConditionValue(value, throughValue, null, line, LocationKind.PREPROCESSED_STREAM);
    }

    /**
     * Delega en {@link ValueReferences#canonicalLiteralText(Object)} --
     * nunca reimplementa la conversion aqui -- para que un VALUE de
     * constante figurativa (p. ej. {@code 88 X VALUE SPACE}) reciba la
     * misma normalizacion canonica que el sending-area de un MOVE/SET, en
     * lugar de repetir el patron {@code String.valueOf(stmt.getValue())}
     * que filtraba {@code FigurativeConstantImpl@<hash-de-identidad>}.
     */
    private static String literalTextOf(ValueStmt stmt) {
        if (stmt == null) {
            return null;
        }
        return ValueReferences.canonicalLiteralText(stmt.getValue());
    }

    private static String dataItemName(DataDescriptionEntry entry) {
        String name = entry.getName();
        if (name != null && !name.isBlank()) {
            return name;
        }
        if (entry instanceof DataDescriptionEntryGroup group && group.getFillerNumber() != null) {
            return "FILLER-" + group.getFillerNumber();
        }
        return "FILLER";
    }

    private static String qualifiedNameOf(DataDescriptionEntry entry) {
        List<String> parts = new ArrayList<>();
        DataDescriptionEntry current = entry;
        while (current != null) {
            parts.add(0, dataItemName(current));
            current = current.getParentDataDescriptionEntryGroup();
        }
        return String.join(".", parts);
    }

    private static String pictureOf(DataDescriptionEntry entry) {
        if (entry instanceof DataDescriptionEntryGroup group && group.getPictureClause() != null) {
            return group.getPictureClause().getPictureString();
        }
        return null;
    }

    private static String usageOf(DataDescriptionEntry entry) {
        if (entry instanceof DataDescriptionEntryGroup group && group.getUsageClause() != null) {
            return group.getUsageClause().getUsageClauseType().name();
        }
        return null;
    }

    // --- Procedure Division ---------------------------------------------

    /**
     * Nombres de condicion 88 resolubles de forma inequivoca por nombre
     * simple: si el mismo nombre aparece bajo mas de un padre (homonimos
     * entre grupos/copybooks distintos), se excluye por completo de este
     * conjunto -- ninguna referencia a ese nombre se resuelve, en vez de
     * adivinar a cual de los padres pertenece (V1 no resuelve
     * calificacion IN/OF; ver docs/LEVEL_88_SUPPORT.md).
     */
    private static Set<String> resolvableConditionNames(List<CanonicalConditionName> conditionNames) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (CanonicalConditionName condition : conditionNames) {
            counts.merge(condition.name(), 1, Integer::sum);
        }
        Set<String> resolvable = new LinkedHashSet<>();
        for (var entry : counts.entrySet()) {
            if (entry.getValue() == 1) {
                resolvable.add(entry.getKey());
            }
        }
        return resolvable;
    }

    private List<CanonicalParagraph> extractParagraphs(
            ProgramUnit programUnit, ExtractionContext ctx, List<CanonicalConditionName> conditionNames) {
        List<CanonicalParagraph> result = new ArrayList<>();
        ProcedureDivision procedureDivision = programUnit.getProcedureDivision();
        if (procedureDivision == null) {
            return result;
        }
        List<Paragraph> paragraphs = new ArrayList<>(procedureDivision.getParagraphs());
        paragraphs.sort(Comparator.comparingInt(CanonicalProgramExtractor::startLineOf));

        Set<String> knownConditionNames = resolvableConditionNames(conditionNames);
        for (Paragraph paragraph : paragraphs) {
            result.add(convertParagraph(paragraph, ctx, knownConditionNames));
        }
        return result;
    }

    private static int startLineOf(Paragraph paragraph) {
        ParserRuleContext pctx = paragraph.getCtx();
        return pctx != null && pctx.getStart() != null ? pctx.getStart().getLine() : Integer.MAX_VALUE;
    }

    private CanonicalParagraph convertParagraph(
            Paragraph paragraph, ExtractionContext ctx, Set<String> knownConditionNames) {
        String name = paragraph.getParagraphName() != null
                ? paragraph.getParagraphName().getName()
                : "UNKNOWN-PARAGRAPH";

        ParserRuleContext pctx = paragraph.getCtx();
        String sourceFile;
        Integer lineStart;
        Integer lineEnd;
        LocationKind locationKind;
        String sourceText;
        if (pctx == null || pctx.getStart() == null) {
            sourceFile = null;
            lineStart = null;
            lineEnd = null;
            locationKind = LocationKind.UNKNOWN;
            sourceText = "";
        } else {
            int start = pctx.getStart().getLine();
            int stop = pctx.getStop() != null ? pctx.getStop().getLine() : start;
            sourceText = ctx.sliceSourceText(start, stop);
            if (ctx.programLocationKind == LocationKind.EXACT) {
                sourceFile = ctx.sourceFileForExact;
                lineStart = start;
                lineEnd = stop;
                locationKind = LocationKind.EXACT;
            } else {
                sourceFile = null;
                lineStart = start;
                lineEnd = stop;
                locationKind = LocationKind.PREPROCESSED_STREAM;
            }
        }

        List<CanonicalStatement> statements = new StatementExtractor(ctx, name, knownConditionNames)
                .extract(paragraph.getStatements());
        List<String> variablesRead = collectOrderedUnique(statements, CanonicalStatement::variablesRead);
        List<String> variablesWritten = collectOrderedUnique(statements, CanonicalStatement::variablesWritten);
        List<CanonicalSqlAccess> sqlAccess = collectSqlAccess(statements);

        return new CanonicalParagraph(
                name, sourceText, sourceFile, lineStart, lineEnd, locationKind, statements, variablesRead,
                variablesWritten, sqlAccess);
    }

    private static List<String> collectOrderedUnique(
            List<CanonicalStatement> statements, Function<CanonicalStatement, List<String>> extractor) {
        LinkedHashSet<String> seen = new LinkedHashSet<>();
        for (CanonicalStatement statement : statements) {
            seen.addAll(extractor.apply(statement));
        }
        return new ArrayList<>(seen);
    }

    private static List<CanonicalSqlAccess> collectSqlAccess(List<CanonicalStatement> statements) {
        List<CanonicalSqlAccess> result = new ArrayList<>();
        for (CanonicalStatement statement : statements) {
            result.addAll(statement.sqlAccess());
        }
        return result;
    }
}
