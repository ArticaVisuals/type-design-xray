from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Tuple

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from glyphblueprint import ir
from glyphblueprint.parsers import binary
from glyphblueprint.parsers.binary import parse_binary


REAL_FONT = Path(
    "/Users/micahhoang/My Drive/Font Design/CaliperSans04/"
    "CaliperSans-Regular.otf"
)


@pytest.fixture(scope="module")
def real_font() -> ir.Font:
    if not REAL_FONT.is_file():
        pytest.skip("CaliperSans-Regular.otf is not available")
    return parse_binary(REAL_FONT)


def _assert_point(point: ir.Point) -> None:
    assert len(point) == 2
    assert all(math.isfinite(value) for value in point)


def _assert_ir_invariants(font: ir.Font) -> None:
    for glyph in font.glyphs.values():
        assert glyph.units_per_em == font.units_per_em
        assert glyph.node_types_exact is False
        for contour in glyph.contours:
            for node in contour.nodes:
                _assert_point(node.point)
                assert node.type in (ir.SEGMENT_LINE, ir.SEGMENT_CURVE)
                if node.handle_in is not None:
                    _assert_point(node.handle_in)
                if node.handle_out is not None:
                    _assert_point(node.handle_out)
            for start, end in contour.segments():
                expected_type = (
                    ir.SEGMENT_CURVE
                    if start.handle_out is not None or end.handle_in is not None
                    else ir.SEGMENT_LINE
                )
                assert end.type == expected_type


def test_real_font_outlines_metrics_and_invariants(real_font: ir.Font) -> None:
    assert 16 <= real_font.units_per_em <= 16384
    assert real_font.source_format == "otf"
    assert real_font.node_types_exact is False

    for character in "afz":
        glyph_name = real_font.cmap[ord(character)]
        glyph = real_font.glyphs[glyph_name]
        assert glyph.contours
        assert all(contour.nodes and contour.closed for contour in glyph.contours)

    _assert_ir_invariants(real_font)


def test_real_font_smooth_inference(real_font: ir.Font) -> None:
    glyph_name = real_font.cmap.get(ord("o"))
    if glyph_name is None:
        pytest.skip("the real font has no lowercase o")
    glyph = real_font.glyphs[glyph_name]
    assert glyph.node_types_exact is False
    assert any(
        node.smooth
        for contour in glyph.contours
        for node in contour.nodes
    )


def _make_synthetic_ttf(path: Path) -> None:
    notdef_pen = TTGlyphPen(None)
    notdef = notdef_pen.glyph()

    round_pen = TTGlyphPen(None)
    round_pen.moveTo((0, 0))
    round_pen.qCurveTo((100, 200), (200, 200), (300, 0))
    round_pen.closePath()
    round_pen.qCurveTo((400, 0), (500, 200), (600, 0), None)
    round_pen.closePath()
    round_glyph = round_pen.glyph()

    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "o"])
    builder.setupCharacterMap({ord("o"): "o"})
    builder.setupGlyf({".notdef": notdef, "o": round_glyph})
    builder.setupHorizontalMetrics({".notdef": (600, 0), "o": (700, 0)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": "Synthetic Quadratic",
            "styleName": "Regular",
            "uniqueFontIdentifier": "Synthetic Quadratic Regular",
            "fullName": "Synthetic Quadratic Regular",
            "psName": "SyntheticQuadratic-Regular",
        }
    )
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
        sxHeight=500,
        sCapHeight=700,
    )
    builder.setupPost()
    builder.setupMaxp()
    builder.save(path)


def _quadratic_point(
    start: ir.Point,
    control: ir.Point,
    end: ir.Point,
    t: float,
) -> ir.Point:
    one_minus_t = 1.0 - t
    return (
        one_minus_t * one_minus_t * start[0]
        + 2.0 * one_minus_t * t * control[0]
        + t * t * end[0],
        one_minus_t * one_minus_t * start[1]
        + 2.0 * one_minus_t * t * control[1]
        + t * t * end[1],
    )


def _cubic_point(
    start: ir.Point,
    control1: ir.Point,
    control2: ir.Point,
    end: ir.Point,
    t: float,
) -> ir.Point:
    one_minus_t = 1.0 - t
    return (
        one_minus_t ** 3 * start[0]
        + 3.0 * one_minus_t ** 2 * t * control1[0]
        + 3.0 * one_minus_t * t * t * control2[0]
        + t ** 3 * end[0],
        one_minus_t ** 3 * start[1]
        + 3.0 * one_minus_t ** 2 * t * control1[1]
        + 3.0 * one_minus_t * t * t * control2[1]
        + t ** 3 * end[1],
    )


def _assert_cubics_match_quadratics(
    contour: ir.Contour,
    quadratics: Iterable[Tuple[ir.Point, ir.Point, ir.Point]],
) -> None:
    cubic_segments = [
        (start, end)
        for start, end in contour.segments()
        if end.type == ir.SEGMENT_CURVE
    ]
    quadratics = list(quadratics)
    assert len(cubic_segments) == len(quadratics)

    for (start, end), quadratic in zip(cubic_segments, quadratics):
        assert start.handle_out is not None
        assert end.handle_in is not None
        for t in (0.0, 0.125, 0.5, 0.875, 1.0):
            source = _quadratic_point(*quadratic, t)
            converted = _cubic_point(
                start.point,
                start.handle_out,
                end.handle_in,
                end.point,
                t,
            )
            assert converted == pytest.approx(source, abs=1e-9)


def test_ttf_quadratics_are_exact_cubics(tmp_path: Path) -> None:
    path = tmp_path / "quadratics.ttf"
    _make_synthetic_ttf(path)

    font = parse_binary(path, glyph_names=["o"])
    glyph = font.glyphs["o"]
    assert font.source_format == "ttf"
    assert font.node_types_exact is False
    assert glyph.node_types_exact is False
    assert len(glyph.contours) == 2
    assert all(contour.closed for contour in glyph.contours)

    implied_midpoint = glyph.contours[0]
    assert [node.point for node in implied_midpoint.nodes] == [
        (0.0, 0.0),
        (150.0, 200.0),
        (300.0, 0.0),
    ]
    _assert_cubics_match_quadratics(
        implied_midpoint,
        [
            ((0.0, 0.0), (100.0, 200.0), (150.0, 200.0)),
            ((150.0, 200.0), (200.0, 200.0), (300.0, 0.0)),
        ],
    )

    all_off_curve = glyph.contours[1]
    assert [node.point for node in all_off_curve.nodes] == [
        (500.0, 0.0),
        (450.0, 100.0),
        (550.0, 100.0),
    ]
    _assert_cubics_match_quadratics(
        all_off_curve,
        [
            ((500.0, 0.0), (400.0, 0.0), (450.0, 100.0)),
            ((450.0, 100.0), (500.0, 200.0), (550.0, 100.0)),
            ((550.0, 100.0), (600.0, 0.0), (500.0, 0.0)),
        ],
    )
    _assert_ir_invariants(font)


def test_binary_layer_selection_is_friendly(tmp_path: Path) -> None:
    path = tmp_path / "quadratics.ttf"
    _make_synthetic_ttf(path)
    with pytest.raises(ValueError, match=r"\.glyphs.*\.ufo"):
        parse_binary(path, layer="foreground")


def test_missing_hmtx_entry_uses_safe_metrics_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "missing-hmtx.ttf"
    _make_synthetic_ttf(path)
    ttfont = binary.TTFont

    def open_with_missing_metric(
        source: object, lazy: bool = True
    ) -> object:
        font = ttfont(source, lazy=lazy)
        del font["hmtx"].metrics["o"]
        return font

    monkeypatch.setattr(binary, "TTFont", open_with_missing_metric)
    glyph = parse_binary(path, glyph_names=["o"]).glyphs["o"]

    assert glyph.advance_width == 0.0
    assert glyph.metrics.lsb == 0.0
    assert glyph.contours
