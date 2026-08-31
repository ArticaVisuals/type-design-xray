"""Self-contained browser UI for the Font Design Process Video player.

The HTTP server owns routing and the process modules own source parsing and
rendering.  Keeping the page here makes the feature additive: the original
blueprint preview and two-up Specimen Player remain independent.
"""

from __future__ import annotations

from .tool_nav import tool_switcher


_PROCESS_PAGE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Type Design X-Ray — Font Design Process Video</title>
  <link rel="icon" href="data:,">
  <style>
    :root {
      color-scheme:dark; --ink:#f5f5f3; --muted:#8b8b88; --line:#353533;
      --process-bg:#000000; --process-text:#ffffff; --process-guides:#737373;
    }
    * { box-sizing:border-box; }
    html, body { min-height:100%; }
    body {
      margin:0; background:#111; color:var(--ink);
      font-family:"SFMono-Regular",Consolas,"Liberation Mono",monospace;
    }
    button, input, select { font:inherit; }
    .tool-switcher {
      position:sticky; top:0; z-index:6; display:grid;
      grid-template-columns:repeat(3,minmax(0,1fr)); gap:1px;
      padding:7px 14px; border-bottom:1px solid #292927; background:#080808;
    }
    .tool-tab {
      min-width:0; display:grid; gap:2px; padding:7px 10px;
      border:1px solid transparent; color:#b8b8b3; text-decoration:none;
    }
    .tool-tab:hover { border-color:#3d3d3a; background:#171715; color:#fff; }
    .tool-tab.active { border-color:#686864; background:#1e1e1b; color:#fff; }
    .tool-name { font-size:11px; font-weight:700; letter-spacing:.08em; }
    .tool-summary {
      overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
      color:#777; font-size:9px; letter-spacing:.02em;
    }
    .toolbar {
      position:sticky; top:61px; z-index:5; min-height:58px; padding:9px 14px;
      display:flex; align-items:center; flex-wrap:wrap; gap:8px;
      border-bottom:1px solid #292927; background:rgba(10,10,10,.97);
    }
    .file-control input {
      position:absolute; inline-size:1px; block-size:1px; clip:rect(0 0 0 0);
    }
    .file-control span, button, select, input[type="number"], input[type="text"] {
      min-height:36px; border:1px solid #3d3d3a; border-radius:2px;
      background:#111; color:var(--ink); padding:7px 10px;
    }
    .file-control span, button {
      display:inline-flex; align-items:center; cursor:pointer;
    }
    button:hover, .file-control span:hover { background:#20201e; }
    button:disabled { cursor:not-allowed; color:#666; background:#0c0c0c; }
    button:focus-visible, select:focus-visible, input:focus-visible,
    summary:focus-visible { outline:1px solid #fff; outline-offset:2px; }
    .file-control input:focus-visible + span {
      outline:1px solid #fff; outline-offset:2px;
    }
    .labelled {
      display:flex; align-items:center; gap:7px; color:#aaa;
      font-size:11px; letter-spacing:.08em;
    }
    .glyph-form { display:flex; align-items:center; gap:6px; margin:0; }
    #glyph { width:124px; }
    #master { max-width:190px; }
    #layer { max-width:250px; }
    input[type="number"] { width:82px; }
    .toggle {
      display:inline-flex; align-items:center; gap:7px;
      min-height:36px; padding:0 8px; cursor:pointer;
    }
    .toggle input { accent-color:#f5f5f3; }
    .toggle:has(input:disabled) { color:#666; cursor:not-allowed; }
    .palette { position:relative; }
    .palette summary {
      min-height:36px; display:inline-flex; align-items:center; cursor:pointer;
      border:1px solid #3d3d3a; border-radius:2px; padding:7px 10px;
      list-style:none; background:#111;
    }
    .palette summary::-webkit-details-marker { display:none; }
    .palette-grid {
      position:absolute; z-index:10; top:43px; left:0; width:310px;
      display:grid; grid-template-columns:1fr 1fr; gap:10px; padding:14px;
      border:1px solid #484845; background:#111; box-shadow:0 14px 40px #000a;
    }
    .color-control {
      display:grid; grid-template-columns:34px 1fr; align-items:center; gap:8px;
      color:#b8b8b3; font-size:10px; letter-spacing:.08em;
    }
    .color-control input {
      width:34px; height:30px; padding:2px;
      border:1px solid #444; background:#111;
    }
    .palette-reset { grid-column:1 / -1; justify-content:center; }
    #status {
      margin-left:auto; max-width:360px; overflow:hidden;
      text-overflow:ellipsis; white-space:nowrap; color:#8e8e8b;
      font-size:11px; letter-spacing:.06em;
    }
    #status.error { color:#ff8d85; }
    .viewport {
      min-height:calc(100vh - 119px); display:grid; place-items:center;
      padding:18px; overflow:auto;
    }
    .process-frame {
      width:min(540px,100%); aspect-ratio:540 / 766;
      display:grid; grid-template-rows:246fr 520fr;
      padding:0 18px; background:var(--process-bg); overflow:hidden;
    }
    .metadata {
      display:flex; align-items:flex-start; margin:0;
      padding-top:18px; padding-bottom:10px;
      border-bottom:1px solid var(--process-guides);
      color:var(--process-text); white-space:pre;
      font-size:clamp(8px,2.59vw,14px); line-height:1.07;
      letter-spacing:.1em; overflow:hidden;
    }
    .glyph-stage {
      min-height:0; display:grid; place-items:stretch; overflow:hidden;
    }
    .glyph-stage svg { display:block; width:100%; height:100%; }
    .empty {
      display:grid; place-items:center; min-height:0; color:#777;
      letter-spacing:.16em; text-align:center; padding:2rem;
    }
    @media (max-width:800px) {
      .tool-switcher { position:static; grid-template-columns:1fr; }
      .tool-summary { white-space:normal; }
      .toolbar { top:0; }
      .viewport { place-items:start center; padding:8px; }
      #status { flex-basis:100%; margin-left:0; }
    }
  </style>
</head>
<body>
  __TOOL_SWITCHER__
  <header class="toolbar" aria-label="Font design process controls">
    <label class="file-control">
      <input id="font-file" type="file" accept=".glyphs">
      <span>IMPORT .GLYPHS</span>
    </label>
    <label class="labelled">STYLE
      <select id="master" disabled><option>—</option></select>
    </label>
    <form id="glyph-form" class="glyph-form">
      <label class="labelled" for="glyph">GLYPH</label>
      <input id="glyph" type="text" inputmode="text" autocomplete="off"
             autocapitalize="off" spellcheck="false" placeholder="A or Aacute"
             aria-describedby="glyph-hint">
      <button id="load-glyph" type="submit" disabled>LOAD</button>
      <span id="glyph-hint" hidden>Enter one character or an exact glyph name.</span>
    </form>
    <button id="previous" type="button" aria-label="Previous layer" disabled>←</button>
    <button id="play" type="button" aria-pressed="false" disabled>PLAY</button>
    <button id="next" type="button" aria-label="Next layer" disabled>→</button>
    <label class="labelled">LAYER
      <select id="layer" disabled><option>—</option></select>
    </label>
    <label class="labelled">SIZE
      <input id="point-size" type="number" min="48" max="520" step="1" value="370">
    </label>
    <label class="labelled">SPEED
      <input id="speed" type="number" min="0.08" max="1" step="0.05" value="0.2"
             aria-describedby="speed-hint">
      <span id="speed-hint" hidden>Seconds per intermediate layer.</span>
    </label>
    <label class="toggle"><input id="bezier" type="checkbox" checked> BEZIER</label>
    <label class="toggle"><input id="handles" type="checkbox"> HANDLES</label>
    <details class="palette">
      <summary>COLORS</summary>
      <div class="palette-grid">
        <label class="color-control"><input type="color" value="#000000" data-color="background">BACKGROUND</label>
        <label class="color-control"><input type="color" value="#ffffff" data-color="fill">FILL</label>
        <label class="color-control"><input type="color" value="#ffffff" data-color="stroke">STROKE</label>
        <label class="color-control"><input type="color" value="#ffffff" data-color="text">TEXT</label>
        <label class="color-control"><input type="color" value="#737373" data-color="guides">GUIDES</label>
        <label class="color-control"><input type="color" value="#8e8e8e" data-color="handles">HANDLES</label>
        <label class="color-control"><input type="color" value="#000000" data-color="point_fill">POINT FILL</label>
        <label class="color-control"><input type="color" value="#ffffff" data-color="point_stroke">POINT STROKE</label>
        <button class="palette-reset" id="reset-colors" type="button">RESET COLORS</button>
      </div>
    </details>
    <button id="export-svg" type="button" disabled>LAYER SVG</button>
    <button id="export-png" type="button" disabled>LAYER PNG</button>
    <button id="export-gif" type="button" disabled>EXPORT GIF</button>
    <button id="export-mp4" type="button" disabled>EXPORT MP4</button>
    <span id="status" role="status">IMPORT A GLYPHS FILE TO BEGIN</span>
  </header>
  <main class="viewport">
    <section id="process-frame" class="process-frame" aria-label="Font design process preview">
      <pre id="metadata" class="metadata" aria-label="Glyph metadata">TYPEFACE: —</pre>
      <div id="glyph-stage" class="glyph-stage">
        <div class="empty">IMPORT A GLYPHS FILE<br>AND ENTER A GLYPH</div>
      </div>
    </section>
  </main>
  <script>
    (() => {
      "use strict";
      const FINAL_HOLD_MS = 3000;
      const $ = (id) => document.getElementById(id);
      const fileInput = $("font-file");
      const master = $("master");
      const glyphForm = $("glyph-form");
      const glyphInput = $("glyph");
      const loadGlyph = $("load-glyph");
      const previous = $("previous");
      const play = $("play");
      const next = $("next");
      const layer = $("layer");
      const pointSize = $("point-size");
      const speed = $("speed");
      const bezier = $("bezier");
      const handles = $("handles");
      const processFrame = $("process-frame");
      const metadata = $("metadata");
      const glyphStage = $("glyph-stage");
      const colorInputs = Array.from(document.querySelectorAll("[data-color]"));
      const resetColors = $("reset-colors");
      const exportSvg = $("export-svg");
      const exportPng = $("export-png");
      const exportGif = $("export-gif");
      const exportMp4 = $("export-mp4");
      const exportButtons = [exportSvg, exportPng, exportGif, exportMp4];
      const status = $("status");
      const DEFAULT_COLORS = {
        background:"#000000", fill:"#ffffff", stroke:"#ffffff",
        text:"#ffffff", guides:"#737373", handles:"#8e8e8e",
        point_fill:"#000000", point_stroke:"#ffffff"
      };
      let fontPath = "";
      let catalog = null;
      let timer = null;
      let playing = false;
      let catalogGeneration = 0;
      let renderGeneration = 0;
      const renderCache = new Map();

      function setStatus(message, error = false) {
        status.textContent = message;
        status.className = error ? "error" : "";
      }
      function currentColors() {
        return Object.fromEntries(
          colorInputs.map((input) => [input.dataset.color, input.value])
        );
      }
      function renderSettingsSignature() {
        return JSON.stringify({
          point_size:Number(pointSize.value),
          bezier:bezier.checked,
          handles:bezier.checked && handles.checked,
          colors:currentColors()
        });
      }
      function clearRenderCache() {
        renderCache.clear();
        renderGeneration += 1;
      }
      function applyPaletteStyles() {
        const colors = currentColors();
        processFrame.style.setProperty("--process-bg", colors.background);
        processFrame.style.setProperty("--process-text", colors.text);
        processFrame.style.setProperty("--process-guides", colors.guides);
      }
      async function jsonRequest(url, payload) {
        const response = await fetch(url, {
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || `Request failed (${response.status})`);
        }
        return data;
      }
      function populate(select, values, selected) {
        select.replaceChildren();
        values.forEach((value) => {
          const option = document.createElement("option");
          option.value = String(value.value);
          option.textContent = value.label;
          option.selected = String(value.value) === String(selected);
          select.append(option);
        });
        select.disabled = values.length === 0;
      }
      function processLayers() {
        if (!catalog) return [];
        return Array.isArray(catalog.layers) ? catalog.layers : (catalog.sequence || []);
      }
      function layerValue(item, index) {
        return String(item.layer_id ?? item.id ?? index);
      }
      function layerLabel(item, index) {
        if (item.label) return String(item.label);
        const name = item.name || item.layer_name || catalog?.master_name || "Active";
        return `${index + 1} · ${name}${item.is_final ? " · ACTIVE" : ""}`;
      }
      function layerIndex() {
        const items = processLayers();
        const selected = String(layer.value);
        const found = items.findIndex((item, index) => layerValue(item, index) === selected);
        return found < 0 ? 0 : found;
      }
      function currentLayer() {
        return processLayers()[layerIndex()] || null;
      }
      function isFinalLayer(item, index = layerIndex()) {
        const items = processLayers();
        return Boolean(item?.is_final) || (items.length > 0 && index === items.length - 1);
      }
      function resolvedGlyphName() {
        return catalog?.glyph_name || catalog?.glyph?.name || glyphInput.value.trim();
      }
      function formattedMetric(value) {
        if (value == null || value === "") return "—";
        const number = Number(value);
        if (!Number.isFinite(number)) return String(value);
        return Number.isInteger(number) ? String(number) : String(Math.round(number * 1000) / 1000);
      }
      function formattedUpm(value) {
        if (value == null || value === "") return "—";
        const number = Number(value);
        if (!Number.isFinite(number) || !Number.isInteger(number)) return formattedMetric(value);
        const sign = number < 0 ? "-" : "";
        return `${sign}${String(Math.abs(number)).padStart(3, "0")}`;
      }
      function metricText(font, glyph) {
        const family = String(font.family_name || catalog?.family_name || "UNTITLED").toUpperCase();
        const style = String(font.master_name || catalog?.master_name || "REGULAR").toUpperCase();
        return [
          `TYPEFACE: ${family}`,
          "",
          `STYLE:    ${style}`,
          `SIZE:     ${formattedMetric(pointSize.value)} pt`,
          "",
          `GLYPH:    ${glyph.category || "GLYPH"}`,
          `GROUP:    ${glyph.group || "OTHER GLYPHS"}`,
          "",
          `NAME:     ${glyph.name || resolvedGlyphName()}`,
          `UNICODE:  ${glyph.unicode || "—"}`,
          "",
          `|↔|:      ${formattedUpm(glyph.width)} upm`,
          `|←|:      ${formattedUpm(glyph.lsb)} upm`,
          ` →|:      ${formattedUpm(glyph.rsb)} upm`
        ].join("\n");
      }
      function syncHandles() {
        handles.disabled = !bezier.checked;
      }
      function setReady(enabled) {
        const count = processLayers().length;
        layer.disabled = !enabled || count === 0;
        previous.disabled = !enabled || count < 2;
        play.disabled = !enabled || count < 2;
        next.disabled = !enabled || count < 2;
        exportButtons.forEach((button) => { button.disabled = !enabled; });
      }
      function stop() {
        const wasPlaying = playing;
        playing = false;
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
        play.textContent = "PLAY";
        play.setAttribute("aria-pressed", "false");
        if (wasPlaying && catalog) {
          setStatus(`${String(catalog.family_name || "FONT").toUpperCase()} · ${layerLabel(currentLayer(), layerIndex())}`);
        }
      }
      function normalDelayMs() {
        const seconds = Number(speed.value);
        return Math.min(1, Math.max(.08, Number.isFinite(seconds) ? seconds : .2)) * 1000;
      }
      async function advanceLoop() {
        const items = processLayers();
        if (!items.length) return;
        if (isFinalLayer(currentLayer())) {
          layer.value = layerValue(items[0], 0);
          await renderLayer({quiet:true});
          return;
        }
        await move(1, true);
      }
      function scheduleNext() {
        if (!playing) return;
        if (timer !== null) window.clearTimeout(timer);
        const selected = currentLayer();
        const delay = isFinalLayer(selected) ? FINAL_HOLD_MS : normalDelayMs();
        timer = window.setTimeout(async () => {
          timer = null;
          if (!playing) return;
          await advanceLoop();
          scheduleNext();
        }, delay);
      }
      async function start() {
        if (processLayers().length < 2) return;
        stop();
        const items = processLayers();
        const selectedCatalogGeneration = catalogGeneration;
        const selectedSettings = renderSettingsSignature();
        play.disabled = true;
        setStatus(`PREPARING ${items.length} LAYERS FOR TIMED PLAYBACK…`);
        try {
          await Promise.all(
            items.map((item, index) => fetchLayerRender(item, index))
          );
          if (
            selectedCatalogGeneration !== catalogGeneration ||
            selectedSettings !== renderSettingsSignature()
          ) return;
          await renderLayer({quiet:true});
          playing = true;
          play.textContent = "PAUSE";
          play.setAttribute("aria-pressed", "true");
          setStatus("PLAYING · LOOPING · FINAL ACTIVE LAYER HOLDS FOR 3000 MS");
          scheduleNext();
        } catch (error) {
          if (
            selectedCatalogGeneration === catalogGeneration &&
            selectedSettings === renderSettingsSignature()
          ) setStatus(error.message, true);
        } finally {
          if (
            selectedCatalogGeneration === catalogGeneration &&
            selectedSettings === renderSettingsSignature()
          ) play.disabled = processLayers().length < 2;
        }
      }
      async function loadCatalog(selectedMaster = "") {
        const requestedGlyph = glyphInput.value.trim();
        if (!fontPath) throw new Error("Import a Glyphs file first");
        if (!requestedGlyph) throw new Error("Enter a character or glyph name");
        stop();
        setReady(false);
        setStatus("READING GLYPH LAYERS…");
        const generation = ++catalogGeneration;
        let nextCatalog;
        try {
          nextCatalog = await jsonRequest("/api/process/catalog", {
            font_path:fontPath,
            master:selectedMaster,
            glyph:requestedGlyph
          });
        } catch (error) {
          if (generation !== catalogGeneration) return;
          throw error;
        }
        if (generation !== catalogGeneration) return;
        catalog = nextCatalog;
        clearRenderCache();
        const masters = Array.isArray(catalog.masters) ? catalog.masters : [];
        populate(master, masters.map((item) => ({
          value:item.id ?? item.master_id ?? item.name,
          label:item.name ?? item.master_name ?? item.id
        })), catalog.selected_master_id || masters[0]?.id || masters[0]?.master_id);
        const items = processLayers();
        if (!items.length) throw new Error("This glyph contains no process layers");
        populate(layer, items.map((item, index) => ({
          value:layerValue(item, index),
          label:layerLabel(item, index)
        })), layerValue(items[0], 0));
        glyphInput.value = resolvedGlyphName();
        setReady(true);
        await renderLayer();
      }
      async function fetchLayerRender(selected, index) {
        const requestGeneration = catalogGeneration;
        const key = JSON.stringify([
          requestGeneration,
          fontPath,
          master.value,
          resolvedGlyphName(),
          layerValue(selected, index),
          renderSettingsSignature()
        ]);
        if (renderCache.has(key)) return renderCache.get(key);
        const data = await jsonRequest("/api/process/render", {
          font_path:fontPath,
          master:master.value,
          glyph:resolvedGlyphName(),
          layer_id:layerValue(selected, index),
          point_size:Number(pointSize.value),
          bezier:bezier.checked,
          handles:bezier.checked && handles.checked,
          colors:currentColors()
        });
        if (requestGeneration === catalogGeneration) renderCache.set(key, data);
        return data;
      }
      async function renderLayer({quiet = false} = {}) {
        const selected = currentLayer();
        if (!catalog || !selected) return;
        const generation = ++renderGeneration;
        if (!quiet) setStatus("RENDERING LAYER…");
        try {
          const data = await fetchLayerRender(selected, layerIndex());
          if (generation !== renderGeneration) return;
          const render = data.render || data.renders?.[0] || data;
          const glyph = render.glyph || data.glyph || catalog.glyph || {};
          metadata.textContent = metricText(data, glyph);
          glyphStage.innerHTML = render.svg || data.svg || "";
          if (!quiet && !playing) {
            setStatus(`${String(data.family_name || catalog.family_name || "FONT").toUpperCase()} · ${layerLabel(selected, layerIndex())}${isFinalLayer(selected) ? " · FINAL ACTIVE" : ""}`);
          }
        } catch (error) {
          if (generation === renderGeneration) {
            stop();
            setStatus(error.message, true);
          }
        }
      }
      async function move(delta, quiet = false) {
        const items = processLayers();
        if (!items.length) return;
        const index = (layerIndex() + delta + items.length) % items.length;
        layer.value = layerValue(items[index], index);
        await renderLayer({quiet});
      }
      function downloadName(format) {
        const family = catalog?.family_name || "font";
        const glyph = resolvedGlyphName() || "glyph";
        const base = `${family}-${glyph}-design-process`
          .replace(/[^A-Za-z0-9_.-]+/g, "-")
          .replace(/^[.-]+|[.-]+$/g, "") || "font-design-process";
        if (format === "svg" || format === "png") {
          const frame = String(layerIndex() + 1).padStart(2, "0");
          return `${base}-layer-${frame}.${format}`;
        }
        return `${base}.${format}`;
      }
      async function exportMedia(format) {
        if (!catalog || !fontPath) return;
        const frameFormat = format === "svg" || format === "png";
        const secondsPerLayer = Number(speed.value);
        if (!frameFormat && (!Number.isFinite(secondsPerLayer) || secondsPerLayer < .08 || secondsPerLayer > 1)) {
          setStatus("SPEED MUST BE BETWEEN 0.08 AND 1 SECOND PER LAYER", true);
          return;
        }
        stop();
        exportButtons.forEach((button) => { button.disabled = true; });
        const selection = frameFormat
          ? `LAYER ${layerIndex() + 1}`
          : `${processLayers().length} LAYERS`;
        setStatus(`EXPORTING ${selection} AS ${format.toUpperCase()}…`);
        try {
          const selected = currentLayer();
          const payload = {
            font_path:fontPath,
            master:master.value,
            glyph:resolvedGlyphName(),
            format,
            output_name:downloadName(format),
            point_size:Number(pointSize.value),
            speed:secondsPerLayer,
            bezier:bezier.checked,
            handles:bezier.checked && handles.checked,
            colors:currentColors()
          };
          if (frameFormat) {
            payload.layer_id = layerValue(selected, layerIndex());
          }
          const response = await fetch("/api/process/export", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify(payload)
          });
          if (!response.ok) {
            let message = `Export failed (${response.status})`;
            try {
              const error = await response.json();
              message = error.error || message;
            } catch (_) {}
            throw new Error(message);
          }
          const blob = await response.blob();
          const url = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = url;
          link.download = downloadName(format);
          document.body.append(link);
          link.click();
          link.remove();
          URL.revokeObjectURL(url);
          setStatus(
            `${format.toUpperCase()} EXPORTED · ${selection}` +
            (frameFormat ? "" : " · FINAL HOLD 3000 MS")
          );
        } catch (error) {
          setStatus(error.message, true);
        } finally {
          exportButtons.forEach((button) => { button.disabled = false; });
        }
      }

      fileInput.addEventListener("change", async () => {
        const file = fileInput.files?.[0];
        if (!file) return;
        stop();
        const generation = ++catalogGeneration;
        catalog = null;
        fontPath = "";
        setReady(false);
        master.disabled = true;
        loadGlyph.disabled = true;
        metadata.textContent = "TYPEFACE: —";
        glyphStage.innerHTML = '<div class="empty">IMPORTING GLYPHS SOURCE…</div>';
        setStatus("IMPORTING GLYPHS SOURCE…");
        try {
          const response = await fetch("/api/upload", {
            method:"POST",
            body:file,
            headers:{
              "Content-Type":"application/octet-stream",
              "X-Filename":encodeURIComponent(file.name)
            }
          });
          const uploaded = await response.json();
          if (generation !== catalogGeneration) return;
          if (!response.ok) throw new Error(uploaded.error || "Upload failed");
          fontPath = uploaded.font_path;
          loadGlyph.disabled = false;
          if (glyphInput.value.trim()) {
            // loadCatalog advances catalogGeneration; clear this import's
            // input first so an older request can never clear a newer one.
            fileInput.value = "";
            try {
              await loadCatalog();
            } catch (error) {
              setStatus(error.message, true);
            }
          } else {
            glyphInput.focus();
            setStatus("SOURCE READY · ENTER A GLYPH");
            glyphStage.innerHTML = '<div class="empty">ENTER A CHARACTER<br>OR GLYPH NAME</div>';
          }
        } catch (error) {
          if (generation !== catalogGeneration) return;
          setStatus(error.message, true);
        } finally {
          if (generation === catalogGeneration) fileInput.value = "";
        }
      });
      glyphForm.addEventListener("submit", (event) => {
        event.preventDefault();
        loadCatalog(master.disabled ? "" : master.value).catch((error) => setStatus(error.message, true));
      });
      master.addEventListener("change", () => {
        loadCatalog(master.value).catch((error) => setStatus(error.message, true));
      });
      layer.addEventListener("change", () => {
        stop();
        renderLayer();
      });
      previous.addEventListener("click", () => {
        stop();
        move(-1);
      });
      next.addEventListener("click", () => {
        stop();
        move(1);
      });
      play.addEventListener("click", () => playing ? stop() : void start());
      pointSize.addEventListener("change", () => {
        stop();
        clearRenderCache();
        renderLayer();
      });
      bezier.addEventListener("change", () => {
        stop();
        syncHandles();
        clearRenderCache();
        renderLayer();
      });
      handles.addEventListener("change", () => {
        stop();
        clearRenderCache();
        renderLayer();
      });
      speed.addEventListener("change", () => {
        if (playing) {
          if (timer !== null) window.clearTimeout(timer);
          timer = null;
          scheduleNext();
        }
      });
      exportSvg.addEventListener("click", () => exportMedia("svg"));
      exportPng.addEventListener("click", () => exportMedia("png"));
      exportGif.addEventListener("click", () => exportMedia("gif"));
      exportMp4.addEventListener("click", () => exportMedia("mp4"));
      colorInputs.forEach((input) => {
        input.addEventListener("change", () => {
          stop();
          applyPaletteStyles();
          clearRenderCache();
          renderLayer();
        });
      });
      resetColors.addEventListener("click", () => {
        stop();
        colorInputs.forEach((input) => {
          input.value = DEFAULT_COLORS[input.dataset.color];
        });
        applyPaletteStyles();
        clearRenderCache();
        renderLayer();
      });
      document.addEventListener("keydown", (event) => {
        if (event.target.closest("input,select,button,summary,[contenteditable='true']")) return;
        if (event.key === "ArrowLeft") {
          stop();
          move(-1);
        }
        if (event.key === "ArrowRight") {
          stop();
          move(1);
        }
        if (event.key === " ") {
          event.preventDefault();
          playing ? stop() : start();
        }
        if (event.key === "Escape") stop();
      });
      syncHandles();
      applyPaletteStyles();
    })();
  </script>
</body>
</html>
"""


def process_page() -> str:
    """Return the self-contained Font Design Process Video player page."""
    return _PROCESS_PAGE.replace(
        "__TOOL_SWITCHER__", tool_switcher("process")
    )


page = process_page


__all__ = ["page", "process_page"]
