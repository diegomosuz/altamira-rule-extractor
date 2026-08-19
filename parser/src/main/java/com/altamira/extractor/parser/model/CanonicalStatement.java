package com.altamira.extractor.parser.model;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.List;

/**
 * Espejo de altamira_extractor.contracts.canonical.CanonicalStatement
 * (Python). Representacion plana: IF/EVALUATE anidados y sus ramas son
 * statements hermanos adicionales que apuntan a su padre via
 * parentStatementId y declaran branchKind (THEN/ELSE/WHEN/WHEN_OTHER).
 *
 * <p>statementId es deterministico y unico en todo el CanonicalProgram
 * (no solo dentro del Paragraph): {@code
 * <program_name>::<paragraph_name>::<ordinal>::<kind>} (ver
 * cobol.CanonicalProgramExtractor).
 *
 * <p>Campos de la Fase 3 de la ampliacion semantica (soporte nivel 88),
 * todos opcionales y aditivos:
 * <ul>
 *   <li>{@code conditionNameTarget}: para {@code kind=SET}, el nombre de
 *   la condicion 88 objetivo cuando el target resuelve de forma
 *   inequivoca contra {@code CanonicalProgram.conditionNames} Y el valor
 *   es literalmente TRUE o FALSE (nunca inferido por coincidencia de
 *   texto en un SET ordinario o de indice).</li>
 *   <li>{@code conditionSetValue}: {@code true}/{@code false} cuando
 *   {@code conditionNameTarget} esta resuelto (TO TRUE / TO FALSE),
 *   {@code null} en cualquier otro caso.</li>
 *   <li>{@code referencedConditionNames}: para {@code kind=IF} (la
 *   sentencia IF misma) y para el primer statement hijo de una rama
 *   {@code WHEN} de EVALUATE, los nombres de condicion 88 referenciados
 *   directamente y verificados contra {@code conditionNames} -- nunca
 *   toda variable leida se asume condicion 88.</li>
 * </ul>
 *
 * <p>Los tres campos de nivel 88 se anotan {@code @JsonInclude(NON_EMPTY)}:
 * cuando estan ausentes/vacios (el caso de CUALQUIER statement que no usa
 * esta extension) ni siquiera aparecen como clave en el JSON -- a
 * diferencia de los campos historicos nullable (p. ej. {@code
 * assignedLiteral}/{@code branchCondition}), que siguen serializando
 * {@code null} exactamente como antes de la Fase 3 (ninguna anotacion
 * nueva los afecta).
 *
 * <p>Campo de la Fase 3 v1.18.3 (correccion de limites de token en
 * expresiones -- checkpoint correctivo, secciones 4/7-9): {@code
 * legacyExpression}, poblado UNICAMENTE por los 3 sitios de riesgo ya
 * corregidos ({@code kind=IF}, sujeto de {@code kind=EVALUATE}, {@code
 * kind=COMPUTE}) via {@code StatementExtractor#renderTokenRangeLegacyGlued}.
 * Reconstruye, a partir del MISMO rango de tokens que produce el {@code
 * expression} ya corregido (con espacio de separacion real), el texto
 * EXACTO que {@code ParserRuleContext#getText()} habria producido ANTES
 * de esta correccion (concatenacion sin separador, p. ej. {@code
 * "SQLCODENOT=0"}) -- unica entrada confiable para preservar
 * {@code candidate_id} de V2/enhanced via el mecanismo de dual-key ya
 * existente ({@code legacy_key}/{@code legacy_condition} en
 * {@code enhanced_candidate_integration.py}, Ciclo 4 v1.18.2, extendido
 * aqui, nunca reemplazado). Anotado {@code @JsonInclude(NON_EMPTY)}:
 * ausente para todo programa/statement no afectado por el defecto de
 * espaciado que corrige, produciendo JSON identico al de antes de esta
 * fase para esos casos. NUNCA debe leerse para construir
 * {@code RuleCandidate.condition}/{@code ContextPackage.decision.
 * expression}/{@code RuleDraft}/evidencia de guardrail/render de UI --
 * es exclusivamente un dato de compatibilidad de identidad.
 *
 * <p>Campos de la Fase 6 de la ampliacion semantica (fundacion
 * interprocedural CALL/LINKAGE), poblados unicamente cuando
 * {@code kind=CALL} (ver {@code CanonicalProgramExtractor}); todos
 * anotados {@code @JsonInclude(NON_EMPTY)} para que cualquier statement
 * de otro {@code kind} -- es decir, TODO programa anterior a esta fase --
 * produzca JSON byte a byte identico al formato previo:
 * <ul>
 *   <li>{@code callTargetKind}/{@code calledProgramName}/
 *   {@code calledProgramExpression}: forma del target de {@code CALL}
 *   (literal resuelto estructuralmente vs. identificador dinamico, nunca
 *   resuelto via propagacion).</li>
 *   <li>{@code callArguments}: argumentos posicionales de
 *   {@code USING}.</li>
 *   <li>{@code callReturningDataItem}: receptor de {@code RETURNING}/
 *   {@code GIVING} cuando es estructuralmente identificable.</li>
 *   <li>{@code callHasOnException}/{@code callHasNotOnException}:
 *   indicadores booleanos de presencia (nunca se modela el control flow
 *   completo de esas clausulas en esta fase).</li>
 * </ul>
 *
 * <p>Campo de la Fase 7b de la ampliacion semantica (distincion
 * estructural de terminadores de programa), poblado unicamente cuando
 * {@code kind=PROGRAM_TERMINATION} (ver {@code StatementExtractor});
 * anotado {@code @JsonInclude(NON_EMPTY)} por el mismo motivo que los
 * campos de {@code CALL}:
 * <ul>
 *   <li>{@code programTerminationKind}: GOBACK/EXIT_PROGRAM/STOP_RUN,
 *   determinado exclusivamente via la API estructurada de ProLeap
 *   ({@code StopStatementContext.RUN()}/{@code ExitStatementContext.
 *   PROGRAM()}, nunca inspeccionando {@code sourceText}). UNKNOWN cubre
 *   formas residuales que ProLeap modela bajo estos tipos ASG pero sin
 *   una via estructural fiable para distinguirlas (defensivo, no
 *   alcanzado por la gramatica actual).</li>
 * </ul>
 */
public record CanonicalStatement(
        String statementId,
        StatementKind kind,
        String sourceText,
        String sourceFile,
        Integer lineStart,
        Integer lineEnd,
        LocationKind locationKind,
        String parentStatementId,
        BranchKind branchKind,
        String branchCondition,
        String expression,
        String normalizedExpression,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) String legacyExpression,
        List<String> operands,
        List<String> variablesRead,
        List<String> variablesWritten,
        List<String> targetDataItems,
        String assignedLiteral,
        List<String> targetParagraphs,
        List<CanonicalSqlAccess> sqlAccess,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) String conditionNameTarget,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) Boolean conditionSetValue,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) List<String> referencedConditionNames,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) CallTargetKind callTargetKind,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) String calledProgramName,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) String calledProgramExpression,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) List<CanonicalCallArgument> callArguments,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) String callReturningDataItem,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) Boolean callHasOnException,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) Boolean callHasNotOnException,
        @JsonInclude(JsonInclude.Include.NON_EMPTY) ProgramTerminationKind programTerminationKind) {

    /**
     * Constructor de compatibilidad para todo statement que no sea
     * {@code kind=CALL} ni {@code kind=PROGRAM_TERMINATION}
     * (MOVE/SET/IF/EVALUATE/COMPUTE/GO_TO/PERFORM/EXEC_SQL/OTHER): delega
     * en el constructor canonico con los siete campos de la Fase 6 y el
     * campo de la Fase 7b en su valor "ausente" ({@code null}/lista
     * vacia), evitando modificar cada sitio de construccion ya existente
     * en {@code StatementExtractor}.
     */
    public CanonicalStatement(
            String statementId,
            StatementKind kind,
            String sourceText,
            String sourceFile,
            Integer lineStart,
            Integer lineEnd,
            LocationKind locationKind,
            String parentStatementId,
            BranchKind branchKind,
            String branchCondition,
            String expression,
            String normalizedExpression,
            List<String> operands,
            List<String> variablesRead,
            List<String> variablesWritten,
            List<String> targetDataItems,
            String assignedLiteral,
            List<String> targetParagraphs,
            List<CanonicalSqlAccess> sqlAccess,
            String conditionNameTarget,
            Boolean conditionSetValue,
            List<String> referencedConditionNames) {
        this(
                statementId, kind, sourceText, sourceFile, lineStart, lineEnd, locationKind,
                parentStatementId, branchKind, branchCondition, expression, normalizedExpression, null,
                operands, variablesRead, variablesWritten, targetDataItems, assignedLiteral,
                targetParagraphs, sqlAccess, conditionNameTarget, conditionSetValue,
                referencedConditionNames, null, null, null, List.of(), null, null, null, null);
    }

    /**
     * Constructor de compatibilidad IDENTICO al anterior (mismos 21
     * campos), mas {@code legacyExpression} (Fase 3 v1.18.3, checkpoint
     * correctivo secciones 4/7-9): UNICAMENTE los 3 sitios de riesgo ya
     * corregidos ({@code convertIf}, sujeto de {@code convertEvaluate},
     * {@code convertCompute}) usan este overload -- todo el resto de
     * {@code StatementExtractor} sigue usando el de 21 argumentos sin
     * cambios (legacyExpression queda {@code null}, ningun otro
     * {@code kind} expone el defecto de {@code getText()} que este campo
     * compensa).
     */
    public CanonicalStatement(
            String statementId,
            StatementKind kind,
            String sourceText,
            String sourceFile,
            Integer lineStart,
            Integer lineEnd,
            LocationKind locationKind,
            String parentStatementId,
            BranchKind branchKind,
            String branchCondition,
            String expression,
            String normalizedExpression,
            List<String> operands,
            List<String> variablesRead,
            List<String> variablesWritten,
            List<String> targetDataItems,
            String assignedLiteral,
            List<String> targetParagraphs,
            List<CanonicalSqlAccess> sqlAccess,
            String conditionNameTarget,
            Boolean conditionSetValue,
            List<String> referencedConditionNames,
            String legacyExpression) {
        this(
                statementId, kind, sourceText, sourceFile, lineStart, lineEnd, locationKind,
                parentStatementId, branchKind, branchCondition, expression, normalizedExpression,
                legacyExpression, operands, variablesRead, variablesWritten, targetDataItems,
                assignedLiteral, targetParagraphs, sqlAccess, conditionNameTarget, conditionSetValue,
                referencedConditionNames, null, null, null, List.of(), null, null, null, null);
    }

    /**
     * Constructor de compatibilidad para {@code kind=PROGRAM_TERMINATION}
     * (GOBACK/EXIT PROGRAM/STOP RUN): delega en el constructor canonico
     * con los siete campos de {@code CALL} (Fase 6) en su valor "ausente",
     * evitando que {@code StatementExtractor} deba construir la tupla
     * completa de 24 campos para este {@code kind}.
     */
    public CanonicalStatement(
            String statementId,
            StatementKind kind,
            String sourceText,
            String sourceFile,
            Integer lineStart,
            Integer lineEnd,
            LocationKind locationKind,
            String parentStatementId,
            BranchKind branchKind,
            String branchCondition,
            String expression,
            String normalizedExpression,
            List<String> operands,
            List<String> variablesRead,
            List<String> variablesWritten,
            List<String> targetDataItems,
            String assignedLiteral,
            List<String> targetParagraphs,
            List<CanonicalSqlAccess> sqlAccess,
            String conditionNameTarget,
            Boolean conditionSetValue,
            List<String> referencedConditionNames,
            ProgramTerminationKind programTerminationKind) {
        this(
                statementId, kind, sourceText, sourceFile, lineStart, lineEnd, locationKind,
                parentStatementId, branchKind, branchCondition, expression, normalizedExpression, null,
                operands, variablesRead, variablesWritten, targetDataItems, assignedLiteral,
                targetParagraphs, sqlAccess, conditionNameTarget, conditionSetValue,
                referencedConditionNames, null, null, null, List.of(), null, null, null,
                programTerminationKind);
    }
}
