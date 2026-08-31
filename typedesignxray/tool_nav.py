"""Shared navigation for the three browser tools."""

from __future__ import annotations

import html
from typing import Tuple


TOOLS: Tuple[Tuple[str, str, str, str], ...] = (
    (
        "xray",
        "/",
        "X-Ray Blueprint",
        "Inspect and export outlines, nodes, handles, spacing, and metrics.",
    ),
    (
        "specimen",
        "/specimen",
        "Font Specimen",
        "Animate every designed symbol as a two-up type specimen.",
    ),
    (
        "process",
        "/process",
        "Font Design Process",
        "Play one glyph from its skeleton layers to the active master.",
    ),
)


def tool_switcher(active: str) -> str:
    """Return semantic tabs for the unified three-tool browser app."""
    tool_ids = {item[0] for item in TOOLS}
    if active not in tool_ids:
        raise ValueError("unknown active tool {!r}".format(active))

    links = []
    for tool_id, href, name, summary in TOOLS:
        current = tool_id == active
        links.append(
            (
                '<a class="tool-tab{}" href="{}"{}>'
                '<span class="tool-name">{}</span>'
                '<span class="tool-summary">{}</span>'
                "</a>"
            ).format(
                " active" if current else "",
                html.escape(href, quote=True),
                ' aria-current="page"' if current else "",
                html.escape(name),
                html.escape(summary),
            )
        )
    return (
        '<nav class="tool-switcher" aria-label="Type Design X-Ray tools">'
        + "".join(links)
        + "</nav>"
    )


__all__ = ["TOOLS", "tool_switcher"]
