"""Resolved style model. The second half of the frozen contract.

``Style`` is what the renderer consumes. The config layer's only job is to
produce one of these by deep-merging (defaults <- preset <- config file <-
CLI overrides). Nothing visual is hard-coded in the renderer; if it can be
seen, it is settable here.

Every dataclass round-trips through ``to_dict``/``from_dict`` so config files
and CLI overrides share one code path. Dotted keys ("handles.size") address any
leaf, which is what the CLI's ``--set`` override plumbing uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

#: Marker shapes available for handle points and on-curve nodes.
SHAPES = ("circle", "square", "diamond", "triangle", "cross", "none")

#: Dash patterns, resolved to SVG stroke-dasharray at render time.
DASH_PATTERNS = ("solid", "dashed", "dotted", "dashdot")

#: Layer names in paint order (first painted = bottom).
LAYER_ORDER = (
    "background",
    "metrics",
    "fill",
    "outline",
    "handle_lines",
    "handle_points",
    "nodes",
)

#: Metric guides that can be toggled individually.
METRIC_NAMES = (
    "baseline",
    "xheight",
    "capheight",
    "ascender",
    "descender",
    "sidebearings",
)

#: How the output viewBox is chosen.
FRAME_MODES = ("auto", "em", "metrics")


# --------------------------------------------------------------------------
# Style sections
# --------------------------------------------------------------------------


@dataclass
class MarkerStyle:
    """A drawn point marker: handle point, corner node, or smooth node."""

    shape: str = "circle"
    #: Radius for round/diamond/triangle shapes, half-side for squares, in px
    #: at the rendered scale (not font units) so markers stay legible at any size.
    size: float = 4.0
    fill: Optional[str] = "#ffffff"
    stroke: Optional[str] = "#3d8bfd"
    stroke_width: float = 1.5
    #: When true the fill is dropped, leaving an outlined ("hollow") marker.
    hollow: bool = False
    opacity: float = 1.0
    visible: bool = True

    def effective_fill(self) -> str:
        if self.hollow or self.fill is None:
            return "none"
        return self.fill

    def effective_stroke(self) -> str:
        return self.stroke if self.stroke is not None else "none"


@dataclass
class LineStyle:
    """A drawn line: handle direction lines, metric guides, outline stroke."""

    color: str = "#3d8bfd"
    width: float = 1.0
    dash: str = "solid"
    opacity: float = 1.0
    visible: bool = True
    linecap: str = "butt"
    linejoin: str = "miter"

    def dasharray(self) -> Optional[str]:
        """SVG ``stroke-dasharray`` for this line's dash pattern, scaled to width."""
        w = max(self.width, 0.1)
        if self.dash == "dashed":
            return "{:g},{:g}".format(w * 4, w * 3)
        if self.dash == "dotted":
            return "{:g},{:g}".format(w * 0.1, w * 2.5)
        if self.dash == "dashdot":
            return "{:g},{:g},{:g},{:g}".format(w * 4, w * 2.5, w * 0.1, w * 2.5)
        return None


@dataclass
class OutlineStyle:
    """The glyph contour itself."""

    stroke: str = "#3d8bfd"
    width: float = 1.6
    dash: str = "solid"
    opacity: float = 1.0
    visible: bool = True
    linecap: str = "round"
    linejoin: str = "round"
    #: Translucent fill under the outline, as in the reference blueprints.
    fill_enabled: bool = False
    fill: str = "#3d8bfd"
    fill_opacity: float = 0.12

    def as_line(self) -> LineStyle:
        return LineStyle(
            color=self.stroke,
            width=self.width,
            dash=self.dash,
            opacity=self.opacity,
            visible=self.visible,
            linecap=self.linecap,
            linejoin=self.linejoin,
        )


@dataclass
class HandleStyle:
    """Off-curve handles: the point markers and the lines that reach them."""

    point: MarkerStyle = field(
        default_factory=lambda: MarkerStyle(
            shape="circle", size=3.5, fill="#0b1f3a", stroke="#5aa9ff", stroke_width=1.4
        )
    )
    line: LineStyle = field(
        default_factory=lambda: LineStyle(color="#5aa9ff", width=1.0, dash="solid", opacity=0.9)
    )


@dataclass
class NodeStyle:
    """On-curve nodes, optionally distinguished by corner vs smooth."""

    corner: MarkerStyle = field(
        default_factory=lambda: MarkerStyle(
            shape="square", size=4.0, fill="#ffffff", stroke="#3d8bfd", stroke_width=1.6
        )
    )
    smooth: MarkerStyle = field(
        default_factory=lambda: MarkerStyle(
            shape="circle", size=4.0, fill="#3d8bfd", stroke="#ffffff", stroke_width=1.6
        )
    )
    #: When False every on-curve node is drawn with the ``corner`` style,
    #: collapsing the corner/smooth distinction.
    distinguish_types: bool = True


@dataclass
class MetricsStyle:
    """Typographic guides and their numeric labels."""

    visible: bool = False
    #: Subset of ``METRIC_NAMES`` to draw.
    show: List[str] = field(
        default_factory=lambda: ["baseline", "xheight", "capheight"]
    )
    line: LineStyle = field(
        default_factory=lambda: LineStyle(color="#2a4a7a", width=1.0, dash="dashed", opacity=0.9)
    )
    sidebearing_line: LineStyle = field(
        default_factory=lambda: LineStyle(color="#2a4a7a", width=1.0, dash="dotted", opacity=0.8)
    )
    labels: bool = True
    label_color: str = "#6f9fd8"
    label_size: float = 11.0
    label_family: str = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    #: Any CSS font-weight: "normal", "bold", or a numeric string "100".."900".
    label_weight: str = "normal"
    #: CSS font-style: "normal", "italic" or "oblique".
    label_style: str = "normal"
    #: CSS letter-spacing in px; negative tightens.
    label_letter_spacing: float = 0.0
    label_opacity: float = 1.0
    #: Small-caps and other variants, e.g. "small-caps". "normal" to disable.
    label_variant: str = "normal"
    #: Include the raw font-unit value in each label ("x-height 510").
    label_values: bool = True
    #: Guide overhang past the lockup on each side, in px.
    extend: float = 24.0


@dataclass
class CanvasStyle:
    """Background, framing and output sizing."""

    #: None means transparent -- no background rect is emitted at all, so the
    #: blueprint composites straight into whatever it is placed over. Presets
    #: may set a colour; the bare default does not.
    background: Optional[str] = None
    #: "auto" fits drawn geometry, "em" locks to the em square, "metrics" locks
    #: to descender..ascender by the full advance.
    frame: str = "auto"
    #: Padding around the frame, in px at render scale.
    padding: float = 48.0
    #: Target output width in px; height follows the aspect ratio.
    width: int = 1400
    #: PNG raster width; falls back to ``width`` when unset.
    png_width: Optional[int] = None


@dataclass
class LayerToggles:
    """Independent on/off for each render layer."""

    background: bool = True
    metrics: bool = True
    fill: bool = True
    outline: bool = True
    handle_lines: bool = True
    handle_points: bool = True
    nodes: bool = True

    def enabled(self, name: str) -> bool:
        return bool(getattr(self, name, False))


@dataclass
class ExportStyle:
    """How the SVG is structured for downstream editors.

    Illustrator and After Effects derive layer names from SVG ``id``
    attributes, so naming every element is what makes a blueprint usable as
    motion-design source rather than a flat picture.
    """

    #: Emit a readable ``id`` on every drawn element, not just layer groups.
    element_ids: bool = True
    #: Wrap each contour's elements in their own ``<g>``, so a contour arrives
    #: as one selectable group (and one precomp-able layer) downstream.
    group_by_contour: bool = True
    #: Prefixed to every generated id. Set this when several blueprints will be
    #: imported into the same document and their ids must not collide.
    id_prefix: str = ""


@dataclass
class Style:
    """The single resolved style object handed to the renderer."""

    canvas: CanvasStyle = field(default_factory=CanvasStyle)
    outline: OutlineStyle = field(default_factory=OutlineStyle)
    handles: HandleStyle = field(default_factory=HandleStyle)
    nodes: NodeStyle = field(default_factory=NodeStyle)
    metrics: MetricsStyle = field(default_factory=MetricsStyle)
    layers: LayerToggles = field(default_factory=LayerToggles)
    export: ExportStyle = field(default_factory=ExportStyle)
    #: Name of the preset this style started from, for provenance in the SVG.
    preset_name: str = "blueprint"

    # -- serialisation -------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return _to_dict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Style":
        style = cls()
        _apply_dict(style, data or {})
        return style

    def merged(self, data: Dict[str, Any]) -> "Style":
        """Return a copy with ``data`` deep-merged over it."""
        merged = Style.from_dict(self.to_dict())
        _apply_dict(merged, data or {})
        return merged

    def set_path(self, dotted: str, value: Any) -> None:
        """Set a leaf by dotted path, e.g. ``handles.point.size``.

        Raises ``KeyError`` for unknown paths and ``ValueError`` for values that
        cannot be coerced to the field's type.
        """
        parts = dotted.split(".")
        target: Any = self
        for part in parts[:-1]:
            if not is_dataclass(target) or not hasattr(target, part):
                raise KeyError(dotted)
            target = getattr(target, part)
        leaf = parts[-1]
        if not is_dataclass(target) or not hasattr(target, leaf):
            raise KeyError(dotted)
        setattr(target, leaf, _coerce(target, leaf, value))

    def get_path(self, dotted: str) -> Any:
        target: Any = self
        for part in dotted.split("."):
            if not hasattr(target, part):
                raise KeyError(dotted)
            target = getattr(target, part)
        return target


# --------------------------------------------------------------------------
# Dict <-> dataclass plumbing
# --------------------------------------------------------------------------


def _to_dict(obj: Any) -> Any:
    if is_dataclass(obj):
        return {f.name: _to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [_to_dict(v) for v in obj]
    return obj


def _apply_dict(target: Any, data: Dict[str, Any]) -> None:
    """Deep-merge ``data`` into a dataclass instance in place."""
    if not isinstance(data, dict):
        raise ValueError("expected a mapping, got {!r}".format(type(data).__name__))
    valid = {f.name for f in fields(target)}
    for key, value in data.items():
        if key not in valid:
            raise KeyError(
                "unknown style key {!r} on {}".format(key, type(target).__name__)
            )
        current = getattr(target, key)
        if is_dataclass(current) and isinstance(value, dict):
            _apply_dict(current, value)
        else:
            setattr(target, key, _coerce(target, key, value))


def _coerce(target: Any, name: str, value: Any) -> Any:
    """Coerce a raw config/CLI value to the declared field type."""
    declared = {f.name: f.type for f in fields(target)}[name]
    text = declared if isinstance(declared, str) else getattr(declared, "__name__", "")

    # "none" is overloaded: on an Optional field it means "unset" (a
    # transparent background), but on a required field it is a real value --
    # "none" is a legitimate marker shape meaning "draw nothing". Only the
    # declared type can tell these apart, which is why the decision lives here
    # rather than in the CLI/config layer that parses the text.
    if (
        isinstance(value, str)
        and value.strip().lower() in ("none", "null")
        and "Optional" in text
    ):
        return None

    if value is None:
        if "Optional" in text:
            return None
        raise ValueError("{} may not be null".format(name))

    if "List[str]" in text:
        if isinstance(value, str):
            return [v.strip() for v in value.split(",") if v.strip()]
        return [str(v) for v in value]

    if "bool" in text:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            if low in ("true", "yes", "on", "1"):
                return True
            if low in ("false", "no", "off", "0"):
                return False
        raise ValueError("{} expects a boolean, got {!r}".format(name, value))

    if "int" in text:
        return int(value)

    if "float" in text:
        return float(value)

    return str(value)


def dotted_paths() -> List[str]:
    """Every settable dotted style path -- used to build CLI help and docs."""
    out: List[str] = []

    def walk(obj: Any, prefix: str) -> None:
        for f in fields(obj):
            value = getattr(obj, f.name)
            path = "{}.{}".format(prefix, f.name) if prefix else f.name
            if is_dataclass(value):
                walk(value, path)
            else:
                out.append(path)

    walk(Style(), "")
    return sorted(out)


__all__ = [
    "SHAPES",
    "DASH_PATTERNS",
    "LAYER_ORDER",
    "METRIC_NAMES",
    "FRAME_MODES",
    "MarkerStyle",
    "LineStyle",
    "OutlineStyle",
    "HandleStyle",
    "NodeStyle",
    "MetricsStyle",
    "CanvasStyle",
    "LayerToggles",
    "ExportStyle",
    "Style",
    "dotted_paths",
]
