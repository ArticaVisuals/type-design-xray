from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from typedesignxray import process_export
from typedesignxray.render.raster import available_backends


def _svg(color: str = "#ffffff") -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="540" height="766" '
        'viewBox="0 0 540 766">'
        '<rect width="540" height="766" fill="{}"/>'
        "</svg>"
    ).format(color)


def test_process_dimensions_and_fixed_final_hold_are_public() -> None:
    assert process_export.FRAME_WIDTH == 540
    assert process_export.FRAME_HEIGHT == 766
    assert process_export.ANIMATION_WIDTH == 1080
    assert process_export.ANIMATION_HEIGHT == 1532
    assert process_export.FINAL_HOLD_MS == 3000


def test_export_frame_writes_svg_and_rasterizes_png_at_logical_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svg_output = tmp_path / "layer.svg"
    svg_result = process_export.export_process_frame(_svg(), svg_output)
    assert svg_output.read_text(encoding="utf-8") == _svg()
    assert svg_result["width"] == 540
    assert svg_result["height"] == 766
    assert svg_result["content_type"] == "image/svg+xml"

    seen = []

    def fake_png(svg: str, output: Path, width: int) -> Path:
        seen.append((svg, width))
        output.write_bytes(b"png")
        return output

    monkeypatch.setattr(process_export, "svg_to_png", fake_png)
    png_output = tmp_path / "layer.png"
    png_result = process_export.export_process_frame(_svg("#112233"), png_output)
    assert png_output.read_bytes() == b"png"
    assert seen == [(_svg("#112233"), 540)]
    assert png_result["content_type"] == "image/png"


def test_animation_accepts_layer_records_and_renderer_with_variable_timing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = [
        {"layer": "Skeleton", "color": "#111111", "is_master": False},
        {"layer": "Regular", "color": "#eeeeee", "is_master": True},
    ]
    rendered = []
    rasterized = []

    def renderer(record: dict) -> str:
        rendered.append(record["layer"])
        return _svg(record["color"])

    def fake_png(svg: str, output: Path, width: int) -> Path:
        rasterized.append((svg, width))
        output.write_bytes(b"png")
        return output

    def fake_encode(
        ffmpeg: str,
        manifest: Path,
        destination: Path,
        format_name: str,
    ) -> None:
        assert ffmpeg == "/fake/ffmpeg"
        assert format_name == "mp4"
        assert manifest.read_text(encoding="utf-8").splitlines() == [
            "ffconcat version 1.0",
            "file frame-000000.png",
            "option framerate 1000",
            "duration 0.125",
            "file frame-000001.png",
            "option framerate 1000",
            "duration 2.999",
            "file frame-000001.png",
            "option framerate 1000",
        ]
        destination.write_bytes(b"video")

    monkeypatch.setattr(process_export, "svg_to_png", fake_png)
    monkeypatch.setattr(process_export, "_find_ffmpeg", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(process_export, "_encode_timed_frames", fake_encode)
    output = tmp_path / "process.mp4"

    result = process_export.export_process_animation(
        records,
        output,
        renderer=renderer,
        frame_delay_ms=125,
    )

    assert rendered == ["Skeleton", "Regular"]
    assert [width for _, width in rasterized] == [1080, 1080]
    assert output.read_bytes() == b"video"
    assert result == {
        "path": str(output.resolve()),
        "name": "process.mp4",
        "content_type": "video/mp4",
        "format": "mp4",
        "frame_count": 2,
        "master_frame": 2,
        "frame_delay_ms": 125.0,
        "final_hold_ms": 3000.0,
        "frame_durations_ms": [125.0, 3000.0],
        "duration_ms": 3125.0,
        "width": 1080,
        "height": 1532,
    }


@pytest.mark.parametrize(
    ("format_name", "content_type"),
    [("gif", "image/gif"), ("mp4", "video/mp4")],
)
def test_animation_accepts_simple_svg_sequence_and_assumes_last_is_master(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    format_name: str,
    content_type: str,
) -> None:
    monkeypatch.setattr(process_export, "_find_ffmpeg", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(
        process_export,
        "svg_to_png",
        lambda svg, output, width: output.write_bytes(b"png") or output,
    )
    monkeypatch.setattr(
        process_export,
        "_encode_timed_frames",
        lambda ffmpeg, manifest, destination, actual_format: (
            destination.write_bytes(b"animation")
        ),
    )
    output = tmp_path / "process.{}".format(format_name)

    result = process_export.export_process_animation(
        [_svg("#111111"), _svg("#222222"), _svg("#333333")],
        output,
        format_name=format_name,
        frame_delay_ms=200,
    )

    assert result["content_type"] == content_type
    assert result["frame_durations_ms"] == [200.0, 200.0, 3000.0]
    assert result["duration_ms"] == 3400.0


def test_animation_requires_identified_master_to_be_exactly_once_and_last(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="exactly one active master"):
        process_export.export_process_animation(
            [
                process_export.ProcessFrame(_svg(), is_master=False),
                process_export.ProcessFrame(_svg(), is_master=False),
            ],
            tmp_path / "none.gif",
        )
    with pytest.raises(ValueError, match="must be the final"):
        process_export.export_process_animation(
            [
                process_export.ProcessFrame(_svg(), is_master=True),
                process_export.ProcessFrame(_svg(), is_master=False),
            ],
            tmp_path / "early.gif",
        )
    with pytest.raises(ValueError, match="exactly one active master"):
        process_export.export_process_animation(
            [
                process_export.ProcessFrame(_svg(), is_master=True),
                process_export.ProcessFrame(_svg(), is_master=True),
            ],
            tmp_path / "two.gif",
        )


def test_process_frame_validation_rejects_wrong_canvas_and_bad_delay(
    tmp_path: Path,
) -> None:
    wrong = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 766"/>'
    with pytest.raises(ValueError, match="viewBox must be 0 0 540 766"):
        process_export.export_process_frame(wrong, tmp_path / "wrong.svg")
    with pytest.raises(ValueError, match="frame_delay_ms"):
        process_export.export_process_animation(
            [_svg()], tmp_path / "bad.gif", frame_delay_ms=0
        )


def test_missing_ffmpeg_is_reported_before_raster_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(process_export, "_find_ffmpeg", lambda: None)
    monkeypatch.setattr(
        process_export,
        "svg_to_png",
        lambda *args, **kwargs: pytest.fail("rasterizer must not run"),
    )
    with pytest.raises(
        process_export.ProcessExportError,
        match="ffmpeg is required.*not found",
    ):
        process_export.export_process_animation([_svg()], tmp_path / "out.gif")


def test_ffmpeg_commands_use_concat_vfr_and_format_specific_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands = []
    monkeypatch.setattr(
        process_export, "_run_ffmpeg", lambda command: commands.append(list(command))
    )
    manifest = tmp_path / "frames.ffconcat"
    manifest.write_text("ffconcat version 1.0\n", encoding="utf-8")

    process_export._encode_timed_frames(
        "/ffmpeg", manifest, tmp_path / "out.gif", "gif"
    )
    process_export._encode_timed_frames(
        "/ffmpeg", manifest, tmp_path / "out.mp4", "mp4"
    )

    gif, mp4 = commands
    assert gif[gif.index("-f") + 1] == "concat"
    assert gif[gif.index("-vsync") + 1] == "vfr"
    assert "palettegen=stats_mode=diff" in gif[gif.index("-filter_complex") + 1]
    assert gif[gif.index("-loop") + 1] == "0"
    assert gif[gif.index("-final_delay") + 1] == "1"
    assert "-t" not in gif
    assert mp4[mp4.index("-f") + 1] == "concat"
    assert mp4[mp4.index("-vsync") + 1] == "vfr"
    assert mp4[mp4.index("-c:v") + 1] == "libx264"
    assert mp4[mp4.index("-bf") + 1] == "0"
    assert mp4[mp4.index("-pix_fmt") + 1] == "yuv420p"
    assert "-t" not in mp4


_HAS_REAL_VIDEO_STACK = bool(
    shutil.which("ffmpeg") and shutil.which("ffprobe") and available_backends()
)


@pytest.mark.skipif(
    not _HAS_REAL_VIDEO_STACK,
    reason="ffmpeg, ffprobe, and an SVG raster backend are required",
)
def test_real_mp4_has_2x_dimensions_and_expected_variable_duration(
    tmp_path: Path,
) -> None:
    output = tmp_path / "timed.mp4"
    result = process_export.export_process_animation(
        [_svg("#112233"), _svg("#778899"), _svg("#ddeeff")],
        output,
        format_name="mp4",
        frame_delay_ms=200,
    )
    probe = subprocess.run(
        [
            str(shutil.which("ffprobe")),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(output),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        text=True,
    )
    metadata = json.loads(probe.stdout)
    video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
    duration = float(metadata["format"]["duration"])

    assert video["width"] == result["width"] == 1080
    assert video["height"] == result["height"] == 1532
    assert duration == pytest.approx(result["duration_ms"] / 1000.0, abs=0.05)


@pytest.mark.skipif(
    not _HAS_REAL_VIDEO_STACK,
    reason="ffmpeg, ffprobe, and an SVG raster backend are required",
)
def test_real_looping_gif_matches_live_130ms_timing(tmp_path: Path) -> None:
    output = tmp_path / "timed.gif"
    result = process_export.export_process_animation(
        [_svg("#112233"), _svg("#778899"), _svg("#ddeeff")],
        output,
        format_name="gif",
        frame_delay_ms=130,
    )
    probe = subprocess.run(
        [
            str(shutil.which("ffprobe")),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(output),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        text=True,
    )
    duration = float(json.loads(probe.stdout)["format"]["duration"])

    assert output.read_bytes().find(b"NETSCAPE2.0") >= 0
    assert duration == pytest.approx(result["duration_ms"] / 1000.0, abs=0.011)
