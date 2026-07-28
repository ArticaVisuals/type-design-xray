# Build contract (read before writing any module)

This file is authored by the project lead and is **frozen**. Every module codes
against it. Do not edit `glyphblueprint/ir.py` or `glyphblueprint/style.py` —
if you believe a contract change is required, stop and report it instead.

## Module ownership

Each module owns a disjoint set of files. Never create or edit a file owned by
another module.

| Module | Owns |
|---|---|
| Contracts (lead) | `glyphblueprint/ir.py`, `glyphblueprint/style.py`, `pyproject.toml` |
| A — glyphs parser | `glyphblueprint/parsers/glyphs.py`, `glyphblueprint/parsers/plist.py`, `tests/test_glyphs_parser.py` |
| B — binary parser | `glyphblueprint/parsers/binary.py`, `tests/test_binary_parser.py` |
| C — layout | `glyphblueprint/layout.py`, `tests/test_layout.py` |
| D — renderer | `glyphblueprint/render/svg.py`, `tests/test_svg_render.py` |
| E — config/export | `glyphblueprint/config.py`, `glyphblueprint/render/raster.py`, `presets/*.json`, `tests/test_config.py` |
| F — CLI | `glyphblueprint/cli.py`, `glyphblueprint/api.py`, `glyphblueprint/__init__.py`, `glyphblueprint/parsers/__init__.py`, `tests/test_cli.py` |

## The internal representation

Defined in `glyphblueprint/ir.py`. Read that file — it is short and fully
documented. Summary:

```
Font    glyphs{name->Glyph}, units_per_em, metrics, cmap{codepoint->name},
        kerning{(left,right)->value}, kern_group_left{}, kern_group_right{},
        family_name, master_name, source_format, node_types_exact
Glyph   name, advance_width, units_per_em, contours[], metrics, unicodes[],
        layer_name, node_types_exact
Contour closed: bool, nodes[]
Node    point, type ("line"|"curve"), smooth: bool, handle_in, handle_out
```

Invariants every parser must satisfy:

1. **All coordinates are raw font units, absolute.** No normalisation, no
   scaling, no y-flip. The renderer flips y.
2. **Handles are absolute points**, in the same space as anchors. A handle line
   is literally `node.point -> node.handle_out`. Never store handles as deltas.
3. **A node owns both of its handles.** `handle_in` is the control point that
   governs the segment *arriving* at this node; `handle_out` governs the
   segment *leaving* it. The renderer never looks at neighbours to find a
   handle.
4. **Segment rule.** For consecutive on-curve nodes `A -> B`: if
   `A.handle_out` or `B.handle_in` is present the segment is a cubic
   `C A.handle_out B.handle_in B.point`, substituting the anchor itself for a
   missing control. If both are absent it is a straight line. This one rule
   renders any contour — do not add special cases.
5. **`node.type`** describes the segment *arriving* at that node
   (`"line"` or `"curve"`).
6. **Open contours are first-class.** `closed=False` must round-trip; several
   real source layers are open centrelines.
7. **Quadratics are upconverted at parse time** with
   `ir.quadratic_to_cubic`. The renderer only ever sees cubics.

## The style model

Defined in `glyphblueprint/style.py`. `Style` is a nested dataclass tree with
`to_dict()` / `from_dict()` / `merged(dict)` / `set_path("a.b.c", value)`.
There are 77 settable leaves. The renderer reads `Style` and nothing else for
visual decisions — no hard-coded colours, sizes, or dash patterns anywhere.

Key helpers already provided: `LineStyle.dasharray()`,
`MarkerStyle.effective_fill()`, `MarkerStyle.effective_stroke()`,
`OutlineStyle.as_line()`, `LayerToggles.enabled(name)`, `style.dotted_paths()`.

## Environment

* Target **Python 3.9+**. Use `from __future__ import annotations`; no `X | Y`
  type syntax at runtime, no `match`, no `tomllib` without a `tomli` fallback.
* The venv is at `.venv/` — run `.venv/bin/python` and `.venv/bin/python -m pytest`.
* `fonttools` is installed. `cairosvg` is **not** installed and must stay an
  optional import.
* Test fixtures: real sources live outside the repo at
  `/Users/micahhoang/My Drive/Font Design/CaliperSans04/CaliperSans_04.glyphs`
  and `.../CaliperSans04/CaliperSans-Regular.otf`. Tests that need a real file
  must `pytest.skip` when it is absent, and small synthetic fixtures should be
  written under `tests/fixtures/` for anything that can be synthesised.

## Style of the code

Match the existing two modules: `from __future__ import annotations`, typed
dataclasses, docstrings that explain *why* rather than restating the signature,
no decorative comments. Keep public surfaces in `__all__`.
