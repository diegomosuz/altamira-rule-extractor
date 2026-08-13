"""Fase 15B3-B1, seccion 4: prueba que `Settings.enhanced_candidates_enabled`
habilita EXCLUSIVAMENTE la produccion de candidatos ampliados dentro de
`CANDIDATES_DETECTED` -- nunca activation, rollback, aprobaciones,
audit, ni la lectura de runs historicos.

`candidate production` != `promotion`/`functional approval`: ningun
candidato (V1, V2 nuevo, o fusionado) puede llegar a `status` distinto
de `DETECTED_CANDIDATE` (`CandidateArtifact` ya lo exige, sin cambios
en esta fase), y ningun modulo de activation/governance/audit importa
`enhanced_candidate_integration` ni referencia el flag."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import altamira_extractor.pipeline as _pipeline_package
from altamira_extractor.config import Settings
from altamira_extractor.contracts.candidate import RuleCandidate
from altamira_extractor.contracts.enums import CandidateStatus

# Resuelto contra el `__path__` del paquete YA IMPORTADO -- nunca contra
# `Path(__file__).parents[N]`: ese calculo asume un checkout tipo
# `src/altamira_extractor/pipeline/...` relativo a este archivo de test,
# que no se cumple en la imagen Docker de `test` (runtime del wheel
# instalado con `--target=/app`, donde el paquete vive directamente en
# `/app/altamira_extractor/pipeline`, sin prefijo `src/`).
_PIPELINE_DIR = Path(_pipeline_package.__path__[0])

# Modulos de activation/promotion/governance/audit que deben permanecer
# completamente ajenos a la deteccion ampliada (Fase 15B3-B/B1 es
# exclusivamente sobre CANDIDATES_DETECTED, nunca sobre estos).
_ISOLATED_MODULE_GLOBS = (
    "unified_activation_*.py",
    "unified_active_lane_*.py",
    "unified_materialization_service.py",
    "active_artifact_resolver.py",
    "operational_governance_*.py",
    "candidate_promotion_*.py",
)


def _isolated_module_paths() -> list[Path]:
    paths: list[Path] = []
    for pattern in _ISOLATED_MODULE_GLOBS:
        paths.extend(sorted(_PIPELINE_DIR.glob(pattern)))
    assert paths, "no se encontraron modulos de activation/governance para auditar"
    return paths


def _imported_module_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("module_path", _isolated_module_paths(), ids=lambda p: p.name)
def test_activation_governance_audit_modules_never_import_enhanced_detection(
    module_path: Path,
) -> None:
    source = module_path.read_text(encoding="utf-8")
    imported = _imported_module_names(source)
    assert not any("enhanced_candidate_integration" in name for name in imported), (
        f"{module_path.name} importa enhanced_candidate_integration; el flag "
        "enhanced_candidates_enabled debe permanecer aislado de activation/promotion/governance"
    )
    assert "enhanced_candidates_enabled" not in source, (
        f"{module_path.name} referencia enhanced_candidates_enabled; activation/rollback/"
        "aprobaciones/audit no deben depender de este flag"
    )


def test_settings_default_is_enabled() -> None:
    """Fase 15B4-CANDIDATE-QUALITY-5E: default flippeado False -> True --
    las 4 familias V2 (RETURN_CODE_PROPAGATION/LEVEL_88_RETURN_CODE/
    STATE_TRANSITION/CALCULATION) alcanzaron el criterio de cierre
    formal (FP=0/FN=0/precision=recall=f1=1.0) y quedan activas por
    defecto en 1.17. El modo legacy/conservador sigue disponible via
    `enhanced_candidates_enabled=False` explicito -- ver
    test_explicit_false_override_still_supported."""
    assert Settings(_env_file=None).enhanced_candidates_enabled is True


def test_explicit_false_override_still_supported() -> None:
    """Seccion 14 (5E): el flag nunca se elimina -- un operador debe
    poder seguir forzando el modo legacy/conservador explicitamente."""
    settings = Settings(_env_file=None, enhanced_candidates_enabled=False)
    assert settings.enhanced_candidates_enabled is False


def test_no_second_flag_was_introduced() -> None:
    """Solo debe existir UN flag nuevo para esta fase."""
    import altamira_extractor.config as _config_module

    settings_source = Path(_config_module.__file__).read_text(encoding="utf-8")
    assert settings_source.count("enhanced_candidates_enabled: bool") == 1


def _candidate(**overrides: object) -> RuleCandidate:
    defaults: dict[str, object] = {
        "candidate_id": "candidate::q0-return-code-decision::1.0::" + "a" * 64 + "::dec-1",
        "paragraph_id": "program::AR::op::PROG::1::abc123::paragraph::MAIN",
        "paragraph_name": "MAIN",
        "decision_id": "program::AR::op::PROG::1::abc123::paragraph::MAIN::decision::10::1",
        "detector_id": "q0-return-code-decision",
        "detector_version": "1.0",
        "detector_score": 1.0,
        "condition": "WS-COD = 'R001'",
        "outcome_code": "R001",
        "rule_type": None,
        "line_start": 10,
        "source_file": "01-codigo/cobol/PROG.cbl",
        "source_package_hash": "a" * 64,
    }
    defaults.update(overrides)
    return RuleCandidate(**defaults)  # type: ignore[arg-type]


def test_candidate_status_enum_has_no_approved_value() -> None:
    """'candidate production, no promotion': `CandidateStatus` (sin
    cambios en esta fase) solo declara `DETECTED_CANDIDATE` -- no existe
    ningun valor `FUNCTIONALLY_APPROVED`/`APPROVED` que un candidato
    ampliado pudiera adoptar, ni siquiera a nivel de tipo."""
    assert list(CandidateStatus) == [CandidateStatus.DETECTED_CANDIDATE]


def test_json_with_foreign_status_value_is_rejected() -> None:
    """Via JSON (p. ej. un artefacto corrupto o de otra fuente) un
    status ajeno tampoco puede colarse: el contrato lo rechaza."""
    candidate_json = _candidate().model_dump_json()
    forged = candidate_json.replace('"DETECTED_CANDIDATE"', '"FUNCTIONALLY_APPROVED"')
    with pytest.raises(Exception, match="DETECTED_CANDIDATE|status"):
        RuleCandidate.model_validate_json(forged)


def test_default_candidate_status_is_detected_candidate() -> None:
    assert _candidate().status == CandidateStatus.DETECTED_CANDIDATE
