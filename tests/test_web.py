from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from glyphblueprint.web import create_server, main, render_request


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
