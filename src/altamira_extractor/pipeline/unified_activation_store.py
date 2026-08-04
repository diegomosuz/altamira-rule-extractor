"""Almacenamiento content-addressed de Fase 14B
(`feat/controlled-unified-materialization`).

Responsable EXCLUSIVAMENTE de filesystem: validar `run_dir`, construir
rutas seguras, adquirir el lock single-writer, persistir/leer
generaciones, eventos y el puntero activo. NUNCA conoce politicas de
activacion (que accion aplicar, que readiness exige un modo) -- eso es
responsabilidad de `pipeline/unified_activation_transition.py` y
`pipeline/unified_materialization_service.py`.

Toda escritura reutiliza `pipeline/artifact_store.py::atomic_write_json`/
`atomic_promote_directory` (ya probados, con reintento acotado ante
contencion transitoria en Windows) -- nunca reimplementa la escritura
atomica.

Lock (`activation/.activation.lock`): creado por exclusion mutua real
(`os.O_CREAT | os.O_EXCL`, nunca "check-then-create"). Contenido minimo
no sensible (PID del proceso, nunca un timestamp). Si ya existe, falla
de inmediato con `UnifiedActivationLockError` -- este modulo NUNCA
asume automaticamente que un lock existente esta "stale"; la
recuperacion ante un crash es un procedimiento MANUAL documentado (ver
`docs/CONTROLLED_UNIFIED_MATERIALIZATION.md`)."""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..contracts.unified_activation_materialization import (
    ActivationTransitionEvent,
    ActiveActivationPointer,
    MaterializedActivationLane,
    MaterializedFileReference,
    MaterializedGenerationManifest,
    check_relative_path_is_safe,
)
from .artifact_store import atomic_promote_directory, atomic_write_json
from .errors import UnifiedActivationLockError, UnifiedActivationStoreError

_LOCK_FILENAME = ".activation.lock"
_MANIFEST_FILENAME = "manifest.json"
_ACTIVE_POINTER_FILENAME = "active.json"
_GENERATIONS_DIR_NAME = "generations"
_EVENTS_DIR_NAME = "events"
_ACTIVATION_DIR_NAME = "activation"


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_id_component(value: str, *, label: str) -> None:
    """Defensa contra path traversal via un identificador
    OPERADOR-provisto (p. ej. `target_generation_id` de una
    autorizacion de rollback, Parte 3) -- nunca se confia en que un
    `generation_id`/`event_id` externo sea seguro solo por su forma."""
    if not value or "/" in value or "\\" in value or value in (".", "..") or "\x00" in value:
        raise UnifiedActivationStoreError(f"{label} invalido: {value!r}")


def _generation_relative_filename(
    manifest: MaterializedGenerationManifest, relative_path: str
) -> str:
    prefix = f"activation/generations/{manifest.generation_id}/"
    normalized = relative_path.replace("\\", "/")
    if not normalized.startswith(prefix):
        raise UnifiedActivationStoreError(
            f"relative_path fuera de la carpeta propia de la generacion: {relative_path!r}"
        )
    return normalized[len(prefix) :]


class UnifiedActivationStore:
    """Store content-addressed de UN run. Nunca comparte estado entre
    instancias/runs distintos."""

    def __init__(self, run_dir: Path) -> None:
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise UnifiedActivationStoreError(f"run_dir invalido o inexistente: {run_dir.name!r}")
        self._run_dir = run_dir
        self._activation_dir = run_dir / _ACTIVATION_DIR_NAME
        self._generations_dir = self._activation_dir / _GENERATIONS_DIR_NAME
        self._events_dir = self._activation_dir / _EVENTS_DIR_NAME
        self._active_pointer_path = self._activation_dir / _ACTIVE_POINTER_FILENAME
        self._lock_path = self._activation_dir / _LOCK_FILENAME

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    @property
    def activation_dir(self) -> Path:
        return self._activation_dir

    @property
    def active_pointer_path(self) -> Path:
        return self._active_pointer_path

    def _resolve_within_run(self, relative_path: str) -> Path:
        """Segunda linea de defensa (Parte 5, invariante 28-29):
        revalida `relative_path` contra `check_relative_path_is_safe` Y
        confirma que la ruta resuelta permanece dentro de `run_dir`
        real -- nunca confia unicamente en la validacion ya aplicada
        por el contrato Pydantic."""
        check_relative_path_is_safe(relative_path)
        run_root = self._run_dir.resolve()
        candidate = (self._run_dir / relative_path).resolve()
        try:
            candidate.relative_to(run_root)
        except ValueError as exc:
            raise UnifiedActivationStoreError(
                f"relative_path escapa del run: {relative_path!r}"
            ) from exc
        return candidate

    # ------------------------------------------------------------------
    # Lock single-writer
    # ------------------------------------------------------------------

    @contextmanager
    def lock(self) -> Iterator[None]:
        """Adquiere `activation/.activation.lock` por exclusion mutua
        real. Se libera SIEMPRE en `finally` -- tanto en exito como en
        cualquier fallo dentro del bloque `with`."""
        self._activation_dir.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise UnifiedActivationLockError(
                "activation/.activation.lock ya esta tomado -- otra materializacion esta en "
                "curso, o un proceso anterior no lo libero (ver recuperacion manual en "
                "docs/CONTROLLED_UNIFIED_MATERIALIZATION.md)"
            ) from exc
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()))
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            self._release_lock()
            raise
        try:
            yield
        finally:
            self._release_lock()

    def _release_lock(self) -> None:
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            pass

    def lock_is_held(self) -> bool:
        return self._lock_path.is_file()

    # ------------------------------------------------------------------
    # Puntero activo
    # ------------------------------------------------------------------

    def read_active_pointer(self) -> ActiveActivationPointer | None:
        """`None` UNICAMENTE cuando `active.json` nunca se escribio
        (run recien inicializado) -- nunca para un archivo corrupto,
        que lanza `UnifiedActivationStoreError` en su lugar."""
        if self._active_pointer_path.is_symlink() or not self._active_pointer_path.is_file():
            return None
        try:
            return ActiveActivationPointer.model_validate_json(
                self._active_pointer_path.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            raise UnifiedActivationStoreError("activation/active.json corrupto") from exc

    def write_active_pointer(self, pointer: ActiveActivationPointer) -> ActiveActivationPointer:
        """Escritura atomica -- el UNICO commit point de una transicion
        (Parte 2, principio 9). Relee y revalida despues de escribir
        (Parte 2, principio 5)."""
        atomic_write_json(self._active_pointer_path, pointer)
        reread = self.read_active_pointer()
        if reread is None or reread.to_stable_json() != pointer.to_stable_json():
            raise UnifiedActivationStoreError(
                "fallo al releer/validar activation/active.json inmediatamente despues de "
                "escribirlo"
            )
        return reread

    # ------------------------------------------------------------------
    # Generaciones
    # ------------------------------------------------------------------

    def generation_dir(self, generation_id: str) -> Path:
        _validate_id_component(generation_id, label="generation_id")
        return self._generations_dir / generation_id

    def generation_exists(self, generation_id: str) -> bool:
        manifest_path = self.generation_dir(generation_id) / _MANIFEST_FILENAME
        return manifest_path.is_file() and not manifest_path.is_symlink()

    def read_generation_manifest(self, generation_id: str) -> MaterializedGenerationManifest:
        manifest_path = self.generation_dir(generation_id) / _MANIFEST_FILENAME
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise UnifiedActivationStoreError(f"generacion inexistente: {generation_id}")
        try:
            manifest = MaterializedGenerationManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except ValueError as exc:
            raise UnifiedActivationStoreError(f"manifest corrupto: {generation_id}") from exc
        if manifest.generation_id != generation_id:
            raise UnifiedActivationStoreError(
                f"manifest.generation_id ({manifest.generation_id!r}) no coincide con el "
                f"directorio de generacion ({generation_id!r})"
            )
        return manifest

    def validate_file_reference(self, file_reference: MaterializedFileReference) -> Path:
        """Revalida bytes/hash de UN `MaterializedFileReference` contra
        el filesystem real (Parte 5, invariante 7) -- usada por
        `validate_generation_files` (todas las referencias) y por el
        router (Parte 10, UNA sola referencia, sin cargar el resto de
        la generacion innecesariamente). Retorna la ruta resuelta real."""
        path = self._resolve_within_run(file_reference.relative_path)
        if path.is_symlink() or not path.is_file():
            raise UnifiedActivationStoreError(
                f"archivo ausente: {file_reference.logical_name} ({file_reference.relative_path})"
            )
        data = path.read_bytes()
        if len(data) != file_reference.byte_size:
            raise UnifiedActivationStoreError(
                f"byte_size no reconciliado: {file_reference.logical_name}"
            )
        if _hash_bytes(data) != file_reference.sha256:
            raise UnifiedActivationStoreError(
                f"sha256 no reconciliado: {file_reference.logical_name}"
            )
        return path

    def validate_generation_files(self, manifest: MaterializedGenerationManifest) -> None:
        """Revalida bytes/hash de CADA `MaterializedFileReference`
        contra el filesystem real (Parte 5, invariante 7) -- usada al
        persistir. El router (Parte 10) usa `validate_file_reference`
        directamente para UNA sola referencia."""
        for file_reference in manifest.files:
            self.validate_file_reference(file_reference)

    def persist_generation(
        self, manifest: MaterializedGenerationManifest, data_files: dict[str, bytes]
    ) -> MaterializedGenerationManifest:
        """Idempotente (Parte 2, principio 14): si la generacion ya
        existe, la revalida y reutiliza SIN reescribirla; si el
        contenido real difiere, falla por colision (nunca sobrescribe
        una generacion distinta). `data_files` UNICAMENTE se usa para
        `lane=UNIFIED` (V1 nunca escribe copias propias, referencia
        `artifacts/` existente -- Parte 5, invariante 16)."""
        destination_dir = self.generation_dir(manifest.generation_id)
        if destination_dir.is_dir():
            return self._reconcile_existing_generation(manifest)

        self._generations_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = self._generations_dir / f".tmp-{os.getpid()}-{secrets.token_hex(8)}"
        temp_dir.mkdir()
        try:
            if manifest.lane == MaterializedActivationLane.UNIFIED:
                for file_reference in manifest.files:
                    filename = _generation_relative_filename(manifest, file_reference.relative_path)
                    data = data_files.get(file_reference.logical_name)
                    if data is None:
                        raise UnifiedActivationStoreError(
                            f"falta el contenido de {file_reference.logical_name!r} para "
                            "persistir la generacion"
                        )
                    target = temp_dir / filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    reread = target.read_bytes()
                    if (
                        len(reread) != file_reference.byte_size
                        or _hash_bytes(reread) != file_reference.sha256
                    ):
                        raise UnifiedActivationStoreError(
                            f"fallo al revalidar {file_reference.logical_name!r} "
                            "inmediatamente despues de escribirlo"
                        )

            manifest_path = temp_dir / _MANIFEST_FILENAME
            atomic_write_json(manifest_path, manifest)
            reread_manifest = MaterializedGenerationManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            if reread_manifest.to_stable_json() != manifest.to_stable_json():
                raise UnifiedActivationStoreError(
                    "fallo al revalidar manifest.json inmediatamente despues de escribirlo"
                )

            if destination_dir.is_dir():
                # Otro escritor gano la carrera (defensa adicional: el
                # lock single-writer deberia hacer esto inalcanzable en
                # operacion normal).
                return self._reconcile_existing_generation(manifest)
            atomic_promote_directory(temp_dir, destination_dir)
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
        return self.read_generation_manifest(manifest.generation_id)

    def _reconcile_existing_generation(
        self, manifest: MaterializedGenerationManifest
    ) -> MaterializedGenerationManifest:
        """`generation_id` se deriva EXCLUSIVAMENTE de `{lane, kind,
        run_id, file_hashes}` (`compute_generation_id`, Parte 2) --
        NUNCA de `authorization_hash`/`activation_evaluation_hash`
        (metadatos de PROVENANCE: que autorizacion/evaluacion peticiono
        esta materializacion, no que contiene). Dos peticiones
        legitimas y distintas (p. ej. inicializar V1 durante un canary,
        y luego volver a construir "el mismo" V1 durante un fallback
        posterior) pueden producir manifiestos con el MISMO
        `generation_id` pero metadatos de provenance diferentes -- la
        reconciliacion compara UNICAMENTE lo que realmente participa
        de la identidad (`lane`/`kind`/`run_id`/`files`); el manifiesto
        YA PERSISTIDO (primero en escribirse) es la fuente de verdad
        para el resto de sus campos, nunca se sobrescribe."""
        existing = self.read_generation_manifest(manifest.generation_id)
        existing_identity = (
            existing.lane,
            existing.kind,
            existing.run_id,
            [(f.logical_name, f.sha256, f.byte_size) for f in existing.files],
        )
        candidate_identity = (
            manifest.lane,
            manifest.kind,
            manifest.run_id,
            [(f.logical_name, f.sha256, f.byte_size) for f in manifest.files],
        )
        if existing_identity != candidate_identity:
            raise UnifiedActivationStoreError(
                f"colision de contenido: la generacion {manifest.generation_id} ya existe "
                "con archivos/lane/kind/run_id distintos"
            )
        self.validate_generation_files(existing)
        return existing

    # ------------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------------

    def _event_path(self, event_id: str) -> Path:
        _validate_id_component(event_id, label="event_id")
        return self._events_dir / f"{event_id}.json"

    def event_exists(self, event_id: str) -> bool:
        path = self._event_path(event_id)
        return path.is_file() and not path.is_symlink()

    def read_event(self, event_id: str) -> ActivationTransitionEvent:
        path = self._event_path(event_id)
        if path.is_symlink() or not path.is_file():
            raise UnifiedActivationStoreError(f"evento inexistente: {event_id}")
        try:
            event = ActivationTransitionEvent.model_validate_json(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise UnifiedActivationStoreError(f"evento corrupto: {event_id}") from exc
        if event.event_id != event_id:
            raise UnifiedActivationStoreError(
                f"event.event_id ({event.event_id!r}) no coincide con el archivo ({event_id!r})"
            )
        return event

    def persist_event(self, event: ActivationTransitionEvent) -> ActivationTransitionEvent:
        """Idempotente: el mismo `event_id` con el mismo contenido se
        reutiliza sin reescribir; contenido distinto es una colision
        (nunca deberia ocurrir dado que `event_id` es determinista
        sobre el estado completo -- Parte 2, principio 3)."""
        self._events_dir.mkdir(parents=True, exist_ok=True)
        path = self._event_path(event.event_id)
        if path.is_file():
            existing = self.read_event(event.event_id)
            if existing.to_stable_json() != event.to_stable_json():
                raise UnifiedActivationStoreError(
                    f"colision de contenido: el evento {event.event_id} ya existe con "
                    "contenido distinto"
                )
            return existing
        atomic_write_json(path, event)
        reread = self.read_event(event.event_id)
        if reread.to_stable_json() != event.to_stable_json():
            raise UnifiedActivationStoreError(
                "fallo al revalidar el evento inmediatamente despues de escribirlo"
            )
        return reread


__all__ = ["UnifiedActivationStore"]
