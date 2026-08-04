"""Abstraccion OPT-IN para consumidores del lane activo (Fase 14B
Parte 14, `feat/controlled-unified-materialization`).

Un consumidor debe elegir EXPLICITAMENTE entre dos caminos, nunca
mezclarlos implicitamente:

1. la ruta V1 DIRECTA e historica (`api/reads.py`/`api/downloads.py`,
   SIN CAMBIOS en esta fase -- ver auditoria de consumidores en
   `docs/CONTROLLED_UNIFIED_MATERIALIZATION.md`); o
2. `ActiveArtifactResolver` (esta clase), que resuelve por
   `logical_name` desde el lane ACTIVO real (V1 o UNIFIED), con
   fallback ejecutable ante corrupcion/ausencia del lane unified.

En Fase 14B, UNICAMENTE el CLI (`unified-activation-status`/
`unified-activation-resolve`) y los tests usan esta clase. La API y la
UI productivas siguen usando su comportamiento actual (Parte 1,
auditoria de consumidores): NUNCA se afirma que "unified" es
globalmente productivo mientras API/UI no opten explicitamente por
este resolver -- esa integracion queda para una fase futura."""

from __future__ import annotations

from pathlib import Path

from ..contracts.unified_activation_materialization import (
    ActiveActivationPointer,
    ActiveArtifactResolution,
)
from .errors import UnifiedMaterializationError
from .unified_activation_store import UnifiedActivationStore
from .unified_active_lane_service import resolve_with_fallback


class ActiveArtifactResolver:
    """Punto de entrada OPT-IN unico -- instanciarlo es, en si mismo,
    la eleccion explicita de usar el lane activo en vez de la ruta V1
    directa historica."""

    def __init__(self, run_dir: Path, *, run_id: str) -> None:
        self._store = UnifiedActivationStore(run_dir)
        self._run_id = run_id

    @property
    def run_id(self) -> str:
        return self._run_id

    def resolve(self, logical_name: str) -> ActiveArtifactResolution:
        """Resuelve `logical_name` desde el lane activo -- con
        fallback ejecutable a V1 si el lane unified activo esta
        corrupto/incompleto y el puntero lo autoriza (ver
        `pipeline/unified_active_lane_service.py::resolve_with_
        fallback`)."""
        return resolve_with_fallback(self._store, run_id=self._run_id, logical_name=logical_name)

    def resolve_path(self, logical_name: str) -> Path | None:
        """Conveniencia: la ruta ABSOLUTA real del archivo resuelto
        (dentro del run), o `None` si `status` no es `RESOLVED`/
        `FALLBACK_APPLIED` (nunca fabrica una ruta para una ausencia
        legitima o un bloqueo)."""
        resolution = self.resolve(logical_name)
        if resolution.relative_path is None:
            return None
        return self._store.run_dir / resolution.relative_path

    def active_pointer(self) -> ActiveActivationPointer:
        """Usado por `unified-activation-status` (Parte 13). Lanza
        `UnifiedMaterializationError` si el run nunca se inicializo."""
        pointer = self._store.read_active_pointer()
        if pointer is None:
            raise UnifiedMaterializationError(
                "el run no tiene ningun lane activo todavia -- ejecutar "
                "unified-activation-materialize primero"
            )
        return pointer

    @property
    def store(self) -> UnifiedActivationStore:
        """Acceso de bajo nivel (validacion de integridad, lectura de
        eventos) para el CLI `unified-activation-status` -- nunca para
        transicionar (esa responsabilidad es exclusiva del servicio de
        materializacion, Parte 12)."""
        return self._store


__all__ = ["ActiveArtifactResolver"]
