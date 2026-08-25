"""Internal representation shared by every parser and the renderer.

This module is the contract. Parsers (``.glyphs``, ``.otf``/``.ttf``, ``.ufo``)
write to these types; the layout engine and renderer only ever read them. Keep
coordinates in raw font units -- scaling happens at render time only.

Rules the renderer relies on:

* Handles are absolute coordinates in the same space as anchors, so a handle
  line is drawn ``node.point -> handle`` with no math.
* A node owns its incoming and outgoing handle. No hunting across the array.
* Between two consecutive on-curve nodes the segment is a cubic if either the
  first node's ``handle_out`` or the second node's ``handle_in`` is present,
  otherwise a straight line. A missing handle on a cubic sits on its own anchor.
* TTF quadratics are upconverted to cubics at parse time, so this is a
  single-curve-type model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]

#: Segment kind leading *into* this node from the previous on-curve node.
SEGMENT_LINE = "line"
SEGMENT_CURVE = "curve"


@dataclass
class Node:
    """A single on-curve anchor plus the off-curve handles it owns."""

    point: Point
    #: ``"line"`` or ``"curve"`` -- describes the segment arriving at this node.
    type: str = SEGMENT_LINE
    #: True when the designer marked this node smooth (tangent continuous).
    #: Only trustworthy for ``.glyphs``/``.ufo`` sources; see ``Glyph.node_types_exact``.
    smooth: bool = False
    #: Incoming off-curve control point (absolute), or None.
    handle_in: Optional[Point] = None
    #: Outgoing off-curve control point (absolute), or None.
    handle_out: Optional[Point] = None

    @property
    def x(self) -> float:
        return self.point[0]

    @property
    def y(self) -> float:
        return self.point[1]


@dataclass
class Contour:
    """One closed or open path made of on-curve nodes."""

    nodes: List[Node] = field(default_factory=list)
    closed: bool = True

    def segments(self) -> List[Tuple[Node, Node]]:
        """Ordered (start, end) on-curve node pairs describing each segment.

        For a closed contour the final segment wraps from the last node back to
        the first. For an open contour it stops at the last node.
        """
        if len(self.nodes) < 2:
            return []
        pairs = [(self.nodes[i], self.nodes[i + 1]) for i in range(len(self.nodes) - 1)]
        if self.closed:
            pairs.append((self.nodes[-1], self.nodes[0]))
        return pairs


@dataclass
class Metrics:
    """Vertical metrics plus per-glyph side bearings, in font units."""

    baseline: float = 0.0
    x_height: Optional[float] = None
    cap_height: Optional[float] = None
    ascender: Optional[float] = None
    descender: Optional[float] = None
    lsb: Optional[float] = None
    rsb: Optional[float] = None

    def vertical_items(self) -> List[Tuple[str, float]]:
        """Named vertical metrics that are actually defined, low to high."""
        raw = [
            ("descender", self.descender),
            ("baseline", self.baseline),
            ("x-height", self.x_height),
            ("cap height", self.cap_height),
            ("ascender", self.ascender),
        ]
        return [(name, value) for name, value in raw if value is not None]


@dataclass
class Glyph:
    """A single glyph on a single layer, in raw font units."""

    name: str
    advance_width: float
    units_per_em: float = 1000.0
    contours: List[Contour] = field(default_factory=list)
    metrics: Metrics = field(default_factory=Metrics)
    #: Unicode codepoints this glyph is mapped from, if any.
    unicodes: List[int] = field(default_factory=list)
    #: Name of the layer this geometry came from ("" for the default master).
    layer_name: str = ""
    #: False when smooth/corner classification was inferred rather than authored
    #: (i.e. the source was a compiled OTF/TTF).
    node_types_exact: bool = True
    #: Glyphs category metadata, when explicitly authored in the source.
    category: Optional[str] = None
    #: Glyphs subCategory metadata, when explicitly authored in the source.
    subcategory: Optional[str] = None
    #: Glyphs script metadata, when explicitly authored in the source.
    script: Optional[str] = None

    def bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """Tight bounding box over anchors *and* handles: (xmin, ymin, xmax, ymax).

        Handles are included because a blueprint draws them, so they must be
        inside the frame even when they sit outside the filled outline.
        """
        xs: List[float] = []
        ys: List[float] = []
        for contour in self.contours:
            for node in contour.nodes:
                for pt in (node.point, node.handle_in, node.handle_out):
                    if pt is not None:
                        xs.append(pt[0])
                        ys.append(pt[1])
        if not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass
class PositionedGlyph:
    """A glyph placed on the layout baseline by the layout engine."""

    glyph: Glyph
    #: Pen position of the glyph origin, in font units.
    origin_x: float
    origin_y: float = 0.0
    #: Kerning applied *before* this glyph (0 for the first glyph).
    kern_before: float = 0.0
    #: The character this glyph was produced from, for diagnostics.
    source_char: Optional[str] = None


@dataclass
class Layout:
    """The full positioned lockup for an input string."""

    glyphs: List[PositionedGlyph] = field(default_factory=list)
    units_per_em: float = 1000.0
    #: Font-level vertical metrics for the run (per-glyph side bearings live on
    #: each glyph's own ``metrics``).
    metrics: Metrics = field(default_factory=Metrics)
    total_advance: float = 0.0

    def bounds(self) -> Optional[Tuple[float, float, float, float]]:
        """Union of every positioned glyph's bounds, in layout space."""
        boxes = []
        for pg in self.glyphs:
            box = pg.glyph.bounds()
            if box is None:
                continue
            boxes.append(
                (
                    box[0] + pg.origin_x,
                    box[1] + pg.origin_y,
                    box[2] + pg.origin_x,
                    box[3] + pg.origin_y,
                )
            )
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )


@dataclass
class Font:
    """A parsed source file: the glyph set plus everything layout needs."""

    #: Glyph geometry keyed by glyph name, for the currently selected layer.
    glyphs: Dict[str, Glyph] = field(default_factory=dict)
    units_per_em: float = 1000.0
    metrics: Metrics = field(default_factory=Metrics)
    #: codepoint -> glyph name.
    cmap: Dict[int, str] = field(default_factory=dict)
    #: Flat pair kerning: (left_key, right_key) -> value. Keys are glyph names
    #: or group references (``@MMK_L_x`` / ``@MMK_R_x`` style).
    kerning: Dict[Tuple[str, str], float] = field(default_factory=dict)
    #: glyph name -> kern group name, for the glyph acting as the LEFT member
    #: of a pair (Glyphs calls this the glyph's *right* kerning group).
    kern_group_left: Dict[str, str] = field(default_factory=dict)
    #: glyph name -> kern group name, for the glyph acting as the RIGHT member
    #: of a pair (Glyphs calls this the glyph's *left* kerning group).
    kern_group_right: Dict[str, str] = field(default_factory=dict)
    family_name: str = ""
    master_name: str = ""
    #: Source format tag: "glyphs" | "ufo" | "otf" | "ttf".
    source_format: str = ""
    node_types_exact: bool = True

    def glyph_for_char(self, char: str) -> Optional[Glyph]:
        name = self.cmap.get(ord(char))
        if name is not None:
            return self.glyphs.get(name)
        return self.glyphs.get(char)


@dataclass
class LayerInfo:
    """Describes one available layer for a glyph (for ``--list-layers``)."""

    layer_id: str
    name: str
    is_master: bool = False
    associated_master_id: str = ""
    contour_count: int = 0
    has_open_contours: bool = False


@dataclass
class MasterInfo:
    """Describes one selectable master/style in a Glyphs source file."""

    master_id: str
    name: str


def quadratic_to_cubic(
    start: Point, control: Point, end: Point
) -> Tuple[Point, Point]:
    """Exact quadratic->cubic control point conversion.

    Returns the two cubic control points for the quadratic defined by
    ``start``, ``control``, ``end``. The conversion is lossless: a quadratic is
    a cubic whose controls sit two-thirds of the way from each endpoint toward
    the quadratic's single control point.
    """
    c1 = (
        start[0] + 2.0 / 3.0 * (control[0] - start[0]),
        start[1] + 2.0 / 3.0 * (control[1] - start[1]),
    )
    c2 = (
        end[0] + 2.0 / 3.0 * (control[0] - end[0]),
        end[1] + 2.0 / 3.0 * (control[1] - end[1]),
    )
    return c1, c2


def infer_smooth(
    handle_in: Optional[Point],
    point: Point,
    handle_out: Optional[Point],
    tolerance_deg: float = 5.0,
) -> bool:
    """Infer smooth-vs-corner from handle colinearity.

    Used for compiled OTF/TTF sources, which carry no authored node type. A
    node counts as smooth when both handles exist and the incoming and outgoing
    directions are within ``tolerance_deg`` of being opposite.
    """
    import math

    if handle_in is None or handle_out is None:
        return False
    inv = (point[0] - handle_in[0], point[1] - handle_in[1])
    outv = (handle_out[0] - point[0], handle_out[1] - point[1])
    in_len = math.hypot(*inv)
    out_len = math.hypot(*outv)
    if in_len == 0 or out_len == 0:
        return False
    cos = (inv[0] * outv[0] + inv[1] * outv[1]) / (in_len * out_len)
    cos = max(-1.0, min(1.0, cos))
    return math.degrees(math.acos(cos)) <= tolerance_deg


__all__ = [
    "Point",
    "SEGMENT_LINE",
    "SEGMENT_CURVE",
    "Node",
    "Contour",
    "Metrics",
    "Glyph",
    "PositionedGlyph",
    "Layout",
    "Font",
    "LayerInfo",
    "MasterInfo",
    "quadratic_to_cubic",
    "infer_smooth",
]
