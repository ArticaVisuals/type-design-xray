from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from typedesignxray.process import (
    FINAL_HOLD_MS,
    catalog_request,
    export_request,
    render_process_frame_svg,
    render_request,
)


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "BlueprintDemo.glyphs"
SVG = {"svg": "http://www.w3.org/2000/svg"}


def _layer_source(tmp_path: Path) -> Path:
    source = tmp_path / "Process.glyphs"
    source.write_text(
        """
        {
        familyName = "Process Demo";
        unitsPerEm = 1000;
        fontMaster = (
            { id = M1; name = Regular; ascender = 750; capHeight = 700; descender = -250; },
            { id = M2; name = Bold; ascender = 750; capHeight = 700; descender = -250; },
        );
        glyphs = (
            {
                glyphname = A;
                unicode = 65;
                layers = (
                    { layerId = M1; width = 700; paths = ({ closed = 1; nodes = ("0 0 LINE", "400 0 LINE", "200 700 LINE"); }); },
                    { layerId = D1; associatedMasterId = M1; name = Draft; width = 400; paths = ({ closed = 1; nodes = ("0 0 LINE", "200 0 LINE", "100 500 LINE"); }); },
                    { layerId = S1; associatedMasterId = M1; name = "Skeleton v1"; width = 300; paths = ({ closed = 0; nodes = ("0 0 LINE", "150 700 LINE", "300 0 LINE"); }); },
                    { layerId = M2; width = 900; paths = ({ closed = 1; nodes = ("0 0 LINE", "600 0 LINE", "300 700 LINE"); }); },
                    { layerId = OTHER; associatedMasterId = M2; name = Other; width = 850; paths = ({ closed = 1; nodes = ("0 0 LINE", "550 0 LINE", "275 700 LINE"); }); },
                    { layerId = D2; associatedMasterId = M1; name = Draft; width = 500; paths = ({ closed = 1; nodes = ("0 0 LINE", "300 0 LINE", "150 600 LINE"); }); },
                    { layerId = LEGACY; name = Legacy; width = 550; paths = ({ closed = 1; nodes = ("0 0 LINE", "350 0 LINE", "175 650 LINE"); }); },
                );
            },
        );
        }
        """,
        encoding="utf-8",
    )
    return source


def test_catalog_starts_with_skeleton_then_preserves_authored_group_order(
    tmp_path: Path,
) -> None:
    source = _layer_source(tmp_path)

    result = catalog_request(
        {"font_path": str(source), "master": "M1", "glyph": "A"}
    )

    assert result["family_name"] == "Process Demo"
    assert result["master_name"] == "Regular"
    assert result["selected_master_id"] == "M1"
    assert [item["layer_id"] for item in result["layers"]] == [
        "S1",
        "D1",
        "D2",
        "M1",
    ]
    assert [item["name"] for item in result["layers"]].count("Draft") == 2
    assert result["layers"][0]["is_skeleton"] is True
    assert result["layers"][-1]["is_final"] is True
    assert result["layers"][-1]["delay_ms"] == FINAL_HOLD_MS
    assert result["normal_delay_ms"] == 200
    assert result["final_hold_ms"] == 3000
    assert result["frame_size"] == {"width": 540, "height": 766}
    assert result["animation_size"] == {"width": 1080, "height": 1532}


def test_catalog_resolves_character_exact_name_and_selected_master(
    tmp_path: Path,
) -> None:
    source = _layer_source(tmp_path)

    character = catalog_request(
        {"font_path": str(source), "master": "Bold", "glyph": "A"}
    )
    explicit = catalog_request(
        {"font_path": str(source), "master": "M2", "glyph": "/A"}
    )

    assert character["glyph"]["name"] == explicit["glyph"]["name"] == "A"
    assert [item["layer_id"] for item in character["layers"]] == [
        "OTHER",
        "M2",
    ]
    with pytest.raises(ValueError, match="character or exact Glyphs glyph name"):
        catalog_request({"font_path": str(source), "glyph": "missing"})


def test_single_master_legacy_unassociated_layer_is_preserved(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Legacy.glyphs"
    source.write_text(
        """
        {
        familyName = Legacy;
        fontMaster = ({ id = M1; name = Regular; });
        glyphs = ({
            glyphname = A;
            unicode = 65;
            layers = (
                { layerId = BACKUP; name = Early; width = 400; },
                { layerId = M1; width = 500; },
            );
        });
        }
        """,
        encoding="utf-8",
    )

    result = catalog_request({"font_path": str(source), "glyph": "A"})

    assert [item["layer_id"] for item in result["layers"]] == ["BACKUP", "M1"]


def test_duplicate_layer_names_render_by_exact_id_and_layer_metrics(
    tmp_path: Path,
) -> None:
    source = _layer_source(tmp_path)

    first = render_request(
        {
            "font_path": str(source),
            "master": "M1",
            "glyph": "A",
            "layer_id": "D1",
            "bezier": False,
        }
    )
    second = render_request(
        {
            "font_path": str(source),
            "master": "M1",
            "glyph": "A",
            "layer_id": "D2",
            "bezier": False,
        }
    )

    assert first["layer"]["name"] == second["layer"]["name"] == "Draft"
    assert first["glyph"]["width"] == second["glyph"]["width"] == 700
    assert first["layer_glyph"]["width"] == 400
    assert second["layer_glyph"]["width"] == 500
    assert first["compounded"] is False
    assert 'data-mode="solid"' in first["svg"]
    with pytest.raises(ValueError, match="process sequence"):
        render_request(
            {
                "font_path": str(source),
                "master": "M1",
                "glyph": "A",
                "layer_id": "OTHER",
            }
        )


def test_open_skeleton_stays_visible_with_bezier_disabled() -> None:
    catalog = catalog_request({"font_path": str(EXAMPLE), "glyph": "a"})
    skeleton = next(item for item in catalog["layers"] if item["is_skeleton"])

    result = render_request(
        {
            "font_path": str(EXAMPLE),
            "glyph": "a",
            "layer_id": skeleton["layer_id"],
            "bezier": False,
            "handles": False,
        }
    )
    root = ET.fromstring(result["svg"])

    assert root.get("data-mode") == "outline"
    assert root.find(".//svg:path[@class='native-outline']", SVG) is not None
    assert root.find(".//svg:g[@class='on-curve-nodes']", SVG) is None
    assert root.find(".//svg:g[@class='handle-lines']", SVG) is None


def test_handles_toggle_is_independent_inside_compounded_bezier_mode() -> None:
    catalog = catalog_request({"font_path": str(EXAMPLE), "glyph": "a"})
    final = catalog["layers"][-1]
    hidden = render_request(
        {
            "font_path": str(EXAMPLE),
            "glyph": "a",
            "layer_id": final["layer_id"],
            "bezier": True,
            "handles": False,
        }
    )
    shown = render_request(
        {
            "font_path": str(EXAMPLE),
            "glyph": "a",
            "layer_id": final["layer_id"],
            "bezier": True,
            "handles": True,
        }
    )

    assert hidden["compounded"] is True
    assert 'class="on-curve-nodes"' in hidden["svg"]
    assert 'class="handle-lines"' not in hidden["svg"]
    assert 'class="handle-lines"' in shown["svg"]
    assert re.search(r'data-handle="(?:in|out)"', shown["svg"])


def test_process_requires_editable_glyphs_source(tmp_path: Path) -> None:
    source = tmp_path / "font.ttf"
    source.write_bytes(b"not a font")
    with pytest.raises(ValueError, match=re.escape("editable .glyphs")):
        catalog_request({"font_path": str(source), "glyph": "A"})


def test_full_process_frame_matches_half_width_reference_geometry(
    tmp_path: Path,
) -> None:
    source = _layer_source(tmp_path)
    svg = render_process_frame_svg(
        {
            "font_path": str(source),
            "master": "M1",
            "glyph": "A",
            "layer_id": "S1",
            "point_size": 370,
            "bezier": False,
            "handles": False,
        }
    )
    root = ET.fromstring(svg)

    assert root.get("width") == "540"
    assert root.get("height") == "766"
    assert root.get("viewBox") == "0 0 540 766"
    assert root.get("data-layer-id") == "S1"
    assert root.get("data-final") == "false"
    assert "TYPEFACE: PROCESS DEMO" in svg
    assert "STYLE:    REGULAR" in svg
    assert "SIZE:     370 pt" in svg
    assert "|↔|:      700 upm" in svg
    assert 'd="M 19 245.5 H 522"' in svg
    assert 'transform="translate(18 256)"' in svg


def test_export_request_writes_current_layer_svg_and_complete_timed_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _layer_source(tmp_path)
    layer_output = tmp_path / "layer.svg"
    layer_result = export_request(
        {
            "font_path": str(source),
            "master": "M1",
            "glyph": "A",
            "layer_id": "D2",
            "format": "svg",
            "output_path": str(layer_output),
            "bezier": False,
            "handles": False,
        }
    )
    assert layer_output.is_file()
    assert layer_result["layer_id"] == "D2"
    assert layer_result["width"] == 540
    assert layer_result["height"] == 766

    captured = {}

    def fake_animation(frames, destination, **kwargs):
        captured["frames"] = list(frames)
        captured["destination"] = Path(destination)
        captured.update(kwargs)
        captured["destination"].write_bytes(b"video")
        return {
            "path": str(captured["destination"]),
            "name": captured["destination"].name,
            "content_type": "video/mp4",
            "format": "mp4",
            "frame_count": len(captured["frames"]),
            "final_hold_ms": 3000,
            "width": 1080,
            "height": 1532,
        }

    monkeypatch.setattr(
        "typedesignxray.process_export.export_process_animation",
        fake_animation,
    )
    animation_output = tmp_path / "process.mp4"
    animation_result = export_request(
        {
            "font_path": str(source),
            "master": "M1",
            "glyph": "A",
            "format": "mp4",
            "output_path": str(animation_output),
            "speed": 0.125,
            "bezier": False,
            "handles": False,
        }
    )

    assert [frame.layer_id for frame in captured["frames"]] == [
        "S1",
        "D1",
        "D2",
        "M1",
    ]
    assert [frame.is_master for frame in captured["frames"]] == [
        False,
        False,
        False,
        True,
    ]
    assert captured["frame_delay_ms"] == 125
    assert animation_result["final_hold_ms"] == 3000
    assert animation_result["width"] == 1080
    assert animation_result["height"] == 1532
