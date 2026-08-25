from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from typedesignxray import ir
from typedesignxray import specimen_export


def _rectangle(x: float = 0.0) -> ir.Contour:
    return ir.Contour(
        nodes=[
            ir.Node((x + 0.0, 0.0)),
            ir.Node((x + 100.0, 0.0)),
            ir.Node((x + 100.0, 200.0)),
            ir.Node((x + 0.0, 200.0)),
        ]
    )


def _font() -> ir.Font:
    glyphs = {
        "A": ir.Glyph(
            "A",
            600,
            contours=[_rectangle()],
            unicodes=[0x41],
            metrics=ir.Metrics(lsb=20, rsb=30),
        ),
        "a": ir.Glyph(
            "a",
            520,
            contours=[_rectangle(10)],
            unicodes=[0x61],
            metrics=ir.Metrics(lsb=10, rsb=40),
        ),
        # An empty source glyph must never appear in the exported animation.
        "space": ir.Glyph("space", 250, unicodes=[0x20]),
        # The odd final designed glyph must occupy one slot, not both.
        "star": ir.Glyph(
            "star", 500, contours=[_rectangle(20)], unicodes=[0x2605]
        ),
    }
    return ir.Font(
        glyphs=glyphs,
        cmap={0x20: "space", 0x41: "A", 0x61: "a", 0x2605: "star"},
        units_per_em=1000,
        metrics=ir.Metrics(
            baseline=0, x_height=450, cap_height=700, ascender=750, descender=-250
        ),
        family_name="Caliper Sans",
        master_name="Regular",
        source_format="glyphs",
    )


def test_designed_sequence_is_exhaustive_unique_and_leaves_odd_slot_blank() -> None:
    font = _font()

    assert specimen_export.designed_glyph_names(font) == ("A", "a", "star")
    assert specimen_export.designed_sequence(font) == (
        ("A", "a"),
        ("star", None),
    )

    names = [
        name
        for pair in specimen_export.designed_sequence(font)
        for name in pair
        if name is not None
    ]
    assert names == ["A", "a", "star"]
    assert len(names) == len(set(names))


def test_frame_matches_reference_dimensions_metadata_and_blank_panel() -> None:
    frame = specimen_export.render_frame_svg(
        _font(), "star", None, point_size=370, xray=False
    )
    root = ET.fromstring(frame)

    assert root.get("width") == "1080"
    assert root.get("height") == "766"
    assert root.get("data-left") == "star"
    assert root.get("data-right") == ""
    assert root.get("data-mode") == "solid"
    assert "TYPEFACE: CALIPER SANS" in frame
    assert "STYLE:    REGULAR" in frame
    assert "SIZE:     370 pt" in frame
    assert "NAME:     star" in frame
    assert "UNICODE:  2605" in frame
    assert 'data-glyph="star"' in frame
    assert frame.count('data-glyph="star"') == 1


def test_frame_xray_includes_native_nodes_and_handles() -> None:
    font = _font()
    first = font.glyphs["A"].contours[0].nodes[0]
    second = font.glyphs["A"].contours[0].nodes[1]
    first.handle_out = (25.0, 50.0)
    second.handle_in = (75.0, 50.0)

    frame = specimen_export.render_frame_svg(
        font, "A", "a", point_size=370, xray=True
    )

    assert 'data-mode="xray"' in frame
    assert 'class="native-outline"' in frame
    assert 'class="on-curve-nodes"' in frame
    assert 'class="handle-lines"' in frame
    assert 'data-handle="out"' in frame


@pytest.mark.parametrize(
    ("format_name", "content_type"),
    [("gif", "image/gif"), ("mp4", "video/mp4")],
)
def test_export_loads_only_selected_master_and_returns_route_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    format_name: str,
    content_type: str,
) -> None:
    calls = []
    compounded = []

    def fake_load_font(path: Path, *, master: str) -> ir.Font:
        calls.append((path, master))
        return _font()

    def fake_svg_to_png(svg: str, output: Path, width: int) -> Path:
        assert width == 1080
        assert 'width="1080" height="766"' in svg
        output.write_bytes(b"png")
        return output

    def fake_compound_glyph(glyph: ir.Glyph) -> ir.Glyph:
        compounded.append(glyph.name)
        return glyph

    def fake_encode(
        ffmpeg: str,
        pattern: Path,
        destination: Path,
        actual_format: str,
        fps: float,
    ) -> None:
        assert ffmpeg == "/fake/ffmpeg"
        assert pattern.name == "frame-%06d.png"
        assert actual_format == format_name
        assert fps == 8
        assert len(list(pattern.parent.glob("frame-*.png"))) == 2
        destination.write_bytes(b"animation")

    monkeypatch.setattr(specimen_export, "load_font", fake_load_font)
    monkeypatch.setattr(specimen_export, "compound_glyph", fake_compound_glyph)
    monkeypatch.setattr(specimen_export, "svg_to_png", fake_svg_to_png)
    monkeypatch.setattr(specimen_export, "_find_ffmpeg", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(specimen_export, "_encode_frames", fake_encode)
    source = tmp_path / "Caliper.glyphs"
    source.write_text("{}", encoding="utf-8")
    output = tmp_path / "exports" / "caliper.{}".format(format_name)

    result = specimen_export.export_specimen(
        source,
        output,
        master="M-REGULAR",
        format_name=format_name,
        fps=8,
        xray=True,
    )

    assert calls == [(source.resolve(), "M-REGULAR")]
    assert set(compounded) == {"A", "a", "star"}
    assert output.read_bytes() == b"animation"
    assert result == {
        "path": str(output.resolve()),
        "name": "caliper.{}".format(format_name),
        "content_type": content_type,
        "format": format_name,
        "family_name": "Caliper Sans",
        "master_name": "Regular",
        "glyph_count": 3,
        "frame_count": 2,
        "total_frame_count": 2,
        "start_frame": 1,
        "end_frame": 2,
        "point_size": 370.0,
        "fps": 8.0,
        "xray": True,
        "colors": {
            "background": "#000000",
            "fill": "#ffffff",
            "stroke": "#ffffff",
            "text": "#ffffff",
            "guides": "#737373",
            "handles": "#8e8e8e",
            "point_fill": "#000000",
            "point_stroke": "#ffffff",
        },
    }


def test_export_frame_writes_selected_svg_with_palette_and_compounding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "Caliper.glyphs"
    source.write_text("{}", encoding="utf-8")
    output = tmp_path / "frame.svg"
    compounded = []
    monkeypatch.setattr(
        specimen_export,
        "load_font",
        lambda path, *, master: _font(),
    )
    monkeypatch.setattr(
        specimen_export,
        "compound_glyph",
        lambda glyph: compounded.append(glyph.name) or glyph,
    )

    result = specimen_export.export_frame(
        source,
        output,
        frame_number=2,
        master="M-REGULAR",
        format_name="svg",
        xray=True,
        colors={
            "background": "#112233",
            "text": "#ddeeff",
            "stroke": "#ff00aa",
        },
    )

    svg = output.read_text(encoding="utf-8")
    assert compounded == ["star"]
    assert 'data-left="star"' in svg
    assert 'data-right=""' in svg
    assert 'fill="#112233"' in svg
    assert 'fill="#ddeeff"' in svg
    assert 'stroke="#ff00aa"' in svg
    assert result["format"] == "svg"
    assert result["start_frame"] == result["end_frame"] == 2
    assert result["total_frame_count"] == 2
    assert result["glyph_count"] == 1


def test_animation_export_accepts_an_inclusive_frame_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "Caliper.glyphs"
    source.write_text("{}", encoding="utf-8")
    output = tmp_path / "range.mp4"
    seen_frames = []
    monkeypatch.setattr(
        specimen_export,
        "load_font",
        lambda path, *, master: _font(),
    )
    monkeypatch.setattr(specimen_export, "_find_ffmpeg", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(
        specimen_export,
        "svg_to_png",
        lambda svg, output, width: seen_frames.append(svg)
        or output.write_bytes(b"png")
        or output,
    )
    monkeypatch.setattr(
        specimen_export,
        "_encode_frames",
        lambda ffmpeg, pattern, destination, format_name, fps: destination.write_bytes(
            b"video"
        ),
    )

    result = specimen_export.export_specimen(
        source,
        output,
        format_name="mp4",
        start_frame=2,
        end_frame=2,
    )

    assert len(seen_frames) == 1
    assert 'data-left="star"' in seen_frames[0]
    assert result["frame_count"] == 1
    assert result["total_frame_count"] == 2
    assert result["start_frame"] == result["end_frame"] == 2

    with pytest.raises(ValueError, match="end_frame"):
        specimen_export.export_specimen(
            source,
            output,
            format_name="mp4",
            start_frame=2,
            end_frame=1,
        )


def test_export_reports_missing_ffmpeg_before_raster_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "Caliper.glyphs"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(specimen_export, "load_font", lambda path, *, master: _font())
    monkeypatch.setattr(specimen_export, "_find_ffmpeg", lambda: None)
    monkeypatch.setattr(
        specimen_export,
        "svg_to_png",
        lambda *args, **kwargs: pytest.fail("rasterizer must not run"),
    )

    with pytest.raises(
        specimen_export.SpecimenExportError,
        match="ffmpeg is required.*not found",
    ):
        specimen_export.export_specimen(source, tmp_path / "out.gif")


def test_export_wraps_missing_raster_backend_as_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "Caliper.glyphs"
    source.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(specimen_export, "load_font", lambda path, *, master: _font())
    monkeypatch.setattr(specimen_export, "_find_ffmpeg", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(
        specimen_export,
        "svg_to_png",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("No SVG backend is available for PNG export")
        ),
    )

    with pytest.raises(
        specimen_export.SpecimenExportError,
        match="Unable to rasterize.*No SVG backend",
    ):
        specimen_export.export_specimen(source, tmp_path / "out.mp4")


def test_export_request_keeps_route_output_inside_owned_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def fake_export(font_path: str, output_path: Path, **kwargs: object) -> dict:
        captured.update(
            font_path=font_path, output_path=output_path, kwargs=kwargs
        )
        return {"path": str(output_path)}

    monkeypatch.setattr(specimen_export, "export_specimen", fake_export)
    result = specimen_export.export_request(
        {
            "font_path": "/fonts/Caliper.glyphs",
            "master": "Regular",
            "format": "mp4",
            "output_name": "caliper-specimen.mp4",
            "bezier": True,
        },
        output_dir=tmp_path,
    )

    assert result == {"path": str(tmp_path / "caliper-specimen.mp4")}
    assert captured["output_path"] == tmp_path / "caliper-specimen.mp4"
    assert captured["kwargs"]["master"] == "Regular"
    assert captured["kwargs"]["xray"] is True
    with pytest.raises(ValueError, match="filename, not a path"):
        specimen_export.export_request(
            {
                "font_path": "/fonts/Caliper.glyphs",
                "output_name": "../escape.gif",
            },
            output_dir=tmp_path,
        )
