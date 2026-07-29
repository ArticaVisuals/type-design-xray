"""Parser for compiled OpenType and TrueType font files."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import DefaultDict, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from fontTools.pens.basePen import (
    decomposeQuadraticSegment,
    decomposeSuperBezierSegment,
)
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont

from typedesignxray import ir


def _point(value: Sequence[float]) -> ir.Point:
    return (float(value[0]), float(value[1]))


class _OutlineBuilder:
    """Normalize pen commands while each segment's context is still available."""

    def __init__(self) -> None:
        self.contours: List[ir.Contour] = []
        self._nodes: Optional[List[ir.Node]] = None

    def consume(self, commands: Iterable[Tuple[str, Tuple[object, ...]]]) -> None:
        for operation, operands in commands:
            if operation == "moveTo":
                self._move_to(_point(operands[0]))
            elif operation == "lineTo":
                self._line_to(_point(operands[0]))
            elif operation == "curveTo":
                self._curve_to(operands)
            elif operation == "qCurveTo":
                self._qcurve_to(operands)
            elif operation == "closePath":
                self._finish(closed=True)
            elif operation == "endPath":
                self._finish(closed=False)
            else:
                raise ValueError(
                    "Unsupported outline command {!r} in compiled font".format(
                        operation
                    )
                )
        if self._nodes is not None:
            self._finish(closed=False)

    def _move_to(self, point: ir.Point) -> None:
        if self._nodes is not None:
            self._finish(closed=False)
        self._nodes = [ir.Node(point=point)]

    def _require_nodes(self, operation: str) -> List[ir.Node]:
        if self._nodes is None:
            raise ValueError("{} appeared before moveTo".format(operation))
        return self._nodes

    def _line_to(self, point: ir.Point) -> None:
        nodes = self._require_nodes("lineTo")
        nodes.append(ir.Node(point=point, type=ir.SEGMENT_LINE))

    def _append_cubic(
        self, control1: ir.Point, control2: ir.Point, end: ir.Point
    ) -> None:
        nodes = self._require_nodes("curveTo")
        nodes[-1].handle_out = control1
        nodes.append(
            ir.Node(
                point=end,
                type=ir.SEGMENT_CURVE,
                handle_in=control2,
            )
        )

    def _curve_to(self, operands: Sequence[object]) -> None:
        points = tuple(_point(value) for value in operands)
        if len(points) == 1:
            self._line_to(points[0])
            return
        if len(points) == 2:
            self._append_quadratic(points[0], points[1])
            return
        if len(points) < 3:
            raise ValueError("curveTo requires an endpoint")
        for control1, control2, end in decomposeSuperBezierSegment(points):
            self._append_cubic(
                _point(control1),
                _point(control2),
                _point(end),
            )

    def _append_quadratic(self, control: ir.Point, end: ir.Point) -> None:
        nodes = self._require_nodes("qCurveTo")
        control1, control2 = ir.quadratic_to_cubic(
            nodes[-1].point,
            control,
            end,
        )
        self._append_cubic(control1, control2, end)

    def _qcurve_to(self, operands: Sequence[object]) -> None:
        if not operands:
            raise ValueError("qCurveTo requires an endpoint")

        all_off_curve = operands[-1] is None
        if all_off_curve:
            off_curves = tuple(_point(value) for value in operands[:-1])
            if not off_curves:
                raise ValueError("all-off-curve contour has no points")
            first = off_curves[0]
            last = off_curves[-1]
            start = (
                0.5 * (last[0] + first[0]),
                0.5 * (last[1] + first[1]),
            )
            self._move_to(start)
            points = off_curves + (start,)
        else:
            points = tuple(_point(value) for value in operands)

        if len(points) == 1:
            self._line_to(points[0])
            return
        for control, end in decomposeQuadraticSegment(points):
            self._append_quadratic(_point(control), _point(end))

    def _finish(self, closed: bool) -> None:
        if self._nodes is None:
            return
        nodes = self._nodes
        self._nodes = None

        if closed and len(nodes) > 1 and nodes[-1].point == nodes[0].point:
            closing_node = nodes.pop()
            nodes[0].type = closing_node.type
            nodes[0].handle_in = closing_node.handle_in

        if nodes:
            self.contours.append(ir.Contour(nodes=nodes, closed=closed))


def _contours_from_commands(
    commands: Iterable[Tuple[str, Tuple[object, ...]]],
    smooth_tolerance_deg: Optional[float],
) -> List[ir.Contour]:
    builder = _OutlineBuilder()
    builder.consume(commands)
    for contour in builder.contours:
        for node in contour.nodes:
            node.smooth = (
                False
                if smooth_tolerance_deg is None
                else ir.infer_smooth(
                    node.handle_in,
                    node.point,
                    node.handle_out,
                    tolerance_deg=smooth_tolerance_deg,
                )
            )
    return builder.contours


def _font_metrics(font: TTFont) -> ir.Metrics:
    os2 = font["OS/2"] if "OS/2" in font else None
    hhea = font["hhea"] if "hhea" in font else None

    ascender = getattr(hhea, "ascent", None)
    descender = getattr(hhea, "descent", None)
    if ascender is None and os2 is not None:
        ascender = getattr(os2, "sTypoAscender", None)
    if descender is None and os2 is not None:
        descender = getattr(os2, "sTypoDescender", None)

    cap_height = getattr(os2, "sCapHeight", 0) if os2 is not None else 0
    x_height = getattr(os2, "sxHeight", 0) if os2 is not None else 0
    return ir.Metrics(
        baseline=0.0,
        x_height=float(x_height) if x_height else None,
        cap_height=float(cap_height) if cap_height else None,
        ascender=float(ascender) if ascender is not None else None,
        descender=float(descender) if descender is not None else None,
    )


def _glyph_metrics(
    font_metrics: ir.Metrics,
    advance: float,
    lsb: float,
    bounds: Optional[Tuple[float, float, float, float]],
) -> ir.Metrics:
    rsb = None
    if bounds is not None:
        rsb = advance - lsb - (bounds[2] - bounds[0])
    return ir.Metrics(
        baseline=font_metrics.baseline,
        x_height=font_metrics.x_height,
        cap_height=font_metrics.cap_height,
        ascender=font_metrics.ascender,
        descender=font_metrics.descender,
        lsb=lsb,
        rsb=rsb,
    )


def _source_format(font: TTFont) -> str:
    if "CFF " in font or "CFF2" in font:
        return "otf"
    if "glyf" in font:
        return "ttf"
    raise ValueError("Compiled font has neither a CFF/CFF2 nor a glyf outline table")


def _family_name(font: TTFont) -> str:
    if "name" not in font:
        return ""
    name_table = font["name"]
    family = name_table.getBestFamilyName()
    if family:
        return family
    record = name_table.getName(1, 3, 1)
    return record.toUnicode() if record is not None else ""


def _legacy_kerning(font: TTFont) -> Dict[Tuple[str, str], float]:
    kerning: Dict[Tuple[str, str], float] = {}
    if "kern" not in font:
        return kerning
    for subtable in getattr(font["kern"], "kernTables", ()):
        if getattr(subtable, "format", None) != 0:
            continue
        for pair, value in getattr(subtable, "kernTable", {}).items():
            if len(pair) == 2:
                kerning[(str(pair[0]), str(pair[1]))] = float(value)
    return kerning


def _x_advance(pair_value_record: object) -> float:
    value1 = getattr(pair_value_record, "Value1", None)
    value = getattr(value1, "XAdvance", 0) if value1 is not None else 0
    return float(value or 0)


def _pair_positioning_subtables(lookup: object) -> Iterable[object]:
    lookup_type = getattr(lookup, "LookupType", None)
    if lookup_type == 2:
        yield from getattr(lookup, "SubTable", ())
    elif lookup_type == 9:
        for extension in getattr(lookup, "SubTable", ()):
            if getattr(extension, "ExtensionLookupType", None) == 2:
                subtable = getattr(extension, "ExtSubTable", None)
                if subtable is not None:
                    yield subtable


def _gpos_kerning(
    font: TTFont,
) -> Tuple[
    Dict[Tuple[str, str], float],
    Dict[str, str],
    Dict[str, str],
]:
    kerning: Dict[Tuple[str, str], float] = {}
    group_left: Dict[str, str] = {}
    group_right: Dict[str, str] = {}
    if "GPOS" not in font:
        return kerning, group_left, group_right

    try:
        table = font["GPOS"].table
        feature_list = getattr(table, "FeatureList", None)
        lookup_list = getattr(table, "LookupList", None)
        if feature_list is None or lookup_list is None:
            return kerning, group_left, group_right

        lookup_indices: Set[int] = set()
        for record in getattr(feature_list, "FeatureRecord", ()):
            if getattr(record, "FeatureTag", None) == "kern":
                lookup_indices.update(
                    getattr(getattr(record, "Feature", None), "LookupListIndex", ())
                )

        next_left_group = 0
        next_right_group = 0
        glyph_order = font.getGlyphOrder()
        for lookup_index in sorted(lookup_indices):
            lookup = lookup_list.Lookup[lookup_index]
            for subtable in _pair_positioning_subtables(lookup):
                positioning_format = getattr(subtable, "Format", None)
                if positioning_format == 1:
                    coverage = getattr(
                        getattr(subtable, "Coverage", None), "glyphs", ()
                    )
                    pair_sets = getattr(subtable, "PairSet", ())
                    for left, pair_set in zip(coverage, pair_sets):
                        for record in getattr(pair_set, "PairValueRecord", ()):
                            value = _x_advance(record)
                            if value:
                                kerning[(left, record.SecondGlyph)] = value
                elif positioning_format == 2:
                    coverage = set(
                        getattr(
                            getattr(subtable, "Coverage", None), "glyphs", ()
                        )
                    )
                    class_def1 = getattr(
                        getattr(subtable, "ClassDef1", None), "classDefs", {}
                    )
                    class_def2 = getattr(
                        getattr(subtable, "ClassDef2", None), "classDefs", {}
                    )
                    left_members: DefaultDict[int, List[str]] = defaultdict(list)
                    right_members: DefaultDict[int, List[str]] = defaultdict(list)
                    for glyph_name in coverage:
                        left_members[class_def1.get(glyph_name, 0)].append(
                            glyph_name
                        )
                    for glyph_name in glyph_order:
                        right_members[class_def2.get(glyph_name, 0)].append(
                            glyph_name
                        )

                    class_pairs: List[Tuple[int, int, float]] = []
                    for left_class, class1_record in enumerate(
                        getattr(subtable, "Class1Record", ())
                    ):
                        for right_class, class2_record in enumerate(
                            getattr(class1_record, "Class2Record", ())
                        ):
                            value = _x_advance(class2_record)
                            if value:
                                class_pairs.append(
                                    (left_class, right_class, value)
                                )

                    left_keys: Dict[int, str] = {}
                    right_keys: Dict[int, str] = {}
                    for left_class, right_class, value in class_pairs:
                        if not left_members[left_class] or not right_members[right_class]:
                            continue
                        if left_class not in left_keys:
                            key = "@CLASS_L_{}".format(next_left_group)
                            next_left_group += 1
                            left_keys[left_class] = key
                            for glyph_name in left_members[left_class]:
                                group_left[glyph_name] = key
                        if right_class not in right_keys:
                            key = "@CLASS_R_{}".format(next_right_group)
                            next_right_group += 1
                            right_keys[right_class] = key
                            for glyph_name in right_members[right_class]:
                                group_right[glyph_name] = key
                        kerning[
                            (left_keys[left_class], right_keys[right_class])
                        ] = value
    except Exception:
        return {}, {}, {}

    return kerning, group_left, group_right


def parse_binary(
    path: object,
    layer: Optional[str] = None,
    master: Optional[str] = None,
    smooth_tolerance_deg: Optional[float] = 5.0,
    glyph_names: Optional[Iterable[str]] = None,
) -> ir.Font:
    """Parse a compiled font into cubic, component-free outline geometry."""

    del master
    if layer is not None:
        raise ValueError(
            "Layer selection is only available for .glyphs and .ufo sources; "
            "compiled fonts contain a single outline layer."
        )

    font = TTFont(Path(path), lazy=True)
    try:
        units_per_em = float(font["head"].unitsPerEm)
        metrics = _font_metrics(font)
        cmap = dict(font.getBestCmap() or {})
        glyph_order = font.getGlyphOrder()
        horizontal_metrics = font["hmtx"].metrics
        missing_horizontal_metrics = {
            name for name in glyph_order if name not in horizontal_metrics
        }
        for glyph_name in missing_horizontal_metrics:
            horizontal_metrics[glyph_name] = (0, 0)
        if glyph_names is not None:
            requested = (
                {glyph_names}
                if isinstance(glyph_names, str)
                else set(glyph_names)
            )
            glyph_order = [name for name in glyph_order if name in requested]

        unicodes_by_name: DefaultDict[str, List[int]] = defaultdict(list)
        for codepoint, glyph_name in cmap.items():
            unicodes_by_name[glyph_name].append(codepoint)

        glyph_set = font.getGlyphSet()
        glyphs: Dict[str, ir.Glyph] = {}
        for glyph_name in glyph_order:
            recording_pen = DecomposingRecordingPen(glyph_set)
            glyph_set[glyph_name].draw(recording_pen)
            contours = _contours_from_commands(
                recording_pen.value,
                smooth_tolerance_deg,
            )

            bounds_pen = BoundsPen(glyph_set)
            glyph_set[glyph_name].draw(bounds_pen)
            bounds = bounds_pen.bounds

            if glyph_name in missing_horizontal_metrics:
                advance = 0.0
                lsb = bounds[0] if bounds is not None else 0.0
            else:
                advance, lsb = horizontal_metrics[glyph_name]
            advance = float(advance)
            lsb = float(lsb)
            glyphs[glyph_name] = ir.Glyph(
                name=glyph_name,
                advance_width=advance,
                units_per_em=units_per_em,
                contours=contours,
                metrics=_glyph_metrics(metrics, advance, lsb, bounds),
                unicodes=sorted(unicodes_by_name[glyph_name]),
                node_types_exact=False,
            )

        kerning = _legacy_kerning(font)
        gpos_kerning, group_left, group_right = _gpos_kerning(font)
        kerning.update(gpos_kerning)
        return ir.Font(
            glyphs=glyphs,
            units_per_em=units_per_em,
            metrics=metrics,
            cmap=cmap,
            kerning=kerning,
            kern_group_left=group_left,
            kern_group_right=group_right,
            family_name=_family_name(font),
            source_format=_source_format(font),
            node_types_exact=False,
        )
    finally:
        font.close()


__all__ = ["parse_binary"]
