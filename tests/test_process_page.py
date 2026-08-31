from typedesignxray.process_page import process_page


def test_process_page_is_additive_half_width_player() -> None:
    page = process_page()

    assert page.startswith("<!doctype html>")
    assert "Font Design Process Video" in page
    assert "aspect-ratio:540 / 766" in page
    assert "width:min(540px,100%)" in page
    assert 'grid-template-rows:246fr 520fr' in page
    assert 'accept=".glyphs"' in page


def test_process_page_exposes_expected_controls() -> None:
    page = process_page()

    for control_id in (
        "font-file",
        "master",
        "glyph",
        "load-glyph",
        "previous",
        "play",
        "next",
        "layer",
        "point-size",
        "speed",
        "bezier",
        "handles",
        "export-svg",
        "export-png",
        "export-gif",
        "export-mp4",
        "status",
    ):
        assert 'id="{}"'.format(control_id) in page

    assert '<input id="bezier" type="checkbox" checked>' in page
    assert '<input id="handles" type="checkbox">' in page
    assert page.count("data-color=") == 8


def test_process_page_uses_process_api_contracts() -> None:
    page = process_page()

    assert 'fetch("/api/upload"' in page
    assert 'jsonRequest("/api/process/catalog"' in page
    assert 'jsonRequest("/api/process/render"' in page
    assert 'fetch("/api/process/export"' in page
    for payload_key in (
        "font_path:fontPath",
        "glyph:resolvedGlyphName()",
        "layer_id:layerValue(selected, index)",
        "point_size:Number(pointSize.value)",
        "bezier:bezier.checked",
        "handles:bezier.checked && handles.checked",
        "colors:currentColors()",
        "speed:secondsPerLayer",
    ):
        assert payload_key in page


def test_process_playback_uses_recursive_timeout_and_exact_final_hold() -> None:
    page = process_page()

    assert "const FINAL_HOLD_MS = 1000;" in page
    assert "isFinalLayer(selected) ? FINAL_HOLD_MS : normalDelayMs()" in page
    assert "window.setTimeout(async () =>" in page
    assert "scheduleNext();" in page
    assert "setInterval" not in page
    assert 'aria-pressed="false"' in page
    assert "start();\n      syncHandles();" not in page
    assert "PREPARING ${items.length} LAYERS FOR TIMED PLAYBACK" in page
    assert "await Promise.all(" in page
    assert "items.map((item, index) => fetchLayerRender(item, index))" in page
    assert "async function advanceLoop()" in page
    assert "if (isFinalLayer(currentLayer()))" in page
    assert "layer.value = layerValue(items[0], 0);" in page
    assert "await advanceLoop();" in page
    assert "PLAYING · LOOPING · FINAL ACTIVE LAYER HOLDS FOR 1000 MS" in page


def test_process_player_caches_every_render_setting_for_exact_live_timing() -> None:
    page = process_page()

    assert "const renderCache = new Map();" in page
    assert "function renderSettingsSignature()" in page
    assert "const requestGeneration = catalogGeneration;" in page
    assert "requestGeneration," in page
    assert "fontPath," in page
    assert "master.value," in page
    assert "resolvedGlyphName()," in page
    assert "if (renderCache.has(key)) return renderCache.get(key);" in page
    assert "if (requestGeneration === catalogGeneration) renderCache.set(key, data);" in page
    assert "clearRenderCache();" in page


def test_process_page_guards_stale_render_and_quiet_playback_updates() -> None:
    page = process_page()

    assert "const generation = ++renderGeneration;" in page
    assert "if (generation !== renderGeneration) return;" in page
    assert "const generation = ++catalogGeneration;" in page
    assert "if (generation !== catalogGeneration) return;" in page
    assert "if (!quiet && !playing)" in page
    assert 'role="status"' in page


def test_process_page_ignores_out_of_order_font_uploads() -> None:
    page = process_page()

    assert "const generation = ++catalogGeneration;" in page
    assert "const uploaded = await response.json();\n          if (generation !== catalogGeneration) return;" in page
    assert "if (generation !== catalogGeneration) return;\n          setStatus(error.message, true);" in page
    assert 'if (generation === catalogGeneration) fileInput.value = "";' in page


def test_current_import_reports_retained_glyph_catalog_errors() -> None:
    page = process_page()

    assert "nextCatalog = await jsonRequest(\"/api/process/catalog\"" in page
    assert "if (generation !== catalogGeneration) return;\n          throw error;" in page
    assert "await loadCatalog();\n            } catch (error) {\n              setStatus(error.message, true);" in page


def test_stale_playback_preload_cannot_disable_new_catalog_controls() -> None:
    page = process_page()

    assert "selectedCatalogGeneration === catalogGeneration &&\n            selectedSettings === renderSettingsSignature()" in page
    assert ") play.disabled = processLayers().length < 2;" in page
    assert "selectedCatalogGeneration !== catalogGeneration ||\n            processLayers().length < 2" not in page
