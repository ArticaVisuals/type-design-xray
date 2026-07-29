from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Optional

import pytest

from typedesignxray import config
from typedesignxray import style
from typedesignxray.render import raster


PRESET_NAMES = {"blueprint", "light", "contrast", "drafting"}


def test_shipped_presets_load_and_validate() -> None:
    assert PRESET_NAMES <= set(config.available_presets())
    for name in PRESET_NAMES:
        style.Style.from_dict(config.load_preset(name))


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


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("empty.json", ""),
        ("empty-object.json", "{}"),
        ("list.json", "[]"),
        ("broken.json", '{"canvas":'),
        ("empty.toml", ""),
        ("broken.toml", "[canvas\nwidth = 100"),
    ],
)
def test_invalid_or_empty_config_names_the_file(
    tmp_path: Path, name: str, body: str
) -> None:
    config_path = tmp_path / name
    config_path.write_text(body, encoding="utf-8")

    with pytest.raises(ValueError) as error:
        config.load_config(config_path)

    assert str(config_path) in str(error.value)


def test_unknown_config_preset_names_the_config_file(tmp_path: Path) -> None:
    config_path = tmp_path / "unknown-preset.json"
    config_path.write_text(
        json.dumps({"preset": "does-not-exist"}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        config.resolve_style(config_path=config_path)

    message = str(error.value)
    assert str(config_path) in message
    assert "unknown preset 'does-not-exist'" in message


def test_null_config_preset_is_not_silently_ignored(tmp_path: Path) -> None:
    config_path = tmp_path / "null-preset.json"
    config_path.write_text(
        json.dumps({"preset": None, "outline": {"width": 2}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must be a non-empty preset name"):
        config.resolve_style(config_path=config_path)


def test_override_syntax_rejects_missing_parts_and_sections() -> None:
    for override in ("outline.width", "=value", "outline.stroke="):
        with pytest.raises(ValueError):
            config.resolve_style(overrides=[override])

    with pytest.raises(ValueError) as error:
        config.resolve_style(overrides=["handles=x"])
    assert "'handles' names a section" in str(error.value)


def test_override_value_may_contain_an_equals_sign() -> None:
    resolved = config.resolve_style(
        overrides=["metrics.label_family=First,Font=Fallback"]
    )

    assert resolved.metrics.label_family == "First,Font=Fallback"


@pytest.mark.parametrize(
    ("override", "path"),
    [
        ("handles.point.size=-1", "handles.point.size"),
        ("outline.width=-2", "outline.width"),
        ("canvas.width=0", "canvas.width"),
        ("canvas.padding=700", "canvas.padding"),
        ("outline.opacity=5", "outline.opacity"),
        ("handles.point.shape=hexagon", "handles.point.shape"),
        ("handles.line.dash=wiggly", "handles.line.dash"),
        ("canvas.frame=page", "canvas.frame"),
        ("metrics.show=baseline,typo", "metrics.show"),
        ("metrics.label_letter_spacing=nan", "metrics.label_letter_spacing"),
        ("metrics.label_letter_spacing=inf", "metrics.label_letter_spacing"),
        ("outline.linecap=nope", "outline.linecap"),
        ("handles.line.linejoin=nope", "handles.line.linejoin"),
        ("metrics.label_style=nope", "metrics.label_style"),
        ("metrics.label_weight=heavy", "metrics.label_weight"),
    ],
)
def test_nonsensical_style_values_are_rejected(
    override: str, path: str
) -> None:
    with pytest.raises(ValueError) as error:
        config.resolve_style(overrides=[override])

    assert path in str(error.value)


def test_extreme_integer_style_value_is_reported_as_invalid() -> None:
    with pytest.raises(ValueError) as error:
        config.resolve_style(overrides={"canvas": {"width": 10**10000}})

    assert "canvas.width" in str(error.value)


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
        assert 'pip install "type-design-xray[raster]"' in message
        assert "brew install cairo" in message


def test_backend_discovery_never_propagates_probe_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_import(name: str) -> None:
        raise ImportError(name)

    def fail_which(command: str) -> None:
        raise OSError(command)

    monkeypatch.setattr(
        raster.importlib,
        "import_module",
        fail_import,
    )
    monkeypatch.setattr(
        raster.shutil,
        "which",
        fail_which,
    )

    assert raster.available_backends() == []


def test_resvg_png_command_includes_width_before_input_and_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = []
    destination = tmp_path / "image.png"
    monkeypatch.setattr(raster, "_load_cairosvg", lambda: None)
    monkeypatch.setattr(
        raster,
        "_find_command",
        lambda name: "/usr/bin/resvg" if name == "resvg" else None,
    )
    monkeypatch.setattr(
        raster,
        "_run_backend",
        lambda command, svg, backend: calls.append((command, svg, backend)),
    )

    raster.svg_to_png("<svg/>", destination, width=640)

    command, payload, backend = calls[0]
    assert command[:3] == ["/usr/bin/resvg", "--width", "640"]
    assert command[-1] == str(destination)
    assert Path(command[-2]).suffix == ".svg"
    assert payload == b""
    assert backend == "resvg"


@pytest.mark.parametrize(
    ("exporter_name", "format_name", "width"),
    [
        ("svg_to_png", "png", 800),
        ("svg_to_pdf", "pdf", None),
    ],
)
def test_rsvg_convert_commands_use_stdin_and_explicit_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exporter_name: str,
    format_name: str,
    width: Optional[int],
) -> None:
    calls = []
    destination = tmp_path / "image.{}".format(format_name)
    monkeypatch.setattr(raster, "_load_cairosvg", lambda: None)
    monkeypatch.setattr(
        raster,
        "_find_command",
        lambda name: (
            "/usr/bin/rsvg-convert" if name == "rsvg-convert" else None
        ),
    )
    monkeypatch.setattr(
        raster,
        "_run_backend",
        lambda command, svg, backend: calls.append((command, svg, backend)),
    )

    exporter = getattr(raster, exporter_name)
    if width is None:
        exporter("<svg/>", destination)
    else:
        exporter("<svg/>", destination, width=width)

    command, payload, backend = calls[0]
    assert command[:5] == [
        "/usr/bin/rsvg-convert",
        "--format",
        format_name,
        "--output",
        str(destination),
    ]
    if width is not None:
        assert command[-2:] == ["--width", str(width)]
    assert payload == b"<svg/>"
    assert backend == "rsvg-convert"
