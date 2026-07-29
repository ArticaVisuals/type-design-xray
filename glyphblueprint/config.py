"""Configuration loading and style resolution.

The renderer consumes one fully resolved :class:`~glyphblueprint.style.Style`.
This module keeps file formats, preset provenance, and friendly validation
errors out of the rendering path.
"""

from __future__ import annotations

import difflib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import style


_PRESET_DIRECTORY = Path(__file__).with_name("presets")
_MISSING = object()


def _toml_loads(text: str) -> Dict[str, Any]:
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError as exc:
            raise RuntimeError(
                "TOML configuration support is unavailable. On Python 3.9 or "
                "3.10, install it with: pip install tomli"
            ) from exc
    return tomllib.loads(text)


def load_config(path: Any) -> Dict[str, Any]:
    """Load a JSON or TOML configuration file as a mapping.

    Known extensions take precedence. Extensionless and unusually named files
    are sniffed so a leading JSON object still behaves as users expect.
    """
    config_path = Path(path)
    text = config_path.read_text(encoding="utf-8")
    suffix = config_path.suffix.lower()

    try:
        if suffix == ".json":
            data = json.loads(text)
        elif suffix == ".toml":
            data = _toml_loads(text)
        elif text.lstrip().startswith("{"):
            data = json.loads(text)
        else:
            data = _toml_loads(text)
    except Exception as exc:
        raise ValueError(
            "failed to parse configuration {!s}: {}".format(config_path, exc)
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(
            "configuration {!s} must contain a top-level mapping, got {}".format(
                config_path, type(data).__name__
            )
        )
    if not data:
        raise ValueError(
            "configuration {!s} must not be empty".format(config_path)
        )
    return data


def available_presets() -> List[str]:
    """Return installed preset names in stable display order."""
    if not _PRESET_DIRECTORY.is_dir():
        return []
    return sorted(path.stem for path in _PRESET_DIRECTORY.glob("*.json"))


def _preset_name(name: Any) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("preset name must be a non-empty string")
    cleaned = name.strip()
    if cleaned.lower().endswith(".json"):
        cleaned = cleaned[:-5]
    if cleaned not in available_presets():
        choices = ", ".join(available_presets()) or "(none installed)"
        raise ValueError(
            "unknown preset {!r}; available presets: {}".format(name, choices)
        )
    return cleaned


def load_preset(name: str) -> Dict[str, Any]:
    """Load a preset from package data so installed copies remain portable."""
    resolved_name = _preset_name(name)
    preset_path = _PRESET_DIRECTORY / "{}.json".format(resolved_name)
    data = json.loads(preset_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(
            "preset {!r} must contain a top-level mapping, got {}".format(
                resolved_name, type(data).__name__
            )
        )
    return data


def parse_override(text: str) -> Tuple[str, Any]:
    """Split one CLI ``path=value`` expression without pre-coercing its value."""
    if "=" not in text:
        raise ValueError(
            "style override {!r} must use dotted.path=value syntax".format(text)
        )
    dotted, value = text.split("=", 1)
    dotted = dotted.strip()
    value = value.strip()
    if not dotted:
        raise ValueError("style override path may not be empty")
    if not value:
        raise ValueError(
            "style override {!r} must have a non-empty value".format(text)
        )
    return dotted, value


def _valid_paths() -> Tuple[List[str], set]:
    leaves = style.dotted_paths()
    prefixes = set()
    for dotted in leaves:
        parts = dotted.split(".")
        for index in range(1, len(parts)):
            prefixes.add(".".join(parts[:index]))
    return leaves, prefixes


def _unknown_key(path: str, leaves: List[str]) -> KeyError:
    matches = difflib.get_close_matches(path, leaves, n=1, cutoff=0.0)
    suggestion = (
        "; did you mean {!r}?".format(matches[0])
        if matches
        else ""
    )
    return KeyError("unknown style key {!r}{}".format(path, suggestion))


def _declared_type(resolved: style.Style, path: str) -> str:
    target: Any = resolved
    parts = path.split(".")
    for part in parts[:-1]:
        target = getattr(target, part)
    declared: Any = next(
        field.type for field in fields(target) if field.name == parts[-1]
    )
    annotation = declared if isinstance(declared, str) else str(declared)
    optional = "Optional" in annotation

    if "List[str]" in annotation:
        expected = "a list of strings"
    elif "bool" in annotation:
        expected = "a boolean"
    elif "int" in annotation:
        expected = "an integer"
    elif "float" in annotation:
        expected = "a number"
    else:
        expected = "a string"
    if optional:
        expected = "{} or null".format(expected)
    return expected


def _set_value(resolved: style.Style, path: str, value: Any) -> None:
    try:
        resolved.set_path(path, value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            "invalid value for {!r}: expected {}, got {!r}".format(
                path, _declared_type(resolved, path), value
            )
        ) from exc


def _apply_mapping(
    resolved: style.Style,
    data: Mapping,
    prefix: str = "",
    null_strings: bool = False,
) -> None:
    leaves, prefixes = _valid_paths()
    for raw_key, value in data.items():
        if not isinstance(raw_key, str):
            raise KeyError("style keys must be strings, got {!r}".format(raw_key))
        path = "{}.{}".format(prefix, raw_key) if prefix else raw_key
        if path in leaves:
            # "none"/"null" is resolved by style._coerce, which is the only
            # layer that knows whether the target field is Optional ("unset")
            # or a required string whose value is literally "none" -- as it is
            # for marker shapes.
            _set_value(resolved, path, value)
        elif path in prefixes:
            if not isinstance(value, Mapping):
                raise ValueError(
                    "invalid value for {!r}: expected a mapping, got {!r}".format(
                        path, value
                    )
                )
            _apply_mapping(resolved, value, path, null_strings)
        else:
            raise _unknown_key(path, leaves)


def _apply_overrides(resolved: style.Style, overrides: Any) -> None:
    if isinstance(overrides, Mapping):
        _apply_mapping(resolved, overrides, null_strings=True)
        return
    if isinstance(overrides, str) or not isinstance(overrides, Sequence):
        raise ValueError("overrides must be a mapping or a list of path=value strings")

    leaves, prefixes = _valid_paths()
    for override in overrides:
        if not isinstance(override, str):
            raise ValueError(
                "style overrides must be path=value strings, got {!r}".format(
                    override
                )
            )
        path, value = parse_override(override)
        if path not in leaves:
            if path in prefixes:
                raise ValueError(
                    "style override {!r} names a section; "
                    "set one of its leaf values instead".format(path)
                )
            raise _unknown_key(path, leaves)
        _set_value(resolved, path, value)


def _validate_style(resolved: style.Style) -> None:
    choices = {
        "canvas.frame": style.FRAME_MODES,
        "handles.point.shape": style.SHAPES,
        "nodes.corner.shape": style.SHAPES,
        "nodes.smooth.shape": style.SHAPES,
        "outline.dash": style.DASH_PATTERNS,
        "handles.line.dash": style.DASH_PATTERNS,
        "metrics.line.dash": style.DASH_PATTERNS,
        "metrics.sidebearing_line.dash": style.DASH_PATTERNS,
    }
    for path, allowed in choices.items():
        value = resolved.get_path(path)
        if value not in allowed:
            raise ValueError(
                "invalid value for {!r}: expected one of {}, got {!r}".format(
                    path, ", ".join(allowed), value
                )
            )

    linecaps = ("butt", "round", "square")
    linejoins = ("miter", "round", "bevel", "miter-clip", "arcs")
    for path in (
        "outline.linecap",
        "handles.line.linecap",
        "metrics.line.linecap",
        "metrics.sidebearing_line.linecap",
    ):
        value = resolved.get_path(path)
        if value not in linecaps:
            raise ValueError(
                "invalid value for {!r}: expected one of {}, got {!r}".format(
                    path, ", ".join(linecaps), value
                )
            )
    for path in (
        "outline.linejoin",
        "handles.line.linejoin",
        "metrics.line.linejoin",
        "metrics.sidebearing_line.linejoin",
    ):
        value = resolved.get_path(path)
        if value not in linejoins:
            raise ValueError(
                "invalid value for {!r}: expected one of {}, got {!r}".format(
                    path, ", ".join(linejoins), value
                )
            )

    label_style = resolved.metrics.label_style
    if label_style not in ("normal", "italic", "oblique"):
        raise ValueError(
            "invalid value for 'metrics.label_style': expected normal, italic, "
            "or oblique, got {!r}".format(label_style)
        )
    label_weight = resolved.metrics.label_weight
    if label_weight not in ("normal", "bold"):
        try:
            numeric_weight = int(label_weight)
        except (TypeError, ValueError, OverflowError):
            numeric_weight = 0
        if str(numeric_weight) != label_weight.strip() or not (
            100 <= numeric_weight <= 900
        ):
            raise ValueError(
                "invalid value for 'metrics.label_weight': expected normal, "
                "bold, or a number from 100 to 900, got {!r}".format(
                    label_weight
                )
            )

    unknown_metrics = [
        name for name in resolved.metrics.show if name not in style.METRIC_NAMES
    ]
    if unknown_metrics:
        raise ValueError(
            "invalid value for 'metrics.show': unknown metric {!r}; "
            "expected only {}".format(
                unknown_metrics[0], ", ".join(style.METRIC_NAMES)
            )
        )

    def finite(path: str, value: Any) -> bool:
        try:
            return math.isfinite(float(value))
        except OverflowError as exc:
            raise ValueError(
                "invalid value for {!r}: {}".format(path, exc)
            ) from exc
        except (TypeError, ValueError):
            return False

    positive = ("canvas.width", "canvas.png_width")
    for path in positive:
        value = resolved.get_path(path)
        if value is None:
            continue
        if not finite(path, value) or value <= 0:
            raise ValueError(
                "invalid value for {!r}: must be greater than zero, got {!r}".format(
                    path, value
                )
            )

    nonnegative = (
        "canvas.padding",
        "outline.width",
        "handles.point.size",
        "handles.point.stroke_width",
        "handles.line.width",
        "nodes.corner.size",
        "nodes.corner.stroke_width",
        "nodes.smooth.size",
        "nodes.smooth.stroke_width",
        "metrics.line.width",
        "metrics.sidebearing_line.width",
        "metrics.label_size",
        "metrics.extend",
    )
    for path in nonnegative:
        value = resolved.get_path(path)
        if not finite(path, value) or value < 0:
            raise ValueError(
                "invalid value for {!r}: must not be negative, got {!r}".format(
                    path, value
                )
            )

    finite_only = ("metrics.label_letter_spacing",)
    for path in finite_only:
        value = resolved.get_path(path)
        if not finite(path, value):
            raise ValueError(
                "invalid value for {!r}: must be finite, got {!r}".format(
                    path, value
                )
            )

    opacity_paths = (
        "outline.opacity",
        "outline.fill_opacity",
        "handles.point.opacity",
        "handles.line.opacity",
        "nodes.corner.opacity",
        "nodes.smooth.opacity",
        "metrics.line.opacity",
        "metrics.sidebearing_line.opacity",
        "metrics.label_opacity",
    )
    for path in opacity_paths:
        value = resolved.get_path(path)
        if not finite(path, value) or not 0.0 <= value <= 1.0:
            raise ValueError(
                "invalid value for {!r}: expected a number from 0 to 1, "
                "got {!r}".format(path, value)
            )

    if resolved.canvas.width - 2.0 * resolved.canvas.padding <= 0:
        raise ValueError(
            "invalid value for 'canvas.padding': padding {!r} with width {!r} "
            "leaves no room for glyph geometry".format(
                resolved.canvas.padding, resolved.canvas.width
            )
        )


def resolve_style(
    preset: Optional[str] = None,
    config_path: Any = None,
    overrides: Any = None,
) -> style.Style:
    """Resolve defaults, one preset, a config body, then CLI overrides."""
    config_body: Dict[str, Any] = {}
    config_preset: Optional[str] = None
    if config_path is not None:
        config_body = dict(load_config(config_path))
        raw_preset = config_body.pop("preset", _MISSING)
        if raw_preset is not _MISSING:
            if not isinstance(raw_preset, str) or not raw_preset.strip():
                raise ValueError(
                    "config key 'preset' must be a non-empty preset name"
                )
            config_preset = raw_preset

    selected_preset = preset if preset is not None else config_preset
    resolved = style.Style()
    canonical_preset: Optional[str] = None

    if selected_preset is not None:
        try:
            canonical_preset = _preset_name(selected_preset)
        except ValueError as exc:
            if preset is None and config_preset is not None:
                raise ValueError(
                    "configuration {!s}: {}".format(config_path, exc)
                ) from exc
            raise
        _apply_mapping(resolved, load_preset(canonical_preset))
    if config_body:
        _apply_mapping(resolved, config_body)
    if overrides is not None:
        _apply_overrides(resolved, overrides)

    if canonical_preset is not None:
        resolved.preset_name = canonical_preset
    elif config_path is not None:
        resolved.preset_name = "custom"
    _validate_style(resolved)
    return resolved


__all__ = [
    "load_config",
    "load_preset",
    "available_presets",
    "resolve_style",
    "parse_override",
]
