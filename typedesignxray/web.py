"""Local browser preview for testing Type Design X-Ray end to end."""

from __future__ import annotations

import argparse
import atexit
import errno
import html
import json
import logging
import math
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import webbrowser
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote

from fontTools.ttLib import TTCollection, TTFont

from .api import blueprint
from .config import available_presets, resolve_style
from .process import (
    catalog_request as process_catalog_request,
    export_request as process_export_request,
    render_request as process_render_request,
)
from .process_page import process_page
from .specimen import (
    catalog_request as specimen_catalog_request,
    render_request as specimen_render_request,
    specimen_page,
)
from .specimen_export import export_request as specimen_export_request
from .style import FRAME_MODES, METRIC_NAMES, SHAPES
from .tool_nav import tool_switcher


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
    tempfile.mkdtemp(prefix="type-design-xray-uploads-")
).resolve()
atexit.register(shutil.rmtree, str(_UPLOAD_DIRECTORY), ignore_errors=True)
_SPECIMEN_EXPORT_DIRECTORY = Path(
    tempfile.mkdtemp(prefix="type-design-xray-specimen-exports-")
).resolve()
atexit.register(
    shutil.rmtree,
    str(_SPECIMEN_EXPORT_DIRECTORY),
    ignore_errors=True,
)
_PROCESS_EXPORT_DIRECTORY = Path(
    tempfile.mkdtemp(prefix="type-design-xray-process-exports-")
).resolve()
atexit.register(
    shutil.rmtree,
    str(_PROCESS_EXPORT_DIRECTORY),
    ignore_errors=True,
)
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
_LABEL_PATHS = (
    "metrics.label_family",
    "metrics.label_size",
    "metrics.label_weight",
    "metrics.label_style",
)
_LABEL_PATH_SET = frozenset(_LABEL_PATHS)
_LABEL_WEIGHTS = frozenset(
    ("normal", "bold") + tuple(str(value) for value in range(100, 1000, 100))
)
_LABEL_STYLES = frozenset(("normal", "italic", "oblique"))
_FONT_SUFFIXES = frozenset((".ttf", ".otf", ".ttc", ".otc"))
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?")
_METRIC_LINE_ELEMENT = re.compile(
    r'<line\b(?=[^>]*\bdata-metric=")[^>]*/>'
)
_INSTALLED_FONT_FAMILIES: Optional[List[str]] = None
_INSTALLED_FONT_FAMILIES_LOCK = threading.Lock()


def _platform_font_directories() -> List[Path]:
    if sys.platform == "darwin":
        return [
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
            Path.home() / "Library" / "Fonts",
        ]
    if sys.platform.startswith("win"):
        windows_directory = Path(
            os.environ.get("WINDIR", r"C:\Windows")
        )
        local_app_data = Path(
            os.environ.get(
                "LOCALAPPDATA",
                str(Path.home() / "AppData" / "Local"),
            )
        )
        return [
            windows_directory / "Fonts",
            local_app_data / "Microsoft" / "Windows" / "Fonts",
        ]
    return [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".fonts",
        Path.home() / ".local" / "share" / "fonts",
    ]


def _font_family(font: TTFont) -> Optional[str]:
    name_table = font["name"]
    family = name_table.getDebugName(16) or name_table.getDebugName(1)
    if family is None:
        return None
    family = family.strip()
    if not family or family.startswith("."):
        return None
    return family


def _scan_installed_font_families() -> List[str]:
    families: Dict[str, str] = {}
    fonttools_logger = logging.getLogger("fontTools")
    previous_handlers = list(fonttools_logger.handlers)
    previous_propagate = fonttools_logger.propagate
    fonttools_logger.handlers = [logging.NullHandler()]
    fonttools_logger.propagate = False
    try:
        for directory in _platform_font_directories():
            try:
                paths = (
                    path
                    for path in directory.rglob("*")
                    if path.is_file() and path.suffix.lower() in _FONT_SUFFIXES
                )
                for path in paths:
                    try:
                        if path.suffix.lower() in (".ttc", ".otc"):
                            collection = TTCollection(
                                str(path), lazy=True
                            )
                            try:
                                fonts = collection.fonts
                                for font in fonts:
                                    family = _font_family(font)
                                    if family is not None:
                                        families.setdefault(
                                            family.casefold(), family
                                        )
                            finally:
                                collection.close()
                        else:
                            font = TTFont(
                                str(path), lazy=True, fontNumber=0
                            )
                            try:
                                family = _font_family(font)
                                if family is not None:
                                    families.setdefault(
                                        family.casefold(), family
                                    )
                            finally:
                                font.close()
                    except Exception:
                        continue
            except (OSError, RuntimeError):
                continue
    finally:
        fonttools_logger.handlers = previous_handlers
        fonttools_logger.propagate = previous_propagate
    return sorted(
        families.values(),
        key=lambda family: (family.casefold(), family),
    )


def installed_font_families() -> List[str]:
    """Return installed font families, scanning and caching on first use."""
    global _INSTALLED_FONT_FAMILIES
    if _INSTALLED_FONT_FAMILIES is None:
        with _INSTALLED_FONT_FAMILIES_LOCK:
            if _INSTALLED_FONT_FAMILIES is None:
                try:
                    _INSTALLED_FONT_FAMILIES = (
                        _scan_installed_font_families()
                    )
                except Exception:
                    _INSTALLED_FONT_FAMILIES = []
    return list(_INSTALLED_FONT_FAMILIES)


_PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Type Design X-Ray local preview</title>
  <link rel="icon" href="data:,">
  <style>
    :root {
      color-scheme: dark;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
      background: #111;
      color: #f5f5f3;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: #111;
    }
    button, input, select { font: inherit; }
    .shell {
      display: grid;
      grid-template-columns: minmax(19rem, 25rem) minmax(0, 1fr);
      height: 100vh;
      height: 100dvh;
      overflow: hidden;
    }
    aside {
      min-width: 0;
      min-height: 0;
      height: 100%;
      padding: 1.5rem;
      border-right: 1px solid #292927;
      background: #0a0a0a;
      overflow-y: auto;
    }
    main {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
      min-width: 0;
      min-height: 0;
      height: 100%;
      padding: 1.5rem;
      gap: 1rem;
      overflow: auto;
    }
    h1 {
      margin: 0;
      font-size: 1.1rem;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .lede {
      margin: .45rem 0 1.35rem;
      color: #8b8b88;
      font-size: .78rem;
      line-height: 1.45;
    }
    .tool-switcher {
      display: grid;
      gap: 1px;
      margin: -.35rem 0 1.4rem;
    }
    .tool-tab {
      display: grid;
      gap: 2px;
      padding: 7px 10px;
      border: 1px solid transparent;
      background: transparent;
      color: #b8b8b3;
      text-decoration: none;
    }
    .tool-tab:hover { border-color: #3d3d3a; background: #171715; color:#fff; }
    .tool-tab.active {
      border-color: #686864;
      background: #1e1e1b;
      color: #fff;
    }
    .tool-name {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .08em;
    }
    .tool-summary {
      color: #777;
      font-size: 9px;
      line-height: 1.35;
    }
    form { display: grid; gap: 1rem; }
    label, legend {
      display: block;
      margin-bottom: .4rem;
      color: #aaa;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    input[type="text"], input[type="number"], select {
      width: 100%;
      min-height: 2.65rem;
      border: 1px solid #3d3d3a;
      border-radius: 2px;
      background: #111;
      color: #f5f5f3;
      padding: .65rem .75rem;
      outline: none;
    }
    input:focus, select:focus {
      border-color: #fff;
      box-shadow: none;
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
      border: 1px solid #3d3d3a;
      border-radius: 2px;
      background: #111;
      color: #f5f5f3;
      padding: .65rem .75rem;
      font-size: .78rem;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: none;
      cursor: pointer;
    }
    .file-input:focus + .file-picker {
      border-color: #fff;
      box-shadow: none;
    }
    .file-name {
      min-height: 1.1rem;
      margin-top: .35rem;
      color: #777;
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
      color: #d8d8d3;
      font-size: .88rem;
      font-weight: 600;
      letter-spacing: 0;
      text-transform: none;
    }
    .check:has(input:disabled) {
      color: #777;
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
      color: #aaa;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
    }
    .colour-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: .45rem .7rem;
    }
    .colour-control {
      display: grid;
      gap: .35rem;
      min-width: 0;
      padding: .55rem;
      border: 1px solid #292927;
      border-radius: 2px;
      background: #0c0c0c;
    }
    .colour-input-row {
      display: grid;
      grid-template-columns: 1.8rem minmax(0, 1fr);
      align-items: center;
      gap: .45rem;
    }
    .colour-label {
      min-width: 0;
      margin: 0;
      color: #d8d8d3;
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
    .size-label-row {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: .45rem;
      min-height: 1.85rem;
    }
    .size-label {
      margin: 0;
      color: #d8d8d3;
      font-size: .72rem;
      font-weight: 600;
      letter-spacing: 0;
      line-height: 1.25;
      text-transform: none;
    }
    .size-value {
      flex: 0 0 auto;
      min-width: 2.5rem;
      color: #8b8b88;
      font-size: .72rem;
      font-variant-numeric: tabular-nums;
      line-height: 1.25;
      text-align: right;
    }
    .size-control input[type="range"] {
      -webkit-appearance: none;
      appearance: none;
      display: block;
      width: 100%;
      height: 1.15rem;
      margin: 0;
      border: 0;
      background: transparent;
      cursor: pointer;
      outline: none;
    }
    .size-control input[type="range"]::-webkit-slider-runnable-track {
      height: .42rem;
      border: 1px solid #3d3d3a;
      border-radius: 999px;
      background: #171715;
    }
    .size-control input[type="range"]::-webkit-slider-thumb {
      -webkit-appearance: none;
      appearance: none;
      width: 1rem;
      height: 1rem;
      margin-top: -.29rem;
      border: 2px solid #f5f5f3;
      border-radius: 50%;
      background: #777;
      box-shadow: 0 .12rem .4rem rgba(0, 0, 0, .35);
    }
    .size-control input[type="range"]::-moz-range-track {
      height: .32rem;
      border: 1px solid #3d3d3a;
      border-radius: 999px;
      background: #171715;
    }
    .size-control input[type="range"]::-moz-range-progress {
      height: .32rem;
      border: 1px solid #8b8b88;
      border-radius: 999px;
      background: #686864;
    }
    .size-control input[type="range"]::-moz-range-thumb {
      width: .78rem;
      height: .78rem;
      border: 2px solid #f5f5f3;
      border-radius: 50%;
      background: #777;
      box-shadow: 0 .12rem .4rem rgba(0, 0, 0, .35);
    }
    .size-control input[type="range"]:focus {
      box-shadow: none;
    }
    .size-control input[type="range"]:focus-visible {
      border-radius: 999px;
      outline: 1px solid #fff;
      outline-offset: 2px;
    }
    .size-number-row {
      display: flex;
      justify-content: flex-end;
      margin-top: .15rem;
    }
    .size-control input[type="number"] {
      width: 4.5rem;
      min-height: 1.8rem;
      padding: .25rem .4rem;
      font-size: .76rem;
      font-variant-numeric: tabular-nums;
    }
    .metric-label-type {
      display: grid;
      gap: .7rem;
    }
    .metric-label-type:disabled {
      opacity: .55;
    }
    .metric-label-type .colour-heading {
      margin-bottom: 0;
    }
    .metric-label-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: .7rem;
    }
    .metric-label-family {
      grid-column: 1 / -1;
    }
    .metric-label-family-custom {
      margin-top: .45rem;
    }
    .metric-label-family-custom[hidden] {
      display: none;
    }
    .metric-label-family .file-name {
      margin-bottom: 0;
    }
    input[type="color"] {
      width: 1.8rem;
      height: 1.55rem;
      border: 1px solid #3d3d3a;
      border-radius: .4rem;
      background: #111;
      padding: .12rem;
      cursor: pointer;
    }
    input[type="color"]:disabled {
      cursor: default;
      filter: grayscale(1);
      opacity: .35;
    }
    .hex-colour {
      min-width: 0;
      min-height: 1.9rem !important;
      padding: .3rem .45rem !important;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: .76rem;
      letter-spacing: .02em;
      text-transform: uppercase;
    }
    .hex-colour:disabled {
      color: #777;
      cursor: default;
      opacity: .55;
    }
    .hex-colour[aria-invalid="true"] {
      border-color: #ff6b81;
    }
    .mini-check {
      display: inline-flex;
      align-items: center;
      gap: .3rem;
      margin: .05rem 0 0;
      color: #8b8b88;
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
      border: 1px solid #3d3d3a;
      border-radius: 2px;
      background: #111;
      color: #f5f5f3;
      padding: .3rem .5rem;
      font-size: .7rem;
      font-weight: 700;
      cursor: pointer;
    }
    .reset:hover { filter: brightness(1.08); }
    .preset-file-panel {
      display: grid;
      gap: .55rem;
      padding: .7rem;
      border: 1px solid #292927;
      border-radius: 2px;
      background: #0c0c0c;
    }
    .preset-file-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      align-items: stretch;
      gap: .45rem;
    }
    .preset-file-row .file-picker,
    .preset-file-row .reset {
      min-height: 2.65rem;
    }
    .preset-help {
      margin: 0;
      color: #777;
      font-size: .7rem;
      line-height: 1.4;
    }
    input[type="checkbox"] {
      width: 1rem;
      height: 1rem;
      accent-color: #f5f5f3;
    }
    .primary {
      min-height: 2.85rem;
      border: 1px solid #f5f5f3;
      border-radius: 2px;
      background: #f5f5f3;
      color: #111;
      font-weight: 800;
      cursor: pointer;
    }
    .primary:hover { filter: brightness(1.08); }
    .primary:disabled { cursor: not-allowed; opacity: .55; }
    .toolbar {
      position: sticky;
      top: 0;
      z-index: 1;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      min-height: 2.5rem;
      background: #111;
    }
    #status {
      color: #8b8b88;
      font-size: .85rem;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    #status.error { color: #ff9aa9; white-space: normal; }
    .stage {
      display: grid;
      place-items: center;
      place-items: safe center;
      min-width: 0;
      min-height: 0;
      overflow: visible;
      border: 1px solid #292927;
      border-radius: 2px;
      background:
        linear-gradient(45deg, #151513 25%, transparent 25%),
        linear-gradient(-45deg, #151513 25%, transparent 25%),
        linear-gradient(45deg, transparent 75%, #151513 75%),
        linear-gradient(-45deg, transparent 75%, #151513 75%),
        #0d0d0c;
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
      color: #777;
      line-height: 1.5;
    }
    code {
      color: #d8d8d3;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }
    @media (max-width: 850px) {
      .shell {
        grid-template-columns: 1fr;
        height: auto;
        overflow: visible;
      }
      aside {
        height: auto;
        overflow-y: visible;
        border-right: 0;
        border-bottom: 1px solid #292927;
      }
      main {
        height: auto;
        min-height: 70vh;
        overflow: visible;
      }
      .preset-file-row { grid-template-columns: 1fr; }
      .toolbar { position: static; }
      .stage { min-height: 28rem; overflow: visible; }
    }
    @media (max-width: 520px) {
      .colour-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside>
      <h1>Type Design X-Ray</h1>
      <p class="lede">Three source-driven font tools in one local app. Switch tools at any time; your files stay on this computer.</p>
      __TOOL_SWITCHER__
      <form id="controls">
        <div>
          <label for="fontPath">Font path</label>
          <div class="font-source">
            <input id="fontPath" name="font_path" type="text" value="examples/Roboto-Regular-subset.ufo" spellcheck="false">
            <input class="file-input" id="fontFile" type="file" accept=".glyphs,.otf,.ttf,.woff,.woff2">
            <label class="file-picker" for="fontFile">Choose file…</label>
          </div>
          <div class="file-name" id="selectedFontName" aria-live="polite">No uploaded file selected</div>
        </div>
        <div>
          <label for="text">Text or /glyph/name</label>
          <input id="text" name="text" type="text" value="Type" maxlength="256">
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
        <section class="preset-file-panel" aria-labelledby="presetFileHeading">
          <label id="presetFileHeading" for="presetName">Reusable style preset</label>
          <div class="preset-file-row">
            <input id="presetName" type="text" maxlength="80" placeholder="My blueprint" aria-describedby="presetHelp">
            <button class="reset" id="savePreset" type="button">Export preset</button>
            <input class="file-input" id="presetFile" type="file" accept=".json,application/json">
            <label class="file-picker" for="presetFile">Load preset…</label>
          </div>
          <p class="preset-help" id="presetHelp">Downloads a JSON style file you can load here later or pass to <code>--config</code>.</p>
        </section>
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
            <label class="check"><input id="compound" name="compound" type="checkbox" checked> Remove overlap</label>
            <label class="check"><input id="kerning" name="kerning" type="checkbox" checked> Apply kerning</label>
          </div>
        </fieldset>
        <fieldset class="metric-label-type" id="metricLabelType" disabled>
          <div class="colour-heading">
            <h2 id="metricLabelTypeHeading">Metric label type</h2>
            <button class="reset" id="resetLabels" type="button">Reset to preset</button>
          </div>
          <div class="metric-label-grid" aria-labelledby="metricLabelTypeHeading">
            <div class="metric-label-family">
              <label for="labelFamily">Family</label>
              <select id="labelFamily" data-label-path="metrics.label_family">
                <option value="">Preset default</option>
                <option value="system-ui, sans-serif">System UI</option>
                <option value="sans-serif">Sans-serif</option>
                <option value="serif">Serif</option>
                <option value="monospace">Monospace</option>
                <option id="installedFontSeparator" disabled>────────── Installed fonts ──────────</option>
                <option id="customFamilyOption" value="__custom__">Custom…</option>
              </select>
              <input class="metric-label-family-custom" id="customLabelFamily" type="text" maxlength="200" placeholder="Futura, sans-serif" aria-label="Custom metric label font family" hidden>
              <p class="file-name">The SVG references this font by name; a machine without it installed will substitute a fallback when the file is opened elsewhere.</p>
            </div>
            <div class="size-control">
              <div class="size-label-row">
                <label class="size-label" for="labelSizeSlider">Size</label>
                <output class="size-value" id="labelSizeValue" for="labelSizeSlider labelSize"></output>
              </div>
              <input id="labelSizeSlider" type="range" min="6" max="32" step="0.5" data-label-slider="metrics.label_size">
              <div class="size-number-row">
                <input id="labelSize" type="number" min="4" max="72" step="0.5" data-label-path="metrics.label_size" aria-label="Exact metric label size">
              </div>
            </div>
            <div>
              <label for="labelWeight">Weight</label>
              <select id="labelWeight" data-label-path="metrics.label_weight">
                <option value="">Preset default</option>
                <option value="normal">Normal</option>
                <option value="bold">Bold</option>
                <option value="100">100</option>
                <option value="200">200</option>
                <option value="300">300</option>
                <option value="400">400</option>
                <option value="500">500</option>
                <option value="600">600</option>
                <option value="700">700</option>
                <option value="800">800</option>
                <option value="900">900</option>
              </select>
            </div>
            <div>
              <label for="labelStyle">Style</label>
              <select id="labelStyle" data-label-path="metrics.label_style">
                <option value="">Preset default</option>
                <option value="normal">Normal</option>
                <option value="italic">Italic</option>
                <option value="oblique">Oblique</option>
              </select>
            </div>
          </div>
        </fieldset>
        <section aria-labelledby="coloursHeading">
          <div class="colour-heading">
            <h2 id="coloursHeading">Colours</h2>
            <button class="reset" id="resetColours" type="button">Reset to preset</button>
          </div>
          <div class="colour-grid">
            <div class="colour-control">
              <label class="colour-label" for="canvasBackground">Background</label>
              <div class="colour-input-row">
                <input type="color" data-color-picker="canvas.background" aria-label="Background colour picker">
                <input class="hex-colour" id="canvasBackground" type="text" data-color-path="canvas.background" maxlength="7" spellcheck="false" aria-label="Background hex colour">
              </div>
              <label class="mini-check"><input type="checkbox" data-transparent-path="canvas.background" aria-label="Make background transparent"> Transparent</label>
            </div>
            <div class="colour-control">
              <label class="colour-label" for="outlineStroke">Outline stroke</label>
              <div class="colour-input-row">
                <input type="color" data-color-picker="outline.stroke" aria-label="Outline stroke colour picker">
                <input class="hex-colour" id="outlineStroke" type="text" data-color-path="outline.stroke" maxlength="7" spellcheck="false" aria-label="Outline stroke hex colour">
              </div>
              <label class="mini-check"><input type="checkbox" data-transparent-path="outline.stroke" aria-label="Make outline stroke transparent"> Transparent</label>
            </div>
            <div class="colour-control">
              <label class="colour-label" for="outlineFill">Outline fill</label>
              <div class="colour-input-row">
                <input type="color" data-color-picker="outline.fill" aria-label="Outline fill colour picker">
                <input class="hex-colour" id="outlineFill" type="text" data-color-path="outline.fill" maxlength="7" spellcheck="false" aria-label="Outline fill hex colour">
              </div>
              <label class="mini-check"><input type="checkbox" data-transparent-path="outline.fill" aria-label="Make outline fill transparent"> Transparent</label>
              <label class="mini-check"><input id="fillOutline" type="checkbox"> Fill outline</label>
            </div>
            <div class="colour-control">
              <label class="colour-label" for="handleLines">Handle lines</label>
              <div class="colour-input-row">
                <input type="color" data-color-picker="handles.line.color" aria-label="Handle lines colour picker">
                <input class="hex-colour" id="handleLines" type="text" data-color-path="handles.line.color" maxlength="7" spellcheck="false" aria-label="Handle lines hex colour">
              </div>
              <label class="mini-check"><input type="checkbox" data-transparent-path="handles.line.color" aria-label="Make handle lines transparent"> Transparent</label>
            </div>
            <div class="colour-control">
              <label class="colour-label" for="handlePointFill">Handle point fill</label>
              <div class="colour-input-row">
                <input type="color" data-color-picker="handles.point.fill" aria-label="Handle point fill colour picker">
                <input class="hex-colour" id="handlePointFill" type="text" data-color-path="handles.point.fill" maxlength="7" spellcheck="false" aria-label="Handle point fill hex colour">
              </div>
              <label class="mini-check"><input type="checkbox" data-transparent-path="handles.point.fill" aria-label="Make handle point fill transparent"> Transparent</label>
            </div>
            <div class="colour-control">
              <label class="colour-label" for="handlePointStroke">Handle point stroke</label>
              <div class="colour-input-row">
                <input type="color" data-color-picker="handles.point.stroke" aria-label="Handle point stroke colour picker">
                <input class="hex-colour" id="handlePointStroke" type="text" data-color-path="handles.point.stroke" maxlength="7" spellcheck="false" aria-label="Handle point stroke hex colour">
              </div>
              <label class="mini-check"><input type="checkbox" data-transparent-path="handles.point.stroke" aria-label="Make handle point stroke transparent"> Transparent</label>
            </div>
            <div class="colour-control">
              <label class="colour-label" for="cornerNodeFill">Corner node fill</label>
              <div class="colour-input-row">
                <input type="color" data-color-picker="nodes.corner.fill" aria-label="Corner node fill colour picker">
                <input class="hex-colour" id="cornerNodeFill" type="text" data-color-path="nodes.corner.fill" maxlength="7" spellcheck="false" aria-label="Corner node fill hex colour">
              </div>
              <label class="mini-check"><input type="checkbox" data-transparent-path="nodes.corner.fill" aria-label="Make corner node fill transparent"> Transparent</label>
            </div>
            <div class="colour-control">
              <label class="colour-label" for="cornerNodeStroke">Corner node stroke</label>
              <div class="colour-input-row">
                <input type="color" data-color-picker="nodes.corner.stroke" aria-label="Corner node stroke colour picker">
                <input class="hex-colour" id="cornerNodeStroke" type="text" data-color-path="nodes.corner.stroke" maxlength="7" spellcheck="false" aria-label="Corner node stroke hex colour">
              </div>
              <label class="mini-check"><input type="checkbox" data-transparent-path="nodes.corner.stroke" aria-label="Make corner node stroke transparent"> Transparent</label>
            </div>
            <div class="colour-control">
              <label class="colour-label" for="smoothNodeFill">Smooth node fill</label>
              <div class="colour-input-row">
                <input type="color" data-color-picker="nodes.smooth.fill" aria-label="Smooth node fill colour picker">
                <input class="hex-colour" id="smoothNodeFill" type="text" data-color-path="nodes.smooth.fill" maxlength="7" spellcheck="false" aria-label="Smooth node fill hex colour">
              </div>
              <label class="mini-check"><input type="checkbox" data-transparent-path="nodes.smooth.fill" aria-label="Make smooth node fill transparent"> Transparent</label>
            </div>
            <div class="colour-control">
              <label class="colour-label" for="smoothNodeStroke">Smooth node stroke</label>
              <div class="colour-input-row">
                <input type="color" data-color-picker="nodes.smooth.stroke" aria-label="Smooth node stroke colour picker">
                <input class="hex-colour" id="smoothNodeStroke" type="text" data-color-path="nodes.smooth.stroke" maxlength="7" spellcheck="false" aria-label="Smooth node stroke hex colour">
              </div>
              <label class="mini-check"><input type="checkbox" data-transparent-path="nodes.smooth.stroke" aria-label="Make smooth node stroke transparent"> Transparent</label>
            </div>
            <div class="colour-control">
              <label class="colour-label" for="metricGuides">Metric guides</label>
              <div class="colour-input-row">
                <input type="color" data-color-picker="metrics.line.color" aria-label="Metric guides colour picker">
                <input class="hex-colour" id="metricGuides" type="text" data-color-path="metrics.line.color" maxlength="7" spellcheck="false" aria-label="Metric guides hex colour">
              </div>
              <label class="mini-check"><input type="checkbox" data-transparent-path="metrics.line.color" aria-label="Make metric guides transparent"> Transparent</label>
            </div>
            <div class="colour-control">
              <label class="colour-label" for="metricLabels">Metric labels (text)</label>
              <div class="colour-input-row">
                <input type="color" data-color-picker="metrics.label_color" aria-label="Metric labels colour picker">
                <input class="hex-colour" id="metricLabels" type="text" data-color-path="metrics.label_color" maxlength="7" spellcheck="false" aria-label="Metric labels hex colour">
              </div>
              <label class="mini-check"><input type="checkbox" data-transparent-path="metrics.label_color" aria-label="Make metric labels transparent"> Transparent</label>
            </div>
          </div>
        </section>
        <section aria-labelledby="sizesHeading">
          <div class="colour-heading">
            <h2 id="sizesHeading">Sizes &amp; weights</h2>
          </div>
          <div class="size-grid">
            <div class="size-control">
              <div class="size-label-row">
                <label class="size-label" for="handlePointSizeSlider">Handle point size</label>
                <output class="size-value" data-size-value for="handlePointSizeSlider handlePointSize"></output>
              </div>
              <input id="handlePointSizeSlider" type="range" min="0" max="12" step="0.25" data-size-slider="handles.point.size">
              <div class="size-number-row">
                <input id="handlePointSize" type="number" step="0.1" min="0" max="20" data-size-path="handles.point.size" aria-label="Exact handle point size">
              </div>
            </div>
            <div class="size-control">
              <div class="size-label-row">
                <label class="size-label" for="handlePointStrokeSlider">Handle point stroke</label>
                <output class="size-value" data-size-value for="handlePointStrokeSlider handlePointStroke"></output>
              </div>
              <input id="handlePointStrokeSlider" type="range" min="0" max="6" step="0.1" data-size-slider="handles.point.stroke_width">
              <div class="size-number-row">
                <input id="handlePointStroke" type="number" step="0.1" min="0" max="10" data-size-path="handles.point.stroke_width" aria-label="Exact handle point stroke">
              </div>
            </div>
            <div class="size-control">
              <div class="size-label-row">
                <label class="size-label" for="cornerNodeSizeSlider">Corner node size</label>
                <output class="size-value" data-size-value for="cornerNodeSizeSlider cornerNodeSize"></output>
              </div>
              <input id="cornerNodeSizeSlider" type="range" min="0" max="12" step="0.25" data-size-slider="nodes.corner.size">
              <div class="size-number-row">
                <input id="cornerNodeSize" type="number" step="0.1" min="0" max="20" data-size-path="nodes.corner.size" aria-label="Exact corner node size">
              </div>
            </div>
            <div class="size-control">
              <div class="size-label-row">
                <label class="size-label" for="smoothNodeSizeSlider">Smooth node size</label>
                <output class="size-value" data-size-value for="smoothNodeSizeSlider smoothNodeSize"></output>
              </div>
              <input id="smoothNodeSizeSlider" type="range" min="0" max="12" step="0.25" data-size-slider="nodes.smooth.size">
              <div class="size-number-row">
                <input id="smoothNodeSize" type="number" step="0.1" min="0" max="20" data-size-path="nodes.smooth.size" aria-label="Exact smooth node size">
              </div>
            </div>
            <div class="size-control">
              <div class="size-label-row">
                <label class="size-label" for="cornerNodeStrokeSlider">Corner node stroke</label>
                <output class="size-value" data-size-value for="cornerNodeStrokeSlider cornerNodeStroke"></output>
              </div>
              <input id="cornerNodeStrokeSlider" type="range" min="0" max="6" step="0.1" data-size-slider="nodes.corner.stroke_width">
              <div class="size-number-row">
                <input id="cornerNodeStroke" type="number" step="0.1" min="0" max="10" data-size-path="nodes.corner.stroke_width" aria-label="Exact corner node stroke">
              </div>
            </div>
            <div class="size-control">
              <div class="size-label-row">
                <label class="size-label" for="smoothNodeStrokeSlider">Smooth node stroke</label>
                <output class="size-value" data-size-value for="smoothNodeStrokeSlider smoothNodeStroke"></output>
              </div>
              <input id="smoothNodeStrokeSlider" type="range" min="0" max="6" step="0.1" data-size-slider="nodes.smooth.stroke_width">
              <div class="size-number-row">
                <input id="smoothNodeStroke" type="number" step="0.1" min="0" max="10" data-size-path="nodes.smooth.stroke_width" aria-label="Exact smooth node stroke">
              </div>
            </div>
            <div class="size-control">
              <div class="size-label-row">
                <label class="size-label" for="outlineWidthSlider">Outline stroke width</label>
                <output class="size-value" data-size-value for="outlineWidthSlider outlineWidth"></output>
              </div>
              <input id="outlineWidthSlider" type="range" min="0" max="8" step="0.1" data-size-slider="outline.width">
              <div class="size-number-row">
                <input id="outlineWidth" type="number" step="0.1" min="0" max="10" data-size-path="outline.width" aria-label="Exact outline stroke width">
              </div>
            </div>
            <div class="size-control">
              <div class="size-label-row">
                <label class="size-label" for="handleLineWidthSlider">Handle line width</label>
                <output class="size-value" data-size-value for="handleLineWidthSlider handleLineWidth"></output>
              </div>
              <input id="handleLineWidthSlider" type="range" min="0" max="6" step="0.1" data-size-slider="handles.line.width">
              <div class="size-number-row">
                <input id="handleLineWidth" type="number" step="0.1" min="0" max="10" data-size-path="handles.line.width" aria-label="Exact handle line width">
              </div>
            </div>
            <div class="size-control">
              <div class="size-label-row">
                <label class="size-label" for="metricGuideWidthSlider">Metric guide width</label>
                <output class="size-value" data-size-value for="metricGuideWidthSlider metricGuideWidth"></output>
              </div>
              <input id="metricGuideWidthSlider" type="range" min="0" max="6" step="0.1" data-size-slider="metrics.line.width">
              <div class="size-number-row">
                <input id="metricGuideWidth" type="number" step="0.1" min="0" max="10" data-size-path="metrics.line.width" aria-label="Exact metric guide width">
              </div>
            </div>
          </div>
        </section>
        <button class="primary" id="exportButton" type="button" disabled>Export SVG</button>
      </form>
    </aside>
    <main>
      <div class="toolbar">
        <div id="status" role="status">Ready</div>
      </div>
      <section class="stage" aria-label="SVG preview">
        <div id="preview"><p class="empty">Choose a font and the preview updates automatically. The bundled <code>examples/Roboto-Regular-subset.ufo</code> is a good starting point &mdash; try it with overlap removal on and off.</p></div>
      </section>
    </main>
  </div>
  <script>
    const PRESET_COLORS = __PRESET_COLORS__;
    const PRESET_SIZES = __PRESET_SIZES__;
    const PRESET_LABELS = __PRESET_LABELS__;
    const form = document.querySelector("#controls");
    const preview = document.querySelector("#preview");
    const status = document.querySelector("#status");
    const exportButton = document.querySelector("#exportButton");
    const resetColours = document.querySelector("#resetColours");
    const resetLabels = document.querySelector("#resetLabels");
    const fontFile = document.querySelector("#fontFile");
    const selectedFontName = document.querySelector("#selectedFontName");
    const presetName = document.querySelector("#presetName");
    const savePreset = document.querySelector("#savePreset");
    const presetFile = document.querySelector("#presetFile");
    const fillOutline = document.querySelector("#fillOutline");
    const colorInputs = Array.from(form.querySelectorAll("[data-color-path]"));
    const colorPickers = Array.from(form.querySelectorAll("[data-color-picker]"));
    const transparentInputs = Array.from(
      form.querySelectorAll("[data-transparent-path]")
    );
    const sizeInputs = Array.from(form.querySelectorAll("[data-size-path]"));
    const labelInputs = Array.from(form.querySelectorAll("[data-label-path]"));
    const metricControls = Array.from(form.querySelectorAll("[data-metric-control]"));
    const metricNameInputs = Array.from(form.querySelectorAll('input[name="metric_names"]'));
    const metricLabelType = document.querySelector("#metricLabelType");
    const labelFamily = document.querySelector("#labelFamily");
    const customLabelFamily = document.querySelector("#customLabelFamily");
    const customFamilyOption = document.querySelector("#customFamilyOption");
    const labelSize = document.querySelector("#labelSize");
    const labelSizeSlider = document.querySelector("#labelSizeSlider");
    const labelSizeValue = document.querySelector("#labelSizeValue");
    const labelWeight = document.querySelector("#labelWeight");
    const labelStyle = document.querySelector("#labelStyle");
    const sizeSliders = Array.from(form.querySelectorAll("[data-size-slider]"));
    const sizeInputsByPath = new Map(
      sizeInputs.map((input) => [input.dataset.sizePath, input])
    );
    const sizeSlidersByPath = new Map(
      sizeSliders.map((slider) => [slider.dataset.sizeSlider, slider])
    );
    const colorInputsByPath = new Map(
      colorInputs.map((input) => [input.dataset.colorPath, input])
    );
    const colorPickersByPath = new Map(
      colorPickers.map((input) => [input.dataset.colorPicker, input])
    );
    const transparentInputsByPath = new Map(
      transparentInputs.map((input) => [input.dataset.transparentPath, input])
    );
    const optionalFillStrokes = {
      "handles.point.fill": "handles.point.stroke",
      "nodes.corner.fill": "nodes.corner.stroke",
      "nodes.smooth.fill": "nodes.smooth.stroke"
    };
    const touchedColors = new Set();
    const touchedSizes = new Set();
    const touchedLabels = new Set();
    const discreteControls = Array.from(
      form.querySelectorAll('select, input[type="checkbox"]')
    ).filter((input) => !input.dataset.transparentPath);
    const textNumberInputs = Array.from(
      form.querySelectorAll('input[type="text"], input[type="number"]')
    ).filter(
      (input) => (
        input !== form.font_path &&
        input !== presetName &&
        !input.dataset.colorPath
      )
    );
    let latestSvg = "";
    let liveRenderTimer = null;
    let renderRequestCounter = 0;

    function normaliseHexColour(value) {
      if (typeof value !== "string") return null;
      let hex = value.trim();
      if (!hex.startsWith("#")) hex = `#${hex}`;
      if (/^#[0-9a-f]{3}$/i.test(hex)) {
        hex = `#${hex[1]}${hex[1]}${hex[2]}${hex[2]}${hex[3]}${hex[3]}`;
      }
      if (!/^#[0-9a-f]{6}$/i.test(hex)) return null;
      return hex.toUpperCase();
    }

    function syncColorDisabledState(path) {
      const transparent = transparentInputsByPath.get(path).checked;
      const fillDisabled = path === "outline.fill" && !fillOutline.checked;
      colorInputsByPath.get(path).disabled = transparent || fillDisabled;
      colorPickersByPath.get(path).disabled = transparent || fillDisabled;
    }

    function fallbackColour(path, presetColors) {
      const strokePath = optionalFillStrokes[path] || "outline.stroke";
      return (
        normaliseHexColour(presetColors[strokePath]) ||
        normaliseHexColour(colorInputsByPath.get(path).value) ||
        "#000000"
      );
    }

    function setColorControl(path, value, presetColors) {
      const textInput = colorInputsByPath.get(path);
      const picker = colorPickersByPath.get(path);
      const transparentInput = transparentInputsByPath.get(path);
      const transparent = value === null || value === "none";
      const normalised = (
        normaliseHexColour(value) ||
        fallbackColour(path, presetColors)
      );
      textInput.value = normalised;
      textInput.setCustomValidity("");
      textInput.setAttribute("aria-invalid", "false");
      picker.value = normalised.toLowerCase();
      transparentInput.checked = transparent;
      syncColorDisabledState(path);
    }

    function currentColour(path) {
      if (transparentInputsByPath.get(path).checked) return "none";
      const input = colorInputsByPath.get(path);
      const normalised = normaliseHexColour(input.value);
      if (normalised === null) {
        throw new Error(
          `${input.getAttribute("aria-label")} must use #RGB or #RRGGBB`
        );
      }
      return normalised;
    }

    function syncSizeFromNumber(input) {
      const slider = sizeSlidersByPath.get(input.dataset.sizePath);
      const value = Number(input.value);
      if (input.value !== "" && Number.isFinite(value)) {
        slider.value = String(
          Math.min(Number(slider.max), Math.max(Number(slider.min), value))
        );
      }
      const output = input
        .closest(".size-control")
        .querySelector("[data-size-value]");
      output.value = input.value || "—";
    }

    function syncSizeFromSlider(slider) {
      const input = sizeInputsByPath.get(slider.dataset.sizeSlider);
      input.value = slider.value;
      syncSizeFromNumber(input);
    }

    function syncLabelSizeFromNumber() {
      const value = Number(labelSize.value);
      if (labelSize.value !== "" && Number.isFinite(value)) {
        labelSizeSlider.value = String(
          Math.min(
            Number(labelSizeSlider.max),
            Math.max(Number(labelSizeSlider.min), value)
          )
        );
      }
      labelSizeValue.value = labelSize.value || "—";
    }

    function syncLabelSizeFromSlider() {
      labelSize.value = labelSizeSlider.value;
      syncLabelSizeFromNumber();
    }

    function selectedLabelFamily() {
      if (labelFamily.value === "__custom__") {
        return customLabelFamily.value;
      }
      return labelFamily.value;
    }

    function setLabelFamily(value) {
      const matchingOption = Array.from(labelFamily.options).find(
        (option) => option.value === value
      );
      if (matchingOption) {
        labelFamily.value = value;
      } else {
        labelFamily.value = "__custom__";
        customLabelFamily.value = value;
      }
      customLabelFamily.hidden = labelFamily.value !== "__custom__";
    }

    function seedColorsFromPreset() {
      const presetColors = PRESET_COLORS[form.preset.value];
      if (!presetColors) return;
      colorInputs.forEach((input) => {
        const path = input.dataset.colorPath;
        setColorControl(path, presetColors[path], presetColors);
      });
      fillOutline.checked = presetColors.fill_enabled;
      syncColorDisabledState("outline.fill");
      touchedColors.clear();
    }

    function seedSizesFromPreset() {
      const presetSizes = PRESET_SIZES[form.preset.value];
      if (!presetSizes) return;
      sizeInputs.forEach((input) => {
        input.value = presetSizes[input.dataset.sizePath];
        syncSizeFromNumber(input);
      });
      touchedSizes.clear();
    }

    function seedLabelsFromPreset() {
      const presetLabels = PRESET_LABELS[form.preset.value];
      if (!presetLabels) return;
      setLabelFamily(presetLabels["metrics.label_family"]);
      labelSize.value = presetLabels["metrics.label_size"];
      syncLabelSizeFromNumber();
      labelWeight.value = presetLabels["metrics.label_weight"];
      labelStyle.value = presetLabels["metrics.label_style"];
      touchedLabels.clear();
    }

    function seedControlsFromPreset() {
      seedColorsFromPreset();
      seedSizesFromPreset();
      seedLabelsFromPreset();
    }

    function updateMetricControls() {
      metricControls.forEach((input) => {
        input.disabled = !form.metrics.checked;
      });
      metricLabelType.disabled = (
        !form.metrics.checked || !form.metric_numbers.checked
      );
    }

    function payload() {
      const colors = {};
      const sizes = {};
      const labels = {};
      colorInputs.forEach((input) => {
        const path = input.dataset.colorPath;
        if (!touchedColors.has(path)) return;
        colors[path] = currentColour(path);
      });
      sizeInputs.forEach((input) => {
        const path = input.dataset.sizePath;
        if (!touchedSizes.has(path)) return;
        sizes[path] = Number(input.value);
      });
      labelInputs.forEach((input) => {
        const path = input.dataset.labelPath;
        if (!touchedLabels.has(path)) return;
        if (path === "metrics.label_family") {
          if (labelFamily.value === "") return;
          labels[path] = selectedLabelFamily();
          return;
        }
        if (
          (path === "metrics.label_weight" ||
            path === "metrics.label_style") &&
          input.value === ""
        ) {
          return;
        }
        labels[path] = (
          path === "metrics.label_size"
            ? Number(input.value)
            : input.value
        );
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
        sizes,
        labels
      };
      return request;
    }

    function stylePresetPayload() {
      const markerShape = form.shape.value;
      const config = {
        preset: form.preset.value,
        canvas: {
          background: currentColour("canvas.background"),
          frame: form.frame.value,
          width: Number(form.width.value)
        },
        outline: {
          stroke: currentColour("outline.stroke"),
          fill: currentColour("outline.fill"),
          width: Number(sizeInputsByPath.get("outline.width").value),
          fill_enabled: fillOutline.checked
        },
        handles: {
          point: {
            fill: currentColour("handles.point.fill"),
            stroke: currentColour("handles.point.stroke"),
            size: Number(sizeInputsByPath.get("handles.point.size").value),
            stroke_width: Number(
              sizeInputsByPath.get("handles.point.stroke_width").value
            )
          },
          line: {
            color: currentColour("handles.line.color"),
            width: Number(sizeInputsByPath.get("handles.line.width").value)
          }
        },
        nodes: {
          corner: {
            fill: currentColour("nodes.corner.fill"),
            stroke: currentColour("nodes.corner.stroke"),
            size: Number(sizeInputsByPath.get("nodes.corner.size").value),
            stroke_width: Number(
              sizeInputsByPath.get("nodes.corner.stroke_width").value
            )
          },
          smooth: {
            fill: currentColour("nodes.smooth.fill"),
            stroke: currentColour("nodes.smooth.stroke"),
            size: Number(sizeInputsByPath.get("nodes.smooth.size").value),
            stroke_width: Number(
              sizeInputsByPath.get("nodes.smooth.stroke_width").value
            )
          }
        },
        metrics: {
          visible: form.metrics.checked,
          show: metricNameInputs
            .filter((input) => input.checked)
            .map((input) => input.value),
          line: {
            color: currentColour("metrics.line.color"),
            width: Number(sizeInputsByPath.get("metrics.line.width").value),
            visible: form.metric_lines.checked
          },
          sidebearing_line: {
            visible: form.metric_lines.checked
          },
          labels: form.metric_numbers.checked,
          label_color: currentColour("metrics.label_color"),
          label_size: Number(labelSize.value),
          label_family: selectedLabelFamily(),
          label_weight: labelWeight.value,
          label_style: labelStyle.value
        }
      };
      if (markerShape) {
        config.handles.point.shape = markerShape;
        config.nodes.corner.shape = markerShape;
        config.nodes.smooth.shape = markerShape;
      }
      return config;
    }

    function safeDownloadName(value) {
      let name = value
        .trim()
        .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "-")
        .replace(/[\s.-]+$/g, "")
        .replace(/\s+/g, "-")
        .slice(0, 80);
      if (!name) name = "type-design-xray-preset";
      const stem = name.split(".")[0].toUpperCase();
      if (
        /^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])$/.test(stem)
      ) {
        name = `_${name}`;
      }
      return `${name}.json`;
    }

    function downloadText(text, type, filename) {
      const url = URL.createObjectURL(new Blob([text], {type}));
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    }

    function exportCurrentPreset() {
      const name = presetName.value.trim();
      if (!name) {
        presetName.setCustomValidity("Give this preset a name first.");
        presetName.reportValidity();
        return;
      }
      presetName.setCustomValidity("");
      try {
        const filename = safeDownloadName(name);
        const config = stylePresetPayload();
        downloadText(
          `${JSON.stringify(config, null, 2)}\n`,
          "application/json",
          filename
        );
        status.className = "";
        status.textContent = `Exported preset ${filename}`;
      } catch (error) {
        showError(error);
      }
    }

    function nestedValue(source, path) {
      let value = source;
      for (const part of path.split(".")) {
        if (
          value === null ||
          typeof value !== "object" ||
          !Object.prototype.hasOwnProperty.call(value, part)
        ) {
          return {found: false, value: undefined};
        }
        value = value[part];
      }
      return {found: true, value};
    }

    function applyLoadedPreset(config) {
      if (
        config === null ||
        typeof config !== "object" ||
        Array.isArray(config)
      ) {
        throw new Error("Preset JSON must contain an object.");
      }
      const basePreset = (
        typeof config.preset === "string" && config.preset.trim()
          ? config.preset.trim()
          : "blueprint"
      );
      if (!Object.prototype.hasOwnProperty.call(PRESET_COLORS, basePreset)) {
        throw new Error(`Unknown base preset "${basePreset}".`);
      }

      form.preset.value = basePreset;
      seedControlsFromPreset();
      const presetColors = PRESET_COLORS[basePreset];

      colorInputs.forEach((input) => {
        const path = input.dataset.colorPath;
        const loaded = nestedValue(config, path);
        if (!loaded.found) return;
        const isTransparent = (
          loaded.value === null ||
          (
            typeof loaded.value === "string" &&
            loaded.value.toLowerCase() === "none"
          )
        );
        if (!isTransparent && normaliseHexColour(loaded.value) === null) {
          throw new Error(
            `Invalid colour for ${path}; use #RGB, #RRGGBB, or "none".`
          );
        }
        setColorControl(
          path,
          isTransparent ? "none" : loaded.value,
          presetColors
        );
        touchedColors.add(path);
      });

      sizeInputs.forEach((input) => {
        const path = input.dataset.sizePath;
        const loaded = nestedValue(config, path);
        if (!loaded.found) return;
        const number = Number(loaded.value);
        if (!Number.isFinite(number)) {
          throw new Error(`Invalid numeric value for ${path}.`);
        }
        input.value = String(number);
        syncSizeFromNumber(input);
        touchedSizes.add(path);
      });

      const loadedFamily = nestedValue(config, "metrics.label_family");
      if (loadedFamily.found) {
        if (typeof loadedFamily.value !== "string") {
          throw new Error("Metric label family must be a string.");
        }
        setLabelFamily(loadedFamily.value);
        touchedLabels.add("metrics.label_family");
      }
      const loadedLabelSize = nestedValue(config, "metrics.label_size");
      if (loadedLabelSize.found) {
        const number = Number(loadedLabelSize.value);
        if (!Number.isFinite(number)) {
          throw new Error("Metric label size must be a number.");
        }
        labelSize.value = String(number);
        syncLabelSizeFromNumber();
        touchedLabels.add("metrics.label_size");
      }
      for (const [path, input] of [
        ["metrics.label_weight", labelWeight],
        ["metrics.label_style", labelStyle]
      ]) {
        const loaded = nestedValue(config, path);
        if (!loaded.found) continue;
        const available = Array.from(input.options).some(
          (option) => option.value === String(loaded.value)
        );
        if (!available) throw new Error(`Unsupported value for ${path}.`);
        input.value = String(loaded.value);
        touchedLabels.add(path);
      }

      const loadedFrame = nestedValue(config, "canvas.frame");
      if (loadedFrame.found) {
        const available = Array.from(form.frame.options).some(
          (option) => option.value === String(loadedFrame.value)
        );
        if (!available) throw new Error("Unsupported canvas frame.");
        form.frame.value = String(loadedFrame.value);
      }
      const loadedWidth = nestedValue(config, "canvas.width");
      if (loadedWidth.found) {
        const width = Number(loadedWidth.value);
        if (!Number.isFinite(width) || width < 320 || width > 4000) {
          throw new Error("Canvas width must be between 320 and 4000.");
        }
        form.width.value = String(width);
      }
      const loadedFill = nestedValue(config, "outline.fill_enabled");
      if (loadedFill.found) {
        if (typeof loadedFill.value !== "boolean") {
          throw new Error("Outline fill setting must be true or false.");
        }
        fillOutline.checked = loadedFill.value;
        syncColorDisabledState("outline.fill");
      }

      const shapePaths = [
        "handles.point.shape",
        "nodes.corner.shape",
        "nodes.smooth.shape"
      ];
      const shapes = shapePaths
        .map((path) => nestedValue(config, path))
        .filter((loaded) => loaded.found)
        .map((loaded) => String(loaded.value));
      if (shapes.length) {
        const uniqueShapes = new Set(shapes);
        if (uniqueShapes.size !== 1) {
          throw new Error(
            "This preset uses different marker shapes; the preview has one shared marker-shape control."
          );
        }
        const shape = shapes[0];
        const available = Array.from(form.shape.options).some(
          (option) => option.value === shape
        );
        if (!available) throw new Error(`Unsupported marker shape "${shape}".`);
        form.shape.value = shape;
      } else {
        form.shape.value = "";
      }

      const loadedMetrics = nestedValue(config, "metrics.visible");
      if (loadedMetrics.found) {
        if (typeof loadedMetrics.value !== "boolean") {
          throw new Error("Metrics visibility must be true or false.");
        }
        form.metrics.checked = loadedMetrics.value;
      }
      const loadedMetricLines = nestedValue(config, "metrics.line.visible");
      if (loadedMetricLines.found) {
        if (typeof loadedMetricLines.value !== "boolean") {
          throw new Error("Metric-line visibility must be true or false.");
        }
        form.metric_lines.checked = loadedMetricLines.value;
      }
      const loadedMetricLabels = nestedValue(config, "metrics.labels");
      if (loadedMetricLabels.found) {
        if (typeof loadedMetricLabels.value !== "boolean") {
          throw new Error("Metric-label visibility must be true or false.");
        }
        form.metric_numbers.checked = loadedMetricLabels.value;
      }
      const loadedMetricNames = nestedValue(config, "metrics.show");
      if (loadedMetricNames.found) {
        if (!Array.isArray(loadedMetricNames.value)) {
          throw new Error("Metric names must be an array.");
        }
        const selected = new Set(loadedMetricNames.value.map(String));
        metricNameInputs.forEach((input) => {
          input.checked = selected.has(input.value);
        });
      }
      updateMetricControls();
    }

    async function loadPresetFile() {
      const file = presetFile.files[0];
      if (!file) return;
      try {
        const config = JSON.parse(await file.text());
        applyLoadedPreset(config);
        presetName.value = file.name.replace(/\.json$/i, "");
        presetName.setCustomValidity("");
        renderLiveNow();
      } catch (error) {
        showError(
          new Error(`Could not load preset ${file.name}: ${error.message}`)
        );
      } finally {
        presetFile.value = "";
      }
    }

    function showError(error) {
      status.className = "error";
      status.textContent = error.message;
    }

    function cancelLiveRender() {
      if (liveRenderTimer === null) return;
      window.clearTimeout(liveRenderTimer);
      liveRenderTimer = null;
    }

    function renderLiveNow() {
      cancelLiveRender();
      exportButton.disabled = true;
      if (!form.font_path.value.trim()) return;
      const colorsValid = colorInputs.every((input) => input.checkValidity());
      if (!colorsValid) return;
      renderBlueprint();
    }

    function scheduleLiveRender(delay = 250) {
      cancelLiveRender();
      exportButton.disabled = true;
      liveRenderTimer = window.setTimeout(() => {
        liveRenderTimer = null;
        renderLiveNow();
      }, delay);
    }

    async function renderBlueprint() {
      const requestId = ++renderRequestCounter;
      exportButton.disabled = true;
      status.className = "";
      status.textContent = "Rendering…";
      try {
        const response = await fetch("/api/render", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload())
        });
        const result = await response.json();
        if (requestId !== renderRequestCounter) return;
        if (!response.ok) throw new Error(result.error || "Render failed");
        latestSvg = result.svg;
        preview.innerHTML = result.svg;
        const details = result.summary;
        status.textContent = `${details.glyphs} glyphs · ${details.nodes} nodes · ${details.width} × ${details.height}`;
        exportButton.disabled = false;
      } catch (error) {
        if (requestId !== renderRequestCounter) return;
        showError(error);
      }
    }

    async function uploadFont() {
      const file = fontFile.files[0];
      if (!file) return;
      selectedFontName.textContent = file.name;
      exportButton.disabled = true;
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
      }
    }

    async function loadInstalledFonts() {
      try {
        const response = await fetch("/api/fonts");
        if (!response.ok) return;
        const result = await response.json();
        if (!Array.isArray(result.families)) return;
        const currentFamily = selectedLabelFamily();
        const options = document.createDocumentFragment();
        result.families.forEach((family) => {
          if (typeof family !== "string") return;
          options.appendChild(new Option(family, family));
        });
        labelFamily.insertBefore(options, customFamilyOption);
        setLabelFamily(currentFamily);
      } catch (error) {
        // The generic and custom choices remain usable if discovery fails.
      }
    }

    colorInputs.forEach((input) => {
      input.addEventListener("input", () => {
        const path = input.dataset.colorPath;
        const normalised = normaliseHexColour(input.value);
        if (normalised === null) {
          input.setCustomValidity("Use #RGB or #RRGGBB.");
          input.setAttribute("aria-invalid", "true");
          exportButton.disabled = true;
          status.className = "error";
          status.textContent = `${input.getAttribute("aria-label")} must use #RGB or #RRGGBB`;
          return;
        }
        input.setCustomValidity("");
        input.setAttribute("aria-invalid", "false");
        colorPickersByPath.get(path).value = normalised.toLowerCase();
        transparentInputsByPath.get(path).checked = false;
        syncColorDisabledState(path);
        touchedColors.add(path);
        scheduleLiveRender();
      });
      input.addEventListener("change", () => {
        const normalised = normaliseHexColour(input.value);
        if (normalised !== null) input.value = normalised;
      });
    });
    colorPickers.forEach((picker) => {
      picker.addEventListener("input", () => {
        const path = picker.dataset.colorPicker;
        const input = colorInputsByPath.get(path);
        input.value = picker.value.toUpperCase();
        input.setCustomValidity("");
        input.setAttribute("aria-invalid", "false");
        transparentInputsByPath.get(path).checked = false;
        syncColorDisabledState(path);
        touchedColors.add(path);
        scheduleLiveRender();
      });
    });
    transparentInputs.forEach((input) => {
      input.addEventListener("change", () => {
        const path = input.dataset.transparentPath;
        syncColorDisabledState(path);
        touchedColors.add(path);
        renderLiveNow();
      });
    });
    sizeInputs.forEach((input) => {
      input.addEventListener("input", () => {
        touchedSizes.add(input.dataset.sizePath);
        syncSizeFromNumber(input);
      });
    });
    sizeSliders.forEach((slider) => {
      slider.addEventListener("input", () => {
        syncSizeFromSlider(slider);
        touchedSizes.add(slider.dataset.sizeSlider);
        scheduleLiveRender();
      });
    });
    labelSize.addEventListener("input", () => {
      touchedLabels.add("metrics.label_size");
      syncLabelSizeFromNumber();
    });
    labelSizeSlider.addEventListener("input", () => {
      syncLabelSizeFromSlider();
      touchedLabels.add("metrics.label_size");
      scheduleLiveRender();
    });
    labelFamily.addEventListener("change", () => {
      customLabelFamily.hidden = labelFamily.value !== "__custom__";
      touchedLabels.add("metrics.label_family");
    });
    customLabelFamily.addEventListener("input", () => {
      touchedLabels.add("metrics.label_family");
    });
    labelWeight.addEventListener("change", () => {
      touchedLabels.add("metrics.label_weight");
    });
    labelStyle.addEventListener("change", () => {
      touchedLabels.add("metrics.label_style");
    });
    fillOutline.addEventListener("change", () => {
      syncColorDisabledState("outline.fill");
    });
    form.preset.addEventListener("change", seedControlsFromPreset);
    resetColours.addEventListener("click", () => {
      seedControlsFromPreset();
      renderLiveNow();
    });
    resetLabels.addEventListener("click", () => {
      seedLabelsFromPreset();
      renderLiveNow();
    });
    form.metrics.addEventListener("change", updateMetricControls);
    form.metric_numbers.addEventListener("change", updateMetricControls);
    discreteControls.forEach((control) => {
      control.addEventListener("change", renderLiveNow);
    });
    textNumberInputs.forEach((input) => {
      input.addEventListener("input", () => {
        scheduleLiveRender();
      });
    });
    form.font_path.addEventListener("input", () => {
      cancelLiveRender();
      exportButton.disabled = true;
      if (!form.font_path.value.trim()) return;
      scheduleLiveRender(600);
    });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      renderLiveNow();
    });
    fontFile.addEventListener("change", uploadFont);
    presetName.addEventListener("input", () => {
      presetName.setCustomValidity("");
    });
    savePreset.addEventListener("click", exportCurrentPreset);
    presetFile.addEventListener("change", loadPresetFile);
    exportButton.addEventListener("click", () => {
      if (!latestSvg) return;
      downloadText(
        latestSvg,
        "image/svg+xml",
        "type-design-xray.svg"
      );
    });

    seedControlsFromPreset();
    updateMetricControls();
    loadInstalledFonts();
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
    preset_labels = {}
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
        preset_labels[name] = {
            path: resolved.get_path(path)
            for path in _LABEL_PATHS
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
    labels_json = json.dumps(
        preset_labels,
        sort_keys=True,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    return (
        _PAGE_TEMPLATE
        .replace("__TOOL_SWITCHER__", tool_switcher("xray"))
        .replace("__PRESET_OPTIONS__", "\n              ".join(options))
        .replace("__PRESET_COLORS__", colors_json)
        .replace("__PRESET_SIZES__", sizes_json)
        .replace("__PRESET_LABELS__", labels_json)
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


def _labels(payload: Dict[str, Any]) -> Dict[str, Any]:
    values = payload.get("labels", {})
    if not isinstance(values, dict):
        raise ValueError(
            "labels must be an object mapping style paths to values"
        )

    overrides: Dict[str, Any] = {}
    for key in values:
        if key not in _LABEL_PATH_SET:
            raise ValueError("unknown label key {!r}".format(key))

    for key, value in values.items():
        if key == "metrics.label_family":
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    "{} must be a non-empty string".format(key)
                )
            if len(value) > 200:
                raise ValueError(
                    "{} must be 200 characters or fewer".format(key)
                )
            if (
                "<" in value
                or ">" in value
                or _CONTROL_CHARACTER.search(value) is not None
            ):
                raise ValueError(
                    "{} must not contain <, >, or control characters".format(
                        key
                    )
                )
            overrides[key] = value.strip()
        elif key == "metrics.label_size":
            if isinstance(value, bool) or not isinstance(
                value, (int, float)
            ):
                raise ValueError("{} must be a number".format(key))
            overrides[key] = _number(values, key, 0.0, 4.0, 72.0)
        elif key == "metrics.label_weight":
            if not isinstance(value, str) or value not in _LABEL_WEIGHTS:
                raise ValueError(
                    "{} must be one of normal, bold, or 100 through 900".format(
                        key
                    )
                )
            overrides[key] = value
        elif key == "metrics.label_style":
            if not isinstance(value, str) or value not in _LABEL_STYLES:
                raise ValueError(
                    "{} must be one of normal, italic, or oblique".format(key)
                )
            overrides[key] = value
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


#: Windows refuses these as filenames even with an extension.
_WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"]
    + ["COM{}".format(i) for i in range(1, 10)]
    + ["LPT{}".format(i) for i in range(1, 10)]
    + ["COM{}".format(i) for i in ("¹", "²", "³")]
    + ["LPT{}".format(i) for i in ("¹", "²", "³")]
)
_WINDOWS_INVALID_FILENAME = re.compile(r'[<>:"|?*\x00-\x1f]')
_MAX_UPLOAD_FILENAME_LENGTH = 120


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
    basename = _WINDOWS_INVALID_FILENAME.sub("-", basename).rstrip(" .")
    suffix = Path(basename).suffix.lower()
    if suffix not in _UPLOAD_FONT_SUFFIXES:
        raise ValueError(
            "unsupported upload extension {!r}; choose from {}".format(
                suffix or "(none)",
                ", ".join(sorted(_UPLOAD_FONT_SUFFIXES)),
            )
        )
    # A file literally named "con.ttf" is legal on macOS and Linux but names a
    # character device on Windows, where writing it would not produce a file.
    device_name = basename.split(".", 1)[0].rstrip(" .").upper()
    if device_name in _WINDOWS_RESERVED_NAMES:
        basename = "_{}".format(basename)
    stem = Path(basename).stem
    if len(basename) > _MAX_UPLOAD_FILENAME_LENGTH:
        stem_limit = _MAX_UPLOAD_FILENAME_LENGTH - len(suffix)
        basename = "{}{}".format(stem[:stem_limit], suffix)
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
    labels = _labels(payload)

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
    overrides.update(labels)
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
        title="Type Design X-Ray preview",
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

    server_version = "type-design-xray-preview/1.0"

    def _send(
        self,
        status: int,
        payload: bytes,
        content_type: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, data: Dict[str, Any]) -> None:
        payload = json.dumps(data).encode("utf-8")
        self._send(status, payload, "application/json; charset=utf-8")

    def _require_json_content_type(self) -> bool:
        content_type = self.headers.get("Content-Type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            self._json(415, {"error": "Content-Type must be application/json"})
            return False
        return True

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(
                200,
                _preview_page().encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if self.path in ("/specimen", "/specimen/"):
            self._send(
                200,
                specimen_page().encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if self.path in (
            "/process",
            "/process/",
            "/font-design-process",
            "/font-design-process/",
        ):
            self._send(
                200,
                process_page().encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        if self.path == "/health":
            self._json(200, {"status": "ok"})
            return
        if self.path == "/api/fonts":
            self._json(
                200,
                {"families": installed_font_families()},
            )
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/api/upload":
            self._upload_font()
            return
        if self.path == "/api/specimen/export":
            self._export_specimen()
            return
        if self.path == "/api/process/export":
            self._export_process()
            return
        request_handlers = {
            "/api/render": render_request,
            "/api/specimen/catalog": specimen_catalog_request,
            "/api/specimen/render": specimen_render_request,
            "/api/process/catalog": process_catalog_request,
            "/api/process/render": process_render_request,
        }
        request_handler = request_handlers.get(self.path)
        if request_handler is None:
            self._json(404, {"error": "not found"})
            return
        if not self._require_json_content_type():
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
            result = request_handler(payload)
        except (UnicodeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "invalid JSON: {}".format(exc)})
            return
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, result)

    def _export_specimen(self) -> None:
        if not self._require_json_content_type():
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
            result = specimen_export_request(
                payload,
                output_dir=_SPECIMEN_EXPORT_DIRECTORY,
            )
            destination = Path(result["path"])
            content = destination.read_bytes()
            safe_name = re.sub(
                r"[^A-Za-z0-9_.-]+",
                "-",
                str(result["name"]),
            ).strip(".-") or "specimen.{}".format(result["format"])
        except (UnicodeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "invalid JSON: {}".format(exc)})
            return
        except (
            OSError,
            ValueError,
            KeyError,
            RuntimeError,
        ) as exc:
            self._json(400, {"error": str(exc)})
            return
        self._send(
            200,
            content,
            str(result["content_type"]),
            {"Content-Disposition": 'attachment; filename="{}"'.format(safe_name)},
        )

    def _export_process(self) -> None:
        if not self._require_json_content_type():
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
            result = process_export_request(
                payload,
                output_dir=_PROCESS_EXPORT_DIRECTORY,
            )
            destination = Path(result["path"])
            content = destination.read_bytes()
            safe_name = re.sub(
                r"[^A-Za-z0-9_.-]+",
                "-",
                str(result["name"]),
            ).strip(".-") or "font-design-process.{}".format(
                result["format"]
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": "invalid JSON: {}".format(exc)})
            return
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        self._send(
            200,
            content,
            str(result["content_type"]),
            {"Content-Disposition": 'attachment; filename="{}"'.format(safe_name)},
        )

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
    if ":" in host:
        class IPv6PreviewServer(ThreadingHTTPServer):
            address_family = socket.AF_INET6

        return IPv6PreviewServer((host, port), PreviewHandler)
    return ThreadingHTTPServer((host, port), PreviewHandler)


def _url_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return "[{}]".format(host)
    return host


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="type-design-xray-preview",
        description="Run the local Type Design X-Ray browser preview.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="print the preview URL without opening a browser",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        print(
            "type-design-xray-preview: error: port must be between 0 and 65535",
            file=sys.stderr,
        )
        return 2
    try:
        server = create_server(args.host, args.port)
    except OSError as exc:
        # Re-running the preview while one is already open is the single most
        # likely failure here, and a socket traceback is a poor way to say so.
        error_codes = {
            code
            for code in (exc.errno, getattr(exc, "winerror", None))
            if code is not None
        }
        address_in_use_codes = {
            errno.EADDRINUSE,
            getattr(errno, "WSAEADDRINUSE", 10048),
        }
        permission_codes = {
            errno.EACCES,
            errno.EPERM,
            getattr(errno, "WSAEACCES", 10013),
        }
        if error_codes.intersection(address_in_use_codes):
            next_port = args.port + 1 if args.port < 65535 else 8765
            print(
                "type-design-xray-preview: error: port {} is already in use. "
                "A preview may already be running at http://{}:{}/ — open "
                "that, or start this one on another port with --port {}.".format(
                    args.port,
                    _url_host(args.host),
                    args.port,
                    next_port,
                ),
                file=sys.stderr,
            )
        elif error_codes.intersection(permission_codes):
            print(
                "type-design-xray-preview: error: not allowed to listen on port "
                "{}. Ports below 1024 need elevated privileges; try "
                "--port 8765.".format(args.port),
                file=sys.stderr,
            )
        else:
            print(
                "type-design-xray-preview: error: could not start on {}:{}: "
                "{}".format(args.host, args.port, exc),
                file=sys.stderr,
            )
        return 2
    host, port = server.server_address[:2]
    url = "http://{}:{}/".format(_url_host(host), port)
    print("Type Design X-Ray preview: {}".format(url), flush=True)
    if not args.no_open:
        try:
            opened = webbrowser.open(url, new=2)
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            print(
                "The browser did not open automatically. Open the preview "
                "URL printed above.",
                file=sys.stderr,
                flush=True,
            )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
