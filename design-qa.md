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
