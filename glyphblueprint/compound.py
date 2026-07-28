"""Overlap removal for glyph outlines using the font export path engine."""

from __future__ import annotations

import importlib
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import ir


_INSTALL_COMMAND = 'pip install "glyphblueprint[compound]"'
_MATCH_EPSILON = 0.01


def _pathops_module() -> Any:
    try:
        return importlib.import_module("pathops")
    except Exception as exc:
        raise RuntimeError(
            "overlap removal requires skia-pathops; install it with: {}".format(
                _INSTALL_COMMAND
            )
        ) from exc


def is_available() -> bool:
    """Report whether the optional overlap-removal engine can be imported."""
    try:
        importlib.import_module("pathops")
    except Exception:
        return False
    return True


def _draw_contour(pen: Any, contour: ir.Contour) -> None:
    if not contour.nodes:
        return
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
    pen.closePath()


def _smooth_lookup(
    contours: Sequence[ir.Contour],
) -> Dict[ir.Point, bool]:
    lookup: Dict[ir.Point, bool] = {}
    for contour in contours:
        for node in contour.nodes:
            if node.point not in lookup:
                lookup[node.point] = node.smooth
    return lookup


def _matched_smooth(
    point: ir.Point, lookup: Dict[ir.Point, bool]
) -> Optional[bool]:
    if point in lookup:
        return lookup[point]

    epsilon_squared = _MATCH_EPSILON * _MATCH_EPSILON
    closest_distance = epsilon_squared
    closest: Optional[bool] = None
    for candidate, smooth in lookup.items():
        dx = point[0] - candidate[0]
        dy = point[1] - candidate[1]
        distance = dx * dx + dy * dy
        if distance <= closest_distance:
            closest_distance = distance
            closest = smooth
    return closest


def _expand_quadratics(segments: List[Tuple[str, Any]]) -> List[Tuple[str, Any]]:
    """Rewrite any ``qCurveTo`` skia emits into equivalent cubics.

    skia represents conic sections as quadratics, so even an all-cubic input
    can come back with quadratic segments. The IR is deliberately
    single-curve-type, so they are upconverted here rather than leaking
    downstream. Consecutive off-curve points imply an on-curve point at their
    midpoint, exactly as in TrueType.
    """
    if not any(operation == "qCurveTo" for operation, _ in segments):
        return segments

    expanded: List[Tuple[str, Any]] = []
    current: Optional[ir.Point] = None
    for operation, points in segments:
        if operation != "qCurveTo":
            expanded.append((operation, points))
            if operation in ("moveTo", "lineTo", "curveTo") and points:
                current = points[-1]
            continue

        controls = list(points[:-1])
        end = points[-1]
        if current is None or not controls:
            expanded.append(("lineTo", (end,)))
            current = end
            continue

        for first, second in zip(controls, controls[1:]):
            implied = ((first[0] + second[0]) / 2.0, (first[1] + second[1]) / 2.0)
            c1, c2 = ir.quadratic_to_cubic(current, first, implied)
            expanded.append(("curveTo", (c1, c2, implied)))
            current = implied
        c1, c2 = ir.quadratic_to_cubic(current, controls[-1], end)
        expanded.append(("curveTo", (c1, c2, end)))
        current = end
    return expanded


def _contour_from_path(
    path_contour: Any,
    smooth_lookup: Dict[ir.Point, bool],
    smooth_tolerance_deg: float,
) -> Tuple[ir.Contour, bool]:
    segments = _expand_quadratics(list(path_contour.segments))
    if not segments or segments[0][0] != "moveTo":
        raise ValueError("pathops returned a contour without a moveTo")

    start_point = segments[0][1][0]
    nodes = [ir.Node(point=start_point)]
    closed = False
    for index, (operation, points) in enumerate(segments[1:], 1):
        if operation == "closePath":
            closed = True
            continue
        if operation not in ("lineTo", "curveTo"):
            raise ValueError(
                "pathops returned unsupported segment {!r}".format(operation)
            )

        end_point = points[-1]
        closes_at_start = (
            end_point == start_point
            and index + 1 < len(segments)
            and segments[index + 1][0] == "closePath"
        )
        if operation == "curveTo":
            nodes[-1].handle_out = points[0]
            if closes_at_start:
                nodes[0].type = ir.SEGMENT_CURVE
                nodes[0].handle_in = points[1]
            else:
                nodes.append(
                    ir.Node(
                        point=end_point,
                        type=ir.SEGMENT_CURVE,
                        handle_in=points[1],
                    )
                )
        elif closes_at_start:
            nodes[0].type = ir.SEGMENT_LINE
        else:
            nodes.append(
                ir.Node(point=end_point, type=ir.SEGMENT_LINE)
            )

    inferred = False
    for node in nodes:
        smooth = _matched_smooth(node.point, smooth_lookup)
        if smooth is None:
            node.smooth = ir.infer_smooth(
                node.handle_in,
                node.point,
                node.handle_out,
                tolerance_deg=smooth_tolerance_deg,
            )
            inferred = True
        else:
            node.smooth = smooth
    return ir.Contour(nodes=nodes, closed=closed), inferred


def compound_glyph(
    glyph: ir.Glyph, *, smooth_tolerance_deg: float = 5.0
) -> ir.Glyph:
    """Return a glyph whose closed contours have export-style overlaps removed."""
    closed_contours = [
        contour for contour in glyph.contours if contour.closed
    ]
    if not closed_contours:
        return replace(glyph, contours=list(glyph.contours))

    pathops = _pathops_module()
    try:
        path = pathops.Path()
        pen = path.getPen()
        for contour in closed_contours:
            _draw_contour(pen, contour)
        path.simplify(fix_winding=True, keep_starting_points=True)

        lookup = _smooth_lookup(closed_contours)
        compounded: List[ir.Contour] = []
        inferred = False
        for path_contour in path.contours:
            contour, contour_inferred = _contour_from_path(
                path_contour,
                lookup,
                smooth_tolerance_deg,
            )
            compounded.append(contour)
            inferred = inferred or contour_inferred
    except Exception as exc:
        raise RuntimeError(
            "failed to remove overlaps in glyph {!r}: {}".format(
                glyph.name, exc
            )
        ) from None

    open_contours = [
        contour for contour in glyph.contours if not contour.closed
    ]
    return replace(
        glyph,
        contours=compounded + open_contours,
        node_types_exact=glyph.node_types_exact and not inferred,
    )


def compound_font(
    font: ir.Font, *, smooth_tolerance_deg: float = 5.0
) -> ir.Font:
    """Return a font with overlap removal applied independently to each glyph."""
    glyphs = {
        name: compound_glyph(
            glyph, smooth_tolerance_deg=smooth_tolerance_deg
        )
        for name, glyph in font.glyphs.items()
    }
    return replace(
        font,
        glyphs=glyphs,
        node_types_exact=font.node_types_exact
        and all(glyph.node_types_exact for glyph in glyphs.values()),
    )


__all__ = ["is_available", "compound_glyph", "compound_font"]
