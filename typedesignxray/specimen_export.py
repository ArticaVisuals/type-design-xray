"""GIF and MP4 export for the source-driven specimen player.

The exporter deliberately reparses the source with ``master=`` for every
export.  It never asks Glyphs for a named/special layer, so component
decomposition and contour selection stay anchored to the selected master.

No animation package is required.  Existing SVG raster backends create the
individual PNG frames and ffmpeg assembles them into GIF or H.264 MP4 output.
"""

from __future__ import annotations

import html
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import ir
from .compound import compound_glyph
from .parsers import load_font
from .render.raster import svg_to_png
from .specimen import (
    _designed_pairs,
    _font_path,
    _glyph_metadata,
    _glyph_svg,
    _metadata_text,
    _ordered_glyph_names,
    _specimen_colors,
    _svg_number,
)


FRAME_WIDTH = 1080
FRAME_HEIGHT = 766
_CONTENT_TYPES = {
    "gif": "image/gif",
    "mp4": "video/mp4",
    "png": "image/png",
    "svg": "image/svg+xml",
}
_MIN_POINT_SIZE = 48.0
_MAX_POINT_SIZE = 520.0
_MIN_FPS = 1.0
_MAX_FPS = 60.0


class SpecimenExportError(RuntimeError):
    """A clean, user-facing export failure."""


def _has_actual_contours(glyph: ir.Glyph) -> bool:
    """Return true only when the selected layer contains drawable geometry."""
    return any(contour.nodes for contour in glyph.contours)


def designed_glyph_names(font: ir.Font) -> Tuple[str, ...]:
    """Every glyph with master-layer contours, in deterministic source order."""
    return tuple(
        name
        for name in _ordered_glyph_names(font)
        if _has_actual_contours(font.glyphs[name])
    )


def designed_sequence(
    font: ir.Font,
) -> Tuple[Tuple[str, Optional[str]], ...]:
    """Build exhaustive two-up frames without ever repeating the odd glyph.

    Authored uppercase/lowercase counterparts lead the sequence, matching the
    reference animation.  Every remaining designed glyph follows in Unicode
    and glyph-name order.  The final right slot is ``None`` when the count is
    odd; duplicating the left glyph would make the animation non-exhaustive.
    """
    ordered = designed_glyph_names(font)
    pairs = _designed_pairs(font)
    flattened = [name for pair in pairs for name in pair if name is not None]
    if len(flattened) != len(set(flattened)) or set(flattened) != set(ordered):
        raise AssertionError("specimen sequence must contain every glyph exactly once")
    return tuple(pairs)


def _compound_sequence_font(
    font: ir.Font,
    sequence: Sequence[Tuple[str, Optional[str]]],
) -> ir.Font:
    """Remove overlaps only in glyphs that will actually be rendered."""
    selected_names = {
        name for pair in sequence for name in pair if name is not None
    }
    glyphs = dict(font.glyphs)
    for name in selected_names:
        glyphs[name] = compound_glyph(glyphs[name])
    return replace(
        font,
        glyphs=glyphs,
        node_types_exact=font.node_types_exact
        and all(glyphs[name].node_types_exact for name in selected_names),
    )


def _frame_number(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError("{} must be an integer".format(name))
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("{} must be an integer".format(name)) from exc
    try:
        exact = float(value)
    except (TypeError, ValueError, OverflowError):
        exact = float(number)
    if not math.isfinite(exact) or exact != number:
        raise ValueError("{} must be an integer".format(name))
    return number


def _select_frames(
    sequence: Sequence[Tuple[str, Optional[str]]],
    start_frame: Any = 1,
    end_frame: Any = None,
) -> Tuple[Tuple[Tuple[str, Optional[str]], ...], int, int]:
    total = len(sequence)
    start = _frame_number(start_frame, "start_frame")
    end = total if end_frame is None else _frame_number(end_frame, "end_frame")
    if not 1 <= start <= total:
        raise ValueError("start_frame must be between 1 and {}".format(total))
    if not start <= end <= total:
        raise ValueError(
            "end_frame must be between start_frame and {}".format(total)
        )
    return tuple(sequence[start - 1 : end]), start, end


def _formatted_metric(value: Any) -> str:
    if value is None:
        return "—"
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return format(number, ".3f").rstrip("0").rstrip(".")


def _formatted_upm(value: Any) -> str:
    if value is None:
        return "—"
    number = float(value)
    if not number.is_integer():
        return _formatted_metric(value)
    integer = int(number)
    sign = "-" if integer < 0 else ""
    return "{}{}".format(sign, str(abs(integer)).zfill(3))


def _metadata_lines(
    font: ir.Font, glyph: ir.Glyph, point_size: float
) -> Tuple[str, ...]:
    metadata = _glyph_metadata(glyph)
    family = _metadata_text(getattr(font, "family_name", "")) or "UNTITLED"
    master = _metadata_text(getattr(font, "master_name", "")) or "REGULAR"
    return (
        "TYPEFACE: {}".format(family.upper()),
        "",
        "STYLE:    {}".format(master.upper()),
        "SIZE:     {} pt".format(_formatted_metric(point_size)),
        "",
        "GLYPH:    {}".format(metadata["category"]),
        "GROUP:    {}".format(metadata["group"]),
        "",
        "NAME:     {}".format(metadata["name"]),
        "UNICODE:  {}".format(metadata["unicode"]),
        "",
        "|↔|:      {} upm".format(_formatted_upm(metadata["width"])),
        "|←|:      {} upm".format(_formatted_upm(metadata["lsb"])),
        " →|:      {} upm".format(_formatted_upm(metadata["rsb"])),
    )


def _metadata_svg(
    font: ir.Font,
    glyph: ir.Glyph,
    point_size: float,
    x: float,
    colors: Dict[str, str],
) -> str:
    lines = _metadata_lines(font, glyph, point_size)
    spans = []
    for index, line in enumerate(lines):
        escaped = html.escape(line, quote=False) if line else "&#160;"
        spans.append(
            '<tspan x="{x}" dy="{dy}">{line}</tspan>'.format(
                x=_svg_number(x),
                dy="0" if index == 0 else "15",
                line=escaped,
            )
        )
    return (
        '<text x="{x}" y="20" fill="{text}" font-size="14" '
        'font-family="SFMono-Regular, Menlo, Consolas, Liberation Mono, '
        'monospace" letter-spacing="1.4">{spans}</text>'
    ).format(
        x=_svg_number(x),
        text=colors["text"],
        spans="".join(spans),
    )


def render_frame_svg(
    font: ir.Font,
    left_name: str,
    right_name: Optional[str],
    *,
    point_size: float = 370.0,
    xray: bool = False,
    colors: Optional[Dict[str, str]] = None,
) -> str:
    """Render one exact 1080 x 766 reference-style animation frame."""
    if not math.isfinite(float(point_size)) or not (
        _MIN_POINT_SIZE <= float(point_size) <= _MAX_POINT_SIZE
    ):
        raise ValueError(
            "point_size must be between {:g} and {:g}".format(
                _MIN_POINT_SIZE, _MAX_POINT_SIZE
            )
        )
    if left_name not in font.glyphs:
        raise ValueError("glyph {!r} was not found".format(left_name))
    if right_name is not None and right_name not in font.glyphs:
        raise ValueError("glyph {!r} was not found".format(right_name))

    mode = "xray" if xray else "solid"
    palette = _specimen_colors({"colors": colors or {}})
    panels: List[str] = []
    for name, panel_x, text_x in (
        (left_name, 18.0, 20.0),
        (right_name, 558.0, 560.0),
    ):
        if name is None:
            continue
        glyph = font.glyphs[name]
        panel_svg = _glyph_svg(
            font,
            glyph,
            float(point_size),
            mode,
            palette,
        )[0]
        panels.append(
            _metadata_svg(
                font,
                glyph,
                float(point_size),
                text_x,
                palette,
            )
        )
        panels.append(
            '<g transform="translate({x} 256)">{svg}</g>'.format(
                x=_svg_number(panel_x), svg=panel_svg
            )
        )

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="766" '
        'viewBox="0 0 1080 766" data-specimen-frame="true" '
        'data-left="{left}" data-right="{right}" data-mode="{mode}">'
        '<rect width="1080" height="766" fill="{background}"/>'
        '<path d="M 19 245.5 H 522 M 559 245.5 H 1063" '
        'fill="none" stroke="{guides}" stroke-width="1"/>{panels}</svg>'
    ).format(
        left=html.escape(left_name, quote=True),
        right=html.escape(right_name or "", quote=True),
        mode=mode,
        background=palette["background"],
        guides=palette["guides"],
        panels="".join(panels),
    )


def _find_ffmpeg() -> Optional[str]:
    try:
        return shutil.which("ffmpeg")
    except Exception:
        return None


def _run_ffmpeg(command: Sequence[str]) -> None:
    completed = subprocess.run(
        list(command),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode == 0:
        return
    detail = completed.stderr.decode("utf-8", errors="replace").strip()
    message = "ffmpeg export failed with exit code {}".format(
        completed.returncode
    )
    if detail:
        message = "{}: {}".format(message, detail)
    raise SpecimenExportError(message)


def _encode_frames(
    ffmpeg: str,
    frame_pattern: Path,
    destination: Path,
    format_name: str,
    fps: float,
) -> None:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-framerate",
        _svg_number(fps),
        "-i",
        str(frame_pattern),
    ]
    if format_name == "gif":
        command.extend(
            [
                "-filter_complex",
                "[0:v]split[source][palette_source];"
                "[palette_source]palettegen=stats_mode=diff[palette];"
                "[source][palette]paletteuse=dither=sierra2_4a",
                "-loop",
                "-1",
            ]
        )
    else:
        command.extend(
            [
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
            ]
        )
    command.append(str(destination))
    _run_ffmpeg(command)


def _normalise_format(value: Optional[str], destination: Path) -> str:
    format_name = (value or destination.suffix.lstrip(".")).strip().lower()
    if format_name not in _CONTENT_TYPES:
        raise ValueError("format must be one of: gif, mp4, png, svg")
    return format_name


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("._-")
    return cleaned[:100] or "specimen"


def _destination_path(
    output_path: Any, font: ir.Font, format_name: str
) -> Path:
    destination = Path(os.path.expanduser(os.fspath(output_path))).resolve()
    if destination.is_dir() or not destination.suffix:
        family = _metadata_text(getattr(font, "family_name", "")) or "font"
        master = _metadata_text(getattr(font, "master_name", "")) or "master"
        filename = _slug("{}-{}-specimen".format(family, master))
        destination = destination / "{}.{}".format(filename, format_name)
    elif destination.suffix.lower() != ".{}".format(format_name):
        raise ValueError(
            "output extension must be .{} for {} export".format(
                format_name, format_name.upper()
            )
        )
    return destination


def export_specimen(
    font_path: Any,
    output_path: Any,
    *,
    master: Optional[str] = None,
    format_name: Optional[str] = None,
    point_size: float = 370.0,
    fps: float = 5.0,
    xray: bool = False,
    colors: Optional[Dict[str, str]] = None,
    start_frame: Any = 1,
    end_frame: Any = None,
) -> Dict[str, Any]:
    """Export every designed glyph from the selected master exactly once.

    The returned dictionary is directly useful to an HTTP route: ``path`` is
    the local file to stream, while ``name`` and ``content_type`` are suitable
    response headers.
    """
    source = _font_path(os.fspath(font_path))
    if not isinstance(master, (str, type(None))):
        raise ValueError("master must be a string")
    selected_master = master.strip() if isinstance(master, str) else None
    selected_master = selected_master or None
    if isinstance(xray, bool) is False:
        raise ValueError("xray must be true or false")
    try:
        numeric_fps = float(fps)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("fps must be a number") from exc
    if not math.isfinite(numeric_fps) or not _MIN_FPS <= numeric_fps <= _MAX_FPS:
        raise ValueError(
            "fps must be between {:g} and {:g}".format(_MIN_FPS, _MAX_FPS)
        )

    # Intentionally never pass ``layer`` here.  For Glyphs sources this makes
    # the chosen master layer the sole source of exported geometry.
    font = load_font(source, master=selected_master)
    provisional = Path(os.path.expanduser(os.fspath(output_path))).resolve()
    resolved_format = _normalise_format(format_name, provisional)
    if resolved_format not in ("gif", "mp4"):
        raise ValueError("animation format must be 'gif' or 'mp4'")
    destination = _destination_path(output_path, font, resolved_format)
    full_sequence = designed_sequence(font)
    if not full_sequence:
        raise ValueError("the selected master has no glyphs with contours")
    sequence, selected_start, selected_end = _select_frames(
        full_sequence,
        start_frame,
        end_frame,
    )
    palette = _specimen_colors({"colors": colors or {}})
    render_font = _compound_sequence_font(font, sequence) if xray else font

    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        raise SpecimenExportError(
            "ffmpeg is required for GIF/MP4 export but was not found on PATH"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="type-design-xray-specimen-"
    ) as temporary_directory:
        temporary = Path(temporary_directory)
        for index, (left, right) in enumerate(sequence):
            svg = render_frame_svg(
                render_font,
                left,
                right,
                point_size=float(point_size),
                xray=xray,
                colors=palette,
            )
            frame_path = temporary / "frame-{:06d}.png".format(index)
            try:
                svg_to_png(svg, frame_path, width=FRAME_WIDTH)
            except Exception as exc:
                raise SpecimenExportError(
                    "Unable to rasterize specimen frames: {}".format(exc)
                ) from exc

        staged = temporary / "specimen.{}".format(resolved_format)
        _encode_frames(
            ffmpeg,
            temporary / "frame-%06d.png",
            staged,
            resolved_format,
            numeric_fps,
        )
        if not staged.is_file():
            raise SpecimenExportError("ffmpeg did not create the export file")
        os.replace(str(staged), str(destination))

    return {
        "path": str(destination),
        "name": destination.name,
        "content_type": _CONTENT_TYPES[resolved_format],
        "format": resolved_format,
        "family_name": _metadata_text(getattr(font, "family_name", ""))
        or source.stem,
        "master_name": _metadata_text(getattr(font, "master_name", ""))
        or selected_master
        or "Regular",
        "glyph_count": sum(
            1 for pair in sequence for name in pair if name is not None
        ),
        "frame_count": len(sequence),
        "total_frame_count": len(full_sequence),
        "start_frame": selected_start,
        "end_frame": selected_end,
        "point_size": float(point_size),
        "fps": numeric_fps,
        "xray": xray,
        "colors": palette,
    }


def export_frame(
    font_path: Any,
    output_path: Any,
    *,
    frame_number: Any,
    master: Optional[str] = None,
    format_name: Optional[str] = None,
    point_size: float = 370.0,
    xray: bool = False,
    colors: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Export one selected two-up frame as editable SVG or raster PNG."""
    source = _font_path(os.fspath(font_path))
    if not isinstance(master, (str, type(None))):
        raise ValueError("master must be a string")
    selected_master = master.strip() if isinstance(master, str) else None
    selected_master = selected_master or None
    if not isinstance(xray, bool):
        raise ValueError("xray must be true or false")

    font = load_font(source, master=selected_master)
    full_sequence = designed_sequence(font)
    if not full_sequence:
        raise ValueError("the selected master has no glyphs with contours")
    selected, selected_start, _ = _select_frames(
        full_sequence,
        frame_number,
        frame_number,
    )
    left, right = selected[0]
    palette = _specimen_colors({"colors": colors or {}})
    render_font = _compound_sequence_font(font, selected) if xray else font
    svg = render_frame_svg(
        render_font,
        left,
        right,
        point_size=float(point_size),
        xray=xray,
        colors=palette,
    )

    provisional = Path(os.path.expanduser(os.fspath(output_path))).resolve()
    resolved_format = _normalise_format(format_name, provisional)
    if resolved_format not in ("png", "svg"):
        raise ValueError("frame format must be 'png' or 'svg'")
    destination = _destination_path(output_path, font, resolved_format)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if resolved_format == "svg":
        destination.write_text(svg, encoding="utf-8")
    else:
        try:
            svg_to_png(svg, destination, width=FRAME_WIDTH)
        except Exception as exc:
            raise SpecimenExportError(
                "Unable to rasterize specimen frame: {}".format(exc)
            ) from exc

    return {
        "path": str(destination),
        "name": destination.name,
        "content_type": _CONTENT_TYPES[resolved_format],
        "format": resolved_format,
        "family_name": _metadata_text(getattr(font, "family_name", ""))
        or source.stem,
        "master_name": _metadata_text(getattr(font, "master_name", ""))
        or selected_master
        or "Regular",
        "glyph_count": 1 if right is None else 2,
        "frame_count": 1,
        "total_frame_count": len(full_sequence),
        "start_frame": selected_start,
        "end_frame": selected_start,
        "point_size": float(point_size),
        "xray": xray,
        "colors": palette,
    }


def export_request(
    payload: Dict[str, Any], *, output_dir: Optional[Any] = None
) -> Dict[str, Any]:
    """Validate a JSON-like request and export into a route-owned directory."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    raw_font_path = payload.get("font_path")
    if not isinstance(raw_font_path, str) or not raw_font_path.strip():
        raise ValueError("font_path is required")
    format_name = str(payload.get("format", "gif")).strip().lower()
    if output_dir is not None:
        raw_name = payload.get("output_name", "specimen.{}".format(format_name))
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("output_name must be a non-empty string")
        if Path(raw_name).name != raw_name:
            raise ValueError("output_name must be a filename, not a path")
        output_path = Path(output_dir) / raw_name
    else:
        output_path = payload.get("output_path")
        if not isinstance(output_path, str) or not output_path.strip():
            raise ValueError("output_path is required when output_dir is omitted")

    xray = payload.get("xray", payload.get("bezier", False))
    common = {
        "master": payload.get("master"),
        "format_name": format_name,
        "point_size": payload.get("point_size", 370.0),
        "xray": xray,
        "colors": payload.get("colors"),
    }
    if format_name in ("png", "svg"):
        return export_frame(
            raw_font_path.strip(),
            output_path,
            frame_number=payload.get("frame"),
            **common,
        )
    return export_specimen(
        raw_font_path.strip(),
        output_path,
        fps=payload.get("fps", 5.0),
        start_frame=payload.get("start_frame", 1),
        end_frame=payload.get("end_frame"),
        **common,
    )


__all__ = [
    "FRAME_WIDTH",
    "FRAME_HEIGHT",
    "SpecimenExportError",
    "designed_glyph_names",
    "designed_sequence",
    "render_frame_svg",
    "export_frame",
    "export_specimen",
    "export_request",
]
