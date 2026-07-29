from __future__ import annotations

import math

import pytest

from glyphblueprint import ir
from glyphblueprint.layout import (
    kern_value,
    layout_per_glyph,
    layout_string,
    resolve_glyph_name,
)


def make_font() -> ir.Font:
    widths = {
        ".notdef": 400.0,
        "A": 600.0,
        "B": 500.0,
        "C": 550.0,
        "V": 580.0,
        "a": 480.0,
        "a.alt": 490.0,
        "ampersand": 620.0,
        "f": 300.0,
        "slash": 220.0,
        "space": 250.0,
        "z": 470.0,
    }
    glyphs = {
        name: ir.Glyph(name=name, advance_width=width)
        for name, width in widths.items()
    }
    metrics = ir.Metrics(
        x_height=510.0,
        cap_height=720.0,
        ascender=760.0,
        descender=-240.0,
    )
    cmap = {
        ord("A"): "A",
        ord("B"): "B",
        ord("C"): "C",
        ord("V"): "V",
        ord(" "): "space",
        ord("/"): "slash",
    }
    return ir.Font(
        glyphs=glyphs,
        units_per_em=2048.0,
        metrics=metrics,
        cmap=cmap,
    )


def glyph_names(layout: ir.Layout) -> list:
    return [positioned.glyph.name for positioned in layout.glyphs]


def test_advance_accumulates_with_and_without_tracking() -> None:
    font = make_font()

    plain = layout_string(font, "ABC")
    tracked = layout_string(font, "ABC", tracking=20.0)

    assert [glyph.origin_x for glyph in plain.glyphs] == [0.0, 600.0, 1100.0]
    assert plain.total_advance == 1650.0
    assert [glyph.origin_x for glyph in tracked.glyphs] == [0.0, 620.0, 1140.0]
    assert tracked.total_advance == 1690.0
    assert tracked.units_per_em == font.units_per_em
    assert tracked.metrics is font.metrics
    assert [glyph.source_char for glyph in tracked.glyphs] == ["A", "B", "C"]


def test_flat_pair_kerning_only_affects_the_matching_adjacent_pair() -> None:
    font = make_font()
    font.kerning[("A", "V")] = -80.0

    layout = layout_string(font, "AVA")

    assert [glyph.kern_before for glyph in layout.glyphs] == [0.0, -80.0, 0.0]
    assert [glyph.origin_x for glyph in layout.glyphs] == [0.0, 520.0, 1100.0]
    assert layout.total_advance == 1700.0

    unkerned = layout_string(font, "AV", apply_kerning=False)
    assert [glyph.kern_before for glyph in unkerned.glyphs] == [0.0, 0.0]
    assert [glyph.origin_x for glyph in unkerned.glyphs] == [0.0, 600.0]
    assert unkerned.total_advance == 1180.0


def test_group_kerning_uses_the_full_precedence_ladder() -> None:
    font = make_font()
    left_group = "@MMK_L_A"
    right_group = "@MMK_R_V"
    font.kern_group_left["A"] = left_group
    font.kern_group_right["V"] = right_group
    font.kerning = {
        ("A", "V"): -10.0,
        ("A", right_group): -20.0,
        (left_group, "V"): -30.0,
        (left_group, right_group): -40.0,
    }

    assert kern_value(font, "A", "V") == -10.0
    del font.kerning[("A", "V")]
    assert kern_value(font, "A", "V") == -20.0
    del font.kerning[("A", right_group)]
    assert kern_value(font, "A", "V") == -30.0
    del font.kerning[(left_group, "V")]
    assert kern_value(font, "A", "V") == -40.0
    assert layout_string(font, "AV").glyphs[1].kern_before == -40.0
    del font.kerning[(left_group, right_group)]
    assert kern_value(font, "A", "V") == 0.0


def test_explicit_zero_pair_cancels_group_kerning() -> None:
    font = make_font()
    left_group = "@MMK_L_A"
    right_group = "@MMK_R_V"
    font.kern_group_left["A"] = left_group
    font.kern_group_right["V"] = right_group
    font.kerning = {
        ("A", "V"): 0.0,
        (left_group, right_group): -120.0,
    }

    assert kern_value(font, "A", "V") == 0.0
    layout = layout_string(font, "AV")
    assert layout.glyphs[1].kern_before == 0.0
    assert layout.glyphs[1].origin_x == 600.0


def test_resolves_cmap_then_falls_back_to_character_glyph_name() -> None:
    font = make_font()
    font.glyphs["-"] = ir.Glyph(name="-", advance_width=200.0)
    font.cmap[ord("-")] = "absent-cmap-target"

    assert resolve_glyph_name(font, "A") == "A"
    assert resolve_glyph_name(font, "-") == "-"
    assert resolve_glyph_name(font, "?") is None


def test_slash_escaped_names_literal_slash_and_mixed_input() -> None:
    font = make_font()
    font.glyphs["a-alt"] = ir.Glyph(name="a-alt", advance_width=495.0)

    mixed = layout_string(font, "A/ampersand B//")
    chained = layout_string(font, "/a.alt/a/f/z")
    punctuation = layout_string(font, "///a.alt/a-alt//")

    assert glyph_names(mixed) == ["A", "ampersand", "space", "B", "slash"]
    assert [glyph.source_char for glyph in mixed.glyphs] == [
        "A",
        "/ampersand",
        " ",
        "B",
        "/",
    ]
    assert glyph_names(chained) == ["a.alt", "a", "f", "z"]
    assert [glyph.source_char for glyph in chained.glyphs] == [
        "/a.alt",
        "/a",
        "/f",
        "/z",
    ]
    assert glyph_names(punctuation) == ["slash", "a.alt", "a-alt", "slash"]


@pytest.mark.parametrize("text", ["/", "/a.alt/"])
def test_incomplete_trailing_slash_escape_is_never_silently_skipped(
    text: str,
) -> None:
    font = make_font()

    for missing in ("error", "skip", "notdef"):
        with pytest.raises(ValueError) as error:
            layout_string(font, text, missing=missing)
        assert "has no name" in str(error.value)
        assert "'//' for a literal slash" in str(error.value)


def test_missing_glyph_policies() -> None:
    font = make_font()

    with pytest.raises(ValueError) as exc_info:
        layout_string(font, "A?B")
    assert "'?'" in str(exc_info.value)
    assert "U+003F" in str(exc_info.value)

    skipped = layout_string(font, "A?B", missing="skip")
    assert glyph_names(skipped) == ["A", "B"]
    assert [glyph.origin_x for glyph in skipped.glyphs] == [0.0, 600.0]
    assert skipped.total_advance == 1100.0

    substituted = layout_string(font, "A?B", missing="notdef")
    assert glyph_names(substituted) == ["A", ".notdef", "B"]
    assert [glyph.source_char for glyph in substituted.glyphs] == ["A", "?", "B"]
    assert substituted.total_advance == 1500.0

    del font.glyphs[".notdef"]
    no_notdef = layout_string(font, "A?B", missing="notdef")
    assert glyph_names(no_notdef) == ["A", "B"]


def test_layout_per_glyph_returns_independent_single_glyph_layouts() -> None:
    font = make_font()
    font.kerning[("A", "V")] = -80.0

    layouts = layout_per_glyph(font, "AV", tracking=25.0)

    assert len(layouts) == 2
    assert layouts[0] is not layouts[1]
    assert layouts[0].glyphs is not layouts[1].glyphs
    assert [glyph_names(layout) for layout in layouts] == [["A"], ["V"]]
    assert [layout.glyphs[0].origin_x for layout in layouts] == [0.0, 0.0]
    assert [layout.glyphs[0].kern_before for layout in layouts] == [0.0, 0.0]
    assert [layout.total_advance for layout in layouts] == [600.0, 580.0]
    assert [layout.glyphs[0].source_char for layout in layouts] == ["A", "V"]


@pytest.mark.parametrize("tracking", [math.nan, math.inf, -math.inf])
def test_nonfinite_tracking_is_rejected(tracking: float) -> None:
    with pytest.raises(ValueError, match="tracking must be a finite number"):
        layout_string(make_font(), "AB", tracking=tracking)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_width_and_kerning_are_rejected(value: float) -> None:
    font = make_font()
    font.glyphs["A"].advance_width = value
    with pytest.raises(ValueError, match="advance width for glyph 'A'"):
        layout_string(font, "A")

    font = make_font()
    font.kerning[("A", "V")] = value
    with pytest.raises(ValueError, match="kerning value for pair"):
        layout_string(font, "AV")


def test_finite_inputs_that_overflow_the_layout_are_rejected() -> None:
    font = make_font()
    font.glyphs["A"].advance_width = 1e308
    font.glyphs["B"].advance_width = 1e308

    with pytest.raises(ValueError, match="layout advance became non-finite"):
        layout_string(font, "AB")
