package com.altamira.extractor.parser.cobol;

import com.altamira.extractor.parser.model.BranchKind;
import com.altamira.extractor.parser.model.CanonicalSqlAccess;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.LocationKind;
import com.altamira.extractor.parser.model.StatementKind;
import com.altamira.extractor.parser.sql.EmbeddedSqlExtractor;
import io.proleap.cobol.asg.metamodel.call.Call;
import io.proleap.cobol.asg.metamodel.procedure.Statement;
import io.proleap.cobol.asg.metamodel.procedure.compute.ComputeStatement;
import io.proleap.cobol.asg.metamodel.procedure.compute.Store;
import io.proleap.cobol.asg.metamodel.procedure.evaluate.EvaluateStatement;
import io.proleap.cobol.asg.metamodel.procedure.evaluate.When;
import io.proleap.cobol.asg.metamodel.procedure.evaluate.WhenPhrase;
import io.proleap.cobol.asg.metamodel.procedure.execsql.ExecSqlStatement;
import io.proleap.cobol.asg.metamodel.procedure.gotostmt.GoToStatement;
import io.proleap.cobol.asg.metamodel.procedure.ifstmt.IfStatement;
import io.proleap.cobol.asg.metamodel.procedure.move.MoveStatement;
import io.proleap.cobol.asg.metamodel.procedure.move.MoveToStatement;
import io.proleap.cobol.asg.metamodel.procedure.perform.PerformStatement;
import io.proleap.cobol.asg.metamodel.procedure.set.SetStatement;
import io.proleap.cobol.asg.metamodel.procedure.set.SetTo;
import io.proleap.cobol.asg.metamodel.procedure.set.To;
import io.proleap.cobol.asg.metamodel.procedure.set.Value;
import io.proleap.cobol.asg.metamodel.valuestmt.ValueStmt;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import org.antlr.v4.runtime.ParserRuleContext;

/**
 * Camina la lista plana de statements de un Paragraph (Scope#getStatements)
 * y produce una lista plana de CanonicalStatement. IF/EVALUATE anidados y
 * sus ramas (THEN/ELSE/WHEN/WHEN_OTHER) se aplanan como statements
 * hermanos adicionales con parentStatementId/branchKind — nunca un arbol
 * JSON recursivo.
 */
final class StatementExtractor {

    private final ExtractionContext ctx;
    private final String paragraphName;
    private final Set<String> knownConditionNames;
    private final List<CanonicalStatement> collected = new ArrayList<>();
    private final EmbeddedSqlExtractor sqlExtractor = new EmbeddedSqlExtractor();

    StatementExtractor(ExtractionContext ctx, String paragraphName, Set<String> knownConditionNames) {
        this.ctx = ctx;
        this.paragraphName = paragraphName;
        this.knownConditionNames = knownConditionNames;
    }

    List<CanonicalStatement> extract(List<Statement> topLevelStatements) {
        walk(topLevelStatements, null, null);
        return collected;
    }

    private void walk(List<Statement> statements, String parentId, BranchKind branchKind) {
        for (Statement statement : statements) {
            convertOne(statement, parentId, branchKind);
        }
    }

    private void convertOne(Statement statement, String parentId, BranchKind branchKind) {
        if (statement instanceof IfStatement ifStmt) {
            convertIf(ifStmt, parentId, branchKind);
        } else if (statement instanceof EvaluateStatement evaluateStmt) {
            convertEvaluate(evaluateStmt, parentId, branchKind);
        } else if (statement instanceof MoveStatement moveStmt) {
            convertMove(moveStmt, parentId, branchKind);
        } else if (statement instanceof SetStatement setStmt) {
            convertSet(setStmt, parentId, branchKind);
        } else if (statement instanceof ComputeStatement computeStmt) {
            convertCompute(computeStmt, parentId, branchKind);
        } else if (statement instanceof GoToStatement gotoStmt) {
            convertGoTo(gotoStmt, parentId, branchKind);
        } else if (statement instanceof PerformStatement performStmt) {
            convertPerform(performStmt, parentId, branchKind);
        } else if (statement instanceof ExecSqlStatement sqlStmt) {
            convertExecSql(sqlStmt, parentId, branchKind);
        } else {
            convertOther(statement, parentId, branchKind);
        }
    }

    private void convertIf(IfStatement ifStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(ifStmt.getCtx());
        List<String> operands = ifStmt.getCondition() != null
                ? ValueReferences.collectVariableNames(ifStmt.getCondition())
                : List.of();
        String expression = ifStmt.getCondition() != null && ifStmt.getCondition().getCtx() != null
                ? ifStmt.getCondition().getCtx().getText()
                : null;
        List<String> referencedConditionNames = ifStmt.getCondition() != null
                ? ConditionNameReferences.fromCondition(ifStmt.getCondition(), knownConditionNames)
                : List.of();
        String id = nextId(StatementKind.IF);
        collected.add(new CanonicalStatement(
                id, StatementKind.IF, loc.sourceText(), loc.sourceFile(), loc.lineStart(), loc.lineEnd(),
                loc.kind(), parentId, branchKind, null, expression, normalizeExpression(expression), operands,
                operands, List.of(), List.of(), null, List.of(), List.of(), null, null, referencedConditionNames));

        if (ifStmt.getThen() != null) {
            walk(ifStmt.getThen().getStatements(), id, BranchKind.THEN);
        }
        if (ifStmt.getElse() != null) {
            walk(ifStmt.getElse().getStatements(), id, BranchKind.ELSE);
        }
    }

    private void convertEvaluate(EvaluateStatement evaluateStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(evaluateStmt.getCtx());
        List<String> operands = evaluateStmt.getSelect() != null
                ? ValueReferences.collectVariableNames(evaluateStmt.getSelect().getSelectValueStmt())
                : List.of();
        // expression = texto crudo del sujeto EVALUATEd (mismo Select ya
        // usado para operands arriba); antes quedaba siempre null aunque
        // el dato estructural ya estaba disponible.
        String expression = evaluateStmt.getSelect() != null && evaluateStmt.getSelect().getCtx() != null
                ? evaluateStmt.getSelect().getCtx().getText()
                : null;
        String id = nextId(StatementKind.EVALUATE);
        collected.add(new CanonicalStatement(
                id, StatementKind.EVALUATE, loc.sourceText(), loc.sourceFile(), loc.lineStart(),
                loc.lineEnd(), loc.kind(), parentId, branchKind, null, expression,
                normalizeExpression(expression), operands, operands, List.of(), List.of(), null, List.of(),
                List.of(), null, null, List.of()));

        for (WhenPhrase whenPhrase : evaluateStmt.getWhenPhrases()) {
            String condition = whenPhrase.getCtx() != null ? whenPhrase.getCtx().getText() : null;
            List<String> referencedConditionNames = evaluateWhenPhraseConditionNames(whenPhrase);
            walkBranchWithCondition(whenPhrase.getStatements(), id, BranchKind.WHEN, condition, referencedConditionNames);
        }
        if (evaluateStmt.getWhenOther() != null) {
            walk(evaluateStmt.getWhenOther().getStatements(), id, BranchKind.WHEN_OTHER);
        }
    }

    /**
     * Referencias a condition-names 88 verificadas en TODAS las
     * alternativas {@code WHEN a WHEN b ...} de una misma WhenPhrase
     * (semantica OR: cualquiera satisface la rama). Verificado
     * empiricamente: {@code When.getCondition().getConditionValueStmt()}
     * es null para una referencia simple a un identificador -- el nombre
     * aparece en cambio via {@code getValue().getValueStmt()} como un
     * CallValueStmt generico (ProLeap no distingue aqui un condition-name
     * de cualquier otro dato); solo se confirma cruzando contra
     * knownConditionNames.
     */
    private List<String> evaluateWhenPhraseConditionNames(WhenPhrase whenPhrase) {
        Set<String> names = new LinkedHashSet<>();
        for (When when : whenPhrase.getWhens()) {
            if (when.getCondition() == null) {
                continue;
            }
            var condition = when.getCondition();
            if (condition.getConditionValueStmt() instanceof io.proleap.cobol.asg.metamodel.valuestmt.ConditionValueStmt cvs) {
                names.addAll(ConditionNameReferences.fromCondition(cvs, knownConditionNames));
            }
            if (condition.getValue() != null && condition.getValue().getValueStmt() != null) {
                names.addAll(ConditionNameReferences.fromEvaluateValueStmt(
                        condition.getValue().getValueStmt(), knownConditionNames));
            }
        }
        return new ArrayList<>(names);
    }

    /**
     * Como walk(), pero el primer statement de la rama lleva branch_condition
     * y las referencias a condition-names de la clausula WHEN (el resto de
     * la rama, si hay mas de un statement, no repite ninguna de las dos:
     * ya quedaron asociadas al primero).
     */
    private void walkBranchWithCondition(
            List<Statement> statements, String parentId, BranchKind branchKind, String condition,
            List<String> referencedConditionNames) {
        if (statements.isEmpty()) {
            return;
        }
        int sizeBefore = collected.size();
        convertOne(statements.get(0), parentId, branchKind);
        if (collected.size() > sizeBefore) {
            int lastIndex = collected.size() - 1;
            collected.set(
                    lastIndex,
                    withBranchCondition(collected.get(lastIndex), condition, referencedConditionNames));
        }
        for (int i = 1; i < statements.size(); i++) {
            convertOne(statements.get(i), parentId, branchKind);
        }
    }

    private static CanonicalStatement withBranchCondition(
            CanonicalStatement statement, String condition, List<String> whenReferencedConditionNames) {
        Set<String> mergedReferences = new LinkedHashSet<>(statement.referencedConditionNames());
        mergedReferences.addAll(whenReferencedConditionNames);
        List<String> merged = new ArrayList<>(mergedReferences);
        merged.sort(null);
        return new CanonicalStatement(
                statement.statementId(), statement.kind(), statement.sourceText(), statement.sourceFile(),
                statement.lineStart(), statement.lineEnd(), statement.locationKind(),
                statement.parentStatementId(), statement.branchKind(), condition, statement.expression(),
                statement.normalizedExpression(), statement.operands(), statement.variablesRead(),
                statement.variablesWritten(), statement.targetDataItems(), statement.assignedLiteral(),
                statement.targetParagraphs(), statement.sqlAccess(), statement.conditionNameTarget(),
                statement.conditionSetValue(), merged);
    }

    private void convertMove(MoveStatement moveStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(moveStmt.getCtx());
        List<String> targets = new ArrayList<>();
        List<String> read = new ArrayList<>();
        String literal = null;

        MoveToStatement moveTo = moveStmt.getMoveToStatement();
        if (moveTo != null) {
            for (Call call : moveTo.getReceivingAreaCalls()) {
                if (call.getName() != null) {
                    targets.add(call.getName());
                }
            }
            if (moveTo.getSendingArea() != null) {
                ValueStmt sending = moveTo.getSendingArea().getSendingAreaValueStmt();
                read.addAll(ValueReferences.collectVariableNames(sending));
                literal = ValueReferences.literalTextIfPure(sending);
            }
        } else {
            ctx.unsupported(
                    "MOVE CORRESPONDING en paragraph " + paragraphName
                            + " no decodificado estructuralmente (kind=MOVE, source_text conservado)");
        }

        String id = nextId(StatementKind.MOVE);
        collected.add(new CanonicalStatement(
                id, StatementKind.MOVE, loc.sourceText(), loc.sourceFile(), loc.lineStart(), loc.lineEnd(),
                loc.kind(), parentId, branchKind, null, null, null, List.of(), read, List.copyOf(targets),
                List.copyOf(targets), literal, List.of(), List.of(), null, null, List.of()));
    }

    private void convertSet(SetStatement setStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(setStmt.getCtx());
        List<String> targets = new ArrayList<>();
        List<String> read = new ArrayList<>();
        String literal = null;
        String conditionNameTarget = null;
        Boolean conditionSetValue = null;

        for (SetTo setTo : setStmt.getSetTos()) {
            for (var to : setTo.getTos()) {
                if (to.getToCall() != null && to.getToCall().getName() != null) {
                    targets.add(to.getToCall().getName());
                }
            }
            for (var value : setTo.getValues()) {
                ValueStmt valueStmt = value.getValueStmt();
                read.addAll(ValueReferences.collectVariableNames(valueStmt));
                String pureLiteral = ValueReferences.literalTextIfPure(valueStmt);
                if (pureLiteral != null) {
                    literal = pureLiteral;
                }
            }
            Boolean booleanValue = singleSetToBooleanValue(setTo);
            String singleTarget = singleSetToTarget(setTo);
            if (booleanValue != null && singleTarget != null && knownConditionNames.contains(singleTarget)) {
                conditionNameTarget = singleTarget;
                conditionSetValue = booleanValue;
            }
        }
        if (setStmt.getSetBy() != null) {
            ctx.unsupported(
                    "SET ... UP/DOWN BY en paragraph " + paragraphName
                            + " no decodificado estructuralmente (kind=SET, source_text conservado)");
        }

        String id = nextId(StatementKind.SET);
        collected.add(new CanonicalStatement(
                id, StatementKind.SET, loc.sourceText(), loc.sourceFile(), loc.lineStart(), loc.lineEnd(),
                loc.kind(), parentId, branchKind, null, null, null, List.of(), read, List.copyOf(targets),
                List.copyOf(targets), literal, List.of(), List.of(), conditionNameTarget, conditionSetValue,
                List.of()));
    }

    /**
     * {@code SET condition-name TO TRUE|FALSE}: verificado empiricamente
     * que ProLeap representa TRUE/FALSE como un LiteralValueStmt generico
     * cuyo texto crudo es exactamente "TRUE"/"true"/"FALSE"/"false" (segun
     * mayusculas de origen) -- no existe un ValueType ni un ValueStmt
     * booleano dedicado alcanzable desde esta forma gramatical. Solo se
     * reconoce esta forma cuando hay exactamente un TO y un VALUE (nunca
     * para {@code SET a, b TO TRUE}: se mantiene el comportamiento SET_VALUE
     * ordinario para esos casos, sin inventar una asociacion unica).
     */
    private static Boolean singleSetToBooleanValue(SetTo setTo) {
        List<Value> values = setTo.getValues();
        if (values.size() != 1) {
            return null;
        }
        ValueStmt valueStmt = values.get(0).getValueStmt();
        if (valueStmt == null || valueStmt.getCtx() == null
                || !ValueReferences.collectVariableNames(valueStmt).isEmpty()) {
            return null;
        }
        String text = valueStmt.getCtx().getText();
        if ("TRUE".equalsIgnoreCase(text)) {
            return Boolean.TRUE;
        }
        if ("FALSE".equalsIgnoreCase(text)) {
            return Boolean.FALSE;
        }
        return null;
    }

    private static String singleSetToTarget(SetTo setTo) {
        List<To> tos = setTo.getTos();
        if (tos.size() != 1 || tos.get(0).getToCall() == null) {
            return null;
        }
        return tos.get(0).getToCall().getName();
    }

    private void convertCompute(ComputeStatement computeStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(computeStmt.getCtx());
        List<String> targets = new ArrayList<>();
        for (Store store : computeStmt.getStores()) {
            if (store.getStoreCall() != null && store.getStoreCall().getName() != null) {
                targets.add(store.getStoreCall().getName());
            }
        }
        String expression = computeStmt.getArithmeticExpression() != null
                && computeStmt.getArithmeticExpression().getCtx() != null
                ? computeStmt.getArithmeticExpression().getCtx().getText()
                : null;
        List<String> read = computeStmt.getArithmeticExpression() != null
                ? ValueReferences.collectVariableNames(computeStmt.getArithmeticExpression())
                : List.of();

        String id = nextId(StatementKind.COMPUTE);
        collected.add(new CanonicalStatement(
                id, StatementKind.COMPUTE, loc.sourceText(), loc.sourceFile(), loc.lineStart(), loc.lineEnd(),
                loc.kind(), parentId, branchKind, null, expression, normalizeExpression(expression), List.of(),
                read, List.copyOf(targets), List.copyOf(targets), null, List.of(), List.of(), null, null,
                List.of()));
    }

    private void convertGoTo(GoToStatement gotoStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(gotoStmt.getCtx());
        List<String> targets = new ArrayList<>();
        List<String> read = new ArrayList<>();

        if (gotoStmt.getSimple() != null && gotoStmt.getSimple().getProcedureCall() != null) {
            String name = gotoStmt.getSimple().getProcedureCall().getName();
            if (name != null) {
                targets.add(name);
            }
        } else if (gotoStmt.getDependingOnPhrase() != null) {
            var dependingOn = gotoStmt.getDependingOnPhrase();
            for (Call call : dependingOn.getProcedureCalls()) {
                if (call.getName() != null) {
                    targets.add(call.getName());
                }
            }
            if (dependingOn.getDependingOnCall() != null && dependingOn.getDependingOnCall().getName() != null) {
                read.add(dependingOn.getDependingOnCall().getName());
            }
        } else {
            ctx.unsupported(
                    "GO TO en paragraph " + paragraphName
                            + " sin target identificable de forma segura (kind=GO_TO, source_text conservado)");
        }

        String id = nextId(StatementKind.GO_TO);
        collected.add(new CanonicalStatement(
                id, StatementKind.GO_TO, loc.sourceText(), loc.sourceFile(), loc.lineStart(), loc.lineEnd(),
                loc.kind(), parentId, branchKind, null, null, null, List.of(), read, List.of(), List.of(),
                null, List.copyOf(targets), List.of(), null, null, List.of()));
    }

    private void convertPerform(PerformStatement performStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(performStmt.getCtx());
        List<String> targets = new ArrayList<>();

        if (performStmt.getPerformProcedureStatement() != null) {
            for (var call : performStmt.getPerformProcedureStatement().getCalls()) {
                if (call.getName() != null) {
                    targets.add(call.getName());
                }
            }
        }

        String id = nextId(StatementKind.PERFORM);
        collected.add(new CanonicalStatement(
                id, StatementKind.PERFORM, loc.sourceText(), loc.sourceFile(), loc.lineStart(), loc.lineEnd(),
                loc.kind(), parentId, branchKind, null, null, null, List.of(), List.of(), List.of(),
                List.of(), null, List.copyOf(targets), List.of(), null, null, List.of()));

        if (performStmt.getPerformInlineStatement() != null) {
            walk(performStmt.getPerformInlineStatement().getStatements(), id, null);
        }
    }

    private void convertExecSql(ExecSqlStatement sqlStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(sqlStmt.getCtx());
        String rawSql = sqlStmt.getExecSqlText() != null ? sqlStmt.getExecSqlText() : loc.sourceText();

        EmbeddedSqlExtractor.Result result = sqlExtractor.extract(rawSql);
        List<CanonicalSqlAccess> sqlAccess = new ArrayList<>();
        for (EmbeddedSqlExtractor.Access access : result.accesses()) {
            sqlAccess.add(new CanonicalSqlAccess(
                    access.table(), access.operation(), access.predicateText(), access.hostVariables(),
                    loc.sourceFile(), loc.lineStart(), loc.lineEnd(), loc.kind()));
        }
        if (!result.interpreted()) {
            ctx.unsupported(
                    "EXEC SQL en paragraph " + paragraphName
                            + " no pudo interpretarse de forma segura (kind=EXEC_SQL, source_text conservado): "
                            + result.reason());
        }

        String id = nextId(StatementKind.EXEC_SQL);
        collected.add(new CanonicalStatement(
                id, StatementKind.EXEC_SQL, loc.sourceText(), loc.sourceFile(), loc.lineStart(), loc.lineEnd(),
                loc.kind(), parentId, branchKind, null, null, null, List.of(), List.of(), List.of(),
                List.of(), null, List.of(), List.copyOf(sqlAccess), null, null, List.of()));
    }

    private void convertOther(Statement statement, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(statement.getCtx());
        ctx.unsupported(
                statement.getClass().getSimpleName() + " en paragraph " + paragraphName
                        + " no decodificado estructuralmente (kind=OTHER, source_text conservado)");

        String id = nextId(StatementKind.OTHER);
        collected.add(new CanonicalStatement(
                id, StatementKind.OTHER, loc.sourceText(), loc.sourceFile(), loc.lineStart(), loc.lineEnd(),
                loc.kind(), parentId, branchKind, null, null, null, List.of(), List.of(), List.of(), List.of(),
                null, List.of(), List.of(), null, null, List.of()));
    }

    private String nextId(StatementKind kind) {
        return ctx.programName + "::" + paragraphName + "::" + ctx.nextOrdinal() + "::" + kind.name();
    }

    /**
     * V1: normalizedExpression es una representacion deterministica y
     * semanticamente CONSERVADORA de expression -- no una normalizacion
     * COBOL con significado semantico. No existe una especificacion
     * formal de normalizacion en V1, asi que el comportamiento honesto
     * es unicamente recortar whitespace EXTERIOR ({@link String#strip()}):
     * conserva mayusculas/minusculas, literales entre comillas (incluido
     * su whitespace interno, p. ej. {@code 'A   B'}), numeros,
     * operadores e identificadores exactamente como los devolvio el
     * parser. No colapsa whitespace interno, no usa regex para
     * interpretar semantica COBOL, y nunca se deriva desde sourceText
     * (solo desde expression, que ya es el texto estructural del nodo
     * ASG correspondiente).
     */
    static String normalizeExpression(String expression) {
        if (expression == null) {
            return null;
        }
        String trimmed = expression.strip();
        return trimmed.isEmpty() ? null : trimmed;
    }

    private record Location(
            String sourceFile, Integer lineStart, Integer lineEnd, LocationKind kind, String sourceText) {
    }

    private Location resolveLocation(ParserRuleContext parserCtx) {
        if (parserCtx == null || parserCtx.getStart() == null) {
            return new Location(null, null, null, LocationKind.UNKNOWN, "");
        }
        int start = parserCtx.getStart().getLine();
        int stop = parserCtx.getStop() != null ? parserCtx.getStop().getLine() : start;
        String text = ctx.sliceSourceText(start, stop);
        if (ctx.programLocationKind == LocationKind.EXACT) {
            return new Location(ctx.sourceFileForExact, start, stop, LocationKind.EXACT, text);
        }
        return new Location(null, start, stop, LocationKind.PREPROCESSED_STREAM, text);
    }
}
