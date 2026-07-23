"""Excepciones de dominio del pipeline RECEIVED..INVENTORIED.

Cada una representa un motivo de fallo explicito (python.md: "Excepciones
de dominio explicitas"). Nunca se atrapan de forma generica: el llamador
decide como traducirlas a StageExecution.error.
"""

from __future__ import annotations


class PipelineError(Exception):
    """Base de todas las excepciones de dominio del pipeline de ingesta."""


class PackageValidationError(PipelineError):
    """El paquete ZIP no cumple integridad, seguridad o estructura minima."""


class ZipSecurityError(PackageValidationError):
    """Una entrada del ZIP viola una regla de seguridad (path, tipo, cifrado, tamano)."""


class ManifestValidationError(PackageValidationError):
    """manifest.xml no es valido contra el XSD, el contrato Pydantic o el ZIP."""


class ExtractionError(PipelineError):
    """La extraccion segura entrada-por-entrada fallo."""


class RunConflictError(PipelineError):
    """Se intento reutilizar un run_id con un estado incompatible."""


class ParserUnavailableError(PipelineError):
    """El JAR del parser o el runtime Java no estan disponibles.

    Fatal a nivel de etapa PARSED completa: aborta antes de procesar
    cualquier programa (o interrumpe el resto de la cola), nunca se marca
    como fallo recuperable de un programa individual.
    """


class ParserContractViolationError(PipelineError):
    """El parser Java (o los datos de origen) violaron el contrato
    documentado: exit code fatal (2/4/5), stdout inesperado, exit 0 sin
    archivo de salida, JSON invalido, o una verificacion cruzada contra
    Inventory/RunState no coincide (source_file, source_hash,
    source_package_hash, contencion de paths). Fatal a nivel de etapa
    PARSED completa, igual que ParserUnavailableError.
    """


class DependencyBuildError(PipelineError):
    """Precondicion de DEPENDENCIES_BUILT incumplida: PARSED no esta
    realmente completo (StageExecution ausente/duplicada/no SUCCEEDED),
    falta un CanonicalProgram esperado segun Inventory, o alguno no valida
    o es inconsistente con Inventory/RunState (source_file, source_hash,
    source_package_hash). Fatal para la etapa completa: no hay reintento
    parcial posible cuando el prerequisito mismo esta roto.
    """
