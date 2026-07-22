"""Parseo y validacion segura de manifest.xml contra schemas/manifest.xsd.

manifest.xml es dato no confiable (viene dentro de un ZIP subido por un
usuario): se parsea con entidades externas deshabilitadas, sin acceso de
red, sin DTD externas y rechazando explicitamente cualquier DOCTYPE.
Tras la validacion XSD estructural se aplica una segunda capa de
validacion semantica con el contrato Pydantic `Manifest` ya existente, y
finalmente un cruce contra el listado real de entradas del ZIP: el
manifest no puede declarar rutas que no existen en el paquete, y sus
tablas parametricas deben apuntar a las carpetas permitidas por
docs/PACKAGE_CONTRACT.md.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from lxml import etree

from ..contracts.enums import SourceFormat
from ..contracts.manifest import (
    Manifest,
    ManifestApplication,
    ManifestCountry,
    ManifestImplementation,
    ManifestOperation,
    ManifestParameterTable,
    ManifestSource,
)
from .errors import ManifestValidationError
from .zip_entries import extension_of
from .zip_paths import normalize_zip_entry_path

DDL_PREFIX = "02-parametria/ddl/"
SNAPSHOT_PREFIX = "02-parametria/snapshots/"
DDL_EXTENSIONS = frozenset({".sql", ".ddl"})
SNAPSHOT_EXTENSIONS = frozenset({".csv"})


def _secure_parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
    )


def _parse_secure(xml_bytes: bytes, *, source_label: str) -> etree._ElementTree:
    try:
        tree = etree.fromstring(xml_bytes, parser=_secure_parser()).getroottree()
    except etree.XMLSyntaxError as exc:
        raise ManifestValidationError(f"{source_label}: XML mal formado: {exc}") from exc

    if tree.docinfo.doctype:
        raise ManifestValidationError(
            f"{source_label}: declaracion DOCTYPE no permitida: {tree.docinfo.doctype!r}"
        )
    return tree


def _load_schema(xsd_path: Path) -> etree.XMLSchema:
    if not xsd_path.is_file():
        raise ManifestValidationError(f"no se encontro el XSD del manifest en {xsd_path}")
    schema_bytes = xsd_path.read_bytes()
    schema_doc = _parse_secure(schema_bytes, source_label=str(xsd_path))
    return etree.XMLSchema(schema_doc)


def _text_attr(element: etree._Element, name: str) -> str | None:
    value = element.get(name)
    return value if value else None


def _element_to_manifest(root: etree._Element) -> Manifest:
    country_el = root.find("country")
    application_el = root.find("application")
    operation_el = root.find("operation")
    implementation_el = root.find("implementation")
    source_el = root.find("source")
    if (
        country_el is None
        or application_el is None
        or operation_el is None
        or implementation_el is None
        or source_el is None
    ):
        # El XSD ya garantiza esta estructura; si llega aqui es un bug de
        # sincronizacion entre el XSD y este mapeo, no un dato de entrada.
        raise ManifestValidationError("manifest.xml no tiene la estructura esperada tras XSD")

    entry_programs = [
        (el.text or "").strip() for el in implementation_el.findall("entry-program")
    ]

    parameter_tables: list[ManifestParameterTable] = []
    parameter_tables_el = root.find("parameter-tables")
    if parameter_tables_el is not None:
        for table_el in parameter_tables_el.findall("table"):
            snapshot_date_raw = _text_attr(table_el, "snapshot-date")
            parameter_tables.append(
                ManifestParameterTable(
                    name=table_el.get("name", ""),
                    ddl=_text_attr(table_el, "ddl"),
                    snapshot=_text_attr(table_el, "snapshot"),
                    snapshot_date=date.fromisoformat(snapshot_date_raw)
                    if snapshot_date_raw
                    else None,
                )
            )

    try:
        return Manifest(
            schema_version=root.get("schema-version", ""),
            country=ManifestCountry(
                code=country_el.get("code", ""), name=country_el.get("name", "")
            ),
            application=ManifestApplication(name=application_el.get("name", "")),
            operation=ManifestOperation(
                logical_name=operation_el.get("logical-name", ""),
                description=_text_attr(operation_el, "description"),
            ),
            implementation=ManifestImplementation(
                version=implementation_el.get("version", ""),
                entry_programs=entry_programs,
            ),
            source=ManifestSource(
                format=SourceFormat(source_el.get("format", "")),
                encoding=source_el.get("encoding", ""),
            ),
            parameter_tables=parameter_tables,
        )
    except ValueError as exc:
        raise ManifestValidationError(
            f"manifest.xml no cumple el contrato Manifest: {exc}"
        ) from exc


def parse_and_validate_manifest(xml_bytes: bytes, xsd_path: Path) -> Manifest:
    """Valida manifest.xml contra el XSD y lo mapea al contrato Manifest."""
    document = _parse_secure(xml_bytes, source_label="manifest.xml")
    schema = _load_schema(xsd_path)
    try:
        schema.assertValid(document)
    except etree.DocumentInvalid as exc:
        raise ManifestValidationError(f"manifest.xml no cumple manifest.xsd: {exc}") from exc

    root = document.getroot()
    return _element_to_manifest(root)


def validate_manifest_paths_against_zip(
    manifest: Manifest, zip_normalized_entries: frozenset[str]
) -> None:
    """Cruza las rutas declaradas por el manifest contra las entradas reales del ZIP.

    No confia en el nombre del ZIP ni en lo que el manifest afirma sin
    evidencia: cada `ddl`/`snapshot` debe existir como entrada real,
    quedar dentro de la raiz del paquete y respetar la carpeta/extension
    que docs/PACKAGE_CONTRACT.md exige para ese tipo de archivo.
    """
    for table in manifest.parameter_tables:
        if table.ddl is not None:
            _check_referenced_path(
                raw_path=table.ddl,
                zip_normalized_entries=zip_normalized_entries,
                allowed_prefix=DDL_PREFIX,
                allowed_extensions=DDL_EXTENSIONS,
                kind_label="ddl",
                table_name=table.name,
            )
        if table.snapshot is not None:
            _check_referenced_path(
                raw_path=table.snapshot,
                zip_normalized_entries=zip_normalized_entries,
                allowed_prefix=SNAPSHOT_PREFIX,
                allowed_extensions=SNAPSHOT_EXTENSIONS,
                kind_label="snapshot",
                table_name=table.name,
            )


def _check_referenced_path(
    *,
    raw_path: str,
    zip_normalized_entries: frozenset[str],
    allowed_prefix: str,
    allowed_extensions: frozenset[str],
    kind_label: str,
    table_name: str,
) -> None:
    try:
        normalized = normalize_zip_entry_path(raw_path)
    except Exception as exc:
        raise ManifestValidationError(
            f"parameter-table {table_name!r}: {kind_label} declara una ruta invalida "
            f"{raw_path!r}: {exc}"
        ) from exc

    if not normalized.startswith(allowed_prefix):
        raise ManifestValidationError(
            f"parameter-table {table_name!r}: {kind_label} {raw_path!r} debe estar bajo "
            f"{allowed_prefix!r}"
        )

    extension = extension_of(normalized)
    if extension not in allowed_extensions:
        raise ManifestValidationError(
            f"parameter-table {table_name!r}: {kind_label} {raw_path!r} debe tener "
            f"extension en {sorted(allowed_extensions)}"
        )

    if normalized not in zip_normalized_entries:
        raise ManifestValidationError(
            f"parameter-table {table_name!r}: {kind_label} {raw_path!r} no existe como "
            "entrada del paquete ZIP"
        )
