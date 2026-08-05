"""Consumo atomico de un solo uso para challenges operativos (Fase
15B1, cierre "single-use real").

El challenge stateless firmado sigue siendo la fuente de verdad de la
intencion (`security/operational_challenge.py`); este modulo agrega la
garantia STATEFUL que un token puramente firmado no puede dar por si
solo: una vez consumido -- con exito, con fallo, o en una transicion
idempotente cuyo `pointer_version` no cambia -- el MISMO challenge
nunca vuelve a aceptarse, sin importar el resultado de la operacion
que desencadeno.

Arbol `audit/consumed-challenges/` -- separado de `activation/`, y
tambien separado de `audit/events/` (que registra el HISTORIAL
narrativo legible; esto registra unicamente el HECHO tecnico de
consumo, indexado por hash). Un archivo por challenge, nombrado por su
`challenge_hash` (SHA-256 hexadecimal del token canonico completo,
NUNCA el token en si, NUNCA CSRF, NUNCA cookie), creado con
`os.O_CREAT | os.O_EXCL`: la creacion misma es la seccion critica --
dos requests concurrentes para el MISMO challenge_hash solo pueden
ganar una, la otra falla con `FileExistsError` de forma atomica a
nivel de sistema de archivos, sin necesidad de un lock adicional."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ..contracts.operational_authorization_request import OperationalAction
from ..contracts.operational_challenge_consumption import ConsumedChallengeRecord

_CONSUMED_DIR_NAME = "audit/consumed-challenges"


class ChallengeConsumptionError(Exception):
    """Base de los fallos de este subsistema. El mensaje nunca incluye
    una ruta absoluta ni el token del challenge."""


class ChallengeAlreadyConsumedError(ChallengeConsumptionError):
    """El `challenge_hash` ya tiene un registro persistido -- replay,
    doble submit concurrente (el perdedor de la carrera de
    `O_CREAT|O_EXCL`), o reintento tras un resultado previo (exito,
    fallo, o transicion idempotente). Cualquiera de estos casos exige
    un `prepare` nuevo."""


class ChallengeConsumptionWriteError(ChallengeConsumptionError):
    """Fallo de I/O al escribir el registro de consumo -- el llamador
    NUNCA debe proceder a ejecutar la operacion cuando esto ocurre: es
    preferible abortar (y exigir un challenge nuevo) que arriesgar una
    doble ejecucion sin registro verificable."""


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _format_occurred_at_utc(moment: datetime) -> str:
    utc_moment = moment.astimezone(UTC)
    return utc_moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc_moment.microsecond:06d}Z"


def compute_challenge_hash(challenge_token: str) -> str:
    """SHA-256 hexadecimal del token canonico completo tal como viaja
    en el formulario -- estable, determinista, y suficiente para
    detectar un replay sin persistir nunca el secreto original."""
    return hashlib.sha256(challenge_token.encode("utf-8")).hexdigest()


def _consumed_dir(run_dir: Path) -> Path:
    return run_dir / _CONSUMED_DIR_NAME


def _record_path(run_dir: Path, challenge_hash: str) -> Path:
    # `challenge_hash` es SIEMPRE un hex SHA-256 de 64 caracteres
    # (validado por `Sha256Hex` al construir `ConsumedChallengeRecord`,
    # y por `compute_challenge_hash` mas arriba) -- nunca contiene "/",
    # "\\", "..", ni proviene directamente de un valor del cliente sin
    # pasar por ese calculo, pero se revalida aqui de todas formas como
    # defensa en profundidad contra path traversal, con el mismo
    # criterio que `operational_audit_service.py::
    # _validate_event_id_component`.
    if (
        not challenge_hash
        or len(challenge_hash) != 64
        or "/" in challenge_hash
        or "\\" in challenge_hash
        or challenge_hash in (".", "..")
        or "\x00" in challenge_hash
        or any(ch not in "0123456789abcdef" for ch in challenge_hash)
    ):
        raise ChallengeConsumptionError(f"challenge_hash con forma invalida: {challenge_hash!r}")
    return _consumed_dir(run_dir) / f"{challenge_hash}.json"


def is_challenge_consumed(run_dir: Path, challenge_hash: str) -> bool:
    """Lectura de solo diagnostico -- el camino REAL de proteccion es
    `consume_challenge_atomically`, cuya atomicidad no depende de esta
    funcion (que puede sufrir TOCTOU entre la lectura y una escritura
    concurrente; por eso nunca se usa como el UNICO chequeo antes de
    ejecutar)."""
    path = _record_path(run_dir, challenge_hash)
    return path.is_file() and not path.is_symlink()


def consume_challenge_atomically(
    run_dir: Path,
    *,
    challenge_token: str,
    run_id: str,
    principal_id: str,
    operational_action: OperationalAction,
    clock: Callable[[], datetime] = _default_clock,
) -> str:
    """Consume el challenge de forma atomica e irreversible. Retorna
    `challenge_hash`. Lanza `ChallengeAlreadyConsumedError` si ya fue
    consumido (replay, doble submit concurrente, o reintento tras
    cualquier resultado previo) y `ChallengeConsumptionWriteError` si
    la escritura del registro falla por I/O -- en NINGUNO de los dos
    casos el llamador debe proceder a ejecutar la transicion."""
    challenge_hash = compute_challenge_hash(challenge_token)
    record_path = _record_path(run_dir, challenge_hash)
    record = ConsumedChallengeRecord(
        challenge_hash=challenge_hash,
        run_id=run_id,
        principal_id=principal_id,
        operational_action=operational_action,
        consumed_at_utc=_format_occurred_at_utc(clock()),
    )
    payload = record.to_stable_json().encode("utf-8")

    record_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(record_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ChallengeAlreadyConsumedError(
            "este challenge ya fue consumido -- se requiere un nuevo prepare para reintentar"
        ) from exc
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ChallengeConsumptionWriteError(
            "fallo al persistir el registro de consumo del challenge -- operacion abortada "
            "sin ejecutar, se requiere un nuevo prepare"
        ) from exc
    return challenge_hash


__all__ = [
    "ChallengeAlreadyConsumedError",
    "ChallengeConsumptionError",
    "ChallengeConsumptionWriteError",
    "compute_challenge_hash",
    "consume_challenge_atomically",
    "is_challenge_consumed",
]
