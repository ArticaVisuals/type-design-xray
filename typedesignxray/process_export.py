"""Timed frame export for the Font Design Process player.

The process player owns font parsing and SVG composition.  This module stays
deliberately independent of that UI: callers may pass ready-made SVG strings,
records containing an ``svg`` field, or arbitrary layer records plus a
``renderer`` callback.  That boundary keeps animation encoding reusable and
avoids a process-player/exporter import cycle.

Single-glyph process frames are 540 x 766; composed-word frames are 1080 x 766.
Animations use a caller-selected output scale (2x by default). Every non-final
layer uses the caller-selected delay; the completed final state is always held
for exactly 1000 milliseconds. Single-glyph GIFs may loop, while word exports
can stop.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .render.raster import svg_to_png


FRAME_WIDTH = 540
FRAME_HEIGHT = 766
ANIMATION_SCALE = 2
ANIMATION_WIDTH = FRAME_WIDTH * ANIMATION_SCALE
ANIMATION_HEIGHT = FRAME_HEIGHT * ANIMATION_SCALE
DEFAULT_FRAME_DELAY_MS = 200.0
FINAL_HOLD_MS = 1000.0

_CONTENT_TYPES = {
    "gif": "image/gif",
    "mp4": "video/mp4",
    "png": "image/png",
    "svg": "image/svg+xml",
}
_MIN_FRAME_DELAY_MS = 1.0
_MAX_FRAME_DELAY_MS = 60_000.0
# Give each PNG input a one-millisecond time base so every selectable player
# delay is represented accurately.  Repeating the final file makes ffconcat
# honor its preceding duration and contributes one final one-millisecond
# packet, which is subtracted from the final declaration below.
_FFCONCAT_FRAMERATE = 1000
_FFCONCAT_FINAL_PACKET_MS = 1000.0 / _FFCONCAT_FRAMERATE
# GIF timestamps use centiseconds.  The repeated final packet therefore needs
# a 10 ms terminal delay rather than the PNG input's one-millisecond packet.
_GIF_FINAL_PACKET_MS = 10.0


class ProcessExportError(RuntimeError):
    """A clean, user-facing process export failure."""


@dataclass(frozen=True)
class ProcessFrame:
    """A ready-to-export process frame.

    This type is optional convenience.  Mapping records with equivalent
    ``svg`` and ``is_master`` fields work equally well, and raw SVG strings
    implicitly treat the final item as the active master.
    """

    svg: str
    is_master: bool = False
    layer_id: str = ""
    layer_name: str = ""


@dataclass(frozen=True)
class _ResolvedFrame:
    svg: str
    is_master: bool


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError("{} must be a number".format(name))
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("{} must be a number".format(name)) from exc
    if not math.isfinite(number):
        raise ValueError("{} must be a finite number".format(name))
    return number


def _frame_delay(value: Any) -> float:
    delay = _number(value, "frame_delay_ms")
    if not _MIN_FRAME_DELAY_MS <= delay <= _MAX_FRAME_DELAY_MS:
        raise ValueError(
            "frame_delay_ms must be between {:g} and {:g}".format(
                _MIN_FRAME_DELAY_MS, _MAX_FRAME_DELAY_MS
            )
        )
    return delay


def _svg_dimension(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    text = value.strip().lower()
    if text.endswith("px"):
        text = text[:-2].strip()
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _validate_frame_svg(
    svg: Any,
    frame_width: int = FRAME_WIDTH,
    frame_height: int = FRAME_HEIGHT,
) -> str:
    if not isinstance(svg, str) or not svg.strip():
        raise ValueError("each process frame must render to a non-empty SVG string")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError("process frame is not valid SVG: {}".format(exc)) from exc
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ValueError("process frame root must be an SVG element")

    view_box = root.get("viewBox")
    if view_box:
        parts = view_box.replace(",", " ").split()
        try:
            values = [float(part) for part in parts]
        except ValueError as exc:
            raise ValueError("process frame has an invalid viewBox") from exc
        valid_view_box = (
            len(values) == 4
            and all(math.isfinite(value) for value in values)
            and math.isclose(values[0], 0.0)
            and math.isclose(values[1], 0.0)
            and math.isclose(values[2], float(frame_width))
            and math.isclose(values[3], float(frame_height))
        )
        if not valid_view_box:
            raise ValueError(
                "process frame viewBox must be 0 0 {} {}".format(
                    frame_width, frame_height
                )
            )
        return svg

    width = _svg_dimension(root.get("width"))
    height = _svg_dimension(root.get("height"))
    if width != frame_width or height != frame_height:
        raise ValueError(
            "process frame must be {} x {}".format(frame_width, frame_height)
        )
    return svg


def _record_svg(
    record: Any,
    renderer: Optional[Callable[[Any], str]],
    frame_width: int,
    frame_height: int,
) -> str:
    if renderer is not None:
        return _validate_frame_svg(renderer(record), frame_width, frame_height)
    if isinstance(record, str):
        return _validate_frame_svg(record, frame_width, frame_height)
    if isinstance(record, Mapping) and "svg" in record:
        return _validate_frame_svg(record["svg"], frame_width, frame_height)
    if hasattr(record, "svg"):
        return _validate_frame_svg(
            getattr(record, "svg"), frame_width, frame_height
        )
    raise ValueError(
        "process frame records require an svg field or a renderer callback"
    )


def _master_marker(record: Any) -> Tuple[bool, bool]:
    """Return ``(marker_was_provided, marker_value)`` for one record."""
    if isinstance(record, str):
        return (False, False)
    if isinstance(record, Mapping) and "is_master" in record:
        value = record["is_master"]
        if not isinstance(value, bool):
            raise ValueError("is_master must be true or false")
        return (True, value)
    if hasattr(record, "is_master"):
        value = getattr(record, "is_master")
        if not isinstance(value, bool):
            raise ValueError("is_master must be true or false")
        return (True, value)
    return (False, False)


def _resolve_frames(
    records: Sequence[Any],
    renderer: Optional[Callable[[Any], str]],
    frame_width: int,
    frame_height: int,
) -> Tuple[_ResolvedFrame, ...]:
    if renderer is not None and not callable(renderer):
        raise ValueError("renderer must be callable")
    if isinstance(records, (str, bytes)) or not isinstance(records, Sequence):
        raise ValueError("frames must be a sequence")
    items = list(records)
    if not items:
        raise ValueError("at least one process frame is required")

    svgs: List[str] = []
    markers: List[Tuple[bool, bool]] = []
    for record in items:
        svgs.append(
            _record_svg(record, renderer, frame_width, frame_height)
        )
        markers.append(_master_marker(record))

    explicit = any(provided for provided, _ in markers)
    if explicit:
        master_indexes = [
            index
            for index, (provided, value) in enumerate(markers)
            if provided and value
        ]
        if len(master_indexes) != 1:
            raise ValueError(
                "process frames must identify exactly one active master layer"
            )
        if master_indexes[0] != len(items) - 1:
            raise ValueError("the active master layer must be the final process frame")
        master_index = master_indexes[0]
    else:
        # A plain SVG sequence is already ordered by the process player.  Its
        # last frame is, by contract, the selected active master.
        master_index = len(items) - 1

    return tuple(
        _ResolvedFrame(svg=svg, is_master=index == master_index)
        for index, svg in enumerate(svgs)
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
    message = "ffmpeg process export failed with exit code {}".format(
        completed.returncode
    )
    if detail:
        message = "{}: {}".format(message, detail)
    raise ProcessExportError(message)


def _ffmpeg_number(value: float) -> str:
    return format(float(value), ".9f").rstrip("0").rstrip(".")


def _write_concat_manifest(
    path: Path,
    frame_paths: Sequence[Path],
    durations_ms: Sequence[float],
) -> None:
    if len(frame_paths) != len(durations_ms) or not frame_paths:
        raise ValueError("frame paths and durations must be non-empty and aligned")
    lines = ["ffconcat version 1.0"]
    for frame_path, duration_ms in zip(frame_paths, durations_ms):
        # Files live beside the manifest and have exporter-controlled names,
        # avoiding concat-demuxer escaping differences across platforms.
        lines.append("file {}".format(frame_path.name))
        lines.append("option framerate {}".format(_FFCONCAT_FRAMERATE))
        lines.append("duration {}".format(_ffmpeg_number(duration_ms / 1000.0)))
    # ffconcat ignores the last duration unless another file follows it.
    # Repeat the final still, then cap output at the intended total duration.
    lines.append("file {}".format(frame_paths[-1].name))
    lines.append("option framerate {}".format(_FFCONCAT_FRAMERATE))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _encode_timed_frames(
    ffmpeg: str,
    manifest: Path,
    destination: Path,
    format_name: str,
    loop: bool = True,
) -> None:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(manifest),
    ]
    if format_name == "gif":
        command.extend(
            [
                "-filter_complex",
                "[0:v]split[source][palette_source];"
                "[palette_source]palettegen=stats_mode=diff[palette];"
                "[source][palette]paletteuse=dither=sierra2_4a",
                "-vsync",
                "vfr",
                "-loop",
                "0" if loop else "-1",
                # Keep the repeated final packet to one GIF tick.  Its 10 ms
                # is compensated in the final manifest duration so one pass
                # still ends at the requested timestamp.
                "-final_delay",
                "1",
            ]
        )
    else:
        command.extend(
            [
                "-vsync",
                "vfr",
                "-c:v",
                "libx264",
                # B-frame reordering can make an MP4 container report only
                # the short pre-hold duration for a sparse VFR still stream.
                # Process frames do not benefit from B-frames, and disabling
                # them preserves the final concat timestamp in the container.
                "-bf",
                "0",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
            ]
        )
    # The repeated final still is what makes ffconcat preserve its preceding
    # timestamp in both containers; its one-packet duration is compensated in
    # the manifest rather than cutting off the stream with ``-t``.
    # The high-resolution PNG input time base keeps selectable delays aligned
    # with the live player; GIF output is naturally quantized to centiseconds.
    command.append(str(destination))
    _run_ffmpeg(command)


def _normalise_format(
    value: Optional[str], destination: Path, allowed: Sequence[str]
) -> str:
    format_name = (value or destination.suffix.lstrip(".")).strip().lower()
    if format_name not in allowed:
        raise ValueError("format must be one of: {}".format(", ".join(allowed)))
    return format_name


def _destination_path(
    output_path: Any, format_name: str, default_stem: str
) -> Path:
    destination = Path(os.path.expanduser(os.fspath(output_path))).resolve()
    if destination.is_dir() or not destination.suffix:
        destination = destination / "{}.{}".format(default_stem, format_name)
    elif destination.suffix.lower() != ".{}".format(format_name):
        raise ValueError(
            "output extension must be .{} for {} export".format(
                format_name, format_name.upper()
            )
        )
    return destination


def export_process_frame(
    svg: str,
    output_path: Any,
    *,
    format_name: Optional[str] = None,
    frame_width: int = FRAME_WIDTH,
    frame_height: int = FRAME_HEIGHT,
) -> Dict[str, Any]:
    """Export one logical process frame as SVG or PNG."""
    validated_svg = _validate_frame_svg(svg, frame_width, frame_height)
    provisional = Path(os.path.expanduser(os.fspath(output_path))).resolve()
    resolved_format = _normalise_format(format_name, provisional, ("png", "svg"))
    destination = _destination_path(
        output_path, resolved_format, "font-design-process-frame"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if resolved_format == "svg":
        destination.write_text(validated_svg, encoding="utf-8")
    else:
        try:
            svg_to_png(validated_svg, destination, width=frame_width)
        except Exception as exc:
            raise ProcessExportError(
                "Unable to rasterize process frame: {}".format(exc)
            ) from exc
    return {
        "path": str(destination),
        "name": destination.name,
        "content_type": _CONTENT_TYPES[resolved_format],
        "format": resolved_format,
        "frame_count": 1,
        "width": frame_width,
        "height": frame_height,
    }


def export_process_animation(
    frames: Sequence[Any],
    output_path: Any,
    *,
    renderer: Optional[Callable[[Any], str]] = None,
    format_name: Optional[str] = None,
    frame_delay_ms: Any = DEFAULT_FRAME_DELAY_MS,
    frame_width: int = FRAME_WIDTH,
    frame_height: int = FRAME_HEIGHT,
    animation_scale: int = ANIMATION_SCALE,
    loop: bool = True,
) -> Dict[str, Any]:
    """Export ordered process frames as a variable-duration GIF or MP4.

    ``frames`` may be SVG strings, :class:`ProcessFrame` objects, mappings with
    an ``svg`` field, or arbitrary layer records when ``renderer`` is supplied.
    Records that expose ``is_master`` must identify exactly one master and put
    it last.  A plain SVG sequence implicitly treats its last frame as master.
    """
    if isinstance(loop, bool) is False:
        raise ValueError("loop must be true or false")
    if frame_width <= 0 or frame_height <= 0 or animation_scale <= 0:
        raise ValueError("frame dimensions and animation_scale must be positive")
    resolved_frames = _resolve_frames(
        frames, renderer, frame_width, frame_height
    )
    delay = _frame_delay(frame_delay_ms)
    durations_ms = [delay] * len(resolved_frames)
    durations_ms[-1] = FINAL_HOLD_MS
    total_duration_ms = sum(durations_ms)

    provisional = Path(os.path.expanduser(os.fspath(output_path))).resolve()
    resolved_format = _normalise_format(format_name, provisional, ("gif", "mp4"))
    destination = _destination_path(
        output_path, resolved_format, "font-design-process"
    )
    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        raise ProcessExportError(
            "ffmpeg is required for GIF/MP4 export but was not found on PATH"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="type-design-xray-process-"
    ) as temporary_directory:
        temporary = Path(temporary_directory)
        frame_paths: List[Path] = []
        for index, frame in enumerate(resolved_frames):
            frame_path = temporary / "frame-{:06d}.png".format(index)
            try:
                svg_to_png(
                    frame.svg,
                    frame_path,
                    width=frame_width * animation_scale,
                )
            except Exception as exc:
                raise ProcessExportError(
                    "Unable to rasterize process frames: {}".format(exc)
                ) from exc
            frame_paths.append(frame_path)

        manifest = temporary / "frames.ffconcat"
        encoded_durations_ms = list(durations_ms)
        final_packet_ms = (
            _GIF_FINAL_PACKET_MS
            if resolved_format == "gif"
            else _FFCONCAT_FINAL_PACKET_MS
        )
        encoded_durations_ms[-1] -= final_packet_ms
        _write_concat_manifest(manifest, frame_paths, encoded_durations_ms)
        staged = temporary / "process.{}".format(resolved_format)
        _encode_timed_frames(
            ffmpeg,
            manifest,
            staged,
            resolved_format,
            loop,
        )
        if not staged.is_file():
            raise ProcessExportError("ffmpeg did not create the process export file")
        os.replace(str(staged), str(destination))

    return {
        "path": str(destination),
        "name": destination.name,
        "content_type": _CONTENT_TYPES[resolved_format],
        "format": resolved_format,
        "frame_count": len(resolved_frames),
        "master_frame": len(resolved_frames),
        "frame_delay_ms": delay,
        "final_hold_ms": FINAL_HOLD_MS,
        "frame_durations_ms": durations_ms,
        "duration_ms": total_duration_ms,
        "width": frame_width * animation_scale,
        "height": frame_height * animation_scale,
        "animation_scale": animation_scale,
        "loop": loop,
    }


__all__ = [
    "FRAME_WIDTH",
    "FRAME_HEIGHT",
    "ANIMATION_SCALE",
    "ANIMATION_WIDTH",
    "ANIMATION_HEIGHT",
    "DEFAULT_FRAME_DELAY_MS",
    "FINAL_HOLD_MS",
    "ProcessExportError",
    "ProcessFrame",
    "export_process_frame",
    "export_process_animation",
]
