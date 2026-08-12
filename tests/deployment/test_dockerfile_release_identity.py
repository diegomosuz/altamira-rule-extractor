"""Verificacion estatica (sin `docker build`, ver
tests/docker/test_docker_e2e.py para la verificacion real de build) de
que el Dockerfile declara los build args y labels OCI minimos de
identidad de release (Fase 15B4-B Seccion 4)."""

from __future__ import annotations

from pathlib import Path

DOCKERFILE = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")


def test_dockerfile_declares_app_version_and_git_sha_build_args() -> None:
    assert "ARG APP_VERSION=" in DOCKERFILE
    assert "ARG GIT_SHA=" in DOCKERFILE


def test_dockerfile_declares_required_oci_labels() -> None:
    for label in (
        "org.opencontainers.image.title",
        "org.opencontainers.image.version",
        "org.opencontainers.image.revision",
        "org.opencontainers.image.source",
    ):
        assert label in DOCKERFILE, f"label OCI faltante en Dockerfile: {label}"


def test_dockerfile_never_hardcodes_a_real_looking_secret() -> None:
    lowered = DOCKERFILE.lower()
    for forbidden in ("api_key=sk-", "password=", "secret="):
        assert forbidden not in lowered
