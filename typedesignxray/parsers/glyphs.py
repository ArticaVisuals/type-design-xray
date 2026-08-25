"""Parser for Glyphs source files without a glyphsLib dependency."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from typedesignxray import ir
from typedesignxray.parsers import plist


@dataclass
class _SourceNode:
    point: ir.Point
    kind: str
    smooth: bool = False


def parse_glyphs(
    path: Union[str, os.PathLike],
    layer: Optional[str] = None,
    master: Optional[str] = None,
) -> ir.Font:
    """Read one Glyphs master and optionally substitute a named layer."""
    data = plist.load(path)
    masters = _dict_items(data.get("fontMaster"))
    selected_master = _select_master(masters, master)
    master_id = _text(selected_master.get("id"))
    master_name = _text(selected_master.get("name"))

    requested_layer = None if layer in (None, "") else str(layer)
    glyph_records = _glyph_records(data)
    if requested_layer is not None and not _layer_exists(
        glyph_records.values(), requested_layer
    ):
        raise ValueError(
            "layer {!r} was not found on any glyph".format(requested_layer)
        )

    units_per_em = _number(data.get("unitsPerEm"), 1000.0)
    font_metrics = _read_metrics(data, selected_master)
    format_version = int(
        _number(data.get(".formatVersion", data.get("formatVersion")), 0.0)
    )

    font = ir.Font(
        units_per_em=units_per_em,
        metrics=font_metrics,
        family_name=_family_name(data),
        master_name=master_name,
        source_format="glyphs",
        node_types_exact=True,
    )

    for glyph_record in _dict_items(data.get("glyphs")):
        glyph_name = _glyph_name(glyph_record)
        if not glyph_name:
            continue
        selected_layer = _select_layer(
            glyph_record, requested_layer, master_id
        )
        try:
            contours = _glyph_contours(
                glyph_name,
                glyph_records,
                requested_layer,
                master_id,
                (),
            )
        except ValueError as error:
            raise ValueError(
                "{}: invalid outline in glyph {!r}: {}".format(
                    os.fspath(path), glyph_name, error
                )
            ) from error
        width = _number(selected_layer.get("width"), 0.0)
        glyph_metrics = _copy_metrics(font_metrics)
        anchors = [
            node.x for contour in contours for node in contour.nodes
        ]
        if anchors:
            glyph_metrics.lsb = min(anchors)
            glyph_metrics.rsb = width - max(anchors)

        unicodes = _parse_unicodes(glyph_record.get("unicode"), format_version)
        glyph = ir.Glyph(
            name=glyph_name,
            advance_width=width,
            units_per_em=units_per_em,
            contours=contours,
            metrics=glyph_metrics,
            unicodes=unicodes,
            layer_name=_text(selected_layer.get("name")),
            node_types_exact=True,
            category=_optional_text(glyph_record.get("category")),
            subcategory=_optional_text(glyph_record.get("subCategory")),
            script=_optional_text(glyph_record.get("script")),
        )
        font.glyphs[glyph_name] = glyph
        for codepoint in unicodes:
            font.cmap[codepoint] = glyph_name

        right_group = glyph_record.get(
            "kernRight", glyph_record.get("rightKerningGroup")
        )
        if right_group not in (None, ""):
            font.kern_group_left[glyph_name] = _group_key(
                _text(right_group), "@MMK_L_"
            )
        left_group = glyph_record.get(
            "kernLeft", glyph_record.get("leftKerningGroup")
        )
        if left_group not in (None, ""):
            font.kern_group_right[glyph_name] = _group_key(
                _text(left_group), "@MMK_R_"
            )

    font.kerning = _read_kerning(data, master_id, master_name)
    return font


def list_masters(path: Union[str, os.PathLike]) -> List[ir.MasterInfo]:
    """List selectable masters/styles without parsing glyph geometry."""
    data = plist.load(path)
    return [
        ir.MasterInfo(
            master_id=_text(master.get("id")),
            name=_text(master.get("name")),
        )
        for master in _dict_items(data.get("fontMaster"))
    ]


def list_layers(
    path: Union[str, os.PathLike], glyph_name: str
) -> List[ir.LayerInfo]:
    """Describe source layers without parsing unrelated glyph geometry."""
    data = plist.load(path)
    masters = _dict_items(data.get("fontMaster"))
    master_ids = {_text(item.get("id")) for item in masters}
    master_ids.discard("")
    record = next(
        (
            item
            for item in _dict_items(data.get("glyphs"))
            if _glyph_name(item) == glyph_name
        ),
        None,
    )
    if record is None:
        raise ValueError("glyph {!r} was not found".format(glyph_name))

    result: List[ir.LayerInfo] = []
    for layer_record in _dict_items(record.get("layers")):
        paths = list(_path_entries(layer_record))
        name = _text(layer_record.get("name"))
        layer_id = _text(layer_record.get("layerId"))
        result.append(
            ir.LayerInfo(
                layer_id=layer_id,
                name=name,
                # Glyphs may persist the master's display name on its layer.
                # The layer ID, not an empty name, is the authoritative link.
                is_master=layer_id in master_ids,
                associated_master_id=_text(
                    layer_record.get("associatedMasterId")
                ),
                contour_count=len(paths),
                has_open_contours=any(
                    not _boolean(path.get("closed"), True) for path in paths
                ),
            )
        )
    return result


def _select_master(
    masters: Sequence[Dict[str, Any]], requested: Optional[str]
) -> Dict[str, Any]:
    if not masters:
        if requested is not None:
            raise ValueError(
                "master {!r} was requested, but the font has no masters".format(
                    requested
                )
            )
        return {}
    if requested is None:
        return masters[0]
    requested_text = str(requested)
    for item in masters:
        if requested_text in (_text(item.get("id")), _text(item.get("name"))):
            return item
    raise ValueError("master {!r} was not found".format(requested_text))


def _glyph_records(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        name: record
        for record in _dict_items(data.get("glyphs"))
        for name in [_glyph_name(record)]
        if name
    }


def _glyph_name(record: Dict[str, Any]) -> str:
    return _text(record.get("glyphname", record.get("name")))


def _layer_exists(
    glyphs: Iterable[Dict[str, Any]], requested: str
) -> bool:
    names = [
        _text(layer.get("name"))
        for glyph in glyphs
        for layer in _dict_items(glyph.get("layers"))
        if layer.get("name") not in (None, "")
    ]
    if requested in names:
        return True
    folded = requested.casefold()
    return any(name.casefold() == folded for name in names)


def _select_layer(
    glyph: Dict[str, Any],
    requested: Optional[str],
    master_id: str,
) -> Dict[str, Any]:
    layers = _dict_items(glyph.get("layers"))
    if requested is not None:
        exact = [
            item
            for item in layers
            if item.get("name") not in (None, "")
            and _text(item.get("name")) == requested
        ]
        if exact:
            return exact[0]
        folded = requested.casefold()
        insensitive = [
            item
            for item in layers
            if item.get("name") not in (None, "")
            and _text(item.get("name")).casefold() == folded
        ]
        if insensitive:
            return insensitive[0]

    if master_id:
        for item in layers:
            if _text(item.get("layerId")) == master_id:
                return item
        raise ValueError(
            "master layer {!r} was not found for glyph {!r}".format(
                master_id, _glyph_name(glyph) or "(unnamed)"
            )
        )

    # A malformed source with no fontMaster records has no authoritative
    # master ID. Preserve the legacy best effort only for that case.
    return layers[0] if layers else {}


def _glyph_contours(
    glyph_name: str,
    glyphs: Dict[str, Dict[str, Any]],
    requested_layer: Optional[str],
    master_id: str,
    stack: Tuple[str, ...],
) -> List[ir.Contour]:
    if glyph_name in stack:
        return []
    glyph = glyphs.get(glyph_name)
    if glyph is None:
        return []
    layer = _select_layer(glyph, requested_layer, master_id)
    contours: List[ir.Contour] = []
    next_stack = stack + (glyph_name,)
    for kind, shape in _layer_shapes(layer):
        if kind == "path":
            contours.append(_assemble_contour(shape))
            continue
        reference = _text(shape.get("ref", shape.get("name")))
        if not reference:
            continue
        component_contours = _glyph_contours(
            reference,
            glyphs,
            requested_layer,
            master_id,
            next_stack,
        )
        transform = _component_transform(shape)
        contours.extend(
            _transform_contour(contour, transform)
            for contour in component_contours
        )
    return contours


def _layer_shapes(
    layer: Dict[str, Any]
) -> Iterable[Tuple[str, Dict[str, Any]]]:
    for shape in _dict_items(layer.get("shapes")):
        if "nodes" in shape:
            yield ("path", shape)
        elif "ref" in shape:
            yield ("component", shape)
    for path in _dict_items(layer.get("paths")):
        yield ("path", path)
    for component in _dict_items(layer.get("components")):
        yield ("component", component)


def _path_entries(layer: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for kind, shape in _layer_shapes(layer):
        if kind == "path":
            yield shape


def _assemble_contour(path: Dict[str, Any]) -> ir.Contour:
    closed = _boolean(path.get("closed"), True)
    leading: List[ir.Point] = []
    pending: List[ir.Point] = []
    nodes: List[ir.Node] = []
    first_source_kind = ""

    for raw_node in _items(path.get("nodes")):
        source = _decode_node(raw_node)
        if source.kind == "offcurve":
            if nodes:
                pending.append(source.point)
            else:
                leading.append(source.point)
            continue

        node_type = (
            ir.SEGMENT_CURVE
            if source.kind in ("curve", "quadratic")
            else ir.SEGMENT_LINE
        )
        node = ir.Node(
            point=source.point,
            type=node_type,
            smooth=source.smooth,
        )
        if nodes:
            _append_segment(nodes, node, source.kind, pending, True)
        else:
            nodes.append(node)
            first_source_kind = source.kind
        pending = []

    if not nodes and leading:
        if not closed:
            raise ValueError(
                "an open Glyphs contour cannot contain only off-curve points"
            )
        return _all_off_curve_contour(leading)
    if closed and nodes:
        wrap_controls = pending + leading
        _append_segment(
            nodes, nodes[0], first_source_kind, wrap_controls, False
        )
    return ir.Contour(nodes=nodes, closed=closed)


def _append_segment(
    nodes: List[ir.Node],
    end: ir.Node,
    end_kind: str,
    controls: Sequence[ir.Point],
    append_end: bool,
) -> None:
    start = nodes[-1]
    if end_kind == "quadratic":
        if not controls:
            raise ValueError(
                "a quadratic Glyphs segment requires an off-curve point"
            )
        for index, control in enumerate(controls):
            if index + 1 < len(controls):
                implied = ir.Node(
                    point=_midpoint(control, controls[index + 1]),
                    type=ir.SEGMENT_CURVE,
                    smooth=True,
                )
                _assign_quadratic(nodes[-1], implied, control)
                nodes.append(implied)
            else:
                _assign_quadratic(nodes[-1], end, control)
    elif end_kind != "curve":
        end.type = ir.SEGMENT_LINE
    else:
        end.type = ir.SEGMENT_CURVE
        if controls:
            start.handle_out = controls[0]
        if len(controls) > 1:
            end.handle_in = controls[1]

    if append_end:
        nodes.append(end)


def _assign_quadratic(
    start: ir.Node, end: ir.Node, control: ir.Point
) -> None:
    start.handle_out, end.handle_in = ir.quadratic_to_cubic(
        start.point, control, end.point
    )
    end.type = ir.SEGMENT_CURVE


def _all_off_curve_contour(
    controls: Sequence[ir.Point],
) -> ir.Contour:
    start = ir.Node(
        point=_midpoint(controls[-1], controls[0]),
        type=ir.SEGMENT_CURVE,
        smooth=True,
    )
    nodes = [start]
    for index, control in enumerate(controls):
        if index + 1 < len(controls):
            end = ir.Node(
                point=_midpoint(control, controls[index + 1]),
                type=ir.SEGMENT_CURVE,
                smooth=True,
            )
            _assign_quadratic(nodes[-1], end, control)
            nodes.append(end)
        else:
            _assign_quadratic(nodes[-1], start, control)
    return ir.Contour(nodes=nodes, closed=True)


def _midpoint(first: ir.Point, second: ir.Point) -> ir.Point:
    return (
        0.5 * (first[0] + second[0]),
        0.5 * (first[1] + second[1]),
    )


def _decode_node(raw: Any) -> _SourceNode:
    if isinstance(raw, (list, tuple)):
        parts = [_text(item).strip() for item in raw]
    else:
        text = _text(raw).strip().strip("()")
        parts = [item for item in re.split(r"[\s,]+", text) if item]
    if (
        len(parts) not in (3, 4)
        or (len(parts) == 4 and parts[3].lower() != "smooth")
    ):
        raise ValueError(
            "invalid Glyphs node {!r}: expected x, y, type, and optional "
            "SMOOTH".format(raw)
        )

    point = (
        _node_coordinate(parts[0], raw),
        _node_coordinate(parts[1], raw),
    )
    node_type = parts[2].strip().lower()
    extra = {item.strip().lower() for item in parts[3:]}
    smooth = "smooth" in extra

    # The compact form is a segment letter followed by zero or more flag
    # letters: "c", "cs" (smooth), "ct" (tangent), and whatever Glyphs adds
    # next. Parse it structurally rather than matching whole strings, so a
    # newer Glyphs build cannot make an otherwise-readable file unparseable.
    # Both "s" and "t" mark a tangent-continuous node.
    if node_type not in _LONG_NODE_TYPES and len(node_type) > 1:
        flags = set(node_type[1:])
        if flags & _TANGENT_FLAGS:
            smooth = True
        node_type = node_type[0]

    if node_type in ("o", "offcurve", "off-curve"):
        kind = "offcurve"
    elif node_type in ("c", "curve"):
        kind = "curve"
    elif node_type in ("q", "qcurve", "quadratic"):
        kind = "quadratic"
    elif node_type in ("l", "line"):
        kind = "line"
    else:
        raise ValueError(
            "unsupported Glyphs node type {!r}".format(parts[2])
        )
    return _SourceNode(point=point, kind=kind, smooth=smooth)


#: Long-form node type names, which must not be treated as letter + flags.
_LONG_NODE_TYPES = frozenset(
    ("offcurve", "off-curve", "curve", "qcurve", "quadratic", "line")
)

#: Compact-form flag letters that mark a tangent-continuous node. "s" is the
#: long-standing smooth flag; "t" is Glyphs' tangent node.
_TANGENT_FLAGS = frozenset(("s", "t"))


def _node_coordinate(value: Any, raw: Any) -> float:
    try:
        coordinate = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "non-numeric coordinate {!r} in Glyphs node {!r}".format(
                value, raw
            )
        ) from error
    if not math.isfinite(coordinate):
        raise ValueError(
            "non-finite coordinate {!r} in Glyphs node {!r}".format(
                value, raw
            )
        )
    return coordinate


def _component_transform(
    component: Dict[str, Any]
) -> Tuple[float, float, float, float, float, float]:
    position = _point_pair(component.get("pos"), (0.0, 0.0))
    scale = _point_pair(component.get("scale"), (1.0, 1.0))
    radians = math.radians(_number(component.get("angle"), 0.0))
    cosine = _clean_trig(math.cos(radians))
    sine = _clean_trig(math.sin(radians))
    return (
        cosine * scale[0],
        sine * scale[0],
        -sine * scale[1],
        cosine * scale[1],
        position[0],
        position[1],
    )


def _transform_contour(
    contour: ir.Contour,
    transform: Tuple[float, float, float, float, float, float],
) -> ir.Contour:
    return ir.Contour(
        closed=contour.closed,
        nodes=[
            ir.Node(
                point=_transform_point(node.point, transform),
                type=node.type,
                smooth=node.smooth,
                handle_in=(
                    _transform_point(node.handle_in, transform)
                    if node.handle_in is not None
                    else None
                ),
                handle_out=(
                    _transform_point(node.handle_out, transform)
                    if node.handle_out is not None
                    else None
                ),
            )
            for node in contour.nodes
        ],
    )


def _transform_point(
    point: ir.Point,
    transform: Tuple[float, float, float, float, float, float],
) -> ir.Point:
    a, b, c, d, translate_x, translate_y = transform
    return (
        a * point[0] + c * point[1] + translate_x,
        b * point[0] + d * point[1] + translate_y,
    )


def _clean_trig(value: float) -> float:
    if abs(value) < 1e-12:
        return 0.0
    if abs(value - 1.0) < 1e-12:
        return 1.0
    if abs(value + 1.0) < 1e-12:
        return -1.0
    return value


def _point_pair(value: Any, default: ir.Point) -> ir.Point:
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        text = _text(value).strip().strip("(){}")
        parts = [item for item in re.split(r"[\s,]+", text) if item]
    if len(parts) < 2:
        return default
    return (_number(parts[0], default[0]), _number(parts[1], default[1]))


def _read_metrics(
    data: Dict[str, Any], master: Dict[str, Any]
) -> ir.Metrics:
    definitions = _dict_items(data.get("metrics"))
    values = _dict_items(master.get("metricValues"))
    found: Dict[str, float] = {}
    for index, definition in enumerate(definitions):
        metric_type = _normalise_metric_type(definition.get("type"))
        if index == 0 and not metric_type:
            metric_type = "ascender"
        attribute = {
            "ascender": "ascender",
            "capheight": "cap_height",
            "xheight": "x_height",
            "baseline": "baseline",
            "descender": "descender",
        }.get(metric_type)
        if attribute is None:
            continue
        if index >= len(values) or "pos" not in values[index]:
            continue
        try:
            position = float(values[index].get("pos"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(position):
            found[attribute] = position

    fallbacks = {
        "ascender": "ascender",
        "cap_height": "capHeight",
        "x_height": "xHeight",
        "descender": "descender",
    }
    for attribute, key in fallbacks.items():
        if attribute not in found and key in master:
            try:
                position = float(master.get(key))
            except (TypeError, ValueError):
                continue
            if math.isfinite(position):
                found[attribute] = position
    return ir.Metrics(
        baseline=found.get("baseline", 0.0),
        x_height=found.get("x_height"),
        cap_height=found.get("cap_height"),
        ascender=found.get("ascender"),
        descender=found.get("descender"),
    )


def _normalise_metric_type(value: Any) -> str:
    return re.sub(r"[\s_-]+", "", _text(value).strip().lower())


def _copy_metrics(metrics: ir.Metrics) -> ir.Metrics:
    return ir.Metrics(
        baseline=metrics.baseline,
        x_height=metrics.x_height,
        cap_height=metrics.cap_height,
        ascender=metrics.ascender,
        descender=metrics.descender,
    )


def _parse_unicodes(value: Any, format_version: int) -> List[int]:
    result: List[int] = []
    for item in _flatten(value):
        for text in _text(item).split(","):
            candidate = text.strip()
            if not candidate:
                continue
            if candidate.lower().startswith("u+"):
                candidate = candidate[2:]
                base = 16
            elif candidate.lower().startswith("0x"):
                candidate = candidate[2:]
                base = 16
            elif format_version and format_version <= 2:
                base = 16
            elif (
                not format_version
                and len(candidate) == 4
                and all(char in "0123456789abcdefABCDEF" for char in candidate)
            ):
                base = 16
            elif re.search(r"[a-fA-F]", candidate):
                base = 16
            elif len(candidate) == 4 and candidate.startswith("0"):
                base = 16
            else:
                base = 10
            try:
                codepoint = int(candidate, base)
            except ValueError:
                continue
            if 0 <= codepoint <= 0x10FFFF and codepoint not in result:
                result.append(codepoint)
    return result


def _read_kerning(
    data: Dict[str, Any], master_id: str, master_name: str
) -> Dict[Tuple[str, str], float]:
    if "kerningLTR" in data:
        kerning_root = data.get("kerningLTR")
    else:
        kerning_root = data.get("kerning")
    if not isinstance(kerning_root, dict):
        return {}
    master_kerning = kerning_root.get(master_id)
    if not isinstance(master_kerning, dict):
        master_kerning = kerning_root.get(master_name)
    if not isinstance(master_kerning, dict):
        return {}

    result: Dict[Tuple[str, str], float] = {}
    for left, right_values in master_kerning.items():
        if not isinstance(right_values, dict):
            continue
        for right, value in right_values.items():
            result[(_text(left), _text(right))] = _number(value, 0.0)
    return result


def _group_key(group: str, prefix: str) -> str:
    if group.startswith("@MMK_"):
        return group
    return prefix + group


def _family_name(data: Dict[str, Any]) -> str:
    direct = data.get("familyName")
    if direct not in (None, ""):
        return _text(direct)
    for property_record in _dict_items(data.get("properties")):
        key = _text(property_record.get("key")).lower()
        if key not in ("familyname", "familynames"):
            continue
        values = _dict_items(property_record.get("values"))
        for value in values:
            if _text(value.get("language")).lower() in ("dflt", "default"):
                return _text(value.get("value"))
        if values:
            return _text(values[0].get("value"))
        if property_record.get("value") not in (None, ""):
            return _text(property_record.get("value"))
    return ""


def _boolean(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return _text(value).strip().lower() not in ("", "0", "false", "no")


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (list, tuple)):
        if not value:
            return default
        value = value[0]
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _optional_text(value: Any) -> Optional[str]:
    text = _text(value).strip()
    return text or None


def _items(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _dict_items(value: Any) -> List[Dict[str, Any]]:
    return [item for item in _items(value) if isinstance(item, dict)]


def _flatten(value: Any) -> Iterable[Any]:
    for item in _items(value):
        if isinstance(item, (list, tuple)):
            yield from _flatten(item)
        else:
            yield item


__all__ = ["parse_glyphs", "list_layers", "list_masters"]
