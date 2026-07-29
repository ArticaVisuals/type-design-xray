from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from glyphblueprint import ir
from glyphblueprint import style as style_contract
from glyphblueprint.render.svg import render_glyph_svg, render_svg


SVG = {"svg": "http://www.w3.org/2000/svg"}


def _layout(
    contours,
    *,
    name="a",
    advance=600.0,
    glyph_metrics=None,
    layout_metrics=None,
):
    glyph = ir.Glyph(
        name=name,
        advance_width=advance,
        contours=list(contours),
        metrics=glyph_metrics or ir.Metrics(),
    )
    return ir.Layout(
        glyphs=[ir.PositionedGlyph(glyph=glyph, origin_x=0.0)],
        metrics=layout_metrics or ir.Metrics(),
        total_advance=advance,
    )


def _root(markup):
    return ET.fromstring(markup)


def _layer(root, name):
    return root.find("svg:g[@data-layer='{}']".format(name), SVG)


def _outline_path(root):
    return _layer(root, "outline").find(".//svg:path", SVG)


def _simple_contour(*, closed=True):
    return ir.Contour(
        nodes=[
            ir.Node((0.0, 0.0)),
            ir.Node((100.0, 0.0)),
            ir.Node((100.0, 200.0)),
        ],
        closed=closed,
    )


def test_output_is_xml_with_ordered_toggleable_layers_and_glyph_groups():
    resolved = style_contract.Style()
    resolved.layers.fill = False
    markup = render_svg(_layout([_simple_contour()]), resolved, title="A & <B>")
    root = _root(markup)

    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.attrib["data-preset"] == "blueprint"
    assert root.find("svg:title", SVG).text == "A & <B>"
    layer_names = [
        child.attrib["data-layer"]
        for child in root
        if child.tag == "{http://www.w3.org/2000/svg}g"
        and "data-layer" in child.attrib
    ]
    assert layer_names == [
        name for name in style_contract.LAYER_ORDER if name != "fill"
    ]
    assert _layer(root, "fill") is None
    assert _layer(root, "outline").find(
        ".//svg:g[@id='outline_01a'][@data-glyph='a']", SVG
    ) is not None


def test_background_none_is_transparent_without_removing_enabled_layer():
    resolved = style_contract.Style()
    resolved.canvas.background = None
    root = _root(render_svg(_layout([_simple_contour()]), resolved))

    background = _layer(root, "background")
    assert background is not None
    assert background.find("svg:rect", SVG) is None


def test_segment_rule_uses_lines_until_either_handle_requires_a_cubic():
    contour = _simple_contour()
    root = _root(render_svg(_layout([contour]), style_contract.Style()))
    plain_path = _outline_path(root).attrib["d"]
    assert " L " in plain_path
    assert " C " not in plain_path

    contour.nodes[0].handle_out = (25.0, 40.0)
    root = _root(render_svg(_layout([contour]), style_contract.Style()))
    curved_path = _outline_path(root).attrib["d"]
    assert "C 25 40 100 0 100 0" in curved_path


def test_open_contour_has_no_close_command_and_is_skipped_by_fill():
    resolved = style_contract.Style()
    resolved.outline.fill_enabled = True
    root = _root(render_svg(_layout([_simple_contour(closed=False)]), resolved))

    outline_path = _outline_path(root)
    assert "Z" not in outline_path.attrib["d"]
    assert _layer(root, "fill").find(".//svg:path", SVG) is None


def _marker_screen_radius(markup):
    root = _root(markup)
    node_layer = _layer(root, "nodes")
    marker = node_layer.find(".//svg:circle[@data-shape='circle']", SVG)
    geometry = node_layer.find("svg:g[@class='font-unit-geometry']", SVG)
    scale_match = re.search(
        r"scale\(([-+0-9.eE]+) ([-+0-9.eE]+)\)",
        geometry.attrib["transform"],
    )
    assert scale_match is not None
    return float(marker.attrib["r"]) * abs(float(scale_match.group(1)))


def test_marker_size_is_invariant_at_canvas_width_400_and_4000():
    layout = _layout([_simple_contour()])
    small = style_contract.Style()
    small.canvas.width = 400
    small.nodes.corner.shape = "circle"
    large = small.merged({"canvas": {"width": 4000}})

    small_radius = _marker_screen_radius(render_svg(layout, small))
    large_radius = _marker_screen_radius(render_svg(layout, large))
    assert small_radius == pytest.approx(small.nodes.corner.size)
    assert large_radius == pytest.approx(large.nodes.corner.size)
    assert small_radius == pytest.approx(large_radius)


@pytest.mark.parametrize(
    ("shape", "tag"),
    [
        ("circle", "circle"),
        ("square", "rect"),
        ("diamond", "polygon"),
        ("triangle", "polygon"),
        ("cross", "path"),
        ("none", None),
    ],
)
def test_every_marker_shape_renders(shape, tag):
    resolved = style_contract.Style()
    resolved.nodes.corner.shape = shape
    resolved.nodes.smooth.visible = False
    root = _root(render_svg(_layout([_simple_contour()]), resolved))
    node_layer = _layer(root, "nodes")
    markers = node_layer.findall(".//*[@data-shape='{}']".format(shape), SVG)

    if tag is None:
        assert markers == []
    else:
        assert markers
        assert all(
            marker.tag == "{{http://www.w3.org/2000/svg}}{}".format(tag)
            for marker in markers
        )


def test_metrics_guides_labels_values_and_screen_space_text():
    glyph_metrics = ir.Metrics(lsb=45.0, rsb=55.0)
    layout_metrics = ir.Metrics(
        baseline=0.0,
        x_height=510.0,
        cap_height=700.0,
        ascender=750.0,
        descender=-250.0,
    )
    layout = _layout(
        [_simple_contour()],
        glyph_metrics=glyph_metrics,
        layout_metrics=layout_metrics,
    )
    resolved = style_contract.Style()
    resolved.metrics.visible = True
    resolved.metrics.show = ["baseline", "xheight", "sidebearings"]
    resolved.metrics.labels = True
    resolved.metrics.label_values = True
    root = _root(render_svg(layout, resolved))
    metrics = _layer(root, "metrics")

    assert metrics.findall(".//svg:line[@data-metric='baseline']", SVG)
    assert metrics.findall(".//svg:line[@data-metric='xheight']", SVG)
    assert len(
        metrics.findall(".//svg:line[@data-metric='sidebearings']", SVG)
    ) == 2
    label_text = [text.text for text in metrics.findall(".//svg:text", SVG)]
    assert "baseline 0" in label_text
    assert "x-height 510" in label_text
    assert "lsb 45" in label_text
    assert "rsb 55" in label_text

    for ancestor in metrics.iter():
        transform = ancestor.attrib.get("transform", "")
        if re.search(r"scale\([^)]*\s-", transform):
            assert ancestor.find(".//svg:text", SVG) is None


def test_dashed_handle_line_emits_scaled_stroke_dasharray():
    contour = _simple_contour()
    contour.nodes[0].handle_out = (30.0, 40.0)
    resolved = style_contract.Style()
    resolved.handles.line.dash = "dashed"
    resolved.handles.line.width = 2.0
    root = _root(render_svg(_layout([contour]), resolved))

    line = _layer(root, "handle_lines").find(".//svg:line", SVG)
    assert line is not None
    assert "stroke-dasharray" in line.attrib
    first, second = [float(value) for value in line.attrib["stroke-dasharray"].split(",")]
    assert first > second > 0


def test_single_glyph_convenience_uses_glyph_metrics_and_advance():
    glyph = ir.Glyph(
        name="ampersand",
        advance_width=720.0,
        contours=[_simple_contour()],
        metrics=ir.Metrics(ascender=780.0, descender=-220.0),
    )
    root = _root(render_glyph_svg(glyph, style_contract.Style()))

    group = _layer(root, "outline").find(
        ".//svg:g[@id='outline_01ampersand']", SVG
    )
    assert group is not None


def test_ids_stay_unique_when_index_width_grows_past_two_digits():
    glyphs = []
    for index in range(100):
        name = "x"
        if index == 9:
            name = "0a"
        elif index == 99:
            name = "a"
        glyph = ir.Glyph(
            name=name,
            advance_width=10.0,
            contours=[_simple_contour(closed=False)],
        )
        glyphs.append(ir.PositionedGlyph(glyph=glyph, origin_x=index * 10.0))
    layout = ir.Layout(glyphs=glyphs, total_advance=1000.0)

    root = _root(render_svg(layout, style_contract.Style()))
    ids = [element.attrib["id"] for element in root.iter() if "id" in element.attrib]

    assert len(ids) == len(set(ids))
    assert "outline_0100a" in ids
    assert "outline_100a" in ids
