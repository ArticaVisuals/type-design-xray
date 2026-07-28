"""Command-line interface for glyphblueprint."""

from __future__ import annotations

import argparse
import sys
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

from . import __version__
from .api import _normalise_formats, _safe_filename, blueprint_to_files
from .config import available_presets
from .parsers import list_font_layers
from .style import METRIC_NAMES, dotted_paths


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="glyphblueprint",
        description=(
            "Export editable SVG blueprints and optional raster files from "
            "font sources."
        ),
    )
    parser.add_argument("font_file", nargs="?", metavar="FONT_FILE")
    parser.add_argument("text", nargs="?", metavar="TEXT")
    parser.add_argument(
        "--layer",
        metavar="NAME",
        help="Glyphs/UFO layer to read (default: finalized master)",
    )
    parser.add_argument(
        "--master",
        metavar="NAME_OR_ID",
        help="master to read by name or ID (default: first)",
    )
    parser.add_argument(
        "--list-layers",
        metavar="GLYPH",
        help="print available layers for a glyph and exit",
    )
    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="print available style presets and exit",
    )
    parser.add_argument(
        "--list-style-keys",
        action="store_true",
        help="print every settable dotted style key and exit",
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="style configuration file (JSON or TOML)",
    )
    parser.add_argument("--preset", metavar="NAME", help="named style preset")

    metrics = parser.add_mutually_exclusive_group()
    metrics.add_argument(
        "--metrics",
        metavar="LIST",
        help=(
            "comma-separated metric guides; also accepts 'all' and 'none'"
        ),
    )
    metrics.add_argument(
        "--no-metrics",
        action="store_true",
        help="force metric guides off",
    )

    handles = parser.add_mutually_exclusive_group()
    handles.add_argument(
        "--show-handles",
        dest="show_handles",
        action="store_true",
        help="show handle lines and points",
    )
    handles.add_argument(
        "--no-handles",
        dest="show_handles",
        action="store_false",
        help="hide handle lines and points",
    )

    nodes = parser.add_mutually_exclusive_group()
    nodes.add_argument(
        "--show-nodes",
        dest="show_nodes",
        action="store_true",
        help="show on-curve nodes",
    )
    nodes.add_argument(
        "--no-nodes",
        dest="show_nodes",
        action="store_false",
        help="hide on-curve nodes",
    )

    outline = parser.add_mutually_exclusive_group()
    outline.add_argument(
        "--show-outline",
        dest="show_outline",
        action="store_true",
        help="show the glyph outline",
    )
    outline.add_argument(
        "--no-outline",
        dest="show_outline",
        action="store_false",
        help="hide the glyph outline",
    )

    fill = parser.add_mutually_exclusive_group()
    fill.add_argument(
        "--fill",
        dest="show_fill",
        action="store_true",
        help="fill closed glyph outlines",
    )
    fill.add_argument(
        "--no-fill",
        dest="show_fill",
        action="store_false",
        help="disable glyph fill",
    )

    parser.set_defaults(
        show_handles=None,
        show_nodes=None,
        show_outline=None,
        show_fill=None,
    )
    parser.add_argument(
        "--background",
        metavar="COLOR",
        help="canvas background color; use 'none' for transparency",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="output file or directory (default: derived from input)",
    )
    parser.add_argument(
        "--format",
        default="svg",
        metavar="FORMATS",
        help="comma-separated output formats: svg, png, pdf (default: svg)",
    )
    parser.add_argument(
        "--png-width",
        type=int,
        metavar="PX",
        help="PNG output width in pixels",
    )
    parser.add_argument(
        "--width",
        type=int,
        metavar="PX",
        help="SVG canvas width in pixels",
    )
    parser.add_argument(
        "--padding",
        type=float,
        metavar="PX",
        help="canvas padding in pixels",
    )
    parser.add_argument(
        "--frame",
        choices=("auto", "em", "metrics"),
        help="canvas framing mode",
    )
    parser.add_argument(
        "--tracking",
        type=float,
        default=0.0,
        metavar="UNITS",
        help="tracking between glyphs in font units",
    )
    parser.add_argument(
        "--no-kerning",
        action="store_true",
        help="disable kerning",
    )
    parser.add_argument(
        "--per-glyph",
        action="store_true",
        help="also emit one file per glyph",
    )
    parser.add_argument(
        "--set",
        dest="style_overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="set any dotted style key; may be repeated",
    )

    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--quiet",
        action="store_true",
        help="suppress written-file summaries",
    )
    output.add_argument(
        "--verbose",
        action="store_true",
        help="show tracebacks for unexpected failures",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s {}".format(__version__),
    )
    return parser


def _metric_overrides(value: str) -> List[str]:
    values = [item.strip().lower() for item in value.split(",") if item.strip()]
    if len(values) == 1 and values[0] == "all":
        values = list(METRIC_NAMES)
    elif len(values) == 1 and values[0] == "none":
        return ["metrics.visible=false"]
    elif "all" in values or "none" in values:
        raise ValueError(
            "'all' and 'none' must be used alone in --metrics"
        )
    if not values:
        raise ValueError(
            "--metrics requires a comma-separated list, 'all', or 'none'"
        )

    unknown = [name for name in values if name not in METRIC_NAMES]
    if unknown:
        raise ValueError(
            "unknown metric {!r}; choose from {}".format(
                unknown[0], ", ".join(METRIC_NAMES)
            )
        )
    deduplicated = list(dict.fromkeys(values))
    return [
        "metrics.visible=true",
        "metrics.show={}".format(",".join(deduplicated)),
    ]


def _explicit_overrides(args: argparse.Namespace) -> List[str]:
    overrides: List[str] = []
    if args.metrics is not None:
        overrides.extend(_metric_overrides(args.metrics))
    elif args.no_metrics:
        overrides.append("metrics.visible=false")

    if args.show_handles is not None:
        value = str(args.show_handles).lower()
        overrides.extend(
            [
                "layers.handle_lines={}".format(value),
                "layers.handle_points={}".format(value),
            ]
        )
    if args.show_nodes is not None:
        overrides.append(
            "layers.nodes={}".format(str(args.show_nodes).lower())
        )
    if args.show_outline is not None:
        overrides.append(
            "layers.outline={}".format(str(args.show_outline).lower())
        )
    if args.show_fill is not None:
        overrides.append(
            "outline.fill_enabled={}".format(str(args.show_fill).lower())
        )
    if args.background is not None:
        overrides.append("canvas.background={}".format(args.background))
    if args.png_width is not None:
        overrides.append("canvas.png_width={}".format(args.png_width))
    if args.width is not None:
        overrides.append("canvas.width={}".format(args.width))
    if args.padding is not None:
        overrides.append("canvas.padding={}".format(args.padding))
    if args.frame is not None:
        overrides.append("canvas.frame={}".format(args.frame))

    overrides.extend(args.style_overrides)
    return overrides


def _error_message(exc: BaseException) -> str:
    if isinstance(exc, FileNotFoundError):
        filename = getattr(exc, "filename", None)
        if filename:
            return "file not found: {}".format(filename)
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    message = str(exc).strip()
    return message or type(exc).__name__


def _svg_summary(path: Path) -> Optional[Tuple[str, str, int, int]]:
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ET.ParseError):
        return None
    if root.tag.rsplit("}", 1)[-1] != "svg":
        return None

    glyphs = {
        element.get("data-glyph-index")
        for element in root.iter()
        if element.get("data-glyph-index") is not None
    }
    node_count = 0
    for layer in root.iter():
        if layer.get("data-layer") != "nodes":
            continue
        node_count += sum(
            1
            for element in layer.iter()
            if element.get("data-shape") is not None
        )
    return (
        root.get("width", "?"),
        root.get("height", "?"),
        len(glyphs),
        node_count,
    )


def _print_written(paths: Sequence[Path], quiet: bool) -> None:
    if quiet:
        return
    for path in paths:
        summary = _svg_summary(path)
        if summary is None:
            print("wrote {}".format(path))
            continue
        width, height, glyph_count, node_count = summary
        glyph_word = "glyph" if glyph_count == 1 else "glyphs"
        node_word = "node" if node_count == 1 else "nodes"
        print(
            "wrote {} ({}x{}, {} {}, {} {})".format(
                path,
                width,
                height,
                glyph_count,
                glyph_word,
                node_count,
                node_word,
            )
        )


def _require_font(parser: argparse.ArgumentParser, args: argparse.Namespace) -> bool:
    if args.font_file is not None:
        return True
    parser.print_usage(sys.stderr)
    print("glyphblueprint: error: a font file is required", file=sys.stderr)
    return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the CLI and return a process-style status code."""
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    if args.list_presets:
        for preset in available_presets():
            print(preset)
        return 0
    if args.list_style_keys:
        for dotted in dotted_paths():
            print(dotted)
        return 0
    if not _require_font(parser, args):
        return 2

    try:
        font_path = Path(args.font_file)
        if not font_path.exists():
            raise FileNotFoundError(
                2, "No such file or directory", str(font_path)
            )

        if args.list_layers is not None:
            layers = list_font_layers(font_path, args.list_layers)
            for layer in layers:
                if layer.name:
                    print(layer.name)
                elif layer.layer_id:
                    print("(master {})".format(layer.layer_id))
                else:
                    print("(unnamed master layer)")
            return 0

        if args.text is None:
            raise ValueError(
                "text is required unless --list-layers is used"
            )

        formats = _normalise_formats(args.format)
        overrides = _explicit_overrides(args)
        out = args.out
        if out is None:
            output_name = _safe_filename(args.text)
            if args.per_glyph and len(formats) > 1:
                out = output_name
            else:
                out = "{}.{}".format(output_name, formats[0])

        paths = blueprint_to_files(
            font_path,
            args.text,
            out,
            formats=formats,
            per_glyph=args.per_glyph,
            layer=args.layer,
            master=args.master,
            preset=args.preset,
            config=args.config,
            overrides=overrides or None,
            tracking=args.tracking,
            apply_kerning=not args.no_kerning,
        )
        _print_written(paths, args.quiet)
        return 0
    except (FileNotFoundError, OSError, ValueError, KeyError, RuntimeError) as exc:
        written_paths = getattr(exc, "written_paths", ())
        _print_written(written_paths, args.quiet)
        print("glyphblueprint: error: {}".format(_error_message(exc)), file=sys.stderr)
        return 2
    except Exception as exc:
        if args.verbose:
            traceback.print_exc()
        else:
            print(
                "glyphblueprint: error: unexpected failure: {}".format(
                    _error_message(exc)
                ),
                file=sys.stderr,
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
