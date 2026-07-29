# Architecture

Type Design X-Ray is deliberately layered. Parsers read a font source and emit
one internal representation; everything downstream reads only that. This is what
lets four input formats and one renderer stay independent of each other.

```
 .glyphs ┐
 .ufo    ├─▶ parser ─▶ internal representation ─▶ layout ─▶ renderer ─▶ SVG ─▶ PNG/PDF
 .otf    │                    (ir.py)                         ▲
 .ttf    ┘                                                    │
                                                       style (style.py)
```

| Module | Responsibility |
|---|---|
| `ir.py` | The internal representation. Every other module speaks it. |
| `style.py` | The resolved style model — 85 settable leaves, no visual decision lives anywhere else. |
| `parsers/` | One parser per format, each emitting `ir.Font`. |
| `layout.py` | String → positioned glyphs, applying advance widths and kerning. |
| `compound.py` | Optional overlap removal via `skia-pathops`. |
| `config.py` | Defaults ← preset ← config file ← CLI overrides, resolved into one `Style`. |
| `render/svg.py` | Layout + style → SVG. |
| `render/raster.py` | SVG → PNG/PDF, backends optional. |
| `cli.py` / `api.py` / `web.py` | The three user-facing surfaces. |

## The internal representation

Defined in [`typedesignxray/ir.py`](../typedesignxray/ir.py), which is short and
fully documented. In summary:

```
Font    glyphs{name->Glyph}, units_per_em, metrics, cmap{codepoint->name},
        kerning{(left,right)->value}, kern_group_left{}, kern_group_right{},
        family_name, master_name, source_format, node_types_exact
Glyph   name, advance_width, units_per_em, contours[], metrics, unicodes[],
        layer_name, node_types_exact
Contour closed: bool, nodes[]
Node    point, type ("line"|"curve"), smooth: bool, handle_in, handle_out
```

### Invariants every parser must satisfy

These are what the renderer relies on. Breaking one produces subtly wrong
geometry rather than a clean failure, so they are worth reading before adding a
format.

1. **All coordinates are raw font units, absolute.** No normalisation, no
   scaling, no y-flip. The renderer flips y, once, at draw time. This is what
   makes a parsed glyph re-renderable at any size without loss.
2. **Handles are absolute points**, in the same space as anchors, so a handle
   line is literally `node.point -> node.handle_out`. Never store deltas.
3. **A node owns both of its handles.** `handle_in` governs the segment
   *arriving* at the node, `handle_out` the one *leaving* it. The renderer never
   inspects a neighbour to find a handle.
4. **One segment rule.** For consecutive on-curve nodes `A -> B`: if
   `A.handle_out` or `B.handle_in` exists the segment is a cubic
   `C A.handle_out B.handle_in B.point`, substituting the anchor itself for a
   missing control; if neither exists it is a straight line. That single rule
   draws any contour — resist adding special cases.
5. **`node.type`** describes the segment arriving at that node.
6. **Open contours are first-class.** `closed=False` must round-trip. Hand-drawn
   centreline layers are a real use case, and any code path assuming a closed
   contour is a bug.
7. **Quadratics are upconverted to cubics at parse time** via
   `ir.quadratic_to_cubic`, so the renderer only ever sees one curve type.
8. **`node_types_exact`** records whether smooth-vs-corner was authored or
   inferred. Compiled fonts carry no such data; `.glyphs` and `.ufo` do.

## The style model

[`typedesignxray/style.py`](../typedesignxray/style.py) is a nested dataclass
tree with `to_dict()` / `from_dict()` / `merged(dict)` / `set_path("a.b.c", v)`.
Run `type-design-xray --list-style-keys` for the current list of leaves.

Anything visible is a style leaf. Adding a visual feature means adding a leaf, a
default, and a line in the README's style reference — not a constant in the
renderer.

Helpers worth knowing: `LineStyle.dasharray()`,
`MarkerStyle.effective_fill()` / `effective_stroke()`, `OutlineStyle.as_line()`,
`LayerToggles.enabled(name)`, `style.dotted_paths()`.

## Ids and document structure

`ExportStyle` governs how the SVG is named, and that naming is what makes the
output usable in Illustrator and After Effects — both derive layer names from
SVG `id` attributes.

* **Every id in the document is unique.** Duplicates are invalid XML and cause
  editors to merge or silently rename layers. The glyph group repeats once per
  render layer, so its id includes the layer name.
* **Every id goes through `_identifier()`**, so `export.id_prefix` namespaces
  the whole document — layer and glyph groups included, not just leaves.
* Names are hierarchical:
  `{layer}_{NN}{glyph}_c{NN}[_n{NN}][_in|_out|_corner|_smooth]`.
* `export.element_ids=False` drops per-node leaf ids only; structural group ids
  always remain.

## Adding a source format

Write `typedesignxray/parsers/yourformat.py` exposing
`parse_yourformat(path, layer=None, master=None) -> ir.Font`, register it in
`parsers/__init__.py`, and add tests. If the format cannot express
smooth-vs-corner classification, set `node_types_exact=False`, use
`ir.infer_smooth`, and add a line to the README's known-limitations section.

## Tests

Everything that can be synthesised lives in `tests/fixtures/` or
`examples/`, so the suite is green on a fresh clone with no setup. A few tests
additionally run against a real production source when one is configured via
`TDXRAY_TEST_GLYPHS` / `TDXRAY_TEST_OTF` (see `tests/_real_fonts.py`); those
skip when unset and assert only format-independent invariants, never values
specific to one typeface.
