from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from glyphblueprint import config
from glyphblueprint import style
from glyphblueprint.render import raster


PRESET_NAMES = {"blueprint", "light", "contrast", "drafting"}


def test_shipped_presets_load_validate_and_match_repo_copies() -> None:
    package_directory = Path(config.__file__).with_name("presets")
    repo_directory = Path(__file__).parents[1] / "presets"

    assert PRESET_NAMES <= set(config.available_presets())
    for name in PRESET_NAMES:
        preset = config.load_preset(name)
        style.Style.from_dict(preset)
        assert (package_directory / "{}.json".format(name)).read_bytes() == (
            repo_directory / "{}.json".format(name)
        ).read_bytes()


def test_json_and_toml_configs_resolve_identically(tmp_path: Path) -> None:
    if (
        importlib.util.find_spec("tomllib") is None
        and importlib.util.find_spec("tomli") is None
    ):
        pytest.skip("no TOML reader is importable")

    json_path = tmp_path / "style.json"
    toml_path = tmp_path / "style.toml"
    json_path.write_text(
        json.dumps(
            {
                "preset": "light",
                "canvas": {"padding": 72},
                "outline": {"width": 2.25},
                "metrics": {"visible": True},
            }
        ),
        encoding="utf-8",
    )
    toml_path.write_text(
        "\n".join(
            [
                'preset = "light"',
                "[canvas]",
                "padding = 72",
                "[outline]",
                "width = 2.25",
                "[metrics]",
                "visible = true",
            ]
        ),
        encoding="utf-8",
    )

    assert config.resolve_style(config_path=json_path).to_dict() == (
        config.resolve_style(config_path=toml_path).to_dict()
    )


def test_merge_order_defaults_preset_config_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "style.json"
    config_path.write_text(
        json.dumps({"preset": "contrast", "outline": {"width": 2.6}}),
        encoding="utf-8",
    )

    assert style.Style().outline.width == 1.6
    assert config.resolve_style(preset="contrast").outline.width == 3.2
    assert config.resolve_style(config_path=config_path).outline.width == 2.6
    resolved = config.resolve_style(
        config_path=config_path,
        overrides=["outline.width=4.4"],
    )
    assert resolved.outline.width == 4.4
    assert resolved.preset_name == "contrast"


def test_unknown_key_suggests_closest_dotted_path(tmp_path: Path) -> None:
    config_path = tmp_path / "mistyped.json"
    config_path.write_text(
        json.dumps({"handles": {"point": {"sze": 6}}}),
        encoding="utf-8",
    )

    with pytest.raises(KeyError) as error:
        config.resolve_style(config_path=config_path)
    message = str(error.value)
    assert "handles.point.sze" in message
    assert "handles.point.size" in message


def test_cli_overrides_are_coerced_and_none_is_optional() -> None:
    assert config.parse_override("handles.point.size=6") == (
        "handles.point.size",
        "6",
    )
    resolved = config.resolve_style(
        overrides=[
            "handles.point.size=6",
            "canvas.background=none",
            "canvas.png_width=None",
        ]
    )
    assert resolved.handles.point.size == 6.0
    assert resolved.canvas.background is None
    assert resolved.canvas.png_width is None


def test_missing_raster_backends_raise_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(raster, "_load_cairosvg", lambda: None)
    monkeypatch.setattr(raster.shutil, "which", lambda command: None)

    assert raster.available_backends() == []
    for exporter, destination in (
        (raster.svg_to_png, tmp_path / "nested" / "image.png"),
        (raster.svg_to_pdf, tmp_path / "nested" / "image.pdf"),
    ):
        with pytest.raises(RuntimeError) as error:
            exporter("<svg xmlns='http://www.w3.org/2000/svg'/>", destination)
        message = str(error.value)
        assert 'pip install "glyphblueprint[raster]"' in message
        assert "brew install cairo" in message
