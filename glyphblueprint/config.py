"""Configuration loading and style resolution.

The renderer consumes one fully resolved :class:`~glyphblueprint.style.Style`.
This module keeps file formats, preset provenance, and friendly validation
errors out of the rendering path.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import style


_PRESET_DIRECTORY = Path(__file__).with_name("presets")


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

    if suffix == ".json":
        data = json.loads(text)
    elif suffix == ".toml":
        data = _toml_loads(text)
    elif text.lstrip().startswith("{"):
        data = json.loads(text)
    else:
        data = _toml_loads(text)

    if not isinstance(data, dict):
        raise ValueError(
            "configuration {!s} must contain a top-level mapping, got {}".format(
                config_path, type(data).__name__
            )
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
    if value.lower() in ("none", "null"):
        return dotted, None
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
    except (TypeError, ValueError) as exc:
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
            if (
                null_strings
                and isinstance(value, str)
                and value.strip().lower() in ("none", "null")
            ):
                value = None
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

    leaves, _ = _valid_paths()
    for override in overrides:
        if not isinstance(override, str):
            raise ValueError(
                "style overrides must be path=value strings, got {!r}".format(
                    override
                )
            )
        path, value = parse_override(override)
        if path not in leaves:
            raise _unknown_key(path, leaves)
        _set_value(resolved, path, value)


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
        raw_preset = config_body.pop("preset", None)
        if raw_preset is not None:
            if not isinstance(raw_preset, str) or not raw_preset.strip():
                raise ValueError(
                    "config key 'preset' must be a non-empty preset name"
                )
            config_preset = raw_preset

    selected_preset = preset if preset is not None else config_preset
    resolved = style.Style()
    canonical_preset: Optional[str] = None

    if selected_preset is not None:
        canonical_preset = _preset_name(selected_preset)
        _apply_mapping(resolved, load_preset(canonical_preset))
    if config_body:
        _apply_mapping(resolved, config_body)
    if overrides is not None:
        _apply_overrides(resolved, overrides)

    if canonical_preset is not None:
        resolved.preset_name = canonical_preset
    elif config_path is not None:
        resolved.preset_name = "custom"
    return resolved


__all__ = [
    "load_config",
    "load_preset",
    "available_presets",
    "resolve_style",
    "parse_override",
]
