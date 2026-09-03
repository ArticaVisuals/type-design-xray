"""Source-layer catalogue and rendering for the Font Design Process player.

Unlike the specimen player, this workflow follows one glyph through the
authored layers in a Glyphs source.  Named layers remain in source order and
the selected master is appended as the final, active frame.  Layer IDs are the
only render selectors because human-readable layer names are not guaranteed to
be unique.
"""

from __future__ import annotations

import html
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import ir
from .compound import compound_glyph
from .layout import layout_string
from .parsers import list_font_layers, load_font
from .specimen import (
    _all_paths,
    _font_path,
    _format_number,
    _glyph_metadata,
    _glyph_svg,
    _handle_geometry,
    _metadata_text,
    _node_geometry,
    _number,
    _ordered_glyph_names,
    _selected_master_id,
    _source_masters,
    _specimen_colors,
    _string,
    _svg_number,
    _vertical_frame,
)
from .specimen_export import _metadata_svg, _slug


FINAL_HOLD_MS = 1000
DEFAULT_FRAME_DELAY_MS = 200
PROCESS_FRAME_WIDTH = 540
PROCESS_FRAME_HEIGHT = 766
PROCESS_ANIMATION_WIDTH = 1080
PROCESS_ANIMATION_HEIGHT = 1532
WORD_FRAME_WIDTH = 1080
WORD_FRAME_HEIGHT = 766
WORD_ANIMATION_WIDTH = 2160
WORD_ANIMATION_HEIGHT = 1532
_WORD_PANEL_WIDTH = 1044.0
_WORD_PANEL_HEIGHT = 510.0
_WORD_MAX_CHARACTERS = 32
_POINT_SIZE_MIN = 48.0
_POINT_SIZE_MAX = 520.0


def _process_font_path(value: str) -> Path:
    path = _font_path(value)
    if path.suffix.lower() != ".glyphs":
        raise ValueError(
            "Font Design Process Video requires an editable .glyphs source file"
        )
    return path


def _cache_key(
    path: Path, master: Optional[str], layer_id: Optional[str]
) -> Tuple[str, str, str, int, int]:
    stat = path.stat()
    return (
        str(path),
        master or "",
        layer_id or "",
        int(stat.st_mtime_ns),
        int(stat.st_size),
    )


@lru_cache(maxsize=128)
def _cached_layer_font(
    path: str,
    master: str,
    layer_id: str,
    _mtime_ns: int,
    _size: int,
) -> ir.Font:
    return load_font(
        path,
        master=master or None,
        layer=layer_id or None,
    )


def _load_layer_font(
    path: Path, master: Optional[str], layer_id: Optional[str]
) -> ir.Font:
    return _cached_layer_font(*_cache_key(path, master, layer_id))


@lru_cache(maxsize=512)
def _cached_compounded_layer_glyph(
    path: str,
    master: str,
    layer_id: str,
    mtime_ns: int,
    size: int,
    glyph_name: str,
) -> ir.Glyph:
    font = _cached_layer_font(path, master, layer_id, mtime_ns, size)
    return compound_glyph(font.glyphs[glyph_name])


@lru_cache(maxsize=512)
def _cached_source_layers(
    path: str,
    glyph_name: str,
    _mtime_ns: int,
    _size: int,
) -> Tuple[ir.LayerInfo, ...]:
    return tuple(list_font_layers(path, glyph_name))


def _source_layers(path: Path, glyph_name: str) -> Tuple[ir.LayerInfo, ...]:
    stat = path.stat()
    return _cached_source_layers(
        str(path),
        glyph_name,
        int(stat.st_mtime_ns),
        int(stat.st_size),
    )


def clear_cache() -> None:
    """Forget cached Glyphs parses after an uploaded source changes."""
    _cached_layer_font.cache_clear()
    _cached_compounded_layer_glyph.cache_clear()
    _cached_source_layers.cache_clear()


def _boolean(payload: Dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError("{} must be true or false".format(key))
    return value


def _glyph_input(payload: Dict[str, Any]) -> str:
    value = payload.get("glyph", payload.get("glyph_name", ""))
    if not isinstance(value, str):
        raise ValueError("glyph must be a string")
    value = value.strip()
    if not value:
        raise ValueError("glyph is required")
    if value.startswith("/"):
        value = value[1:]
    if not value:
        raise ValueError("glyph name after / must not be empty")
    return value


def _content_mode(payload: Dict[str, Any]) -> str:
    value = _string(payload, "content_mode", "single").lower()
    if value not in ("single", "word"):
        raise ValueError("content_mode must be 'single' or 'word'")
    return value


def _animation_mode(payload: Dict[str, Any]) -> str:
    value = _string(payload, "animation_mode", "sequential").lower()
    if value not in ("sequential", "simultaneous"):
        raise ValueError(
            "animation_mode must be 'sequential' or 'simultaneous'"
        )
    return value


def _word_input(payload: Dict[str, Any]) -> str:
    value = payload.get("text", payload.get("glyph", ""))
    if not isinstance(value, str):
        raise ValueError("text must be a string")
    value = value.strip()
    if not value:
        raise ValueError("word text is required")
    if len(value) > _WORD_MAX_CHARACTERS:
        raise ValueError(
            "word text may contain at most {} characters".format(
                _WORD_MAX_CHARACTERS
            )
        )
    return value


def _resolve_glyph_name(font: ir.Font, requested: str) -> str:
    # Exact authored glyph names win, including single-character names.
    if requested in font.glyphs:
        return requested
    if len(requested) == 1:
        name = font.cmap.get(ord(requested))
        if name in font.glyphs:
            return str(name)
    raise ValueError(
        "glyph {!r} was not found; enter a character or exact Glyphs glyph name".format(
            requested
        )
    )


def _master_name(
    masters: Sequence[Dict[str, str]], master_id: str, fallback: str
) -> str:
    for item in masters:
        if item["id"] == master_id:
            return item["name"]
    return fallback or master_id or "Regular"


def _layer_sequence(
    layers: Sequence[ir.LayerInfo],
    selected_master_id: str,
    selected_master_name: str,
    *,
    include_unassociated: bool,
) -> List[Dict[str, Any]]:
    """Start at authored skeletons, then keep remaining order, master last."""
    active_master: Optional[ir.LayerInfo] = None
    authored: List[ir.LayerInfo] = []
    for layer in layers:
        if layer.is_master:
            if layer.layer_id == selected_master_id:
                active_master = layer
            continue
        associated = layer.associated_master_id
        if associated and associated != selected_master_id:
            continue
        if not associated and not include_unassociated:
            continue
        authored.append(layer)

    if active_master is None:
        raise ValueError(
            "selected master layer {!r} was not found on this glyph".format(
                selected_master_id
            )
        )

    # Glyphs normally stores a process skeleton first, but the source format
    # does not require that ordering.  Stable-partition skeleton-like layers
    # to the front so playback always begins with the construction drawing
    # while preserving the designer's order within both groups.
    skeletons = [layer for layer in authored if "skel" in layer.name.casefold()]
    other_authored = [
        layer for layer in authored if "skel" not in layer.name.casefold()
    ]
    ordered = skeletons + other_authored + [active_master]
    result: List[Dict[str, Any]] = []
    for index, layer in enumerate(ordered):
        is_final = index == len(ordered) - 1
        raw_name = layer.name.strip()
        name = raw_name or (
            selected_master_name if is_final else "Untitled layer"
        )
        result.append(
            {
                "id": index,
                "layer_id": layer.layer_id,
                "name": name,
                "label": "{} · {}{}".format(
                    index + 1,
                    name,
                    " — ACTIVE" if is_final else "",
                ),
                "is_master": layer.is_master,
                "is_final": is_final,
                "is_skeleton": "skel" in name.casefold(),
                "associated_master_id": layer.associated_master_id,
                "contour_count": layer.contour_count,
                "has_open_contours": layer.has_open_contours,
                "delay_ms": FINAL_HOLD_MS if is_final else DEFAULT_FRAME_DELAY_MS,
            }
        )
    return result


def _catalog(
    path: Path, requested_master: Optional[str], requested_glyph: str
) -> Tuple[ir.Font, str, str, List[Dict[str, str]], List[Dict[str, Any]]]:
    font = _load_layer_font(path, requested_master, None)
    masters = _source_masters(path)
    if not masters:
        raise ValueError("the Glyphs source contains no font masters")
    parsed_master_name = _metadata_text(getattr(font, "master_name", ""))
    selected_master_id = _selected_master_id(
        masters,
        requested_master,
        parsed_master_name,
    )
    selected_master_name = _master_name(
        masters, selected_master_id, parsed_master_name
    )
    glyph_name = _resolve_glyph_name(font, requested_glyph)
    layers = _layer_sequence(
        _source_layers(path, glyph_name),
        selected_master_id,
        selected_master_name,
        include_unassociated=len(masters) == 1,
    )
    return font, glyph_name, selected_master_id, masters, layers


def _word_sequence(
    glyphs: Sequence[Dict[str, Any]], animation_mode: str
) -> List[Dict[str, Any]]:
    """Build a deterministic word timeline from per-glyph layer sequences."""
    frames: List[Dict[str, Any]] = []
    if animation_mode == "sequential":
        for glyph_index, glyph in enumerate(glyphs):
            glyph_layers = glyph["layers"]
            for layer_index, active_layer in enumerate(glyph_layers):
                selections = []
                for index, item in enumerate(glyphs):
                    if index < glyph_index:
                        selections.append(item["layers"][-1]["layer_id"])
                    elif index == glyph_index:
                        selections.append(active_layer["layer_id"])
                    else:
                        # Future glyphs are deliberately absent until their
                        # own animation begins.
                        selections.append(None)
                frames.append(
                    {
                        "layer_id": "sequential:{}:{}".format(
                            glyph_index, layer_index
                        ),
                        "glyph_layers": selections,
                        "active_glyph_index": glyph_index,
                        "active_layer_index": layer_index,
                        "name": "{} · {}".format(
                            glyph["character"] or glyph["name"],
                            active_layer["name"],
                        ),
                    }
                )
    else:
        frame_count = max(len(glyph["layers"]) for glyph in glyphs)
        for layer_index in range(frame_count):
            selections = []
            labels = []
            for glyph in glyphs:
                selected_index = min(layer_index, len(glyph["layers"]) - 1)
                selected = glyph["layers"][selected_index]
                selections.append(selected["layer_id"])
                labels.append(selected["name"])
            unique_labels = list(dict.fromkeys(labels))
            frames.append(
                {
                    "layer_id": "simultaneous:{}".format(layer_index),
                    "glyph_layers": selections,
                    "active_glyph_index": None,
                    "active_layer_index": layer_index,
                    "name": " / ".join(unique_labels),
                }
            )

    for index, frame in enumerate(frames):
        is_final = index == len(frames) - 1
        frame.update(
            {
                "id": index,
                "label": "{} · {}{}".format(
                    index + 1,
                    frame["name"],
                    " — COMPLETE" if is_final else "",
                ),
                "is_master": is_final,
                "is_final": is_final,
                "delay_ms": (
                    FINAL_HOLD_MS if is_final else DEFAULT_FRAME_DELAY_MS
                ),
            }
        )
    return frames


def _catalog_word(
    path: Path,
    requested_master: Optional[str],
    text: str,
    animation_mode: str,
) -> Tuple[
    ir.Font,
    str,
    List[Dict[str, str]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:
    active_font = _load_layer_font(path, requested_master, None)
    masters = _source_masters(path)
    if not masters:
        raise ValueError("the Glyphs source contains no font masters")
    parsed_master_name = _metadata_text(
        getattr(active_font, "master_name", "")
    )
    selected_master_id = _selected_master_id(
        masters, requested_master, parsed_master_name
    )
    selected_master_name = _master_name(
        masters, selected_master_id, parsed_master_name
    )
    run = layout_string(active_font, text, apply_kerning=True, missing="error")
    if not run.glyphs:
        raise ValueError("word text did not resolve to any glyphs")

    glyphs: List[Dict[str, Any]] = []
    for index, positioned in enumerate(run.glyphs):
        glyph = positioned.glyph
        layers = _layer_sequence(
            _source_layers(path, glyph.name),
            selected_master_id,
            selected_master_name,
            include_unassociated=len(masters) == 1,
        )
        glyphs.append(
            {
                "index": index,
                "name": glyph.name,
                "character": positioned.source_char or "",
                "origin_x": _format_number(positioned.origin_x),
                "kern_before": _format_number(positioned.kern_before),
                "glyph": _glyph_metadata(glyph),
                "layers": layers,
            }
        )
    return (
        active_font,
        selected_master_id,
        masters,
        glyphs,
        _word_sequence(glyphs, animation_mode),
    )


def catalog_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a single-glyph or composed-word process sequence."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    path = _process_font_path(_string(payload, "font_path"))
    master = _string(payload, "master") or None
    content_mode = _content_mode(payload)
    if content_mode == "word":
        text = _word_input(payload)
        animation_mode = _animation_mode(payload)
        font, selected_master_id, masters, glyphs, frames = _catalog_word(
            path, master, text, animation_mode
        )
        return {
            "font_path": str(path),
            "family_name": _metadata_text(getattr(font, "family_name", ""))
            or path.stem,
            "master_name": _master_name(
                masters,
                selected_master_id,
                _metadata_text(getattr(font, "master_name", "")),
            ),
            "selected_master_id": selected_master_id,
            "source_format": "glyphs",
            "units_per_em": _format_number(font.units_per_em),
            "masters": masters,
            "content_mode": "word",
            "animation_mode": animation_mode,
            "text": text,
            "glyphs": glyphs,
            "glyph_count": len(glyphs),
            "layers": frames,
            "sequence": frames,
            "normal_delay_ms": DEFAULT_FRAME_DELAY_MS,
            "final_hold_ms": FINAL_HOLD_MS,
            "frame_size": {
                "width": WORD_FRAME_WIDTH,
                "height": WORD_FRAME_HEIGHT,
            },
            "animation_size": {
                "width": WORD_ANIMATION_WIDTH,
                "height": WORD_ANIMATION_HEIGHT,
            },
        }
    requested_glyph = _glyph_input(payload)
    font, glyph_name, selected_master_id, masters, layers = _catalog(
        path, master, requested_glyph
    )
    glyph = font.glyphs[glyph_name]
    glyph_choices = [
        {
            "name": name,
            "character": _glyph_metadata(font.glyphs[name])["character"],
        }
        for name in _ordered_glyph_names(font)
    ]
    result = {
        "font_path": str(path),
        "family_name": _metadata_text(getattr(font, "family_name", ""))
        or path.stem,
        "master_name": _master_name(
            masters,
            selected_master_id,
            _metadata_text(getattr(font, "master_name", "")),
        ),
        "selected_master_id": selected_master_id,
        "source_format": "glyphs",
        "content_mode": "single",
        "units_per_em": _format_number(font.units_per_em),
        "masters": masters,
        "glyph": _glyph_metadata(glyph),
        "glyph_input": requested_glyph,
        "glyph_choices": glyph_choices,
        "layers": layers,
        # ``sequence`` makes the catalogue convenient to generic players while
        # ``layers`` remains the clearer public name for this workflow.
        "sequence": layers,
        "normal_delay_ms": DEFAULT_FRAME_DELAY_MS,
        "final_hold_ms": FINAL_HOLD_MS,
        "frame_size": {
            "width": PROCESS_FRAME_WIDTH,
            "height": PROCESS_FRAME_HEIGHT,
        },
        "animation_size": {
            "width": PROCESS_ANIMATION_WIDTH,
            "height": PROCESS_ANIMATION_HEIGHT,
        },
    }
    return result


def _selected_layer(
    layers: Sequence[Dict[str, Any]], requested: str
) -> Dict[str, Any]:
    if not requested:
        return layers[0]
    for item in layers:
        if item["layer_id"] == requested:
            return item
    raise ValueError("layer_id was not found in this glyph's process sequence")


def _process_glyph_svg(
    font: ir.Font,
    glyph: ir.Glyph,
    point_size: float,
    bezier: bool,
    handles: bool,
    colors: Dict[str, str],
) -> Tuple[str, str, float]:
    """Render solid layers normally while keeping open skeletons visible."""
    if bezier:
        mode = "xray"
    elif any(not contour.closed for contour in glyph.contours):
        mode = "outline"
    else:
        mode = "solid"
    return _glyph_svg(
        font,
        glyph,
        point_size,
        mode,
        colors,
        show_handles=bezier and handles,
        show_nodes=bezier,
    )


def _word_glyph_geometry(
    glyph: ir.Glyph,
    bezier: bool,
    handles: bool,
    colors: Dict[str, str],
    scale: float,
) -> str:
    path = html.escape(_all_paths(glyph), quote=True)
    if bezier:
        marker_radius = 3.0 / scale
        return (
            '<path class="xray-fill" d="{path}" fill="{fill}" '
            'fill-opacity="0.08" fill-rule="nonzero"/>'
            '<path class="native-outline" d="{path}" fill="none" '
            'stroke="{stroke}" stroke-width="1.25" '
            'vector-effect="non-scaling-stroke"/>{handles}{nodes}'
        ).format(
            path=path,
            fill=colors["fill"],
            stroke=colors["stroke"],
            handles=(
                _handle_geometry(glyph, marker_radius, colors)
                if handles
                else ""
            ),
            nodes=_node_geometry(glyph, marker_radius, colors),
        )
    if any(not contour.closed for contour in glyph.contours):
        return (
            '<path class="native-outline" d="{path}" fill="none" '
            'stroke="{stroke}" stroke-width="1.25" '
            'vector-effect="non-scaling-stroke"/>'
        ).format(path=path, stroke=colors["stroke"])
    return (
        '<path class="solid-outline" d="{path}" fill="{fill}" '
        'fill-rule="nonzero"/>'
    ).format(path=path, fill=colors["fill"])


def _word_panel_svg(
    path: Path,
    active_font: ir.Font,
    selected_master_id: str,
    glyphs: Sequence[Dict[str, Any]],
    frame: Dict[str, Any],
    point_size: float,
    bezier: bool,
    handles: bool,
    colors: Dict[str, str],
) -> Tuple[str, float]:
    upem = active_font.units_per_em if active_font.units_per_em > 0 else 1000.0
    low, high = _vertical_frame(active_font)
    vertical_units = max(high - low, 1.0)
    total_advance = max(
        float(glyphs[-1]["origin_x"])
        + active_font.glyphs[glyphs[-1]["name"]].advance_width,
        1.0,
    )
    scale = min(
        point_size / upem,
        (_WORD_PANEL_WIDTH - 72.0) / total_advance,
        (_WORD_PANEL_HEIGHT - 72.0) / vertical_units,
    )
    baseline = ((_WORD_PANEL_HEIGHT - vertical_units * scale) * 0.5) + (
        high * scale
    )
    run_x = (_WORD_PANEL_WIDTH - total_advance * scale) * 0.5
    selections = frame["glyph_layers"]
    rendered_glyphs: List[str] = []
    for index, (glyph_record, layer_id) in enumerate(zip(glyphs, selections)):
        if layer_id is None:
            continue
        glyph_name = str(glyph_record["name"])
        layer_font = _load_layer_font(path, selected_master_id, str(layer_id))
        glyph = layer_font.glyphs[glyph_name]
        if bezier:
            glyph = _cached_compounded_layer_glyph(
                *_cache_key(path, selected_master_id, str(layer_id)),
                glyph_name,
            )
        geometry = _word_glyph_geometry(
            glyph, bezier, handles, colors, scale
        )
        x = run_x + float(glyph_record["origin_x"]) * scale
        rendered_glyphs.append(
            '<g class="word-glyph" data-word-index="{index}" '
            'data-glyph="{glyph}" data-layer-id="{layer}" '
            'transform="translate({x} {baseline})">'
            '<g class="font-unit-geometry" transform="scale({scale} {negative})">'
            '{geometry}</g></g>'.format(
                index=index,
                glyph=html.escape(glyph_name, quote=True),
                layer=html.escape(str(layer_id), quote=True),
                x=_svg_number(x),
                baseline=_svg_number(baseline),
                scale=_svg_number(scale),
                negative=_svg_number(-scale),
                geometry=geometry,
            )
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'viewBox="0 0 1044 510" width="1044" height="510" '
        'role="img" aria-label="Word design process" '
        'data-process-word-panel="true" data-frame-id="{frame_id}" '
        'data-visible-glyphs="{visible}">{glyphs}</svg>'.format(
            frame_id=html.escape(str(frame["layer_id"]), quote=True),
            visible=sum(layer_id is not None for layer_id in selections),
            glyphs="".join(rendered_glyphs),
        ),
        scale,
    )


def _render_word_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    path = _process_font_path(_string(payload, "font_path"))
    master = _string(payload, "master") or None
    text = _word_input(payload)
    animation_mode = _animation_mode(payload)
    point_size = _number(
        payload, "point_size", 370.0, _POINT_SIZE_MIN, _POINT_SIZE_MAX
    )
    bezier = _boolean(payload, "bezier", True)
    handles = _boolean(payload, "handles", True)
    show_metadata = _boolean(payload, "show_metadata", True)
    colors = _specimen_colors(payload)
    active_font, selected_master_id, masters, glyphs, frames = _catalog_word(
        path, master, text, animation_mode
    )
    frame = _selected_layer(frames, _string(payload, "layer_id"))
    panel, scale = _word_panel_svg(
        path,
        active_font,
        selected_master_id,
        glyphs,
        frame,
        point_size,
        bezier,
        handles,
        colors,
    )
    return {
        "font_path": str(path),
        "family_name": _metadata_text(getattr(active_font, "family_name", ""))
        or path.stem,
        "master_name": _master_name(
            masters,
            selected_master_id,
            _metadata_text(getattr(active_font, "master_name", "")),
        ),
        "selected_master_id": selected_master_id,
        "content_mode": "word",
        "animation_mode": animation_mode,
        "text": text,
        "glyphs": glyphs,
        "layer": frame,
        "point_size": _format_number(point_size),
        "bezier": bezier,
        "handles": handles,
        "show_metadata": show_metadata,
        "compounded": bezier,
        "colors": colors,
        "font_unit_scale": _format_number(scale),
        "svg": panel,
        "total_frame_count": len(frames),
        "final_hold_ms": FINAL_HOLD_MS,
    }


def render_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Render one exact single-glyph layer or composed-word frame."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    if _content_mode(payload) == "word":
        return _render_word_request(payload)
    path = _process_font_path(_string(payload, "font_path"))
    master = _string(payload, "master") or None
    requested_glyph = _glyph_input(payload)
    point_size = _number(
        payload, "point_size", 370.0, _POINT_SIZE_MIN, _POINT_SIZE_MAX
    )
    bezier = _boolean(payload, "bezier", True)
    handles = _boolean(payload, "handles", True)
    show_metadata = _boolean(payload, "show_metadata", True)
    colors = _specimen_colors(payload)
    active_font, glyph_name, selected_master_id, masters, layers = _catalog(
        path, master, requested_glyph
    )
    layer_id = _string(payload, "layer_id")
    layer = _selected_layer(layers, layer_id)
    layer_font = _load_layer_font(path, selected_master_id, layer["layer_id"])
    glyph = layer_font.glyphs[glyph_name]
    render_glyph = glyph
    compounded = False
    if bezier:
        render_glyph = _cached_compounded_layer_glyph(
            *_cache_key(path, selected_master_id, layer["layer_id"]),
            glyph_name,
        )
        compounded = True
    svg, transform, scale = _process_glyph_svg(
        layer_font,
        render_glyph,
        point_size,
        bezier,
        handles,
        colors,
    )
    # The process changes geometry, not specimen identity. Keep the metadata
    # anchored to the selected active master so it remains stable while backup
    # widths and sidebearings evolve underneath it, matching the reference.
    metadata = _glyph_metadata(active_font.glyphs[glyph_name])
    return {
        "font_path": str(path),
        "family_name": _metadata_text(getattr(active_font, "family_name", ""))
        or path.stem,
        "master_name": _master_name(
            masters,
            selected_master_id,
            _metadata_text(getattr(active_font, "master_name", "")),
        ),
        "selected_master_id": selected_master_id,
        "glyph": metadata,
        "layer_glyph": _glyph_metadata(glyph),
        "layer": layer,
        "point_size": _format_number(point_size),
        "bezier": bezier,
        "handles": handles,
        "show_metadata": show_metadata,
        "compounded": compounded,
        "colors": colors,
        "font_unit_scale": _format_number(scale),
        "transform": transform,
        "svg": svg,
        "final_hold_ms": FINAL_HOLD_MS,
    }


def _word_metadata_svg(rendered: Dict[str, Any]) -> str:
    glyph_names = " / ".join(item["name"] for item in rendered["glyphs"])
    frame = rendered["layer"]
    lines = (
        "TYPEFACE: {}".format(str(rendered["family_name"]).upper()),
        "",
        "STYLE:    {}".format(str(rendered["master_name"]).upper()),
        "SIZE:     {} pt".format(rendered["point_size"]),
        "",
        "TEXT:     {}".format(rendered["text"]),
        "GLYPHS:   {}".format(glyph_names),
        "PROCESS:  {}".format(str(rendered["animation_mode"]).upper()),
        "",
        "FRAME:    {} / {}".format(
            int(frame["id"]) + 1, rendered["total_frame_count"]
        ),
        "STATE:    {}".format(
            "COMPLETE" if frame["is_final"] else frame["name"]
        ),
    )
    spans = []
    for index, line in enumerate(lines):
        spans.append(
            '<tspan x="20" dy="{dy}">{line}</tspan>'.format(
                dy="0" if index == 0 else "15",
                line=html.escape(line, quote=False) if line else "&#160;",
            )
        )
    return (
        '<text x="20" y="20" fill="{text}" font-size="14" '
        'font-family="SFMono-Regular, Menlo, Consolas, Liberation Mono, '
        'monospace" letter-spacing="1.4">{spans}</text>'
    ).format(text=rendered["colors"]["text"], spans="".join(spans))


def render_process_frame_svg(payload: Dict[str, Any]) -> str:
    """Compose one exact single-glyph or landscape word process frame."""
    rendered = render_request(payload)
    show_metadata = bool(rendered["show_metadata"])
    panel_y = 256 if show_metadata else 128
    if rendered.get("content_mode") == "word":
        frame = rendered["layer"]
        palette = rendered["colors"]
        chrome = ""
        if show_metadata:
            chrome = (
                '<path d="M 19 245.5 H 1062" fill="none" '
                'stroke="{guides}" stroke-width="1"/>{metadata}'
            ).format(
                guides=palette["guides"],
                metadata=_word_metadata_svg(rendered),
            )
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="766" '
            'viewBox="0 0 1080 766" data-process-frame="true" '
            'data-process-content="word" data-text="{text}" '
            'data-animation-mode="{mode}" data-frame-id="{frame_id}" '
            'data-final="{final}" data-bezier="{bezier}" '
            'data-handles="{handles}" data-show-metadata="{show_metadata}">'
            '<rect width="1080" height="766" fill="{background}"/>'
            '{chrome}<g transform="translate(18 {panel_y})">{panel}</g></svg>'
        ).format(
            text=html.escape(str(rendered["text"]), quote=True),
            mode=html.escape(str(rendered["animation_mode"]), quote=True),
            frame_id=html.escape(str(frame["layer_id"]), quote=True),
            final="true" if frame["is_final"] else "false",
            bezier="true" if rendered["bezier"] else "false",
            handles="true" if rendered["handles"] else "false",
            show_metadata="true" if show_metadata else "false",
            background=palette["background"],
            chrome=chrome,
            panel_y=panel_y,
            panel=rendered["svg"],
        )
    path = _process_font_path(rendered["font_path"])
    layer = rendered["layer"]
    font = _load_layer_font(
        path,
        rendered["selected_master_id"],
        None,
    )
    glyph = font.glyphs[rendered["glyph"]["name"]]
    palette = rendered["colors"]
    chrome = ""
    if show_metadata:
        chrome = (
            '<path d="M 19 245.5 H 522" fill="none" '
            'stroke="{guides}" stroke-width="1"/>{metadata}'
        ).format(
            guides=palette["guides"],
            metadata=_metadata_svg(
                font,
                glyph,
                float(rendered["point_size"]),
                20.0,
                palette,
            ),
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="540" height="766" '
        'viewBox="0 0 540 766" data-process-frame="true" '
        'data-glyph="{glyph}" data-layer-id="{layer_id}" '
        'data-layer-name="{layer_name}" data-final="{final}" '
        'data-bezier="{bezier}" data-handles="{handles}" '
        'data-show-metadata="{show_metadata}">'
        '<rect width="540" height="766" fill="{background}"/>'
        '{chrome}<g transform="translate(18 {panel_y})">{panel}</g></svg>'
    ).format(
        glyph=html.escape(glyph.name, quote=True),
        layer_id=html.escape(str(layer["layer_id"]), quote=True),
        layer_name=html.escape(str(layer["name"]), quote=True),
        final="true" if layer["is_final"] else "false",
        bezier="true" if rendered["bezier"] else "false",
        handles="true" if rendered["handles"] else "false",
        show_metadata="true" if show_metadata else "false",
        background=palette["background"],
        chrome=chrome,
        panel_y=panel_y,
        panel=rendered["svg"],
    )


def _export_destination(
    payload: Dict[str, Any],
    output_dir: Optional[Any],
    format_name: str,
    family_name: str,
    glyph_name: str,
) -> Path:
    if output_dir is not None:
        default_name = "{}-{}-design-process.{}".format(
            _slug(family_name),
            _slug(glyph_name),
            format_name,
        )
        raw_name = payload.get("output_name", default_name)
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("output_name must be a non-empty string")
        if Path(raw_name).name != raw_name:
            raise ValueError("output_name must be a filename, not a path")
        destination = Path(output_dir) / raw_name
    else:
        raw_path = payload.get("output_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("output_path is required when output_dir is omitted")
        destination = Path(os.path.expanduser(raw_path)).resolve()
    if destination.suffix.lower() != ".{}".format(format_name):
        raise ValueError(
            "output extension must be .{} for {} export".format(
                format_name, format_name.upper()
            )
        )
    return destination


def export_request(
    payload: Dict[str, Any], *, output_dir: Optional[Any] = None
) -> Dict[str, Any]:
    """Render and export one layer or the complete one-glyph process."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    format_name = _string(payload, "format", "gif").lower()
    if format_name not in ("svg", "png", "gif", "mp4"):
        raise ValueError("format must be one of: gif, mp4, png, svg")

    catalog = catalog_request(payload)
    is_word = catalog.get("content_mode") == "word"
    target_name = (
        str(catalog["text"])
        if is_word
        else str(catalog["glyph"]["name"])
    )
    destination = _export_destination(
        payload,
        output_dir,
        format_name,
        str(catalog["family_name"]),
        target_name,
    )
    show_metadata = _boolean(payload, "show_metadata", True)
    common = {
        "font_path": catalog["font_path"],
        "master": catalog["selected_master_id"],
        "content_mode": catalog["content_mode"],
        "glyph": target_name,
        "text": catalog.get("text", ""),
        "animation_mode": catalog.get("animation_mode", "sequential"),
        "point_size": payload.get("point_size", 370.0),
        "bezier": payload.get("bezier", True),
        "handles": payload.get("handles", False),
        "show_metadata": show_metadata,
        "colors": payload.get("colors"),
    }

    from .process_export import (
        ProcessFrame,
        export_process_animation,
        export_process_frame,
    )

    if format_name in ("svg", "png"):
        requested_layer_id = _string(payload, "layer_id")
        layer = _selected_layer(catalog["layers"], requested_layer_id)
        svg = render_process_frame_svg(
            dict(common, layer_id=layer["layer_id"])
        )
        result = export_process_frame(
            svg,
            destination,
            format_name=format_name,
            frame_width=(WORD_FRAME_WIDTH if is_word else PROCESS_FRAME_WIDTH),
            frame_height=(
                WORD_FRAME_HEIGHT if is_word else PROCESS_FRAME_HEIGHT
            ),
        )
        result.update(
            {
                "layer_id": layer["layer_id"],
                "layer_name": layer["name"],
                "layer_number": layer["id"] + 1,
                "total_frame_count": len(catalog["layers"]),
            }
        )
    else:
        speed = _number(payload, "speed", 0.2, 0.08, 1.0)
        frames = []
        for layer in catalog["layers"]:
            frames.append(
                ProcessFrame(
                    svg=render_process_frame_svg(
                        dict(common, layer_id=layer["layer_id"])
                    ),
                    is_master=bool(layer["is_final"]),
                    layer_id=str(layer["layer_id"]),
                    layer_name=str(layer["name"]),
                )
            )
        result = export_process_animation(
            frames,
            destination,
            format_name=format_name,
            frame_delay_ms=speed * 1000.0,
            frame_width=(WORD_FRAME_WIDTH if is_word else PROCESS_FRAME_WIDTH),
            frame_height=(
                WORD_FRAME_HEIGHT if is_word else PROCESS_FRAME_HEIGHT
            ),
            loop=not is_word,
        )
        result["total_frame_count"] = len(catalog["layers"])

    result.update(
        {
            "family_name": catalog["family_name"],
            "master_name": catalog["master_name"],
            "content_mode": catalog["content_mode"],
            "animation_mode": catalog.get("animation_mode"),
            "text": catalog.get("text"),
            "glyph_name": target_name,
            "point_size": float(common["point_size"]),
            "bezier": bool(common["bezier"]),
            "handles": bool(common["handles"]),
            "show_metadata": show_metadata,
            "colors": _specimen_colors(
                {"colors": common["colors"] or {}}
            ),
        }
    )
    return result


__all__ = [
    "DEFAULT_FRAME_DELAY_MS",
    "FINAL_HOLD_MS",
    "PROCESS_FRAME_WIDTH",
    "PROCESS_FRAME_HEIGHT",
    "PROCESS_ANIMATION_WIDTH",
    "PROCESS_ANIMATION_HEIGHT",
    "WORD_FRAME_WIDTH",
    "WORD_FRAME_HEIGHT",
    "WORD_ANIMATION_WIDTH",
    "WORD_ANIMATION_HEIGHT",
    "catalog_request",
    "render_request",
    "render_process_frame_svg",
    "export_request",
    "clear_cache",
]
