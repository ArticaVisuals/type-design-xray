"""Parser for Unified Font Object source bundles."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPointPen
from fontTools.ufoLib import UFOReader
from fontTools.ufoLib.errors import UFOLibError

from glyphblueprint import ir


@dataclass
class _SourcePoint:
    point: ir.Point
    segment_type: Optional[str]
    smooth: bool


@dataclass
class _RawGlyph:
    width: float
    unicodes: List[int]
    commands: List[Tuple[str, Tuple[Any, ...], Dict[str, Any]]]


@dataclass
class _LayerSources:
    selected_name: str
    selected: Any
    default_name: str
    default: Any


class _GlyphObject:
    pass


class _FontInfo:
    pass


def parse_ufo(
    path: Union[str, os.PathLike],
    layer: Optional[str] = None,
    master: Optional[str] = None,
) -> ir.Font:
    """Read a UFO layer into cubic, component-free outline geometry."""
    del master
    with _open_reader(path) as reader:
        layer_names = reader.getLayerNames()
        default_name = reader.getDefaultLayerName()
        selected_name = _select_layer(layer_names, default_name, layer)
        default_set = reader.getGlyphSet(default_name)
        selected_set = (
            default_set
            if selected_name == default_name
            else reader.getGlyphSet(selected_name)
        )
        sources = _LayerSources(
            selected_name=selected_name,
            selected=selected_set,
            default_name=default_name,
            default=default_set,
        )

        info = _FontInfo()
        reader.readInfo(info)
        units_per_em = _number(getattr(info, "unitsPerEm", None), 1000.0)
        font_metrics = _font_metrics(info)
        font = ir.Font(
            units_per_em=units_per_em,
            metrics=font_metrics,
            family_name=str(getattr(info, "familyName", "") or ""),
            master_name=default_name,
            source_format="ufo",
            node_types_exact=True,
        )

        cache: Dict[Tuple[str, str], _RawGlyph] = {}
        for glyph_name in _glyph_names(default_set, selected_set):
            source = _glyph_source(sources, glyph_name)
            if source is None:
                continue
            source_name, glyph_set = source
            raw = _read_raw_glyph(
                source_name, glyph_set, glyph_name, cache
            )
            contours = _glyph_contours(
                glyph_name, sources, cache, ()
            )
            glyph_metrics = _glyph_metrics(
                font_metrics, raw.width, contours
            )
            glyph = ir.Glyph(
                name=glyph_name,
                advance_width=raw.width,
                units_per_em=units_per_em,
                contours=contours,
                metrics=glyph_metrics,
                unicodes=raw.unicodes,
                layer_name=(
                    "" if source_name == default_name else source_name
                ),
                node_types_exact=True,
            )
            font.glyphs[glyph_name] = glyph
            for codepoint in raw.unicodes:
                font.cmap[codepoint] = glyph_name

        font.kerning = {
            (str(left), str(right)): float(value)
            for (left, right), value in reader.readKerning().items()
        }
        _read_groups(reader.readGroups(), font)
        return font


def list_layers(
    path: Union[str, os.PathLike], glyph_name: str
) -> List[ir.LayerInfo]:
    """Describe every UFO layer that explicitly contains a glyph."""
    with _open_reader(path) as reader:
        layer_names = reader.getLayerNames()
        default_name = reader.getDefaultLayerName()
        default_set = reader.getGlyphSet(default_name)
        cache: Dict[Tuple[str, str], _RawGlyph] = {}
        result: List[ir.LayerInfo] = []

        for layer_name in layer_names:
            glyph_set = (
                default_set
                if layer_name == default_name
                else reader.getGlyphSet(layer_name)
            )
            if glyph_name not in glyph_set:
                continue
            sources = _LayerSources(
                selected_name=layer_name,
                selected=glyph_set,
                default_name=default_name,
                default=default_set,
            )
            contours = _glyph_contours(
                glyph_name, sources, cache, ()
            )
            result.append(
                ir.LayerInfo(
                    layer_id=layer_name,
                    name=layer_name,
                    is_master=layer_name == default_name,
                    contour_count=len(contours),
                    has_open_contours=any(
                        not contour.closed for contour in contours
                    ),
                )
            )

        if not result:
            raise ValueError(
                "glyph {!r} was not found in any UFO layer".format(
                    glyph_name
                )
            )
        return result


@contextmanager
def _open_reader(
    path: Union[str, os.PathLike]
) -> Iterator[UFOReader]:
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(
            "UFO source {!s} does not exist".format(source)
        )
    if source.is_dir():
        if not (source / "metainfo.plist").is_file():
            raise ValueError(
                "{!s} is not a UFO directory: metainfo.plist is "
                "missing".format(source)
            )
    elif source.suffix.lower() != ".ufoz":
        raise ValueError(
            "{!s} is not a UFO directory or .ufoz archive".format(source)
        )

    try:
        with UFOReader(str(source)) as reader:
            yield reader
    except (OSError, UFOLibError) as error:
        raise ValueError(
            "could not read UFO source {!s}: {}".format(source, error)
        ) from error


def _select_layer(
    layer_names: Sequence[str],
    default_name: str,
    requested: Optional[str],
) -> str:
    if requested in (None, ""):
        return default_name
    requested_text = str(requested)
    if requested_text in layer_names:
        return requested_text
    folded = requested_text.casefold()
    for layer_name in layer_names:
        if layer_name.casefold() == folded:
            return layer_name
    raise ValueError(
        "UFO layer {!r} was not found; available layers: {}".format(
            requested_text, ", ".join(layer_names)
        )
    )


def _glyph_names(default_set: Any, selected_set: Any) -> List[str]:
    names = list(default_set.keys())
    names.extend(name for name in selected_set.keys() if name not in default_set)
    return names


def _glyph_source(
    sources: _LayerSources, glyph_name: str
) -> Optional[Tuple[str, Any]]:
    if glyph_name in sources.selected:
        return (sources.selected_name, sources.selected)
    if glyph_name in sources.default:
        return (sources.default_name, sources.default)
    return None


def _read_raw_glyph(
    layer_name: str,
    glyph_set: Any,
    glyph_name: str,
    cache: Dict[Tuple[str, str], _RawGlyph],
) -> _RawGlyph:
    key = (layer_name, glyph_name)
    cached = cache.get(key)
    if cached is not None:
        return cached

    glyph_object = _GlyphObject()
    pen = RecordingPointPen()
    glyph_set.readGlyph(glyph_name, glyph_object, pen)
    raw = _RawGlyph(
        width=_number(getattr(glyph_object, "width", None), 0.0),
        unicodes=[
            int(value)
            for value in getattr(glyph_object, "unicodes", ())
            if 0 <= int(value) <= 0x10FFFF
        ],
        commands=list(pen.value),
    )
    cache[key] = raw
    return raw


def _glyph_contours(
    glyph_name: str,
    sources: _LayerSources,
    cache: Dict[Tuple[str, str], _RawGlyph],
    stack: Tuple[str, ...],
) -> List[ir.Contour]:
    if glyph_name in stack:
        return []
    source = _glyph_source(sources, glyph_name)
    if source is None:
        return []
    layer_name, glyph_set = source
    raw = _read_raw_glyph(layer_name, glyph_set, glyph_name, cache)
    next_stack = stack + (glyph_name,)
    contours: List[ir.Contour] = []
    points: Optional[List[_SourcePoint]] = None

    for operation, operands, _kwargs in raw.commands:
        if operation == "beginPath":
            if points is not None:
                raise ValueError(
                    "nested contours in UFO glyph {!r}".format(glyph_name)
                )
            points = []
        elif operation == "addPoint":
            if points is None:
                raise ValueError(
                    "point outside a contour in UFO glyph {!r}".format(
                        glyph_name
                    )
                )
            point, segment_type, smooth, _name = operands
            points.append(
                _SourcePoint(
                    point=_point(point),
                    segment_type=(
                        str(segment_type).lower()
                        if segment_type is not None
                        else None
                    ),
                    smooth=bool(smooth),
                )
            )
        elif operation == "endPath":
            if points is None:
                raise ValueError(
                    "endPath without beginPath in UFO glyph {!r}".format(
                        glyph_name
                    )
                )
            contours.append(_assemble_contour(points))
            points = None
        elif operation == "addComponent":
            if points is not None:
                raise ValueError(
                    "component inside a contour in UFO glyph {!r}".format(
                        glyph_name
                    )
                )
            base_name, transform = operands
            component_contours = _glyph_contours(
                str(base_name), sources, cache, next_stack
            )
            contours.extend(
                _transform_contour(contour, transform)
                for contour in component_contours
            )
        elif operation == "addVarComponent":
            raise ValueError(
                "variable components are not supported in UFO outlines"
            )
        else:
            raise ValueError(
                "unsupported point-pen operation {!r} in UFO glyph "
                "{!r}".format(operation, glyph_name)
            )

    if points is not None:
        raise ValueError(
            "unterminated contour in UFO glyph {!r}".format(glyph_name)
        )
    return contours


def _assemble_contour(points: Sequence[_SourcePoint]) -> ir.Contour:
    if not points:
        return ir.Contour(nodes=[], closed=True)

    move_indices = [
        index
        for index, point in enumerate(points)
        if point.segment_type == "move"
    ]
    if move_indices and move_indices != [0]:
        raise ValueError(
            "an open UFO contour must begin with its only move point"
        )
    closed = not move_indices

    if closed and all(point.segment_type is None for point in points):
        return _all_off_curve_contour(points)

    leading: List[ir.Point] = []
    pending: List[ir.Point] = []
    nodes: List[ir.Node] = []
    first_segment_type = ""

    for source in points:
        if source.segment_type is None:
            if nodes:
                pending.append(source.point)
            else:
                leading.append(source.point)
            continue

        if source.segment_type not in ("move", "line", "curve", "qcurve"):
            raise ValueError(
                "unsupported UFO segment type {!r}".format(
                    source.segment_type
                )
            )
        node = ir.Node(point=source.point, smooth=source.smooth)
        if not nodes:
            if source.segment_type == "move" and leading:
                raise ValueError(
                    "an open UFO contour cannot begin with off-curve points"
                )
            nodes.append(node)
            first_segment_type = source.segment_type
            continue

        _append_segment(nodes, node, source.segment_type, pending, True)
        pending = []

    if not nodes:
        return ir.Contour(nodes=[], closed=closed)
    if closed:
        _append_segment(
            nodes,
            nodes[0],
            first_segment_type,
            pending + leading,
            False,
        )
    elif pending:
        raise ValueError(
            "an open UFO contour cannot end with off-curve points"
        )
    return ir.Contour(nodes=nodes, closed=closed)


def _append_segment(
    nodes: List[ir.Node],
    end: ir.Node,
    segment_type: str,
    controls: Sequence[ir.Point],
    append_end: bool,
) -> None:
    start = nodes[-1]
    if segment_type in ("move", "line"):
        if segment_type == "move" or controls:
            raise ValueError(
                "invalid off-curve points before UFO {!r} segment".format(
                    segment_type
                )
            )
        end.type = ir.SEGMENT_LINE
    elif segment_type == "curve":
        if len(controls) != 2:
            raise ValueError(
                "a cubic UFO segment requires exactly two off-curve points"
            )
        start.handle_out = controls[0]
        end.handle_in = controls[1]
        end.type = ir.SEGMENT_CURVE
    elif segment_type == "qcurve":
        if not controls:
            raise ValueError(
                "a quadratic UFO segment requires an off-curve point"
            )
        for index, control in enumerate(controls):
            if index + 1 < len(controls):
                next_control = controls[index + 1]
                piece_end = ir.Node(
                    point=_midpoint(control, next_control),
                    type=ir.SEGMENT_CURVE,
                    smooth=True,
                )
                _assign_quadratic(nodes[-1], piece_end, control)
                nodes.append(piece_end)
            else:
                _assign_quadratic(nodes[-1], end, control)
    else:
        raise ValueError(
            "unsupported UFO segment type {!r}".format(segment_type)
        )

    if append_end:
        nodes.append(end)


def _assign_quadratic(
    start: ir.Node, end: ir.Node, control: ir.Point
) -> None:
    start.handle_out, end.handle_in = ir.quadratic_to_cubic(
        start.point, control, end.point
    )
    end.type = ir.SEGMENT_CURVE


def _all_off_curve_contour(
    points: Sequence[_SourcePoint],
) -> ir.Contour:
    controls = [source.point for source in points]
    start = ir.Node(
        point=_midpoint(controls[-1], controls[0]),
        type=ir.SEGMENT_CURVE,
        smooth=True,
    )
    nodes = [start]
    for index, control in enumerate(controls):
        if index + 1 < len(controls):
            end = ir.Node(
                point=_midpoint(control, controls[index + 1]),
                type=ir.SEGMENT_CURVE,
                smooth=True,
            )
            _assign_quadratic(nodes[-1], end, control)
            nodes.append(end)
        else:
            _assign_quadratic(nodes[-1], start, control)
    return ir.Contour(nodes=nodes, closed=True)


def _midpoint(first: ir.Point, second: ir.Point) -> ir.Point:
    return (
        0.5 * (first[0] + second[0]),
        0.5 * (first[1] + second[1]),
    )


def _transform_contour(
    contour: ir.Contour, transform: Sequence[float]
) -> ir.Contour:
    return ir.Contour(
        closed=contour.closed,
        nodes=[
            ir.Node(
                point=_transform_point(node.point, transform),
                type=node.type,
                smooth=node.smooth,
                handle_in=(
                    _transform_point(node.handle_in, transform)
                    if node.handle_in is not None
                    else None
                ),
                handle_out=(
                    _transform_point(node.handle_out, transform)
                    if node.handle_out is not None
                    else None
                ),
            )
            for node in contour.nodes
        ],
    )


def _transform_point(
    point: ir.Point, transform: Sequence[float]
) -> ir.Point:
    a, b, c, d, translate_x, translate_y = transform
    return (
        float(a) * point[0] + float(c) * point[1] + float(translate_x),
        float(b) * point[0] + float(d) * point[1] + float(translate_y),
    )


def _font_metrics(info: _FontInfo) -> ir.Metrics:
    return ir.Metrics(
        baseline=0.0,
        x_height=_optional_number(getattr(info, "xHeight", None)),
        cap_height=_optional_number(getattr(info, "capHeight", None)),
        ascender=_optional_number(getattr(info, "ascender", None)),
        descender=_optional_number(getattr(info, "descender", None)),
    )


def _glyph_metrics(
    font_metrics: ir.Metrics,
    advance: float,
    contours: Sequence[ir.Contour],
) -> ir.Metrics:
    extent = _x_extent(contours)
    return ir.Metrics(
        baseline=font_metrics.baseline,
        x_height=font_metrics.x_height,
        cap_height=font_metrics.cap_height,
        ascender=font_metrics.ascender,
        descender=font_metrics.descender,
        lsb=extent[0] if extent is not None else None,
        rsb=advance - extent[1] if extent is not None else None,
    )


def _x_extent(
    contours: Sequence[ir.Contour],
) -> Optional[Tuple[float, float]]:
    points = [
        node.point
        for contour in contours
        for node in contour.nodes
    ]
    if not points:
        return None

    pen = BoundsPen(None)
    for contour in contours:
        if not contour.nodes:
            continue
        pen.moveTo(contour.nodes[0].point)
        for start, end in contour.segments():
            if start.handle_out is not None or end.handle_in is not None:
                pen.curveTo(
                    start.handle_out or start.point,
                    end.handle_in or end.point,
                    end.point,
                )
            else:
                pen.lineTo(end.point)
        if contour.closed:
            pen.closePath()
        else:
            pen.endPath()

    if pen.bounds is not None:
        return (float(pen.bounds[0]), float(pen.bounds[2]))
    return (
        min(point[0] for point in points),
        max(point[0] for point in points),
    )


def _read_groups(groups: Dict[str, List[str]], font: ir.Font) -> None:
    for group_name, members in groups.items():
        if group_name.startswith("public.kern1."):
            target = font.kern_group_left
        elif group_name.startswith("public.kern2."):
            target = font.kern_group_right
        else:
            continue
        for glyph_name in members:
            target[str(glyph_name)] = str(group_name)


def _point(value: Sequence[float]) -> ir.Point:
    return (float(value[0]), float(value[1]))


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _optional_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["parse_ufo", "list_layers"]
