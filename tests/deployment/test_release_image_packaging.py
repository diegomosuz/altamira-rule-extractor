"""Verificacion estatica (sin `docker build`, ver
tests/deployment/test_k3s_manifests.py::
test_app_deployment_consumes_the_expected_configmap_via_env_from y
Fase 15B4-C-RC-PACKAGING-REPRODUCIBILITY para la verificacion real
contra un cluster kind efimero) de que el procedimiento de packaging
de release esta completamente versionado -- nunca dependiente de
comandos manuales de una sesion de qualification.

Defectos reales que este test impide reintroducir silenciosamente
(Fase 15B4-C, P1/P2): un `docker build` sin `--provenance=false
--sbom=false` produce una imagen `app` que `ctr images import`
rechaza; un `docker tag` simple sobre la imagen oficial
`neo4j:5-community` (sin repack) produce un archive con un
manifest-referrer de atestacion que `ctr images import` tambien
rechaza."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = (REPO_ROOT / "scripts" / "release" / "build_release_images.py").read_text(
    encoding="utf-8"
)
NEO4J_OFFLINE_DOCKERFILE = (
    REPO_ROOT / "scripts" / "release" / "neo4j-offline.Dockerfile"
).read_text(encoding="utf-8")
EXPORT_BUNDLE_SCRIPT = (REPO_ROOT / "scripts" / "release" / "export_bundle.py").read_text(
    encoding="utf-8"
)
NEO4J_STATEFULSET = (REPO_ROOT / "deploy" / "k3s" / "neo4j-statefulset.yaml").read_text(
    encoding="utf-8"
)


def test_build_release_images_disables_provenance_and_sbom_for_app_build() -> None:
    assert "--provenance=false" in BUILD_SCRIPT
    assert "--sbom=false" in BUILD_SCRIPT
    assert '"docker"' in BUILD_SCRIPT
    assert '"build"' in BUILD_SCRIPT


def test_build_release_images_targets_runtime_stage() -> None:
    assert '"--target"' in BUILD_SCRIPT
    assert '"runtime"' in BUILD_SCRIPT


def test_build_release_images_derives_app_version_and_git_sha_from_repo() -> None:
    """Nunca hardcodeados: reutiliza collect_version_report() (misma
    fuente que scripts/release/version_check.py)."""
    assert "collect_version_report" in BUILD_SCRIPT
    assert 'f"APP_VERSION={version_report' in BUILD_SCRIPT
    assert 'f"GIT_SHA={version_report' in BUILD_SCRIPT


def test_build_release_images_repacks_neo4j_via_dedicated_dockerfile() -> None:
    assert "neo4j-offline.Dockerfile" in BUILD_SCRIPT
    assert "--provenance=false" in BUILD_SCRIPT
    assert "--sbom=false" in BUILD_SCRIPT


def test_neo4j_offline_dockerfile_pins_the_expected_source_image() -> None:
    """Debe coincidir exactamente con
    scripts/release/export_bundle.py::_DEFAULT_NEO4J_SOURCE_IMAGE."""
    assert "FROM neo4j:5-community" in NEO4J_OFFLINE_DOCKERFILE
    assert '_DEFAULT_NEO4J_SOURCE_IMAGE = "neo4j:5-community"' in EXPORT_BUNDLE_SCRIPT


def test_export_bundle_documents_the_build_release_images_prerequisite() -> None:
    """El docstring debe advertir explicitamente que --neo4j-source-image
    NUNCA debe ser la referencia oficial flotante sin repack previo --
    ver Fase 15B4-C, defecto P2."""
    assert "build_release_images" in EXPORT_BUNDLE_SCRIPT
    assert "PRERREQUISITO OBLIGATORIO" in EXPORT_BUNDLE_SCRIPT


def test_neo4j_bundle_reference_consistent_across_export_script_and_k3s_manifest() -> None:
    """La referencia de bundle (tag local) usada por
    scripts/release/export_bundle.py y por
    scripts/release/build_release_images.py debe ser exactamente la
    misma que consume deploy/k3s/neo4j-statefulset.yaml -- si diverge,
    el bundle exportado no coincide con lo que el manifest espera."""
    bundle_ref = "altamira-dependencies/neo4j:5.26.28"
    assert f'_DEFAULT_NEO4J_BUNDLE_REFERENCE = "{bundle_ref}"' in EXPORT_BUNDLE_SCRIPT
    assert f'image: "{bundle_ref}"' in NEO4J_STATEFULSET
