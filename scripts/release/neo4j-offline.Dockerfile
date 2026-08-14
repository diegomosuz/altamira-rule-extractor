# Repack offline de Neo4j (Fase 15B4-C-RC-PACKAGING-REPRODUCIBILITY).
#
# Un `docker tag` simple sobre "neo4j:5-community" NUNCA es suficiente
# para el bundle offline: la imagen oficial de Docker Hub es un INDICE
# OCI multi-plataforma que ademas trae un manifest-referrer de
# atestacion adjunto (`io.containerd.manifest.subject`) -- verificado
# empiricamente en Fase 15B4-C: `ctr images import` (usado por
# `kind load image-archive` y, en release real, por el procedimiento de
# `docs/release/INSTALL_K3S.md`) falla con "content digest ... not
# found" / "mismatched image rootfs and manifest layers" contra un
# archive `docker save` de la imagen sin repack.
#
# Este Dockerfile de una sola linea, construido SIN provenance/SBOM
# (ver scripts/release/build_release_images.py), produce un manifest
# plano de una sola plataforma -- unico cambio necesario para que el
# archive resultante sea importable offline. Nunca reconstruye Neo4j
# desde cero, nunca agrega/quita capas: es exactamente el mismo
# contenido/digest de imagen, solo con un manifest limpio.
#
# Version fijada explicitamente (nunca ":latest", ver
# deploy/k3s/neo4j-statefulset.yaml para la justificacion completa del
# pin) -- actualizar aqui y en scripts/release/export_bundle.py
# (_DEFAULT_NEO4J_SOURCE_IMAGE) juntos si el release cambia de version
# de Neo4j.
FROM neo4j:5-community
