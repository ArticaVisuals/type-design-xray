"""Render positioned glyph outlines and blueprint overlays as editable SVG."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from .. import ir
from .. import style as style_contract


@dataclass(frozen=True)
class _Frame:
    xmin: float
    ymin: float
    xmax: float
    ymax: float
    scale: float
    width: float
    height: float
    padding: float

    def screen_point(self, point: ir.Point) -> ir.Point:
        return (
            self.padding + (point[0] - self.xmin) * self.scale,
            self.padding + (self.ymax - point[1]) * self.scale,
        )

    def transform(self) -> str:
        tx = self.padding - self.xmin * self.scale
        ty = self.padding + self.ymax * self.scale
        return "translate({} {}) scale({} {})".format(
            _number(tx), _number(ty), _number(self.scale), _number(-self.scale)
        )


def _number(value: float) -> str:
    if not math.isfinite(float(value)):
        raise ValueError("SVG values must be finite")
    if abs(float(value)) < 1e-12:
        return "0"
    return format(float(value), ".12g")


def _points(points: Iterable[ir.Point]) -> str:
    return " ".join("{},{}".format(_number(x), _number(y)) for x, y in points)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return cleaned or "unnamed"


def _glyph_attributes(index: int, glyph: ir.Glyph) -> dict:
    return {
        "id": "glyph-{}-{}".format(index, _slug(glyph.name)),
        "data-glyph": glyph.name,
        "data-glyph-index": str(index),
    }


def _metric_items(layout: ir.Layout) -> List[Tuple[str, str, float]]:
    values = {
        "baseline": ("baseline", layout.metrics.baseline),
        "xheight": ("x-height", layout.metrics.x_height),
        "capheight": ("cap height", layout.metrics.cap_height),
        "ascender": ("ascender", layout.metrics.ascender),
        "descender": ("descender", layout.metrics.descender),
    }
    items = []
    for name in style_contract.METRIC_NAMES:
        if name == "sidebearings":
            continue
        label, value = values[name]
        if value is not None:
            items.append((name, label, value))
    return items


def _shown_metric_items(
    layout: ir.Layout, resolved_style: style_contract.Style
) -> List[Tuple[str, str, float]]:
    shown = set(resolved_style.metrics.show)
    return [item for item in _metric_items(layout) if item[0] in shown]


def _fallback_vertical_bounds(layout: ir.Layout) -> Tuple[float, float]:
    upem = layout.units_per_em if layout.units_per_em > 0 else 1000.0
    descender = layout.metrics.descender
    ascender = layout.metrics.ascender
    low = descender if descender is not None else -0.25 * upem
    high = ascender if ascender is not None else 0.75 * upem
    if high <= low:
        high = low + upem
    return low, high


def _nondegenerate_bounds(
    bounds: Tuple[float, float, float, float], layout: ir.Layout
) -> Tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = bounds
    upem = layout.units_per_em if layout.units_per_em > 0 else 1000.0
    if xmax <= xmin:
        half = max(upem * 0.5, 0.5)
        xmin -= half
        xmax += half
    if ymax <= ymin:
        low, high = _fallback_vertical_bounds(layout)
        if high > low:
            ymin, ymax = low, high
        else:
            ymin -= max(upem * 0.5, 0.5)
            ymax += max(upem * 0.5, 0.5)
    return xmin, ymin, xmax, ymax


def _auto_base_bounds(
    layout: ir.Layout, resolved_style: style_contract.Style
) -> Tuple[Tuple[float, float, float, float], bool]:
    points: List[ir.Point] = []
    geometry = layout.bounds()
    if geometry is not None:
        points.extend(
            [
                (geometry[0], geometry[1]),
                (geometry[2], geometry[3]),
            ]
        )

    metrics_enabled = (
        resolved_style.layers.enabled("metrics") and resolved_style.metrics.visible
    )
    horizontal_metrics = []
    if metrics_enabled and resolved_style.metrics.line.visible:
        horizontal_metrics = _shown_metric_items(layout, resolved_style)
        for _, _, value in horizontal_metrics:
            points.extend([(0.0, value), (layout.total_advance, value)])

    sidebearings = (
        metrics_enabled
        and "sidebearings" in resolved_style.metrics.show
        and resolved_style.metrics.sidebearing_line.visible
    )
    if sidebearings:
        for positioned in layout.glyphs:
            left = positioned.origin_x
            right = positioned.origin_x + positioned.glyph.advance_width
            points.extend([(left, 0.0), (right, 0.0)])

    if not points:
        low, high = _fallback_vertical_bounds(layout)
        advance = layout.total_advance
        if advance <= 0:
            advance = layout.units_per_em if layout.units_per_em > 0 else 1000.0
        return (0.0, low, advance, high), bool(horizontal_metrics)

    xmin = min(point[0] for point in points)
    xmax = max(point[0] for point in points)
    ymin = min(point[1] for point in points)
    ymax = max(point[1] for point in points)
    if sidebearings and geometry is None and not horizontal_metrics:
        ymin, ymax = _fallback_vertical_bounds(layout)
    return _nondegenerate_bounds((xmin, ymin, xmax, ymax), layout), bool(
        horizontal_metrics
    )


def _auto_scale(
    xmin: float,
    xmax: float,
    advance: float,
    extend: float,
    available_width: float,
) -> float:
    if extend <= 0:
        return available_width / (xmax - xmin)
    if available_width <= 2.0 * extend:
        raise ValueError("canvas width leaves no room for extended metric guides")

    def drawn_width(scale: float) -> float:
        left = min(scale * xmin, -extend)
        right = max(scale * xmax, scale * advance + extend)
        return right - left

    low = 0.0
    high = available_width / (xmax - xmin)
    while drawn_width(high) < available_width:
        high *= 2.0
    for _ in range(80):
        middle = (low + high) * 0.5
        if drawn_width(middle) < available_width:
            low = middle
        else:
            high = middle
    return (low + high) * 0.5


def _resolve_frame(
    layout: ir.Layout, resolved_style: style_contract.Style
) -> _Frame:
    width = float(resolved_style.canvas.width)
    padding = float(resolved_style.canvas.padding)
    if width <= 0:
        raise ValueError("canvas width must be positive")
    if padding < 0:
        raise ValueError("canvas padding may not be negative")
    available_width = width - 2.0 * padding
    if available_width <= 0:
        raise ValueError("canvas padding leaves no room for glyph geometry")

    mode = resolved_style.canvas.frame
    horizontal_extended = False
    if mode == "auto":
        bounds, horizontal_extended = _auto_base_bounds(layout, resolved_style)
    elif mode in ("em", "metrics"):
        ymin, ymax = _fallback_vertical_bounds(layout)
        xmax = layout.total_advance
        if xmax <= 0:
            xmax = layout.units_per_em if layout.units_per_em > 0 else 1000.0
        bounds = (0.0, ymin, xmax, ymax)
    else:
        raise ValueError("unknown canvas frame {!r}".format(mode))

    xmin, ymin, xmax, ymax = _nondegenerate_bounds(bounds, layout)
    extend = 0.0
    if horizontal_extended:
        extend = max(float(resolved_style.metrics.extend), 0.0)
        scale = _auto_scale(
            xmin, xmax, layout.total_advance, extend, available_width
        )
        xmin = min(xmin, -extend / scale)
        xmax = max(xmax, layout.total_advance + extend / scale)
    else:
        scale = available_width / (xmax - xmin)

    height = (ymax - ymin) * scale + 2.0 * padding
    return _Frame(
        xmin=xmin,
        ymin=ymin,
        xmax=xmax,
        ymax=ymax,
        scale=scale,
        width=width,
        height=height,
        padding=padding,
    )


def _contour_path(contour: ir.Contour) -> str:
    if not contour.nodes:
        return ""
    first = contour.nodes[0]
    commands = ["M {} {}".format(_number(first.x), _number(first.y))]
    for start, end in contour.segments():
        if start.handle_out is not None or end.handle_in is not None:
            first_control = start.handle_out or start.point
            second_control = end.handle_in or end.point
            commands.append(
                "C {} {} {} {} {} {}".format(
                    _number(first_control[0]),
                    _number(first_control[1]),
                    _number(second_control[0]),
                    _number(second_control[1]),
                    _number(end.x),
                    _number(end.y),
                )
            )
        else:
            commands.append("L {} {}".format(_number(end.x), _number(end.y)))
    if contour.closed:
        commands.append("Z")
    return " ".join(commands)


def _scaled_dasharray(
    line_style: style_contract.LineStyle, scale: float
) -> Optional[str]:
    dasharray = line_style.dasharray()
    if dasharray is None:
        return None
    return ",".join(_number(float(value) / scale) for value in dasharray.split(","))


def _line_attributes(
    line_style: style_contract.LineStyle, scale: float
) -> dict:
    attributes = {
        "fill": "none",
        "stroke": line_style.color,
        "stroke-width": _number(line_style.width / scale),
        "stroke-opacity": _number(line_style.opacity),
        "stroke-linecap": line_style.linecap,
        "stroke-linejoin": line_style.linejoin,
    }
    dasharray = _scaled_dasharray(line_style, scale)
    if dasharray is not None:
        attributes["stroke-dasharray"] = dasharray
    return attributes


def _marker(
    parent: ET.Element,
    point: ir.Point,
    marker_style: style_contract.MarkerStyle,
    scale: float,
) -> Optional[ET.Element]:
    if not marker_style.visible or marker_style.shape == "none":
        return None
    if marker_style.shape not in style_contract.SHAPES:
        raise ValueError("unknown marker shape {!r}".format(marker_style.shape))

    radius = marker_style.size / scale
    x, y = point
    attributes = {
        "data-shape": marker_style.shape,
        "fill": marker_style.effective_fill(),
        "stroke": marker_style.effective_stroke(),
        "stroke-width": _number(marker_style.stroke_width / scale),
        "opacity": _number(marker_style.opacity),
    }
    if marker_style.shape == "circle":
        attributes.update(
            {"cx": _number(x), "cy": _number(y), "r": _number(radius)}
        )
        return ET.SubElement(parent, "circle", attributes)
    if marker_style.shape == "square":
        attributes.update(
            {
                "x": _number(x - radius),
                "y": _number(y - radius),
                "width": _number(radius * 2.0),
                "height": _number(radius * 2.0),
            }
        )
        return ET.SubElement(parent, "rect", attributes)
    if marker_style.shape == "diamond":
        attributes["points"] = _points(
            [
                (x, y + radius),
                (x + radius, y),
                (x, y - radius),
                (x - radius, y),
            ]
        )
        return ET.SubElement(parent, "polygon", attributes)
    if marker_style.shape == "triangle":
        attributes["points"] = _points(
            [
                (x, y + radius),
                (x - math.sqrt(3.0) * radius / 2.0, y - radius / 2.0),
                (x + math.sqrt(3.0) * radius / 2.0, y - radius / 2.0),
            ]
        )
        return ET.SubElement(parent, "polygon", attributes)

    attributes["d"] = "M {} {} L {} {} M {} {} L {} {}".format(
        _number(x - radius),
        _number(y),
        _number(x + radius),
        _number(y),
        _number(x),
        _number(y - radius),
        _number(x),
        _number(y + radius),
    )
    return ET.SubElement(parent, "path", attributes)


def _geometry_group(layer: ET.Element, frame: _Frame) -> ET.Element:
    return ET.SubElement(
        layer,
        "g",
        {
            "class": "font-unit-geometry",
            "transform": frame.transform(),
        },
    )


def _positioned_glyph_group(
    parent: ET.Element, index: int, positioned: ir.PositionedGlyph
) -> ET.Element:
    attributes = _glyph_attributes(index, positioned.glyph)
    if positioned.origin_x != 0 or positioned.origin_y != 0:
        attributes["transform"] = "translate({} {})".format(
            _number(positioned.origin_x), _number(positioned.origin_y)
        )
    return ET.SubElement(parent, "g", attributes)


def _render_background(
    layer: ET.Element, frame: _Frame, resolved_style: style_contract.Style
) -> None:
    if resolved_style.canvas.background is None:
        return
    ET.SubElement(
        layer,
        "rect",
        {
            "x": "0",
            "y": "0",
            "width": _number(frame.width),
            "height": _number(frame.height),
            "fill": resolved_style.canvas.background,
        },
    )


def _metric_label(
    parent: ET.Element,
    x: float,
    y: float,
    value: str,
    metric_name: str,
    resolved_style: style_contract.Style,
) -> None:
    text = ET.SubElement(
        parent,
        "text",
        {
            "x": _number(x),
            "y": _number(y),
            "fill": resolved_style.metrics.label_color,
            "font-size": _number(resolved_style.metrics.label_size),
            "font-family": resolved_style.metrics.label_family,
            "data-metric": metric_name,
        },
    )
    text.text = value


def _render_metrics(
    layer: ET.Element,
    layout: ir.Layout,
    resolved_style: style_contract.Style,
    frame: _Frame,
) -> None:
    metrics_style = resolved_style.metrics
    if not metrics_style.visible:
        return

    shown_items = _shown_metric_items(layout, resolved_style)
    geometry = _geometry_group(layer, frame)
    labels = None
    if metrics_style.labels:
        labels = ET.SubElement(layer, "g", {"class": "metric-labels"})

    if metrics_style.line.visible:
        extend = max(float(metrics_style.extend), 0.0) / frame.scale
        x1 = -extend
        x2 = layout.total_advance + extend
        for name, label, value in shown_items:
            attributes = _line_attributes(metrics_style.line, frame.scale)
            attributes.update(
                {
                    "x1": _number(x1),
                    "y1": _number(value),
                    "x2": _number(x2),
                    "y2": _number(value),
                    "data-metric": name,
                }
            )
            ET.SubElement(geometry, "line", attributes)
            if labels is not None:
                screen_x, screen_y = frame.screen_point((x1, value))
                text = label
                if metrics_style.label_values:
                    text = "{} {}".format(text, _number(value))
                _metric_label(
                    labels,
                    screen_x,
                    screen_y,
                    text,
                    name,
                    resolved_style,
                )

    sidebearings = (
        "sidebearings" in metrics_style.show
        and metrics_style.sidebearing_line.visible
    )
    if not sidebearings:
        return

    for index, positioned in enumerate(layout.glyphs):
        glyph = positioned.glyph
        left = positioned.origin_x
        right = positioned.origin_x + glyph.advance_width
        for side, x, raw_value in (
            ("lsb", left, glyph.metrics.lsb),
            ("rsb", right, glyph.metrics.rsb),
        ):
            attributes = _line_attributes(
                metrics_style.sidebearing_line, frame.scale
            )
            attributes.update(
                {
                    "x1": _number(x),
                    "y1": _number(frame.ymin),
                    "x2": _number(x),
                    "y2": _number(frame.ymax),
                    "data-metric": "sidebearings",
                    "data-side": side,
                    "data-glyph-index": str(index),
                }
            )
            ET.SubElement(geometry, "line", attributes)
            if labels is not None:
                screen_x, _ = frame.screen_point((x, frame.ymax))
                text = side
                if metrics_style.label_values and raw_value is not None:
                    text = "{} {}".format(side, _number(raw_value))
                _metric_label(
                    labels,
                    screen_x,
                    frame.padding + metrics_style.label_size,
                    text,
                    "sidebearings",
                    resolved_style,
                )


def _render_fill(
    layer: ET.Element,
    layout: ir.Layout,
    resolved_style: style_contract.Style,
    frame: _Frame,
) -> None:
    geometry = _geometry_group(layer, frame)
    for index, positioned in enumerate(layout.glyphs):
        glyph_group = _positioned_glyph_group(geometry, index, positioned)
        if not resolved_style.outline.fill_enabled:
            continue
        paths = [
            _contour_path(contour)
            for contour in positioned.glyph.contours
            if contour.closed
        ]
        paths = [path for path in paths if path]
        if paths:
            ET.SubElement(
                glyph_group,
                "path",
                {
                    "d": " ".join(paths),
                    "fill": resolved_style.outline.fill,
                    "fill-opacity": _number(resolved_style.outline.fill_opacity),
                    "stroke": "none",
                },
            )


def _render_outline(
    layer: ET.Element,
    layout: ir.Layout,
    resolved_style: style_contract.Style,
    frame: _Frame,
) -> None:
    geometry = _geometry_group(layer, frame)
    line_style = resolved_style.outline.as_line()
    for index, positioned in enumerate(layout.glyphs):
        glyph_group = _positioned_glyph_group(geometry, index, positioned)
        if not line_style.visible:
            continue
        attributes = _line_attributes(line_style, frame.scale)
        for contour in positioned.glyph.contours:
            path = _contour_path(contour)
            if path:
                ET.SubElement(glyph_group, "path", dict(attributes, d=path))


def _render_handle_lines(
    layer: ET.Element,
    layout: ir.Layout,
    resolved_style: style_contract.Style,
    frame: _Frame,
) -> None:
    geometry = _geometry_group(layer, frame)
    line_style = resolved_style.handles.line
    for index, positioned in enumerate(layout.glyphs):
        glyph_group = _positioned_glyph_group(geometry, index, positioned)
        if not line_style.visible:
            continue
        base_attributes = _line_attributes(line_style, frame.scale)
        for contour in positioned.glyph.contours:
            for node in contour.nodes:
                for handle in (node.handle_in, node.handle_out):
                    if handle is None or handle == node.point:
                        continue
                    attributes = dict(base_attributes)
                    attributes.update(
                        {
                            "x1": _number(node.x),
                            "y1": _number(node.y),
                            "x2": _number(handle[0]),
                            "y2": _number(handle[1]),
                        }
                    )
                    ET.SubElement(glyph_group, "line", attributes)


def _render_handle_points(
    layer: ET.Element,
    layout: ir.Layout,
    resolved_style: style_contract.Style,
    frame: _Frame,
) -> None:
    geometry = _geometry_group(layer, frame)
    marker_style = resolved_style.handles.point
    for index, positioned in enumerate(layout.glyphs):
        glyph_group = _positioned_glyph_group(geometry, index, positioned)
        for contour in positioned.glyph.contours:
            for node in contour.nodes:
                for handle in (node.handle_in, node.handle_out):
                    if handle is None or handle == node.point:
                        continue
                    _marker(glyph_group, handle, marker_style, frame.scale)


def _render_nodes(
    layer: ET.Element,
    layout: ir.Layout,
    resolved_style: style_contract.Style,
    frame: _Frame,
) -> None:
    geometry = _geometry_group(layer, frame)
    for index, positioned in enumerate(layout.glyphs):
        glyph_group = _positioned_glyph_group(geometry, index, positioned)
        for contour in positioned.glyph.contours:
            for node in contour.nodes:
                marker_style = resolved_style.nodes.corner
                if resolved_style.nodes.distinguish_types and node.smooth:
                    marker_style = resolved_style.nodes.smooth
                _marker(glyph_group, node.point, marker_style, frame.scale)


def render_svg(
    layout: ir.Layout,
    style: style_contract.Style,
    *,
    title: Optional[str] = None,
) -> str:
    """Return an SVG rendering of an already positioned glyph layout."""
    frame = _resolve_frame(layout, style)
    root = ET.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "width": _number(frame.width),
            "height": _number(frame.height),
            "viewBox": "0 0 {} {}".format(
                _number(frame.width), _number(frame.height)
            ),
            "data-preset": style.preset_name,
        },
    )
    root.append(ET.Comment(" generated by glyphblueprint "))
    if title is not None:
        title_element = ET.SubElement(root, "title")
        title_element.text = title

    renderers = {
        "background": lambda layer: _render_background(layer, frame, style),
        "metrics": lambda layer: _render_metrics(layer, layout, style, frame),
        "fill": lambda layer: _render_fill(layer, layout, style, frame),
        "outline": lambda layer: _render_outline(layer, layout, style, frame),
        "handle_lines": lambda layer: _render_handle_lines(
            layer, layout, style, frame
        ),
        "handle_points": lambda layer: _render_handle_points(
            layer, layout, style, frame
        ),
        "nodes": lambda layer: _render_nodes(layer, layout, style, frame),
    }
    for name in style_contract.LAYER_ORDER:
        if not style.layers.enabled(name):
            continue
        layer = ET.SubElement(
            root,
            "g",
            {
                "id": name,
                "data-layer": name,
            },
        )
        renderers[name](layer)

    return ET.tostring(root, encoding="unicode", short_empty_elements=True)


def render_glyph_svg(
    glyph: ir.Glyph,
    style: style_contract.Style,
    *,
    title: Optional[str] = None,
) -> str:
    """Render one glyph at the origin using its own metrics and advance."""
    layout = ir.Layout(
        glyphs=[ir.PositionedGlyph(glyph=glyph, origin_x=0.0)],
        units_per_em=glyph.units_per_em,
        metrics=glyph.metrics,
        total_advance=glyph.advance_width,
    )
    return render_svg(layout, style, title=title)


__all__ = ["render_svg", "render_glyph_svg"]
