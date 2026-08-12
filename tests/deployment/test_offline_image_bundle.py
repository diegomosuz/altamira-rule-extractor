"""Fase 15B4-B2: contrato de referencias offline de imagenes (export
tooling + release metadata). Nunca invoca Docker real -- todo
`subprocess.run`/`inspect_image` se sustituye via monkeypatch. Cubre
la correccion central de esta fase: el bundle SIEMPRE se guarda bajo
un alias local controlado por el release (nunca bajo la referencia
flotante original), y la metadata SIEMPRE distingue
source_reference/bundle_reference/image_id/source_repo_digest/
detected_version sin fabricar ninguno."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from scripts.release import build_release_metadata, export_bundle

DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy" / "k3s"


def _neo4j_manifest_image() -> str:
    text = (DEPLOY_DIR / "neo4j-statefulset.yaml").read_text(encoding="utf-8")
    docs = [d for d in yaml.safe_load_all(text) if d is not None]
    statefulset = next(d for d in docs if d.get("kind") == "StatefulSet")
    image: str = statefulset["spec"]["template"]["spec"]["containers"][0]["image"]
    return image


def test_export_bundle_default_neo4j_bundle_reference_matches_manifest() -> None:
    """El default de export_bundle.py y la referencia usada en el
    manifest K3s deben ser exactamente la misma cadena -- nunca dos
    fuentes de verdad divergentes para el mismo alias."""
    assert export_bundle._DEFAULT_NEO4J_BUNDLE_REFERENCE == _neo4j_manifest_image()


def test_export_bundle_tags_before_saving_and_saves_bundle_reference_not_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> Any:
        calls.append(cmd)
        if cmd[:2] == ["docker", "save"]:
            # -o <path> es el tercer/cuarto elemento -- simula que
            # docker realmente produjo el tarball, para que el
            # checksum posterior del script tenga algo que leer.
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"fake-tarball-bytes")

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr(export_bundle.subprocess, "run", _fake_run)

    exit_code = export_bundle.main(
        [
            "--app-image",
            "altamira-rule-extractor-app:1.17.0",
            "--neo4j-source-image",
            "neo4j:5-community",
            "--neo4j-bundle-reference",
            "altamira-dependencies/neo4j:5.26.28",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert len(calls) == 2

    tag_call, save_call = calls
    assert tag_call[:2] == ["docker", "tag"]
    assert tag_call[2:] == ["neo4j:5-community", "altamira-dependencies/neo4j:5.26.28"]

    assert save_call[:2] == ["docker", "save"]
    # El bundle contiene la referencia REETIQUETADA, nunca la fuente
    # flotante original -- es exactamente la correccion de esta fase.
    assert "altamira-rule-extractor-app:1.17.0" in save_call
    assert "altamira-dependencies/neo4j:5.26.28" in save_call
    assert "neo4j:5-community" not in save_call

    checksum_files = list(tmp_path.glob("*.sha256"))
    assert len(checksum_files) == 1


def test_build_release_metadata_distinguishes_source_bundle_digest_and_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _fake_inspect_image(image_ref: str) -> dict[str, object]:
        if image_ref == "altamira-rule-extractor-app:1.17.0":
            return {
                "requested_ref": image_ref,
                "id": "sha256:appimageid",
                "repo_tags": [image_ref],
                "repo_digests": [],
                "labels": {},
                "created": "2026-08-12T00:00:00Z",
                "size_bytes": 123,
            }
        if image_ref == "altamira-dependencies/neo4j:5.26.28":
            return {
                "requested_ref": image_ref,
                "id": "sha256:neo4jimageid",
                "repo_tags": [image_ref, "neo4j:5-community"],
                "repo_digests": [
                    "neo4j@sha256:362542416de6c09a971484d1893878016cc3b5cdec166e54b1c824a220ecd6b9"
                ],
                "labels": {},
                "created": "2026-08-12T00:00:00Z",
                "size_bytes": 456,
            }
        raise AssertionError(f"referencia inesperada en el test: {image_ref!r}")

    def _fake_version_report() -> dict[str, object]:
        return {
            "pyproject_version": "1.17.0",
            "git_sha": "deadbeef",
            "git_branch": "feat/observability-validation-release",
            "working_tree_dirty": True,
            "consistent": True,
        }

    monkeypatch.setattr(build_release_metadata, "inspect_image", _fake_inspect_image)
    monkeypatch.setattr(build_release_metadata, "collect_version_report", _fake_version_report)

    metadata = build_release_metadata.build_metadata(
        app_image_ref="altamira-rule-extractor-app:1.17.0",
        neo4j_source_image_ref="neo4j:5-community",
        neo4j_bundle_reference="altamira-dependencies/neo4j:5.26.28",
    )

    app_image = metadata["app_image"]
    assert isinstance(app_image, dict)
    expected_app_ref = "altamira-rule-extractor-app:1.17.0"
    assert app_image["bundle_reference"] == expected_app_ref
    assert app_image["source_reference"] == expected_app_ref
    assert app_image["image_id"] == "sha256:appimageid"
    # nunca fabricado: la imagen app no tiene RepoDigests locales
    assert app_image["source_repo_digest"] is None

    neo4j_image = metadata["neo4j_image"]
    assert isinstance(neo4j_image, dict)
    assert neo4j_image["bundle_reference"] == "altamira-dependencies/neo4j:5.26.28"
    assert neo4j_image["source_reference"] == "neo4j:5-community"
    assert neo4j_image["bundle_reference"] != neo4j_image["source_reference"]
    assert neo4j_image["image_id"] == "sha256:neo4jimageid"
    assert (
        neo4j_image["source_repo_digest"]
        == "neo4j@sha256:362542416de6c09a971484d1893878016cc3b5cdec166e54b1c824a220ecd6b9"
    )
    assert neo4j_image["detected_version"] == "5.26.28"


def test_extract_repo_digest_for_never_fabricates_when_absent() -> None:
    assert build_release_metadata._extract_repo_digest_for([], "neo4j:5-community") is None
