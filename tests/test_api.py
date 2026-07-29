from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from typedesignxray import ir
from typedesignxray import api
from typedesignxray.api import blueprint_to_files
from typedesignxray.style import Style


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "BlueprintDemo.glyphs"


def test_per_glyph_file_names_do_not_repeat_hyphenated_run_text(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "lockup.svg"

    paths = blueprint_to_files(
        EXAMPLE,
        "/A/V",
        destination,
        formats=("svg",),
        per_glyph=True,
    )

    assert [path.name for path in paths] == [
        "lockup.svg",
        "lockup-01-A.svg",
        "lockup-02-V.svg",
    ]
    assert all(ET.parse(path).getroot().tag.endswith("svg") for path in paths)


def test_repeated_glyph_names_are_disambiguated(tmp_path: Path) -> None:
    paths = blueprint_to_files(
        EXAMPLE,
        "AA",
        tmp_path,
        formats=("svg",),
        per_glyph=True,
    )

    assert [path.name for path in paths] == [
        "AA.svg",
        "AA-01-A.svg",
        "AA-02-A-2.svg",
    ]


def test_explicit_file_remains_a_file_for_multiple_per_glyph_formats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typedesignxray.render import raster

    def write_png(svg: str, path: Path, width: int) -> Path:
        destination = Path(path)
        destination.write_bytes(b"PNG")
        return destination

    monkeypatch.setattr(raster, "svg_to_png", write_png)
    destination = tmp_path / "lockup.svg"

    paths = blueprint_to_files(
        EXAMPLE,
        "/A/V",
        destination,
        formats=("svg", "png"),
        per_glyph=True,
    )

    assert [path.name for path in paths] == [
        "lockup.svg",
        "lockup.png",
        "lockup-01-A.svg",
        "lockup-01-A.png",
        "lockup-02-V.svg",
        "lockup-02-V.png",
    ]
    assert destination.is_file()
    assert all(path.is_file() for path in paths)


def test_format_string_is_normalised_deduplicated_and_keeps_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typedesignxray.render import raster

    widths = []

    def write_png(svg: str, path: Path, width: int) -> Path:
        destination = Path(path)
        destination.write_bytes(b"PNG")
        widths.append(width)
        return destination

    monkeypatch.setattr(raster, "svg_to_png", write_png)

    paths = blueprint_to_files(
        EXAMPLE,
        "A",
        tmp_path / "lockup.svg",
        formats=".PNG, SVG, png",
        png_width=321,
    )

    assert [path.name for path in paths] == ["lockup.png", "lockup.svg"]
    assert widths == [321]
    assert paths[0].read_bytes() == b"PNG"
    assert ET.parse(paths[1]).getroot().tag.endswith("svg")


def test_sanitised_glyph_name_collisions_are_disambiguated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    glyphs = [
        ir.Glyph(name="a/b", advance_width=500),
        ir.Glyph(name="a?b", advance_width=500),
    ]
    layout = ir.Layout(
        glyphs=[
            ir.PositionedGlyph(glyph=glyph, origin_x=index * 500)
            for index, glyph in enumerate(glyphs)
        ],
        total_advance=1000,
    )
    style = Style()
    monkeypatch.setattr(
        api,
        "_prepare_blueprint",
        lambda *args, **kwargs: (
            ir.Font(),
            layout,
            style,
            "<svg xmlns='http://www.w3.org/2000/svg'/>",
        ),
    )

    paths = blueprint_to_files(
        "unused.glyphs",
        "ab",
        tmp_path / "lockup.svg",
        per_glyph=True,
    )

    assert [path.name for path in paths] == [
        "lockup.svg",
        "lockup-01-a-b.svg",
        "lockup-02-a-b-2.svg",
    ]


@pytest.mark.parametrize(
    "filename",
    [
        "CON.svg",
        "aux.preview.svg",
        "COM¹.svg",
        "bad:name.svg",
        "trailing.svg.",
    ],
)
def test_explicit_output_filename_must_be_portable_to_windows(
    tmp_path: Path,
    filename: str,
) -> None:
    destination = tmp_path / filename

    with pytest.raises(ValueError, match="Windows"):
        blueprint_to_files(EXAMPLE, "A", destination)

    assert not destination.exists()
