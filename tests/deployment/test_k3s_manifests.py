"""Invariantes minimos de los manifests K3s (Fase 15B4-B Seccion 31/32).
Nunca un framework de testing de Kubernetes -- solo parseo YAML
(PyYAML, dependencia ya existente) + aserciones directas sobre la
estructura. Sin Docker/Neo4j/cluster real."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy" / "k3s"


def _load_documents(filename: str) -> list[dict[str, Any]]:
    text = (DEPLOY_DIR / filename).read_text(encoding="utf-8")
    return [doc for doc in yaml.safe_load_all(text) if doc is not None]


def _find(docs: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    matches = [d for d in docs if d.get("kind") == kind]
    assert len(matches) == 1, f"se esperaba exactamente un {kind!r}, hay {len(matches)}"
    return matches[0]


def test_all_manifests_parse_as_valid_yaml() -> None:
    for filename in (
        "namespace.yaml",
        "configmap.yaml",
        "secret.example.yaml",
        "app-pvc.yaml",
        "app-deployment.yaml",
        "neo4j-statefulset.yaml",
    ):
        docs = _load_documents(filename)
        assert docs, f"{filename} no produjo ningun documento YAML"
        for doc in docs:
            assert "kind" in doc and "apiVersion" in doc


def test_all_resources_use_the_dedicated_namespace() -> None:
    for filename in (
        "configmap.yaml",
        "secret.example.yaml",
        "app-pvc.yaml",
        "app-deployment.yaml",
        "neo4j-statefulset.yaml",
    ):
        for doc in _load_documents(filename):
            if doc.get("kind") == "Namespace":
                continue
            assert doc["metadata"]["namespace"] == "altamira-rule-extractor"


def test_app_deployment_has_single_replica_and_recreate_strategy() -> None:
    deployment = _find(_load_documents("app-deployment.yaml"), "Deployment")
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"


def test_neo4j_statefulset_has_single_replica() -> None:
    statefulset = _find(_load_documents("neo4j-statefulset.yaml"), "StatefulSet")
    assert statefulset["spec"]["replicas"] == 1


def test_app_service_is_cluster_ip() -> None:
    service = _find(_load_documents("app-deployment.yaml"), "Service")
    assert service["spec"]["type"] == "ClusterIP"


def test_neo4j_service_is_cluster_ip() -> None:
    service = _find(_load_documents("neo4j-statefulset.yaml"), "Service")
    assert service["spec"]["type"] == "ClusterIP"


def test_app_container_image_is_not_latest_tag() -> None:
    deployment = _find(_load_documents("app-deployment.yaml"), "Deployment")
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    assert not image.endswith(":latest")
    assert "1.17.0" in image


def test_app_container_uses_if_not_present_pull_policy() -> None:
    deployment = _find(_load_documents("app-deployment.yaml"), "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["imagePullPolicy"] == "IfNotPresent"


def test_neo4j_image_uses_release_owned_offline_alias_not_floating_tag() -> None:
    """Fase 15B4-B2: un digest puro (`neo4j@sha256:...`) NO sobrevive
    como referencia resoluble tras `docker save` -> `ctr images
    import` (verificado empiricamente: containerd registra la imagen
    bajo `io.containerd.image.name`, no bajo el digest) -- el manifest
    debe usar el alias local controlado por el release, nunca
    `neo4j:5-community` ni `:latest`."""
    statefulset = _find(_load_documents("neo4j-statefulset.yaml"), "StatefulSet")
    image = statefulset["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image == "altamira-dependencies/neo4j:5.26.28"
    assert image != "neo4j:5-community"
    assert not image.endswith(":latest")


def test_app_container_security_context_is_non_root_and_hardened() -> None:
    deployment = _find(_load_documents("app-deployment.yaml"), "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    security_context = container["securityContext"]
    assert security_context["runAsNonRoot"] is True
    assert security_context["runAsUser"] == 10001
    assert security_context["runAsGroup"] == 10001
    assert security_context["allowPrivilegeEscalation"] is False
    assert security_context["capabilities"]["drop"] == ["ALL"]


def test_app_pod_does_not_use_host_network_or_host_pid() -> None:
    deployment = _find(_load_documents("app-deployment.yaml"), "Deployment")
    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec.get("hostNetwork", False) is False
    assert pod_spec.get("hostPID", False) is False
    for container in pod_spec["containers"]:
        assert container["securityContext"].get("privileged", False) is False


def test_app_deployment_has_all_three_probes_targeting_expected_endpoints() -> None:
    deployment = _find(_load_documents("app-deployment.yaml"), "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert container["startupProbe"]["httpGet"]["path"] == "/health"
    assert container["livenessProbe"]["httpGet"]["path"] == "/health"
    assert container["readinessProbe"]["httpGet"]["path"] == "/ready"


def test_app_container_has_resource_requests_and_limits() -> None:
    deployment = _find(_load_documents("app-deployment.yaml"), "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    resources = container["resources"]
    assert "cpu" in resources["requests"] and "memory" in resources["requests"]
    assert "cpu" in resources["limits"] and "memory" in resources["limits"]


def test_neo4j_container_has_resource_requests_and_limits() -> None:
    statefulset = _find(_load_documents("neo4j-statefulset.yaml"), "StatefulSet")
    container = statefulset["spec"]["template"]["spec"]["containers"][0]
    resources = container["resources"]
    assert "cpu" in resources["requests"] and "memory" in resources["requests"]
    assert "cpu" in resources["limits"] and "memory" in resources["limits"]


def test_configmap_has_enhanced_candidates_disabled_by_default() -> None:
    configmap = _find(_load_documents("configmap.yaml"), "ConfigMap")
    assert configmap["data"]["ALTAMIRA_ENHANCED_CANDIDATES_ENABLED"] == "false"


def test_configmap_max_workers_is_one() -> None:
    configmap = _find(_load_documents("configmap.yaml"), "ConfigMap")
    assert configmap["data"]["ALTAMIRA_API_MAX_WORKERS"] == "1"


def test_configmap_never_contains_known_secret_keys() -> None:
    configmap = _find(_load_documents("configmap.yaml"), "ConfigMap")
    forbidden_keys = {
        "NEO4J_PASSWORD",
        "ALTAMIRA_SESSION_SECRET",
        "OPENAI_API_KEY",
        "PWC_GENAI_API_KEY",
        "security.yaml",
    }
    assert forbidden_keys.isdisjoint(configmap["data"].keys())


def test_secret_example_contains_security_yaml_and_llm_keys_are_placeholders() -> None:
    """Fase 15B4-B2: security.yaml (bloque completo, ver
    test_security_profile.py para su validez) y NEO4J_PASSWORD/
    ALTAMIRA_SESSION_SECRET (deliberadamente cortos, ver
    test_security_profile.py) ya no siguen el patron "REPLACE_WITH_"
    generico -- solo los campos LLM comentados siguen esa convencion."""
    secret = _find(_load_documents("secret.example.yaml"), "Secret")
    assert "security.yaml" in secret["stringData"]
    assert "TRUSTED_PROXY_HEADERS" in secret["stringData"]["security.yaml"]
    assert "NEO4J_PASSWORD" in secret["stringData"]
    assert "ALTAMIRA_SESSION_SECRET" in secret["stringData"]


def test_secret_example_never_contains_a_real_looking_llm_key() -> None:
    secret = _find(_load_documents("secret.example.yaml"), "Secret")
    for key, value in secret["stringData"].items():
        if key in ("OPENAI_API_KEY", "PWC_GENAI_API_KEY"):
            assert value.startswith("REPLACE_WITH_")


def test_app_pvc_uses_read_write_once() -> None:
    docs = _load_documents("app-pvc.yaml")
    pvc = _find(docs, "PersistentVolumeClaim")
    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]


def test_neo4j_statefulset_volume_claim_template_uses_read_write_once() -> None:
    statefulset = _find(_load_documents("neo4j-statefulset.yaml"), "StatefulSet")
    claim = statefulset["spec"]["volumeClaimTemplates"][0]
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]
