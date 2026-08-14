# QA → PROD y Rollback (Fase 15B4-B)

## Promoción QA → PROD

```
BUILD ONCE (scripts/release/build_release_images.py -- app + repack Neo4j)
  → SCAN (Syft + Grype, scripts/release/sbom_scan.py)
  → EXPORT / REGISTER imagen inmutable (digest capturado, scripts/release/export_bundle.py)
  → DEPLOY QA
  → ACCEPTANCE (docs/release/ACCEPTANCE.md, perfil A y/o B según corresponda)
  → PROMOTE el MISMO digest de imagen (nunca rebuild)
  → DEPLOY PROD
```

**BUILD ONCE, en detalle** (Fase 15B4-C-RC-PACKAGING-REPRODUCIBILITY —
único procedimiento versionado, nunca comandos manuales de una sesión
de qualification):

```bash
python -m scripts.release.build_release_images
python -m scripts.release.export_bundle \
    --app-image altamira-rule-extractor-app:<version> \
    --neo4j-source-image altamira-dependencies/neo4j:5.26.28 \
    --neo4j-bundle-reference altamira-dependencies/neo4j:5.26.28 \
    --output-dir dist/release/<version>
python -m scripts.release.build_release_metadata \
    --app-image altamira-rule-extractor-app:<version> \
    --output dist/release/<version>/release-metadata.json
```

`build_release_images.py` construye la imagen `app` (`--target runtime`,
labels de identidad correctos, `--provenance=false --sbom=false` —
sin esto, `ctr images import` en K3s/containerd rechaza la imagen,
ver defecto P1 abajo) y repackea `neo4j:5-community` (imagen oficial,
un índice multi-plataforma con un manifest-referrer de atestación
adjunto) hacia un manifest plano tageado directamente como
`altamira-dependencies/neo4j:5.26.28` (defecto P2). Ambos defectos
fueron encontrados durante el rehearsal real contra un cluster
`kind` efímero — nunca teóricos — y quedan cerrados por este único
script, verificado mediante reconstrucción limpia + import offline +
despliegue exitoso sin ningún paso correctivo manual posterior.

**Nunca se reconstruye la imagen entre QA y PROD.** El digest capturado por
`scripts/release/build_release_metadata.py` en el momento del build es la
identidad inmutable que se promueve sin cambios — solo cambian los
manifests/ConfigMap/Secret que apuntan al ambiente (namespace, `NEO4J_URI`,
`ALTAMIRA_TRUSTED_HOSTS`, credenciales), nunca la imagen en sí.

**Build vs runtime**: el build de `BUILD ONCE` puede requerir acceso a
registry/dependency acquisition (Maven Central, PyPI, Docker Hub para
`neo4j:5-community`) — nunca se afirma "build air-gapped". El claim de
offline aplica exclusivamente al **runtime** (import del archive vía
`ctr images import`/`kind load image-archive` + despliegue, sin acceso
externo), ver `docs/release/INSTALL_K3S.md`.

## Rollback

Mínimo necesario, documentado en `release-metadata.json` de cada release
(`scripts/release/build_release_metadata.py`):

- referencia/digest de la imagen `app` anterior;
- referencia/digest de la imagen `neo4j` anterior (si cambió respecto al
  release actual);
- manifests K3s anteriores (versionados junto al release, `deploy/k3s/`);
- `ConfigMap` anterior;
- claves de `Secret` compatibles (mismo esquema de claves esperado por la
  versión anterior de la app — verificar antes de rollback, ver abajo);
- snapshot del PVC (`altamira-app-data`, `neo4j-data`) tomado **antes** del
  upgrade que se está revirtiendo.

**Antes de cualquier rollback real**: comparar los `schema_version` de
artefactos/contratos entre el release actual y el release destino del
rollback (docenas de contratos Pydantic llevan su propio `schema_version`,
ver `contracts/*.py`). Un rollback de imagen de `app` **no** requiere
migración de datos de Neo4j en el caso general — `SEMANTIC_GRAPH_LOADED`
reemplaza transaccionalmente el subgrafo gestionado desde el artefacto
filesystem de cada run, nunca depende de un estado incremental en Neo4j —
pero si `schema_version` cambió entre versiones, auditar explícitamente
antes de revertir. No existe ningún script de migración en este repo (nunca
ha sido necesario aún) — riesgo real a vigilar en releases futuros que sí
cambien `schema_version` entre sí.

No se desarrolla un framework de migración en este release.

## Backup / Restore

**Mínimo necesario**: snapshot del PVC `neo4j-data` + `altamira-app-data`
antes de cualquier upgrade — ambos son el único estado verdaderamente
irrecuperable (todo lo demás dentro de un run es regenerable desde
`input/package.zip`, ver `pipeline/runner.py`).

- **Requerido para QA**: backup manual antes de cada prueba de upgrade.
- **Recomendado para producción**: snapshot automatizado usando la
  herramienta nativa de backup del almacenamiento/K3s del cliente (este
  release no introduce ninguna dependencia nueva de backup, p. ej. Velero,
  salvo que el cliente ya la tenga disponible).

No se diseña una plataforma de backup enterprise — fuera de alcance.
