from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from glyphblueprint.api import blueprint
from glyphblueprint.config import available_presets, resolve_style
from glyphblueprint.style import METRIC_NAMES
from glyphblueprint.web import (
    _preview_page,
    create_server,
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
        title="glyphblueprint preview",
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
        title="glyphblueprint preview",
    )

    assert rendered == plain


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
    server = create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:{}".format(server.server_address[1])
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as response:
            page = response.read().decode("utf-8")
        assert response.status == 200
        assert "glyphblueprint" in page
        assert "/api/render" in page

        with urllib.request.urlopen(base + "/health", timeout=5) as response:
            health = json.load(response)
        assert health == {"status": "ok"}

        request = urllib.request.Request(
            base + "/api/render",
            data=json.dumps(_payload(text="Ao")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            rendered = json.load(response)
        assert response.status == 200
        assert rendered["summary"]["glyphs"] == 2
        ET.fromstring(rendered["svg"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_http_server_returns_json_error() -> None:
    server = create_server(port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    request = urllib.request.Request(
        "http://127.0.0.1:{}/api/render".format(server.server_address[1]),
        data=json.dumps(_payload(text="")).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        assert caught.value.code == 400
        body = json.loads(caught.value.read().decode("utf-8"))
        assert body["error"] == "text is required"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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


def test_preview_reports_a_busy_port_without_a_traceback(capsys):
    """Re-running the preview while one is open is the likeliest failure."""
    from glyphblueprint.web import create_server, main

    server = create_server("127.0.0.1", 0)
    port = server.server_address[1]
    try:
        code = main(["--port", str(port)])
    finally:
        server.server_close()
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
