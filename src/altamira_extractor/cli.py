"""CLI Typer del pipeline de ingesta (Prompt 3, extendido en Prompt 13c).

Uso:

    python -m altamira_extractor.cli ingest <zip>
    python -m altamira_extractor.cli resume <run_id>
    python -m altamira_extractor.cli runs
    python -m altamira_extractor.cli status <run_id>
    python -m altamira_extractor.cli candidates <run_id>
    python -m altamira_extractor.cli context <run_id> <candidate_id>
    python -m altamira_extractor.cli rule <run_id> <candidate_id>
    python -m altamira_extractor.cli download <run_id> [--output PATH]
    python -m altamira_extractor.cli semantic-coverage <run_id> [--json]
    python -m altamira_extractor.cli semantic-coverage-report <run_id> [--json]
    python -m altamira_extractor.cli functional-validate <run_id> [--json]
    python -m altamira_extractor.cli functional-validate-dataset --dataset <id> \
        --lane <lane> --run-id <run_id> [--run-id <run_id> ...] [--json]
    python -m altamira_extractor.cli release-readiness-assess <run_id> [--json]
    python -m altamira_extractor.cli release-readiness-assess-dataset --dataset <id> \
        --lane <lane> --run-id <run_id> [--run-id <run_id> ...] [--json]
    python -m altamira_extractor.cli semantic-effects <run_id> [--json]
    python -m altamira_extractor.cli semantic-propagation <run_id> [--json]
    python -m altamira_extractor.cli v2-candidates-shadow <run_id> [--json]
    python -m altamira_extractor.cli semantic-interprocedural <run_id> [--json]
    python -m altamira_extractor.cli semantic-interprocedural-propagation <run_id> [--json]
    python -m altamira_extractor.cli interprocedural-candidates-shadow <run_id> [--json]
    python -m altamira_extractor.cli candidate-promotion-assessment <run_id> [--json]
    python -m altamira_extractor.cli candidate-promotion-review-package <run_id> [--json]
    python -m altamira_extractor.cli candidate-promotion-plan <run_id> --decisions <path> [--json]
    python -m altamira_extractor.cli unified-candidates-shadow <run_id> [--json]
    python -m altamira_extractor.cli unified-shadow-validate <run_id> [--json]
    python -m altamira_extractor.cli unified-shadow-downstream <run_id> [--json]
    python -m altamira_extractor.cli unified-activation-evaluate <run_id> --config <path> [--json]
    python -m altamira_extractor.cli unified-activation-materialize <run_id> \
        --authorization <path> [--json]
    python -m altamira_extractor.cli unified-activation-status <run_id> [--json]
    python -m altamira_extractor.cli unified-activation-resolve <run_id> \
        --artifact <logical-name> [--json]
    python -m altamira_extractor.cli unified-activation-rollback <run_id> \
        --authorization <path> [--json]

`semantic-coverage` (Fase 1 de la ampliacion semantica,
`feat/semantic-expansion-foundation`) calcula y persiste
`<run_dir>/diagnostics/semantic-coverage.json`: un informe NO
contractual, generado exclusivamente bajo demanda, de cuanto de cada
CanonicalProgram recibio interpretacion semantica estructurada (ver
`docs/SEMANTIC_COVERAGE.md`). Nunca se invoca automaticamente desde
`ingest`/`resume`/la API/la UI; nunca modifica `run.json` ni ningun
artefacto `artifacts/01-10`.

`semantic-effects` (Fase 2 de la ampliacion semantica,
`feat/semantic-effects-foundation`) calcula y persiste
`<run_dir>/diagnostics/semantic-effects.json`: un artefacto NO
contractual, generado exclusivamente bajo demanda, que traduce cada
CanonicalStatement interpretado a un `SemanticEffect` normalizado
(sin propagar valores entre sentencias -- ver `docs/SEMANTIC_EFFECTS.
md`). Requiere unicamente que RUN_ID haya alcanzado PARSED
(SUCCEEDED). Nunca se invoca automaticamente desde `ingest`/`resume`/
la API/la UI; nunca modifica `run.json` ni ningun artefacto
`artifacts/01-10`.

`semantic-propagation` (Fase 4 de la ampliacion semantica,
`feat/constant-copy-propagation`) calcula y persiste `<run_dir>/
diagnostics/semantic-propagation.json`: un artefacto NO contractual,
generado exclusivamente bajo demanda, que demuestra -- de forma
conservadora, intraparrafo y trazable -- cadenas literal->copia y
condicion-88->copia (ver `docs/SEMANTIC_PROPAGATION.md`). Calcula un
`SemanticEffectsArtifact` en memoria (nunca lee ni escribe
`diagnostics/semantic-effects.json`). Requiere unicamente que RUN_ID
haya alcanzado PARSED (SUCCEEDED). Nunca se invoca automaticamente
desde `ingest`/`resume`/la API/la UI; nunca modifica `run.json` ni
ningun artefacto `artifacts/01-10`; esta fase todavia no usa la
propagacion para detectar reglas.

`v2-candidates-shadow` (Fase 5 de la ampliacion semantica,
`feat/v2-detectors-shadow-mode`) calcula y persiste `<run_dir>/
diagnostics/v2-candidates-shadow.json`: un artefacto NO contractual,
generado exclusivamente bajo demanda, que ejecuta detectores V2
experimentales (registry de `pipeline/v2_detector_registry.py`) en
modo shadow y compara sus candidatos contra `CandidateArtifact` V1
(`artifacts/06-candidates.json`) -- ver `docs/V2_DETECTORS_SHADOW_MODE.
md`. Calcula `SemanticEffectsArtifact`/`SemanticPropagationArtifact`
en memoria (nunca lee ni escribe sus diagnostics). Requiere que RUN_ID
haya alcanzado PARSED, SEMANTIC_GRAPH_BUILT y CANDIDATES_DETECTED
(SUCCEEDED). Nunca se invoca automaticamente desde `ingest`/`resume`/
la API/la UI; nunca modifica `run.json`, ningun `artifacts/01-10` ni
ningun `diagnostics/` preexistente; nunca alimenta ContextPackage, el
LLM, los guardrails ni Neo4j; sus candidatos nunca se presentan como
reglas.

`semantic-interprocedural` (Fase 6 de la ampliacion semantica,
`feat/interprocedural-call-linkage-foundation`) calcula y persiste
`<run_dir>/diagnostics/interprocedural-call-linkage.json`: un artefacto
NO contractual, generado exclusivamente bajo demanda, que construye una
representacion interprocedural paralela y diagnostica -- interfaces de
programa (LINKAGE SECTION/PROCEDURE DIVISION USING/RETURNING), call
sites de CALL resueltos contra el paquete, bindings actual-formal
posicionales y el call graph directo con deteccion de recursion/ciclos
-- ver `docs/INTERPROCEDURAL_CALL_LINKAGE.md`. Calcula
`SemanticEffectsArtifact` en memoria (nunca lee ni escribe sus
diagnostics). Requiere unicamente que RUN_ID haya alcanzado PARSED
(SUCCEEDED). Nunca se invoca automaticamente desde `ingest`/`resume`/la
API/la UI; nunca modifica `run.json`, ningun `artifacts/01-10` ni ningun
`diagnostics/` preexistente; nunca accede a Neo4j ni invoca un
proveedor LLM; nunca propaga valores entre programas ni analiza el
cuerpo de un programa invocado; sus call sites/bindings nunca alimentan
`SemanticGraph`, `CandidateArtifact` V1, candidatos V2, `ContextPackage`
ni la generacion de reglas.

`semantic-interprocedural-propagation` (Fase 7 de la ampliacion
semantica, `feat/interprocedural-propagation-shadow`) calcula y persiste
`<run_dir>/diagnostics/interprocedural-propagation.json`: un artefacto
NO contractual, generado exclusivamente bajo demanda, que propaga
valores literales deterministicos ENTRE programas -- apoyado
exclusivamente en `PropagatedValueFact` ya demostrados por
`semantic-propagation` y en la resolucion/bindings ya calculados por
`semantic-interprocedural` -- ver `docs/INTERPROCEDURAL_PROPAGATION.md`.
Solo CALL con target literal, resolucion interna y bindings
suficientemente estructurados; nunca CALL dinamico, programa ausente/
ambiguo, self-call ni miembros de un SCC (bloqueados explicitamente, sin
fixed point). Calcula `SemanticEffectsArtifact`/
`SemanticPropagationArtifact`/`InterproceduralCallLinkageArtifact` en
memoria (nunca lee ni escribe sus diagnostics). Requiere unicamente que
RUN_ID haya alcanzado PARSED (SUCCEEDED). Nunca se invoca
automaticamente desde `ingest`/`resume`/la API/la UI; nunca modifica
`run.json`, ningun `artifacts/01-10` ni ningun `diagnostics/`
preexistente; nunca accede a Neo4j ni invoca un proveedor LLM; su
resultado nunca alimenta `SemanticGraph`, `CandidateArtifact` V1,
candidatos V2, `ContextPackage` ni la generacion de reglas.

`interprocedural-candidates-shadow` (Fase 8 de la ampliacion semantica,
`feat/interprocedural-rule-detectors-shadow`) calcula y persiste
`<run_dir>/diagnostics/interprocedural-rule-candidates-shadow.json`: un
artefacto NO contractual, generado exclusivamente bajo demanda, que
convierte hechos interprocedurales ya demostrados (Fase 6/7) en
candidatos experimentales de regla de negocio -- RETURNING, BY
REFERENCE y transiciones de estado observables entre programas -- ver
docs/INTERPROCEDURAL_RULE_DETECTORS_SHADOW.md. Calcula
`SemanticEffectsArtifact`/`SemanticPropagationArtifact`/
`InterproceduralCallLinkageArtifact`/`InterproceduralPropagationArtifact`
en memoria (nunca lee ni escribe sus diagnostics); carga opcionalmente
`artifacts/06-candidates.json` (V1), `artifacts/04-semantic-graph.json`
(unicamente para derivar V2 en memoria) y
`artifacts/03b-semantic-enrichment.json` (unicamente para transiciones
de estado) -- su ausencia nunca es un error. Requiere unicamente que
RUN_ID haya alcanzado PARSED (SUCCEEDED). Nunca se invoca
automaticamente desde `ingest`/`resume`/la API/la UI; nunca modifica
`run.json`, ningun `artifacts/01-10`, `CandidateArtifact` V1 ni
`SemanticGraph`; nunca accede a Neo4j ni invoca un proveedor LLM; sus
candidatos nunca alimentan `ContextPackage`, el LLM, los guardrails, ni
se presentan como regla aprobada.

`candidate-promotion-assessment` (Fase 9 de la ampliacion semantica,
`feat/unified-candidate-promotion-assessment`) calcula y persiste
`<run_dir>/diagnostics/candidate-promotion-assessment.json`: un
artefacto NO contractual, generado exclusivamente bajo demanda, que
cataloga -- sin fusionar ni modificar -- los candidatos de las tres
superficies existentes (`CandidateArtifact` V1, `V2ShadowCandidatesArtifact`,
`InterproceduralRuleCandidatesArtifact`), identifica equivalencias
exactas y relaciones parciales, detecta conflictos demostrables, y
evalua criterios de preparacion para una eventual promocion -- ver
docs/CANDIDATE_PROMOTION_ASSESSMENT.md. Deriva V1/V2/interprocedural en
memoria (nunca lee ni escribe sus propios diagnostics); requiere
unicamente que RUN_ID haya alcanzado PARSED (SUCCEEDED); la ausencia de
cualquiera de las tres fuentes nunca es un error (se marca
`NOT_AVAILABLE`, nunca se fabrica un artefacto vacio). Nunca modifica
`run.json`, ningun `artifacts/01-10`, `CandidateArtifact` V1,
`V2ShadowCandidatesArtifact` ni `InterproceduralRuleCandidatesArtifact`;
nunca accede a Neo4j directamente ni invoca un proveedor LLM; nunca
promueve ningun candidato -- `READY_FOR_CONTROLLED_REVIEW` es
exclusivamente diagnostico, nunca una aprobacion.

`candidate-promotion-review-package` (Fase 10 de la ampliacion
semantica, `feat/controlled-candidate-promotion-plan`) calcula y
persiste `<run_dir>/diagnostics/candidate-promotion-review-package.
json`: deriva EXCLUSIVAMENTE del `CandidatePromotionAssessmentArtifact`
(Fase 9, calculado en memoria, nunca leido de su propio diagnostico) --
ver docs/CONTROLLED_CANDIDATE_PROMOTION_PLAN.md. Cada candidato
conserva su identidad y provenance completas; la elegibilidad
(`ReviewEligibility`) se deriva unicamente de `PromotionDisposition`,
nunca reinterpretando criterios individuales. Requiere unicamente que
RUN_ID haya alcanzado PARSED (SUCCEEDED). Nunca modifica `run.json`,
ningun `artifacts/01-10` ni `diagnostics/candidate-promotion-
assessment.json`; nunca promueve ningun candidato.

`candidate-promotion-plan` (Fase 10 de la ampliacion semantica) valida
un manifiesto de decisiones HUMANO (`--decisions <path>`, un archivo
externo nunca copiado al repositorio) contra el paquete de revision ya
persistido y el assessment actual, y persiste `<run_dir>/diagnostics/
candidate-promotion-plan.json`: un plan de promocion en DRY-RUN --
`PROPOSE_SHADOW_PROMOTION` nunca implica una promocion real. Cada
decision se valida contra hashes de origen (`review_package_hash`/
`assessment_artifact_hash`/`run_id`); una decision invalida se rechaza
explicitamente, nunca se ignora en silencio. Requiere que el paquete de
revision ya exista (`candidate-promotion-review-package` previo) y
unicamente que RUN_ID haya alcanzado PARSED (SUCCEEDED). Nunca modifica
`run.json`, ningun `artifacts/01-10` ni ningun `diagnostics/`
preexistente; nunca crea un candidato nuevo, nunca genera
`ContextPackage`/`RuleDraft`/reglas Markdown, nunca escribe en Neo4j.

`unified-shadow-validate` (Fase 12 de la ampliacion semantica,
`feat/unified-shadow-differential-validation`) calcula y persiste
`<run_dir>/diagnostics/unified-shadow-validation-report.json`: un
reporte NO contractual, generado exclusivamente bajo demanda, que
determina si un `UnifiedCandidatesShadowArtifact` (Fase 11) cumple las
condiciones TECNICAS minimas para alimentar, en una fase posterior, un
flujo downstream tambien en shadow mode -- ver docs/
UNIFIED_SHADOW_DIFFERENTIAL_VALIDATION.md. Distingue SIEMPRE validez
ESTRUCTURAL (verificable automaticamente) de correccion FUNCIONAL
(nunca inferida); `QUALIFIED_FOR_DOWNSTREAM_SHADOW` nunca significa
regla validada, candidato promovido ni autorizacion productiva.
Requiere que RUN_ID haya alcanzado PARSED (SUCCEEDED) Y que
`diagnostics/unified-candidates-shadow.json` exista y sea valido -- el
objeto PRINCIPAL de esta validacion, nunca una fuente opcional: su
ausencia, JSON invalido, version incompatible o contrato Pydantic
invalido es SIEMPRE un error de comando (exit code distinto de cero,
sin reporte generado). La ausencia o invalidez de cualquier fuente
SECUNDARIA (`CandidateArtifact` V1, V2/interprocedural, assessment,
review package, plan) nunca es un error de comando -- se refleja como
un hallazgo representable dentro del reporte (`NOT_EVALUATED`,
`BLOCKED`, `REVIEW_REQUIRED` segun la causa), que igual se genera y
persiste. Nunca modifica `run.json`, ningun `artifacts/01-10` ni
ningun otro `diagnostics/` preexistente; nunca regenera el artefacto
unificado, un plan o una decision humana ausentes; nunca promueve ni
modifica ningun candidato; nunca accede a Neo4j ni invoca un proveedor
LLM.

Opera directamente sobre el filesystem local y las funciones Python
compartidas del pipeline (`pipeline/runner.py`) y de lectura de
artefactos (`api/reads.py`, `api/downloads.py`, `api/validation.py`,
`api/mappers.py`) -- nunca llama a FastAPI por HTTP, y por lo tanto no
requiere que el servidor este' levantado. Esos modulos de `api/` son
Python puro (sin `fastapi`, sin `Depends`/`Request`/`Response`, sin
estado de aplicacion ASGI): reutilizarlos aqui no importa el framework
HTTP, solo su logica de validacion/lectura ya probada -- evita
duplicar esa validacion sin necesitar un paquete `application/` nuevo
(ver docs/ARCHITECTURE.md 3.1, que documenta casos de uso compartidos,
no una clase concreta). `build_rule_response` (Prompt 13d) es la misma
funcion que usan `api/routers/runs.py::get_rule` y la UI (`ui/`): un
unico mapeo `GuardrailCandidateArtifact -> RuleResponse`, nunca
duplicado una segunda vez.
"""

from __future__ import annotations

import functools
import json
import logging
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from .api.downloads import build_rules_zip
from .api.errors import ApiError, ArtifactCorruptedError, RunNotResumableError
from .api.mappers import build_rule_response
from .api.reads import (
    read_candidate_artifact,
    read_context_package,
    read_guardrail_candidate_artifact,
    read_run_state,
    require_stage_succeeded,
)
from .api.validation import validate_candidate_id, validate_run_id
from .config import Settings, load_settings
from .contracts.enums import PipelineStage
from .contracts.functional_dataset_validation import FunctionalDatasetLane
from .contracts.interprocedural_rule_candidates import InterproceduralRuleType
from .contracts.run_state import RunState
from .contracts.semantic_coverage import SemanticSupportStatus
from .logging_setup import configure_logging
from .pipeline.active_artifact_resolver import ActiveArtifactResolver
from .pipeline.candidate_promotion_assessment_service import (
    compute_candidate_promotion_assessment_artifact,
    write_candidate_promotion_assessment_artifact,
)
from .pipeline.candidate_promotion_plan_service import (
    compute_candidate_promotion_plan_artifact,
    write_candidate_promotion_plan_artifact,
)
from .pipeline.candidate_promotion_review_service import (
    compute_candidate_promotion_review_package,
    write_candidate_promotion_review_package,
)
from .pipeline.errors import (
    PipelineError,
    UnifiedActivationStoreError,
    UnifiedMaterializationError,
)
from .pipeline.functional_dataset_validation_service import (
    compute_functional_dataset_validation_report,
    write_functional_dataset_validation_report,
)
from .pipeline.functional_validation_service import (
    compute_functional_validation_report,
    write_functional_validation_report,
)
from .pipeline.interprocedural_call_linkage_service import (
    compute_interprocedural_call_linkage_artifact,
    write_interprocedural_call_linkage_artifact,
)
from .pipeline.interprocedural_propagation_service import (
    compute_interprocedural_propagation_artifact,
    write_interprocedural_propagation_artifact,
)
from .pipeline.interprocedural_rule_candidates_service import (
    compute_interprocedural_rule_candidates_artifact,
    write_interprocedural_rule_candidates_artifact,
)
from .pipeline.release_readiness_service import (
    compute_release_readiness_assessment,
    compute_release_readiness_assessment_for_dataset,
    write_dataset_release_readiness_assessment,
    write_release_readiness_assessment,
)
from .pipeline.runner import run_ingestion
from .pipeline.semantic_coverage_observations_service import (
    compute_semantic_coverage_observations,
    write_semantic_coverage_observations,
)
from .pipeline.semantic_coverage_registry import (
    load_semantic_coverage_manifest,
    reconcile_manifest,
)
from .pipeline.semantic_coverage_service import (
    compute_semantic_coverage_report,
    write_semantic_coverage_report,
)
from .pipeline.semantic_effects_service import (
    compute_semantic_effects_artifact,
    write_semantic_effects_artifact,
)
from .pipeline.semantic_propagation_service import (
    compute_semantic_propagation_artifact,
    write_semantic_propagation_artifact,
)
from .pipeline.unified_activation_service import (
    compute_unified_activation_evaluation,
    write_unified_activation_evaluation,
)
from .pipeline.unified_active_lane_router import KNOWN_LOGICAL_NAMES
from .pipeline.unified_candidates_shadow_service import (
    compute_unified_candidates_shadow_artifact,
    write_unified_candidates_shadow_artifact,
)
from .pipeline.unified_materialization_service import (
    action_is_rollback,
    materialize_unified_activation,
    peek_authorization_action,
)
from .pipeline.unified_shadow_downstream_service import (
    compute_unified_shadow_downstream_artifact,
    write_unified_shadow_downstream_artifact,
)
from .pipeline.unified_shadow_validation_service import (
    compute_unified_shadow_validation_report,
    write_unified_shadow_validation_report,
)
from .pipeline.v2_shadow_candidates_service import (
    compute_v2_shadow_candidates_artifact,
    write_v2_shadow_candidates_artifact,
)

logger = logging.getLogger(__name__)

app = typer.Typer(add_completion=False, help="Altamira Rule Extractor - pipeline de ingesta")


@app.callback()
def _bootstrap_logging() -> None:
    """Configura logging JSON estructurado antes de cualquier comando
    (Fase 15B2-B). `configure_logging` es idempotente (limpia handlers
    previos en cada llamada), asi que invocarlo aqui es seguro incluso
    si un comando individual lo invocara de nuevo."""
    configure_logging(load_settings().log_level)


PackageArgument = Annotated[
    Path,
    typer.Argument(
        exists=True,
        dir_okay=False,
        readable=True,
        help="Ruta al paquete .zip Altamira a ingerir",
    ),
]
RunIdOption = Annotated[
    str | None,
    typer.Option("--run-id", help="Reanuda un run_id existente en vez de crear uno nuevo"),
]
RunIdArgument = Annotated[str, typer.Argument(help="Identificador del run (run_id)")]
CandidateIdArgument = Annotated[
    str, typer.Argument(help="Identificador del candidato (candidate_id)")
]
LimitOption = Annotated[int, typer.Option(min=1, max=100, help="Cantidad maxima de runs a listar")]
OffsetOption = Annotated[
    int, typer.Option(min=0, help="Cantidad de runs a omitir desde el mas reciente")
]
OutputOption = Annotated[
    Path | None,
    typer.Option("--output", help="Destino del ZIP (default: ./RUN_ID-rules.zip)"),
]
SemanticCoverageJsonOption = Annotated[
    bool,
    typer.Option("--json", help="Imprime el SemanticCoverageReport completo en JSON estable"),
]
SemanticCoverageObservationsJsonOption = Annotated[
    bool,
    typer.Option(
        "--json", help="Imprime el SemanticCoverageObservationsArtifact completo en JSON estable"
    ),
]
FunctionalValidationJsonOption = Annotated[
    bool,
    typer.Option("--json", help="Imprime el FunctionalValidationReport completo en JSON estable"),
]
DatasetIdOption = Annotated[
    str,
    typer.Option(
        "--dataset",
        help="dataset_id logico CERRADO (ver functional_dataset_validation_service.py); "
        "nunca una ruta",
    ),
]
DatasetLaneOption = Annotated[
    FunctionalDatasetLane,
    typer.Option("--lane", help="Superficie de candidatos agregada (motor cerrado)"),
]
DatasetRunIdsOption = Annotated[
    list[str],
    typer.Option("--run-id", help="run_id a incluir en la agregacion (repetible, minimo 1)"),
]
FunctionalDatasetValidationJsonOption = Annotated[
    bool,
    typer.Option(
        "--json", help="Imprime el FunctionalDatasetValidationReport completo en JSON estable"
    ),
]
ReleaseReadinessJsonOption = Annotated[
    bool,
    typer.Option("--json", help="Imprime el ReleaseReadinessAssessment completo en JSON estable"),
]
DatasetReleaseReadinessJsonOption = Annotated[
    bool,
    typer.Option(
        "--json", help="Imprime el DatasetReleaseReadinessAssessment completo en JSON estable"
    ),
]
SemanticEffectsJsonOption = Annotated[
    bool,
    typer.Option("--json", help="Imprime el SemanticEffectsArtifact completo en JSON estable"),
]
SemanticPropagationJsonOption = Annotated[
    bool,
    typer.Option("--json", help="Imprime el SemanticPropagationArtifact completo en JSON estable"),
]
V2ShadowJsonOption = Annotated[
    bool,
    typer.Option("--json", help="Imprime el V2ShadowCandidatesArtifact completo en JSON estable"),
]
InterproceduralJsonOption = Annotated[
    bool,
    typer.Option(
        "--json", help="Imprime el InterproceduralCallLinkageArtifact completo en JSON estable"
    ),
]
InterproceduralPropagationJsonOption = Annotated[
    bool,
    typer.Option(
        "--json", help="Imprime el InterproceduralPropagationArtifact completo en JSON estable"
    ),
]
InterproceduralRuleCandidatesJsonOption = Annotated[
    bool,
    typer.Option(
        "--json",
        help="Imprime el InterproceduralRuleCandidatesArtifact completo en JSON estable",
    ),
]
CandidatePromotionAssessmentJsonOption = Annotated[
    bool,
    typer.Option(
        "--json",
        help="Imprime el CandidatePromotionAssessmentArtifact completo en JSON estable",
    ),
]
CandidatePromotionReviewPackageJsonOption = Annotated[
    bool,
    typer.Option(
        "--json",
        help="Imprime el CandidatePromotionReviewPackage completo en JSON estable",
    ),
]
CandidatePromotionPlanJsonOption = Annotated[
    bool,
    typer.Option(
        "--json",
        help="Imprime el CandidatePromotionPlanArtifact completo en JSON estable",
    ),
]
CandidatePromotionPlanDecisionsOption = Annotated[
    str,
    typer.Option(
        "--decisions",
        help="Ruta al manifiesto de decisiones humano (CandidatePromotionDecisionManifest)",
    ),
]
UnifiedCandidatesShadowJsonOption = Annotated[
    bool,
    typer.Option(
        "--json",
        help="Imprime el UnifiedCandidatesShadowArtifact completo en JSON estable",
    ),
]
UnifiedShadowValidationJsonOption = Annotated[
    bool,
    typer.Option(
        "--json",
        help="Imprime el UnifiedShadowValidationReport completo en JSON estable",
    ),
]
UnifiedShadowDownstreamJsonOption = Annotated[
    bool,
    typer.Option(
        "--json",
        help="Imprime el UnifiedShadowDownstreamArtifact completo en JSON estable",
    ),
]
UnifiedActivationConfigOption = Annotated[
    str,
    typer.Option(
        "--config",
        help="Ruta al YAML de UnifiedActivationConfig (externo, nunca copiado al repositorio "
        "ni al run)",
    ),
]
UnifiedActivationJsonOption = Annotated[
    bool,
    typer.Option(
        "--json",
        help="Imprime el UnifiedActivationEvaluationArtifact completo en JSON estable",
    ),
]
UnifiedMaterializationAuthorizationOption = Annotated[
    str,
    typer.Option(
        "--authorization",
        help="Ruta al YAML de UnifiedMaterializationAuthorization (externo, nunca copiado al "
        "repositorio ni al run)",
    ),
]
UnifiedMaterializationJsonOption = Annotated[
    bool,
    typer.Option("--json", help="Imprime el resultado completo en JSON estable"),
]
UnifiedActivationResolveArtifactOption = Annotated[
    str,
    typer.Option(
        "--artifact",
        help=f"Logical name a resolver (uno de: {', '.join(sorted(KNOWN_LOGICAL_NAMES))})",
    ),
]

# Exit codes estables (Prompt 13c). Mapeados por `ApiError.code` (nunca por
# texto de mensaje): "resource not found" -> 3, "etapa/estado no valido
# para la operacion" -> 4, "artefacto corrupto" -> 5. Ningun otro `code`
# de ApiError es alcanzable desde el CLI (RunAlreadyActiveError,
# ExecutorAtCapacityError, EmptyUploadError, UploadTooLargeError,
# ServiceUnavailableError son exclusivos de la API HTTP/upload/executor,
# que el CLI no usa) -- si alguno llegara a ocurrir de todos modos, cae al
# 1 generico por seguridad.
_EXIT_CODE_BY_ERROR_CODE: dict[str, int] = {
    "run_not_found": 3,
    "candidate_not_found": 3,
    "stage_not_reached": 4,
    "run_not_resumable": 4,
    "artifact_corrupted": 5,
    "invalid_identifier": 2,
}


class _UsageError(Exception):
    """Error de uso local del CLI (p. ej. `--output` invalido) que no
    corresponde a ningun `ApiError` compartido con la API. Mapea a exit
    code 2, igual que un argumento invalido detectado por Typer/Click."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _guard[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Limite exterior comun de cada comando: nunca deja escapar un
    stacktrace, un path absoluto interno o un mensaje sin sanitizar, y
    traduce cada situacion anticipada a un exit code estable (ver tabla
    de `_EXIT_CODE_BY_ERROR_CODE`). `typer.Exit` (usado por `ingest`/
    `resume` para el exit code 1 de un pipeline FAILED) se re-lanza sin
    tocar: este wrapper solo interviene en rutas de error no manejadas
    explicitamente por el comando.

    Ninguna rama de este wrapper adjunta `exc_info` a un `LogRecord`
    (nunca `logger.exception(...)`, nunca `exc_info=True`): aunque desde
    la Fase 15B2-B el entrypoint CLI SI configura un handler JSON
    (`logging_setup.configure_logging`, invocado por el callback
    `_bootstrap_logging`), un traceback adjunto seguiria siendo
    informacion interna que este wrapper decide nunca emitir -- el
    unico dato de la excepcion que se loguea es `type(exc).__name__`.
    `PipelineError` (la base comun de TODAS las
    excepciones de dominio del pipeline, Fase 9-12 incluidas) se
    distingue de un error verdaderamente inesperado, pero NUNCA se
    imprime su `str(exc)`: aunque la mayoria de sus subclases
    documentan mensajes sin ruta absoluta, al menos una
    (`AtomicWriteError`) documenta explicitamente incluir una ruta
    destino -- mostrar solo el nombre de la clase es seguro
    independientemente del contenido real del mensaje, hoy y ante
    cualquier subclase futura."""

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except typer.Exit:
            raise
        except _UsageError as exc:
            typer.echo(exc.message, err=True)
            raise typer.Exit(code=2) from exc
        except KeyboardInterrupt:
            typer.echo("Operación interrumpida.", err=True)
            raise typer.Exit(code=130) from None
        except ApiError as exc:
            typer.echo(exc.message, err=True)
            raise typer.Exit(code=_EXIT_CODE_BY_ERROR_CODE.get(exc.code, 1)) from exc
        except PipelineError as exc:
            logger.error("error de dominio en comando CLI: %s", type(exc).__name__)
            typer.echo(f"error: {type(exc).__name__}", err=True)
            raise typer.Exit(code=1) from None
        except Exception:
            logger.error("error interno inesperado en comando CLI")
            typer.echo("error interno", err=True)
            raise typer.Exit(code=1) from None

    return wrapper


def _run_dir(settings: Settings, run_id: str) -> Path:
    validate_run_id(run_id)
    return settings.runs_dir / run_id


def _print_ingestion_summary(state: RunState) -> None:
    typer.echo(f"run_id: {state.run_id}")
    typer.echo(f"stage: {state.current_stage.value}")
    for stage in state.stages:
        line = f"  {stage.stage.value}: {stage.status.value}"
        if stage.error:
            line += f" ({stage.error})"
        typer.echo(line)


def _resolve_download_destination(run_id: str, output: Path | None) -> Path:
    destination = output if output is not None else Path(f"{run_id}-rules.zip")
    if destination.is_dir():
        raise _UsageError(f"el destino no puede ser un directorio: {destination}")
    if destination.exists():
        raise _UsageError(f"el destino ya existe, no se sobrescribe: {destination}")
    parent = destination.parent
    if not parent.is_dir():
        raise _UsageError(f"el directorio padre del destino no existe: {parent}")
    return destination


@app.command()
@_guard
def ingest(package: PackageArgument, run_id: RunIdOption = None) -> None:
    """Ejecuta el pipeline completo (RECEIVED -> ... -> COMPLETED o FAILED) sobre PACKAGE."""
    settings = load_settings()
    state = run_ingestion(source_zip=package, settings=settings, run_id=run_id)
    _print_ingestion_summary(state)

    if state.current_stage == PipelineStage.FAILED:
        raise typer.Exit(code=1)


@app.command()
@_guard
def resume(run_id: RunIdArgument) -> None:
    """Reanuda RUN_ID de forma bloqueante, desde la ultima etapa valida.

    Limitacion V1: el CLI no coordina locks con un proceso FastAPI que
    pudiera estar ejecutando el mismo run (no hay `RunExecutor`, no hay
    `api_max_workers` aqui: el CLI es un unico proceso y comando). El
    operador no debe ejecutar `resume` localmente mientras el mismo run
    este activo via la API.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)

    if state.current_stage == PipelineStage.COMPLETED:
        raise RunNotResumableError()

    input_zip_path = run_dir / "input" / "package.zip"
    if input_zip_path.is_symlink() or not input_zip_path.is_file():
        raise ArtifactCorruptedError("input/package.zip ausente, no regular o symlink")

    state = run_ingestion(input_zip_path, settings, run_id=run_id)
    _print_ingestion_summary(state)

    if state.current_stage == PipelineStage.FAILED:
        raise typer.Exit(code=1)


@app.command(name="runs")
@_guard
def list_runs(limit: LimitOption = 20, offset: OffsetOption = 0) -> None:
    """Lista runs (mas recientes primero), igual orden que `GET /api/runs`."""
    settings = load_settings()
    states: list[RunState] = []
    if settings.runs_dir.is_dir():
        for entry in settings.runs_dir.iterdir():
            if entry.is_symlink() or not entry.is_dir():
                continue
            try:
                states.append(read_run_state(entry))
            except ApiError:
                logger.warning("run corrupto omitido del listado: run_id=%s", entry.name)
                continue

    if not states:
        typer.echo("No hay ejecuciones registradas.")
        return

    states.sort(key=lambda s: s.run_id, reverse=True)
    for state in states[offset : offset + limit]:
        hash_display = state.source_package_hash or "No informado"
        typer.echo(f"{state.run_id}\t{state.current_stage.value}\t{hash_display}")


@app.command()
@_guard
def status(run_id: RunIdArgument) -> None:
    """Muestra el `RunState` persistido de RUN_ID tal cual (sin reinterpretar etapas)."""
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)

    typer.echo(f"run_id: {state.run_id}")
    typer.echo(f"current_stage: {state.current_stage.value}")
    typer.echo(f"package_filename: {state.package_filename}")
    typer.echo(f"source_package_hash: {state.source_package_hash or 'No informado'}")
    for stage_execution in state.stages:
        typer.echo(f"  {stage_execution.stage.value}: {stage_execution.status.value}")
        started_at = stage_execution.started_at
        finished_at = stage_execution.finished_at
        started = started_at.isoformat() if started_at else "No informado"
        finished = finished_at.isoformat() if finished_at else "No informado"
        typer.echo(f"    started_at: {started}")
        typer.echo(f"    finished_at: {finished}")
        for warning in stage_execution.warnings:
            typer.echo(f"    warning: {warning}")
        if stage_execution.error:
            typer.echo(f"    error: {stage_execution.error}")


@app.command()
@_guard
def candidates(run_id: RunIdArgument) -> None:
    """Lista los candidatos detectados (Q0) de RUN_ID, sin hashes ni provenance."""
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.CANDIDATES_DETECTED)
    artifact = read_candidate_artifact(run_dir, state.source_package_hash)

    if not artifact.candidates:
        typer.echo("No hay candidatos detectados.")
        return

    for candidate in artifact.candidates:
        outcome_code = candidate.outcome_code or "No informado"
        rule_type = candidate.rule_type or "No informado"
        typer.echo(
            f"{candidate.candidate_id}\t{candidate.paragraph_name}\t{candidate.condition}\t"
            f"{outcome_code}\t{rule_type}\t{candidate.status.value}"
        )


@app.command()
@_guard
def context(run_id: RunIdArgument, candidate_id: CandidateIdArgument) -> None:
    """Imprime el ContextPackage validado (D1-D7) de CANDIDATE_ID como JSON indentado."""
    validate_candidate_id(candidate_id)
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.CONTEXTS_BUILT)
    package = read_context_package(run_dir, candidate_id)
    typer.echo(package.model_dump_json(indent=2, exclude_none=False))


@app.command()
@_guard
def rule(run_id: RunIdArgument, candidate_id: CandidateIdArgument) -> None:
    """Imprime la regla propuesta y el veredicto del guardrail de CANDIDATE_ID como JSON.

    Mismo contenido funcional que
    `GET /api/runs/{run_id}/candidates/{candidate_id}/rule`: nunca
    `repair_history`, hashes de provenance, `provider`/`model` ni el
    draft PENDING de `artifacts/08-rule-drafts/` (nunca se lee ese
    directorio).
    """
    validate_candidate_id(candidate_id)
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.GUARDRAILS_APPLIED)
    artifact = read_guardrail_candidate_artifact(run_dir, candidate_id)
    response = build_rule_response(artifact)
    typer.echo(response.model_dump_json(indent=2))


@app.command()
@_guard
def download(run_id: RunIdArgument, output: OutputOption = None) -> None:
    """Descarga el ZIP de reglas finales (artifacts/10-rules/) de RUN_ID a OUTPUT."""
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    state = read_run_state(run_dir)
    require_stage_succeeded(state, PipelineStage.COMPLETED)
    destination = _resolve_download_destination(run_id, output)

    zip_path = build_rules_zip(run_dir, run_id)
    try:
        shutil.move(str(zip_path), str(destination))
    finally:
        if zip_path.exists():
            zip_path.unlink()

    typer.echo(f"Reglas descargadas en: {destination}")


@app.command(name="semantic-coverage")
@_guard
def semantic_coverage(
    run_id: RunIdArgument, json_output: SemanticCoverageJsonOption = False
) -> None:
    """Calcula y persiste diagnostics/semantic-coverage.json de RUN_ID.

    Informe NO contractual, bajo demanda (Fase 1 de la ampliacion
    semantica, ver docs/SEMANTIC_COVERAGE.md): describe cuanto de cada
    CanonicalProgram recibio interpretacion semantica estructurada.
    Requiere que RUN_ID ya haya alcanzado CANDIDATES_DETECTED
    (SUCCEEDED). Nunca modifica run.json ni ningun artifacts/01-10;
    nunca accede a Neo4j ni invoca un proveedor LLM.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    report = compute_semantic_coverage_report(run_dir, run_id)
    report_path = write_semantic_coverage_report(run_dir, report)
    relative_report_path = report_path.relative_to(run_dir).as_posix()

    summary = report.summary
    typer.echo(f"run_id: {report.run_id}")
    typer.echo(f"programs: {summary.program_count}")
    typer.echo(f"statements: {summary.statement_count}")
    typer.echo(f"decisions: {summary.decision_count}")
    typer.echo(
        f"decisions_without_resolved_effect: {summary.decisions_without_resolved_effect_count}"
    )
    typer.echo(f"candidates_q0: {summary.candidate_count}")
    typer.echo(f"level_88_detected: {summary.level_88_data_item_count}")
    typer.echo(f"preserved_only_constructs: {summary.preserved_only_count}")
    typer.echo(f"unsupported_constructs: {summary.unsupported_construct_count}")
    typer.echo(f"report: {relative_report_path}")

    if json_output:
        typer.echo(report.model_dump_json(indent=2, exclude_none=False))


@app.command(name="semantic-coverage-report")
@_guard
def semantic_coverage_report(
    run_id: RunIdArgument, json_output: SemanticCoverageObservationsJsonOption = False
) -> None:
    """Reconcilia config/semantic_coverage.yaml y persiste diagnostics/
    semantic-coverage-observations.json de RUN_ID.

    Dos senales DISTINTAS combinadas en un unico comando (Fase 15B2-A,
    Partes B/C/D, ver docs/SEMANTIC_COVERAGE.md): (1) reconciliacion
    ESTATICA del manifiesto `config/semantic_coverage.yaml` contra los
    registries reales de detectores (`pipeline/semantic_coverage_
    registry.py`, independiente de RUN_ID -- se imprime siempre, nunca
    se persiste por run); (2) observaciones POR-RUN de cuantas veces se
    encontro cada construct_id del catalogo en `artifacts/02-canonical/`
    de RUN_ID (`pipeline/semantic_coverage_observations_service.py`,
    persistidas). Requiere que RUN_ID ya haya alcanzado PARSED
    (SUCCEEDED). Nunca modifica run.json ni ningun artifacts/01-10; nunca
    accede a Neo4j ni invoca un proveedor LLM.
    """
    settings = load_settings()
    manifest = load_semantic_coverage_manifest(settings.semantic_coverage_manifest_path)
    issues = reconcile_manifest(manifest)
    error_issue_count = sum(1 for issue in issues if issue.severity.value == "ERROR")
    typer.echo(f"manifest_edition: {manifest.manifest_edition}")
    typer.echo(f"reconciliation_issues: {len(issues)} (error={error_issue_count})")
    for issue in issues:
        typer.echo(f"  [{issue.severity.value}] {issue.reason_code}: {issue.issue_id}")

    run_dir = _run_dir(settings, run_id)
    observations = compute_semantic_coverage_observations(run_dir, run_id, manifest)
    observations_path = write_semantic_coverage_observations(run_dir, observations)
    relative_observations_path = observations_path.relative_to(run_dir).as_posix()

    summary = observations.summary
    typer.echo(f"run_id: {observations.run_id}")
    typer.echo(f"catalog_constructs: {summary.construct_count}")
    typer.echo(f"observed_constructs: {summary.observed_construct_count}")
    typer.echo(f"unsupported_identities: {summary.unsupported_identity_count}")
    typer.echo(f"mapped_unsupported_identities: {summary.mapped_unsupported_identity_count}")
    typer.echo(f"observations: {relative_observations_path}")

    if json_output:
        typer.echo(observations.model_dump_json(indent=2, exclude_none=False))


@app.command(name="functional-validate")
@_guard
def functional_validate(
    run_id: RunIdArgument, json_output: FunctionalValidationJsonOption = False
) -> None:
    """Calcula y persiste diagnostics/functional-validation-report.json
    de RUN_ID.

    Informe NO contractual, bajo demanda (Fase 15B2-A, Parte F, ver
    docs/FUNCTIONAL_VALIDATION.md): compara config/ground_truth/
    synthetic_engineering.yaml contra los UnifiedCandidateReference
    reales de RUN_ID (CandidatePromotionAssessmentArtifact, Fase 9) con
    matching deterministico y exacto -- nunca LLM, nunca embeddings,
    nunca similitud semantica. Requiere que RUN_ID ya haya alcanzado
    PARSED (SUCCEEDED). Nunca modifica run.json ni ningun
    artifacts/01-10; nunca accede a Neo4j ni invoca un proveedor LLM.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    report = compute_functional_validation_report(run_dir, run_id, settings.ground_truth_path)
    report_path = write_functional_validation_report(run_dir, report)
    relative_report_path = report_path.relative_to(run_dir).as_posix()

    typer.echo(f"run_id: {report.run_id}")
    typer.echo(f"ground_truth_catalog_edition: {report.ground_truth_catalog_edition}")
    typer.echo(f"dataset_applicability: {report.dataset_applicability.value}")
    typer.echo(f"coverage_status: {report.coverage_status.value}")
    typer.echo(
        f"required_cases: {report.evaluated_required_case_count}/{report.required_case_count} "
        f"evaluated  forbidden_cases: "
        f"{report.evaluated_forbidden_case_count}/{report.forbidden_case_count} evaluated"
    )
    if report.pending_case_ids:
        typer.echo(f"pending_case_ids: {', '.join(report.pending_case_ids)}")
    typer.echo(f"dataset_disposition: {report.dataset_disposition.value}")
    for case in report.case_results:
        typer.echo(
            f"  {case.case_id} [{case.kind.value}] applicability={case.applicability.value}: "
            f"{case.outcome.value}"
        )
    metrics = report.metrics
    typer.echo(
        f"tp={metrics.true_positive_count} fn={metrics.false_negative_count} "
        f"fp={metrics.false_positive_count} tn={metrics.true_negative_count}"
    )
    typer.echo(f"precision: {metrics.precision}")
    typer.echo(f"recall: {metrics.recall}")
    typer.echo(f"f1_score: {metrics.f1_score}")
    typer.echo(f"report: {relative_report_path}")

    if json_output:
        typer.echo(report.model_dump_json(indent=2, exclude_none=False))


@app.command(name="functional-validate-dataset")
@_guard
def functional_validate_dataset(
    dataset: DatasetIdOption,
    lane: DatasetLaneOption,
    run_ids: DatasetRunIdsOption,
    json_output: FunctionalDatasetValidationJsonOption = False,
) -> None:
    """Calcula y persiste diagnostics/functional-dataset-validation-
    report.json bajo el PRIMER --run-id (el "ancla" de la invocacion).

    Submodo cerrado (cierre de Fase 15B2-A, Seccion 2/3): agrega N
    FunctionalValidationReport (uno por --run-id, recalculado en
    memoria, nunca releido de un diagnostico persistido) contra el
    catalogo COMPLETO de --dataset (clave logica CERRADA, ver
    functional_dataset_validation_service.py -- nunca una ruta), para
    cubrir casos cuyas fixtures viven en runs/paquetes distintos (p. ej.
    BY_REFERENCE_OUTPUT). --dataset/--lane son motores cerrados; --run-id
    exige el formato exacto de run_id (rechaza cualquier desviacion que
    pudiera interpretarse como ruta). Un FunctionalValidationReport de UN
    SOLO run NUNCA puede afirmar PASS_ENGINEERING del dataset completo --
    ver coverage_status en `functional-validate`. Requiere que cada
    RUN_ID ya haya alcanzado PARSED (SUCCEEDED). Nunca modifica run.json
    ni ningun artifacts/01-10 de ningun run; nunca accede a Neo4j ni
    invoca un proveedor LLM.
    """
    if not run_ids:
        raise _UsageError("se requiere al menos un --run-id")
    settings = load_settings()
    anchor_run_dir = _run_dir(settings, run_ids[0])
    report = compute_functional_dataset_validation_report(
        settings, run_ids, dataset_id=dataset, lane=lane
    )
    report_path = write_functional_dataset_validation_report(anchor_run_dir, report)
    relative_report_path = report_path.relative_to(anchor_run_dir).as_posix()

    typer.echo(f"report_id: {report.report_id}")
    typer.echo(f"dataset_id: {report.dataset_id}")
    typer.echo(f"dataset_version: {report.dataset_version}")
    typer.echo(f"lane: {report.lane.value}")
    typer.echo(f"source_run_ids: {', '.join(report.source_run_ids)}")
    typer.echo(f"coverage_status: {report.coverage_status.value}")
    typer.echo(
        f"required_cases: {report.evaluated_required_case_count}/{report.required_case_count} "
        f"evaluated  forbidden_cases: "
        f"{report.evaluated_forbidden_case_count}/{report.forbidden_case_count} evaluated"
    )
    if report.pending_case_ids:
        typer.echo(f"pending_case_ids: {', '.join(report.pending_case_ids)}")
    if report.duplicate_case_ids:
        typer.echo(f"duplicate_case_ids: {', '.join(report.duplicate_case_ids)}")
    typer.echo(f"dataset_disposition: {report.dataset_disposition.value}")
    for case in report.case_results:
        source = f" (run={case.source_run_id})" if case.source_run_id else ""
        typer.echo(
            f"  {case.case_id} [{case.kind.value}] applicability={case.applicability.value}: "
            f"{case.outcome.value}{source}"
        )
    metrics = report.metrics
    typer.echo(
        f"tp={metrics.true_positive_count} fn={metrics.false_negative_count} "
        f"fp={metrics.false_positive_count} tn={metrics.true_negative_count}"
    )
    typer.echo(f"precision: {metrics.precision}")
    typer.echo(f"recall: {metrics.recall}")
    typer.echo(f"f1_score: {metrics.f1_score}")
    typer.echo(f"report: {relative_report_path}")

    if json_output:
        typer.echo(report.model_dump_json(indent=2, exclude_none=False))


@app.command(name="release-readiness-assess")
@_guard
def release_readiness_assess(
    run_id: RunIdArgument, json_output: ReleaseReadinessJsonOption = False
) -> None:
    """Calcula y persiste diagnostics/release-readiness-assessment.json
    de RUN_ID.

    Informe NO contractual, bajo demanda (Fase 15B2-A, Parte G, ver
    docs/RELEASE_READINESS.md): evalua config/release_readiness_
    policy.yaml contra la reconciliacion estatica del catalogo semantico
    (Parte C) y el FunctionalValidationReport de RUN_ID (Parte F, se
    recalcula aqui -- nunca relee un diagnostics/functional-validation-
    report.json persistido). ALCANCE ACOTADO: `disposition=
    FUNCTIONAL_CRITERIA_MET` NUNCA significa que el release completo esta
    aprobado -- ver docs/RELEASE_READINESS.md para lo que este comando
    deliberadamente NO evalua (Docker/Helm/K3s, backup/restore,
    consolidacion de version). Requiere que RUN_ID ya haya alcanzado
    PARSED (SUCCEEDED). Nunca modifica run.json ni ningun
    artifacts/01-10; nunca accede a Neo4j ni invoca un proveedor LLM.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    assessment = compute_release_readiness_assessment(
        run_dir,
        run_id,
        policy_path=settings.release_readiness_policy_path,
        semantic_coverage_manifest_path=settings.semantic_coverage_manifest_path,
        ground_truth_path=settings.ground_truth_path,
    )
    assessment_path = write_release_readiness_assessment(run_dir, assessment)
    relative_assessment_path = assessment_path.relative_to(run_dir).as_posix()

    typer.echo(f"run_id: {assessment.run_id}")
    typer.echo(f"policy_edition: {assessment.policy_edition}")
    typer.echo(f"disposition: {assessment.disposition.value}")
    typer.echo(f"structural_readiness: {assessment.structural_readiness.value}")
    typer.echo(
        f"engineering_functional_readiness: {assessment.engineering_functional_readiness.value}"
    )
    typer.echo(f"domain_functional_readiness: {assessment.domain_functional_readiness.value}")
    for result in assessment.criteria_results:
        typer.echo(f"  {result.criterion_id} [{result.kind.value}]: {result.status.value}")
    typer.echo(
        f"criteria: {assessment.summary.criterion_count} "
        f"(passed={assessment.summary.passed_count} failed={assessment.summary.failed_count} "
        f"not_evaluated={assessment.summary.not_evaluated_count})"
    )
    for warning in assessment.warnings:
        typer.echo(f"  WARNING [{warning.code.value}]: {warning.message}")
    typer.echo(f"assessment: {relative_assessment_path}")

    if json_output:
        typer.echo(assessment.model_dump_json(indent=2, exclude_none=False))


@app.command(name="release-readiness-assess-dataset")
@_guard
def release_readiness_assess_dataset(
    dataset: DatasetIdOption,
    lane: DatasetLaneOption,
    run_ids: DatasetRunIdsOption,
    json_output: DatasetReleaseReadinessJsonOption = False,
) -> None:
    """Calcula y persiste diagnostics/dataset-release-readiness-
    assessment.json bajo el PRIMER --run-id (el "ancla" de la
    invocacion).

    Variante multi-run de `release-readiness-assess` (cierre de Fase
    15B2-A, Seccion 4): agrega los FunctionalValidationReport de todos
    los --run-id (mismo mecanismo que `functional-validate-dataset`) y
    evalua config/release_readiness_policy.yaml contra el
    FunctionalDatasetValidationReport resultante -- nunca contra un solo
    run aislado. `engineering_functional_readiness=PASSED` exige
    `coverage_status=COMPLETELY_EVALUATED` (todos los casos REQUIRED/
    FORBIDDEN del dataset evaluados en algun run incluido); un run
    aislado con casos pendientes en otro paquete queda `disposition=
    NOT_EVALUATED`, nunca FUNCTIONAL_CRITERIA_MET. ALCANCE ACOTADO: ver
    docs/RELEASE_READINESS.md -- `domain_functional_readiness` nunca
    excede PENDING_DOMAIN_REVIEW. Requiere que cada RUN_ID ya haya
    alcanzado PARSED (SUCCEEDED). Nunca modifica run.json ni ningun
    artifacts/01-10 de ningun run; nunca accede a Neo4j ni invoca un
    proveedor LLM.
    """
    if not run_ids:
        raise _UsageError("se requiere al menos un --run-id")
    settings = load_settings()
    anchor_run_dir = _run_dir(settings, run_ids[0])
    assessment = compute_release_readiness_assessment_for_dataset(
        settings,
        run_ids,
        dataset_id=dataset,
        lane=lane,
        policy_path=settings.release_readiness_policy_path,
        semantic_coverage_manifest_path=settings.semantic_coverage_manifest_path,
    )
    assessment_path = write_dataset_release_readiness_assessment(anchor_run_dir, assessment)
    relative_assessment_path = assessment_path.relative_to(anchor_run_dir).as_posix()

    typer.echo(f"report_id: {assessment.report_id}")
    typer.echo(f"dataset_id: {assessment.dataset_id}")
    typer.echo(f"dataset_version: {assessment.dataset_version}")
    typer.echo(f"policy_edition: {assessment.policy_edition}")
    typer.echo(f"disposition: {assessment.disposition.value}")
    typer.echo(f"structural_readiness: {assessment.structural_readiness.value}")
    typer.echo(
        f"engineering_functional_readiness: {assessment.engineering_functional_readiness.value}"
    )
    typer.echo(f"domain_functional_readiness: {assessment.domain_functional_readiness.value}")
    for result in assessment.criteria_results:
        typer.echo(f"  {result.criterion_id} [{result.kind.value}]: {result.status.value}")
    typer.echo(
        f"criteria: {assessment.summary.criterion_count} "
        f"(passed={assessment.summary.passed_count} failed={assessment.summary.failed_count} "
        f"not_evaluated={assessment.summary.not_evaluated_count})"
    )
    for warning in assessment.warnings:
        typer.echo(f"  WARNING [{warning.code.value}]: {warning.message}")
    typer.echo(f"assessment: {relative_assessment_path}")

    if json_output:
        typer.echo(assessment.model_dump_json(indent=2, exclude_none=False))


@app.command(name="semantic-effects")
@_guard
def semantic_effects(run_id: RunIdArgument, json_output: SemanticEffectsJsonOption = False) -> None:
    """Calcula y persiste diagnostics/semantic-effects.json de RUN_ID.

    Artefacto NO contractual, bajo demanda (Fase 2 de la ampliacion
    semantica, ver docs/SEMANTIC_EFFECTS.md): traduce cada
    CanonicalStatement interpretado a un SemanticEffect normalizado,
    sin propagar valores entre sentencias. Requiere que RUN_ID ya haya
    alcanzado PARSED (SUCCEEDED). Nunca modifica run.json ni ningun
    artifacts/01-10; nunca accede a Neo4j ni invoca un proveedor LLM.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    artifact = compute_semantic_effects_artifact(run_dir, run_id)
    artifact_path = write_semantic_effects_artifact(run_dir, artifact)
    relative_artifact_path = artifact_path.relative_to(run_dir).as_posix()

    summary = artifact.summary
    typer.echo(f"run_id: {artifact.run_id}")
    typer.echo(f"programs: {summary.program_count}")
    typer.echo(f"effects_total: {summary.effect_count}")
    for kind in sorted(summary.counts_by_kind, key=lambda item: item.value):
        typer.echo(f"  {kind.value}: {summary.counts_by_kind[kind]}")
    status_counts = summary.counts_by_support_status
    typer.echo(f"fully_supported: {status_counts.get(SemanticSupportStatus.FULLY_SUPPORTED, 0)}")
    typer.echo(
        f"partially_supported: {status_counts.get(SemanticSupportStatus.PARTIALLY_SUPPORTED, 0)}"
    )
    typer.echo(f"preserved: {status_counts.get(SemanticSupportStatus.PRESERVED_ONLY, 0)}")
    typer.echo(f"unsupported: {status_counts.get(SemanticSupportStatus.UNSUPPORTED, 0)}")
    typer.echo(f"report: {relative_artifact_path}")

    if json_output:
        typer.echo(artifact.model_dump_json(indent=2, exclude_none=False))


@app.command(name="semantic-propagation")
@_guard
def semantic_propagation(
    run_id: RunIdArgument, json_output: SemanticPropagationJsonOption = False
) -> None:
    """Calcula y persiste diagnostics/semantic-propagation.json de RUN_ID.

    Artefacto NO contractual, bajo demanda (Fase 4 de la ampliacion
    semantica, ver docs/SEMANTIC_PROPAGATION.md): demuestra, dentro de un
    unico paragraph, cadenas literal->copia (p. ej. `MOVE '0005' TO
    WS-COD-AUX` seguido de `MOVE WS-COD-AUX TO WS-COD-RETORNO`) y
    condicion-88->copia, de forma conservadora y trazable. Requiere que
    RUN_ID ya haya alcanzado PARSED (SUCCEEDED). Nunca modifica run.json,
    ningun artifacts/01-10, ni diagnostics/semantic-coverage.json/
    diagnostics/semantic-effects.json; nunca accede a Neo4j ni invoca un
    proveedor LLM; esta fase todavia no usa la propagacion para detectar
    reglas.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    artifact = compute_semantic_propagation_artifact(run_dir, run_id)
    artifact_path = write_semantic_propagation_artifact(run_dir, artifact)
    relative_artifact_path = artifact_path.relative_to(run_dir).as_posix()

    summary = artifact.summary
    typer.echo(f"run_id: {artifact.run_id}")
    typer.echo(f"programs: {summary.program_count}")
    typer.echo(f"paragraphs: {summary.paragraph_count}")
    typer.echo(f"facts_total: {summary.fact_count}")
    typer.echo(f"direct_literal: {summary.direct_literal_count}")
    typer.echo(f"propagated_literal: {summary.propagated_literal_count}")
    typer.echo(f"condition_literal: {summary.condition_literal_count}")
    typer.echo(f"unresolved_copy: {summary.unresolved_copy_count}")
    typer.echo(f"invalidated: {summary.invalidated_count}")
    typer.echo(f"blocked: {summary.blocked_count}")
    typer.echo(f"barriers: {summary.barrier_count}")
    typer.echo(f"report: {relative_artifact_path}")

    if json_output:
        typer.echo(artifact.model_dump_json(indent=2, exclude_none=False))


@app.command(name="semantic-interprocedural-propagation")
@_guard
def semantic_interprocedural_propagation(
    run_id: RunIdArgument, json_output: InterproceduralPropagationJsonOption = False
) -> None:
    """Calcula y persiste diagnostics/interprocedural-propagation.json de RUN_ID.

    Artefacto NO contractual, bajo demanda (Fase 7 de la ampliacion
    semantica, ver docs/INTERPROCEDURAL_PROPAGATION.md): propaga valores
    literales deterministicos entre programas, apoyado exclusivamente en
    PropagatedValueFact ya demostrados por SemanticPropagation y en la
    resolucion/bindings ya calculados por el analisis interprocedural
    CALL/LINKAGE. Solo CALL con target literal, resolucion interna y
    bindings suficientemente estructurados; CALL dinamico, programa
    ausente/ambiguo, self-call y miembros de un SCC quedan bloqueados
    explicitamente, sin fixed point sobre ciclos. Calcula
    SemanticEffectsArtifact/SemanticPropagationArtifact/
    InterproceduralCallLinkageArtifact en memoria (nunca lee ni escribe
    sus diagnostics). Requiere que RUN_ID ya haya alcanzado PARSED
    (SUCCEEDED). Nunca modifica run.json, ningun artifacts/01-10 ni
    ningun diagnostics/ preexistente; nunca accede a Neo4j ni invoca un
    proveedor LLM; su contenido nunca alimenta SemanticGraph, candidatos
    V1/V2, ContextPackage ni la generacion de reglas.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    artifact = compute_interprocedural_propagation_artifact(run_dir, run_id)
    artifact_path = write_interprocedural_propagation_artifact(run_dir, artifact)
    relative_artifact_path = artifact_path.relative_to(run_dir).as_posix()

    summary = artifact.summary
    typer.echo(f"run_id: {artifact.run_id}")
    typer.echo(f"programs: {summary.program_count}")
    typer.echo(f"call_sites: {summary.call_site_count}")
    typer.echo(f"eligible_calls: {summary.eligible_call_count}")
    typer.echo(f"propagated_calls: {summary.propagated_call_count}")
    typer.echo(f"blocked_calls: {summary.blocked_call_count}")
    typer.echo(f"entry_facts: {summary.entry_fact_count}")
    typer.echo(f"returning_facts: {summary.returning_fact_count}")
    typer.echo(f"by_reference_outputs: {summary.by_reference_output_count}")
    typer.echo(f"invalidations: {summary.invalidation_count}")
    typer.echo(f"report: {relative_artifact_path}")

    if json_output:
        typer.echo(artifact.model_dump_json(indent=2, exclude_none=False))


@app.command(name="interprocedural-candidates-shadow")
@_guard
def interprocedural_candidates_shadow(
    run_id: RunIdArgument, json_output: InterproceduralRuleCandidatesJsonOption = False
) -> None:
    """Calcula y persiste diagnostics/interprocedural-rule-candidates-shadow.json de RUN_ID.

    Artefacto NO contractual, bajo demanda (Fase 8 de la ampliacion
    semantica, ver docs/INTERPROCEDURAL_RULE_DETECTORS_SHADOW.md):
    ejecuta detectores experimentales que convierten hechos
    interprocedurales ya demostrados (Fase 6/7: RETURNING, BY REFERENCE,
    transiciones de estado observables entre programas) en candidatos de
    regla de negocio, con proveniencia completa caller->call
    site->binding->callee->output->caller, y los compara de forma
    conservadora contra CandidateArtifact V1 y V2ShadowCandidatesArtifact
    (ambos opcionales, cargados de solo lectura). Calcula
    SemanticEffectsArtifact/SemanticPropagationArtifact/
    InterproceduralCallLinkageArtifact/InterproceduralPropagationArtifact
    en memoria (nunca lee ni escribe sus diagnostics). Requiere que
    RUN_ID ya haya alcanzado PARSED (SUCCEEDED); la ausencia de
    artifacts/06-candidates.json, artifacts/04-semantic-graph.json o
    artifacts/03b-semantic-enrichment.json nunca es un error, solo reduce
    la comparacion V1/V2 y/o deshabilita las transiciones de estado.
    Nunca modifica run.json, ningun artifacts/01-10, CandidateArtifact V1
    ni SemanticGraph; nunca accede a Neo4j ni invoca un proveedor LLM;
    sus candidatos nunca alimentan ContextPackage, el LLM, los
    guardrails, ni se presentan como regla aprobada.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    artifact = compute_interprocedural_rule_candidates_artifact(run_dir, run_id)
    artifact_path = write_interprocedural_rule_candidates_artifact(run_dir, artifact)
    relative_artifact_path = artifact_path.relative_to(run_dir).as_posix()

    summary = artifact.summary
    typer.echo(f"run_id: {artifact.run_id}")
    typer.echo(f"candidates_total: {summary.candidate_count}")
    typer.echo(f"deterministic: {summary.deterministic_count}")
    typer.echo(f"partial: {summary.partial_count}")
    typer.echo(f"blocked: {summary.blocked_count}")
    for rule_type in sorted(InterproceduralRuleType, key=lambda item: item.value):
        typer.echo(f"  {rule_type.value}: {summary.counts_by_rule_type.get(rule_type, 0)}")
    typer.echo(f"matched_v1: {summary.matched_v1_count}")
    typer.echo(f"matched_v2: {summary.matched_v2_count}")
    typer.echo(f"related_v1: {summary.related_v1_count}")
    typer.echo(f"related_v2: {summary.related_v2_count}")
    typer.echo(f"interprocedural_only: {summary.interprocedural_only_count}")
    typer.echo(f"not_evaluated: {summary.not_evaluated_count}")
    typer.echo(f"report: {relative_artifact_path}")

    if json_output:
        typer.echo(artifact.model_dump_json(indent=2, exclude_none=False))


@app.command(name="candidate-promotion-assessment")
@_guard
def candidate_promotion_assessment(
    run_id: RunIdArgument, json_output: CandidatePromotionAssessmentJsonOption = False
) -> None:
    """Calcula y persiste diagnostics/candidate-promotion-assessment.json de RUN_ID.

    Artefacto NO contractual, bajo demanda (Fase 9 de la ampliacion
    semantica, ver docs/CANDIDATE_PROMOTION_ASSESSMENT.md): cataloga --
    sin fusionar ni modificar -- los candidatos de las tres superficies
    existentes (CandidateArtifact V1, V2ShadowCandidatesArtifact,
    InterproceduralRuleCandidatesArtifact), identifica equivalencias
    exactas y relaciones parciales, detecta conflictos demostrables, y
    evalua criterios de preparacion para una eventual promocion -- nunca
    promueve ningun candidato. Deriva V1/V2/interprocedural en memoria
    (nunca lee ni escribe sus propios diagnostics). Requiere que RUN_ID
    ya haya alcanzado PARSED (SUCCEEDED); la ausencia de cualquiera de
    las tres fuentes nunca es un error (se marca NOT_AVAILABLE, se
    continua con las fuentes restantes). Nunca modifica run.json, ningun
    artifacts/01-10, CandidateArtifact V1, V2ShadowCandidatesArtifact ni
    InterproceduralRuleCandidatesArtifact; nunca accede a Neo4j
    directamente ni invoca un proveedor LLM; nunca genera ContextPackage,
    RuleDraft ni reglas Markdown.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    artifact = compute_candidate_promotion_assessment_artifact(run_dir, run_id)
    artifact_path = write_candidate_promotion_assessment_artifact(run_dir, artifact)
    relative_artifact_path = artifact_path.relative_to(run_dir).as_posix()

    summary = artifact.summary
    typer.echo(f"run_id: {artifact.run_id}")
    typer.echo(f"v1_candidates: {summary.v1_candidate_count}")
    typer.echo(f"v2_candidates: {summary.v2_candidate_count}")
    typer.echo(f"interprocedural_candidates: {summary.interprocedural_candidate_count}")
    typer.echo(f"references_total: {summary.unified_reference_count}")
    typer.echo(f"exact_matches: {summary.exact_match_relation_count}")
    typer.echo(f"related: {summary.related_relation_count}")
    typer.echo(f"conflicts: {summary.conflict_count}")
    typer.echo(f"baseline_v1: {summary.baseline_v1_count}")
    typer.echo(f"already_covered: {summary.already_covered_count}")
    typer.echo(f"ready_for_controlled_review: {summary.ready_for_controlled_review_count}")
    typer.echo(f"review_required: {summary.review_required_count}")
    typer.echo(f"blocked: {summary.blocked_count}")
    typer.echo(f"conflicting: {summary.conflicting_count}")
    typer.echo(f"not_evaluated: {summary.not_evaluated_count}")
    for source in sorted(summary.source_availability, key=lambda item: item.value):
        typer.echo(f"  availability[{source.value}]: {summary.source_availability[source].value}")
    typer.echo(f"report: {relative_artifact_path}")

    if json_output:
        typer.echo(artifact.model_dump_json(indent=2, exclude_none=False))


@app.command(name="candidate-promotion-review-package")
@_guard
def candidate_promotion_review_package(
    run_id: RunIdArgument, json_output: CandidatePromotionReviewPackageJsonOption = False
) -> None:
    """Calcula y persiste diagnostics/candidate-promotion-review-package.json de RUN_ID.

    Artefacto NO contractual, bajo demanda (Fase 10 de la ampliacion
    semantica, ver docs/CONTROLLED_CANDIDATE_PROMOTION_PLAN.md): deriva
    EXCLUSIVAMENTE del CandidatePromotionAssessmentArtifact (Fase 9,
    calculado en memoria, nunca leido de su propio diagnostico) un
    paquete de revision humana -- una CandidateReviewItem por cada
    candidato evaluado, con su ReviewEligibility derivada unicamente de
    PromotionDisposition. Requiere que RUN_ID ya haya alcanzado PARSED
    (SUCCEEDED). Nunca modifica run.json, ningun artifacts/01-10 ni
    diagnostics/candidate-promotion-assessment.json; nunca promueve
    ningun candidato.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    package = compute_candidate_promotion_review_package(run_dir, run_id)
    package_path = write_candidate_promotion_review_package(run_dir, package)
    relative_package_path = package_path.relative_to(run_dir).as_posix()

    summary = package.summary
    typer.echo(f"run_id: {package.run_id}")
    typer.echo(f"total: {summary.total_items}")
    typer.echo(f"eligible: {summary.eligible_count}")
    typer.echo(f"not_eligible: {summary.not_eligible_count}")
    typer.echo(f"already_covered: {summary.already_covered_count}")
    typer.echo(f"baseline: {summary.baseline_count}")
    typer.echo(f"blocked: {summary.blocked_count}")
    typer.echo(f"report: {relative_package_path}")

    if json_output:
        typer.echo(package.model_dump_json(indent=2, exclude_none=False))


@app.command(name="candidate-promotion-plan")
@_guard
def candidate_promotion_plan(
    run_id: RunIdArgument,
    decisions: CandidatePromotionPlanDecisionsOption,
    json_output: CandidatePromotionPlanJsonOption = False,
) -> None:
    """Calcula y persiste diagnostics/candidate-promotion-plan.json de RUN_ID.

    Artefacto NO contractual, bajo demanda (Fase 10 de la ampliacion
    semantica, ver docs/CONTROLLED_CANDIDATE_PROMOTION_PLAN.md): valida
    --decisions (un manifiesto de decisiones HUMANO, archivo externo,
    nunca copiado al repositorio) contra diagnostics/candidate-
    promotion-review-package.json (debe existir de antemano) y el
    assessment actual (Fase 9, en memoria), y produce un plan de
    promocion en DRY-RUN. PROPOSE_SHADOW_PROMOTION nunca implica una
    promocion real. Cada decision se valida contra hashes de origen; una
    decision invalida se rechaza explicitamente, nunca se ignora en
    silencio. Requiere que RUN_ID ya haya alcanzado PARSED (SUCCEEDED) y
    que el paquete de revision ya exista. Nunca modifica run.json,
    ningun artifacts/01-10 ni ningun diagnostics/ preexistente; nunca
    crea un candidato nuevo, nunca genera ContextPackage/RuleDraft/
    reglas Markdown, nunca escribe en Neo4j.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    artifact = compute_candidate_promotion_plan_artifact(run_dir, run_id, decisions_path=decisions)
    artifact_path = write_candidate_promotion_plan_artifact(run_dir, artifact)
    relative_artifact_path = artifact_path.relative_to(run_dir).as_posix()

    summary = artifact.summary
    typer.echo(f"run_id: {artifact.run_id}")
    typer.echo(f"total: {summary.total_items}")
    typer.echo(f"keep_baseline: {summary.keep_baseline_count}")
    typer.echo(f"skip_already_covered: {summary.skip_already_covered_count}")
    typer.echo(f"propose_shadow_promotion: {summary.propose_shadow_promotion_count}")
    typer.echo(f"reject: {summary.reject_count}")
    typer.echo(f"defer: {summary.defer_count}")
    typer.echo(f"block: {summary.block_count}")
    typer.echo(f"pending_review: {summary.pending_review_count}")
    typer.echo(f"invalid_decisions: {summary.invalid_decision_count}")
    typer.echo(f"report: {relative_artifact_path}")

    if json_output:
        typer.echo(artifact.model_dump_json(indent=2, exclude_none=False))


@app.command(name="unified-candidates-shadow")
@_guard
def unified_candidates_shadow(
    run_id: RunIdArgument, json_output: UnifiedCandidatesShadowJsonOption = False
) -> None:
    """Calcula y persiste diagnostics/unified-candidates-shadow.json de RUN_ID.

    Artefacto NO contractual, bajo demanda (Fase 11 de la ampliacion
    semantica, ver docs/UNIFIED_CANDIDATES_SHADOW.md): preserva
    CandidateArtifact V1 como baseline inmutable (lane BASELINE_V1) e
    incorpora como propuestas shadow UNICAMENTE los items de
    diagnostics/candidate-promotion-plan.json con
    action=PROPOSE_SHADOW_PROMOTION y status=VALID (lane
    SHADOW_PROPOSAL), resolviendo cada uno contra su candidato fuente
    real (V2/interprocedural) y agrupando exclusivamente las propuestas
    EXACT_MATCH ya declaradas por Fase 9. Requiere que RUN_ID ya haya
    alcanzado PARSED (SUCCEEDED), que CandidateArtifact V1
    (artifacts/06-candidates.json) exista, y que
    diagnostics/candidate-promotion-review-package.json y
    diagnostics/candidate-promotion-plan.json ya existan (esta fase
    NUNCA regenera un plan ausente ni decisiones humanas). Nunca modifica
    run.json, ningun artifacts/01-10, CandidateArtifact V1 ni ningun otro
    diagnostics/ preexistente; nunca ejecuta una promocion real, nunca
    genera ContextPackage/RuleDraft/reglas Markdown, nunca escribe en
    Neo4j.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    artifact = compute_unified_candidates_shadow_artifact(run_dir, run_id)
    artifact_path = write_unified_candidates_shadow_artifact(run_dir, artifact)
    relative_artifact_path = artifact_path.relative_to(run_dir).as_posix()

    summary = artifact.summary
    typer.echo(f"run_id: {artifact.run_id}")
    typer.echo(f"v1_baseline: {summary.v1_baseline_count}")
    typer.echo(f"proposed_plan_items: {summary.proposed_plan_item_count}")
    typer.echo(f"shadow_members: {summary.shadow_member_count}")
    typer.echo(f"shadow_groups: {summary.shadow_group_count}")
    typer.echo(f"valid_groups: {summary.valid_group_count}")
    typer.echo(f"invalid_groups: {summary.invalid_group_count}")
    typer.echo(f"exact_baseline_match: {summary.exact_baseline_match_group_count}")
    typer.echo(f"related_to_baseline: {summary.related_to_baseline_group_count}")
    typer.echo(f"not_in_baseline: {summary.not_in_baseline_group_count}")
    typer.echo(f"conflicting_with_baseline: {summary.conflicting_with_baseline_group_count}")
    typer.echo(f"excluded_plan_items: {summary.excluded_plan_item_count}")
    typer.echo(f"report: {relative_artifact_path}")

    if json_output:
        typer.echo(artifact.model_dump_json(indent=2, exclude_none=False))


@app.command(name="unified-shadow-validate")
@_guard
def unified_shadow_validate(
    run_id: RunIdArgument, json_output: UnifiedShadowValidationJsonOption = False
) -> None:
    """Calcula y persiste diagnostics/unified-shadow-validation-report.json de RUN_ID.

    Reporte NO contractual, bajo demanda (Fase 12 de la ampliacion
    semantica, ver docs/UNIFIED_SHADOW_DIFFERENTIAL_VALIDATION.md):
    determina si diagnostics/unified-candidates-shadow.json (Fase 11)
    cumple las condiciones TECNICAS minimas para alimentar, en una fase
    posterior, un flujo downstream tambien en shadow mode -- nunca
    afirma correccion funcional, nunca promueve ningun candidato.
    Requiere que RUN_ID haya alcanzado PARSED (SUCCEEDED) Y que
    diagnostics/unified-candidates-shadow.json exista y sea valido --
    su ausencia, JSON invalido, version incompatible o contrato
    Pydantic invalido es SIEMPRE un error de comando (exit code
    distinto de cero, sin reporte). La ausencia o invalidez de
    cualquier fuente SECUNDARIA (CandidateArtifact V1, V2/
    interprocedural, assessment, review package, plan) nunca impide
    generar el reporte -- se refleja como un hallazgo representable
    (NOT_EVALUATED/BLOCKED/REVIEW_REQUIRED segun la causa). Nunca
    modifica run.json, ningun artifacts/01-10 ni ningun otro
    diagnostics/ preexistente; nunca regenera el artefacto unificado,
    un plan o una decision humana ausentes; nunca promueve ni modifica
    ningun candidato; nunca escribe en Neo4j.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    report = compute_unified_shadow_validation_report(run_dir, run_id)
    report_path = write_unified_shadow_validation_report(run_dir, report)
    relative_report_path = report_path.relative_to(run_dir).as_posix()

    summary = report.summary
    gate_status_counts = {
        status.value: count for status, count in summary.counts_by_gate_status.items()
    }
    typer.echo(f"run_id: {report.run_id}")
    typer.echo(f"disposition: {report.disposition.value}")
    typer.echo(f"baseline_candidates: {summary.baseline_candidate_count}")
    typer.echo(f"shadow_members: {summary.shadow_member_count}")
    typer.echo(f"shadow_groups: {summary.shadow_group_count}")
    typer.echo(f"downstream_eligible_groups: {summary.downstream_eligible_group_count}")
    typer.echo(f"errors: {summary.error_count}")
    typer.echo(f"warnings: {summary.warning_count}")
    typer.echo(f"blocking_issues: {summary.blocking_issue_count}")
    typer.echo(f"gates_passed: {gate_status_counts.get('PASS', 0)}")
    typer.echo(f"gates_failed: {gate_status_counts.get('FAIL', 0)}")
    typer.echo(f"gates_not_evaluated: {gate_status_counts.get('NOT_EVALUATED', 0)}")
    typer.echo(f"report: {relative_report_path}")

    if json_output:
        typer.echo(report.model_dump_json(indent=2, exclude_none=False))


@app.command(name="unified-shadow-downstream")
@_guard
def unified_shadow_downstream(
    run_id: RunIdArgument, json_output: UnifiedShadowDownstreamJsonOption = False
) -> None:
    """Calcula y persiste diagnostics/unified-shadow-downstream.json de RUN_ID.

    Artefacto NO contractual, bajo demanda (Fase 13 de la ampliacion
    semantica, ver docs/UNIFIED_SHADOW_DOWNSTREAM_PIPELINE.md): ejecuta,
    EXCLUSIVAMENTE para los grupos con elegibilidad downstream efectiva
    (estructural, Fase 12, Y disposicion de validacion QUALIFIED_FOR_
    DOWNSTREAM_SHADOW/QUALIFIED_WITH_WARNINGS), el mismo flujo
    productivo ContextPackage -> RuleDraft -> Guardrails, pero
    envolviendolo en shadow mode -- nunca reemplaza, modifica ni
    alimenta el pipeline V1, nunca publica una regla. El unico
    proveedor admitido es el fake determinista oficial: esta fase nunca
    invoca un proveedor LLM real. Requiere que RUN_ID haya alcanzado
    PARSED (SUCCEEDED) y que diagnostics/unified-candidates-shadow.json
    Y diagnostics/unified-shadow-validation-report.json existan y sean
    validos -- la ausencia/invalidez de cualquiera de los dos, o una
    inconsistencia de hash entre las fuentes, es SIEMPRE un error de
    comando (exit code distinto de cero, sin artefacto). Una disposicion
    de validacion REVIEW_REQUIRED/BLOCKED/NOT_EVALUATED produce un
    artefacto NOT_EXECUTED (exit code 0), nunca un error tecnico. Nunca
    modifica run.json, ningun artifacts/01-10, CandidateArtifact V1, el
    artefacto unificado, el reporte de validacion ni ningun otro
    diagnostics/ preexistente; nunca escribe en Neo4j.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    artifact = compute_unified_shadow_downstream_artifact(run_dir, run_id, settings=settings)
    artifact_path = write_unified_shadow_downstream_artifact(run_dir, artifact)
    relative_artifact_path = artifact_path.relative_to(run_dir).as_posix()

    summary = artifact.summary
    typer.echo(f"run_id: {artifact.run_id}")
    typer.echo(f"disposition: {artifact.disposition.value}")
    typer.echo(f"validation_groups: {summary.validation_group_count}")
    typer.echo(f"eligible_groups: {summary.downstream_eligible_group_count}")
    typer.echo(f"executed_groups: {summary.executed_group_count}")
    typer.echo(f"skipped_groups: {summary.skipped_group_count}")
    typer.echo(f"context_packages: {summary.context_package_count}")
    typer.echo(f"rule_drafts: {summary.rule_draft_count}")
    typer.echo(f"guardrails_passed: {summary.guardrail_passed_count}")
    typer.echo(f"guardrails_rejected: {summary.guardrail_rejected_count}")
    typer.echo(f"technical_failures: {summary.technical_failure_count}")
    typer.echo(f"report: {relative_artifact_path}")

    if json_output:
        typer.echo(artifact.model_dump_json(indent=2, exclude_none=False))


@app.command(name="unified-activation-evaluate")
@_guard
def unified_activation_evaluate(
    run_id: RunIdArgument,
    config: UnifiedActivationConfigOption,
    json_output: UnifiedActivationJsonOption = False,
) -> None:
    """Calcula y persiste diagnostics/unified-activation-evaluation.json de RUN_ID.

    Control plane de activacion unificada, NO contractual, bajo
    demanda (Fase 14A de la ampliacion semantica, ver
    docs/CONTROLLED_UNIFIED_ACTIVATION.md): lee `--config` (un YAML
    EXTERNO de `UnifiedActivationConfig`, NUNCA copiado al repositorio
    ni al directorio del run), determina el modo operativo solicitado,
    selecciona canary de forma deterministica, compara el pipeline V1
    con el downstream unified shadow (Fase 13) UNICAMENTE mediante
    igualdad demostrable de campos estructurales, y produce una
    evaluacion de activacion -- pero NUNCA selecciona `unified` como
    lane efectivo, NUNCA materializa candidatos/drafts/reglas, NUNCA
    inicializa un proveedor real. Esta es una decision INFORMATIVA de
    lo que una futura Fase 14B haria; no autoriza ninguna activacion
    productiva.

    Requiere que RUN_ID haya alcanzado PARSED (SUCCEEDED). Carga
    UNICAMENTE los artefactos ya existentes en disco -- nunca
    regenera `unified-candidates-shadow.json`/`unified-shadow-
    validation-report.json`/`unified-shadow-downstream.json`
    ausentes; su ausencia se refleja como un hallazgo representable
    (`NOT_EVALUATED`/`BLOCKED`), nunca un error tecnico. Nunca acepta
    proveedor, API key, endpoint, modelo, bandera de materializacion,
    porcentaje de canary ni decisiones humanas por linea de comandos
    -- todo proviene EXCLUSIVAMENTE del YAML validado. Nunca modifica
    run.json, ningun artifacts/01-10, CandidateArtifact V1 ni ningun
    otro diagnostics/ preexistente; nunca escribe en Neo4j.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    artifact = compute_unified_activation_evaluation(run_dir, run_id, config_path=Path(config))
    artifact_path = write_unified_activation_evaluation(run_dir, artifact)
    relative_artifact_path = artifact_path.relative_to(run_dir).as_posix()

    summary = artifact.summary
    canary_selected = artifact.canary_selection.selected if artifact.canary_selection else False
    typer.echo(f"run_id: {artifact.run_id}")
    typer.echo(f"mode: {artifact.mode.value}")
    typer.echo(f"requested_lane: {artifact.requested_lane.value}")
    typer.echo(f"effective_lane: {artifact.effective_lane.value}")
    typer.echo(f"fallback_lane: {artifact.fallback_lane.value}")
    typer.echo(f"canary_selected: {canary_selected}")
    typer.echo(f"readiness_disposition: {artifact.readiness_disposition.value}")
    typer.echo(f"activation_decision: {artifact.activation_decision.value}")
    typer.echo(f"v1_references: {summary.v1_reference_count}")
    typer.echo(f"unified_references: {summary.unified_reference_count}")
    typer.echo(f"exact_equivalents: {summary.exact_equivalent_count}")
    typer.echo(f"unified_additive: {summary.unified_additive_count}")
    typer.echo(f"v1_only: {summary.v1_only_count}")
    typer.echo(f"conflicts: {summary.conflicting_count}")
    typer.echo(f"warnings: {summary.warning_count}")
    typer.echo(f"blocking_issues: {summary.blocking_issue_count}")
    typer.echo(f"materialization_enabled: {artifact.materialization_enabled}")
    typer.echo(f"report: {relative_artifact_path}")

    if json_output:
        typer.echo(artifact.model_dump_json(indent=2, exclude_none=False))


@app.command(name="unified-activation-materialize")
@_guard
def unified_activation_materialize(
    run_id: RunIdArgument,
    authorization: UnifiedMaterializationAuthorizationOption,
    json_output: UnifiedMaterializationJsonOption = False,
) -> None:
    """Ejecuta una materializacion controlada y reversible de RUN_ID.

    Control plane ejecutable, NO contractual, bajo demanda (Fase 14B
    de la ampliacion semantica, ver
    docs/CONTROLLED_UNIFIED_MATERIALIZATION.md): lee `--authorization`
    (un YAML EXTERNO de `UnifiedMaterializationAuthorization`, NUNCA
    copiado al repositorio ni al directorio del run), valida que
    corresponda exactamente a la evaluacion de Fase 14A vigente
    (`run_id`/`activation_evaluation_hash`/`expected_readiness_
    disposition`), y ejecuta la accion autorizada: mantener V1,
    activar un canary/primary unified, ejecutar un fallback real a V1,
    o revertir a una generacion anterior. V1 NUNCA se sobrescribe;
    cada generacion es inmutable y content-addressed; el UNICO commit
    point es la escritura atomica de `activation/active.json`.
    Idempotente: la misma autorizacion aplicada al mismo estado nunca
    produce un evento nuevo. Persiste EXCLUSIVAMENTE bajo
    `<run_dir>/activation/` -- nunca modifica `run.json`, ningun
    `artifacts/01-10` ni ningun `diagnostics/*.json` preexistente.
    Nunca inicializa un proveedor real, nunca publica una regla.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    result = materialize_unified_activation(run_dir, run_id, authorization_path=Path(authorization))

    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"action: {result.action.value}")
    typer.echo(f"generation_id: {result.generation_id}")
    typer.echo(f"active_lane: {result.active_lane.value}")
    typer.echo(f"previous_generation: {result.previous_generation_id or '(none)'}")
    typer.echo(f"fallback_generation: {result.fallback_generation_id}")
    typer.echo(f"pointer_version: {result.pointer_version}")
    typer.echo(f"event_id: {result.event_id}")
    typer.echo(f"idempotent: {result.idempotent}")
    typer.echo(f"materialized_file_count: {result.materialized_file_count}")
    typer.echo("activation_dir: activation")

    if json_output:
        payload = {
            "run_id": result.run_id,
            "action": result.action.value,
            "generation_id": result.generation_id,
            "active_lane": result.active_lane.value,
            "previous_generation_id": result.previous_generation_id,
            "fallback_generation_id": result.fallback_generation_id,
            "pointer_version": result.pointer_version,
            "event_id": result.event_id,
            "idempotent": result.idempotent,
            "materialized_file_count": result.materialized_file_count,
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@app.command(name="unified-activation-status")
@_guard
def unified_activation_status(
    run_id: RunIdArgument, json_output: UnifiedMaterializationJsonOption = False
) -> None:
    """Muestra el estado actual del control plane de materializacion de RUN_ID.

    Solo lectura (Fase 14B Parte 13): nunca transiciona, nunca
    modifica `activation/`. Revalida hash/bytes de cada archivo de la
    generacion activa contra el filesystem real antes de reportar
    `manifest_valid`/`integrity_status`.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    resolver = ActiveArtifactResolver(run_dir, run_id=run_id)
    store = resolver.store
    pointer = resolver.active_pointer()

    manifest_valid = True
    integrity_status = "OK"
    generation_kind = "UNKNOWN"
    try:
        manifest = store.read_generation_manifest(pointer.active_generation_id)
        generation_kind = manifest.kind.value
        store.validate_generation_files(manifest)
    except UnifiedActivationStoreError:
        manifest_valid = False
        integrity_status = "CORRUPT"

    chain_length = 1
    event = store.read_event(pointer.latest_event_id)
    while event.previous_event_id is not None:
        event = store.read_event(event.previous_event_id)
        chain_length += 1

    typer.echo(f"active_lane: {pointer.active_lane.value}")
    typer.echo(f"generation_id: {pointer.active_generation_id}")
    typer.echo(f"generation_kind: {generation_kind}")
    typer.echo(f"pointer_version: {pointer.pointer_version}")
    typer.echo(f"manifest_valid: {manifest_valid}")
    typer.echo(f"fallback_generation: {pointer.fallback_generation_id}")
    typer.echo(f"latest_event: {pointer.latest_event_id}")
    typer.echo(f"chain_length: {chain_length}")
    typer.echo(f"integrity_status: {integrity_status}")

    if json_output:
        payload = {
            "active_lane": pointer.active_lane.value,
            "generation_id": pointer.active_generation_id,
            "generation_kind": generation_kind,
            "pointer_version": pointer.pointer_version,
            "manifest_valid": manifest_valid,
            "fallback_generation": pointer.fallback_generation_id,
            "latest_event": pointer.latest_event_id,
            "chain_length": chain_length,
            "integrity_status": integrity_status,
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@app.command(name="unified-activation-resolve")
@_guard
def unified_activation_resolve(
    run_id: RunIdArgument,
    artifact: UnifiedActivationResolveArtifactOption,
    json_output: UnifiedMaterializationJsonOption = False,
) -> None:
    """Resuelve --artifact desde el lane activo de RUN_ID (con fallback ejecutable).

    Fase 14B Parte 13: nunca acepta un proveedor, credencial, ruta de
    generacion ni lane arbitrario -- UNICAMENTE un `logical_name`
    tipado. Si el lane activo es UNIFIED y esta corrupto/incompleto,
    y el puntero autoriza fallback, ejecuta `FALLBACK_TO_V1`
    automaticamente (auditable, ver `unified-activation-status`) y
    resuelve de nuevo desde V1.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    resolver = ActiveArtifactResolver(run_dir, run_id=run_id)
    resolution = resolver.resolve(artifact)

    typer.echo(f"artifact: {resolution.requested_logical_name}")
    typer.echo(f"status: {resolution.status.value}")
    typer.echo(f"resolved_lane: {resolution.resolved_lane.value}")
    typer.echo(f"generation_id: {resolution.generation_id}")
    typer.echo(f"relative_path: {resolution.relative_path or '(none)'}")
    typer.echo(f"sha256: {resolution.sha256 or '(none)'}")
    typer.echo(f"fallback_applied: {resolution.fallback_applied}")
    typer.echo(f"fallback_event: {resolution.fallback_event_id or '(none)'}")

    if json_output:
        typer.echo(resolution.model_dump_json(indent=2, exclude_none=False))


@app.command(name="unified-activation-rollback")
@_guard
def unified_activation_rollback(
    run_id: RunIdArgument,
    authorization: UnifiedMaterializationAuthorizationOption,
    json_output: UnifiedMaterializationJsonOption = False,
) -> None:
    """Ejecuta un rollback explicito de RUN_ID segun AUTHORIZATION.

    Envoltorio delgado sobre `unified-activation-materialize` (Fase
    14B Parte 13): rechaza, ANTES de ejecutar cualquier transicion,
    una autorizacion cuya `action` no sea `ROLLBACK_TO_PREVIOUS`/
    `ROLLBACK_TO_GENERATION` -- para ese proposito general esta
    `unified-activation-materialize`.
    """
    authorization_path = Path(authorization)
    action = peek_authorization_action(authorization_path)
    if not action_is_rollback(action):
        raise UnifiedMaterializationError(
            f"unified-activation-rollback exige una accion de rollback, obtuvo: {action.value} "
            "-- usar unified-activation-materialize para otras acciones"
        )

    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    result = materialize_unified_activation(run_dir, run_id, authorization_path=authorization_path)

    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"action: {result.action.value}")
    typer.echo(f"generation_id: {result.generation_id}")
    typer.echo(f"active_lane: {result.active_lane.value}")
    typer.echo(f"previous_generation: {result.previous_generation_id or '(none)'}")
    typer.echo(f"pointer_version: {result.pointer_version}")
    typer.echo(f"event_id: {result.event_id}")
    typer.echo(f"idempotent: {result.idempotent}")

    if json_output:
        payload = {
            "run_id": result.run_id,
            "action": result.action.value,
            "generation_id": result.generation_id,
            "active_lane": result.active_lane.value,
            "previous_generation_id": result.previous_generation_id,
            "pointer_version": result.pointer_version,
            "event_id": result.event_id,
            "idempotent": result.idempotent,
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@app.command(name="v2-candidates-shadow")
@_guard
def v2_candidates_shadow(run_id: RunIdArgument, json_output: V2ShadowJsonOption = False) -> None:
    """Calcula y persiste diagnostics/v2-candidates-shadow.json de RUN_ID.

    Artefacto NO contractual, bajo demanda (Fase 5 de la ampliacion
    semantica, ver docs/V2_DETECTORS_SHADOW_MODE.md): ejecuta detectores
    V2 experimentales en modo shadow sobre CanonicalProgram/
    SemanticGraph/SemanticEffectsArtifact (en memoria)/
    SemanticPropagationArtifact (en memoria) y compara sus candidatos
    contra CandidateArtifact V1 (Q0). Requiere que RUN_ID ya haya
    alcanzado PARSED, SEMANTIC_GRAPH_BUILT y CANDIDATES_DETECTED
    (SUCCEEDED). Nunca modifica run.json, ningun artifacts/01-10 ni
    ningun diagnostics/ preexistente; nunca accede a Neo4j ni invoca un
    proveedor LLM; sus candidatos nunca alimentan ContextPackage, el
    LLM, los guardrails ni la generacion de reglas.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    artifact = compute_v2_shadow_candidates_artifact(run_dir, run_id)
    artifact_path = write_v2_shadow_candidates_artifact(run_dir, artifact)
    relative_artifact_path = artifact_path.relative_to(run_dir).as_posix()

    summary = artifact.summary
    typer.echo(f"run_id: {artifact.run_id}")
    typer.echo(f"detectors_executed: {summary.detector_count}")
    typer.echo(f"v1_candidates: {summary.v1_candidate_count}")
    typer.echo(f"v2_candidates: {summary.v2_candidate_count}")
    typer.echo(f"deterministic: {summary.deterministic_count}")
    typer.echo(f"partial: {summary.partial_count}")
    typer.echo(f"blocked: {summary.blocked_count}")
    typer.echo(f"matched: {summary.matched_count}")
    typer.echo(f"v1_only: {summary.v1_only_count}")
    typer.echo(f"v2_only: {summary.v2_only_count}")
    typer.echo(f"related_not_equivalent: {summary.related_not_equivalent_count}")
    for rule_type in sorted(summary.counts_by_rule_type, key=lambda item: item.value):
        typer.echo(f"  {rule_type.value}: {summary.counts_by_rule_type[rule_type]}")
    typer.echo(f"report: {relative_artifact_path}")

    if json_output:
        typer.echo(artifact.model_dump_json(indent=2, exclude_none=False))


@app.command(name="semantic-interprocedural")
@_guard
def semantic_interprocedural(
    run_id: RunIdArgument, json_output: InterproceduralJsonOption = False
) -> None:
    """Calcula y persiste diagnostics/interprocedural-call-linkage.json de RUN_ID.

    Artefacto NO contractual, bajo demanda (Fase 6 de la ampliacion
    semantica, ver docs/INTERPROCEDURAL_CALL_LINKAGE.md): construye la
    fundacion interprocedural CALL/LINKAGE -- interfaces de programa,
    call sites resueltos contra el paquete, bindings actual-formal
    posicionales y el call graph directo con recursion/ciclos. Calcula
    SemanticEffectsArtifact en memoria (nunca lee ni escribe
    diagnostics/semantic-effects.json). Requiere que RUN_ID ya haya
    alcanzado PARSED (SUCCEEDED). Nunca modifica run.json, ningun
    artifacts/01-10 ni ningun diagnostics/ preexistente; nunca accede a
    Neo4j ni invoca un proveedor LLM; nunca propaga valores entre
    programas; su contenido nunca alimenta SemanticGraph, candidatos
    V1/V2, ContextPackage ni la generacion de reglas.
    """
    settings = load_settings()
    run_dir = _run_dir(settings, run_id)
    artifact = compute_interprocedural_call_linkage_artifact(run_dir, run_id)
    artifact_path = write_interprocedural_call_linkage_artifact(run_dir, artifact)
    relative_artifact_path = artifact_path.relative_to(run_dir).as_posix()

    summary = artifact.summary
    typer.echo(f"run_id: {artifact.run_id}")
    typer.echo(f"programs: {summary.program_count}")
    typer.echo(f"interfaces: {summary.interface_count}")
    typer.echo(f"call_sites: {summary.call_site_count}")
    typer.echo(f"resolved_internal: {summary.resolved_internal_count}")
    typer.echo(f"dynamic: {summary.dynamic_count}")
    typer.echo(f"missing_program: {summary.missing_program_count}")
    typer.echo(f"ambiguous_program: {summary.ambiguous_program_count}")
    typer.echo(f"bindings_resolved: {summary.resolved_binding_count}")
    typer.echo(f"bindings_unresolved: {summary.unresolved_binding_count}")
    typer.echo(f"recursive_calls: {summary.recursive_call_count}")
    typer.echo(f"cycles: {summary.cycle_count}")
    typer.echo(f"report: {relative_artifact_path}")

    if json_output:
        typer.echo(artifact.model_dump_json(indent=2, exclude_none=False))


if __name__ == "__main__":
    app()
