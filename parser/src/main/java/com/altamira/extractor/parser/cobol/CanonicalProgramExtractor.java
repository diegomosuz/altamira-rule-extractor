package com.altamira.extractor.parser.cobol;

import com.altamira.extractor.parser.model.CanonicalDataItem;
import com.altamira.extractor.parser.model.CanonicalParagraph;
import com.altamira.extractor.parser.model.CanonicalProgram;
import com.altamira.extractor.parser.model.CanonicalSqlAccess;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.LocationKind;
import io.proleap.cobol.asg.metamodel.ProgramUnit;
import io.proleap.cobol.asg.metamodel.data.DataDivision;
import io.proleap.cobol.asg.metamodel.data.datadescription.DataDescriptionEntry;
import io.proleap.cobol.asg.metamodel.data.datadescription.DataDescriptionEntryGroup;
import io.proleap.cobol.asg.metamodel.data.workingstorage.WorkingStorageSection;
import io.proleap.cobol.asg.metamodel.procedure.Paragraph;
import io.proleap.cobol.asg.metamodel.procedure.ProcedureDivision;
import java.io.IOException;
import java.nio.charset.Charset;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashSet;
import java.util.List;
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
        List<CanonicalParagraph> paragraphs = extractParagraphs(programUnit, ctx);

        return new CanonicalProgram(
                "1.0",
                programName,
                sourceFileIdentity,
                sourceHash,
                sourcePackageHash,
                parseResult.resolvedFormat(),
                encoding.name(),
                dataItems,
                paragraphs,
                List.copyOf(ctx.warnings),
                List.copyOf(ctx.unsupportedConstructs));
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

    private List<CanonicalParagraph> extractParagraphs(ProgramUnit programUnit, ExtractionContext ctx) {
        List<CanonicalParagraph> result = new ArrayList<>();
        ProcedureDivision procedureDivision = programUnit.getProcedureDivision();
        if (procedureDivision == null) {
            return result;
        }
        List<Paragraph> paragraphs = new ArrayList<>(procedureDivision.getParagraphs());
        paragraphs.sort(Comparator.comparingInt(CanonicalProgramExtractor::startLineOf));

        for (Paragraph paragraph : paragraphs) {
            result.add(convertParagraph(paragraph, ctx));
        }
        return result;
    }

    private static int startLineOf(Paragraph paragraph) {
        ParserRuleContext pctx = paragraph.getCtx();
        return pctx != null && pctx.getStart() != null ? pctx.getStart().getLine() : Integer.MAX_VALUE;
    }

    private CanonicalParagraph convertParagraph(Paragraph paragraph, ExtractionContext ctx) {
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

        List<CanonicalStatement> statements = new StatementExtractor(ctx, name).extract(paragraph.getStatements());
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
