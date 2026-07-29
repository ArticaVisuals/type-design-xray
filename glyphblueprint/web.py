"""Local browser preview for testing glyphblueprint end to end."""

from __future__ import annotations

import argparse
import atexit
import errno
import html
import json
import math
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote

from .api import blueprint
from .config import available_presets, resolve_style
from .style import FRAME_MODES, METRIC_NAMES, SHAPES


_MAX_REQUEST_BYTES = 1_000_000
_MAX_UPLOAD_BYTES = 64 * 1024 * 1024
_SUPPORTED_FONT_SUFFIXES = (
    ".glyphs",
    ".otf",
    ".ttf",
    ".woff",
    ".woff2",
    ".ufo",
)
_UPLOAD_FONT_SUFFIXES = frozenset(
    suffix for suffix in _SUPPORTED_FONT_SUFFIXES if suffix != ".ufo"
)
_UPLOAD_DIRECTORY = Path(
    tempfile.mkdtemp(prefix="glyphblueprint-uploads-")
).resolve()
atexit.register(shutil.rmtree, str(_UPLOAD_DIRECTORY), ignore_errors=True)
_COLOR_PATHS = (
    "canvas.background",
    "outline.stroke",
    "outline.fill",
    "handles.line.color",
    "handles.point.fill",
    "handles.point.stroke",
    "nodes.corner.fill",
    "nodes.corner.stroke",
    "nodes.smooth.fill",
    "nodes.smooth.stroke",
    "metrics.line.color",
    "metrics.label_color",
)
_COLOR_PATH_SET = frozenset(_COLOR_PATHS)
_SIZE_LIMITS = {
    "handles.point.size": 20.0,
    "handles.point.stroke_width": 10.0,
    "nodes.corner.size": 20.0,
    "nodes.smooth.size": 20.0,
    "nodes.corner.stroke_width": 10.0,
    "nodes.smooth.stroke_width": 10.0,
    "outline.width": 10.0,
    "handles.line.width": 10.0,
    "metrics.line.width": 10.0,
}
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?")
_METRIC_LINE_ELEMENT = re.compile(
    r'<line\b(?=[^>]*\bdata-metric=")[^>]*/>'
)


_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>glyphblueprint local preview</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #07111f;
      color: #e8f1ff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at 10% 0%, rgba(61, 139, 253, .18), transparent 32rem),
        #07111f;
    }
    button, input, select { font: inherit; }
    .shell {
      display: grid;
      grid-template-columns: minmax(19rem, 25rem) minmax(0, 1fr);
      min-height: 100vh;
    }
    aside {
      padding: 1.5rem;
      border-right: 1px solid #1f3552;
      background: rgba(8, 20, 36, .92);
      overflow: auto;
    }
    main {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      min-width: 0;
      padding: 1.5rem;
      gap: 1rem;
    }
    h1 {
      margin: 0;
      font-size: 1.35rem;
      letter-spacing: -.025em;
    }
    .lede {
      margin: .45rem 0 1.35rem;
      color: #9db1cb;
      font-size: .9rem;
      line-height: 1.45;
    }
    form { display: grid; gap: 1rem; }
    label, legend {
      display: block;
      margin-bottom: .4rem;
      color: #bbcae0;
      font-size: .78rem;
      font-weight: 700;
      letter-spacing: .04em;
      text-transform: uppercase;
    }
    input[type="text"], input[type="number"], select {
      width: 100%;
      min-height: 2.65rem;
      border: 1px solid #2a4568;
      border-radius: .55rem;
      background: #0b1a2d;
      color: #f4f8ff;
      padding: .65rem .75rem;
      outline: none;
    }
    input:focus, select:focus {
      border-color: #5aa9ff;
      box-shadow: 0 0 0 3px rgba(90, 169, 255, .16);
    }
    .font-source {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: stretch;
      gap: .55rem;
    }
    .file-input {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip: rect(0, 0, 0, 0);
      clip-path: inset(50%);
      white-space: nowrap;
    }
    .file-picker {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 2.65rem;
      margin: 0;
      border: 1px solid #2d527d;
      border-radius: .55rem;
      background: #0d2139;
      color: #dbeaff;
      padding: .65rem .75rem;
      font-size: .78rem;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: none;
      cursor: pointer;
    }
    .file-input:focus + .file-picker {
      border-color: #5aa9ff;
      box-shadow: 0 0 0 3px rgba(90, 169, 255, .16);
    }
    .file-name {
      min-height: 1.1rem;
      margin-top: .35rem;
      color: #8195b0;
      font-size: .72rem;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: .75rem;
    }
    fieldset {
      margin: 0;
      padding: 0;
      border: 0;
    }
    .checks {
      display: flex;
      flex-wrap: wrap;
      gap: .65rem 1rem;
    }
    fieldset > .checks + .checks {
      margin-top: .65rem;
    }
    .check {
      display: inline-flex;
      align-items: center;
      gap: .5rem;
      margin: 0;
      color: #dbe8fa;
      font-size: .88rem;
      font-weight: 600;
      letter-spacing: 0;
      text-transform: none;
    }
    .check:has(input:disabled) {
      color: #8195b0;
      opacity: .55;
    }
    .colour-heading {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: .75rem;
      margin-bottom: .45rem;
    }
    .colour-heading h2 {
      margin: 0;
      color: #bbcae0;
      font-size: .78rem;
      font-weight: 700;
      letter-spacing: .04em;
      text-transform: uppercase;
    }
    .colour-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: .45rem .7rem;
    }
    .colour-control {
      display: grid;
      grid-template-columns: 1.8rem minmax(0, 1fr);
      align-items: center;
      gap: .2rem .45rem;
      min-width: 0;
    }
    .colour-label {
      min-width: 0;
      margin: 0;
      color: #dbe8fa;
      font-size: .76rem;
      font-weight: 600;
      letter-spacing: 0;
      line-height: 1.2;
      text-transform: none;
    }
    .size-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: .55rem .7rem;
    }
    .size-control {
      min-width: 0;
    }
    .size-label {
      min-height: 1.85rem;
      margin: 0 0 .25rem;
      color: #dbe8fa;
      font-size: .72rem;
      font-weight: 600;
      letter-spacing: 0;
      line-height: 1.25;
      text-transform: none;
    }
    .size-control input[type="number"] {
      min-height: 2.35rem;
      padding: .45rem .6rem;
    }
    input[type="color"] {
      width: 1.8rem;
      height: 1.55rem;
      border: 1px solid #2a4568;
      border-radius: .4rem;
      background: #0b1a2d;
      padding: .12rem;
      cursor: pointer;
    }
    input[type="color"]:disabled {
      cursor: default;
      filter: grayscale(1);
      opacity: .35;
    }
    .mini-check {
      grid-column: 1 / -1;
      display: inline-flex;
      align-items: center;
      gap: .3rem;
      margin: .05rem 0 0;
      color: #9db1cb;
      font-size: .68rem;
      font-weight: 600;
      letter-spacing: 0;
      text-transform: none;
    }
    .mini-check input[type="checkbox"] {
      width: .82rem;
      height: .82rem;
      margin: 0;
    }
    .reset {
      border: 1px solid #2d527d;
      border-radius: .4rem;
      background: #0d2139;
      color: #dbeaff;
      padding: .3rem .5rem;
      font-size: .7rem;
      font-weight: 700;
      cursor: pointer;
    }
    .reset:hover { filter: brightness(1.08); }
    input[type="checkbox"] {
      width: 1rem;
      height: 1rem;
      accent-color: #5aa9ff;
    }
    .primary {
      min-height: 2.85rem;
      border: 0;
      border-radius: .6rem;
      background: linear-gradient(135deg, #64afff, #3d8bfd);
      color: #06101d;
      font-weight: 800;
      cursor: pointer;
    }
    .primary:hover { filter: brightness(1.08); }
    .primary:disabled { cursor: wait; opacity: .6; }
    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      min-height: 2.5rem;
    }
    #status {
      color: #9db1cb;
      font-size: .85rem;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    #status.error { color: #ff9aa9; white-space: normal; }
    .download {
      flex: 0 0 auto;
      border: 1px solid #2d527d;
      border-radius: .5rem;
      background: #0d2139;
      color: #dbeaff;
      padding: .55rem .8rem;
      font-weight: 700;
      cursor: pointer;
    }
    .download:disabled { opacity: .4; cursor: default; }
    .stage {
      display: grid;
      place-items: center;
      min-height: 28rem;
      overflow: auto;
      border: 1px solid #1f3552;
      border-radius: .9rem;
      background:
        linear-gradient(45deg, #0b1727 25%, transparent 25%),
        linear-gradient(-45deg, #0b1727 25%, transparent 25%),
        linear-gradient(45deg, transparent 75%, #0b1727 75%),
        linear-gradient(-45deg, transparent 75%, #0b1727 75%),
        #0e1c2e;
      background-size: 24px 24px;
      background-position: 0 0, 0 12px, 12px -12px, -12px 0;
      padding: 1rem;
    }
    #preview {
      width: 100%;
      display: grid;
      place-items: center;
    }
    #preview svg {
      display: block;
      width: min(100%, 90rem);
      height: auto;
      border-radius: .35rem;
      box-shadow: 0 1rem 4rem rgba(0, 0, 0, .28);
    }
    .empty {
      max-width: 27rem;
      text-align: center;
      color: #8195b0;
      line-height: 1.5;
    }
    code {
      color: #b9dcff;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    @media (max-width: 850px) {
      .shell { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid #1f3552; }
      main { min-height: 70vh; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <h1>glyphblueprint</h1>
      <p class="lede">Local end-to-end preview. Every render is generated from the selected font by the Python exporter.</p>
      <form id="controls">
        <div>
          <label for="fontPath">Font path</label>
          <div class="font-source">
            <input id="fontPath" name="font_path" type="text" value="examples/BlueprintDemo.glyphs" spellcheck="false">
            <input class="file-input" id="fontFile" type="file" accept=".glyphs,.otf,.ttf,.woff,.woff2">
            <label class="file-picker" for="fontFile">Choose file…</label>
          </div>
          <div class="file-name" id="selectedFontName" aria-live="polite">No uploaded file selected</div>
        </div>
        <div>
          <label for="text">Text or /glyph/name</label>
          <input id="text" name="text" type="text" value="Vao" maxlength="256">
        </div>
        <div class="row">
          <div>
            <label for="preset">Preset</label>
            <select id="preset" name="preset">
              __PRESET_OPTIONS__
            </select>
          </div>
          <div>
            <label for="shape">Marker shape</label>
            <select id="shape" name="shape">
              <option value="" selected>Preset default</option>
              <option>circle</option>
              <option>square</option>
              <option>diamond</option>
              <option>triangle</option>
              <option>cross</option>
              <option>none</option>
            </select>
          </div>
        </div>
        <div class="row">
          <div>
            <label for="frame">Frame</label>
            <select id="frame" name="frame">
              <option value="auto">Auto</option>
              <option value="em">Em square</option>
              <option value="metrics">Metrics</option>
            </select>
          </div>
          <div>
            <label for="width">Width (px)</label>
            <input id="width" name="width" type="number" value="1400" min="320" max="4000" step="20">
          </div>
        </div>
        <div class="row">
          <div>
            <label for="tracking">Tracking</label>
            <input id="tracking" name="tracking" type="number" value="0" min="-10000" max="10000" step="5">
          </div>
          <div>
            <label for="layer">Named layer</label>
            <input id="layer" name="layer" type="text" placeholder="Optional">
          </div>
        </div>
        <fieldset>
          <legend>Options</legend>
          <div class="checks">
            <label class="check"><input id="metrics" name="metrics" type="checkbox"> Show metrics</label>
          </div>
          <div class="checks">
            <label class="check"><input id="metricLines" name="metric_lines" type="checkbox" data-metric-control checked disabled> Metric lines</label>
            <label class="check"><input id="metricNumbers" name="metric_numbers" type="checkbox" data-metric-control checked disabled> Metric numbers</label>
          </div>
          <div class="checks">
            <label class="check"><input id="metricBaseline" name="metric_names" type="checkbox" value="baseline" data-metric-control checked disabled> Baseline</label>
            <label class="check"><input id="metricXheight" name="metric_names" type="checkbox" value="xheight" data-metric-control checked disabled> X-height</label>
            <label class="check"><input id="metricCapheight" name="metric_names" type="checkbox" value="capheight" data-metric-control checked disabled> Cap height</label>
            <label class="check"><input id="metricAscender" name="metric_names" type="checkbox" value="ascender" data-metric-control checked disabled> Ascender</label>
            <label class="check"><input id="metricDescender" name="metric_names" type="checkbox" value="descender" data-metric-control checked disabled> Descender</label>
            <label class="check"><input id="metricSidebearings" name="metric_names" type="checkbox" value="sidebearings" data-metric-control checked disabled> Side bearings</label>
          </div>
          <div class="checks">
            <label class="check"><input id="compound" name="compound" type="checkbox"> Remove overlap</label>
            <label class="check"><input id="kerning" name="kerning" type="checkbox" checked> Apply kerning</label>
          </div>
        </fieldset>
        <section aria-labelledby="coloursHeading">
          <div class="colour-heading">
            <h2 id="coloursHeading">Colours</h2>
            <button class="reset" id="resetColours" type="button">Reset to preset</button>
          </div>
          <div class="colour-grid">
            <div class="colour-control">
              <input id="canvasBackground" type="color" data-color-path="canvas.background">
              <label class="colour-label" for="canvasBackground">Background</label>
              <label class="mini-check"><input id="transparentBackground" type="checkbox"> Transparent</label>
            </div>
            <div class="colour-control">
              <input id="outlineStroke" type="color" data-color-path="outline.stroke">
              <label class="colour-label" for="outlineStroke">Outline stroke</label>
            </div>
            <div class="colour-control">
              <input id="outlineFill" type="color" data-color-path="outline.fill">
              <label class="colour-label" for="outlineFill">Outline fill</label>
              <label class="mini-check"><input id="fillOutline" type="checkbox"> Fill outline</label>
            </div>
            <div class="colour-control">
              <input id="handleLines" type="color" data-color-path="handles.line.color">
              <label class="colour-label" for="handleLines">Handle lines</label>
            </div>
            <div class="colour-control">
              <input id="handlePointFill" type="color" data-color-path="handles.point.fill">
              <label class="colour-label" for="handlePointFill">Handle point fill</label>
            </div>
            <div class="colour-control">
              <input id="handlePointStroke" type="color" data-color-path="handles.point.stroke">
              <label class="colour-label" for="handlePointStroke">Handle point stroke</label>
            </div>
            <div class="colour-control">
              <input id="cornerNodeFill" type="color" data-color-path="nodes.corner.fill">
              <label class="colour-label" for="cornerNodeFill">Corner node fill</label>
            </div>
            <div class="colour-control">
              <input id="cornerNodeStroke" type="color" data-color-path="nodes.corner.stroke">
              <label class="colour-label" for="cornerNodeStroke">Corner node stroke</label>
            </div>
            <div class="colour-control">
              <input id="smoothNodeFill" type="color" data-color-path="nodes.smooth.fill">
              <label class="colour-label" for="smoothNodeFill">Smooth node fill</label>
            </div>
            <div class="colour-control">
              <input id="smoothNodeStroke" type="color" data-color-path="nodes.smooth.stroke">
              <label class="colour-label" for="smoothNodeStroke">Smooth node stroke</label>
            </div>
            <div class="colour-control">
              <input id="metricGuides" type="color" data-color-path="metrics.line.color">
              <label class="colour-label" for="metricGuides">Metric guides</label>
            </div>
            <div class="colour-control">
              <input id="metricLabels" type="color" data-color-path="metrics.label_color">
              <label class="colour-label" for="metricLabels">Metric labels (text)</label>
            </div>
          </div>
        </section>
        <section aria-labelledby="sizesHeading">
          <div class="colour-heading">
            <h2 id="sizesHeading">Sizes &amp; weights</h2>
          </div>
          <div class="size-grid">
            <div class="size-control">
              <label class="size-label" for="handlePointSize">Handle point size</label>
              <input id="handlePointSize" type="number" step="0.1" min="0" max="20" data-size-path="handles.point.size">
            </div>
            <div class="size-control">
              <label class="size-label" for="handlePointStroke">Handle point stroke</label>
              <input id="handlePointStroke" type="number" step="0.1" min="0" max="10" data-size-path="handles.point.stroke_width">
            </div>
            <div class="size-control">
              <label class="size-label" for="cornerNodeSize">Corner node size</label>
              <input id="cornerNodeSize" type="number" step="0.1" min="0" max="20" data-size-path="nodes.corner.size">
            </div>
            <div class="size-control">
              <label class="size-label" for="smoothNodeSize">Smooth node size</label>
              <input id="smoothNodeSize" type="number" step="0.1" min="0" max="20" data-size-path="nodes.smooth.size">
            </div>
            <div class="size-control">
              <label class="size-label" for="cornerNodeStroke">Corner node stroke</label>
              <input id="cornerNodeStroke" type="number" step="0.1" min="0" max="10" data-size-path="nodes.corner.stroke_width">
            </div>
            <div class="size-control">
              <label class="size-label" for="smoothNodeStroke">Smooth node stroke</label>
              <input id="smoothNodeStroke" type="number" step="0.1" min="0" max="10" data-size-path="nodes.smooth.stroke_width">
            </div>
            <div class="size-control">
              <label class="size-label" for="outlineWidth">Outline stroke width</label>
              <input id="outlineWidth" type="number" step="0.1" min="0" max="10" data-size-path="outline.width">
            </div>
            <div class="size-control">
              <label class="size-label" for="handleLineWidth">Handle line width</label>
              <input id="handleLineWidth" type="number" step="0.1" min="0" max="10" data-size-path="handles.line.width">
            </div>
            <div class="size-control">
              <label class="size-label" for="metricGuideWidth">Metric guide width</label>
              <input id="metricGuideWidth" type="number" step="0.1" min="0" max="10" data-size-path="metrics.line.width">
            </div>
          </div>
        </section>
        <button class="primary" id="renderButton" type="submit">Render blueprint</button>
      </form>
    </aside>
    <main>
      <div class="toolbar">
        <div id="status" role="status">Ready</div>
        <button class="download" id="downloadButton" type="button" disabled>Download SVG</button>
      </div>
      <section class="stage" aria-label="SVG preview">
        <div id="preview"><p class="empty">Choose a font and render. The bundled <code>examples/BlueprintDemo.glyphs</code> is a good starting point &mdash; try it with overlap removal enabled.</p></div>
      </section>
    </main>
  </div>
  <script>
    const PRESET_COLORS = __PRESET_COLORS__;
    const PRESET_SIZES = __PRESET_SIZES__;
    const form = document.querySelector("#controls");
    const preview = document.querySelector("#preview");
    const status = document.querySelector("#status");
    const renderButton = document.querySelector("#renderButton");
    const downloadButton = document.querySelector("#downloadButton");
    const resetColours = document.querySelector("#resetColours");
    const fontFile = document.querySelector("#fontFile");
    const selectedFontName = document.querySelector("#selectedFontName");
    const transparentBackground = document.querySelector("#transparentBackground");
    const fillOutline = document.querySelector("#fillOutline");
    const colorInputs = Array.from(form.querySelectorAll("[data-color-path]"));
    const sizeInputs = Array.from(form.querySelectorAll("[data-size-path]"));
    const metricControls = Array.from(form.querySelectorAll("[data-metric-control]"));
    const metricNameInputs = Array.from(form.querySelectorAll('input[name="metric_names"]'));
    const backgroundInput = form.querySelector('[data-color-path="canvas.background"]');
    const outlineFillInput = form.querySelector('[data-color-path="outline.fill"]');
    const optionalFillStrokes = {
      "handles.point.fill": "handles.point.stroke",
      "nodes.corner.fill": "nodes.corner.stroke",
      "nodes.smooth.fill": "nodes.smooth.stroke"
    };
    const touchedColors = new Set();
    const touchedSizes = new Set();
    let latestSvg = "";

    function seedColorsFromPreset() {
      const presetColors = PRESET_COLORS[form.preset.value];
      if (!presetColors) return;
      colorInputs.forEach((input) => {
        const path = input.dataset.colorPath;
        let value = presetColors[path];
        if (value === null) {
          const strokePath = optionalFillStrokes[path] || "outline.stroke";
          value = presetColors[strokePath];
        }
        input.value = value;
      });
      transparentBackground.checked = presetColors["canvas.background"] === null;
      backgroundInput.disabled = transparentBackground.checked;
      fillOutline.checked = presetColors.fill_enabled;
      outlineFillInput.disabled = !fillOutline.checked;
      touchedColors.clear();
    }

    function seedSizesFromPreset() {
      const presetSizes = PRESET_SIZES[form.preset.value];
      if (!presetSizes) return;
      sizeInputs.forEach((input) => {
        input.value = presetSizes[input.dataset.sizePath];
      });
      touchedSizes.clear();
    }

    function seedControlsFromPreset() {
      seedColorsFromPreset();
      seedSizesFromPreset();
    }

    function updateMetricControls() {
      metricControls.forEach((input) => {
        input.disabled = !form.metrics.checked;
      });
    }

    function payload() {
      const colors = {};
      const sizes = {};
      colorInputs.forEach((input) => {
        const path = input.dataset.colorPath;
        if (!touchedColors.has(path)) return;
        colors[path] = (
          path === "canvas.background" && transparentBackground.checked
            ? "none"
            : input.value
        );
      });
      sizeInputs.forEach((input) => {
        const path = input.dataset.sizePath;
        if (!touchedSizes.has(path)) return;
        sizes[path] = Number(input.value);
      });
      const request = {
        font_path: form.font_path.value,
        text: form.text.value,
        preset: form.preset.value,
        shape: form.shape.value,
        frame: form.frame.value,
        width: Number(form.width.value),
        tracking: Number(form.tracking.value),
        layer: form.layer.value,
        compound: form.compound.checked,
        metrics: form.metrics.checked,
        metric_lines: form.metric_lines.checked,
        metric_numbers: form.metric_numbers.checked,
        metric_names: metricNameInputs
          .filter((input) => input.checked)
          .map((input) => input.value),
        apply_kerning: form.kerning.checked,
        fill_enabled: fillOutline.checked,
        colors,
        sizes
      };
      return request;
    }

    function showError(error) {
      latestSvg = "";
      preview.innerHTML = `<p class="empty">The preview could not be generated. Check the font path and settings.</p>`;
      status.className = "error";
      status.textContent = error.message;
    }

    async function renderBlueprint(event) {
      if (event) event.preventDefault();
      renderButton.disabled = true;
      downloadButton.disabled = true;
      status.className = "";
      status.textContent = "Rendering…";
      try {
        const response = await fetch("/api/render", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload())
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Render failed");
        latestSvg = result.svg;
        preview.innerHTML = result.svg;
        const details = result.summary;
        status.textContent = `${details.glyphs} glyphs · ${details.nodes} nodes · ${details.width} × ${details.height}`;
        downloadButton.disabled = false;
      } catch (error) {
        showError(error);
      } finally {
        renderButton.disabled = false;
      }
    }

    async function uploadFont() {
      const file = fontFile.files[0];
      if (!file) return;
      selectedFontName.textContent = file.name;
      renderButton.disabled = true;
      downloadButton.disabled = true;
      status.className = "";
      status.textContent = "Uploading…";
      try {
        const response = await fetch("/api/upload", {
          method: "POST",
          headers: {
            "Content-Type": "application/octet-stream",
            "X-Filename": encodeURIComponent(file.name)
          },
          body: file
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || "Upload failed");
        form.font_path.value = result.font_path;
        selectedFontName.textContent = result.name;
        await renderBlueprint();
      } catch (error) {
        showError(error);
      } finally {
        renderButton.disabled = false;
      }
    }

    colorInputs.forEach((input) => {
      input.addEventListener("input", () => {
        touchedColors.add(input.dataset.colorPath);
      });
    });
    sizeInputs.forEach((input) => {
      input.addEventListener("input", () => {
        touchedSizes.add(input.dataset.sizePath);
      });
    });
    transparentBackground.addEventListener("change", () => {
      backgroundInput.disabled = transparentBackground.checked;
      touchedColors.add("canvas.background");
    });
    fillOutline.addEventListener("change", () => {
      outlineFillInput.disabled = !fillOutline.checked;
    });
    form.preset.addEventListener("change", seedControlsFromPreset);
    resetColours.addEventListener("click", seedControlsFromPreset);
    form.metrics.addEventListener("change", updateMetricControls);
    form.addEventListener("submit", renderBlueprint);
    fontFile.addEventListener("change", uploadFont);
    downloadButton.addEventListener("click", () => {
      if (!latestSvg) return;
      const url = URL.createObjectURL(new Blob([latestSvg], {type: "image/svg+xml"}));
      const link = document.createElement("a");
      link.href = url;
      link.download = "glyphblueprint.svg";
      link.click();
      URL.revokeObjectURL(url);
    });

    seedControlsFromPreset();
    updateMetricControls();
    renderBlueprint();
  </script>
</body>
</html>
"""


def _preview_page() -> str:
    presets = available_presets()
    options = []
    preset_colors = {}
    preset_sizes = {}
    for name in presets:
        selected = " selected" if name == "blueprint" else ""
        options.append(
            '<option value="{}"{}>{}</option>'.format(
                html.escape(name, quote=True),
                selected,
                html.escape(name.replace("-", " ").title()),
            )
        )
        resolved = resolve_style(preset=name)
        colors = {
            path: resolved.get_path(path)
            for path in _COLOR_PATHS
        }
        colors["fill_enabled"] = resolved.outline.fill_enabled
        preset_colors[name] = colors
        preset_sizes[name] = {
            path: resolved.get_path(path)
            for path in _SIZE_LIMITS
        }

    colors_json = json.dumps(
        preset_colors,
        sort_keys=True,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    sizes_json = json.dumps(
        preset_sizes,
        sort_keys=True,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    return (
        _PAGE_TEMPLATE
        .replace("__PRESET_OPTIONS__", "\n              ".join(options))
        .replace("__PRESET_COLORS__", colors_json)
        .replace("__PRESET_SIZES__", sizes_json)
    )


def _string(payload: Dict[str, Any], key: str, default: str = "") -> str:
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise ValueError("{} must be a string".format(key))
    return value.strip()


def _boolean(payload: Dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError("{} must be true or false".format(key))
    return value


def _number(
    payload: Dict[str, Any],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise ValueError("{} must be a number".format(key))
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("{} must be a number".format(key)) from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(
            "{} must be between {:g} and {:g}".format(key, minimum, maximum)
        )
    return number


def _colors(payload: Dict[str, Any]) -> Dict[str, str]:
    values = payload.get("colors", {})
    if not isinstance(values, dict):
        raise ValueError("colors must be an object mapping style paths to colours")

    overrides = {}
    for key, value in values.items():
        if key not in _COLOR_PATH_SET:
            raise ValueError("unknown colour key {!r}".format(key))
        if not isinstance(value, str) or (
            value != "none" and _HEX_COLOR.fullmatch(value) is None
        ):
            raise ValueError(
                "invalid colour for {!r}: expected 'none' or a "
                "#rgb/#rrggbb hex colour, got {!r}".format(key, value)
            )
        overrides[key] = value
    return overrides


def _sizes(payload: Dict[str, Any]) -> Dict[str, float]:
    values = payload.get("sizes", {})
    if not isinstance(values, dict):
        raise ValueError("sizes must be an object mapping style paths to numbers")

    overrides = {}
    for key, value in values.items():
        if key not in _SIZE_LIMITS:
            raise ValueError("unknown size key {!r}".format(key))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("{!r} size must be a number".format(key))
        overrides[key] = _number(
            values,
            key,
            0.0,
            0.0,
            _SIZE_LIMITS[key],
        )
    return overrides


def _metric_names(payload: Dict[str, Any]) -> List[str]:
    if "metric_names" not in payload:
        return list(METRIC_NAMES)

    values = payload["metric_names"]
    if not isinstance(values, list):
        raise ValueError("metric_names must be a list of metric names")
    for value in values:
        if value not in METRIC_NAMES:
            raise ValueError(
                "unknown metric name {!r}; choose from {}".format(
                    value, ", ".join(METRIC_NAMES)
                )
            )
    return list(values)


def _font_path(value: str) -> Path:
    if not value:
        raise ValueError("font_path is required")
    path = Path(os.path.expanduser(value)).resolve()
    if path.suffix.lower() not in _SUPPORTED_FONT_SUFFIXES:
        raise ValueError(
            "font_path must end in {}".format(
                ", ".join(_SUPPORTED_FONT_SUFFIXES)
            )
        )
    if not path.exists():
        raise ValueError("font file not found: {}".format(path))
    if path.suffix.lower() == ".ufo":
        if not path.is_dir():
            raise ValueError("UFO path is not a directory: {}".format(path))
    elif not path.is_file():
        raise ValueError("font path is not a file: {}".format(path))
    return path


def _upload_basename(encoded_name: str) -> str:
    if not encoded_name:
        raise ValueError("X-Filename header is required")
    try:
        decoded = unquote(encoded_name, encoding="utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("X-Filename must be valid percent-encoded UTF-8") from exc
    basename = decoded.replace("\\", "/").rsplit("/", 1)[-1]
    if not basename or basename in (".", "..") or "\x00" in basename:
        raise ValueError("X-Filename must contain a valid filename")
    suffix = Path(basename).suffix.lower()
    if suffix not in _UPLOAD_FONT_SUFFIXES:
        raise ValueError(
            "unsupported upload extension {!r}; choose from {}".format(
                suffix or "(none)",
                ", ".join(sorted(_UPLOAD_FONT_SUFFIXES)),
            )
        )
    return basename


def _upload_destination(encoded_name: str) -> Tuple[str, Path]:
    basename = _upload_basename(encoded_name)
    destination = (_UPLOAD_DIRECTORY / basename).resolve()
    if destination.parent != _UPLOAD_DIRECTORY:
        raise ValueError("uploaded filename resolves outside the upload directory")
    return basename, destination


def render_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate one browser request and return its SVG plus a small summary."""
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")

    path = _font_path(_string(payload, "font_path"))
    text = _string(payload, "text")
    if not text:
        raise ValueError("text is required")
    if len(text) > 256:
        raise ValueError("text must be 256 characters or fewer")

    preset = _string(payload, "preset", "blueprint")
    if preset not in available_presets():
        raise ValueError(
            "unknown preset {!r}; choose from {}".format(
                preset, ", ".join(available_presets())
            )
        )
    # An empty shape means "leave the preset alone". Forcing one shape onto
    # corner nodes, smooth nodes and handle points collapses the corner/smooth
    # distinction, so the preview would stop showing what the tool actually
    # exports. Only override when the user explicitly picks a shape.
    shape = _string(payload, "shape")
    if shape and shape not in SHAPES:
        raise ValueError(
            "unknown marker shape {!r}; choose from {}".format(
                shape, ", ".join(SHAPES)
            )
        )
    frame = _string(payload, "frame", "auto")
    if frame not in FRAME_MODES:
        raise ValueError(
            "unknown frame {!r}; choose from {}".format(
                frame, ", ".join(FRAME_MODES)
            )
        )

    width = int(_number(payload, "width", 1400, 320, 4000))
    tracking = _number(payload, "tracking", 0.0, -10000, 10000)
    layer = _string(payload, "layer") or None
    compound = _boolean(payload, "compound", False)
    metrics = _boolean(payload, "metrics", False)
    metric_lines = _boolean(payload, "metric_lines", True)
    metric_numbers = _boolean(payload, "metric_numbers", True)
    metric_names = _metric_names(payload)
    apply_kerning = _boolean(payload, "apply_kerning", True)
    colors = _colors(payload)
    sizes = _sizes(payload)

    overrides = {
        "canvas": {"frame": frame, "width": width},
        "metrics": {
            "visible": metrics,
            "show": metric_names,
            "line": {"visible": metric_lines},
            "sidebearing_line": {"visible": metric_lines},
            "labels": metric_numbers,
        },
    }
    overrides.update(colors)
    overrides.update(sizes)
    if "fill_enabled" in payload:
        overrides["outline.fill_enabled"] = _boolean(
            payload, "fill_enabled", False
        )
    if shape:
        overrides["handles"] = {"point": {"shape": shape}}
        overrides["nodes"] = {
            "corner": {"shape": shape},
            "smooth": {"shape": shape},
        }

    render_overrides = overrides
    if metric_numbers and not metric_lines:
        # The SVG renderer positions metric labels while emitting their
        # matching rules. Generate both for the labels-only case, then remove
        # just those metric line elements from the browser response below.
        render_overrides = dict(overrides)
        render_metrics = dict(overrides["metrics"])
        render_metrics["line"] = {"visible": True}
        render_metrics["sidebearing_line"] = {"visible": True}
        render_overrides["metrics"] = render_metrics
    svg = blueprint(
        path,
        text,
        layer=layer,
        compound=compound,
        preset=preset,
        overrides=render_overrides,
        tracking=tracking,
        apply_kerning=apply_kerning,
        title="glyphblueprint preview",
    )
    if not metric_lines:
        svg = _METRIC_LINE_ELEMENT.sub("", svg)
    root = ET.fromstring(svg)
    glyphs = {
        element.get("data-glyph-index")
        for element in root.iter()
        if element.get("data-glyph-index") is not None
    }
    nodes = sum(
        1
        for layer_element in root.iter()
        if layer_element.get("data-layer") == "nodes"
        for element in layer_element.iter()
        if element.get("data-node-index") is not None
    )
    return {
        "svg": svg,
        "summary": {
            "glyphs": len(glyphs),
            "nodes": nodes,
            "width": root.get("width", "?"),
            "height": root.get("height", "?"),
            "font_path": str(path),
        },
    }


class PreviewHandler(BaseHTTPRequestHandler):
    """Serve the preview page and its local upload/render endpoints."""

    server_version = "glyphblueprint-preview/1.0"

    def _send(
        self, status: int, payload: bytes, content_type: str
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, data: Dict[str, Any]) -> None:
        payload = json.dumps(data).encode("utf-8")
        self._send(status, payload, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(
                200,
                _preview_page().encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if self.path == "/health":
            self._json(200, {"status": "ok"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/api/upload":
            self._upload_font()
            return
        if self.path != "/api/render":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "invalid Content-Length"})
            return
        if length <= 0 or length > _MAX_REQUEST_BYTES:
            self._json(413, {"error": "request body is empty or too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = render_request(payload)
        except (UnicodeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "invalid JSON: {}".format(exc)})
            return
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, result)

    def _upload_font(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() != (
            "application/octet-stream"
        ):
            self._json(
                415,
                {
                    "error": (
                        "upload Content-Type must be application/octet-stream"
                    )
                },
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "invalid Content-Length"})
            return
        if length <= 0:
            self._json(400, {"error": "upload body is empty"})
            return
        if length > _MAX_UPLOAD_BYTES:
            self._json(
                413,
                {"error": "upload exceeds the 64 MB size limit"},
            )
            return
        try:
            basename, destination = _upload_destination(
                self.headers.get("X-Filename", "")
            )
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("upload body ended before Content-Length bytes")
            destination.write_bytes(body)
        except (OSError, ValueError) as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(
            200,
            {"font_path": str(destination), "name": basename},
        )

    def log_message(self, format: str, *args: Any) -> None:
        return


def create_server(
    host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    """Create, but do not start, a localhost preview server."""
    return ThreadingHTTPServer((host, port), PreviewHandler)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glyphblueprint-preview",
        description="Run the local glyphblueprint browser preview.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        print(
            "glyphblueprint-preview: error: port must be between 0 and 65535",
            file=sys.stderr,
        )
        return 2
    try:
        server = create_server(args.host, args.port)
    except OSError as exc:
        # Re-running the preview while one is already open is the single most
        # likely failure here, and a socket traceback is a poor way to say so.
        if exc.errno == errno.EADDRINUSE:
            print(
                "glyphblueprint-preview: error: port {} is already in use. "
                "A preview may already be running at http://{}:{}/ — open "
                "that, or start this one on another port with --port {}.".format(
                    args.port, args.host, args.port, args.port + 1
                ),
                file=sys.stderr,
            )
        elif exc.errno in (errno.EACCES, errno.EPERM):
            print(
                "glyphblueprint-preview: error: not allowed to listen on port "
                "{}. Ports below 1024 need elevated privileges; try "
                "--port 8765.".format(args.port),
                file=sys.stderr,
            )
        else:
            print(
                "glyphblueprint-preview: error: could not start on {}:{}: "
                "{}".format(args.host, args.port, exc),
                file=sys.stderr,
            )
        return 2
    host, port = server.server_address[:2]
    print("glyphblueprint preview: http://{}:{}/".format(host, port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
