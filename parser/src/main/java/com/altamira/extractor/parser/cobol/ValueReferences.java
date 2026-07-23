package com.altamira.extractor.parser.cobol;

import io.proleap.cobol.asg.metamodel.valuestmt.ArithmeticValueStmt;
import io.proleap.cobol.asg.metamodel.valuestmt.CallValueStmt;
import io.proleap.cobol.asg.metamodel.valuestmt.ConditionValueStmt;
import io.proleap.cobol.asg.metamodel.valuestmt.RelationConditionValueStmt;
import io.proleap.cobol.asg.metamodel.valuestmt.ValueStmt;
import io.proleap.cobol.asg.metamodel.valuestmt.arithmetic.Basis;
import io.proleap.cobol.asg.metamodel.valuestmt.arithmetic.MultDiv;
import io.proleap.cobol.asg.metamodel.valuestmt.arithmetic.MultDivs;
import io.proleap.cobol.asg.metamodel.valuestmt.arithmetic.PlusMinus;
import io.proleap.cobol.asg.metamodel.valuestmt.arithmetic.Power;
import io.proleap.cobol.asg.metamodel.valuestmt.arithmetic.Powers;
import io.proleap.cobol.asg.metamodel.valuestmt.condition.AndOrCondition;
import io.proleap.cobol.asg.metamodel.valuestmt.condition.CombinableCondition;
import io.proleap.cobol.asg.metamodel.valuestmt.condition.SimpleCondition;
import io.proleap.cobol.asg.metamodel.valuestmt.relation.ArithmeticComparison;
import io.proleap.cobol.asg.metamodel.valuestmt.relation.CombinedComparison;
import io.proleap.cobol.asg.metamodel.valuestmt.relation.SignCondition;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/**
 * Recolecta nombres de variables referenciadas dentro de un arbol de
 * expresion/condicion de ProLeap (condicion de IF/EVALUATE, expresion
 * aritmetica de COMPUTE, area de envio de un MOVE, valor de un SET...)
 * usando la resolucion semantica propia de ProLeap
 * ({@link CallValueStmt#getCall()}), nunca una inferencia por regex sobre
 * texto.
 *
 * <p>El ASG de ProLeap no expone un unico metodo generico para caminar
 * expresiones/condiciones: cada nodo compuesto (comparacion, +-, * /, ^,
 * AND/OR...) tiene sus propios getters tipados. Este recorrido cubre
 * explicitamente los nodos relevantes para las construcciones soportadas
 * (IF, EVALUATE, COMPUTE, MOVE, SET) mas el mecanismo generico
 * {@code getSubValueStmts()} como red de seguridad adicional.
 */
final class ValueReferences {

    private ValueReferences() {
    }

    static List<String> collectVariableNames(ValueStmt stmt) {
        Set<String> names = new LinkedHashSet<>();
        collect(stmt, names);
        return new ArrayList<>(names);
    }

    private static void collect(ValueStmt stmt, Set<String> out) {
        if (stmt == null) {
            return;
        }

        if (stmt instanceof CallValueStmt callValueStmt
                && callValueStmt.getCall() != null
                && callValueStmt.getCall().getName() != null) {
            out.add(callValueStmt.getCall().getName());
        }

        if (stmt instanceof ConditionValueStmt condition) {
            collect(condition.getCombinableCondition(), out);
            for (AndOrCondition andOr : condition.getAndOrConditions()) {
                collect(andOr.getCombinableCondition(), out);
            }
        } else if (stmt instanceof CombinableCondition combinable) {
            collect(combinable.getSimpleCondition(), out);
        } else if (stmt instanceof SimpleCondition simple) {
            collect(simple.getCondition(), out);
            collect(simple.getRelationCondition(), out);
        } else if (stmt instanceof RelationConditionValueStmt relation) {
            collect(relation.getArithmeticComparison(), out);
            collect(relation.getCombinedComparison(), out);
            collect(relation.getSignCondition(), out);
        } else if (stmt instanceof ArithmeticComparison comparison) {
            collect(comparison.getArithmeticExpressionLeft(), out);
            collect(comparison.getArithmeticExpressionRight(), out);
        } else if (stmt instanceof CombinedComparison combined) {
            collect(combined.getArithmeticExpression(), out);
            if (combined.getCombinedCondition() != null) {
                for (ArithmeticValueStmt expr : combined.getCombinedCondition().getArithmeticExpressions()) {
                    collect(expr, out);
                }
            }
        } else if (stmt instanceof SignCondition sign) {
            collect(sign.getArithmeticExpression(), out);
        } else if (stmt instanceof ArithmeticValueStmt arithmetic) {
            collect(arithmetic.getMultDivs(), out);
            for (PlusMinus plusMinus : arithmetic.getPlusMinus()) {
                collect(plusMinus, out);
            }
        } else if (stmt instanceof PlusMinus plusMinus) {
            collect(plusMinus.getMultDivs(), out);
        } else if (stmt instanceof MultDivs multDivs) {
            collect(multDivs.getPowers(), out);
            for (MultDiv multDiv : multDivs.getMultDivs()) {
                collect(multDiv, out);
            }
        } else if (stmt instanceof MultDiv multDiv) {
            collect(multDiv.getPowers(), out);
        } else if (stmt instanceof Powers powers) {
            collect(powers.getBasis(), out);
            for (Power power : powers.getPowers()) {
                collect(power, out);
            }
        } else if (stmt instanceof Power power) {
            collect(power.getBasis(), out);
        } else if (stmt instanceof Basis basis) {
            collect(basis.getBasisValueStmt(), out);
        }

        for (ValueStmt sub : stmt.getSubValueStmts()) {
            collect(sub, out);
        }
    }

    /**
     * Si el arbol completo no referencia ninguna variable (ninguna Call en
     * ningun nodo), devuelve una representacion textual del valor literal
     * de la raiz; en caso contrario devuelve null (no es un literal puro,
     * o esta vacio).
     */
    static String literalTextIfPure(ValueStmt stmt) {
        if (stmt == null) {
            return null;
        }
        if (!collectVariableNames(stmt).isEmpty()) {
            return null;
        }
        Object value = stmt.getValue();
        return value != null ? String.valueOf(value) : null;
    }
}
