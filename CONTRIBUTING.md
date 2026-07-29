# Contributing

Thanks for taking a look. This project is small and deliberately layered, so
most contributions touch exactly one module.

## Getting set up

```bash
git clone https://github.com/ArticaVisuals/type-design-xray
cd type-design-xray
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev,raster]"
.venv/bin/python -m pytest
```

## The architecture, in one paragraph

Parsers read a source file and emit the internal representation in
[`typedesignxray/ir.py`](typedesignxray/ir.py). The layout engine turns a string
plus a parsed font into positioned glyphs. The config layer resolves defaults,
presets, config files and CLI overrides into one `Style`
([`typedesignxray/style.py`](typedesignxray/style.py)). The renderer takes
layout plus style and returns SVG. Exporters turn SVG into PNG or PDF. The CLI
wires it together.

`ir.py` and `style.py` are what keep those layers independent. Changing them
affects everything, so changes there need a good reason and a matching update to
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Rules that matter

* **Coordinates stay in raw font units** everywhere except the renderer.
  Scaling happens once, at render time. This is what makes a parsed glyph
  re-renderable at any size.
* **Handles are absolute points**, never deltas, and a node owns both of its
  handles. The renderer must never look at a neighbouring node to find a handle.
* **One curve type.** Quadratics are upconverted to cubics at parse time. If you
  add a parser, do the conversion there, not downstream.
* **Nothing visual is hard-coded.** If you can see it, it belongs in `Style`.
  Adding a visual feature means adding a style leaf, a default, and a mention in
  the README's style reference.
* **Open contours are first-class.** Centreline layers are a real use case; any
  code path that assumes `closed=True` is a bug.

## Adding a new source format

Write `typedesignxray/parsers/yourformat.py` exposing
`parse_yourformat(path, layer=None, master=None) -> ir.Font`, register it in
`typedesignxray/parsers/__init__.py`, and add tests. If the format cannot
express smooth-vs-corner node classification, set `node_types_exact=False` and
use `ir.infer_smooth` — and add a line to the README's known-limitations table.

## Tests

`pytest` at the root. Tests that depend on a font file that is not in the repo
must `pytest.skip` when it is absent, so a fresh clone stays green. Anything
that can be synthesised should be synthesised into `tests/fixtures/`.

## Style

`from __future__ import annotations`, typed dataclasses, Python 3.9-compatible
syntax. Docstrings explain why, not what. Keep public surfaces in `__all__`.
