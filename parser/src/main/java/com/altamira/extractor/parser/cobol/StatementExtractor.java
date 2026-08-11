package com.altamira.extractor.parser.cobol;

import com.altamira.extractor.parser.model.BranchKind;
import com.altamira.extractor.parser.model.CallPassingMode;
import com.altamira.extractor.parser.model.CallTargetKind;
import com.altamira.extractor.parser.model.CanonicalCallArgument;
import com.altamira.extractor.parser.model.CanonicalSqlAccess;
import com.altamira.extractor.parser.model.CanonicalStatement;
import com.altamira.extractor.parser.model.LocationKind;
import com.altamira.extractor.parser.model.ProgramTerminationKind;
import com.altamira.extractor.parser.model.StatementKind;
import com.altamira.extractor.parser.sql.EmbeddedSqlExtractor;
import io.proleap.cobol.Cobol85Parser;
import io.proleap.cobol.asg.metamodel.call.Call;
import io.proleap.cobol.asg.metamodel.call.DataDescriptionEntryCall;
import io.proleap.cobol.asg.metamodel.procedure.Statement;
import io.proleap.cobol.asg.metamodel.procedure.add.AddCorrespondingStatement;
import io.proleap.cobol.asg.metamodel.procedure.add.AddStatement;
import io.proleap.cobol.asg.metamodel.procedure.add.AddToGivingStatement;
import io.proleap.cobol.asg.metamodel.procedure.add.AddToStatement;
import io.proleap.cobol.asg.metamodel.procedure.add.From;
import io.proleap.cobol.asg.metamodel.procedure.add.ToGiving;
import io.proleap.cobol.asg.metamodel.procedure.call.ByContent;
import io.proleap.cobol.asg.metamodel.procedure.call.ByReference;
import io.proleap.cobol.asg.metamodel.procedure.call.ByValue;
import io.proleap.cobol.asg.metamodel.procedure.call.CallStatement;
import io.proleap.cobol.asg.metamodel.procedure.call.UsingParameter;
import io.proleap.cobol.asg.metamodel.procedure.call.UsingPhrase;
import io.proleap.cobol.asg.metamodel.procedure.compute.ComputeStatement;
import io.proleap.cobol.asg.metamodel.procedure.compute.Store;
import io.proleap.cobol.asg.metamodel.procedure.divide.DivideByGivingStatement;
import io.proleap.cobol.asg.metamodel.procedure.divide.DivideIntoGivingStatement;
import io.proleap.cobol.asg.metamodel.procedure.divide.DivideIntoStatement;
import io.proleap.cobol.asg.metamodel.procedure.divide.DivideStatement;
import io.proleap.cobol.asg.metamodel.procedure.divide.Into;
import io.proleap.cobol.asg.metamodel.procedure.evaluate.EvaluateStatement;
import io.proleap.cobol.asg.metamodel.procedure.evaluate.When;
import io.proleap.cobol.asg.metamodel.procedure.evaluate.WhenPhrase;
import io.proleap.cobol.asg.metamodel.procedure.execsql.ExecSqlStatement;
import io.proleap.cobol.asg.metamodel.procedure.exit.ExitStatement;
import io.proleap.cobol.asg.metamodel.procedure.goback.GobackStatement;
import io.proleap.cobol.asg.metamodel.procedure.gotostmt.GoToStatement;
import io.proleap.cobol.asg.metamodel.procedure.ifstmt.IfStatement;
import io.proleap.cobol.asg.metamodel.procedure.move.MoveStatement;
import io.proleap.cobol.asg.metamodel.procedure.move.MoveToStatement;
import io.proleap.cobol.asg.metamodel.procedure.multiply.ByOperand;
import io.proleap.cobol.asg.metamodel.procedure.multiply.ByPhrase;
import io.proleap.cobol.asg.metamodel.procedure.multiply.GivingOperand;
import io.proleap.cobol.asg.metamodel.procedure.multiply.GivingResult;
import io.proleap.cobol.asg.metamodel.procedure.multiply.MultiplyStatement;
import io.proleap.cobol.asg.metamodel.procedure.perform.PerformStatement;
import io.proleap.cobol.asg.metamodel.procedure.set.SetStatement;
import io.proleap.cobol.asg.metamodel.procedure.set.SetTo;
import io.proleap.cobol.asg.metamodel.procedure.set.To;
import io.proleap.cobol.asg.metamodel.procedure.set.Value;
import io.proleap.cobol.asg.metamodel.procedure.stop.StopStatement;
import io.proleap.cobol.asg.metamodel.procedure.subtract.Minuend;
import io.proleap.cobol.asg.metamodel.procedure.subtract.MinuendGiving;
import io.proleap.cobol.asg.metamodel.procedure.subtract.SubtractCorrespondingStatement;
import io.proleap.cobol.asg.metamodel.procedure.subtract.SubtractFromGivingStatement;
import io.proleap.cobol.asg.metamodel.procedure.subtract.SubtractFromStatement;
import io.proleap.cobol.asg.metamodel.procedure.subtract.SubtractStatement;
import io.proleap.cobol.asg.metamodel.procedure.subtract.Subtrahend;
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
        } else if (statement instanceof AddStatement addStmt) {
            convertAdd(addStmt, parentId, branchKind);
        } else if (statement instanceof SubtractStatement subtractStmt) {
            convertSubtract(subtractStmt, parentId, branchKind);
        } else if (statement instanceof MultiplyStatement multiplyStmt) {
            convertMultiply(multiplyStmt, parentId, branchKind);
        } else if (statement instanceof DivideStatement divideStmt) {
            convertDivide(divideStmt, parentId, branchKind);
        } else if (statement instanceof GoToStatement gotoStmt) {
            convertGoTo(gotoStmt, parentId, branchKind);
        } else if (statement instanceof PerformStatement performStmt) {
            convertPerform(performStmt, parentId, branchKind);
        } else if (statement instanceof ExecSqlStatement sqlStmt) {
            convertExecSql(sqlStmt, parentId, branchKind);
        } else if (statement instanceof CallStatement callStmt) {
            convertCall(callStmt, parentId, branchKind);
        } else if (statement instanceof GobackStatement gobackStmt) {
            convertGoback(gobackStmt, parentId, branchKind);
        } else if (statement instanceof StopStatement stopStmt) {
            convertStop(stopStmt, parentId, branchKind);
        } else if (statement instanceof ExitStatement exitStmt) {
            convertExit(exitStmt, parentId, branchKind);
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
        // Constructor completo (nunca uno de los de compatibilidad): el
        // primer statement de una rama WHEN puede ser un CALL (Fase 6,
        // caso obligatorio "llamada dentro de EVALUATE") o un GOBACK/
        // STOP RUN/EXIT PROGRAM (Fase 7b), y los constructores de
        // compatibilidad pondrian esos campos estructurados en
        // null/vacio, perdiendo la extraccion ya hecha.
        return new CanonicalStatement(
                statement.statementId(), statement.kind(), statement.sourceText(), statement.sourceFile(),
                statement.lineStart(), statement.lineEnd(), statement.locationKind(),
                statement.parentStatementId(), statement.branchKind(), condition, statement.expression(),
                statement.normalizedExpression(), statement.operands(), statement.variablesRead(),
                statement.variablesWritten(), statement.targetDataItems(), statement.assignedLiteral(),
                statement.targetParagraphs(), statement.sqlAccess(), statement.conditionNameTarget(),
                statement.conditionSetValue(), merged, statement.callTargetKind(),
                statement.calledProgramName(), statement.calledProgramExpression(), statement.callArguments(),
                statement.callReturningDataItem(), statement.callHasOnException(),
                statement.callHasNotOnException(), statement.programTerminationKind());
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

    /**
     * {@code ADD} (Fase 15B3-C2-B1): a diferencia de {@code COMPUTE}, ninguna
     * de las 4 variantes ({@code TO}/{@code TO...GIVING}/{@code
     * CORRESPONDING}) expone un nodo de expresion unico equivalente a
     * {@code ArithmeticExpression} -- {@code expression}/{@code
     * normalizedExpression} quedan {@code null} por diseno (mismo criterio
     * ya usado por {@code convertMove}: sin nodo de expresion dedicado, no
     * se sintetiza texto reconstruido via {@code getText()}, que ademas
     * pierde todo espacio entre tokens -- verificado empiricamente que
     * {@code getText()} produce p.ej. {@code "WS-SALDO<0"} sin espacios).
     * {@code source_text} (siempre preservado, verbatim) queda como unica
     * evidencia textual. {@code ROUNDED}/{@code ON SIZE ERROR}/{@code NOT
     * ON SIZE ERROR}/{@code CORRESPONDING} se registran via {@code
     * ctx.unsupported()} (camino feliz aritmetico interpretado, esas
     * clausulas no) -- nunca se pierden silenciosamente.
     */
    private void convertAdd(AddStatement addStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(addStmt.getCtx());
        List<String> targets = new ArrayList<>();
        List<String> read = new ArrayList<>();

        if (addStmt.getOnSizeErrorPhrase() != null || addStmt.getNotOnSizeErrorPhrase() != null) {
            ctx.unsupported(
                    "ADD ... ON SIZE ERROR/NOT ON SIZE ERROR en paragraph " + paragraphName
                            + " no interpreta el branch de control (kind=ADD, camino feliz procesado)");
        }

        AddToStatement addTo = addStmt.getAddToStatement();
        AddToGivingStatement addToGiving = addStmt.getAddToGivingStatement();
        AddCorrespondingStatement addCorresponding = addStmt.getAddCorrespondingStatement();

        if (addTo != null) {
            for (From from : addTo.getFroms()) {
                read.addAll(ValueReferences.collectVariableNames(from.getFromValueStmt()));
            }
            for (io.proleap.cobol.asg.metamodel.procedure.add.To to : addTo.getTos()) {
                if (to.getToCall() != null && to.getToCall().getName() != null) {
                    targets.add(to.getToCall().getName());
                }
                if (to.isRounded()) {
                    ctx.unsupported(
                            "ADD ... ROUNDED en paragraph " + paragraphName
                                    + " no ajusta el valor estructuralmente (kind=ADD, presencia "
                                    + "registrada, valor no recalculado)");
                }
            }
        } else if (addToGiving != null) {
            for (From from : addToGiving.getFroms()) {
                read.addAll(ValueReferences.collectVariableNames(from.getFromValueStmt()));
            }
            for (ToGiving to : addToGiving.getTos()) {
                read.addAll(ValueReferences.collectVariableNames(to.getToValueStmt()));
            }
            for (io.proleap.cobol.asg.metamodel.procedure.add.Giving giving : addToGiving.getGivings()) {
                if (giving.getGivingCall() != null && giving.getGivingCall().getName() != null) {
                    targets.add(giving.getGivingCall().getName());
                }
                if (giving.isRounded()) {
                    ctx.unsupported(
                            "ADD ... GIVING ... ROUNDED en paragraph " + paragraphName
                                    + " no ajusta el valor estructuralmente (kind=ADD, presencia "
                                    + "registrada, valor no recalculado)");
                }
            }
        } else if (addCorresponding != null) {
            ctx.unsupported(
                    "ADD CORRESPONDING en paragraph " + paragraphName
                            + " no decodificado estructuralmente (kind=ADD, source_text conservado)");
        } else {
            ctx.unsupported(
                    "AddStatement en paragraph " + paragraphName
                            + " con forma no reconocida estructuralmente (kind=ADD)");
        }

        String id = nextId(StatementKind.ADD);
        collected.add(new CanonicalStatement(
                id, StatementKind.ADD, loc.sourceText(), loc.sourceFile(), loc.lineStart(), loc.lineEnd(),
                loc.kind(), parentId, branchKind, null, null, null, List.of(), read, List.copyOf(targets),
                List.copyOf(targets), null, List.of(), List.of(), null, null, List.of()));
    }

    /** {@code SUBTRACT} (Fase 15B3-C2-B1): mismo criterio que {@link #convertAdd}. */
    private void convertSubtract(SubtractStatement subtractStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(subtractStmt.getCtx());
        List<String> targets = new ArrayList<>();
        List<String> read = new ArrayList<>();

        if (subtractStmt.getOnSizeErrorPhrase() != null || subtractStmt.getNotOnSizeErrorPhrase() != null) {
            ctx.unsupported(
                    "SUBTRACT ... ON SIZE ERROR/NOT ON SIZE ERROR en paragraph " + paragraphName
                            + " no interpreta el branch de control (kind=SUBTRACT, camino feliz procesado)");
        }

        SubtractFromStatement subtractFrom = subtractStmt.getSubtractFromStatement();
        SubtractFromGivingStatement subtractFromGiving = subtractStmt.getSubtractFromGivingStatement();
        SubtractCorrespondingStatement subtractCorresponding = subtractStmt.getSubtractCorrespondingStatement();

        if (subtractFrom != null) {
            for (Subtrahend subtrahend : subtractFrom.getSubtrahends()) {
                read.addAll(ValueReferences.collectVariableNames(subtrahend.getSubtrahendValueStmt()));
            }
            for (Minuend minuend : subtractFrom.getMinuends()) {
                if (minuend.getMinuendCall() != null && minuend.getMinuendCall().getName() != null) {
                    targets.add(minuend.getMinuendCall().getName());
                }
                if (minuend.isRounded()) {
                    ctx.unsupported(
                            "SUBTRACT ... ROUNDED en paragraph " + paragraphName
                                    + " no ajusta el valor estructuralmente (kind=SUBTRACT, presencia "
                                    + "registrada, valor no recalculado)");
                }
            }
        } else if (subtractFromGiving != null) {
            for (Subtrahend subtrahend : subtractFromGiving.getSubtrahends()) {
                read.addAll(ValueReferences.collectVariableNames(subtrahend.getSubtrahendValueStmt()));
            }
            MinuendGiving minuendGiving = subtractFromGiving.getMinuend();
            if (minuendGiving != null && minuendGiving.getMinuendValueStmt() != null) {
                read.addAll(ValueReferences.collectVariableNames(minuendGiving.getMinuendValueStmt()));
            }
            for (io.proleap.cobol.asg.metamodel.procedure.subtract.Giving giving :
                    subtractFromGiving.getGivings()) {
                if (giving.getGivingCall() != null && giving.getGivingCall().getName() != null) {
                    targets.add(giving.getGivingCall().getName());
                }
                if (giving.isRounded()) {
                    ctx.unsupported(
                            "SUBTRACT ... GIVING ... ROUNDED en paragraph " + paragraphName
                                    + " no ajusta el valor estructuralmente (kind=SUBTRACT, presencia "
                                    + "registrada, valor no recalculado)");
                }
            }
        } else if (subtractCorresponding != null) {
            ctx.unsupported(
                    "SUBTRACT CORRESPONDING en paragraph " + paragraphName
                            + " no decodificado estructuralmente (kind=SUBTRACT, source_text conservado)");
        } else {
            ctx.unsupported(
                    "SubtractStatement en paragraph " + paragraphName
                            + " con forma no reconocida estructuralmente (kind=SUBTRACT)");
        }

        String id = nextId(StatementKind.SUBTRACT);
        collected.add(new CanonicalStatement(
                id, StatementKind.SUBTRACT, loc.sourceText(), loc.sourceFile(), loc.lineStart(), loc.lineEnd(),
                loc.kind(), parentId, branchKind, null, null, null, List.of(), read, List.copyOf(targets),
                List.copyOf(targets), null, List.of(), List.of(), null, null, List.of()));
    }

    /**
     * {@code MULTIPLY} (Fase 15B3-C2-B1): sin variante {@code
     * CORRESPONDING} (no existe en la gramatica COBOL estandar para este
     * verbo, confirmado por ausencia de esa clase en el ASG de ProLeap).
     */
    private void convertMultiply(MultiplyStatement multiplyStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(multiplyStmt.getCtx());
        List<String> targets = new ArrayList<>();
        List<String> read = new ArrayList<>();

        if (multiplyStmt.getOnSizeErrorPhrase() != null || multiplyStmt.getNotOnSizeErrorPhrase() != null) {
            ctx.unsupported(
                    "MULTIPLY ... ON SIZE ERROR/NOT ON SIZE ERROR en paragraph " + paragraphName
                            + " no interpreta el branch de control (kind=MULTIPLY, camino feliz procesado)");
        }

        if (multiplyStmt.getOperandValueStmt() != null) {
            read.addAll(ValueReferences.collectVariableNames(multiplyStmt.getOperandValueStmt()));
        }

        ByPhrase byPhrase = multiplyStmt.getByPhrase();
        io.proleap.cobol.asg.metamodel.procedure.multiply.GivingPhrase givingPhrase =
                multiplyStmt.getGivingPhrase();

        if (byPhrase != null) {
            for (ByOperand operand : byPhrase.getByOperands()) {
                if (operand.getOperandCall() != null && operand.getOperandCall().getName() != null) {
                    targets.add(operand.getOperandCall().getName());
                }
                if (operand.isRounded()) {
                    ctx.unsupported(
                            "MULTIPLY ... ROUNDED en paragraph " + paragraphName
                                    + " no ajusta el valor estructuralmente (kind=MULTIPLY, presencia "
                                    + "registrada, valor no recalculado)");
                }
            }
        } else if (givingPhrase != null) {
            GivingOperand givingOperand = givingPhrase.getGivingOperand();
            if (givingOperand != null && givingOperand.getOperandValueStmt() != null) {
                read.addAll(ValueReferences.collectVariableNames(givingOperand.getOperandValueStmt()));
            }
            for (GivingResult result : givingPhrase.getGivingResults()) {
                if (result.getResultCall() != null && result.getResultCall().getName() != null) {
                    targets.add(result.getResultCall().getName());
                }
                if (result.isRounded()) {
                    ctx.unsupported(
                            "MULTIPLY ... GIVING ... ROUNDED en paragraph " + paragraphName
                                    + " no ajusta el valor estructuralmente (kind=MULTIPLY, presencia "
                                    + "registrada, valor no recalculado)");
                }
            }
        } else {
            ctx.unsupported(
                    "MultiplyStatement en paragraph " + paragraphName
                            + " con forma no reconocida estructuralmente (kind=MULTIPLY)");
        }

        String id = nextId(StatementKind.MULTIPLY);
        collected.add(new CanonicalStatement(
                id, StatementKind.MULTIPLY, loc.sourceText(), loc.sourceFile(), loc.lineStart(), loc.lineEnd(),
                loc.kind(), parentId, branchKind, null, null, null, List.of(), read, List.copyOf(targets),
                List.copyOf(targets), null, List.of(), List.of(), null, null, List.of()));
    }

    /**
     * {@code DIVIDE} (Fase 15B3-C2-B1): sin variante {@code
     * CORRESPONDING} (misma razon que {@link #convertMultiply}). {@code
     * REMAINDER} (segundo target real, {@code Remainder.getRemainderCall()})
     * deliberadamente NO se agrega a {@code target_data_items}: hacerlo
     * produciria {@code writes=[cociente,resto]} sin forma de distinguir
     * cual es cual sin un campo nuevo (rechazado en 15B3-C2-A2, opcion B:
     * diagnostico explicito, productizacion diferida) -- se registra via
     * {@code ctx.unsupported()}, nunca se pierde silenciosamente, y el
     * cociente (unico target capturado) sigue siendo una escritura limpia
     * y sin ambiguedad.
     */
    private void convertDivide(DivideStatement divideStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(divideStmt.getCtx());
        List<String> targets = new ArrayList<>();
        List<String> read = new ArrayList<>();

        if (divideStmt.getOnSizeErrorPhrase() != null || divideStmt.getNotOnSizeErrorPhrase() != null) {
            ctx.unsupported(
                    "DIVIDE ... ON SIZE ERROR/NOT ON SIZE ERROR en paragraph " + paragraphName
                            + " no interpreta el branch de control (kind=DIVIDE, camino feliz procesado)");
        }

        if (divideStmt.getDivisorValueStmt() != null) {
            read.addAll(ValueReferences.collectVariableNames(divideStmt.getDivisorValueStmt()));
        }

        DivideIntoStatement divideInto = divideStmt.getDivideIntoStatement();
        DivideIntoGivingStatement divideIntoGiving = divideStmt.getDivideIntoGivingStatement();
        DivideByGivingStatement divideByGiving = divideStmt.getDivideByGivingStatement();

        if (divideInto != null) {
            for (Into into : divideInto.getIntos()) {
                if (into.getGivingCall() != null && into.getGivingCall().getName() != null) {
                    targets.add(into.getGivingCall().getName());
                }
                if (into.isRounded()) {
                    ctx.unsupported(
                            "DIVIDE ... ROUNDED en paragraph " + paragraphName
                                    + " no ajusta el valor estructuralmente (kind=DIVIDE, presencia "
                                    + "registrada, valor no recalculado)");
                }
            }
        } else if (divideIntoGiving != null) {
            if (divideIntoGiving.getIntoValueStmt() != null) {
                read.addAll(ValueReferences.collectVariableNames(divideIntoGiving.getIntoValueStmt()));
            }
            collectDivideGivingPhrase(divideIntoGiving.getGivingPhrase(), targets);
        } else if (divideByGiving != null) {
            if (divideByGiving.getByValueStmt() != null) {
                read.addAll(ValueReferences.collectVariableNames(divideByGiving.getByValueStmt()));
            }
            collectDivideGivingPhrase(divideByGiving.getGivingPhrase(), targets);
        } else {
            ctx.unsupported(
                    "DivideStatement en paragraph " + paragraphName
                            + " con forma no reconocida estructuralmente (kind=DIVIDE)");
        }

        if (divideStmt.getRemainder() != null) {
            ctx.unsupported(
                    "DIVIDE ... REMAINDER en paragraph " + paragraphName
                            + " no se representa como target estructurado (kind=DIVIDE, cociente "
                            + "capturado, resto no capturado en target_data_items para evitar "
                            + "ambiguedad quotient/remainder; ver 15B3-C2-A2 seccion REMAINDER)");
        }

        String id = nextId(StatementKind.DIVIDE);
        collected.add(new CanonicalStatement(
                id, StatementKind.DIVIDE, loc.sourceText(), loc.sourceFile(), loc.lineStart(), loc.lineEnd(),
                loc.kind(), parentId, branchKind, null, null, null, List.of(), read, List.copyOf(targets),
                List.copyOf(targets), null, List.of(), List.of(), null, null, List.of()));
    }

    private void collectDivideGivingPhrase(
            io.proleap.cobol.asg.metamodel.procedure.divide.GivingPhrase givingPhrase, List<String> targets) {
        if (givingPhrase == null) {
            return;
        }
        for (io.proleap.cobol.asg.metamodel.procedure.divide.Giving giving : givingPhrase.getGivings()) {
            if (giving.getGivingCall() != null && giving.getGivingCall().getName() != null) {
                targets.add(giving.getGivingCall().getName());
            }
            if (giving.isRounded()) {
                ctx.unsupported(
                        "DIVIDE ... GIVING ... ROUNDED en paragraph " + paragraphName
                                + " no ajusta el valor estructuralmente (kind=DIVIDE, presencia "
                                + "registrada, valor no recalculado)");
            }
        }
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

    /**
     * {@code CALL} (Fase 6, fundacion interprocedural). {@code
     * variablesWritten}/{@code targetDataItems} se dejan deliberadamente
     * vacios (a diferencia de MOVE/SET/COMPUTE, que representan una
     * escritura CIERTA): un argumento BY REFERENCE o un RETURNING solo
     * describen un efecto POTENCIAL (el subprograma puede o no
     * modificarlo -- ver {@code PotentialDataFlow} del contrato
     * interprocedural y {@code PropagationBarrierReason.CALL_BOUNDARY}),
     * nunca una certeza equivalente a un MOVE. Esto tambien garantiza que
     * CALL nunca alimenta {@code dependency_builder.py}/
     * {@code semantic_graph_builder.py} con una nueva relacion
     * DATA_DEPENDS_ON/LEADS_TO -- ninguno de los dos se modifica en esta
     * fase.
     */
    private void convertCall(CallStatement callStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(callStmt.getCtx());

        ValueStmt programValueStmt = callStmt.getProgramValueStmt();
        CallTargetKind targetKind;
        String calledProgramName = null;
        String calledProgramExpression = null;
        List<String> read = new ArrayList<>();

        String literalTarget = ValueReferences.literalTextIfPure(programValueStmt);
        List<String> targetVariableNames = programValueStmt != null
                ? ValueReferences.collectVariableNames(programValueStmt)
                : List.of();
        if (literalTarget != null) {
            targetKind = CallTargetKind.LITERAL;
            calledProgramName = literalTarget;
        } else if (!targetVariableNames.isEmpty()) {
            targetKind = CallTargetKind.DYNAMIC;
            calledProgramExpression = targetVariableNames.get(0);
            read.addAll(targetVariableNames);
        } else {
            targetKind = CallTargetKind.UNKNOWN;
            ctx.unsupported(
                    "CALL en paragraph " + paragraphName
                            + " con target no identificable de forma estructural (kind=CALL)");
        }

        List<CanonicalCallArgument> arguments = extractCallArguments(callStmt, read);

        String returningItem = null;
        if (callStmt.getGivingPhrase() != null && callStmt.getGivingPhrase().getGivingCall() != null) {
            returningItem = callStmt.getGivingPhrase().getGivingCall().getName();
        }

        Boolean hasOnException = callStmt.getOnExceptionClause() != null ? Boolean.TRUE : null;
        Boolean hasNotOnException = callStmt.getNotOnExceptionClause() != null ? Boolean.TRUE : null;

        String expression = calledProgramName != null ? calledProgramName : calledProgramExpression;

        String id = nextId(StatementKind.CALL);
        collected.add(new CanonicalStatement(
                id, StatementKind.CALL, loc.sourceText(), loc.sourceFile(), loc.lineStart(), loc.lineEnd(),
                loc.kind(), parentId, branchKind, null, expression, normalizeExpression(expression), List.of(),
                List.copyOf(read), List.of(), List.of(), null, List.of(), List.of(), null, null, List.of(),
                targetKind, calledProgramName, calledProgramExpression, arguments, returningItem, hasOnException,
                hasNotOnException, null));
    }

    private List<CanonicalCallArgument> extractCallArguments(CallStatement callStmt, List<String> readAccumulator) {
        List<CanonicalCallArgument> arguments = new ArrayList<>();
        UsingPhrase usingPhrase = callStmt.getUsingPhrasePhrase();
        if (usingPhrase == null) {
            return arguments;
        }
        int ordinal = 1;
        for (UsingParameter parameter : usingPhrase.getUsingParameters()) {
            if (parameter.getByReferencePhrase() != null) {
                for (ByReference byReference : parameter.getByReferencePhrase().getByReferences()) {
                    boolean omitted = byReference.getByReferenceType() == ByReference.ByReferenceType.OMITTED;
                    boolean addressOf = byReference.getByReferenceType() == ByReference.ByReferenceType.ADDRESS_OF;
                    arguments.add(convertCallArgument(
                            ordinal++, CallPassingMode.REFERENCE, byReference.getValueStmt(), omitted, addressOf,
                            readAccumulator));
                }
            } else if (parameter.getByContentPhrase() != null) {
                for (ByContent byContent : parameter.getByContentPhrase().getByContents()) {
                    boolean addressOf = byContent.getByContentType() == ByContent.ByContentType.ADDRESS_OF;
                    arguments.add(convertCallArgument(
                            ordinal++, CallPassingMode.CONTENT, byContent.getValueStmt(), false, addressOf,
                            readAccumulator));
                }
            } else if (parameter.getByValuePhrase() != null) {
                for (ByValue byValue : parameter.getByValuePhrase().getByValues()) {
                    boolean addressOf = byValue.getByValueType() == ByValue.ByValueType.ADDRESS_OF;
                    arguments.add(convertCallArgument(
                            ordinal++, CallPassingMode.VALUE, byValue.getValueStmt(), false, addressOf,
                            readAccumulator));
                }
            } else {
                ctx.unsupported(
                        "CALL en paragraph " + paragraphName
                                + " con argumento USING sin phrase BY REFERENCE/BY CONTENT/BY VALUE "
                                + "reconocible (kind=CALL)");
                Location argLoc = resolveLocation(null);
                arguments.add(new CanonicalCallArgument(
                        ordinal++, "<unsupported>", null, null, null, CallPassingMode.UNKNOWN, false,
                        argLoc.sourceFile(), argLoc.lineStart(), argLoc.kind()));
            }
        }
        return arguments;
    }

    private CanonicalCallArgument convertCallArgument(
            int ordinal, CallPassingMode mode, ValueStmt valueStmt, boolean omitted, boolean addressOf,
            List<String> readAccumulator) {
        Location loc = resolveLocation(valueStmt != null ? valueStmt.getCtx() : null);
        if (omitted) {
            return new CanonicalCallArgument(
                    ordinal, "OMITTED", null, null, null, mode, true, loc.sourceFile(), loc.lineStart(), loc.kind());
        }
        String literal = ValueReferences.literalTextIfPure(valueStmt);
        if (literal != null) {
            String expr = addressOf ? "ADDRESS OF " + literal : literal;
            return new CanonicalCallArgument(
                    ordinal, expr, null, null, literal, mode, false, loc.sourceFile(), loc.lineStart(), loc.kind());
        }
        Call call = valueStmt != null ? ValueReferences.singleCallIfSimple(valueStmt) : null;
        if (call != null && call.getName() != null) {
            String name = call.getName();
            readAccumulator.add(name);
            String qualifiedName = call instanceof DataDescriptionEntryCall ddec
                    ? CanonicalProgramExtractor.qualifiedNameOf(ddec.getDataDescriptionEntry())
                    : null;
            String expr = addressOf ? "ADDRESS OF " + name : name;
            return new CanonicalCallArgument(
                    ordinal, expr, name, qualifiedName, null, mode, false, loc.sourceFile(), loc.lineStart(),
                    loc.kind());
        }
        ctx.unsupported(
                "CALL en paragraph " + paragraphName
                        + " con argumento USING no identificable de forma estructural (kind=CALL)");
        return new CanonicalCallArgument(
                ordinal, "<unsupported>", null, null, null, CallPassingMode.UNKNOWN, false, loc.sourceFile(),
                loc.lineStart(), loc.kind());
    }

    /**
     * {@code GOBACK} (Fase 7b): retorna control al caller de forma
     * incondicional en la gramatica de esta version de ProLeap ({@code
     * GobackStatementContext} no expone ninguna sub-clausula opcional,
     * ver {@code Cobol85Parser.GobackStatementContext}) -- siempre
     * {@code ProgramTerminationKind.GOBACK}, nunca ambiguo.
     */
    private void convertGoback(GobackStatement gobackStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(gobackStmt.getCtx());
        String id = nextId(StatementKind.PROGRAM_TERMINATION);
        collected.add(new CanonicalStatement(
                id, StatementKind.PROGRAM_TERMINATION, loc.sourceText(), loc.sourceFile(), loc.lineStart(),
                loc.lineEnd(), loc.kind(), parentId, branchKind, null, null, null, List.of(), List.of(),
                List.of(), List.of(), null, List.of(), List.of(), null, null, List.of(),
                ProgramTerminationKind.GOBACK));
    }

    /**
     * {@code STOP RUN} vs {@code STOP <literal>} (Fase 7b): distinguido
     * EXCLUSIVAMENTE via la API estructurada de ProLeap -- {@code
     * StopStatementContext.RUN()} devuelve un {@code TerminalNode} no nulo
     * solo para la forma {@code STOP RUN}, nunca inspeccionando {@code
     * sourceText}. La forma {@code STOP <literal>} (codigo de finalizacion)
     * no es {@code STOP RUN} en sentido estricto y se clasifica {@code
     * UNKNOWN} -- Fase 7 (propagacion interprocedural) bloquea la
     * propagacion de salida para ambos casos por igual (ninguno de los dos
     * retorna control normalmente al caller), asi que la distincion
     * practica relevante es unicamente "es STOP RUN" vs "no lo es".
     */
    private void convertStop(StopStatement stopStmt, String parentId, BranchKind branchKind) {
        Location loc = resolveLocation(stopStmt.getCtx());
        ProgramTerminationKind terminationKind = ProgramTerminationKind.UNKNOWN;
        if (stopStmt.getCtx() instanceof Cobol85Parser.StopStatementContext stopCtx && stopCtx.RUN() != null) {
            terminationKind = ProgramTerminationKind.STOP_RUN;
        } else {
            ctx.unsupported(
                    "STOP en paragraph " + paragraphName
                            + " sin forma RUN identificable de forma estructural "
                            + "(kind=PROGRAM_TERMINATION, program_termination_kind=UNKNOWN)");
        }
        String id = nextId(StatementKind.PROGRAM_TERMINATION);
        collected.add(new CanonicalStatement(
                id, StatementKind.PROGRAM_TERMINATION, loc.sourceText(), loc.sourceFile(), loc.lineStart(),
                loc.lineEnd(), loc.kind(), parentId, branchKind, null, null, null, List.of(), List.of(),
                List.of(), List.of(), null, List.of(), List.of(), null, null, List.of(), terminationKind));
    }

    /**
     * {@code EXIT PROGRAM} vs {@code EXIT} simple (Fase 7b): distinguido
     * EXCLUSIVAMENTE via {@code ExitStatementContext.PROGRAM()} (no nulo
     * solo para {@code EXIT PROGRAM}). Un {@code EXIT} simple NO es un
     * terminador de programa -- en COBOL es un marcador no-operativo
     * (tipicamente destino de un {@code GO TO} al final de un paragraph),
     * nunca retorna control ni termina nada -- por lo que se enruta a
     * {@code convertOther} (permanece {@code kind=OTHER}, sin cambio de
     * comportamiento respecto a versiones anteriores de esta fase para
     * este caso).
     */
    private void convertExit(ExitStatement exitStmt, String parentId, BranchKind branchKind) {
        boolean isExitProgram = exitStmt.getCtx() instanceof Cobol85Parser.ExitStatementContext exitCtx
                && exitCtx.PROGRAM() != null;
        if (!isExitProgram) {
            convertOther(exitStmt, parentId, branchKind);
            return;
        }
        Location loc = resolveLocation(exitStmt.getCtx());
        String id = nextId(StatementKind.PROGRAM_TERMINATION);
        collected.add(new CanonicalStatement(
                id, StatementKind.PROGRAM_TERMINATION, loc.sourceText(), loc.sourceFile(), loc.lineStart(),
                loc.lineEnd(), loc.kind(), parentId, branchKind, null, null, null, List.of(), List.of(),
                List.of(), List.of(), null, List.of(), List.of(), null, null, List.of(),
                ProgramTerminationKind.EXIT_PROGRAM));
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
