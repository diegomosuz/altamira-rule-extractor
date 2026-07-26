"""Verificacion CSRF minima para las 2 rutas HTML mutantes (Prompt 13d):
`POST /ui/runs` y `POST /ui/runs/{run_id}/resume`.

V1 no tiene autenticacion ni sesiones: un token CSRF firmado sin una
sesion real que lo respalde daria una falsa sensacion de seguridad
(`.claude/rules/security.md` no exige uno, y CLAUDE.md documenta V1 sin
auth). En su lugar, se exige que el origen de la request (`Origin`, o
`Referer` como fallback) coincida EXACTAMENTE con el origen que sirve
esta aplicacion -- scheme, host y port. Nunca se confia en `HX-Request`,
el filename del upload ni cookies (no existe ninguna en V1).

Caso especial documentado (checkpoint correctivo): algunos navegadores
envian `Origin: null` en navegaciones same-origin legitimas (el caso
observado es una subida de archivo -- `<form method="post"
enctype="multipart/form-data">` -- served desde `http://127.0.0.1:8000`
hacia si mismo). Ese `Origin: null` NUNCA se acepta por si solo: la
unica senal adicional confiable para distinguirlo de un `Origin: null`
cross-site (iframe sandboxed, redirect, documento `file://`, etc.) es
`Sec-Fetch-Site: same-origin`, que el navegador fija sin que el
contenido de la pagina pueda alterarlo. Si esa cabecera no es
exactamente `same-origin` (falta, o vale `same-site`/`cross-site`/
`none`), un `Origin: null` se rechaza igual que cualquier otro origen
no coincidente -- nunca se recurre a `Referer` ni a `Host` para
salvarlo, y nunca se generaliza a aceptar `Origin: null` sin esa senal.

Esta proteccion NO reemplaza autenticacion: la aplicacion debe
restringirse mediante controles de red externos (firewall/red interna),
igual que ya documenta `api/app.py` sobre el acceso general sin auth."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Request

from ..api.errors import ApiError

_OPAQUE_ORIGIN = "null"
_SAFE_SEC_FETCH_SITE = "same-origin"


class CsrfRejectedError(ApiError):
    """`Origin`/`Referer` ausentes o de un origen distinto al que sirve
    esta aplicacion, en una request HTML mutante. 403."""

    def __init__(self, message: str = "origen de la solicitud no valido") -> None:
        super().__init__(message, status_code=403, code="csrf_rejected")


def _origin_of(url: str) -> str:
    """Solo para `request.base_url`: siempre bien formado (lo construye
    Starlette, nunca un cliente), por lo que `urlsplit` nunca levanta
    aqui."""
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _parsed_untrusted_origin(value: str) -> str | None:
    """Normaliza un `Origin`/`Referer` de un cliente no confiable.
    `urlsplit` puede levantar `ValueError` con entradas malformadas (p.
    ej. `http://[::1` sin cerrar), y un valor sin scheme/netloc no es un
    origen valido -- en ambos casos se devuelve `None` (nunca 500)."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def verify_same_origin(request: Request) -> None:
    """Orden exacto exigido: `Origin` si esta presente (debe coincidir,
    con el caso especial de `Origin: null` descrito en el docstring del
    modulo); si falta, `Referer` (debe coincidir); si ambos faltan,
    rechaza."""
    expected = _origin_of(str(request.base_url))

    origin = request.headers.get("origin")
    if origin is not None:
        if origin.strip() == _OPAQUE_ORIGIN:
            if request.headers.get("sec-fetch-site") == _SAFE_SEC_FETCH_SITE:
                return
            raise CsrfRejectedError()

        normalized_origin = _parsed_untrusted_origin(origin)
        if normalized_origin is None or normalized_origin != expected:
            raise CsrfRejectedError()
        return

    referer = request.headers.get("referer")
    if referer is not None:
        normalized_referer = _parsed_untrusted_origin(referer)
        if normalized_referer is None or normalized_referer != expected:
            raise CsrfRejectedError()
        return

    raise CsrfRejectedError()
