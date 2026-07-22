# Contrato del paquete Altamira V1

## Estructura mínima

```text
package.zip
├── manifest.xml
├── 01-codigo/
│   ├── cobol/*.cbl
│   ├── copybooks/*.cpy
│   └── dclgen/*.dcl
└── 02-parametria/
    ├── ddl/*.sql|*.ddl
    └── snapshots/*.csv
```

Obligatorio:

- manifest.xml;
- al menos un programa COBOL;
- al menos un DDL o snapshot de paramétrica.

## Manifest

```xml
<?xml version="1.0" encoding="UTF-8"?>
<altamira-package schema-version="1.0">
  <country code="AR" name="Argentina"/>
  <application name="Transferencias"/>
  <operation logical-name="OP-TRF-PROPIA"
             description="Transferencia entre cuentas propias"/>
  <implementation version="3.2">
    <entry-program>ARTRFPROP01</entry-program>
  </implementation>
  <source format="AUTO" encoding="AUTO"/>
  <parameter-tables>
    <table name="PARAM_TRANSFER"
           ddl="02-parametria/ddl/PARAM_TRANSFER.sql"
           snapshot="02-parametria/snapshots/PARAM_TRANSFER_20260515.csv"
           snapshot-date="2026-05-15"/>
  </parameter-tables>
</altamira-package>
```

## Reglas

- Paths relativos.
- Sin `..`, paths absolutos o enlaces.
- Formatos: AUTO, FIXED, FREE, TANDEM.
- Encodings controlados.
- Múltiples programas admitidos.
- La fecha de snapshot no se infiere silenciosamente.
- El manifest representa la jerarquía mínima D1 durante V1; no sustituye completamente el repositorio Altamira.
