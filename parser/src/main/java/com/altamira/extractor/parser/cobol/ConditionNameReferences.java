package com.altamira.extractor.parser.cobol;

import io.proleap.cobol.asg.metamodel.valuestmt.CallValueStmt;
import io.proleap.cobol.asg.metamodel.valuestmt.ConditionValueStmt;
import io.proleap.cobol.asg.metamodel.valuestmt.ValueStmt;
import io.proleap.cobol.asg.metamodel.valuestmt.condition.AndOrCondition;
import io.proleap.cobol.asg.metamodel.valuestmt.condition.CombinableCondition;
import io.proleap.cobol.asg.metamodel.valuestmt.condition.SimpleCondition;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Recolecta referencias DIRECTAS y VERIFICABLES a condition-names nivel
 * 88 dentro de una condicion de IF/EVALUATE de ProLeap. Deliberadamente
 * SEPARADO de {@link ValueReferences}: no modifica su recorrido (que
 * alimenta operands/variables_read/expression, campos ya establecidos
 * cuyo comportamiento no puede cambiar para preservar artefactos
 * historicos), y solo produce una referencia cuando el nombre resuelto
 * existe -- de forma inequivoca -- en el registro de condition-names ya
 * extraido de CanonicalProgram.conditionNames. Nunca infiere que toda
 * variable leida es una condicion 88; nunca reconstruye precedencia u
 * operadores booleanos.
 *
 * <p>Dos formas estructurales distintas, verificadas empiricamente:
 * <ul>
 *   <li>IF: {@code IfStatement.getCondition()} es un {@code
 *   ConditionValueStmt} que, para una referencia directa, desciende via
 *   {@code CombinableCondition}/{@code AndOrCondition} hasta un {@code
 *   SimpleCondition.getConditionNameReference().getConditionCall()}
 *   (tipo distinto de una comparacion {@code RelationConditionValueStmt},
 *   resuelto por la gramatica de ProLeap en tiempo de parseo).</li>
 *   <li>EVALUATE WHEN: {@code When.getCondition()} no usa ese mismo
 *   arbol para una referencia simple -- {@code getConditionValueStmt()}
 *   es null y el nombre aparece como un {@code CallValueStmt} generico
 *   via {@code getValue().getValueStmt()} (ProLeap no distingue
 *   estructuralmente aqui un condition-name de cualquier otro
 *   identificador; la unica forma de confirmarlo es el nombre
 *   coincidiendo con el registro de condition-names ya extraido).</li>
 * </ul>
 */
final class ConditionNameReferences {

    private ConditionNameReferences() {
    }

    static List<String> fromCondition(ConditionValueStmt condition, Set<String> knownConditionNames) {
        Set<String> out = new LinkedHashSet<>();
        collectFromConditionValueStmt(condition, knownConditionNames, out);
        return sorted(out);
    }

    static List<String> fromEvaluateValueStmt(ValueStmt valueStmt, Set<String> knownConditionNames) {
        Set<String> out = new LinkedHashSet<>();
        if (valueStmt instanceof CallValueStmt callValueStmt
                && callValueStmt.getCall() != null
                && callValueStmt.getCall().getName() != null) {
            addIfKnown(callValueStmt.getCall().getName(), knownConditionNames, out);
        } else if (valueStmt instanceof ConditionValueStmt condition) {
            collectFromConditionValueStmt(condition, knownConditionNames, out);
        }
        return sorted(out);
    }

    private static List<String> sorted(Set<String> names) {
        List<String> result = new ArrayList<>(names);
        result.sort(null);
        return result;
    }

    private static void collectFromConditionValueStmt(
            ConditionValueStmt condition, Set<String> knownConditionNames, Set<String> out) {
        if (condition == null) {
            return;
        }
        collectFromCombinable(condition.getCombinableCondition(), knownConditionNames, out);
        for (AndOrCondition andOr : condition.getAndOrConditions()) {
            collectFromCombinable(andOr.getCombinableCondition(), knownConditionNames, out);
        }
    }

    private static void collectFromCombinable(
            CombinableCondition combinable, Set<String> knownConditionNames, Set<String> out) {
        if (combinable == null) {
            return;
        }
        collectFromSimple(combinable.getSimpleCondition(), knownConditionNames, out);
    }

    private static void collectFromSimple(
            SimpleCondition simple, Set<String> knownConditionNames, Set<String> out) {
        if (simple == null) {
            return;
        }
        if (simple.getConditionNameReference() != null
                && simple.getConditionNameReference().getConditionCall() != null) {
            addIfKnown(simple.getConditionNameReference().getConditionCall().getName(), knownConditionNames, out);
        }
        // Condicion parentizada anidada, p. ej. IF (COND-A AND COND-B).
        collectFromConditionValueStmt(simple.getCondition(), knownConditionNames, out);
    }

    private static void addIfKnown(String name, Set<String> knownConditionNames, Set<String> out) {
        if (name != null && knownConditionNames.contains(name)) {
            out.add(name);
        }
    }
}
