from __future__ import annotations

import errno
import json
import re
import socket
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import pytest

from typedesignxray.api import blueprint
from typedesignxray.config import available_presets, resolve_style
from typedesignxray.style import METRIC_NAMES
from typedesignxray.web import (
    _MAX_UPLOAD_BYTES,
    _UPLOAD_DIRECTORY,
    _preview_page,
    PreviewHandler,
    main,
    render_request,
)


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "BlueprintDemo.glyphs"


def _payload(**changes):
    payload = {
        "font_path": str(EXAMPLE),
        "text": "Vao",
        "preset": "blueprint",
        "shape": "diamond",
        "frame": "auto",
        "width": 900,
        "tracking": 0,
        "layer": "",
        "compound": False,
        "metrics": True,
        "apply_kerning": True,
    }
    payload.update(changes)
    return payload


def _exchange(request):
    """Exercise one raw HTTP request without binding a network port."""
    server_side, client_side = socket.socketpair()
    thread = threading.Thread(
        target=PreviewHandler,
        args=(server_side, ("local", 0), object()),
    )
    try:
        thread.start()
        client_side.sendall(request)
        client_side.shutdown(socket.SHUT_WR)
        reader = client_side.makefile("rb")
        status_line = reader.readline().decode("ascii")
        headers = {}
        while True:
            line = reader.readline()
            if line in (b"\r\n", b""):
                break
            key, value = line.decode("latin-1").split(":", 1)
            headers[key.lower()] = value.strip()
        response_body = reader.read(int(headers["content-length"]))
        reader.close()
        thread.join(timeout=5)
        assert not thread.is_alive()
    finally:
        server_side.close()
        client_side.close()
    status = int(status_line.split()[1])
    return status, headers, response_body


def _post_upload(body, filename, content_length=None):
    """Exercise one raw HTTP upload without binding a network port."""
    length = len(body) if content_length is None else content_length
    request = (
        "POST /api/upload HTTP/1.0\r\n"
        "Host: localhost\r\n"
        "Connection: close\r\n"
        "Content-Type: application/octet-stream\r\n"
        "X-Filename: {}\r\n"
        "Content-Length: {}\r\n"
        "\r\n"
    ).format(quote(filename, safe=""), length).encode("ascii") + body
    status, _, response_body = _exchange(request)
    return status, json.loads(response_body.decode("utf-8"))


def _get(path, parse_json=True):
    request = (
        "GET {} HTTP/1.0\r\n"
        "Host: localhost\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).format(path).encode("ascii")
    status, _, response_body = _exchange(request)
    decoded = response_body.decode("utf-8")
    return status, json.loads(decoded) if parse_json else decoded


def _post_render(payload):
    body = json.dumps(payload).encode("utf-8")
    request = (
        "POST /api/render HTTP/1.0\r\n"
        "Host: localhost\r\n"
        "Connection: close\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: {}\r\n"
        "\r\n"
    ).format(len(body)).encode("ascii") + body
    status, _, response_body = _exchange(request)
    return status, json.loads(response_body.decode("utf-8"))


def test_fonts_endpoint_returns_sorted_visible_family_names() -> None:
    status, result = _get("/api/fonts")

    assert status == 200
    families = result["families"]
    assert isinstance(families, list)
    assert all(isinstance(family, str) for family in families)
    assert all(not family.startswith(".") for family in families)
    assert families == sorted(
        families,
        key=lambda family: (family.casefold(), family),
    )


def test_upload_round_trip_can_render_the_uploaded_font() -> None:
    source = EXAMPLE.read_bytes()

    status, uploaded = _post_upload(source, EXAMPLE.name)

    assert status == 200
    assert uploaded["name"] == EXAMPLE.name
    uploaded_path = Path(uploaded["font_path"])
    assert uploaded_path.is_absolute()
    assert uploaded_path.read_bytes() == source
    result = render_request(
        _payload(font_path=uploaded["font_path"], text="Ao")
    )
    assert result["summary"]["glyphs"] == 2
    ET.fromstring(result["svg"])


def test_upload_rejects_disallowed_extension() -> None:
    status, result = _post_upload(b"not a font", "BlueprintDemo.txt")

    assert status == 400
    assert "unsupported upload extension '.txt'" in result["error"]


def test_upload_rejects_body_over_64_mb_without_reading_it() -> None:
    status, result = _post_upload(
        b"x",
        "too-large.glyphs",
        content_length=_MAX_UPLOAD_BYTES + 1,
    )

    assert status == 413
    assert "64 MB" in result["error"]


def test_upload_strips_traversal_components_from_filename() -> None:
    status, uploaded = _post_upload(
        EXAMPLE.read_bytes(),
        "../../evil.glyphs",
    )

    assert status == 200
    uploaded_path = Path(uploaded["font_path"]).resolve()
    assert uploaded["name"] == "evil.glyphs"
    assert uploaded_path.name == "evil.glyphs"
    assert uploaded_path.parent == _UPLOAD_DIRECTORY
    assert uploaded_path.read_bytes() == EXAMPLE.read_bytes()


def test_render_request_returns_valid_svg_and_summary() -> None:
    result = render_request(_payload())
    root = ET.fromstring(result["svg"])

    assert root.tag.endswith("svg")
    assert root.get("width") == "900"
    assert result["summary"]["glyphs"] == 3
    assert result["summary"]["nodes"] > 0
    assert any(
        element.get("data-shape") == "diamond"
        for element in root.iter()
    )


_COLOR_CASES = (
    ("canvas.background", "background", "rect", "fill", None),
    ("outline.stroke", "outline", "path", "stroke", None),
    ("outline.fill", "fill", "path", "fill", None),
    ("handles.line.color", "handle_lines", "line", "stroke", None),
    ("handles.point.fill", "handle_points", "circle", "fill", None),
    ("handles.point.stroke", "handle_points", "circle", "stroke", None),
    ("nodes.corner.fill", "nodes", "rect", "fill", "corner"),
    ("nodes.corner.stroke", "nodes", "rect", "stroke", "corner"),
    ("nodes.smooth.fill", "nodes", "circle", "fill", "smooth"),
    ("nodes.smooth.stroke", "nodes", "circle", "stroke", "smooth"),
    ("metrics.line.color", "metrics", "line", "stroke", None),
    ("metrics.label_color", "metrics", "text", "fill", None),
)

_SIZE_CASES = (
    (
        "handles.point.size",
        11.25,
        "handle_points",
        "circle",
        "r",
        None,
        None,
    ),
    (
        "handles.point.stroke_width",
        5.5,
        "handle_points",
        "circle",
        "stroke-width",
        None,
        None,
    ),
    (
        "nodes.corner.size",
        11.25,
        "nodes",
        "rect",
        "width",
        "corner",
        None,
    ),
    (
        "nodes.smooth.size",
        11.25,
        "nodes",
        "circle",
        "r",
        "smooth",
        None,
    ),
    (
        "nodes.corner.stroke_width",
        5.5,
        "nodes",
        "rect",
        "stroke-width",
        "corner",
        None,
    ),
    (
        "nodes.smooth.stroke_width",
        5.5,
        "nodes",
        "circle",
        "stroke-width",
        "smooth",
        None,
    ),
    (
        "outline.width",
        7.5,
        "outline",
        "path",
        "stroke-width",
        None,
        None,
    ),
    (
        "handles.line.width",
        5.5,
        "handle_lines",
        "line",
        "stroke-width",
        None,
        None,
    ),
    (
        "metrics.line.width",
        5.5,
        "metrics",
        "line",
        "stroke-width",
        None,
        "baseline",
    ),
)

_SLIDER_RANGES = {
    "handles.point.size": ("0", "12", "0.25"),
    "nodes.corner.size": ("0", "12", "0.25"),
    "nodes.smooth.size": ("0", "12", "0.25"),
    "handles.point.stroke_width": ("0", "6", "0.1"),
    "nodes.corner.stroke_width": ("0", "6", "0.1"),
    "nodes.smooth.stroke_width": ("0", "6", "0.1"),
    "outline.width": ("0", "8", "0.1"),
    "handles.line.width": ("0", "6", "0.1"),
    "metrics.line.width": ("0", "6", "0.1"),
}


def _elements_in_layer(root: ET.Element, layer_name: str):
    for layer in root.iter():
        if layer.get("data-layer") == layer_name:
            yield from layer.iter()


def _metric_elements(root: ET.Element, tag: str):
    return [
        element
        for element in _elements_in_layer(root, "metrics")
        if element.tag.split("}")[-1] == tag
        and element.get("data-metric") is not None
    ]


def test_metric_lines_can_render_without_numbers() -> None:
    result = render_request(
        _payload(metric_lines=True, metric_numbers=False)
    )
    root = ET.fromstring(result["svg"])

    assert _metric_elements(root, "line")
    assert not any(
        element.tag.split("}")[-1] == "text"
        for element in root.iter()
    )


def test_metric_numbers_can_render_without_lines() -> None:
    result = render_request(
        _payload(metric_lines=False, metric_numbers=True)
    )
    root = ET.fromstring(result["svg"])

    assert _metric_elements(root, "text")
    assert not _metric_elements(root, "line")


def test_metric_name_subset_renders_exactly_the_selected_guides() -> None:
    result = render_request(
        _payload(metric_names=["baseline", "xheight"])
    )
    root = ET.fromstring(result["svg"])

    guide_names = [
        element.get("data-metric")
        for element in _metric_elements(root, "line")
    ]
    assert guide_names == ["baseline", "xheight"]
    assert {
        element.get("data-metric")
        for element in _metric_elements(root, "text")
    } == {"baseline", "xheight"}


def test_empty_metric_name_list_renders_no_guides() -> None:
    result = render_request(_payload(metric_names=[]))
    root = ET.fromstring(result["svg"])

    assert not _metric_elements(root, "line")
    assert not _metric_elements(root, "text")


def test_render_request_rejects_unknown_metric_name() -> None:
    unknown = "overshoot"
    with pytest.raises(ValueError) as caught:
        render_request(_payload(metric_names=["baseline", unknown]))

    message = str(caught.value)
    assert repr(unknown) in message
    assert "unknown metric name" in message
    assert ", ".join(METRIC_NAMES) in message


def test_omitted_metric_fields_keep_all_guides_lines_and_numbers() -> None:
    request = _payload(shape="")
    assert "metric_lines" not in request
    assert "metric_numbers" not in request
    assert "metric_names" not in request

    rendered = render_request(request)["svg"]
    today = blueprint(
        EXAMPLE,
        request["text"],
        preset=request["preset"],
        overrides={
            "canvas": {
                "frame": request["frame"],
                "width": request["width"],
            },
            "metrics": {
                "visible": request["metrics"],
                "show": list(METRIC_NAMES),
            },
        },
        tracking=request["tracking"],
        apply_kerning=request["apply_kerning"],
        title="Type Design X-Ray preview",
    )
    root = ET.fromstring(rendered)

    assert rendered == today
    assert {
        element.get("data-metric")
        for element in _metric_elements(root, "line")
    } == set(METRIC_NAMES)
    assert {
        element.get("data-metric")
        for element in _metric_elements(root, "text")
    } == set(METRIC_NAMES)


@pytest.mark.parametrize(
    ("path", "value", "attribute", "expected"),
    [
        (
            "metrics.label_family",
            "Futura, sans-serif",
            "font-family",
            "Futura, sans-serif",
        ),
        ("metrics.label_size", 18.5, "font-size", "18.5"),
        ("metrics.label_weight", "700", "font-weight", "700"),
        ("metrics.label_style", "italic", "font-style", "italic"),
    ],
)
def test_each_metric_label_override_reaches_rendered_svg(
    path, value, attribute, expected
) -> None:
    baseline = ET.fromstring(
        render_request(_payload(shape="", labels={}))["svg"]
    )
    changed = ET.fromstring(
        render_request(
            _payload(shape="", labels={path: value})
        )["svg"]
    )
    baseline_values = {
        element.get(attribute)
        for element in _metric_elements(baseline, "text")
    }
    changed_values = {
        element.get(attribute)
        for element in _metric_elements(changed, "text")
    }

    assert changed_values == {expected}
    assert baseline_values != changed_values


def test_render_request_rejects_unknown_label_key() -> None:
    key = "metrics.label_variant"
    with pytest.raises(ValueError) as caught:
        render_request(_payload(labels={key: "small-caps"}))

    assert str(caught.value) == "unknown label key {!r}".format(key)


def test_render_request_rejects_unsafe_label_family() -> None:
    path = "metrics.label_family"
    with pytest.raises(ValueError) as caught:
        render_request(_payload(labels={path: "Futura<svg"}))

    assert path in str(caught.value)
    assert "<" in str(caught.value)


def test_render_request_rejects_out_of_range_label_size() -> None:
    path = "metrics.label_size"
    with pytest.raises(ValueError) as caught:
        render_request(_payload(labels={path: 72.5}))

    assert str(caught.value) == "{} must be between 4 and 72".format(path)


def test_render_request_rejects_invalid_label_weight() -> None:
    path = "metrics.label_weight"
    with pytest.raises(ValueError) as caught:
        render_request(_payload(labels={path: "heavy"}))

    assert path in str(caught.value)
    assert "normal, bold, or 100 through 900" in str(caught.value)


def test_render_request_rejects_invalid_label_style() -> None:
    path = "metrics.label_style"
    with pytest.raises(ValueError) as caught:
        render_request(_payload(labels={path: "slanted"}))

    assert path in str(caught.value)
    assert "normal, italic, or oblique" in str(caught.value)


def test_omitting_labels_entirely_leaves_output_unchanged() -> None:
    request = _payload(shape="")
    assert "labels" not in request

    without_labels = render_request(request)["svg"]
    with_empty_labels = render_request(
        dict(request, labels={})
    )["svg"]

    assert without_labels == with_empty_labels


@pytest.mark.parametrize(
    ("path", "layer_name", "tag", "attribute", "node_type"),
    _COLOR_CASES,
)
def test_each_colour_override_reaches_rendered_svg(
    path, layer_name, tag, attribute, node_type
) -> None:
    colour = "#123abc"
    result = render_request(
        _payload(
            shape="",
            colors={path: colour},
            fill_enabled=True,
        )
    )
    root = ET.fromstring(result["svg"])

    assert any(
        element.tag.split("}")[-1] == tag
        and element.get(attribute) == colour
        and (
            node_type is None
            or element.get("data-node-type") == node_type
        )
        for element in _elements_in_layer(root, layer_name)
    )


def _rendered_style_value(
    root, layer_name, tag, attribute, node_type, metric_name
):
    matches = [
        element
        for element in _elements_in_layer(root, layer_name)
        if element.tag.split("}")[-1] == tag
        and element.get(attribute) is not None
        and (
            node_type is None
            or element.get("data-node-type") == node_type
        )
        and (
            metric_name is None
            or element.get("data-metric") == metric_name
        )
    ]
    assert matches
    return float(matches[0].get(attribute))


@pytest.mark.parametrize(
    (
        "path",
        "value",
        "layer_name",
        "tag",
        "attribute",
        "node_type",
        "metric_name",
    ),
    _SIZE_CASES,
)
def test_each_slider_path_reaches_rendered_svg(
    path,
    value,
    layer_name,
    tag,
    attribute,
    node_type,
    metric_name,
) -> None:
    minimum, maximum, _ = _SLIDER_RANGES[path]
    assert float(minimum) <= value <= float(maximum)
    baseline = ET.fromstring(
        render_request(_payload(shape="", sizes={}))["svg"]
    )
    changed = ET.fromstring(
        render_request(_payload(shape="", sizes={path: value}))["svg"]
    )

    baseline_value = _rendered_style_value(
        baseline,
        layer_name,
        tag,
        attribute,
        node_type,
        metric_name,
    )
    changed_value = _rendered_style_value(
        changed,
        layer_name,
        tag,
        attribute,
        node_type,
        metric_name,
    )
    assert changed_value > baseline_value


def test_typed_value_above_slider_maximum_is_honoured_by_server() -> None:
    path = "outline.width"
    typed_value = 9.2
    assert typed_value > float(_SLIDER_RANGES[path][1])

    baseline = ET.fromstring(
        render_request(_payload(shape="", sizes={}))["svg"]
    )
    changed = ET.fromstring(
        render_request(
            _payload(shape="", sizes={path: typed_value})
        )["svg"]
    )

    baseline_value = _rendered_style_value(
        baseline,
        "outline",
        "path",
        "stroke-width",
        None,
        None,
    )
    changed_value = _rendered_style_value(
        changed,
        "outline",
        "path",
        "stroke-width",
        None,
        None,
    )
    preset_value = resolve_style(preset="blueprint").get_path(path)
    assert changed_value / baseline_value == pytest.approx(
        typed_value / preset_value
    )


def test_render_request_rejects_unknown_size_key() -> None:
    key = "metrics.sidebearing_line.width"
    with pytest.raises(ValueError) as caught:
        render_request(_payload(sizes={key: 2.0}))

    assert str(caught.value) == "unknown size key {!r}".format(key)


def test_render_request_rejects_negative_size_value() -> None:
    path = "handles.point.size"
    with pytest.raises(ValueError) as caught:
        render_request(_payload(sizes={path: -0.1}))

    assert path in str(caught.value)
    assert "between 0 and 20" in str(caught.value)


@pytest.mark.parametrize(
    ("path", "value", "maximum"),
    [
        ("handles.point.size", 20.01, 20),
        ("outline.width", 10.01, 10),
    ],
)
def test_render_request_rejects_size_above_hard_limit_with_clear_message(
    path, value, maximum
) -> None:
    with pytest.raises(ValueError) as caught:
        render_request(_payload(sizes={path: value}))

    assert str(caught.value) == "{} must be between 0 and {}".format(
        path, maximum
    )


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")],
)
def test_render_request_rejects_non_finite_size_value(value) -> None:
    path = "outline.width"
    with pytest.raises(ValueError) as caught:
        render_request(_payload(sizes={path: value}))

    assert path in str(caught.value)
    assert "between 0 and 10" in str(caught.value)


def test_omitting_sizes_entirely_leaves_output_unchanged() -> None:
    request = _payload(shape="")
    assert "sizes" not in request

    without_sizes = render_request(request)["svg"]
    with_empty_sizes = render_request(
        dict(request, sizes={})
    )["svg"]

    assert without_sizes == with_empty_sizes


def test_render_request_rejects_unknown_colour_key() -> None:
    key = "outline.width"
    with pytest.raises(ValueError) as caught:
        render_request(_payload(colors={key: "#123456"}))

    assert str(caught.value) == "unknown colour key {!r}".format(key)


@pytest.mark.parametrize("value", ["red", "#12", "#12345g", None, 123456])
def test_render_request_rejects_malformed_colour_value(value) -> None:
    path = "outline.stroke"
    with pytest.raises(ValueError) as caught:
        render_request(_payload(colors={path: value}))

    message = str(caught.value)
    assert "invalid colour for {!r}".format(path) in message
    assert "#rgb/#rrggbb" in message


def test_none_background_omits_the_background_rect() -> None:
    result = render_request(
        _payload(shape="", colors={"canvas.background": "none"})
    )
    root = ET.fromstring(result["svg"])

    assert not any(
        element.tag.split("}")[-1] == "rect"
        for element in _elements_in_layer(root, "background")
    )


def test_untouched_colours_are_byte_identical_to_plain_preset() -> None:
    request = _payload(
        preset="drafting",
        shape="",
        metrics=False,
        colors={},
        sizes={},
        fill_enabled=True,
    )
    rendered = render_request(request)["svg"]
    plain = blueprint(
        EXAMPLE,
        request["text"],
        preset=request["preset"],
        overrides={
            "canvas": {
                "frame": request["frame"],
                "width": request["width"],
            },
            "metrics": {
                "visible": request["metrics"],
                "show": list(METRIC_NAMES),
            },
            "outline.fill_enabled": request["fill_enabled"],
        },
        tracking=request["tracking"],
        apply_kerning=request["apply_kerning"],
        title="Type Design X-Ray preview",
    )

    assert rendered == plain


def _css_rule(page, selector, start=0):
    match = re.search(
        re.escape(selector) + r"\s*\{([^{}]*)\}",
        page[start:],
    )
    assert match is not None
    return match.group(1)


def test_preview_page_uses_independently_scrolling_desktop_panes() -> None:
    page = _preview_page()
    shell = _css_rule(page, ".shell")
    aside = _css_rule(page, "aside")
    main = _css_rule(page, "main")
    stage = _css_rule(page, ".stage")

    assert "height: 100vh;" in shell
    assert "height: 100dvh;" in shell
    assert shell.index("height: 100vh;") < shell.index("height: 100dvh;")
    assert "overflow: hidden;" in shell

    assert "min-width: 0;" in aside
    assert "min-height: 0;" in aside
    assert "height: 100%;" in aside
    assert "overflow-y: auto;" in aside

    assert "grid-template-rows: auto minmax(0, 1fr);" in main
    assert "min-width: 0;" in main
    assert "min-height: 0;" in main
    assert "height: 100%;" in main
    assert "overflow: auto;" in main

    assert "place-items: center;" in stage
    assert "min-width: 0;" in stage
    assert "min-height: 0;" in stage
    assert "overflow: visible;" in stage

    toolbar = _css_rule(page, ".toolbar")
    assert "position: sticky;" in toolbar
    assert "top: 0;" in toolbar


def test_preview_page_restores_document_scrolling_when_stacked() -> None:
    page = _preview_page()
    media_start = page.index("@media (max-width: 850px)")
    shell = _css_rule(page, ".shell", media_start)
    aside = _css_rule(page, "aside", media_start)
    main = _css_rule(page, "main", media_start)
    stage = _css_rule(page, ".stage", media_start)

    assert "grid-template-columns: 1fr;" in shell
    assert "height: auto;" in shell
    assert "overflow: visible;" in shell
    assert "height: auto;" in aside
    assert "overflow-y: visible;" in aside
    assert "height: auto;" in main
    assert "overflow: visible;" in main
    assert "overflow: visible;" in stage


def test_preview_page_has_seeded_controls_for_every_colour_path() -> None:
    page = _preview_page()
    match = re.search(r"const PRESET_COLORS = (\{.*\});", page)

    assert match is not None
    preset_colors = json.loads(match.group(1))
    assert set(preset_colors) == set(available_presets())
    assert len(re.findall(r"<input[^>]+type=\"color\"", page)) == len(
        _COLOR_CASES
    )
    assert "Reset to preset" in page
    assert "Transparent" in page
    assert "Fill outline" in page

    paths = [case[0] for case in _COLOR_CASES]
    for path in paths:
        assert 'data-color-path="{}"'.format(path) in page
    for preset in available_presets():
        resolved = resolve_style(preset=preset)
        assert {
            path: preset_colors[preset][path]
            for path in paths
        } == {
            path: resolved.get_path(path)
            for path in paths
        }
        assert (
            preset_colors[preset]["fill_enabled"]
            is resolved.outline.fill_enabled
        )


def test_preview_page_has_upload_control_and_raw_upload_script() -> None:
    page = _preview_page()

    assert (
        '<input class="file-input" id="fontFile" type="file" '
        'accept=".glyphs,.otf,.ttf,.woff,.woff2">'
    ) in page
    assert "Choose file…" in page
    assert 'id="selectedFontName"' in page
    assert 'fetch("/api/upload"' in page
    assert '"Content-Type": "application/octet-stream"' in page
    assert '"X-Filename": encodeURIComponent(file.name)' in page
    assert "form.font_path.value = result.font_path;" in page
    assert "await renderBlueprint();" in page


def test_preview_page_seeds_and_tracks_every_size_control() -> None:
    page = _preview_page()
    match = re.search(r"const PRESET_SIZES = (\{.*\});", page)

    assert match is not None
    preset_sizes = json.loads(match.group(1))
    paths = [case[0] for case in _SIZE_CASES]
    assert set(preset_sizes) == set(available_presets())
    assert len(re.findall(r'data-size-path="[^"]+"', page)) == len(paths)
    for path in paths:
        assert 'data-size-path="{}"'.format(path) in page
    for preset in available_presets():
        resolved = resolve_style(preset=preset)
        assert preset_sizes[preset] == {
            path: resolved.get_path(path)
            for path in paths
        }

    assert "const touchedSizes = new Set();" in page
    assert "if (!touchedSizes.has(path)) return;" in page
    assert "sizes[path] = Number(input.value);" in page
    assert "syncSizeFromNumber(input);" in page
    assert "syncSizeFromSlider(slider);" in page
    assert (
        "Math.min(Number(slider.max), Math.max(Number(slider.min), value))"
        in page
    )
    assert "touchedSizes.clear();" in page
    assert 'resetColours.addEventListener("click", () => {' in page
    assert "seedControlsFromPreset();" in page


def test_preview_page_has_nine_documented_size_sliders() -> None:
    page = _preview_page()
    tags = re.findall(
        r'<input\b(?=[^>]*\btype="range")'
        r'(?=[^>]*\bdata-size-slider=")[^>]*>',
        page,
    )
    sliders = {}
    for tag in tags:
        attributes = dict(
            re.findall(r'([\w-]+)="([^"]*)"', tag)
        )
        sliders[attributes["data-size-slider"]] = attributes

    assert len(tags) == 9
    assert set(sliders) == set(_SLIDER_RANGES)
    for path, (minimum, maximum, step) in _SLIDER_RANGES.items():
        assert sliders[path]["min"] == minimum
        assert sliders[path]["max"] == maximum
        assert sliders[path]["step"] == step

    number_tags = re.findall(
        r'<input\b(?=[^>]*\btype="number")'
        r'(?=[^>]*\bdata-size-path=")[^>]*>',
        page,
    )
    numbers = {}
    for tag in number_tags:
        attributes = dict(
            re.findall(r'([\w-]+)="([^"]*)"', tag)
        )
        numbers[attributes["data-size-path"]] = attributes
    assert len(number_tags) == 9
    assert set(numbers) == set(_SLIDER_RANGES)
    for path, attributes in numbers.items():
        assert attributes["min"] == "0"
        assert attributes["max"] == (
            "20" if path.endswith(".size") else "10"
        )


def test_preview_page_seeds_and_tracks_metric_label_controls() -> None:
    page = _preview_page()
    match = re.search(r"const PRESET_LABELS = (\{.*\});", page)

    assert match is not None
    preset_labels = json.loads(match.group(1))
    paths = {
        "metrics.label_family",
        "metrics.label_size",
        "metrics.label_weight",
        "metrics.label_style",
    }
    assert set(preset_labels) == set(available_presets())
    for preset in available_presets():
        resolved = resolve_style(preset=preset)
        assert preset_labels[preset] == {
            path: resolved.get_path(path)
            for path in paths
        }
    for path in paths:
        assert 'data-label-path="{}"'.format(path) in page

    label_slider = re.search(
        r'<input\b(?=[^>]*\bid="labelSizeSlider")'
        r'(?=[^>]*\btype="range")[^>]*>',
        page,
    )
    assert label_slider is not None
    slider_attributes = dict(
        re.findall(r'([\w-]+)="([^"]*)"', label_slider.group(0))
    )
    assert slider_attributes["min"] == "6"
    assert slider_attributes["max"] == "32"
    assert slider_attributes["step"] == "0.5"
    assert 'id="labelSize" type="number" min="4" max="72" step="0.5"' in page
    assert "const touchedLabels = new Set();" in page
    assert "if (!touchedLabels.has(path)) return;" in page
    assert "touchedLabels.clear();" in page
    assert 'fetch("/api/fonts")' in page
    assert "Custom…" in page
    assert "System UI" in page
    assert "Sans-serif" in page
    assert "Serif" in page
    assert "Monospace" in page
    assert "a machine without it installed will substitute a fallback" in page
    assert (
        "!form.metrics.checked || !form.metric_numbers.checked"
        in page
    )


def test_preview_page_live_renders_all_control_groups_once() -> None:
    page = _preview_page()

    assert page.count("const discreteControls = Array.from(") == 1
    assert (
        "form.querySelectorAll('select, input[type=\"checkbox\"]')"
        in page
    )
    assert page.count("discreteControls.forEach((control) => {") == 1
    assert (
        'control.addEventListener("change", renderLiveNow);'
        in page
    )

    assert page.count("const textNumberInputs = Array.from(") == 1
    assert (
        "form.querySelectorAll('input[type=\"text\"], "
        "input[type=\"number\"]')"
        in page
    )
    assert ").filter((input) => input !== form.font_path);" in page
    assert page.count("textNumberInputs.forEach((input) => {") == 1

    color_listener = re.search(
        r"colorInputs\.forEach\(\(input\) => \{\s*"
        r'input\.addEventListener\("input", \(\) => \{\s*'
        r"touchedColors\.add\(input\.dataset\.colorPath\);\s*"
        r"scheduleLiveRender\(\);",
        page,
    )
    assert color_listener is not None

    assert page.count(
        'form.font_path.addEventListener("input", () => {'
    ) == 1
    assert "if (!form.font_path.value.trim()) return;" in page
    assert "scheduleLiveRender(600);" in page


def test_preview_page_debounces_live_renders_and_drops_stale_responses() -> None:
    page = _preview_page()

    assert "let renderRequestCounter = 0;" in page
    assert "const requestId = ++renderRequestCounter;" in page
    assert page.count(
        "if (requestId !== renderRequestCounter) return;"
    ) == 2
    assert "renderBlueprint(null, {live: true});" in page
    assert "function scheduleLiveRender(delay = 250)" in page
    assert "}, delay);" in page
    assert (
        "if (!live) {\n"
        "        renderButton.disabled = true;"
    ) in page
    assert 'form.addEventListener("submit", renderBlueprint);' in page

    show_error = re.search(
        r"function showError\(error\) \{([^{}]*)\}",
        page,
    )
    assert show_error is not None
    assert "latestSvg" not in show_error.group(1)
    assert "preview.innerHTML" not in show_error.group(1)


def test_preview_page_has_ordered_disabling_metric_controls() -> None:
    page = _preview_page()
    controls = (
        ("metricLines", "Metric lines"),
        ("metricNumbers", "Metric numbers"),
        ("metricBaseline", "Baseline"),
        ("metricXheight", "X-height"),
        ("metricCapheight", "Cap height"),
        ("metricAscender", "Ascender"),
        ("metricDescender", "Descender"),
        ("metricSidebearings", "Side bearings"),
    )

    positions = [page.index("> Show metrics</label>")]
    for control_id, label in controls:
        input_pattern = (
            r'<input id="{}"[^>]*data-metric-control checked disabled>'
        ).format(control_id)
        assert re.search(input_pattern, page)
        positions.append(page.index("> {}</label>".format(label)))
    assert positions == sorted(positions)
    assert 'name="metric_names"' in page
    assert "input.disabled = !form.metrics.checked;" in page
    assert (
        'form.metrics.addEventListener("change", updateMetricControls);'
        in page
    )


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"font_path": "missing.glyphs"}, "font file not found"),
        ({"text": ""}, "text is required"),
        ({"preset": "missing"}, "unknown preset"),
        ({"shape": "star"}, "unknown marker shape"),
        ({"width": 100}, "width must be between"),
        ({"compound": "yes"}, "compound must be true or false"),
    ],
)
def test_render_request_rejects_invalid_input(change, problem) -> None:
    with pytest.raises(ValueError, match=problem):
        render_request(_payload(**change))


def test_http_server_serves_page_health_and_render_endpoint() -> None:
    page_status, page = _get("/", parse_json=False)
    assert page_status == 200
    assert "Type Design X-Ray" in page
    assert "/api/render" in page

    health_status, health = _get("/health")
    assert health_status == 200
    assert health == {"status": "ok"}

    render_status, rendered = _post_render(_payload(text="Ao"))
    assert render_status == 200
    assert rendered["summary"]["glyphs"] == 2
    ET.fromstring(rendered["svg"])


def test_http_server_returns_json_error() -> None:
    status, body = _post_render(_payload(text=""))

    assert status == 400
    assert body["error"] == "text is required"


def test_marker_shape_defaults_to_the_preset_so_corner_and_smooth_differ():
    """Regression: the preview forced one shape onto every marker.

    That collapsed the corner-vs-smooth distinction the presets exist to
    show, so the preview stopped representing what the tool actually exports.
    """
    demo = Path(__file__).resolve().parent.parent / "examples" / "BlueprintDemo.glyphs"
    result = render_request(
        {"font_path": str(demo), "text": "ao", "preset": "blueprint"}
    )
    root = ET.fromstring(result["svg"])
    kinds = {}
    for group in root.iter():
        if group.get("data-layer") != "nodes":
            continue
        for element in group.iter():
            kind = element.get("data-node-type")
            if kind:
                kinds.setdefault(kind, set()).add(element.tag.split("}")[-1])
    assert kinds.get("corner") == {"rect"}
    assert kinds.get("smooth") == {"circle"}

    # an explicit shape still overrides everything
    forced = render_request(
        {
            "font_path": str(demo),
            "text": "ao",
            "preset": "blueprint",
            "shape": "diamond",
        }
    )
    root = ET.fromstring(forced["svg"])
    shapes = {
        element.tag.split("}")[-1]
        for group in root.iter()
        if group.get("data-layer") == "nodes"
        for element in group.iter()
        if element.get("data-node-type")
    }
    assert shapes == {"polygon"}


def test_preview_reports_a_busy_port_without_a_traceback(
    capsys, monkeypatch
):
    """Re-running the preview while one is open is the likeliest failure."""
    import typedesignxray.web as web

    def busy_server(host, port):
        raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setattr(web, "create_server", busy_server)
    port = 8765
    code = main(["--port", str(port)])

    assert code == 2
    err = capsys.readouterr().err
    assert "already in use" in err
    assert "--port" in err
    assert "Traceback" not in err


def test_preview_rejects_an_out_of_range_port_cleanly(capsys):
    assert main(["--port", "99999"]) == 2
    err = capsys.readouterr().err
    assert "between 0 and 65535" in err
    assert "Traceback" not in err
