from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Sequence, Tuple

import pytest
from fontTools.ufoLib import UFOWriter

from glyphblueprint.layout import kern_value, layout_string
from glyphblueprint.parsers.ufo import list_layers, parse_ufo


def _write_glyph(
    glyph_set: Any,
    name: str,
    width: float,
    unicodes: Sequence[int],
    draw_points: Callable[[Any], None],
) -> None:
    glyph_set.writeGlyph(
        name,
        SimpleNamespace(width=width, unicodes=list(unicodes)),
        draw_points,
    )


def _closed_cubic(pen: Any) -> None:
    pen.beginPath()
    pen.addPoint((10, 0))
    pen.addPoint((20, 0), "curve", smooth=True)
    pen.addPoint((30, 0))
    pen.addPoint((40, 10))
    pen.addPoint((50, 0), "curve", smooth=True)
    pen.addPoint((50, 50), "line")
    pen.addPoint((0, 50), "line")
    pen.addPoint((0, 0))
    pen.endPath()


def _open_cubic(pen: Any) -> None:
    pen.beginPath()
    pen.addPoint((0, 0), "move")
    pen.addPoint((10, 20))
    pen.addPoint((20, 20))
    pen.addPoint((30, 0), "curve", smooth=True)
    pen.addPoint((40, 0), "line")
    pen.endPath()


def _quadratic(pen: Any) -> None:
    pen.beginPath()
    pen.addPoint((0, 0), "move")
    pen.addPoint((30, 60))
    pen.addPoint((60, 60))
    pen.addPoint((90, 0), "qcurve", smooth=True)
    pen.endPath()


def _component_base(pen: Any) -> None:
    pen.beginPath()
    pen.addPoint((0, 0), "move")
    pen.addPoint((5, 0))
    pen.addPoint((10, 5))
    pen.addPoint((10, 10), "curve", smooth=True)
    pen.endPath()


def _closed_triangle(pen: Any) -> None:
    pen.beginPath()
    pen.addPoint((0, 0), "line")
    pen.addPoint((20, 0), "line")
    pen.addPoint((10, 20), "line")
    pen.endPath()


def _alternate_layer_path(pen: Any) -> None:
    pen.beginPath()
    pen.addPoint((5, 5), "move")
    pen.addPoint((25, 5), "line")
    pen.endPath()


def _empty(_pen: Any) -> None:
    pass


def _build_ufo(tmp_path: Path) -> Path:
    path = tmp_path / "Synthetic.ufo"
    with UFOWriter(str(path), formatVersion=3) as writer:
        writer.writeInfo(
            SimpleNamespace(
                familyName="Synthetic UFO",
                unitsPerEm=1000,
                ascender=800,
                descender=-200,
                capHeight=700,
                xHeight=500,
            )
        )
        default = writer.getGlyphSet()
        _write_glyph(
            default, "closedCubic", 600, [0x43, 0x0106], _closed_cubic
        )
        _write_glyph(default, "openCubic", 500, [], _open_cubic)
        _write_glyph(default, "quadratic", 500, [], _quadratic)
        _write_glyph(default, "componentBase", 100, [], _component_base)
        _write_glyph(
            default,
            "componentMiddle",
            200,
            [],
            lambda pen: pen.addComponent(
                "componentBase", (1, 0, 0, 1, 10, 20)
            ),
        )
        _write_glyph(
            default,
            "componentNested",
            500,
            [],
            lambda pen: pen.addComponent(
                "componentMiddle", (2, 0, 0, 2, 100, 200)
            ),
        )
        _write_glyph(
            default,
            "cycleA",
            100,
            [],
            lambda pen: pen.addComponent(
                "cycleB", (1, 0, 0, 1, 0, 0)
            ),
        )
        _write_glyph(
            default,
            "cycleB",
            100,
            [],
            lambda pen: pen.addComponent(
                "cycleA", (1, 0, 0, 1, 0, 0)
            ),
        )
        _write_glyph(default, "A", 600, [0x41], _empty)
        _write_glyph(default, "V", 580, [0x56], _empty)
        _write_glyph(default, "layerProbe", 400, [], _closed_triangle)
        default.writeContents()

        alternate = writer.getGlyphSet("Sketch", defaultLayer=False)
        _write_glyph(
            alternate, "layerProbe", 410, [], _alternate_layer_path
        )
        _write_glyph(alternate, "alternateOnly", 300, [], _closed_triangle)
        alternate.writeContents()

        writer.writeGroups(
            {
                "public.kern1.AGroup": ["A"],
                "public.kern2.VGroup": ["V"],
                "public.other": ["closedCubic"],
            }
        )
        writer.writeKerning(
            {
                (
                    "public.kern1.AGroup",
                    "public.kern2.VGroup",
                ): -80
            }
        )
        writer.writeLayerContents(["public.default", "Sketch"])
    return path


def _quadratic_point(
    start: Tuple[float, float],
    control: Tuple[float, float],
    end: Tuple[float, float],
    t: float,
) -> Tuple[float, float]:
    reverse = 1.0 - t
    return (
        reverse * reverse * start[0]
        + 2.0 * reverse * t * control[0]
        + t * t * end[0],
        reverse * reverse * start[1]
        + 2.0 * reverse * t * control[1]
        + t * t * end[1],
    )


def _cubic_point(
    start: Tuple[float, float],
    control1: Tuple[float, float],
    control2: Tuple[float, float],
    end: Tuple[float, float],
    t: float,
) -> Tuple[float, float]:
    reverse = 1.0 - t
    return (
        reverse ** 3 * start[0]
        + 3.0 * reverse * reverse * t * control1[0]
        + 3.0 * reverse * t * t * control2[0]
        + t ** 3 * end[0],
        reverse ** 3 * start[1]
        + 3.0 * reverse * reverse * t * control1[1]
        + 3.0 * reverse * t * t * control2[1]
        + t ** 3 * end[1],
    )


def test_closed_cubic_preserves_smooth_and_wraparound_handles(
    tmp_path: Path,
) -> None:
    font = parse_ufo(_build_ufo(tmp_path))
    contour = font.glyphs["closedCubic"].contours[0]
    first, curved, _top_right, last = contour.nodes

    assert contour.closed is True
    assert font.node_types_exact is True
    assert font.glyphs["closedCubic"].node_types_exact is True
    assert first.point == (20.0, 0.0)
    assert first.type == "curve"
    assert first.smooth is True
    assert first.handle_in == (10.0, 0.0)
    assert last.handle_out == (0.0, 0.0)
    assert curved.type == "curve"
    assert curved.smooth is True
    assert first.handle_out == (30.0, 0.0)
    assert curved.handle_in == (40.0, 10.0)


def test_leading_move_produces_an_open_contour(tmp_path: Path) -> None:
    font = parse_ufo(_build_ufo(tmp_path))
    contour = font.glyphs["openCubic"].contours[0]

    assert contour.closed is False
    assert [node.point for node in contour.nodes] == [
        (0.0, 0.0),
        (30.0, 0.0),
        (40.0, 0.0),
    ]
    assert contour.nodes[0].handle_in is None
    assert contour.nodes[0].handle_out == (10.0, 20.0)
    assert contour.nodes[1].handle_in == (20.0, 20.0)
    assert contour.nodes[1].smooth is True


def test_qcurve_is_upconverted_with_implied_oncurve_points(
    tmp_path: Path,
) -> None:
    font = parse_ufo(_build_ufo(tmp_path))
    contour = font.glyphs["quadratic"].contours[0]

    assert contour.closed is False
    assert [node.point for node in contour.nodes] == [
        (0.0, 0.0),
        (45.0, 60.0),
        (90.0, 0.0),
    ]
    assert all(node.type in ("line", "curve") for node in contour.nodes)
    assert contour.nodes[1].smooth is True
    assert contour.nodes[2].smooth is True

    source_pieces = [
        ((0.0, 0.0), (30.0, 60.0), (45.0, 60.0)),
        ((45.0, 60.0), (60.0, 60.0), (90.0, 0.0)),
    ]
    for (start, control, end), (ir_start, ir_end) in zip(
        source_pieces, contour.segments()
    ):
        assert ir_start.handle_out is not None
        assert ir_end.handle_in is not None
        for t in (0.2, 0.5, 0.8):
            assert _cubic_point(
                ir_start.point,
                ir_start.handle_out,
                ir_end.handle_in,
                ir_end.point,
                t,
            ) == pytest.approx(
                _quadratic_point(start, control, end, t)
            )


def test_components_decompose_recursively_with_transforms_and_cycle_guard(
    tmp_path: Path,
) -> None:
    font = parse_ufo(_build_ufo(tmp_path))
    contour = font.glyphs["componentNested"].contours[0]

    assert contour.closed is False
    assert contour.nodes[0].point == (120.0, 240.0)
    assert contour.nodes[0].handle_out == (130.0, 240.0)
    assert contour.nodes[1].handle_in == (140.0, 250.0)
    assert contour.nodes[1].point == (140.0, 260.0)
    assert font.glyphs["cycleA"].contours == []
    assert font.glyphs["cycleB"].contours == []


def test_kern_groups_are_inverted_for_layout_lookup(tmp_path: Path) -> None:
    font = parse_ufo(_build_ufo(tmp_path))

    assert font.kern_group_left["A"] == "public.kern1.AGroup"
    assert font.kern_group_right["V"] == "public.kern2.VGroup"
    assert font.kerning[
        ("public.kern1.AGroup", "public.kern2.VGroup")
    ] == -80.0
    assert kern_value(font, "A", "V") == -80.0
    assert layout_string(font, "AV").glyphs[1].kern_before == -80.0


def test_list_layers_and_named_layer_fallback(tmp_path: Path) -> None:
    path = _build_ufo(tmp_path)
    layers = list_layers(path, "layerProbe")

    assert [
        (
            item.layer_id,
            item.name,
            item.is_master,
            item.contour_count,
            item.has_open_contours,
        )
        for item in layers
    ] == [
        ("public.default", "public.default", True, 1, False),
        ("Sketch", "Sketch", False, 1, True),
    ]

    selected = parse_ufo(path, layer="Sketch")
    assert selected.glyphs["layerProbe"].layer_name == "Sketch"
    assert selected.glyphs["layerProbe"].contours[0].closed is False
    assert selected.glyphs["closedCubic"].layer_name == ""
    assert "alternateOnly" in selected.glyphs
    with pytest.raises(ValueError, match="layer.*Missing.*not found"):
        parse_ufo(path, layer="Missing")


def test_font_metadata_cmap_advance_and_sidebearings(tmp_path: Path) -> None:
    font = parse_ufo(_build_ufo(tmp_path))
    glyph = font.glyphs["closedCubic"]

    assert font.source_format == "ufo"
    assert font.family_name == "Synthetic UFO"
    assert font.units_per_em == 1000.0
    assert font.metrics.baseline == 0.0
    assert font.metrics.ascender == 800.0
    assert font.metrics.descender == -200.0
    assert font.metrics.cap_height == 700.0
    assert font.metrics.x_height == 500.0
    assert glyph.advance_width == 600.0
    assert glyph.unicodes == [0x43, 0x0106]
    assert font.cmap[0x43] == "closedCubic"
    assert font.cmap[0x0106] == "closedCubic"
    assert glyph.metrics.lsb == 0.0
    assert glyph.metrics.rsb == 550.0
    assert font.glyphs["A"].metrics.lsb is None
    assert font.glyphs["A"].metrics.rsb is None


def test_missing_and_non_ufo_paths_have_friendly_errors(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError, match="does not exist"):
        parse_ufo(tmp_path / "Missing.ufo")

    directory = tmp_path / "NotAFont"
    directory.mkdir()
    with pytest.raises(ValueError, match="not a UFO directory"):
        parse_ufo(directory)

    path = _build_ufo(tmp_path)
    with pytest.raises(ValueError, match="glyph.*absent.*not found"):
        list_layers(path, "absent")
