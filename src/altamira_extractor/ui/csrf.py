"""Verificacion CSRF minima para las 2 rutas HTML mutantes (Prompt 13d):
`POST /ui/runs` y `POST /ui/runs/{run_id}/resume`.

V1 no tiene autenticacion ni sesiones: un token CSRF firmado sin una
sesion real que lo respalde daria una falsa sensacion de seguridad
(`.claude/rules/security.md` no exige uno, y CLAUDE.md documenta V1 sin
auth). En su lugar, se exige que el origen de la request (`Origin`, o
`Referer` como fallback) coincida EXACTAMENTE con el origen que sirve
esta aplicacion -- scheme, host y port. Nunca se confia en `HX-Request`,
`Sec-Fetch-Site` como unica defensa, el filename del upload ni cookies
(no existe ninguna en V1).

Esta proteccion NO reemplaza autenticacion: la aplicacion debe
restringirse mediante controles de red externos (firewall/red interna),
igual que ya documenta `api/app.py` sobre el acceso general sin auth."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request

from ..api.errors import ApiError


class CsrfRejectedError(ApiError):
    """`Origin`/`Referer` ausentes o de un origen distinto al que sirve
    esta aplicacion, en una request HTML mutante. 403."""

    def __init__(self, message: str = "origen de la solicitud no valido") -> None:
        super().__init__(message, status_code=403, code="csrf_rejected")


def _origin_of(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def verify_same_origin(request: Request) -> None:
    """Orden exacto exigido: `Origin` si esta presente (debe coincidir);
    si falta, `Referer` (debe coincidir); si ambos faltan, rechaza."""
    expected = _origin_of(str(request.base_url))

    origin = request.headers.get("origin")
    if origin is not None:
        if origin != expected:
            raise CsrfRejectedError()
        return

    referer = request.headers.get("referer")
    if referer is not None:
        if _origin_of(referer) != expected:
            raise CsrfRejectedError()
        return

    raise CsrfRejectedError()
