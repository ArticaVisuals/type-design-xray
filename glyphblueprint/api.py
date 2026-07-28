"""Small, composable Python API for blueprint rendering and export."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import ir
from .config import resolve_style
from .layout import layout_string
from .parsers import load_font
from .render.svg import render_svg
from .style import Style


_OUTPUT_FORMATS = ("svg", "png", "pdf")


def _prepare_blueprint(
    font_path: Any,
    text: str,
    *,
    layer: Optional[str] = None,
    master: Optional[str] = None,
    compound: bool = False,
    preset: Optional[str] = None,
    config: Any = None,
    overrides: Any = None,
    tracking: float = 0.0,
    apply_kerning: bool = True,
    missing: str = "error",
    title: Optional[str] = None
) -> Tuple[ir.Font, ir.Layout, Style, str]:
    font = load_font(font_path, layer=layer, master=master)
    if compound:
        from .compound import compound_font

        font = compound_font(font)
    layout = layout_string(
        font,
        text,
        tracking=tracking,
        apply_kerning=apply_kerning,
        missing=missing,
    )
    resolved_style = resolve_style(
        preset=preset,
        config_path=config,
        overrides=overrides,
    )
    svg = render_svg(layout, resolved_style, title=title)
    return font, layout, resolved_style, svg


def blueprint(
    font_path: Any,
    text: str,
    *,
    layer: Optional[str] = None,
    master: Optional[str] = None,
    compound: bool = False,
    preset: Optional[str] = None,
    config: Any = None,
    overrides: Any = None,
    tracking: float = 0.0,
    apply_kerning: bool = True,
    missing: str = "error",
    title: Optional[str] = None
) -> str:
    """Render text from a supported font source and return its SVG string."""
    return _prepare_blueprint(
        font_path,
        text,
        layer=layer,
        master=master,
        compound=compound,
        preset=preset,
        config=config,
        overrides=overrides,
        tracking=tracking,
        apply_kerning=apply_kerning,
        missing=missing,
        title=title,
    )[3]


def _normalise_formats(formats: Iterable[str]) -> Tuple[str, ...]:
    if isinstance(formats, str):
        raw_formats = formats.split(",")
    else:
        raw_formats = list(formats)

    normalised: List[str] = []
    for raw_format in raw_formats:
        format_name = str(raw_format).strip().lower().lstrip(".")
        if not format_name:
            continue
        if format_name not in _OUTPUT_FORMATS:
            raise ValueError(
                "unsupported output format {!r}; choose from {}".format(
                    raw_format, ", ".join(_OUTPUT_FORMATS)
                )
            )
        if format_name not in normalised:
            normalised.append(format_name)
    if not normalised:
        raise ValueError("at least one output format is required")
    return tuple(normalised)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(" ._-")
    if cleaned:
        return cleaned[:80]
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return "glyph-{}".format(digest)


def _single_layout(
    positioned: ir.PositionedGlyph, source: ir.Layout
) -> ir.Layout:
    glyph = positioned.glyph
    return ir.Layout(
        glyphs=[
            ir.PositionedGlyph(
                glyph=glyph,
                origin_x=0.0,
                origin_y=0.0,
                kern_before=0.0,
                source_char=positioned.source_char,
            )
        ],
        units_per_em=source.units_per_em,
        metrics=source.metrics,
        total_advance=glyph.advance_width,
    )


def _render_documents(
    layout: ir.Layout,
    resolved_style: Style,
    text: str,
    title: Optional[str],
    full_svg: str,
    per_glyph: bool,
) -> List[Tuple[str, str]]:
    documents = [(_safe_filename(text), full_svg)]
    if not per_glyph:
        return documents

    seen: Dict[str, int] = {}
    for index, positioned in enumerate(layout.glyphs, 1):
        glyph_label = _safe_filename(positioned.glyph.name)
        seen[glyph_label] = seen.get(glyph_label, 0) + 1
        repeat = seen[glyph_label]
        if repeat > 1:
            glyph_label = "{}-{}".format(glyph_label, repeat)
        label = "{}-{:02d}-{}".format(documents[0][0], index, glyph_label)
        glyph_layout = _single_layout(positioned, layout)
        glyph_title = title
        if glyph_title is not None:
            glyph_title = "{} — {}".format(glyph_title, positioned.glyph.name)
        documents.append(
            (label, render_svg(glyph_layout, resolved_style, title=glyph_title))
        )
    return documents


def _directory_target(
    out: Any, format_names: Sequence[str], per_glyph: bool
) -> bool:
    raw_path = os.fspath(out)
    raw_text = os.fsdecode(raw_path)
    trailing_separator = raw_text.endswith(("/", "\\"))
    return (
        Path(raw_path).is_dir()
        or trailing_separator
        or (per_glyph and len(format_names) > 1)
    )


def _replace_output_suffix(path: Path, format_name: str) -> Path:
    if path.suffix.lower() in (".svg", ".png", ".pdf"):
        return path.with_suffix(".{}".format(format_name))
    return Path("{}.{!s}".format(path, format_name))


def _output_plan(
    out: Any,
    format_names: Sequence[str],
    documents: Sequence[Tuple[str, str]],
    per_glyph: bool,
) -> List[Tuple[str, str, Path]]:
    destination = Path(out)
    plan: List[Tuple[str, str, Path]] = []
    if _directory_target(out, format_names, per_glyph):
        for label, svg in documents:
            for format_name in format_names:
                plan.append(
                    (
                        format_name,
                        svg,
                        destination / "{}.{}".format(label, format_name),
                    )
                )
        return plan

    first_format = format_names[0]
    first_suffix = destination.suffix.lower()
    if first_suffix in (".svg", ".png", ".pdf"):
        full_first = destination.with_suffix(".{}".format(first_format))
    else:
        full_first = destination

    for document_index, (label, svg) in enumerate(documents):
        for format_name in format_names:
            if document_index == 0 and format_name == first_format:
                path = full_first
            elif document_index == 0:
                path = _replace_output_suffix(full_first, format_name)
            else:
                base = full_first
                if base.suffix.lower() in (".svg", ".png", ".pdf"):
                    base = base.with_suffix("")
                path = Path(
                    "{}-{}.{}".format(base, label.split("-", 1)[-1], format_name)
                )
            plan.append((format_name, svg, path))
    return plan


def _attach_written_paths(exc: Exception, paths: Sequence[Path]) -> None:
    try:
        setattr(exc, "written_paths", list(paths))
    except Exception:
        pass


def blueprint_to_files(
    font_path: Any,
    text: str,
    out: Any,
    *,
    formats: Sequence[str] = ("svg",),
    png_width: Optional[int] = None,
    per_glyph: bool = False,
    compound: bool = False,
    **kw: Any
) -> List[Path]:
    """Render a blueprint and write its requested full-run and glyph files."""
    format_names = _normalise_formats(formats)
    _, layout, resolved_style, full_svg = _prepare_blueprint(
        font_path, text, compound=compound, **kw
    )
    title = kw.get("title")
    documents = _render_documents(
        layout,
        resolved_style,
        text,
        title,
        full_svg,
        per_glyph,
    )
    plan = _output_plan(out, format_names, documents, per_glyph)
    written: List[Path] = []

    for format_name, svg, path in plan:
        if format_name != "svg":
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg, encoding="utf-8")
        written.append(path)

    raster_width = png_width
    if raster_width is None:
        raster_width = (
            resolved_style.canvas.png_width
            if resolved_style.canvas.png_width is not None
            else resolved_style.canvas.width
        )

    try:
        for format_name, svg, path in plan:
            if format_name == "svg":
                continue
            from .render import raster

            if format_name == "png":
                raster.svg_to_png(svg, path, width=raster_width)
            else:
                raster.svg_to_pdf(svg, path)
            written.append(path)
    except Exception as exc:
        _attach_written_paths(exc, written)
        raise

    written_set = set(written)
    return [path for _, _, path in plan if path in written_set]


__all__ = ["blueprint", "blueprint_to_files", "load_font"]
