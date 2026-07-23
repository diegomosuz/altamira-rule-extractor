package com.altamira.extractor.parser.model;

/**
 * Formato COBOL resuelto y persistido en CanonicalProgram.source_format.
 *
 * <p>Deliberadamente NO incluye AUTO (se resuelve antes de construir un
 * CanonicalProgram, ver cobol.ProLeapCobolParser) ni FREE: se verifico
 * contra el codigo fuente real de ProLeap 2.4.0
 * (CobolPreprocessor.CobolSourceFormatEnum) que su unico candidato a
 * representar "formato libre" (VARIABLE) exige la misma disposicion de
 * columnas que FIXED — no es COBOL free-format moderno. Como esta version
 * de ProLeap no tiene un formato libre real, este parser nunca produce
 * "FREE" en el JSON de salida.
 *
 * <p>Espejo (subconjunto) de altamira_extractor.contracts.enums.SourceFormat
 * (Python), que si admite AUTO y FREE porque tambien se usa para
 * manifest.xml y para versiones futuras de ProLeap que pudieran
 * soportarlo; el contrato Python no se modifica por esta limitacion de
 * la version de ProLeap seleccionada ahora.
 */
public enum SourceFormat {
    FIXED,
    TANDEM
}
