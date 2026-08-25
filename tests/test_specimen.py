from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import typedesignxray.specimen as specimen_module
from typedesignxray.specimen import (
    catalog_request,
    render_request,
    specimen_page,
)
from typedesignxray.parsers import load_font


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "BlueprintDemo.glyphs"
SVG = {"svg": "http://www.w3.org/2000/svg"}


def test_catalog_is_source_derived_and_pairs_case_counterparts() -> None:
    result = catalog_request({"font_path": str(EXAMPLE)})

    assert result["family_name"] == "Blueprint Demo"
    assert result["master_name"] == "Regular"
    assert result["selected_master_id"] == "m01"
    assert result["units_per_em"] == 1000
    assert result["master_layer_only"] is True
    assert result["masters"] == [{"id": "m01", "name": "Regular"}]
    assert result["sequence"][0] == {
        "id": 0,
        "left": "A",
        "right": "a",
        "label": "A / a",
    }

    glyphs = {glyph["name"]: glyph for glyph in result["glyphs"]}
    assert glyphs["A"]["category"] == "MAJUSCULE"
    assert glyphs["A"]["group"] == "STD LATIN ALPHABET"
    assert glyphs["A"]["unicode"] == "0041"
    assert glyphs["A"]["width"] == 640
    assert glyphs["a"]["lsb"] == 5
    assert glyphs["a"]["rsb"] == 60

    played = [
        name
        for frame in result["sequence"]
        for name in (frame["left"], frame["right"])
        if name is not None
    ]
    designed = {
        name
        for name, glyph in load_font(EXAMPLE, master="m01").glyphs.items()
        if any(contour.nodes for contour in glyph.contours)
    }
    assert len(played) == len(set(played))
    assert set(played) == designed
    assert result["designed_glyph_count"] == len(designed)


def test_catalog_preserves_selected_master_id_when_names_collide(
    tmp_path: Path,
) -> None:
    source = tmp_path / "duplicate-master-names.glyphs"
    source.write_text(
        """
        {
        familyName = Demo;
        fontMaster = (
            { id = M1; name = Regular; },
            { id = M2; name = Regular; },
        );
        glyphs = (
            {
                glyphname = A;
                unicode = 65;
                layers = (
                    {
                        layerId = M1;
                        width = 500;
                        paths = ({
                            closed = 1;
                            nodes = (
                                "0 0 LINE",
                                "100 0 LINE",
                                "100 100 LINE",
                            );
                        });
                    },
                    {
                        layerId = M2;
                        width = 700;
                        paths = ({
                            closed = 1;
                            nodes = (
                                "0 0 LINE",
                                "200 0 LINE",
                                "200 100 LINE",
                            );
                        });
                    },
                );
            },
        );
        }
        """,
        encoding="utf-8",
    )

    result = catalog_request(
        {"font_path": str(source), "master": "M2"}
    )

    assert result["master_name"] == "Regular"
    assert result["selected_master_id"] == "M2"
    assert result["glyphs"][0]["width"] == 700


def test_render_returns_both_modes_with_one_shared_font_unit_scale() -> None:
    result = render_request(
        {
            "font_path": str(EXAMPLE),
            "glyphs": ["A", "a"],
            "point_size": 370,
            "mode": "xray",
        }
    )

    assert result["mode"] == "xray"
    assert result["compounded"] is True
    assert result["font_unit_scale"] == pytest.approx(0.37)
    assert len(result["renders"]) == 2
    assert result["svgs"] == [render["xray_svg"] for render in result["renders"]]

    transforms = []
    for render in result["renders"]:
        assert render["xray_compounded"] is True
        solid = ET.fromstring(render["solid_svg"])
        xray = ET.fromstring(render["xray_svg"])
        assert solid.get("data-mode") == "solid"
        assert xray.get("data-mode") == "xray"
        geometry = xray.find(".//svg:g[@class='font-unit-geometry']", SVG)
        assert geometry is not None
        transforms.append(geometry.get("transform"))
        assert xray.find(".//svg:path[@class='native-outline']", SVG) is not None
        assert xray.find(".//svg:g[@class='on-curve-nodes']", SVG) is not None

    assert transforms == ["scale(0.37 -0.37)", "scale(0.37 -0.37)"]
    a_xray = ET.fromstring(result["renders"][1]["xray_svg"])
    assert a_xray.find(".//svg:g[@class='handle-lines']/svg:line", SVG) is not None
    assert a_xray.find(".//svg:g[@class='handle-points']/svg:circle", SVG) is not None


def test_xray_mode_compounds_requested_master_glyphs_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_compound(path: Path, master: str, glyph_name: str):
        calls.append((path, master, glyph_name))
        return load_font(EXAMPLE, master=master).glyphs[glyph_name]

    monkeypatch.setattr(specimen_module, "_compound_for_render", fake_compound)
    xray = render_request(
        {
            "font_path": str(EXAMPLE),
            "master": "m01",
            "glyphs": ["A", "a"],
            "mode": "xray",
        }
    )
    assert [call[2] for call in calls] == ["A", "a"]
    assert all(call[1] == "m01" for call in calls)
    assert xray["compounded"] is True

    calls.clear()
    solid = render_request(
        {
            "font_path": str(EXAMPLE),
            "master": "m01",
            "glyphs": ["A", "a"],
            "mode": "solid",
        }
    )
    assert calls == []
    assert solid["compounded"] is False


def test_boolean_bezier_alias_selects_xray_and_unknown_glyph_is_clear() -> None:
    result = render_request(
        {
            "font_path": str(EXAMPLE),
            "glyph_name": "a",
            "bezier": True,
        }
    )
    assert result["mode"] == "xray"
    assert result["svg"] == result["renders"][0]["xray_svg"]

    with pytest.raises(ValueError, match=re.escape("glyph 'missing' was not found")):
        render_request(
            {"font_path": str(EXAMPLE), "glyphs": ["missing"]}
        )


def test_custom_palette_reaches_live_solid_and_xray_svg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        specimen_module,
        "_compound_for_render",
        lambda path, master, glyph_name: load_font(EXAMPLE).glyphs[glyph_name],
    )
    colors = {
        "fill": "#123456",
        "stroke": "#abcdef",
        "guides": "#334455",
        "handles": "#556677",
        "point_fill": "#101010",
        "point_stroke": "#eeeeee",
    }
    solid = render_request(
        {"font_path": str(EXAMPLE), "glyphs": ["A"], "colors": colors}
    )
    assert 'class="solid-outline"' in solid["svg"]
    assert 'fill="#123456"' in solid["svg"]
    assert 'stroke="#334455"' in solid["svg"]

    xray = render_request(
        {
            "font_path": str(EXAMPLE),
            "glyphs": ["a"],
            "mode": "xray",
            "colors": colors,
        }
    )
    assert 'class="native-outline"' in xray["svg"]
    assert 'stroke="#abcdef"' in xray["svg"]
    assert 'stroke="#556677"' in xray["svg"]
    assert 'fill="#101010" stroke="#eeeeee"' in xray["svg"]

    with pytest.raises(ValueError, match="six-digit hex color"):
        render_request(
            {
                "font_path": str(EXAMPLE),
                "glyphs": ["A"],
                "colors": {"fill": "red"},
            }
        )


def test_page_exposes_player_controls_and_existing_upload_route() -> None:
    page = specimen_page()

    for control_id in (
        "font-file",
        "master",
        "previous",
        "play",
        "next",
        "point-size",
        "speed",
        "pair",
        "bezier",
        "frame-start",
        "frame-end",
        "use-current",
        "export-svg",
        "export-png",
        "export-gif",
        "export-mp4",
    ):
        assert 'id="{}"'.format(control_id) in page
    assert 'fetch("/api/upload"' in page
    assert 'jsonRequest("/api/specimen/catalog"' in page
    assert 'jsonRequest("/api/specimen/render"' in page
    assert 'fetch("/api/specimen/export"' in page
    assert "COMPOUNDED" in page
    assert page.count("data-color=") == 8
    assert "aspect-ratio:1080 / 766" in page
    assert 'id="speed" type="number" min="0.08" max="1"' in page
    assert "catalog.selected_master_id || catalog.masters[0]?.id" in page
    assert "SPEED MUST BE BETWEEN 0.08 AND 1 SECOND PER FRAME" in page
