"""Public package surface for Type Design X-Ray."""

from __future__ import annotations

__version__ = "1.0.0"

from . import ir as ir
from .ir import (
    Contour,
    Font,
    Glyph,
    LayerInfo,
    Layout,
    Metrics,
    Node,
    PositionedGlyph,
)
from .style import Style
from .api import blueprint, blueprint_to_files, load_font


__all__ = [
    "__version__",
    "blueprint",
    "blueprint_to_files",
    "load_font",
    "Style",
    "Node",
    "Contour",
    "Metrics",
    "Glyph",
    "PositionedGlyph",
    "Layout",
    "Font",
    "LayerInfo",
]
