from __future__ import annotations

from pathlib import Path

import pytest

from typedesignxray import MasterInfo, list_font_masters, load_font
from typedesignxray.parsers import plist
from typedesignxray.parsers.glyphs import (
    list_layers,
    list_masters,
    parse_glyphs,
)


FIXTURES = Path(__file__).parent / "fixtures"
DEMO = Path(__file__).resolve().parent.parent / "examples" / "BlueprintDemo.glyphs"


def test_plist_reader_handles_openstep_syntax_and_inline_tuples() -> None:
    source = r'''
        // Glyphs permits dotted, unquoted keys before the normal content.
        .appVersion = "4\"000";
        .formatVersion = 4;
        /* Arrays can contain dictionaries and v4 node tuples. */
        payload = {
            bare-key = unquoted;
            escaped = "line\nsnowman:\U2603";
            nested = ({ key = value; }, (479,675,l),);
            commaList = first,second,;
        };
        # Hash comments occur in some hand-edited fixtures.
    '''

    value = plist.loads(source)

    assert value[".appVersion"] == '4"000'
    assert value["payload"]["bare-key"] == "unquoted"
    assert value["payload"]["escaped"] == "line\nsnowman:☃"
    assert value["payload"]["nested"] == [
        {"key": "value"},
        ["479", "675", "l"],
    ]
    assert value["payload"]["commaList"] == ["first", "second"]


def test_plist_load_reports_path_for_malformed_source(
    tmp_path: Path,
) -> None:
    path = tmp_path / "truncated.glyphs"
    path.write_text("{ glyphs = (", encoding="utf-8")

    with pytest.raises(
        plist.PlistParseError,
        match=r"truncated\.glyphs.*unterminated array",
    ):
        plist.load(path)


def test_empty_glyphs_source_reports_path_and_problem(
    tmp_path: Path,
) -> None:
    path = tmp_path / "empty.glyphs"
    path.write_text("", encoding="utf-8")

    with pytest.raises(
        plist.PlistParseError,
        match=r"empty\.glyphs.*empty property list",
    ):
        parse_glyphs(path)


def test_plist_load_accepts_legacy_mac_roman_encoding(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.glyphs"
    path.write_bytes(
        b'{ familyName = "Caf' + bytes([0x8E]) + b'"; glyphs = (); }'
    )

    assert parse_glyphs(path).family_name == "Caf\u00e9"


@pytest.mark.parametrize(
    ("node", "problem"),
    [
        ("(479,675)", "expected x, y, type"),
        ("(479,675,l,extra)", "expected x, y, type"),
        ("(not-a-number,675,l)", "non-numeric coordinate"),
        ("(479,675,unknown)", "unsupported Glyphs node type"),
    ],
)
def test_malformed_nodes_report_file_glyph_and_problem(
    tmp_path: Path,
    node: str,
    problem: str,
) -> None:
    path = tmp_path / "bad-node.glyphs"
    path.write_text(
        """
        {
        fontMaster = ({ id = M1; });
        glyphs = ({
            glyphname = bad;
            layers = ({
                layerId = M1;
                shapes = ({ nodes = (%s); });
            });
        });
        }
        """
        % node,
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=r"bad-node\.glyphs.*glyph 'bad'.*{}".format(problem),
    ):
        parse_glyphs(path)


def test_v4_node_assembly_wrap_smooth_open_and_quadratic() -> None:
    font = parse_glyphs(FIXTURES / "synthetic_v4.glyphs")
    glyph = font.glyphs["base"]

    closed = glyph.contours[0]
    first, curved, last = closed.nodes
    assert closed.closed is True
    assert first.point == (20.0, 0.0)
    assert first.type == "curve"
    assert first.smooth is True
    assert first.handle_in == (10.0, 0.0)
    assert first.handle_out == (30.0, 0.0)
    assert curved.type == "curve"
    assert curved.smooth is True
    assert curved.handle_in == (40.0, 10.0)
    assert last.type == "line"
    assert last.smooth is True
    assert last.handle_out == (110.0, 0.0)

    open_contour = glyph.contours[1]
    assert open_contour.closed is False
    assert open_contour.nodes[-1].handle_in == (20.0, 30.0)
    assert open_contour.nodes[0].handle_in is None

    quadratic = glyph.contours[2]
    assert quadratic.closed is False
    assert quadratic.nodes[-1].type == "curve"
    assert quadratic.nodes[-1].smooth is True
    assert quadratic.nodes[0].handle_out == pytest.approx(
        (20.0 / 3.0, 140.0 / 3.0)
    )
    assert quadratic.nodes[-1].handle_in == pytest.approx(
        (40.0 / 3.0, 140.0 / 3.0)
    )


def test_quadratic_paths_insert_implied_oncurve_points(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quadratic-paths.glyphs"
    path.write_text(
        """
        {
        fontMaster = ({ id = M1; });
        glyphs = ({
            glyphname = quadratic;
            layers = ({
                layerId = M1;
                shapes = (
                    {
                        closed = 0;
                        nodes = (
                            (0,0,l),
                            (30,60,o),
                            (60,60,o),
                            (90,0,q),
                        );
                    },
                    {
                        nodes = (
                            (0,0,o),
                            (100,0,o),
                            (100,100,o),
                            (0,100,o),
                        );
                    },
                );
            });
        });
        }
        """,
        encoding="utf-8",
    )

    open_quadratic, all_off_curve = (
        parse_glyphs(path).glyphs["quadratic"].contours
    )
    assert [node.point for node in open_quadratic.nodes] == [
        (0.0, 0.0),
        (45.0, 60.0),
        (90.0, 0.0),
    ]
    assert open_quadratic.nodes[0].handle_out == pytest.approx((20.0, 40.0))
    assert open_quadratic.nodes[1].handle_in == pytest.approx((35.0, 60.0))
    assert open_quadratic.nodes[1].handle_out == pytest.approx((55.0, 60.0))
    assert open_quadratic.nodes[2].handle_in == pytest.approx((70.0, 40.0))

    assert all_off_curve.closed is True
    assert [node.point for node in all_off_curve.nodes] == [
        (0.0, 50.0),
        (50.0, 0.0),
        (100.0, 50.0),
        (50.0, 100.0),
    ]
    assert all(node.smooth for node in all_off_curve.nodes)
    assert all(
        start.handle_out is not None and end.handle_in is not None
        for start, end in all_off_curve.segments()
    )


def test_component_decomposition_and_named_layer_fallback() -> None:
    default_font = parse_glyphs(FIXTURES / "synthetic_v4.glyphs")
    composite = default_font.glyphs["composite"]
    assert len(composite.contours) == 3
    assert composite.contours[0].nodes[0].point == (140.0, 200.0)
    assert composite.contours[0].nodes[0].handle_in == (120.0, 200.0)

    alternate_font = parse_glyphs(
        FIXTURES / "synthetic_v4.glyphs", layer="alternate"
    )
    assert alternate_font.glyphs["composite"].layer_name == "Alternate"
    assert alternate_font.glyphs["composite"].contours[0].nodes[0].point == (
        25.0,
        0.0,
    )
    assert alternate_font.glyphs["base"].layer_name == ""
    with pytest.raises(ValueError, match="not found on any glyph"):
        parse_glyphs(FIXTURES / "synthetic_v4.glyphs", layer="Missing")


def test_exact_layer_id_wins_over_name_and_applies_to_components(
    tmp_path: Path,
) -> None:
    path = tmp_path / "layer-id-components.glyphs"
    path.write_text(
        """
        {
        fontMaster = (
            { id = M1; name = Regular; },
            { id = M2; name = Bold; },
        );
        glyphs = (
            {
                glyphname = base;
                layers = (
                    {
                        layerId = NAMED-BASE;
                        name = PROCESS-L2;
                        shapes = ({ nodes = ((10,0,l),(10,100,l)); });
                        width = 110;
                    },
                    {
                        associatedMasterId = M2;
                        layerId = PROCESS-L2;
                        name = "Process layer";
                        shapes = ({ nodes = ((20,0,l),(20,200,l)); });
                        width = 220;
                    },
                    {
                        layerId = M2;
                        shapes = ({ nodes = ((30,0,l),(30,300,l)); });
                        width = 330;
                    },
                );
            },
            {
                glyphname = composite;
                layers = (
                    {
                        layerId = NAMED-COMPOSITE;
                        name = PROCESS-L2;
                        shapes = ({ ref = base; pos = (100,0); });
                        width = 410;
                    },
                    {
                        associatedMasterId = M2;
                        layerId = PROCESS-L2;
                        name = "Process layer";
                        shapes = ({ ref = base; pos = (200,0); });
                        width = 420;
                    },
                    {
                        layerId = M2;
                        shapes = ({ ref = base; pos = (300,0); });
                        width = 430;
                    },
                );
            },
            {
                glyphname = masterFallback;
                layers = (
                    { layerId = M1; width = 510; },
                    { layerId = M2; width = 520; },
                );
            },
        );
        }
        """,
        encoding="utf-8",
    )

    font = load_font(path, layer="PROCESS-L2", master="Bold")

    assert font.glyphs["base"].advance_width == 220
    assert font.glyphs["base"].layer_name == "Process layer"
    assert font.glyphs["base"].contours[0].nodes[0].point == (20.0, 0.0)
    assert font.glyphs["composite"].advance_width == 420
    assert font.glyphs["composite"].contours[0].nodes[0].point == (
        220.0,
        0.0,
    )
    assert font.glyphs["masterFallback"].advance_width == 520


def test_named_master_layer_wins_over_earlier_background_layer(
    tmp_path: Path,
) -> None:
    """A master is identified by its layer ID even when it has a name.

    Glyphs can save the master layer with its display name (for example,
    ``Regular``). If a background layer precedes it, requiring an empty name
    silently selects the background instead.
    """
    path = tmp_path / "named-master.glyphs"
    path.write_text(
        """
        {
        fontMaster = ({ id = M1; name = Regular; });
        glyphs = ({
            glyphname = s;
            layers = (
                {
                    associatedMasterId = M1;
                    layerId = BACKGROUND;
                    name = "Skeleton v1";
                    shapes = ({
                        nodes = ((20,0,l),(200,500,l),(392,0,l));
                    });
                    width = 413;
                },
                {
                    layerId = M1;
                    name = Regular;
                    shapes = ({
                        nodes = ((50,0,l),(250,500,l),(472,0,l));
                    });
                    width = 523;
                },
            );
        });
        }
        """,
        encoding="utf-8",
    )

    glyph = parse_glyphs(path).glyphs["s"]
    assert glyph.advance_width == 523
    assert glyph.layer_name == "Regular"
    assert glyph.contours[0].nodes[0].point == (50.0, 0.0)

    layers = list_layers(path, "s")
    assert next(layer for layer in layers if layer.name == "Regular").is_master
    assert not next(
        layer for layer in layers if layer.name == "Skeleton v1"
    ).is_master


def test_selected_master_never_falls_back_to_another_layer(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-master-layer.glyphs"
    path.write_text(
        """
        {
        fontMaster = ({ id = M1; name = Regular; });
        glyphs = ({
            glyphname = s;
            layers = ({
                associatedMasterId = M1;
                layerId = BACKGROUND;
                name = "Skeleton v1";
                shapes = ({
                    nodes = ((20,0,l),(200,500,l),(392,0,l));
                });
                width = 413;
            });
        });
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match=r"master layer 'M1'.*glyph 's'",
    ):
        parse_glyphs(path)


def test_requested_master_wins_regardless_of_layer_order_or_name(
    tmp_path: Path,
) -> None:
    path = tmp_path / "multiple-masters.glyphs"
    path.write_text(
        """
        {
        fontMaster = (
            { id = M1; name = Regular; },
            { id = M2; name = Bold; },
        );
        glyphs = ({
            glyphname = s;
            layers = (
                {
                    associatedMasterId = M1;
                    layerId = BACKGROUND;
                    name = Backup;
                    shapes = ({ nodes = ((10,0,l),(100,200,l),(190,0,l)); });
                    width = 200;
                },
                {
                    layerId = M2;
                    name = Bold;
                    shapes = ({ nodes = ((30,0,l),(150,300,l),(270,0,l)); });
                    width = 300;
                },
                {
                    layerId = M1;
                    name = Regular;
                    shapes = ({ nodes = ((20,0,l),(125,250,l),(230,0,l)); });
                    width = 250;
                },
            );
        });
        }
        """,
        encoding="utf-8",
    )

    assert parse_glyphs(path).glyphs["s"].advance_width == 250
    assert parse_glyphs(path, master="Regular").glyphs["s"].advance_width == 250
    assert parse_glyphs(path, master="M1").glyphs["s"].advance_width == 250
    assert parse_glyphs(path, master="Bold").glyphs["s"].advance_width == 300
    assert parse_glyphs(path, master="M2").glyphs["s"].advance_width == 300


def test_authored_glyph_metadata_and_missing_defaults(tmp_path: Path) -> None:
    path = tmp_path / "glyph-metadata.glyphs"
    path.write_text(
        """
        {
        fontMaster = ({ id = M1; name = Regular; });
        glyphs = (
            {
                category = Letter;
                glyphname = A;
                layers = ({ layerId = M1; width = 600; });
                script = latin;
                subCategory = Uppercase;
                unicode = 65;
            },
            {
                category = "";
                glyphname = alternate;
                layers = ({ layerId = M1; width = 600; });
                script = "";
                subCategory = "";
            },
            {
                glyphname = unclassified;
                layers = ({ layerId = M1; width = 600; });
            },
        );
        }
        """,
        encoding="utf-8",
    )

    font = parse_glyphs(path)
    classified = font.glyphs["A"]
    assert classified.category == "Letter"
    assert classified.subcategory == "Uppercase"
    assert classified.script == "latin"

    for glyph_name in ("alternate", "unclassified"):
        glyph = font.glyphs[glyph_name]
        assert glyph.category is None
        assert glyph.subcategory is None
        assert glyph.script is None


def test_list_masters_exposes_ids_and_style_names(tmp_path: Path) -> None:
    path = tmp_path / "masters.glyphs"
    path.write_text(
        """
        {
        fontMaster = (
            { id = M1; name = Regular; },
            { id = M2; name = "Display Bold"; },
        );
        glyphs = ();
        }
        """,
        encoding="utf-8",
    )

    expected = [
        MasterInfo(master_id="M1", name="Regular"),
        MasterInfo(master_id="M2", name="Display Bold"),
    ]
    assert list_masters(path) == expected
    assert list_font_masters(path) == expected


def test_list_masters_defaults_missing_fields_to_empty_strings(
    tmp_path: Path,
) -> None:
    path = tmp_path / "unnamed-master.glyphs"
    path.write_text(
        "{ fontMaster = ({}, { id = M2; }); glyphs = (); }",
        encoding="utf-8",
    )

    assert list_masters(path) == [
        MasterInfo(master_id="", name=""),
        MasterInfo(master_id="M2", name=""),
    ]


def test_decimal_and_hex_unicode_forms() -> None:
    modern = parse_glyphs(FIXTURES / "synthetic_v4.glyphs")
    assert modern.glyphs["decimalZ"].unicodes == [90]
    assert modern.cmap[90] == "decimalZ"
    assert modern.glyphs["hexA"].unicodes == [0x61]

    legacy = parse_glyphs(FIXTURES / "synthetic_v2.glyphs")
    assert legacy.glyphs["part"].unicodes == [0x5A]
    assert legacy.glyphs["made"].unicodes == [0x61, 0x62]
    assert legacy.glyphs["made"].contours[0].nodes[0].point == (10.0, 0.0)
    assert legacy.glyphs["part"].contours[0].nodes[-1].smooth is True


def test_kern_group_direction_and_full_keys() -> None:
    font = parse_glyphs(FIXTURES / "synthetic_v4.glyphs")

    assert font.kern_group_left["base"] == "@MMK_L_LeftSide"
    assert font.kern_group_right["base"] == "@MMK_R_RightSide"
    assert (
        font.kerning[
            ("@MMK_L_LeftSide", "@MMK_R_RightSide")
        ]
        == -80.0
    )


def test_metrics_index_alignment() -> None:
    """The font-level `metrics` array aligns by index with `metricValues`.

    The first entry carries no `type` and means ascender, which is the easy
    part to get wrong. The demo font reproduces that exact structure.
    """
    font = parse_glyphs(DEMO)

    assert font.metrics.ascender == 750
    assert font.metrics.cap_height == 700
    assert font.metrics.x_height == 460
    assert font.metrics.descender == -200
    assert font.metrics.baseline == 0
    assert font.cmap[65] == "A"   # decimal unicode, not hex


def test_sparse_metric_values_leave_missing_metrics_undefined(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sparse-metrics.glyphs"
    path.write_text(
        """
        {
        .formatVersion = 3;
        fontMaster = ({
            id = M1;
            metricValues = ({ pos = 800; }, {}, { pos = ""; });
        });
        glyphs = ();
        metrics = (
            {},
            { type = "cap height"; },
            { type = "x-height"; },
        );
        }
        """,
        encoding="utf-8",
    )

    metrics = parse_glyphs(path).metrics
    assert metrics.ascender == 800.0
    assert metrics.cap_height is None
    assert metrics.x_height is None


def test_empty_legacy_metric_fields_remain_undefined(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy-missing-metrics.glyphs"
    path.write_text(
        """
        {
        .formatVersion = 2;
        fontMaster = ({
            id = M1;
            ascender = 800;
            capHeight = "";
            xHeight = "";
        });
        glyphs = ();
        }
        """,
        encoding="utf-8",
    )

    metrics = parse_glyphs(path).metrics
    assert metrics.ascender == 800.0
    assert metrics.cap_height is None
    assert metrics.x_height is None


def test_deep_component_chain_is_not_silently_truncated(
    tmp_path: Path,
) -> None:
    records = []
    for index in range(41):
        shape = (
            "{ nodes = ((0,0,l)); }"
            if index == 40
            else "{{ ref = g{}; pos = (1,0); }}".format(index + 1)
        )
        records.append(
            """
            {
                glyphname = g%s;
                layers = ({
                    layerId = M1;
                    shapes = (%s);
                    width = 100;
                });
            }
            """
            % (index, shape)
        )
    path = tmp_path / "deep-components.glyphs"
    path.write_text(
        """
        {
        fontMaster = ({ id = M1; });
        glyphs = (%s);
        }
        """
        % ",".join(records),
        encoding="utf-8",
    )

    contour = parse_glyphs(path).glyphs["g0"].contours[0]
    assert contour.nodes[0].point == (40.0, 0.0)


def test_list_layers_finds_named_open_path_layer() -> None:
    layers = list_layers(DEMO, "a")
    skeleton = next(layer for layer in layers if layer.name == "Skeleton v1")

    assert skeleton.associated_master_id == "m01"
    assert skeleton.contour_count == 2
    assert skeleton.has_open_contours is True


def test_compact_node_types_are_parsed_as_letter_plus_flags() -> None:
    """Regression: a Glyphs save introduced 'ct' and 'lt' and broke parsing.

    The compact node type is a segment letter followed by flag letters. Matching
    whole strings meant a newer Glyphs build could make an otherwise readable
    file unparseable, so the letters are now parsed structurally. Both 's'
    (smooth) and 't' (tangent) mark a tangent-continuous node.
    """
    source = """
    {
    .formatVersion = 3;
    fontMaster = ({ id = M1; });
    glyphs = (
    {
    glyphname = test;
    layers = (
    {
    layerId = M1;
    shapes = (
    {
    closed = 1;
    nodes = (
    (0,0,l),
    (100,0,lt),
    (200,0,o),
    (300,0,o),
    (400,0,ct),
    (500,0,cs),
    (600,0,o),
    (700,0,o)
    );
    }
    );
    width = 800;
    }
    );
    unicode = 65;
    }
    );
    unitsPerEm = 1000;
    }
    """
    path = FIXTURES.parent / "_compact_node_types.glyphs"
    path.write_text(source)
    try:
        font = parse_glyphs(path)
    finally:
        path.unlink()

    nodes = [n for c in font.glyphs["test"].contours for n in c.nodes]
    by_x = {int(n.point[0]): n for n in nodes}
    assert by_x[100].smooth is True, "'lt' must mark a tangent node"
    assert by_x[400].smooth is True, "'ct' must mark a tangent node"
    assert by_x[500].smooth is True, "'cs' must still mark a smooth node"
    assert by_x[0].smooth is False, "plain 'l' stays a corner"
    assert by_x[100].type == "line"
    assert by_x[400].type == "curve"


def test_unknown_future_node_flags_do_not_break_parsing() -> None:
    """An unrecognised flag letter must degrade, not raise."""
    source = """
    {
    .formatVersion = 3;
    fontMaster = ({ id = M1; });
    glyphs = ({ glyphname = test; layers = ({ layerId = M1; shapes = (
    { closed = 1; nodes = ((0,0,l),(100,0,lz),(200,0,l)); }
    ); width = 300; }); unicode = 65; });
    unitsPerEm = 1000;
    }
    """
    path = FIXTURES.parent / "_future_node_flag.glyphs"
    path.write_text(source)
    try:
        font = parse_glyphs(path)
    finally:
        path.unlink()
    nodes = [n for c in font.glyphs["test"].contours for n in c.nodes]
    assert len(nodes) == 3
    assert all(n.type == "line" for n in nodes)
