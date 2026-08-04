# Type Design X-Ray

Export styled **blueprint drawings of letterforms** — the outline, every
on-curve node, every off-curve bezier handle, and the handle lines between them
— straight from your font source files.

<p align="center">
  <img src="examples/output/hero.svg" alt="Blueprint of the letters V, a and o showing outlines, bezier handles, corner and smooth nodes, and labelled metric guides" width="100%">
</p>

Think of it as a batch, source-driven version of the "Bezier Inspector" family of
Illustrator plugins — except it reads your actual font file instead of tracing
placed vector art, and it sets a whole typed string at once **using the font's
real advance widths and kerning**.

Every element in the exported SVG is a real, named, editable object, so a
blueprint can go straight into Illustrator, Figma or After Effects and be
animated.

---

## What it does

- Reads **`.glyphs`** (Glyphs 3, format 4), **`.ufo`**, **`.otf`** and **`.ttf`**
- Lays out any input string with **real advance widths and kerning**, including
  **group kerning** — the lockup matches how your font actually sets
- Draws outline, handle lines, handle points, and corner-vs-smooth on-curve nodes
- Optional **metrics overlay**: baseline, x-height, cap height, ascender,
  descender and per-glyph side bearings, with numeric labels
- Renders **open contours**, so hand-drawn centreline/skeleton layers work
- Optionally **merges overlapping shapes** (`--compound`) the way the font
  compiler does at export, so you can blueprint the finished outline
- Exports **SVG** (primary), plus PNG and PDF
- Everything visual is configurable — **85 style properties**, every one
  settable from a config file *or* the command line

## Download and set up

You need **Python 3.9 or newer**. Every command below comes in two versions —
use the one for your system.

> **PowerShell:** the semicolons at the end of the Windows commands are
> intentional separators. You may paste a whole Windows code block at once,
> but do not join commands after removing the semicolons.

**Step 1 — download the project.**

macOS / Linux:

```bash
git clone https://github.com/ArticaVisuals/type-design-xray
cd type-design-xray
```

Windows (PowerShell):

```powershell
git clone https://github.com/ArticaVisuals/type-design-xray;
Set-Location .\type-design-xray;
```

No `git`? Download the ZIP from the green **Code** button on GitHub, unzip it,
and `cd` into the folder instead.

**Step 2 — create an isolated environment.** This keeps Type Design X-Ray and
its dependencies out of your system Python, so nothing else on your machine is
affected.

macOS / Linux:

```bash
python3 -m venv .venv
```

Windows (PowerShell):

```powershell
py -3 -m venv .venv;
```

If `py` is unavailable but `python --version` reports Python 3.9 or newer, use
`python -m venv .venv` instead.

**Step 3 — install it.**

macOS / Linux:

```bash
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[compound]"
```

Windows (PowerShell):

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip;
.\.venv\Scripts\python.exe -m pip install -e ".[compound]";
```

The `[compound]` part adds overlap removal (the `--compound` option). Leave it
off if you don't need it by replacing `".[compound]"` with `"."`. The `-e`
keeps the virtual environment connected to this checkout, so pulling a newer
version updates the code used by the preview after you restart it.

**Step 4 — check it works.** This should write an SVG and print a one-line
summary.

macOS / Linux:

```bash
.venv/bin/type-design-xray examples/Roboto-Regular-subset.ufo "Type" --out blueprint.svg
```

Windows (PowerShell):

```powershell
.\.venv\Scripts\type-design-xray.exe examples\Roboto-Regular-subset.ufo "Type" --out blueprint.svg;
```

Open `blueprint.svg` in a browser, Illustrator, or Figma.

### If something goes wrong

**`Set-Location: A positional parameter cannot be found that accepts argument
'-3'`** — two PowerShell commands were joined without a separator. If the line
at the prompt looks like `cd type-design-xraypy -3 -m venv .venv`, cancel it
and run these two commands separately:

```powershell
Set-Location .\type-design-xray;
py -3 -m venv .venv;
```

If your prompt already ends in `type-design-xray>`, you are already in the
right folder and should run only the second command.

**`Python was not found; run without arguments to install from the Microsoft
Store`** — you are on Windows and typed `python3`. Windows ships a stub for that
name which does nothing useful. Use `py` instead, as in the Windows commands
above. If `py` is also missing, install Python from
[python.org](https://www.python.org/downloads/) and tick **"Add Python to
PATH"** during setup.

**`The term '.venv/bin/python' is not recognized`** — that is the macOS path on
a Windows machine. Windows puts the environment in `.\.venv\Scripts\`; use the
PowerShell command from step 3 exactly.

**`No such file or directory: .venv/bin/python`** — step 2 did not actually
create the environment. Scroll up and check it succeeded before continuing.

**`'type-design-xray' is not recognized`** — you are missing the `.venv/bin/`
(or `.\.venv\Scripts\`) prefix, or the environment is not activated. See below.

### Updating an existing installation

Stop a running preview with **Ctrl-C** before updating. If you cloned the
project with Git, run the commands for your system from inside the project
folder.

macOS / Linux:

```bash
git switch main
git pull --ff-only origin main
.venv/bin/python -m pip install --upgrade -e ".[compound]"
.venv/bin/type-design-xray-preview
```

Windows (PowerShell):

```powershell
Set-Location "C:\path\to\type-design-xray"
git switch main
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install --upgrade -e ".[compound]"
.\.venv\Scripts\type-design-xray-preview.exe
```

Run the PowerShell commands one line at a time. Replace the example path in
the first line with the folder where you cloned the project. After the preview
restarts, reopen its printed localhost address and hard-refresh the browser
with **Ctrl-F5** on Windows or **Command-Shift-R** on macOS.

The install command above is important for older checkouts that were installed
as a fixed copy. Once the editable install is in place, future code-only
updates need only `git pull --ff-only origin main` followed by a preview
restart; rerunning the install command is safe and also picks up dependency or
command-line entry-point changes.

If you downloaded a ZIP instead of cloning with Git, `git pull` will not work.
Download the newest ZIP from GitHub into a new folder, then repeat setup steps
2 and 3 there.

### Activating it in later sessions

The `.venv/bin/` and `.\.venv\Scripts\` prefixes always work and need no
activation. If you would rather type just `type-design-xray` (or the short alias
`tdxray`), activate the environment first.

macOS / Linux:

```bash
source .venv/bin/activate
```

Windows (PowerShell):

```powershell
.\.venv\Scripts\Activate.ps1
```

Then `type-design-xray --version` works directly. Run `deactivate` to leave.
You will need to activate again in each new terminal. If PowerShell blocks
`Activate.ps1`, activation is optional: use the full `.\.venv\Scripts\...`
commands from steps 3 and 4 instead.

### Local browser preview

The fastest way to try it. Start the preview:

macOS / Linux:

```bash
.venv/bin/type-design-xray-preview
```

Windows (PowerShell):

```powershell
.\.venv\Scripts\type-design-xray-preview.exe
```

Then open **[http://127.0.0.1:8765/](http://127.0.0.1:8765/)** in your browser
and choose a font file. Everything stays on your own computer: the server binds
to localhost only, and nothing is sent over the internet. (Choosing a file with
the picker copies it to a temporary folder on your machine, which is deleted
when you stop the server.) It uses the same Python parsing, layout, compounding
and SVG rendering code as the command-line tool, so what you see is what the
exporter produces. Press Ctrl-C in the terminal to stop it.

It gives you, live:

- **A file picker** — click "Choose file…" to load a `.glyphs`, `.otf`, `.ttf`
  or `.woff` file. The path box beside it still works for typing or pasting a
  path, and is the way to load a `.ufo`, which is a folder rather than a single
  file and so cannot be picked this way.
- **Colour pickers** for all twelve colours — background, outline stroke and
  fill, handle lines, handle point fill and stroke, corner and smooth node fill
  and stroke, metric guides, and metric label text. Every colour has an
  editable hex field and a Transparent toggle. Choosing a preset seeds every
  colour with that preset's real values, so a preset is a starting point rather
  than a straitjacket. "Reset to preset" puts them back. Leave the colours alone
  and the output is byte-identical to the plain preset.
- **Sizes and stroke weights as sliders** — handle point size and stroke,
  corner and smooth node size and stroke, outline stroke width, handle line
  width, and metric guide width. Drag to find a value by eye and the preview
  re-renders live; each slider has a number box beside it for exact entry,
  which accepts values beyond the slider's range.
- **A metric label font chooser** — pick the typeface the annotations are set
  in from the fonts actually installed on your machine, plus size, weight and
  style. Note the SVG references the font *by name* rather than embedding it, so
  a machine without that font will substitute a fallback when the file is opened
  elsewhere.
- **Granular metric toggles** — a master switch, then independent checkboxes for
  the guide *lines* and the numeric *labels*, plus one per guide (baseline,
  x-height, cap height, ascender, descender, side bearings) so you can render
  exactly the pieces you want. Side-bearing metrics use a Glyphs-style spacing
  row: dotted advance boundaries with the LSB, advance width, and RSB values
  positioned beneath each glyph.
- **Reusable style presets** — name the current settings and export them as a
  JSON preset. Load that file in the preview next time, or pass it to the CLI
  with `--config`.
- Preset, marker shape, frame mode, width, tracking, named layer, remove
  overlap and kerning. The preview updates after every change; use the primary
  **Export SVG** button at the bottom when it is ready.

Use `--port` if 8765 is taken.

### Windows export notes

The core SVG workflow, overlap removal, and local preview work on Windows.
Every push and pull request is tested on Windows and macOS before release.

`skia-pathops` (for `--compound`) ships Windows wheels, so it installs cleanly.
**`cairosvg` does not** — it needs the Cairo native library, which on Windows
means installing GTK runtime DLLs by hand. For PNG, the easier route is
[`resvg`](https://github.com/linebender/resvg/releases): put `resvg.exe` on
your `PATH` and the exporter will find it. PDF still needs CairoSVG or
`rsvg-convert`. SVG export needs none of these.

### Optional: PNG and PDF export

SVG export needs nothing extra. On macOS, install the Cairo system library and
then the optional local extra:

```bash
brew install cairo
.venv/bin/python -m pip install ".[raster]"
```

If no backend is installed, `--format png` prints exactly what to install and
still writes the SVG.

## Quickstart

```bash
# The basic blueprint
type-design-xray MyFont.glyphs "afz" --out afz.svg

# With metric guides and numeric labels
type-design-xray MyFont.glyphs "afz" --metrics baseline,xheight,capheight,sidebearings --out afz.svg

# A different look
type-design-xray MyFont.glyphs "Rag" --preset drafting --out rag.svg

# Draw a hand-made centreline layer instead of the outline
type-design-xray MyFont.glyphs "a" --layer "Skeleton v1" --out skeleton.svg

# See what layers a glyph has
type-design-xray MyFont.glyphs --list-layers a

# PNG at a specific width
type-design-xray MyFont.glyphs "afz" --format svg,png --png-width 2400 --out afz.svg

# One file per glyph, as well as the combined lockup
type-design-xray MyFont.glyphs "afz" --per-glyph --out out/
```

Typing a glyph *name* rather than a character — useful for `&`, `.`, or
alternates — uses a leading slash, the convention type designers already know:

```bash
type-design-xray MyFont.glyphs "/ampersand/period/a.alt" --out named.svg
```

## Presets

**Every preset exports on a transparent background by default**, so a blueprint
composites straight into whatever you place it over. Set one when you want it:
`--background '#0b1f3a'`, or the swatch in the preview.

One caveat: `contrast` is designed for a dark backdrop — its cyan outline reads
at about 1.5:1 against white, so give it a dark `--background` when you use it.

Four ship in the box. Use `--preset <name>`, or start from one in your own
config and override just what you want.

| | |
|---|---|
| **blueprint** — the signature look | <img src="examples/output/preset-blueprint.svg" width="320"> |
| **light** — for print and docs | <img src="examples/output/preset-light.svg" width="320"> |
| **contrast** — presentation / accessibility | <img src="examples/output/preset-contrast.svg" width="320"> |
| **drafting** — spec-sheet feel, translucent fill | <img src="examples/output/preset-drafting.svg" width="320"> |

## Styling

Nothing is hard-coded. Anything you can see, you can change — from a config file
(JSON or TOML) or straight from the command line.

```bash
type-design-xray MyFont.glyphs "afz" \
  --set handles.point.shape=diamond \
  --set handles.point.size=5 \
  --set handles.line.dash=dotted \
  --set nodes.corner.shape=triangle \
  --set outline.stroke='#7dd3fc'
```

<p align="center">
  <img src="examples/output/custom-shapes.svg" alt="The same letters drawn with triangle corner nodes, diamond handle points and dotted handle lines" width="100%">
</p>

Or put it in a file — see [`examples/style-example.toml`](examples/style-example.toml):

```bash
type-design-xray MyFont.glyphs "afz" --config examples/style-example.toml
```

`--config` and `--set` compose, in this order:

```
built-in defaults  →  preset  →  config file  →  explicit flags  →  --set
```

Run `type-design-xray --list-style-keys` to print every settable key.

### What you can control

**Handle points** — `shape` (circle, square, diamond, triangle, cross, none),
`size`, `fill`, `stroke`, `stroke_width`, `hollow`, `opacity`, `visible`.

**Handle lines** — `color`, `width`, `dash` (solid, dashed, dotted, dashdot),
`opacity`, `visible`.

**On-curve nodes** — independent `corner` and `smooth` marker styles with the
same full set of properties, plus `distinguish_types` to switch the
corner/smooth distinction off entirely.

**Outline** — `stroke`, `width`, `dash`, `linecap`, `linejoin`, plus an optional
translucent `fill`.

Every line is exported as a real SVG `stroke` with a `stroke-width` — never
converted to an outlined, filled shape — so strokes stay live and re-editable
after import into Illustrator, Figma or After Effects.

**Metrics** — which guides to draw, their line style, and full control over the
label typeface: `label_family`, `label_size`, `label_weight`, `label_style`,
`label_variant`, `label_letter_spacing`, `label_color`, `label_opacity`. There
are shorthand flags too:

```bash
type-design-xray MyFont.glyphs "afz" --metrics all \
  --label-font "Helvetica Neue, sans-serif" --label-weight 600 --label-size 13
```

**Canvas** — `background`, `width`, `padding`, and
`frame`: `auto` fits the drawing, `metrics` locks to descender–ascender, and
`em` locks to a box exactly one em tall sitting on the descender. The locked
modes give every render an identical scale, which is what you want when
exporting a whole character set.

**Layers** — `background`, `metrics`, `fill`, `outline`, `handle_lines`,
`handle_points`, `nodes`, each independently on or off.

## Compounded outlines (`--compound`)

Glyphs sources store letterforms as separate **overlapping shapes** — an `f` is
typically a stem-and-arch shape with the crossbar laid across it as its own
rectangle. The font compiler merges those at export. `--compound` does the same
thing, so you can blueprint the finished outline instead of the construction:

```bash
type-design-xray MyFont.glyphs "f" --compound --out f.svg
```

<p align="center">
  <img src="examples/output/compound-before.svg" width="45%" alt="Source f: the crossbar is a separate rectangle crossing the stem">
  <img src="examples/output/compound-after.svg" width="45%" alt="Compounded f: one outline with intersection nodes where the crossbar meets the stem">
</p>

Left: the source, two overlapping contours. Right: compounded, one contour with
new nodes where the crossbar meets the stem.

`--remove-overlap` is an alias for the same option. It needs the `compound`
extra installed in step 3. If you initially left that extra off, rerun the
matching macOS/Linux or Windows step 3 command with `".[compound]"`.

This uses `skia-pathops`, the same engine `fontmake` uses for overlap removal,
so results match a real export. Two things worth knowing:

- **Open contours are never merged.** A boolean union is meaningless on an open
  centreline, so open paths pass through untouched and your `Skeleton v1`-style
  layers are safe.
- **Node types at new intersections are inferred, not authored.** Merging
  invents nodes that don't exist in your source. Nodes that survive at their
  original coordinates keep their real smooth/corner flags; only the new ones
  are inferred. The exported OTF has exactly the same limitation.

Without `--compound` you see the shapes as you actually drew them, which is
usually what you want when reviewing construction.

## Motion design: After Effects and Illustrator

Illustrator and After Effects name imported layers from SVG `id` attributes, so
every element gets a readable, unique name:

```
outline_01a_c01_path              the first contour of glyph 1 ("a")
handleline_01a_c01_n03_out        node 3's outgoing handle line
handlepoint_01a_c01_n03_out       the handle point at its end
node_01a_c02_n01_smooth           an on-curve node, tagged smooth or corner
```

Each contour is also wrapped in its own group, so it arrives as one selectable
object you can precompose. Import the SVG into Illustrator, save as `.ai`, then
import that into After Effects as a **Composition — Retain Layer Sizes** to get
the whole structure as named layers.

Importing several blueprints into one project? Namespace them so ids can't
collide:

```bash
type-design-xray MyFont.glyphs "a" --id-prefix "shotA-" --out shotA.svg
```

Elements also carry `data-glyph`, `data-node-index`, `data-node-type`,
`data-handle` and `data-contour-index` attributes if you'd rather drive things
by script.

## Layers in the source file (`.glyphs` / `.ufo`)

By default the tool reads the **finalized master layer**. Glyphs files also hold
backup layers (timestamp names like `Jul 2, 26 at 12:18`) and your own named
layers.

```bash
type-design-xray MyFont.glyphs --list-layers a     # what's available
type-design-xray MyFont.glyphs "a" --layer "Skeleton v1"
```

Open, unclosed contours are fully supported — a centreline layer draws as an
open path and is never filled:

<p align="center">
  <img src="examples/output/skeleton-layer.svg" alt="An open centreline path with its nodes and handles" width="70%">
</p>

Glyphs absent from a named layer quietly fall back to their master layer, so a
partially-drawn layer still renders a full string.

## Python API

For batching over a character set:

```python
from typedesignxray import blueprint, blueprint_to_files, load_font

svg = blueprint("MyFont.glyphs", "afz", preset="blueprint")

font = load_font("MyFont.glyphs")
for name, glyph in font.glyphs.items():
    if glyph.unicodes:
        blueprint_to_files(
            "MyFont.glyphs", chr(glyph.unicodes[0]),
            out=f"out/{name}.svg", formats=("svg",),
        )
```

`load_font` returns the internal representation described in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — raw font units, absolute handle
coordinates, one curve type — if you want to do your own drawing.

`blueprint_to_files` accepts `svg`, `png`, and `pdf` in any order and returns
the paths it wrote in that same order. An existing directory (or a path ending
in `/`) receives files named from the input text. A file path names the combined
lockup; with `per_glyph=True`, the individual glyph files are written beside
it. PNG and PDF calls raise an actionable error when no optional rendering
backend is installed; the command-line tool additionally preserves an SVG
fallback in that case.

## Known limitations

- **OTF/TTF lose node-type fidelity.** Compiled outlines are final filled
  contours and carry no smooth-vs-corner metadata. The tool infers smoothness
  from handle colinearity, which is close but not authoritative. For exact node
  types, use `.glyphs` or `.ufo`. Compiled fonts also often carry extra points
  at curve extrema that the source did not have.
- **Layer selection is a source-format feature.** `--layer` works for `.glyphs`
  and `.ufo` only; compiled fonts have a single outline.
- **Kerning from compiled fonts is partial.** The legacy `kern` table and simple
  GPOS pair positioning are read; complex contextual kerning is not applied.
  `.glyphs` kerning, including group kerning, is read exactly.
- **Variable fonts** render their default instance. Named instances and axis
  positions are not yet selectable.
- **Metric labels use a system font stack.** SVG output looks the same
  everywhere the font is present; PNG/PDF rasterisation depends on the backend
  finding it. Set `metrics.label_family` to something you know is installed if
  it matters.
- **PNG/PDF need a backend.** See the install section. SVG never does.

## Reproducing the examples

Two fonts ship in [`examples/`](examples/), and between them they cover
everything shown above.

**[`Roboto-Regular-subset.ufo`](examples/Roboto-Regular-subset.ufo)** — the
letterforms in most of the images. A four-glyph excerpt (`T y p e`) of Roboto
Regular, taken from its own UFO sources. Roboto's sources keep the overlapping
construction a designer draws: `T` really is a stem rectangle crossed by a bar,
and `y` is two overlapping strokes. That is what makes it a good subject for
`--compound` — the shipped Roboto TTF has already had those merged, so the
source is the interesting artefact. It also carries authored smooth/corner node
data, which a compiled font cannot.

```bash
type-design-xray examples/Roboto-Regular-subset.ufo "Type" --compound \
  --metrics baseline,xheight,capheight,sidebearings --out examples/output/hero.svg
```

**[`BlueprintDemo.glyphs`](examples/BlueprintDemo.glyphs)** — a small
public-domain font written for this project, used for the features Roboto's
excerpt cannot show. It deliberately exercises the awkward cases: an open
`Skeleton v1` centreline layer, contours whose handles wrap around the start of
the point list, flat pair kerning, group kerning, and an explicit zero-value
pair that has to override a group kern.

```bash
type-design-xray examples/BlueprintDemo.glyphs "a" --layer "Skeleton v1" \
  --out examples/output/skeleton-layer.svg
```

### Roboto attribution

Roboto is Copyright 2011 Google Inc., licensed under the
[Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0). The excerpt
here is redistributed with its outline data unchanged — only contours that
existed solely to carry anchor positions were removed — under the terms of that
licence. See [`examples/Roboto-Regular-subset.ufo/NOTICE.txt`](examples/Roboto-Regular-subset.ufo/NOTICE.txt).
Type Design X-Ray itself is MIT; the bundled excerpt keeps its own licence.

## Development

Use the platform-specific setup and test commands in
[`CONTRIBUTING.md`](CONTRIBUTING.md). Architecture and the rules each layer
relies on are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## License

MIT — see [LICENSE](LICENSE).
