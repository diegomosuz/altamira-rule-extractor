package com.altamira.extractor.parser.model;

/**
 * Espejo de altamira_extractor.contracts.enums.TableAccessOperation
 * (Python). No existe un valor DELETES propio: SQL DELETE se clasifica
 * como WRITES (ver sql.EmbeddedSqlExtractor).
 */
public enum TableAccessOperation {
    READS,
    WRITES,
    UPDATES,
    INSERTS
}
