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
