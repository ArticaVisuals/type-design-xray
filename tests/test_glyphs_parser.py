from __future__ import annotations

from pathlib import Path

import pytest

from glyphblueprint.parsers import plist
from glyphblueprint.parsers.glyphs import list_layers, parse_glyphs


FIXTURES = Path(__file__).parent / "fixtures"
REAL_GLYPHS = Path(
    "/Users/micahhoang/My Drive/Font Design/CaliperSans04/"
    "CaliperSans_04.glyphs"
)


def test_plist_reader_handles_openstep_syntax_and_inline_tuples() -> None:
    source = r'''
        // Glyphs permits dotted, unquoted keys before the normal content.
        .appVersion = "4\"000";
        .formatVersion = 4;
        /* Arrays can contain dictionaries and v4 node tuples. */
        payload = {
            bare-key = unquoted;
            escaped = "line\nsnowman:\U2603";
            nested = ({ key = value; }, (479,675,l),);
            commaList = first,second,;
        };
        # Hash comments occur in some hand-edited fixtures.
    '''

    value = plist.loads(source)

    assert value[".appVersion"] == '4"000'
    assert value["payload"]["bare-key"] == "unquoted"
    assert value["payload"]["escaped"] == "line\nsnowman:☃"
    assert value["payload"]["nested"] == [
        {"key": "value"},
        ["479", "675", "l"],
    ]
    assert value["payload"]["commaList"] == ["first", "second"]


def test_v4_node_assembly_wrap_smooth_open_and_quadratic() -> None:
    font = parse_glyphs(FIXTURES / "synthetic_v4.glyphs")
    glyph = font.glyphs["base"]

    closed = glyph.contours[0]
    first, curved, last = closed.nodes
    assert closed.closed is True
    assert first.point == (20.0, 0.0)
    assert first.type == "curve"
    assert first.smooth is True
    assert first.handle_in == (10.0, 0.0)
    assert first.handle_out == (30.0, 0.0)
    assert curved.type == "curve"
    assert curved.smooth is True
    assert curved.handle_in == (40.0, 10.0)
    assert last.type == "line"
    assert last.smooth is True
    assert last.handle_out == (110.0, 0.0)

    open_contour = glyph.contours[1]
    assert open_contour.closed is False
    assert open_contour.nodes[-1].handle_in == (20.0, 30.0)
    assert open_contour.nodes[0].handle_in is None

    quadratic = glyph.contours[2]
    assert quadratic.closed is False
    assert quadratic.nodes[-1].type == "curve"
    assert quadratic.nodes[-1].smooth is True
    assert quadratic.nodes[0].handle_out == pytest.approx(
        (20.0 / 3.0, 140.0 / 3.0)
    )
    assert quadratic.nodes[-1].handle_in == pytest.approx(
        (40.0 / 3.0, 140.0 / 3.0)
    )


def test_component_decomposition_and_named_layer_fallback() -> None:
    default_font = parse_glyphs(FIXTURES / "synthetic_v4.glyphs")
    composite = default_font.glyphs["composite"]
    assert len(composite.contours) == 3
    assert composite.contours[0].nodes[0].point == (140.0, 200.0)
    assert composite.contours[0].nodes[0].handle_in == (120.0, 200.0)

    alternate_font = parse_glyphs(
        FIXTURES / "synthetic_v4.glyphs", layer="alternate"
    )
    assert alternate_font.glyphs["composite"].layer_name == "Alternate"
    assert alternate_font.glyphs["composite"].contours[0].nodes[0].point == (
        25.0,
        0.0,
    )
    assert alternate_font.glyphs["base"].layer_name == ""
    with pytest.raises(ValueError, match="not found on any glyph"):
        parse_glyphs(FIXTURES / "synthetic_v4.glyphs", layer="Missing")


def test_decimal_and_hex_unicode_forms() -> None:
    modern = parse_glyphs(FIXTURES / "synthetic_v4.glyphs")
    assert modern.glyphs["decimalZ"].unicodes == [90]
    assert modern.cmap[90] == "decimalZ"
    assert modern.glyphs["hexA"].unicodes == [0x61]

    legacy = parse_glyphs(FIXTURES / "synthetic_v2.glyphs")
    assert legacy.glyphs["part"].unicodes == [0x5A]
    assert legacy.glyphs["made"].unicodes == [0x61, 0x62]
    assert legacy.glyphs["made"].contours[0].nodes[0].point == (10.0, 0.0)
    assert legacy.glyphs["part"].contours[0].nodes[-1].smooth is True


def test_kern_group_direction_and_full_keys() -> None:
    font = parse_glyphs(FIXTURES / "synthetic_v4.glyphs")

    assert font.kern_group_left["base"] == "@MMK_L_LeftSide"
    assert font.kern_group_right["base"] == "@MMK_R_RightSide"
    assert (
        font.kerning[
            ("@MMK_L_LeftSide", "@MMK_R_RightSide")
        ]
        == -80.0
    )


def test_metrics_index_alignment_against_real_file() -> None:
    if not REAL_GLYPHS.exists():
        pytest.skip("real CaliperSans Glyphs source is not available")

    font = parse_glyphs(REAL_GLYPHS)

    assert font.metrics.ascender == 850
    assert font.metrics.cap_height == 740
    assert font.metrics.x_height == 510
    assert font.metrics.descender == -230
    assert font.cmap[90] == "Z"


def test_list_layers_finds_real_skeleton_layer() -> None:
    if not REAL_GLYPHS.exists():
        pytest.skip("real CaliperSans Glyphs source is not available")

    layers = list_layers(REAL_GLYPHS, "a")
    skeleton = next(layer for layer in layers if layer.name == "Skeleton v1")

    assert skeleton.associated_master_id == "m01"
    assert skeleton.contour_count == 2
    assert skeleton.has_open_contours is True
