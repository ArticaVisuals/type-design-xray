"""Format-aware font parser dispatch."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, List, Optional

from typedesignxray import ir

from .binary import parse_binary
from .glyphs import list_layers, list_masters, parse_glyphs


_GLYPHS_EXTENSIONS = (".glyphs", ".glyphspackage")
_BINARY_EXTENSIONS = (".otf", ".ttf", ".woff", ".woff2")
_UFO_EXTENSION = ".ufo"
_SUPPORTED_EXTENSIONS = _GLYPHS_EXTENSIONS + _BINARY_EXTENSIONS + (
    _UFO_EXTENSION,
)


def _extension(path: Any) -> str:
    return Path(os.fspath(path)).suffix.lower()


def _unsupported(path: Any) -> ValueError:
    extension = _extension(path) or "(none)"
    return ValueError(
        "unsupported font extension {!r} for {!s}; supported extensions: {}".format(
            extension,
            path,
            ", ".join(_SUPPORTED_EXTENSIONS),
        )
    )


def _ufo_module() -> Any:
    try:
        return importlib.import_module(".ufo", __name__)
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "UFO support is unavailable because typedesignxray.parsers.ufo "
            "is not installed."
        ) from exc


def load_font(
    path: Any,
    layer: Optional[str] = None,
    master: Optional[str] = None,
    **kw: Any
) -> ir.Font:
    """Load a font using the parser implied by its filename extension."""
    extension = _extension(path)
    if extension in _GLYPHS_EXTENSIONS:
        return parse_glyphs(path, layer=layer, master=master, **kw)
    if extension in _BINARY_EXTENSIONS:
        return parse_binary(path, layer=layer, master=master, **kw)
    if extension == _UFO_EXTENSION:
        parser = getattr(_ufo_module(), "parse_ufo", None)
        if parser is None:
            raise RuntimeError(
                "UFO support is unavailable: typedesignxray.parsers.ufo "
                "does not export parse_ufo()."
            )
        return parser(path, layer=layer, master=master, **kw)
    raise _unsupported(path)


def list_font_layers(path: Any, glyph_name: str) -> List[ir.LayerInfo]:
    """List source layers when the selected font format can contain them."""
    extension = _extension(path)
    if extension in _GLYPHS_EXTENSIONS:
        return list_layers(path, glyph_name)
    if extension in _BINARY_EXTENSIONS:
        raise ValueError(
            "{} fonts contain a single compiled outline and do not expose "
            "selectable layers".format(extension)
        )
    if extension == _UFO_EXTENSION:
        lister = getattr(_ufo_module(), "list_layers", None)
        if lister is None:
            raise RuntimeError(
                "UFO layer listing is unavailable: typedesignxray.parsers.ufo "
                "does not export list_layers()."
            )
        return lister(path, glyph_name)
    raise _unsupported(path)


def list_font_masters(path: Any) -> List[ir.MasterInfo]:
    """List selectable Glyphs masters/styles for a supported font source."""
    extension = _extension(path)
    if extension in _GLYPHS_EXTENSIONS:
        return list_masters(path)
    if extension in _BINARY_EXTENSIONS or extension == _UFO_EXTENSION:
        return []
    raise _unsupported(path)


__all__ = [
    "load_font",
    "list_font_layers",
    "list_font_masters",
    "parse_binary",
    "parse_glyphs",
    "list_layers",
    "list_masters",
]
