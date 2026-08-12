"""Genera SBOM (Syft) y escanea vulnerabilidades (Grype) de una imagen
Docker YA PRESENTE localmente. Tooling EXCLUSIVAMENTE externo -- Syft/
Grype nunca se instalan dentro de la imagen runtime, solo se invocan
desde este script de release (`subprocess`, nunca `pip install` de un
scanner dentro de `Dockerfile`).

Uso:
    python -m scripts.release.sbom_scan <image-ref> --output-dir DIR

Distingue explicitamente dos resultados nunca confundidos entre si:
    TOOL_EXECUTION_FAILED    -- syft/grype ausentes o el proceso fallo.
    VULNERABILITIES_FOUND    -- el tooling corrio bien; SI reporta CVEs.

Sin politica de "fail on HIGH" impuesta aqui (Fase 15B4-B Seccion 22:
esa es una decision de seguridad del cliente, no de este script) --
`--fail-on-vulnerabilities` es opt-in y apagado por defecto.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


class ToolExecutionError(RuntimeError):
    """El tooling externo (Syft/Grype) esta ausente o fallo -- nunca se
    confunde con `VULNERABILITIES_FOUND` (that's a successful scan
    with findings)."""


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ToolExecutionError(
            f"{name!r} no esta disponible en PATH -- instalar Syft/Grype antes de "
            "ejecutar este script (tooling externo, nunca dentro de la imagen runtime)"
        )
    return path


def generate_sbom(image_ref: str, output_path: Path) -> None:
    syft = _require_tool("syft")
    result = subprocess.run(  # noqa: S603 - ejecutable resuelto via shutil.which, sin shell
        [syft, image_ref, "-o", f"spdx-json={output_path}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ToolExecutionError(f"syft fallo generando SBOM de {image_ref!r}: {result.stderr}")


def scan_image(image_ref: str, output_path: Path) -> dict[str, object]:
    grype = _require_tool("grype")
    result = subprocess.run(  # noqa: S603 - ejecutable resuelto via shutil.which, sin shell
        [grype, image_ref, "-o", "json"],
        capture_output=True,
        text=True,
    )
    # Grype devuelve exit!=0 tambien cuando SOLO reporta vulnerabilidades
    # (segun su propia config de --fail-on) -- se distingue por si stdout
    # es JSON valido: si lo es, el tooling corrio bien y el resultado son
    # los hallazgos reales, nunca un fallo de ejecucion.
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ToolExecutionError(f"grype fallo escaneando {image_ref!r}: {result.stderr}") from exc
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    matches = report.get("matches", [])
    severities: dict[str, int] = {}
    for match in matches:
        severity = ((match.get("vulnerability") or {}).get("severity")) or "Unknown"
        severities[severity] = severities.get(severity, 0) + 1
    return {"total_findings": len(matches), "by_severity": severities}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_ref")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--fail-on-vulnerabilities",
        action="store_true",
        help="Opt-in: exit!=0 si Grype encuentra CUALQUIER hallazgo (apagado por defecto).",
    )
    args = parser.parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = args.image_ref.replace("/", "_").replace(":", "_").replace("@", "_")
    sbom_path = args.output_dir / f"{safe_name}.spdx.json"
    scan_path = args.output_dir / f"{safe_name}.grype.json"

    try:
        generate_sbom(args.image_ref, sbom_path)
        scan_summary = scan_image(args.image_ref, scan_path)
    except ToolExecutionError as exc:
        print(f"TOOL_EXECUTION_FAILED: {exc}", file=sys.stderr)
        return 2

    result = {
        "image_ref": args.image_ref,
        "sbom_path": str(sbom_path),
        "scan_report_path": str(scan_path),
        "scan_summary": scan_summary,
    }
    print(json.dumps(result, indent=2))

    if args.fail_on_vulnerabilities and scan_summary["total_findings"] > 0:
        print("VULNERABILITIES_FOUND (--fail-on-vulnerabilities activo)", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
