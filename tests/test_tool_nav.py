from typedesignxray.process_page import process_page
from typedesignxray.specimen import specimen_page
from typedesignxray.tool_nav import TOOLS, tool_switcher
from typedesignxray.web import _preview_page


def test_shared_switcher_names_and_explains_all_three_tools() -> None:
    markup = tool_switcher("xray")

    assert len(TOOLS) == 3
    assert "X-Ray Blueprint" in markup
    assert "Font Specimen" in markup
    assert "Font Design Process" in markup
    assert "outlines, nodes, handles, spacing, and metrics" in markup
    assert "every designed symbol" in markup
    assert "skeleton layers to the active master" in markup


def test_each_tool_page_has_one_correct_active_tab() -> None:
    pages = {
        "xray": _preview_page(),
        "specimen": specimen_page(),
        "process": process_page(),
    }

    for active, page in pages.items():
        assert page.count('class="tool-tab') == 3
        assert page.count('aria-current="page"') == 1
        assert '__TOOL_SWITCHER__' not in page
        expected_href = dict((item[0], item[1]) for item in TOOLS)[active]
        assert 'class="tool-tab active" href="{}" aria-current="page"'.format(
            expected_href
        ) in page


def test_switcher_rejects_an_unknown_active_tool() -> None:
    try:
        tool_switcher("unknown")
    except ValueError as error:
        assert "unknown active tool" in str(error)
    else:
        raise AssertionError("unknown tool must be rejected")
