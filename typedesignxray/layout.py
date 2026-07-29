"""Parser-agnostic glyph resolution and horizontal layout."""

from __future__ import annotations

import math
from typing import Any, List, Optional, Tuple

from . import ir


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            "{} must be a finite number, got {!r}".format(label, value)
        ) from exc
    if not math.isfinite(number):
        raise ValueError(
            "{} must be a finite number, got {!r}".format(label, value)
        )
    return number


def _finite_sum(left: float, right: float, label: str) -> float:
    result = left + right
    if not math.isfinite(result):
        raise ValueError(
            "layout advance became non-finite while applying {}".format(label)
        )
    return result


def resolve_glyph_name(font: ir.Font, char: str) -> Optional[str]:
    """Resolve one character while tolerating fonts without a complete cmap."""
    if len(char) != 1:
        raise ValueError("expected one character, got {!r}".format(char))

    mapped_name = font.cmap.get(ord(char))
    if mapped_name is not None and mapped_name in font.glyphs:
        return mapped_name
    if char in font.glyphs:
        return char
    return None


def kern_value(font: ir.Font, left_name: str, right_name: str) -> float:
    """Return the most specific kern, including explicit zero overrides."""
    left_group = font.kern_group_left.get(left_name)
    right_group = font.kern_group_right.get(right_name)

    pairs = [(left_name, right_name)]
    if right_group is not None:
        pairs.append((left_name, right_group))
    if left_group is not None:
        pairs.append((left_group, right_name))
    if left_group is not None and right_group is not None:
        pairs.append((left_group, right_group))

    for pair in pairs:
        if pair in font.kerning:
            return _finite_number(
                font.kerning[pair],
                "kerning value for pair {!r}".format(pair),
            )
    return 0.0


def layout_string(
    font: ir.Font,
    text: str,
    *,
    tracking: float = 0.0,
    apply_kerning: bool = True,
    missing: str = "error",
) -> ir.Layout:
    """Lay out a run without coupling positioning to any source parser."""
    tracking_value = _finite_number(tracking, "tracking")
    units_per_em = _finite_number(font.units_per_em, "units per em")
    resolved = _resolve_text(font, text, missing)
    positioned: List[ir.PositionedGlyph] = []
    pen_x = 0.0
    previous_name: Optional[str] = None

    for index, (glyph_name, source_char) in enumerate(resolved):
        before = 0.0
        if apply_kerning and previous_name is not None:
            before = kern_value(font, previous_name, glyph_name)
        pen_x = _finite_sum(
            pen_x,
            before,
            "kerning before glyph {!r}".format(glyph_name),
        )

        glyph = font.glyphs[glyph_name]
        advance_width = _finite_number(
            glyph.advance_width,
            "advance width for glyph {!r}".format(glyph_name),
        )
        positioned.append(
            ir.PositionedGlyph(
                glyph=glyph,
                origin_x=pen_x,
                kern_before=before,
                source_char=source_char,
            )
        )
        pen_x = _finite_sum(
            pen_x,
            advance_width,
            "advance width for glyph {!r}".format(glyph_name),
        )
        if index < len(resolved) - 1:
            pen_x = _finite_sum(pen_x, tracking_value, "tracking")
        previous_name = glyph_name

    return ir.Layout(
        glyphs=positioned,
        units_per_em=units_per_em,
        metrics=font.metrics,
        total_advance=pen_x,
    )


def layout_per_glyph(font: ir.Font, text: str, **kwargs: object) -> List[ir.Layout]:
    """Split a resolved run into layouts that can be rendered independently."""
    run = layout_string(font, text, **kwargs)
    layouts: List[ir.Layout] = []
    for positioned in run.glyphs:
        single = ir.PositionedGlyph(
            glyph=positioned.glyph,
            origin_x=0.0,
            kern_before=0.0,
            source_char=positioned.source_char,
        )
        layouts.append(
            ir.Layout(
                glyphs=[single],
                units_per_em=run.units_per_em,
                metrics=run.metrics,
                total_advance=_finite_number(
                    positioned.glyph.advance_width,
                    "advance width for glyph {!r}".format(
                        positioned.glyph.name
                    ),
                ),
            )
        )
    return layouts


def _resolve_text(
    font: ir.Font, text: str, missing: str
) -> List[Tuple[str, str]]:
    if missing not in ("error", "skip", "notdef"):
        raise ValueError("unknown missing-glyph policy {!r}".format(missing))

    resolved: List[Tuple[str, str]] = []
    for source_char, explicit_name in _input_tokens(text):
        if explicit_name is None:
            glyph_name = resolve_glyph_name(font, source_char)
        elif explicit_name in font.glyphs:
            glyph_name = explicit_name
        else:
            glyph_name = None

        if glyph_name is None:
            if missing == "error":
                raise ValueError(_missing_glyph_message(source_char, explicit_name))
            if missing == "notdef" and ".notdef" in font.glyphs:
                glyph_name = ".notdef"
            else:
                continue

        resolved.append((glyph_name, source_char))
    return resolved


def _input_tokens(text: str) -> List[Tuple[str, Optional[str]]]:
    """Keep source spelling alongside names so diagnostics survive escapes."""
    tokens: List[Tuple[str, Optional[str]]] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "/":
            tokens.append((char, None))
            index += 1
            continue

        if index + 1 < len(text) and text[index + 1] == "/":
            tokens.append(("/", None))
            index += 2
            continue

        start = index
        index += 1
        name_start = index
        while (
            index < len(text)
            and text[index] != "/"
            and not text[index].isspace()
        ):
            index += 1
        if name_start == index:
            raise ValueError(
                "glyph-name escape at index {} has no name; "
                "use '//' for a literal slash".format(start)
            )
        tokens.append((text[start:index], text[name_start:index]))

    return tokens


def _missing_glyph_message(
    source_char: str, explicit_name: Optional[str]
) -> str:
    if explicit_name is not None:
        return "missing glyph name {!r} from escape {!r}".format(
            explicit_name, source_char
        )
    return "missing glyph for character {!r} (U+{:04X})".format(
        source_char, ord(source_char)
    )


__all__ = [
    "layout_string",
    "resolve_glyph_name",
    "kern_value",
    "layout_per_glyph",
]
