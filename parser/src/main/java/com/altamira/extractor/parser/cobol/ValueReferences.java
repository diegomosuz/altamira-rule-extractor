package com.altamira.extractor.parser.cobol;

import io.proleap.cobol.asg.metamodel.FigurativeConstant;
import io.proleap.cobol.asg.metamodel.call.Call;
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

    /**
     * Devuelve el {@link Call} (referencia semantica de ProLeap a un
     * elemento nombrado) de un {@code ValueStmt} simple -- usado por la
     * fundacion interprocedural (Fase 6) para resolver el
     * {@code qualified_name} real de un argumento de {@code CALL}/
     * parametro formal via {@code DataDescriptionEntryCall#getDataDescriptionEntry()}
     * cuando ProLeap logro resolverlo contra una {@code DataDescriptionEntry}
     * real. A diferencia de {@link #collectVariableNames}, que solo
     * necesita el nombre, este metodo conserva el objeto {@code Call}
     * completo; se detiene en la primera coincidencia (los argumentos de
     * {@code CALL ... USING} son siempre un unico identificador/literal
     * por posicion, nunca una expresion compuesta).
     */
    static Call singleCallIfSimple(ValueStmt stmt) {
        if (stmt == null) {
            return null;
        }
        if (stmt instanceof CallValueStmt callValueStmt && callValueStmt.getCall() != null) {
            return callValueStmt.getCall();
        }
        for (ValueStmt sub : stmt.getSubValueStmts()) {
            Call found = singleCallIfSimple(sub);
            if (found != null) {
                return found;
            }
        }
        return null;
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
        return canonicalLiteralText(stmt.getValue());
    }

    /**
     * Convierte el {@code Object} devuelto por {@link ValueStmt#getValue()}
     * (o por {@code Literal#getValue()}, su origen mas comun) en su
     * representacion textual canonica.
     *
     * <p>Para literales ordinarios -- {@code String} (NON_NUMERIC),
     * {@code Boolean} (BOOLEAN/SET condicion-88 TO TRUE) e
     * {@code Integer}/{@code Double} (NUMERIC, incluida la evaluacion de
     * expresiones aritmeticas puras sin variables) -- se delega en
     * {@code String.valueOf}, exactamente el comportamiento previo: son
     * tipos JDK con {@code toString()} estable, determinista y sin estado
     * de identidad.
     *
     * <p>Para una constante figurativa (SPACES, ZERO, HIGH-VALUES, ...)
     * ProLeap no devuelve un String: {@code Literal.getValue()} devuelve
     * la instancia {@code FigurativeConstant}/{@code FigurativeConstantImpl}
     * cruda (confirmado con {@code javap -c io.proleap.cobol.asg.metamodel.
     * impl.LiteralImpl}, caso {@code FIGURATIVE_CONSTANT} del switch).
     * Invocar {@code String.valueOf}/{@code toString()} sobre ese objeto es
     * incorrecto: {@code FigurativeConstantImpl.toString()} antepone
     * {@code Object.toString()} (nombre de clase + hash de identidad,
     * dependiente del heap de cada JVM -- confirmado con
     * {@code javap -c io.proleap.cobol.asg.metamodel.impl.
     * FigurativeConstantImpl}, que invoca {@code java/lang/Object.toString}
     * antes de concatenar {@code figurativeConstantType}). Esto rompe la
     * reproducibilidad byte a byte entre procesos JVM distintos. En su
     * lugar se usa {@link FigurativeConstant#getFigurativeConstantType()}
     * (enum estable, sin identidad de objeto) mapeado a la forma canonica
     * COBOL estandar via {@link #canonicalFigurativeConstantText}.
     */
    static String canonicalLiteralText(Object value) {
        if (value == null) {
            return null;
        }
        if (value instanceof FigurativeConstant figurativeConstant) {
            return canonicalFigurativeConstantText(figurativeConstant.getFigurativeConstantType());
        }
        return String.valueOf(value);
    }

    /**
     * Forma canonica estandar COBOL por constante figurativa (singular,
     * con guion donde el termino de referencia del lenguaje lo usa):
     * {@code ZERO}, {@code SPACE}, {@code HIGH-VALUE}, {@code LOW-VALUE},
     * {@code QUOTE}, {@code ALL}, {@code NULL}. ProLeap preserva la
     * ortografia lexica exacta del programa fuente como constantes de
     * enum separadas ({@code ZERO}/{@code ZEROS}/{@code ZEROES},
     * {@code SPACE}/{@code SPACES}, ...) -- todas son sinonimos del mismo
     * valor segun el estandar COBOL, asi que se pliegan a una unica forma
     * canonica por familia en lugar de propagar la variante lexica
     * (singular vs. plural) como si fuera informacion semantica distinta.
     * El switch es exhaustivo sobre las 14 constantes reales de
     * {@code FigurativeConstant.FigurativeConstantType} (confirmado con
     * javap): el compilador rechaza cualquier caso faltante.
     */
    private static String canonicalFigurativeConstantText(FigurativeConstant.FigurativeConstantType type) {
        return switch (type) {
            case ZERO, ZEROS, ZEROES -> "ZERO";
            case SPACE, SPACES -> "SPACE";
            case HIGH_VALUE, HIGH_VALUES -> "HIGH-VALUE";
            case LOW_VALUE, LOW_VALUES -> "LOW-VALUE";
            case QUOTE, QUOTES -> "QUOTE";
            case ALL -> "ALL";
            case NULL, NULLS -> "NULL";
        };
    }
}
