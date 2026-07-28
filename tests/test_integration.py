"""End-to-end tests across the whole stack.

These deliberately re-derive their expectations from the source file rather than
reusing helpers from the module-level tests, so a shared misunderstanding
between a module and its own tests still gets caught here.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from glyphblueprint.api import blueprint
from glyphblueprint.config import available_presets, resolve_style
from glyphblueprint.layout import kern_value, layout_string
from glyphblueprint.parsers.glyphs import list_layers, parse_glyphs
from glyphblueprint.render.svg import render_svg

DEMO = Path(__file__).resolve().parent.parent / "examples" / "BlueprintDemo.glyphs"


@pytest.fixture(scope="module")
def demo():
    return parse_glyphs(str(DEMO))


def signed_area(contour):
    pts = [n.point for n in contour.nodes]
    return 0.5 * sum(
        pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
        for i in range(len(pts))
    )


def test_demo_font_counters_are_wound_opposite(demo):
    """Counters must oppose their outer contour or fill knocks nothing out."""
    for name in ("A", "a", "o"):
        glyph = demo.glyphs[name]
        signs = [signed_area(c) > 0 for c in glyph.contours]
        outer = signs[0]
        assert signs[1] is not outer, "{}: counter has same winding as outer".format(name)


def test_fill_layer_groups_contours_into_one_path_per_glyph(demo):
    """Counter knockout requires subpaths in a single path element."""
    style = resolve_style(preset="drafting")
    style.outline.fill_enabled = True
    svg = render_svg(layout_string(demo, "o"), style)
    root = ET.fromstring(svg)
    fills = [
        el
        for g in root.iter()
        if g.get("data-layer") == "fill"
        for el in g.iter()
        if el.tag.endswith("path")
    ]
    assert len(fills) == 1
    assert fills[0].get("d").count("M") == 2


def test_em_frame_is_exactly_one_em_tall_and_differs_from_metrics(demo):
    """Regression: 'em' and 'metrics' once collapsed into the same branch."""
    heights = {}
    for mode in ("em", "metrics"):
        style = resolve_style(preset="blueprint")
        style.canvas.frame = mode
        style.canvas.padding = 0.0
        root = ET.fromstring(render_svg(layout_string(demo, "ao"), style))
        heights[mode] = float(root.get("viewBox").split()[3])

    upem = demo.units_per_em
    span = demo.metrics.ascender - demo.metrics.descender
    assert span != upem, "fixture must have ascender-descender != upem to be meaningful"
    assert heights["em"] != heights["metrics"]
    assert heights["em"] / heights["metrics"] == pytest.approx(upem / span, rel=1e-6)


def test_kerning_precedence_through_the_full_stack(demo):
    """Exact pairs beat group pairs, including an explicit zero."""
    assert kern_value(demo, "T", "o") == -80          # flat pair
    assert kern_value(demo, "A", "o") == -55          # group x group
    assert kern_value(demo, "V", "a") == -55          # group x group
    assert kern_value(demo, "V", "o") == 0            # exact 0 cancels the group
    assert kern_value(demo, "A", "T") == 0            # unkerned

    lay = layout_string(demo, "To")
    assert lay.glyphs[1].kern_before == -80
    assert lay.glyphs[1].origin_x == demo.glyphs["T"].advance_width - 80


def test_open_contour_layer_renders_without_closing_or_filling(demo):
    skeleton = parse_glyphs(str(DEMO), layer="Skeleton v1")
    glyph = skeleton.glyphs["a"]
    assert glyph.contours and all(not c.closed for c in glyph.contours)

    style = resolve_style(preset="blueprint")
    style.outline.fill_enabled = True
    root = ET.fromstring(render_svg(layout_string(skeleton, "a"), style))
    for el in root.iter():
        if el.tag.endswith("path") and el.get("d"):
            assert "Z" not in el.get("d").upper()
    fill_paths = [
        el
        for g in root.iter()
        if g.get("data-layer") == "fill"
        for el in g.iter()
        if el.tag.endswith("path")
    ]
    assert fill_paths == [], "open contours must not be filled"


def test_list_layers_reports_the_named_layer(demo):
    names = [layer.name for layer in list_layers(str(DEMO), "a")]
    assert "Skeleton v1" in names


def test_markers_keep_their_on_screen_size_across_canvas_widths(demo):
    """A node must look the same size at width 400 and width 4000."""

    def marker_pixel_sizes(width):
        style = resolve_style(preset="blueprint")
        style.canvas.width = width
        root = ET.fromstring(render_svg(layout_string(demo, "o"), style))
        found = []

        def walk(el, scale):
            transform = el.get("transform") or ""
            if "scale(" in transform:
                inner = transform.split("scale(")[1].split(")")[0]
                scale = scale * abs(float(inner.replace(",", " ").split()[0]))
            for child in el:
                if child.tag.endswith("circle") and child.get("r"):
                    found.append(round(float(child.get("r")) * scale, 4))
                walk(child, scale)

        walk(root, 1.0)
        return sorted(set(found))

    assert marker_pixel_sizes(400) == pytest.approx(marker_pixel_sizes(4000), rel=1e-4)


def test_every_preset_renders_valid_xml(demo):
    for name in available_presets():
        svg = blueprint(str(DEMO), "ao", preset=name)
        ET.fromstring(svg)


def test_public_api_matches_renderer_output(demo):
    style = resolve_style(preset="light")
    direct = render_svg(layout_string(demo, "Tao"), style)
    viaapi = blueprint(str(DEMO), "Tao", preset="light")
    assert direct == viaapi
