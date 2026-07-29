"""Optional raster and PDF export backends for rendered SVG strings."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, List, Optional


def _load_cairosvg() -> Any:
    try:
        return importlib.import_module("cairosvg")
    except Exception:
        return None


def _find_command(name: str) -> Optional[str]:
    try:
        return shutil.which(name)
    except Exception:
        return None


def available_backends() -> List[str]:
    """Report usable exporters without making CairoSVG a hard dependency."""
    backends: List[str] = []
    if _load_cairosvg() is not None:
        backends.append("cairosvg")
    if _find_command("resvg") is not None:
        backends.append("resvg")
    if _find_command("rsvg-convert") is not None:
        backends.append("rsvg-convert")
    return backends


def _missing_backend(format_name: str) -> RuntimeError:
    qualification = (
        " (resvg can export PNG only)" if format_name.upper() == "PDF" else ""
    )
    return RuntimeError(
        "No SVG backend is available for {} export{}. Install the Python "
        "backend with: pip install \"glyphblueprint[raster]\". On macOS, "
        "install Cairo first with: brew install cairo. Alternatively, install "
        "the resvg or rsvg-convert command-line tool.".format(
            format_name.upper(), qualification
        )
    )


def _svg_bytes(svg: Any) -> bytes:
    if isinstance(svg, bytes):
        return svg
    if isinstance(svg, str):
        return svg.encode("utf-8")
    raise TypeError("svg must be a string or bytes, got {}".format(type(svg).__name__))


def _run_backend(command: List[str], svg: bytes, backend: str) -> None:
    completed = subprocess.run(
        command,
        input=svg,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        message = "{} export failed with exit code {}".format(
            backend, completed.returncode
        )
        if detail:
            message = "{}: {}".format(message, detail)
        raise RuntimeError(message)


def svg_to_png(svg: Any, out_path: Any, width: Optional[int] = None) -> Path:
    """Write an SVG string as PNG, optionally overriding its raster width."""
    if width is not None and width <= 0:
        raise ValueError("PNG width must be greater than zero")

    payload = _svg_bytes(svg)
    destination = Path(out_path)
    cairosvg = _load_cairosvg()
    resvg = _find_command("resvg")
    rsvg_convert = _find_command("rsvg-convert")
    if cairosvg is None and resvg is None and rsvg_convert is None:
        raise _missing_backend("PNG")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if cairosvg is not None:
        options = {
            "bytestring": payload,
            "write_to": str(destination),
        }
        if width is not None:
            options["output_width"] = width
        cairosvg.svg2png(**options)
        return destination

    if resvg is not None:
        with tempfile.NamedTemporaryFile(suffix=".svg") as source:
            source.write(payload)
            source.flush()
            command = [resvg]
            if width is not None:
                command.extend(["--width", str(width)])
            command.extend([source.name, str(destination)])
            _run_backend(command, b"", "resvg")
        return destination

    command = [rsvg_convert, "--format", "png", "--output", str(destination)]
    if width is not None:
        command.extend(["--width", str(width)])
    _run_backend(command, payload, "rsvg-convert")
    return destination


def svg_to_pdf(svg: Any, out_path: Any) -> Path:
    """Write an SVG string as PDF using a vector-capable backend."""
    payload = _svg_bytes(svg)
    destination = Path(out_path)
    cairosvg = _load_cairosvg()
    rsvg_convert = _find_command("rsvg-convert")
    if cairosvg is None and rsvg_convert is None:
        raise _missing_backend("PDF")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if cairosvg is not None:
        cairosvg.svg2pdf(bytestring=payload, write_to=str(destination))
        return destination

    command = [
        rsvg_convert,
        "--format",
        "pdf",
        "--output",
        str(destination),
    ]
    _run_backend(command, payload, "rsvg-convert")
    return destination


__all__ = ["svg_to_png", "svg_to_pdf", "available_backends"]
