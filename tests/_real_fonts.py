"""Optional real-font test inputs.

The suite runs in full on the synthetic fixtures in this directory and on
``examples/BlueprintDemo.glyphs``. A handful of tests additionally exercise a
real, production designer source when one is available, because shipping files
contain shapes and quirks that synthetic fixtures do not.

Point these at your own files to enable those tests::

    export GLYPHBLUEPRINT_TEST_GLYPHS=~/fonts/MyFont.glyphs
    export GLYPHBLUEPRINT_TEST_OTF=~/fonts/MyFont-Regular.otf

They are skipped when unset, so a fresh clone is green with no setup. Tests
using them assert only format-independent invariants -- never values specific
to one typeface -- so any real font works.
"""

from __future__ import annotations

import os
from pathlib import Path

#: A path that deliberately does not exist, so callers can keep using the
#: plain ``.exists()`` / ``.is_file()`` guards they already had.
_MISSING = Path(__file__).parent / "__no_real_font_configured__"


def _from_env(name: str) -> Path:
    value = os.environ.get(name, "").strip()
    if not value:
        return _MISSING
    path = Path(os.path.expanduser(value))
    return path if path.exists() else _MISSING


REAL_GLYPHS = _from_env("GLYPHBLUEPRINT_TEST_GLYPHS")
REAL_OTF = _from_env("GLYPHBLUEPRINT_TEST_OTF")

SKIP_REASON = (
    "set GLYPHBLUEPRINT_TEST_GLYPHS / GLYPHBLUEPRINT_TEST_OTF to run this"
)

__all__ = ["REAL_GLYPHS", "REAL_OTF", "SKIP_REASON"]
