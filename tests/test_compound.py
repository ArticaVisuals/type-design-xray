from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Tuple

import pytest

from glyphblueprint import ir
from glyphblueprint.compound import (
    _contour_from_path,
    _expand_quadratics,
    _matched_smooth,
    compound_font,
    compound_glyph,
    is_available,
)
from glyphblueprint.parsers.binary import parse_binary
from glyphblueprint.parsers.glyphs import parse_glyphs


pytestmark = pytest.mark.skipif(
    not is_available(), reason="skia-pathops is not installed"
)

from _real_fonts import REAL_GLYPHS as REAL_SOURCE, REAL_OTF  # noqa: E402


def _contour(
    points: Iterable[ir.Point],
    *,
    closed: bool = True,
    smooth: bool = False,
) -> ir.Contour:
    return ir.Contour(
        nodes=[
            ir.Node(point=point, smooth=smooth)
            for point in points
        ],
        closed=closed,
    )


def _glyph(*contours: ir.Contour, name: str = "test") -> ir.Glyph:
    return ir.Glyph(
        name=name,
        advance_width=500,
        contours=list(contours),
    )


def _anchor_bounds(contours: Iterable[ir.Contour]) -> Tuple[float, ...]:
    points = [
        node.point
        for contour in contours
        for node in contour.nodes
    ]
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _signed_area(contour: ir.Contour) -> float:
    points = [node.point for node in contour.nodes]
    return 0.5 * sum(
        start[0] * end[1] - end[0] * start[1]
        for start, end in zip(points, points[1:] + points[:1])
    )


def test_overlapping_squares_merge_with_union_bounds() -> None:
    left = _contour([(0, 0), (100, 0), (100, 100), (0, 100)])
    right = _contour([(50, 0), (150, 0), (150, 100), (50, 100)])

    result = compound_glyph(_glyph(left, right))

    assert len(result.contours) == 1
    assert result.contours[0].closed is True
    assert _anchor_bounds(result.contours) == (0, 0, 150, 100)


def test_counter_survives_with_opposing_winding() -> None:
    outer = _contour([(0, 0), (200, 0), (200, 200), (0, 200)])
    inner = _contour([(50, 50), (50, 150), (150, 150), (150, 50)])

    result = compound_glyph(_glyph(outer, inner))

    assert len(result.contours) == 2
    areas = [_signed_area(contour) for contour in result.contours]
    assert areas[0] * areas[1] < 0


def test_open_contour_is_untouched_while_closed_contours_merge() -> None:
    left = _contour([(0, 0), (100, 0), (100, 100), (0, 100)])
    open_contour = ir.Contour(
        nodes=[
            ir.Node(
                point=(10, 300),
                smooth=True,
                handle_out=(25, 325),
            ),
            ir.Node(
                point=(50, 350),
                type=ir.SEGMENT_CURVE,
                smooth=True,
                handle_in=(35, 340),
            ),
        ],
        closed=False,
    )
    right = _contour([(50, 0), (150, 0), (150, 100), (50, 100)])

    result = compound_glyph(_glyph(left, open_contour, right))

    assert len([contour for contour in result.contours if contour.closed]) == 1
    result_open = next(
        contour for contour in result.contours if not contour.closed
    )
    assert result_open is open_contour
    assert result_open == open_contour
    assert [node.point for node in result_open.nodes] == [
        (10, 300),
        (50, 350),
    ]


def test_authored_smooth_flags_survive_at_unchanged_coordinates() -> None:
    contour = _contour(
        [(0, 0), (100, 0), (100, 100), (0, 100)]
    )
    contour.nodes[0].smooth = True
    contour.nodes[2].smooth = True
    expected: Dict[ir.Point, bool] = {
        node.point: node.smooth for node in contour.nodes
    }

    result = compound_glyph(_glyph(contour))
    actual = {
        node.point: node.smooth
        for node in result.contours[0].nodes
    }

    assert actual == expected
    assert result.node_types_exact is True


def test_glyph_with_only_open_contours_is_returned_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import glyphblueprint.compound as compound

    open_contour = _contour(
        [(0, 0), (50, 75), (100, 0)],
        closed=False,
        smooth=True,
    )
    glyph = _glyph(open_contour)
    monkeypatch.setattr(
        compound,
        "_pathops_module",
        lambda: pytest.fail("pathops must not be touched"),
    )

    result = compound.compound_glyph(glyph)

    assert result == glyph
    assert result is not glyph
    assert result.contours[0] is open_contour


def test_single_node_open_contour_passes_through_exactly() -> None:
    point = (468.0, 542.0)
    open_contour = _contour([point], closed=False, smooth=True)

    result = compound_glyph(_glyph(open_contour, name="y"))

    assert result.contours == [open_contour]
    assert result.contours[0] is open_contour
    assert result.contours[0].nodes[0].point == point


def test_nearby_smooth_flags_are_not_guessed_when_match_is_ambiguous() -> None:
    left_first = {(0.0, 0.0): False, (0.01, 0.0): True}
    right_first = dict(reversed(list(left_first.items())))

    assert _matched_smooth((0.005, 0.0), left_first) is None
    assert _matched_smooth((0.005, 0.0), right_first) is None
    assert _matched_smooth((0.001, 0.0), left_first) is False
    assert _matched_smooth((0.009, 0.0), left_first) is True


def test_compound_font_preserves_layout_and_font_metadata() -> None:
    empty = _glyph(name="space")
    font = ir.Font(
        glyphs={"space": empty},
        units_per_em=2048.0,
        metrics=ir.Metrics(ascender=900.0, descender=-250.0),
        cmap={ord(" "): "space"},
        kerning={("space", "space"): -10.0},
        kern_group_left={"space": "@MMK_L_space"},
        kern_group_right={"space": "@MMK_R_space"},
        family_name="Test Family",
        master_name="Regular",
        source_format="glyphs",
    )

    result = compound_font(font)

    assert result.glyphs["space"].advance_width == empty.advance_width
    assert result.units_per_em == font.units_per_em
    assert result.metrics is font.metrics
    assert result.cmap is font.cmap
    assert result.kerning is font.kerning
    assert result.kern_group_left is font.kern_group_left
    assert result.kern_group_right is font.kern_group_right
    assert result.family_name == font.family_name
    assert result.master_name == font.master_name
    assert result.source_format == font.source_format


def test_real_source_f_matches_exported_otf() -> None:
    if not REAL_SOURCE.is_file() or not REAL_OTF.is_file():
        pytest.skip("no real font configured")

    source_font = parse_glyphs(REAL_SOURCE)
    source_f = source_font.glyph_for_char("f")
    assert source_f is not None
    exported_font = parse_binary(REAL_OTF, glyph_names=["f"])
    exported_f = exported_font.glyph_for_char("f")
    assert exported_f is not None

    result = compound_glyph(source_f)

    # The exported OTF has already had overlap removed by the compiler, so it
    # is independent ground truth: compounding the source must agree with it.
    assert len(source_f.contours) >= len(exported_f.contours)
    assert len(result.contours) == len(exported_f.contours)


def test_quadratic_segments_from_skia_are_upconverted_to_cubics():
    """skia represents conics as quadratics; the IR is single-curve-type.

    Regression: compounding the real font crashed on glyph 'j' because
    ``qCurveTo`` was rejected outright rather than converted.
    """
    from glyphblueprint.compound import _expand_quadratics

    start = (0.0, 0.0)
    control = (30.0, 60.0)
    end = (60.0, 0.0)
    segments = [("moveTo", (start,)), ("qCurveTo", (control, end)), ("closePath", ())]
    expanded = _expand_quadratics(segments)

    assert [op for op, _ in expanded] == ["moveTo", "curveTo", "closePath"]
    c1, c2, got_end = expanded[1][1]
    assert got_end == end

    def quad(t):
        mt = 1 - t
        return tuple(
            mt * mt * s + 2 * mt * t * c + t * t * e
            for s, c, e in zip(start, control, end)
        )

    def cubic(t):
        mt = 1 - t
        return tuple(
            mt ** 3 * s + 3 * mt ** 2 * t * a + 3 * mt * t ** 2 * b + t ** 3 * e
            for s, a, b, e in zip(start, c1, c2, end)
        )

    for i in range(11):
        t = i / 10.0
        assert quad(t) == pytest.approx(cubic(t), abs=1e-9)


def test_multiple_consecutive_off_curve_points_imply_midpoints():
    segments = [
        ("moveTo", ((0.0, 0.0),)),
        ("qCurveTo", ((20.0, 40.0), (60.0, 40.0), (80.0, 0.0))),
        ("closePath", ()),
    ]
    expanded = _expand_quadratics(segments)
    curves = [pts for op, pts in expanded if op == "curveTo"]
    assert len(curves) == 2
    # the implied on-curve point sits midway between the two controls
    assert curves[0][2] == pytest.approx((40.0, 40.0))
    assert curves[1][2] == (80.0, 0.0)


@pytest.mark.parametrize(
    "segments",
    [
        [("moveTo", ((0.0, 0.0),)), ("qCurveTo", ()), ("closePath", ())],
        [
            ("moveTo", ((0.0, 0.0),)),
            ("qCurveTo", ((10.0, 10.0), None)),
            ("closePath", ()),
        ],
    ],
)
def test_malformed_quadratics_are_rejected_instead_of_degrading_to_lines(
    segments,
) -> None:
    with pytest.raises(ValueError, match="qCurveTo"):
        _expand_quadratics(segments)


class _FakePathContour:
    def __init__(self, segments) -> None:
        self.segments = segments


@pytest.mark.parametrize(
    "segments",
    [
        [("moveTo", ((0.0, 0.0),)), ("lineTo", (1.0, 2.0)), ("closePath", ())],
        [("moveTo", ((0.0, 0.0),)), ("lineTo", ((1.0, 2.0),))],
        [
            ("moveTo", ((0.0, 0.0),)),
            ("closePath", ()),
            ("lineTo", ((1.0, 2.0),)),
        ],
    ],
)
def test_malformed_pathops_contours_are_rejected(segments) -> None:
    with pytest.raises(ValueError):
        _contour_from_path(_FakePathContour(segments), {}, 5.0)


@pytest.mark.skipif(not REAL_SOURCE.exists(), reason="no real font configured")
def test_every_glyph_in_the_real_font_compounds_without_error():
    font = parse_glyphs(str(REAL_SOURCE))
    failures = []
    for name, glyph in font.glyphs.items():
        try:
            compound_glyph(glyph)
        except Exception as exc:  # noqa: BLE001 - we want the glyph name
            failures.append("{}: {}".format(name, exc))
    assert not failures, failures
