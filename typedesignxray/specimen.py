"""A compact, source-driven specimen player for Type Design X-Ray.

The web server deliberately owns HTTP concerns.  This module owns the page,
catalogue validation, and deterministic SVG rendering so it can be routed by
``web.py`` with only three small calls::

    specimen_page()
    catalog_request(json_payload)
    render_request(json_payload)

The renderer does not fit each glyph independently.  Point size is converted
to one font-unit scale and reused by every panel, which preserves the visual
comparison made by the reference Type Design X-Ray animation.
"""

from __future__ import annotations

import html
import math
import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import ir
from . import parsers as _parsers
from .parsers import load_font
from .tool_nav import tool_switcher


_SUPPORTED_SUFFIXES = frozenset(
    (".glyphs", ".glyphspackage", ".otf", ".ttf", ".woff", ".woff2", ".ufo")
)
_PANEL_WIDTH = 504.0
_PANEL_HEIGHT = 500.0
_POINT_SIZE_MIN = 48.0
_POINT_SIZE_MAX = 520.0
DEFAULT_SPECIMEN_COLORS = {
    "background": "#000000",
    "fill": "#ffffff",
    "stroke": "#ffffff",
    "text": "#ffffff",
    "guides": "#737373",
    "handles": "#8e8e8e",
    "point_fill": "#000000",
    "point_stroke": "#ffffff",
}
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")


def _string(payload: Dict[str, Any], key: str, default: str = "") -> str:
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise ValueError("{} must be a string".format(key))
    return value.strip()


def _number(
    payload: Dict[str, Any],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise ValueError("{} must be a number".format(key))
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("{} must be a number".format(key)) from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(
            "{} must be between {:g} and {:g}".format(key, minimum, maximum)
        )
    return result


def _specimen_colors(payload: Dict[str, Any]) -> Dict[str, str]:
    raw = payload.get("colors", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("colors must be an object")
    unknown = sorted(set(raw) - set(DEFAULT_SPECIMEN_COLORS))
    if unknown:
        raise ValueError(
            "unknown specimen color{}: {}".format(
                "s" if len(unknown) != 1 else "",
                ", ".join(unknown),
            )
        )
    result = dict(DEFAULT_SPECIMEN_COLORS)
    for name, value in raw.items():
        if not isinstance(value, str) or _HEX_COLOR.fullmatch(value) is None:
            raise ValueError("colors.{} must be a six-digit hex color".format(name))
        result[name] = value.lower()
    return result


def _font_path(value: str) -> Path:
    if not value:
        raise ValueError("font_path is required")
    path = Path(os.path.expanduser(value)).resolve()
    if not path.exists():
        raise ValueError("font file does not exist: {}".format(path))
    if path.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise ValueError(
            "unsupported font extension {!r}".format(path.suffix or "(none)")
        )
    return path


def _cache_key(path: Path, master: Optional[str]) -> Tuple[str, str, int, int]:
    stat = path.stat()
    return (
        str(path),
        master or "",
        int(stat.st_mtime_ns),
        int(stat.st_size) if path.is_file() else 0,
    )


@lru_cache(maxsize=24)
def _cached_font(
    path: str, master: str, _mtime_ns: int, _size: int
) -> ir.Font:
    return load_font(path, master=master or None)


def _load(path: Path, master: Optional[str]) -> ir.Font:
    return _cached_font(*_cache_key(path, master))


@lru_cache(maxsize=512)
def _cached_compounded_glyph(
    path: str,
    master: str,
    mtime_ns: int,
    size: int,
    glyph_name: str,
) -> ir.Glyph:
    from .compound import compound_glyph

    font = _cached_font(path, master, mtime_ns, size)
    return compound_glyph(font.glyphs[glyph_name])


def _compound_for_render(
    path: Path,
    master: Optional[str],
    glyph_name: str,
) -> ir.Glyph:
    """Return cached export-style overlap removal for one master glyph."""
    return _cached_compounded_glyph(
        *_cache_key(path, master),
        glyph_name,
    )


def clear_cache() -> None:
    """Clear parsed-font state; useful to hosts which manage uploaded files."""
    _cached_font.cache_clear()
    _cached_compounded_glyph.cache_clear()


def _master_value(item: Any, *names: str) -> str:
    for name in names:
        if isinstance(item, dict):
            value = item.get(name)
        else:
            value = getattr(item, name, None)
        if value not in (None, ""):
            return str(value)
    return ""


def _source_masters(path: Path) -> List[Dict[str, str]]:
    """Read selectable styles while remaining compatible with older parsers."""
    for function_name in ("list_font_masters", "list_masters"):
        lister = getattr(_parsers, function_name, None)
        if lister is None:
            continue
        try:
            raw_items = lister(path)
        except (TypeError, ValueError, OSError):
            continue
        result = []
        for item in raw_items:
            master_id = _master_value(item, "master_id", "id", "layer_id")
            name = _master_value(item, "name", "master_name") or master_id
            if name or master_id:
                result.append({"id": master_id or name, "name": name or master_id})
        if result:
            return result

    if path.suffix.lower() == ".glyphs":
        # The fallback keeps this module useful before list_font_masters was
        # added to the public parser surface.  It is intentionally read-only.
        from .parsers import plist

        data = plist.load(path)
        raw = data.get("fontMaster", [])
        if isinstance(raw, dict):
            raw = [raw]
        if isinstance(raw, list):
            result = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                master_id = str(item.get("id", "") or "")
                name = str(item.get("name", "") or master_id)
                if name or master_id:
                    result.append(
                        {"id": master_id or name, "name": name or master_id}
                    )
            if result:
                return result
    return []


def _selected_master_id(
    masters: Sequence[Dict[str, str]],
    requested: Optional[str],
    parsed_name: str,
) -> str:
    """Resolve the exact master ID selected by the parser.

    Matching a parsed style name is insufficient because Glyphs sources may
    legitimately contain multiple masters with the same display name.  Keep
    the parser's source-order matching semantics when a request was supplied,
    then use the parsed name only for the initial/default selection.
    """
    if requested:
        for item in masters:
            if requested in (item["id"], item["name"]):
                return item["id"]
    if parsed_name:
        for item in masters:
            if item["name"] == parsed_name:
                return item["id"]
    return masters[0]["id"] if masters else ""


def _format_number(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    if number.is_integer():
        return int(number)
    return round(number, 3)


def _codepoint_character(codepoint: int) -> str:
    try:
        value = chr(codepoint)
    except (TypeError, ValueError):
        return ""
    if unicodedata.category(value).startswith("C"):
        return ""
    return value


def _unicode_label(codepoint: int) -> str:
    return "{:04X}".format(codepoint)


def _metadata_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()


def _unicode_kind(codepoint: Optional[int]) -> Tuple[str, str]:
    if codepoint is None:
        return "GLYPH", "UNCODED GLYPHS"
    character = _codepoint_character(codepoint)
    if not character:
        return "GLYPH", "CONTROL CHARACTERS"
    category = unicodedata.category(character)
    unicode_name = unicodedata.name(character, "")
    if category == "Lu":
        kind = "MAJUSCULE"
    elif category == "Ll":
        kind = "MINUSCULE"
    elif category.startswith("L"):
        kind = "LETTER"
    elif category == "Nd":
        kind = "FIGURE"
    elif category.startswith("N"):
        kind = "NUMBER"
    elif category.startswith("P"):
        kind = "PUNCTUATION"
    elif category == "Sm":
        kind = "MATHEMATICAL SYMBOL"
    elif category == "Sc":
        kind = "CURRENCY SYMBOL"
    elif category.startswith("S"):
        kind = "SYMBOL"
    elif category.startswith("M"):
        kind = "MARK"
    else:
        kind = "GLYPH"

    if "LATIN" in unicode_name and category.startswith("L"):
        if codepoint <= 0x007F:
            group = "STD LATIN ALPHABET"
        else:
            group = "EXT LATIN ALPHABET"
    elif category == "Nd":
        group = "FIGURES"
    elif 0x2190 <= codepoint <= 0x21FF:
        group = "ARROWS"
    elif category == "Sm":
        group = "MATHEMATICAL SYMBOLS"
    elif category == "Sc":
        group = "CURRENCY SYMBOLS"
    elif category.startswith(("P", "S")):
        group = "PUNCTUATION & SYMBOLS"
    elif category.startswith("M"):
        group = "DIACRITICS & MARKS"
    else:
        group = "OTHER GLYPHS"
    return kind, group


def _authored_kind(glyph: ir.Glyph, fallback: str) -> str:
    category = _metadata_text(getattr(glyph, "category", ""))
    subcategory = _metadata_text(
        getattr(glyph, "subcategory", getattr(glyph, "sub_category", ""))
    )
    lowered = "{} {}".format(category, subcategory).casefold()
    if "uppercase" in lowered:
        return "MAJUSCULE"
    if "lowercase" in lowered:
        return "MINUSCULE"
    if "decimal" in lowered or "digit" in lowered:
        return "FIGURE"
    if category:
        return category.upper()
    return fallback


def _glyph_metadata(glyph: ir.Glyph) -> Dict[str, Any]:
    unicodes = [int(value) for value in getattr(glyph, "unicodes", [])]
    primary = unicodes[0] if unicodes else None
    kind, group = _unicode_kind(primary)
    kind = _authored_kind(glyph, kind)
    script = _metadata_text(getattr(glyph, "script", ""))
    if script and group == "OTHER GLYPHS":
        group = "{} GLYPHS".format(script.upper())
    metrics = getattr(glyph, "metrics", None)
    return {
        "name": glyph.name,
        "character": _codepoint_character(primary) if primary is not None else "",
        "unicode": _unicode_label(primary) if primary is not None else "—",
        "unicodes": [_unicode_label(value) for value in unicodes],
        "category": kind,
        "group": group,
        "script": script,
        "width": _format_number(glyph.advance_width),
        "lsb": _format_number(getattr(metrics, "lsb", None)),
        "rsb": _format_number(getattr(metrics, "rsb", None)),
        "contours": len(glyph.contours),
        "nodes": sum(len(contour.nodes) for contour in glyph.contours),
    }


def _ordered_glyph_names(font: ir.Font) -> List[str]:
    codepoint_by_name: Dict[str, int] = {}
    for codepoint, name in font.cmap.items():
        codepoint_by_name.setdefault(name, int(codepoint))
    return sorted(
        font.glyphs,
        key=lambda name: (
            codepoint_by_name.get(name, 0x110000),
            name.casefold(),
            name,
        ),
    )


def _has_designed_geometry(glyph: ir.Glyph) -> bool:
    """Return whether the selected master layer contains a drawable outline."""
    return any(contour.nodes for contour in glyph.contours)


def _designed_pairs(font: ir.Font) -> List[Tuple[str, Optional[str]]]:
    # The player is an outline specimen, so intentionally blank glyphs such as
    # ``space`` are not frames. Every glyph with geometry on the selected
    # master layer appears exactly once; backup and named layers are never
    # requested from the parser.
    ordered = [
        name
        for name in _ordered_glyph_names(font)
        if _has_designed_geometry(font.glyphs[name])
    ]
    remaining = set(ordered)
    pairs: List[Tuple[str, Optional[str]]] = []

    # Start with authored upper/lower counterparts.  Case conversion is only
    # used to discover a counterpart already present in the source font.
    for name in ordered:
        if name not in remaining:
            continue
        glyph = font.glyphs[name]
        unicodes = getattr(glyph, "unicodes", [])
        if not unicodes:
            continue
        character = _codepoint_character(int(unicodes[0]))
        if not character or not character.isalpha():
            continue
        upper = character.upper()
        lower = character.lower()
        if len(upper) != 1 or len(lower) != 1 or upper == lower:
            continue
        upper_name = font.cmap.get(ord(upper))
        lower_name = font.cmap.get(ord(lower))
        if (
            upper_name in remaining
            and lower_name in remaining
            and upper_name != lower_name
        ):
            pairs.append((str(upper_name), str(lower_name)))
            remaining.remove(str(upper_name))
            remaining.remove(str(lower_name))

    leftovers = [name for name in ordered if name in remaining]
    for index in range(0, len(leftovers), 2):
        left = leftovers[index]
        right = leftovers[index + 1] if index + 1 < len(leftovers) else None
        pairs.append((left, right))
    return pairs


def _paired_sequence(font: ir.Font) -> List[Dict[str, Any]]:
    result = []
    for index, (left, right) in enumerate(_designed_pairs(font)):
        left_meta = _glyph_metadata(font.glyphs[left])
        right_meta = _glyph_metadata(font.glyphs[right]) if right else None
        left_label = left_meta["character"] or left
        right_label = (right_meta["character"] or right) if right_meta else "—"
        result.append(
            {
                "id": index,
                "left": left,
                "right": right,
                "label": "{} / {}".format(left_label, right_label),
            }
        )
    return result


def _metric_dict(metrics: ir.Metrics) -> Dict[str, Any]:
    return {
        "baseline": _format_number(getattr(metrics, "baseline", 0.0)),
        "x_height": _format_number(getattr(metrics, "x_height", None)),
        "cap_height": _format_number(getattr(metrics, "cap_height", None)),
        "ascender": _format_number(getattr(metrics, "ascender", None)),
        "descender": _format_number(getattr(metrics, "descender", None)),
    }


def catalog_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return source metadata and a deterministic two-panel play sequence."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    path = _font_path(_string(payload, "font_path"))
    master = _string(payload, "master") or None
    font = _load(path, master)
    masters = _source_masters(path)
    if not masters:
        name = _metadata_text(getattr(font, "master_name", "")) or "Regular"
        masters = [{"id": name, "name": name}]
    ordered_names = _ordered_glyph_names(font)
    glyphs = [_glyph_metadata(font.glyphs[name]) for name in ordered_names]
    sequence = _paired_sequence(font)
    designed_names = [
        name for name in ordered_names if _has_designed_geometry(font.glyphs[name])
    ]
    parsed_master_name = _metadata_text(getattr(font, "master_name", ""))
    return {
        "font_path": str(path),
        "family_name": _metadata_text(getattr(font, "family_name", ""))
        or path.stem,
        "master_name": parsed_master_name or masters[0]["name"],
        "selected_master_id": _selected_master_id(
            masters,
            master,
            parsed_master_name,
        ),
        "source_format": _metadata_text(getattr(font, "source_format", ""))
        or path.suffix.lower().lstrip("."),
        "units_per_em": _format_number(font.units_per_em),
        "metrics": _metric_dict(font.metrics),
        "masters": masters,
        "glyphs": glyphs,
        "designed_glyph_count": len(designed_names),
        "master_layer_only": True,
        "sequence": sequence,
    }


def _svg_number(value: float) -> str:
    if not math.isfinite(float(value)):
        raise ValueError("SVG values must be finite")
    if abs(float(value)) < 1e-10:
        return "0"
    return format(float(value), ".10g")


def _contour_path(contour: ir.Contour) -> str:
    if not contour.nodes:
        return ""
    first = contour.nodes[0]
    commands = ["M {} {}".format(_svg_number(first.x), _svg_number(first.y))]
    for start, end in contour.segments():
        if start.handle_out is not None or end.handle_in is not None:
            first_control = start.handle_out or start.point
            second_control = end.handle_in or end.point
            commands.append(
                "C {} {} {} {} {} {}".format(
                    _svg_number(first_control[0]),
                    _svg_number(first_control[1]),
                    _svg_number(second_control[0]),
                    _svg_number(second_control[1]),
                    _svg_number(end.x),
                    _svg_number(end.y),
                )
            )
        else:
            commands.append(
                "L {} {}".format(_svg_number(end.x), _svg_number(end.y))
            )
    if contour.closed:
        commands.append("Z")
    return " ".join(commands)


def _all_paths(glyph: ir.Glyph) -> str:
    return " ".join(
        path for path in (_contour_path(contour) for contour in glyph.contours) if path
    )


def _vertical_frame(font: ir.Font) -> Tuple[float, float]:
    upem = font.units_per_em if font.units_per_em > 0 else 1000.0
    metrics = font.metrics
    high_values = [
        value
        for value in (
            getattr(metrics, "ascender", None),
            getattr(metrics, "cap_height", None),
        )
        if value is not None
    ]
    low_values = [
        value
        for value in (getattr(metrics, "descender", None), 0.0)
        if value is not None
    ]
    high = max(high_values) if high_values else upem * 0.8
    low = min(low_values) if low_values else -upem * 0.2
    if high <= low:
        low, high = -upem * 0.2, upem * 0.8
    # Keep a true one-em comparison frame even when sparse source metrics span
    # less than an em.  Larger authored spans are preserved without clipping.
    if high - low < upem:
        high = low + upem
    return float(low), float(high)


def _bracket_path(y: float) -> str:
    x1 = 28.0
    x2 = _PANEL_WIDTH - 28.0
    arm = 26.0
    drop = 20.0
    return (
        "M {a} {y} H {b} V {c} "
        "M {d} {y} H {e} V {c}"
    ).format(
        a=_svg_number(x1),
        b=_svg_number(x1 + arm),
        c=_svg_number(y + drop),
        d=_svg_number(x2),
        e=_svg_number(x2 - arm),
        y=_svg_number(y),
    )


def _metric_brackets(
    font: ir.Font,
    baseline: float,
    scale: float,
    color: str,
) -> str:
    low, high = _vertical_frame(font)
    candidates: List[float] = [high]
    cap_height = getattr(font.metrics, "cap_height", None)
    if cap_height is not None and not math.isclose(cap_height, high):
        candidates.append(float(cap_height))
    candidates.append(float(getattr(font.metrics, "baseline", 0.0)))
    if not math.isclose(low, candidates[-1]):
        candidates.append(low)
    seen: List[float] = []
    paths = []
    for metric in candidates:
        y = baseline - metric * scale
        if not 4.0 <= y <= _PANEL_HEIGHT - 24.0:
            continue
        if any(math.isclose(y, old, abs_tol=1.0) for old in seen):
            continue
        seen.append(y)
        paths.append(_bracket_path(y))
    return "".join(
        '<path d="{}" fill="none" stroke="{}" stroke-width="0.75" '
        'stroke-dasharray="1 1"/>'.format(path, color)
        for path in paths
    )


def _handle_geometry(
    glyph: ir.Glyph,
    marker_radius: float,
    colors: Dict[str, str],
) -> str:
    lines: List[str] = []
    points: List[str] = []
    for contour_index, contour in enumerate(glyph.contours):
        for node_index, node in enumerate(contour.nodes):
            for side, handle in (
                ("in", node.handle_in),
                ("out", node.handle_out),
            ):
                if handle is None or handle == node.point:
                    continue
                lines.append(
                    '<line data-handle="{side}" x1="{x1}" y1="{y1}" '
                    'x2="{x2}" y2="{y2}"/>'.format(
                        side=side,
                        x1=_svg_number(node.x),
                        y1=_svg_number(node.y),
                        x2=_svg_number(handle[0]),
                        y2=_svg_number(handle[1]),
                    )
                )
                points.append(
                    '<circle data-contour-index="{ci}" data-node-index="{ni}" '
                    'data-handle="{side}" cx="{x}" cy="{y}" r="{r}"/>'.format(
                        ci=contour_index,
                        ni=node_index,
                        side=side,
                        x=_svg_number(handle[0]),
                        y=_svg_number(handle[1]),
                        r=_svg_number(marker_radius * 0.8),
                    )
                )
    return (
        '<g class="handle-lines" fill="none" stroke="{handles}" '
        'stroke-width="0.8" vector-effect="non-scaling-stroke">{}</g>'
        '<g class="handle-points" fill="{point_fill}" stroke="{point_stroke}" '
        'stroke-width="0.8" vector-effect="non-scaling-stroke">{}</g>'
    ).format(
        "".join(lines),
        "".join(points),
        handles=colors["handles"],
        point_fill=colors["point_fill"],
        point_stroke=colors["point_stroke"],
    )


def _node_geometry(
    glyph: ir.Glyph,
    marker_radius: float,
    colors: Dict[str, str],
) -> str:
    markers: List[str] = []
    for contour_index, contour in enumerate(glyph.contours):
        for node_index, node in enumerate(contour.nodes):
            common = (
                'data-contour-index="{}" data-node-index="{}" '
                'data-node-type="{}"'
            ).format(
                contour_index,
                node_index,
                "smooth" if node.smooth else "corner",
            )
            if node.smooth:
                markers.append(
                    '<circle {common} cx="{x}" cy="{y}" r="{r}"/>'.format(
                        common=common,
                        x=_svg_number(node.x),
                        y=_svg_number(node.y),
                        r=_svg_number(marker_radius),
                    )
                )
            else:
                markers.append(
                    '<rect {common} x="{x}" y="{y}" width="{size}" '
                    'height="{size}"/>'.format(
                        common=common,
                        x=_svg_number(node.x - marker_radius),
                        y=_svg_number(node.y - marker_radius),
                        size=_svg_number(marker_radius * 2.0),
                    )
                )
    return (
        '<g class="on-curve-nodes" fill="{point_fill}" stroke="{point_stroke}" '
        'stroke-width="0.9" vector-effect="non-scaling-stroke">{}</g>'
    ).format(
        "".join(markers),
        point_fill=colors["point_fill"],
        point_stroke=colors["point_stroke"],
    )


def _glyph_svg(
    font: ir.Font,
    glyph: ir.Glyph,
    point_size: float,
    mode: str,
    colors: Optional[Dict[str, str]] = None,
    *,
    show_handles: bool = True,
    show_nodes: bool = True,
) -> Tuple[str, str, float]:
    palette = dict(DEFAULT_SPECIMEN_COLORS)
    if colors:
        palette.update(colors)
    upem = font.units_per_em if font.units_per_em > 0 else 1000.0
    scale = point_size / upem
    low, high = _vertical_frame(font)
    vertical_span = (high - low) * scale
    frame_top = (_PANEL_HEIGHT - vertical_span) * 0.5
    baseline = frame_top + high * scale
    translate_x = (_PANEL_WIDTH - glyph.advance_width * scale) * 0.5
    placement = "translate({} {})".format(
        _svg_number(translate_x),
        _svg_number(baseline),
    )
    # Keep the units-to-pixels transform byte-for-byte identical for every
    # glyph. Horizontal centring belongs to the outer placement group.
    transform = "scale({} {})".format(
        _svg_number(scale),
        _svg_number(-scale),
    )
    path = _all_paths(glyph)
    escaped_name = html.escape(glyph.name, quote=True)
    bracket_markup = _metric_brackets(
        font,
        baseline,
        scale,
        palette["guides"],
    )
    if mode == "solid":
        geometry = (
            '<path class="solid-outline" d="{}" fill="{}" '
            'fill-rule="nonzero"/>'
        ).format(html.escape(path, quote=True), palette["fill"])
    elif mode == "outline":
        # Open skeleton layers need a visible path even when the interactive
        # Bézier overlay is disabled.  Keep this deliberately free of nodes
        # and handles so the toggle still has a clear, literal meaning.
        geometry = (
            '<path class="native-outline" d="{path}" fill="none" '
            'stroke="{stroke}" stroke-width="1.25" '
            'vector-effect="non-scaling-stroke"/>'
        ).format(
            path=html.escape(path, quote=True),
            stroke=palette["stroke"],
        )
    else:
        marker_radius = 3.0 / scale
        geometry = (
            '<path class="xray-fill" d="{path}" fill="{fill}" '
            'fill-opacity="0.08" fill-rule="nonzero"/>'
            '<path class="native-outline" d="{path}" fill="none" '
            'stroke="{stroke}" stroke-width="1.25" '
            'vector-effect="non-scaling-stroke"/>'
            '{handles}{nodes}'
        ).format(
            path=html.escape(path, quote=True),
            fill=palette["fill"],
            stroke=palette["stroke"],
            handles=(
                _handle_geometry(glyph, marker_radius, palette)
                if show_handles
                else ""
            ),
            nodes=(
                _node_geometry(glyph, marker_radius, palette)
                if show_nodes
                else ""
            ),
        )
    markup = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        'role="img" aria-label="{name}" data-mode="{mode}" '
        'data-glyph="{name}" data-font-scale="{scale}">'
        '<title>{name} — {mode}</title>{brackets}'
        '<g class="glyph-placement" transform="{placement}">'
        '<g class="font-unit-geometry" transform="{transform}">{geometry}</g>'
        '</g>'
        '</svg>'
    ).format(
        width=_svg_number(_PANEL_WIDTH),
        height=_svg_number(_PANEL_HEIGHT),
        name=escaped_name,
        mode=mode,
        scale=_svg_number(scale),
        brackets=bracket_markup,
        placement=placement,
        transform=transform,
        geometry=geometry,
    )
    return markup, transform, scale


def _requested_names(payload: Dict[str, Any]) -> List[str]:
    value = payload.get("glyphs", payload.get("glyph_names"))
    if value is None:
        single = payload.get("glyph_name")
        if single is not None:
            value = [single]
    if not isinstance(value, list) or not value:
        raise ValueError("glyphs must be a non-empty array of glyph names")
    if len(value) > 2:
        raise ValueError("glyphs may contain at most two names")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("each glyph name must be a non-empty string")
        result.append(item.strip())
    return result


def render_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Render one or two glyph panels in both solid and X-Ray modes."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    path = _font_path(_string(payload, "font_path"))
    master = _string(payload, "master") or None
    point_size = _number(
        payload, "point_size", 370.0, _POINT_SIZE_MIN, _POINT_SIZE_MAX
    )
    mode = _string(payload, "mode", "")
    if not mode:
        xray = payload.get("xray", payload.get("bezier", False))
        if not isinstance(xray, bool):
            raise ValueError("xray must be true or false")
        mode = "xray" if xray else "solid"
    if mode not in ("solid", "xray"):
        raise ValueError("mode must be 'solid' or 'xray'")
    colors = _specimen_colors(payload)

    font = _load(path, master)
    renders = []
    scales = []
    for name in _requested_names(payload):
        glyph = font.glyphs.get(name)
        if glyph is None:
            raise ValueError("glyph {!r} was not found".format(name))
        xray_glyph = (
            _compound_for_render(path, master, name)
            if mode == "xray"
            else glyph
        )
        solid, solid_transform, solid_scale = _glyph_svg(
            font, glyph, point_size, "solid", colors
        )
        xray, xray_transform, xray_scale = _glyph_svg(
            font, xray_glyph, point_size, "xray", colors
        )
        scales.append(solid_scale)
        renders.append(
            {
                "glyph": _glyph_metadata(glyph),
                "solid_svg": solid,
                "xray_svg": xray,
                "svg": xray if mode == "xray" else solid,
                "xray_compounded": mode == "xray",
                "solid_transform": solid_transform,
                "xray_transform": xray_transform,
            }
        )
    return {
        "font_path": str(path),
        "family_name": _metadata_text(getattr(font, "family_name", ""))
        or path.stem,
        "master_name": _metadata_text(getattr(font, "master_name", ""))
        or "Regular",
        "units_per_em": _format_number(font.units_per_em),
        "point_size": _format_number(point_size),
        "mode": mode,
        "compounded": mode == "xray",
        "colors": colors,
        "font_unit_scale": _format_number(scales[0]),
        "renders": renders,
        "svgs": [item["svg"] for item in renders],
        "svg": renders[0]["svg"],
    }


_SPECIMEN_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Type Design X-Ray — Specimen Player</title>
  <link rel="icon" href="data:,">
  <style>
    :root {
      color-scheme:dark; --ink:#f5f5f3; --muted:#8b8b88; --line:#353533;
      --specimen-bg:#000000; --specimen-text:#ffffff; --specimen-guides:#737373;
    }
    * { box-sizing:border-box; }
    html, body { min-height:100%; }
    body {
      margin:0; background:#111; color:var(--ink);
      font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace;
    }
    button, input, select { font:inherit; }
    .tool-switcher {
      position:sticky; top:0; z-index:6; display:grid;
      grid-template-columns:repeat(3,minmax(0,1fr)); gap:1px;
      padding:7px 14px; border-bottom:1px solid #292927; background:#080808;
    }
    .tool-tab {
      min-width:0; display:grid; gap:2px; padding:7px 10px;
      border:1px solid transparent; color:#b8b8b3; text-decoration:none;
    }
    .tool-tab:hover { border-color:#3d3d3a; background:#171715; color:#fff; }
    .tool-tab.active { border-color:#686864; background:#1e1e1b; color:#fff; }
    .tool-name { font-size:11px; font-weight:700; letter-spacing:.08em; }
    .tool-summary {
      overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
      color:#777; font-size:9px; letter-spacing:.02em;
    }
    .toolbar {
      position:sticky; top:61px; z-index:5; min-height:58px; padding:9px 14px;
      display:flex; align-items:center; flex-wrap:wrap; gap:8px;
      border-bottom:1px solid #292927; background:rgba(10,10,10,.97);
    }
    .file-control input { position:absolute; inline-size:1px; block-size:1px; clip:rect(0 0 0 0); }
    .control, .file-control span, button, select, input[type="number"] {
      min-height:36px; border:1px solid #3d3d3a; border-radius:2px;
      background:#111; color:var(--ink); padding:7px 10px;
    }
    .file-control span, button { display:inline-flex; align-items:center; cursor:pointer; }
    button:hover, .file-control span:hover { background:#20201e; }
    button:disabled { cursor:not-allowed; color:#666; background:#0c0c0c; }
    button:focus-visible, select:focus-visible, input:focus-visible { outline:1px solid #fff; outline-offset:2px; }
    .file-control input:focus-visible + span { outline:1px solid #fff; outline-offset:2px; }
    .labelled { display:flex; align-items:center; gap:7px; color:#aaa; font-size:11px; letter-spacing:.08em; }
    select { max-width:190px; }
    input[type="number"] { width:82px; }
    .toggle { display:inline-flex; align-items:center; gap:7px; min-height:36px; padding:0 8px; cursor:pointer; }
    .toggle input { accent-color:#f5f5f3; }
    .palette { position:relative; }
    .palette summary {
      min-height:36px; display:inline-flex; align-items:center; cursor:pointer;
      border:1px solid #3d3d3a; border-radius:2px; padding:7px 10px;
      list-style:none; background:#111;
    }
    .palette summary::-webkit-details-marker { display:none; }
    .palette-grid {
      position:absolute; z-index:10; top:43px; left:0; width:310px;
      display:grid; grid-template-columns:1fr 1fr; gap:10px; padding:14px;
      border:1px solid #484845; background:#111; box-shadow:0 14px 40px #000a;
    }
    .color-control {
      display:grid; grid-template-columns:34px 1fr; align-items:center; gap:8px;
      color:#b8b8b3; font-size:10px; letter-spacing:.08em;
    }
    .color-control input { width:34px; height:30px; padding:2px; border:1px solid #444; background:#111; }
    .palette-reset { grid-column:1 / -1; justify-content:center; }
    #status { margin-left:auto; max-width:300px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#8e8e8b; font-size:11px; letter-spacing:.06em; }
    #status.error { color:#ff8d85; }
    .viewport { min-height:calc(100vh - 119px); display:grid; place-items:center; padding:18px; overflow:auto; }
    .specimen {
      width:min(1080px,100%); aspect-ratio:1080 / 766; min-width:720px;
      display:grid; grid-template-columns:1fr 1fr; background:var(--specimen-bg); overflow:hidden;
    }
    .panel { min-width:0; display:grid; grid-template-rows:246fr 520fr; padding:0 18px; }
    .panel + .panel { padding-left:19px; }
    .metadata {
      display:flex; align-items:flex-start; margin:0; padding-top:18px; padding-bottom:10px;
      border-bottom:1px solid var(--specimen-guides); color:var(--specimen-text); white-space:pre;
      font-size:clamp(8px,1.31vw,14px); line-height:1.07; letter-spacing:.1em;
      overflow:hidden;
    }
    .glyph-stage { min-height:0; display:grid; place-items:stretch; overflow:hidden; }
    .glyph-stage svg { display:block; width:100%; height:100%; }
    .empty {
      grid-column:1 / -1; display:grid; place-items:center; min-height:0;
      color:#777; letter-spacing:.16em; text-align:center; padding:2rem;
    }
    @media (max-width:800px) {
      .tool-switcher { position:static; grid-template-columns:1fr; }
      .tool-summary { white-space:normal; }
      .toolbar { top:0; }
      .viewport { place-items:start center; padding:8px; }
      #status { flex-basis:100%; margin-left:0; }
    }
  </style>
</head>
<body>
  __TOOL_SWITCHER__
  <header class="toolbar" aria-label="Specimen controls">
    <label class="file-control"><input id="font-file" type="file" accept=".glyphs"><span>IMPORT .GLYPHS</span></label>
    <label class="labelled">STYLE <select id="master" disabled><option>—</option></select></label>
    <button id="previous" type="button" aria-label="Previous pair" disabled>←</button>
    <button id="play" type="button" aria-pressed="false" disabled>PLAY</button>
    <button id="next" type="button" aria-label="Next pair" disabled>→</button>
    <label class="labelled">PAIR <select id="pair" disabled><option>—</option></select></label>
    <label class="labelled">FROM <input id="frame-start" type="number" min="1" value="1" disabled></label>
    <label class="labelled">TO <input id="frame-end" type="number" min="1" value="1" disabled></label>
    <button id="use-current" type="button" disabled>USE CURRENT</button>
    <label class="labelled">SIZE <input id="point-size" type="number" min="48" max="520" step="1" value="370"></label>
    <label class="labelled">SPEED <input id="speed" type="number" min="0.08" max="1" step="0.05" value="0.2"></label>
    <label class="toggle"><input id="bezier" type="checkbox"> BEZIER</label>
    <details class="palette">
      <summary>COLORS</summary>
      <div class="palette-grid">
        <label class="color-control"><input type="color" value="#000000" data-color="background">BACKGROUND</label>
        <label class="color-control"><input type="color" value="#ffffff" data-color="fill">FILL</label>
        <label class="color-control"><input type="color" value="#ffffff" data-color="stroke">STROKE</label>
        <label class="color-control"><input type="color" value="#ffffff" data-color="text">TEXT</label>
        <label class="color-control"><input type="color" value="#737373" data-color="guides">GUIDES</label>
        <label class="color-control"><input type="color" value="#8e8e8e" data-color="handles">HANDLES</label>
        <label class="color-control"><input type="color" value="#000000" data-color="point_fill">POINT FILL</label>
        <label class="color-control"><input type="color" value="#ffffff" data-color="point_stroke">POINT STROKE</label>
        <button class="palette-reset" id="reset-colors" type="button">RESET COLORS</button>
      </div>
    </details>
    <button id="export-svg" type="button" disabled>FRAME SVG</button>
    <button id="export-png" type="button" disabled>FRAME PNG</button>
    <button id="export-gif" type="button" disabled>EXPORT GIF</button>
    <button id="export-mp4" type="button" disabled>EXPORT MP4</button>
    <span id="status" role="status">IMPORT A GLYPHS FILE TO BEGIN</span>
  </header>
  <main class="viewport">
    <section class="specimen">
      <article class="panel">
        <pre id="left-meta" class="metadata" aria-label="Left glyph metadata">TYPEFACE: —</pre>
        <div id="left-glyph" class="glyph-stage"></div>
      </article>
      <article class="panel">
        <pre id="right-meta" class="metadata" aria-label="Right glyph metadata">TYPEFACE: —</pre>
        <div id="right-glyph" class="glyph-stage"></div>
      </article>
    </section>
  </main>
  <script>
    (() => {
      "use strict";
      const $ = (id) => document.getElementById(id);
      const fileInput = $("font-file");
      const master = $("master");
      const pair = $("pair");
      const previous = $("previous");
      const next = $("next");
      const play = $("play");
      const pointSize = $("point-size");
      const speed = $("speed");
      const bezier = $("bezier");
      const frameStart = $("frame-start");
      const frameEnd = $("frame-end");
      const useCurrent = $("use-current");
      const specimen = document.querySelector(".specimen");
      const colorInputs = Array.from(document.querySelectorAll("[data-color]"));
      const resetColors = $("reset-colors");
      const exportSvg = $("export-svg");
      const exportPng = $("export-png");
      const exportGif = $("export-gif");
      const exportMp4 = $("export-mp4");
      const exportButtons = [exportSvg, exportPng, exportGif, exportMp4];
      const playbackButtons = [previous, play, next];
      const status = $("status");
      const DEFAULT_COLORS = {
        background:"#000000", fill:"#ffffff", stroke:"#ffffff",
        text:"#ffffff", guides:"#737373", handles:"#8e8e8e",
        point_fill:"#000000", point_stroke:"#ffffff"
      };
      let fontPath = "";
      let catalog = null;
      let timer = null;
      let renderGeneration = 0;

      function setStatus(message, error = false) {
        status.textContent = message;
        status.className = error ? "error" : "";
      }
      function currentColors() {
        return Object.fromEntries(
          colorInputs.map((input) => [input.dataset.color, input.value])
        );
      }
      function applyPaletteStyles() {
        const colors = currentColors();
        specimen.style.setProperty("--specimen-bg", colors.background);
        specimen.style.setProperty("--specimen-text", colors.text);
        specimen.style.setProperty("--specimen-guides", colors.guides);
      }
      async function jsonRequest(url, payload) {
        const response = await fetch(url, {
          method:"POST", headers:{"Content-Type":"application/json"},
          body:JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
        return data;
      }
      function populate(select, values, selected) {
        select.replaceChildren();
        values.forEach((value) => {
          const option = document.createElement("option");
          option.value = String(value.value);
          option.textContent = value.label;
          option.selected = String(value.value) === String(selected);
          select.append(option);
        });
        select.disabled = values.length === 0;
      }
      function metricText(font, glyph) {
        const pad = (value) => String(value == null ? "—" : value).padStart(3, "0");
        return [
          `TYPEFACE: ${font.family_name.toUpperCase()}`,
          "",
          `STYLE:    ${font.master_name.toUpperCase()}`,
          `SIZE:     ${pointSize.value} pt`,
          "",
          `GLYPH:    ${glyph.category}`,
          `GROUP:    ${glyph.group}`,
          "",
          `NAME:     ${glyph.name}`,
          `UNICODE:  ${glyph.unicode}`,
          "",
          `|↔|:      ${pad(glyph.width)} upm`,
          `|←|:      ${pad(glyph.lsb)} upm`,
          ` →|:      ${pad(glyph.rsb)} upm`
        ].join("\n");
      }
      async function loadCatalog(selectedMaster = "") {
        if (!fontPath) return;
        setStatus("READING GLYPHS SOURCE…");
        catalog = await jsonRequest("/api/specimen/catalog", {
          font_path:fontPath, master:selectedMaster
        });
        populate(master, catalog.masters.map((item) => ({
          value:item.id, label:item.name
        })), catalog.selected_master_id || catalog.masters[0]?.id);
        populate(pair, catalog.sequence.map((item) => ({
          value:item.id, label:item.label
        })), 0);
        if (!catalog.sequence.length) throw new Error("This source contains no renderable glyphs");
        frameStart.max = String(catalog.sequence.length);
        frameEnd.max = String(catalog.sequence.length);
        frameStart.value = "1";
        frameEnd.value = String(catalog.sequence.length);
        frameStart.disabled = false;
        frameEnd.disabled = false;
        useCurrent.disabled = false;
        playbackButtons.forEach((button) => { button.disabled = false; });
        exportButtons.forEach((button) => { button.disabled = false; });
        setStatus(`${catalog.family_name.toUpperCase()} · ${catalog.designed_glyph_count} DESIGNED GLYPHS · MASTER LAYER`);
        await renderPair();
      }
      async function renderPair() {
        if (!catalog || !catalog.sequence.length) return;
        const generation = ++renderGeneration;
        const selected = catalog.sequence[Number(pair.value) || 0];
        setStatus("RENDERING…");
        try {
          const data = await jsonRequest("/api/specimen/render", {
            font_path:fontPath,
            master:master.value,
            glyphs:[selected.left, selected.right].filter(Boolean),
            point_size:Number(pointSize.value),
            mode:bezier.checked ? "xray" : "solid",
            colors:currentColors()
          });
          if (generation !== renderGeneration) return;
          const sides = ["left", "right"];
          sides.forEach((side, index) => {
            const render = data.renders[index];
            $(`${side}-meta`).textContent = render ? metricText(data, render.glyph) : "";
            $(`${side}-glyph`).innerHTML = render ? render.svg : "";
          });
          setStatus(`${data.family_name.toUpperCase()} · ${selected.label}${data.compounded ? " · COMPOUNDED" : ""}`);
        } catch (error) {
          if (generation === renderGeneration) setStatus(error.message, true);
        }
      }
      function move(delta) {
        if (!catalog?.sequence.length) return;
        const length = catalog.sequence.length;
        pair.value = String(((Number(pair.value) || 0) + delta + length) % length);
        renderPair();
      }
      function stop() {
        if (timer !== null) window.clearInterval(timer);
        timer = null; play.textContent = "PLAY"; play.setAttribute("aria-pressed", "false");
      }
      function start() {
        stop();
        play.textContent = "PAUSE"; play.setAttribute("aria-pressed", "true");
        timer = window.setInterval(() => move(1), Math.max(.08, Number(speed.value) || .2) * 1000);
      }
      function downloadName(format) {
        const base = `${catalog.family_name}-${catalog.master_name}-specimen`
          .replace(/[^A-Za-z0-9_.-]+/g, "-")
          .replace(/^[.-]+|[.-]+$/g, "") || "specimen";
        if (format === "svg" || format === "png") {
          const frame = String((Number(pair.value) || 0) + 1).padStart(2, "0");
          return `${base}-frame-${frame}.${format}`;
        }
        const start = Number(frameStart.value);
        const end = Number(frameEnd.value);
        const full = start === 1 && end === catalog.sequence.length;
        const suffix = full ? "" : `-frames-${String(start).padStart(2, "0")}-${String(end).padStart(2, "0")}`;
        return `${base}${suffix}.${format}`;
      }
      async function exportMedia(format) {
        if (!catalog || !fontPath) return;
        const frameFormat = format === "svg" || format === "png";
        const start = Number(frameStart.value);
        const end = Number(frameEnd.value);
        const secondsPerFrame = Number(speed.value);
        if (!frameFormat && (!Number.isInteger(start) || !Number.isInteger(end) || start < 1 || end < start || end > catalog.sequence.length)) {
          setStatus(`FRAME RANGE MUST BE BETWEEN 1 AND ${catalog.sequence.length}`, true);
          return;
        }
        if (!frameFormat && (!Number.isFinite(secondsPerFrame) || secondsPerFrame < .08 || secondsPerFrame > 1)) {
          setStatus("SPEED MUST BE BETWEEN 0.08 AND 1 SECOND PER FRAME", true);
          return;
        }
        stop();
        exportButtons.forEach((button) => { button.disabled = true; });
        const selection = frameFormat
          ? `FRAME ${(Number(pair.value) || 0) + 1}`
          : `FRAMES ${start}–${end}`;
        setStatus(`EXPORTING ${selection} AS ${format.toUpperCase()}…`);
        try {
          const payload = {
            font_path:fontPath,
            master:master.value,
            format,
            output_name:downloadName(format),
            point_size:Number(pointSize.value),
            xray:bezier.checked,
            colors:currentColors()
          };
          if (frameFormat) {
            payload.frame = (Number(pair.value) || 0) + 1;
          } else {
            payload.fps = 1 / secondsPerFrame;
            payload.start_frame = start;
            payload.end_frame = end;
          }
          const response = await fetch("/api/specimen/export", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify(payload)
          });
          if (!response.ok) {
            const error = await response.json();
            throw new Error(error.error || `Export failed (${response.status})`);
          }
          const blob = await response.blob();
          const url = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = url;
          link.download = downloadName(format);
          document.body.append(link);
          link.click();
          link.remove();
          URL.revokeObjectURL(url);
          setStatus(`${format.toUpperCase()} EXPORTED · ${selection} · MASTER LAYER${bezier.checked ? " · COMPOUNDED" : ""}`);
        } catch (error) {
          setStatus(error.message, true);
        } finally {
          exportButtons.forEach((button) => { button.disabled = false; });
        }
      }
      fileInput.addEventListener("change", async () => {
        const file = fileInput.files?.[0];
        if (!file) return;
        stop();
        playbackButtons.forEach((button) => { button.disabled = true; });
        exportButtons.forEach((button) => { button.disabled = true; });
        setStatus("IMPORTING SOURCE…");
        try {
          const response = await fetch("/api/upload", {
            method:"POST", body:file,
            headers:{"Content-Type":"application/octet-stream", "X-Filename":encodeURIComponent(file.name)}
          });
          const uploaded = await response.json();
          if (!response.ok) throw new Error(uploaded.error || "Upload failed");
          fontPath = uploaded.font_path;
          await loadCatalog();
        } catch (error) { setStatus(error.message, true); }
        finally { fileInput.value = ""; }
      });
      master.addEventListener("change", () => loadCatalog(master.value).catch((error) => setStatus(error.message, true)));
      pair.addEventListener("change", renderPair);
      useCurrent.addEventListener("click", () => {
        const frame = String((Number(pair.value) || 0) + 1);
        frameStart.value = frame;
        frameEnd.value = frame;
      });
      previous.addEventListener("click", () => move(-1));
      next.addEventListener("click", () => move(1));
      play.addEventListener("click", () => timer === null ? start() : stop());
      pointSize.addEventListener("change", renderPair);
      bezier.addEventListener("change", renderPair);
      exportSvg.addEventListener("click", () => exportMedia("svg"));
      exportPng.addEventListener("click", () => exportMedia("png"));
      exportGif.addEventListener("click", () => exportMedia("gif"));
      exportMp4.addEventListener("click", () => exportMedia("mp4"));
      colorInputs.forEach((input) => {
        input.addEventListener("change", () => {
          applyPaletteStyles();
          renderPair();
        });
      });
      resetColors.addEventListener("click", () => {
        colorInputs.forEach((input) => {
          input.value = DEFAULT_COLORS[input.dataset.color];
        });
        applyPaletteStyles();
        renderPair();
      });
      speed.addEventListener("change", () => { if (timer !== null) start(); });
      document.addEventListener("keydown", (event) => {
        if (event.target.matches("input,select")) return;
        if (event.key === "ArrowLeft") move(-1);
        if (event.key === "ArrowRight") move(1);
        if (event.key === " ") { event.preventDefault(); timer === null ? start() : stop(); }
      });
      applyPaletteStyles();
    })();
  </script>
</body>
</html>
"""


def specimen_page() -> str:
    """Return the self-contained HTML/CSS/JS specimen player."""
    return _SPECIMEN_PAGE.replace(
        "__TOOL_SWITCHER__", tool_switcher("specimen")
    )


# A short alias is convenient for alternate hosts and keeps routing explicit.
page = specimen_page


__all__ = [
    "catalog_request",
    "clear_cache",
    "page",
    "render_request",
    "specimen_page",
]
