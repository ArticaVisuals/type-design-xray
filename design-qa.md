# Glyphs-Style Spacing Metrics — Design QA

## Reference and implementation

- Reference: `/Users/micahhoang/Desktop/Screenshot 2026-07-29 at 3.37.44 PM.png`
- Reference size: 1726 × 1094 px
- Rendered SVG: `/tmp/type-design-xray-spacing-qa.lmR4pW/caliper-spacing-final.svg`
- Rendered PNG: `/tmp/type-design-xray-spacing-qa.lmR4pW/caliper-spacing-final.png`
- Implementation size: 1727 × 1172 px
- Full-view comparison: `/tmp/type-design-xray-spacing-qa.lmR4pW/source-vs-implementation.png`
- Focused spacing-strip comparison: `/tmp/type-design-xray-spacing-qa.lmR4pW/spacing-strip-comparison.png`

## Test state

- Font source: `CaliperSans04/CaliperSans_04.glyphs`
- Master: default source master
- Text: `nus`
- Metrics: baseline and sidebearings enabled
- Handles, nodes, and outline strokes: disabled to isolate the spacing treatment
- Output: direct SVG render converted to PNG for visual inspection

The requested result is an SVG artifact, so this QA compares the direct rendered artifact rather than a browser screenshot.

## Fidelity review

### Typography

- Metric values remain controlled by the existing metric-label style.
- Labels are small, neutral, and centered beneath their corresponding spacing region.
- Values render without redundant `lsb` and `rsb` prefixes, matching the compact Glyphs presentation.

### Spacing and layout

- Each glyph displays one horizontal triplet: left sidebearing, advance width, right sidebearing.
- Shared boundaries between adjacent unkerned glyphs are deduplicated, preventing doubled or visually heavier lines.
- Boundary lines use a fine dotted pattern with round caps.
- The CaliperSans values match the reference exactly:
  - `n`: 80 / 563 / 70
  - `u`: 70 / 563 / 80
  - `s`: 50 / 523 / 51

### Color and style tokens

- Boundary and label colors continue to use the existing metrics style configuration.
- No new hardcoded visual palette was introduced.

### Image quality and assets

- The final result is vector SVG.
- No raster or placeholder assets were introduced.

### Copy and content

- Numeric labels are sourced from each positioned glyph's metrics.
- Advance width is now shown in addition to both sidebearings.

## Comparison result

The full-view and focused strip comparisons show the requested Glyphs-style information hierarchy and alignment. The reference also contains Glyphs-only editing affordances—selection pills, node markers, and green handles—that are intentionally outside the exporter output. The existing baseline label remains independently controlled by the baseline metric setting.

No P0, P1, or P2 visual mismatch remains for the requested spacing-metrics treatment.

## Verification

- Full automated suite: 266 passed, 4 skipped
- Diff validation: passed
- Visual QA: passed

---

# Font Design Process Video — Design QA

## Reference and implementation

- Source reference: `/Users/micahhoang/Desktop/Screenshot 2026-08-30 at 7.11.38 PM.png`
- Source pixels: 712 × 1000
- Browser-rendered implementation capture: `/tmp/type-design-xray-process-caliper-A-final.png`
- Implementation comparison pixels: 540 × 766
- Full comparison input: `/tmp/type-design-xray-process-source-vs-implementation-final.png`
- Focused evidence: the full comparison is already limited to the single 540 × 766 player canvas, so no second crop is necessary.

## Browser state and normalization

- Route: `http://127.0.0.1:8765/process`
- Browser viewport override: 900 × 1100 device pixels; reported page viewport 1125 × 1375 CSS pixels at 0.8 device pixel ratio
- Source: latest `CaliperSans_06.glyphs`
- Master: `m01 / Regular`
- Glyph: `A`
- Layer: `Skeleton v1` (`2FEF1CD5-0823-4185-9555-505F81D99410`)
- Point size: 370
- Bézier: enabled
- Handles: disabled; on-curve nodes remain visible
- Palette: default black, white, and neutral-gray X-Ray colors
- The reference was proportionally scaled to 540 × 758 and padded by four pixels above and below to reach 540 × 766. The browser-rendered canvas was normalized to its canonical 540 × 766 size after the in-app browser's capture scaling.

## Fidelity review

### Layout and typography

- The implementation uses the exact left-panel geometry from the existing specimen renderer: 540 × 766 canvas, metadata at x=20/y=20, separator `M 19 245.5 H 522`, and glyph panel translated to x=18/y=256.
- The reference's roughly 32% metadata / 68% glyph division, side insets, black field, monospaced uppercase metadata, guide brackets, outline weight, and node styling are all preserved.
- Metadata remains sourced from the selected active master while only the process geometry changes, matching the reference's stable header.

### Source-derived differences

- The screenshot and latest active Caliper master both report 742 upm with 36/36 sidebearings. The implementation keeps those active-master metadata values stable while process geometry changes, matching the reference behavior.
- The screenshot's two-contour process construction and the latest file's authored `Skeleton v1` one-contour construction differ in shape and top extent. The implementation correctly shows the exact newest source layer rather than visually faking the older construction.

### Interaction and export

- File import, character/exact-name resolution, master selection, exact layer-ID selection, stepping, playback, layer selection, color controls, Bézier mode, independent handle visibility, and all four export actions are wired to the same source sequence.
- Playback uses recursive timeouts: ordinary layers use the adjustable speed and the final active master uses a fixed 1000 ms hold.
- Current-layer SVG/PNG is 540 × 766. Complete GIF/MP4 is 1080 × 1532.

## Findings and iteration history

- P0: none.
- P1: none.
- P2: none.
- An initial screenshot crop exposed the in-app browser's non-1.0 capture scale. The final comparison was regenerated from the full browser screenshot and normalized to the canonical canvas before review.
- The first normalized comparison exposed layer-local 600/17/17 metrics. Those were corrected to the active master's 742/36/36 values, then the browser comparison was regenerated. The remaining glyph difference is required by the user's instruction to use the latest Caliper source.

## Verification

- Automated suite: 330 passed, 4 skipped
- Latest Caliper `A`: six exact process layers, master final
- Real Caliper GIF: 1080 × 1532, 4.000 seconds
- Real Caliper MP4: 1080 × 1532, 4.000 seconds
- Browser console errors: none
- Visual comparison: passed

final result: passed

## Shared selector placement on X-Ray Blueprint — 2026-08-30

- Moved the shared three-tool selector outside the X-Ray Blueprint's two-pane
  shell so it spans the full viewport above both the controls and preview.
- Matched the desktop selector geometry used by Font Specimen and Font Design
  Process: three equal columns, sticky top placement, and the same 61 px header
  allowance for the independently scrolling X-Ray panes.
- Retained the existing stacked, full-width selector treatment at compact
  widths.
- Browser console: no warnings or errors.
- Automated suite: 335 passed, 4 skipped.
- Embedded JavaScript syntax check: passed.
- Visual capture: `/tmp/type-design-xray-top-switcher.png`.

final result: passed

## X-Ray Blueprint monochrome shell — 2026-08-30

- Restyled the X-Ray Blueprint application shell to use the same monospaced
  typography, black field, white text, neutral borders, compact control radii,
  and grayscale three-tool switcher as Font Specimen and Font Design Process.
- Preserved the independent blueprint artwork colors and all color controls;
  the monochrome change applies to the interface chrome, not exported artwork.
- Confirmed the compact browser layout remains readable and all three tool
  links remain visible.
- Browser console: no warnings or errors.
- Automated suite: 334 passed, 4 skipped.
- Embedded JavaScript syntax check: passed.
- Visual capture: `/tmp/type-design-xray-monochrome-blueprint.png`.

final result: passed

## Unified three-tool navigation and looping playback — 2026-08-30

- Added one shared switcher to X-Ray Blueprint, Font Specimen, and Font Design
  Process. Each tab names and explains its distinct job, and the active tool is
  exposed with `aria-current="page"`.
- Confirmed the switcher is usable in the compact Codex browser viewport and
  does not cover the existing controls or player canvas.
- Confirmed Font Design Process playback continues after the 1000 ms active
  master hold: `Regular — ACTIVE` returned to `Skeleton v1`, then advanced to
  `Regular — ACTIVE` again without another Play click.
- Browser console: no warnings or errors.
- Automated suite: 333 passed, 4 skipped.
- Clean wheel smoke: version 1.1.0 imports all three pages, exposes all three
  tabs, and both installed preview/CLI entry points run.
- Cross-platform workflow continues to build/test the wheel and documented
  setup commands on macOS and Windows.
- Visual capture: `/tmp/type-design-xray-three-tools.png`.

final result: passed

## Word process animation — 2026-09-02

- Added a full-width 1080 × 766 Word mode to Font Design Process while
  preserving the existing 540 × 766 Single Glyph mode.
- Sequential mode reveals one glyph at a time, keeps completed glyphs on the
  selected active master, and hides future glyphs until their animation begins.
- Simultaneous mode reveals the whole word immediately, advances every glyph's
  authored layers together, and holds shorter layer sequences on the active
  master while the remaining glyphs finish.
- The Word-only Solid Finish switch changes each glyph independently from its
  compounded Bézier construction to the solid active-master fill as soon as its
  own layer sequence completes. Enabling it also enables Bézier and Handles;
  the behavior applies to preview and every export.
- Both word timelines stop after the completed frame's 1000 ms hold; existing
  single-glyph preview playback continues to loop.
- The Metadata toggle removes both the complete header and divider from live
  preview and every export, then centers the artwork in the full canvas.
- The process palette independently controls background, fill, outline stroke,
  metadata text, guides, handles, node fill, and node stroke.
- MP4 resolution is selectable at 2× or 4×, with the browser defaulting to 4×.
  A real simultaneous `Aa` word export measured 4320 × 3064 and 1.130 seconds.
- Latest Caliper `Caliper` check: 67 sequential frames and 19 simultaneous
  frames, with per-glyph authored layer counts of 4/19/4/7/13/5/15. Both modes
  end with all seven glyphs on `m01 / Regular`.
- Word positions always use the selected master's Glyphs kerning. Process-level
  coverage verifies class kerning, pair exceptions, fixed positions across both
  animation modes, and the exact same placement in composed SVG frames.
- Latest Caliper `m01 / Regular` kerning check: `AV` resolves to -90 units and
  `Caliper` resolves to 0/+10/+20/+10/+5/0 units between its glyphs.
- Real-browser Caliper check: simultaneous frame 5 rendered three completed
  glyphs solid while four unfinished glyphs retained nodes and handle lines;
  the console reported no errors or warnings.
- Real bundled-demo GIF/MP4 exports: 2160 × 1532. The word GIF has no repeat
  extension and the MP4 is a single finite timeline.
- Clean 1.2.4 wheel install: both word modes catalogued and rendered from the
  installed package, and the installed preview command opened successfully.
- Automated suite: 352 passed, 4 skipped.
- Python compilation, embedded JavaScript syntax, and diff checks: passed.
- Visual capture: `/tmp/caliper-word-process-final.png`.
- Metadata-free visual capture: `/tmp/caliper-word-no-metadata.png`.

final result: passed
