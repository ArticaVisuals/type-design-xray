from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from typedesignxray.cli import main


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "BlueprintDemo.glyphs"


def _run(tmp_path: Path, *arguments: str) -> Path:
    destination = tmp_path / "output.svg"
    result = main([str(EXAMPLE), *arguments, "--out", str(destination)])
    assert result == 0
    return destination


def test_writes_svg_that_parses_as_xml(tmp_path: Path) -> None:
    destination = _run(tmp_path, "AV")
    root = ET.parse(destination).getroot()
    assert root.tag.endswith("svg")


def test_list_layers_without_text(capsys: pytest.CaptureFixture[str]) -> None:
    result = main([str(EXAMPLE), "--list-layers", "a"])
    captured = capsys.readouterr()
    assert result == 0
    assert "Skeleton v1" in captured.out
    assert captured.err == ""


def test_named_layer_differs_from_default(tmp_path: Path) -> None:
    default = tmp_path / "default.svg"
    skeleton = tmp_path / "skeleton.svg"
    assert main([str(EXAMPLE), "a", "--out", str(default)]) == 0
    assert (
        main(
            [
                str(EXAMPLE),
                "a",
                "--layer",
                "Skeleton v1",
                "--out",
                str(skeleton),
            ]
        )
        == 0
    )
    assert default.read_text(encoding="utf-8") != skeleton.read_text(
        encoding="utf-8"
    )


def test_presets_produce_distinct_output(tmp_path: Path) -> None:
    light = tmp_path / "light.svg"
    blueprint = tmp_path / "blueprint.svg"
    assert (
        main(
            [
                str(EXAMPLE),
                "o",
                "--preset",
                "light",
                "--out",
                str(light),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                str(EXAMPLE),
                "o",
                "--preset",
                "blueprint",
                "--out",
                str(blueprint),
            ]
        )
        == 0
    )
    assert light.read_text(encoding="utf-8") != blueprint.read_text(
        encoding="utf-8"
    )


def test_generic_style_override_changes_handle_shape(tmp_path: Path) -> None:
    destination = _run(
        tmp_path,
        "o",
        "--set",
        "handles.point.shape=diamond",
    )
    root = ET.parse(destination).getroot()
    handle_layer = next(
        element
        for element in root.iter()
        if element.get("data-layer") == "handle_points"
    )
    assert any(
        element.get("data-shape") == "diamond"
        for element in handle_layer.iter()
    )


def test_no_handles_removes_both_handle_layers(tmp_path: Path) -> None:
    destination = _run(tmp_path, "o", "--no-handles")
    layer_names = {
        element.get("data-layer")
        for element in ET.parse(destination).getroot().iter()
    }
    assert "handle_lines" not in layer_names
    assert "handle_points" not in layer_names


def test_metrics_flag_emits_selected_metrics(tmp_path: Path) -> None:
    destination = _run(tmp_path, "A", "--metrics", "baseline,xheight")
    root = ET.parse(destination).getroot()
    metric_names = {
        element.get("data-metric")
        for element in root.iter()
        if element.get("data-metric") is not None
    }
    assert "baseline" in metric_names
    assert "xheight" in metric_names


def test_per_glyph_writes_full_run_and_each_glyph(tmp_path: Path) -> None:
    destination = tmp_path / "files"
    destination.mkdir()
    result = main(
        [
            str(EXAMPLE),
            "AV",
            "--per-glyph",
            "--out",
            str(destination),
        ]
    )
    assert result == 0
    files = sorted(destination.glob("*.svg"))
    assert len(files) == 3
    assert all(ET.parse(path).getroot().tag.endswith("svg") for path in files)


@pytest.mark.parametrize(
    "arguments, expected",
    [
        (("--preset", "does-not-exist"), "unknown preset"),
        (("--set", "handles.not_a_key=true"), "unknown style key"),
    ],
)
def test_style_errors_are_friendly(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    arguments: tuple,
    expected: str,
) -> None:
    destination = tmp_path / "error.svg"
    result = main(
        [str(EXAMPLE), "A", *arguments, "--out", str(destination)]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert expected in captured.err
    assert "Traceback" not in captured.err
    assert not destination.exists()


def test_missing_font_is_friendly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.glyphs"
    result = main([str(missing), "A"])
    captured = capsys.readouterr()
    assert result == 2
    assert "file not found" in captured.err
    assert "Traceback" not in captured.err


def test_huge_width_is_an_expected_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    destination = tmp_path / "huge.svg"
    result = main(
        [
            str(EXAMPLE),
            "A",
            "--width",
            "9" * 500,
            "--out",
            str(destination),
        ]
    )
    captured = capsys.readouterr()

    assert result == 2
    assert "int too large to convert to float" in captured.err
    assert "unexpected failure" not in captured.err
    assert "Traceback" not in captured.err
    assert not destination.exists()


def test_missing_raster_backend_keeps_svg(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typedesignxray.render import raster

    monkeypatch.setattr(raster, "_load_cairosvg", lambda: None)
    monkeypatch.setattr(raster.shutil, "which", lambda command: None)
    destination = tmp_path / "output.svg"
    result = main(
        [
            str(EXAMPLE),
            "A",
            "--format",
            "svg,png",
            "--out",
            str(destination),
        ]
    )
    captured = capsys.readouterr()
    assert result == 2
    assert destination.exists()
    assert not destination.with_suffix(".png").exists()
    assert 'pip install "type-design-xray[raster]"' in captured.err
    assert "Traceback" not in captured.err


def test_png_only_request_keeps_svg_when_raster_backend_is_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typedesignxray.render import raster

    monkeypatch.setattr(raster, "_load_cairosvg", lambda: None)
    monkeypatch.setattr(raster.shutil, "which", lambda command: None)
    destination = tmp_path / "output.png"

    result = main(
        [
            str(EXAMPLE),
            "A",
            "--format",
            "png",
            "--out",
            str(destination),
        ]
    )
    captured = capsys.readouterr()

    fallback = destination.with_suffix(".svg")
    assert result == 2
    assert fallback.exists()
    assert ET.parse(fallback).getroot().tag.endswith("svg")
    assert not destination.exists()
    assert "wrote {}".format(fallback) in captured.out
    assert 'pip install "type-design-xray[raster]"' in captured.err
    assert "Traceback" not in captured.err


def test_version_and_list_presets_exit_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--version"]) == 0
    version_output = capsys.readouterr()
    assert "1.2.5" in version_output.out

    assert main(["--list-presets"]) == 0
    preset_output = capsys.readouterr()
    assert "blueprint" in preset_output.out
    assert "light" in preset_output.out
