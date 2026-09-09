"""The furniture: what every page is now made of.

These are cheap tests over pure functions, which is the point of having pulled
the markup out of the pages. Four properties are worth more than the rest.

**Nothing user-supplied reaches the page unescaped.** Company names come from a
public register, notes and filenames are typed by hand, and a component that
escaped its label but not its hint would be a hole in exactly the place a
reviewer stops looking.

**A component handed nothing renders nothing.** A card called "contacts" with
no contacts in it has said something false about the record.

**The doubled-brace failure cannot come back.** A page's own rules used to
travel through a `str.format` call, so every brace had to be doubled and the
doubled braces reached the browser, and CSS error recovery threw away the whole
file. Two pages rendered unstyled and nothing said so. There is no format call
now, and this is what proves it.

**Every sortable offers three ways to move a row.** WCAG 2.2 SC 2.5.7 wants a
path that needs neither dragging nor a keyboard, so the buttons are not
optional. A test is how that survives the next person tidying the markup.
"""

from __future__ import annotations

import re

import pytest

from arpenteur.console import assets, render, ui
from arpenteur.kernel import modules

EVIL = '<script>alert("x")</script>'


# --- escaping -----------------------------------------------------------------


@pytest.mark.parametrize(
    "html",
    [
        ui.pill(EVIL),
        ui.chip(EVIL, href="/x", title=EVIL),
        ui.banner(EVIL),
        ui.empty(EVIL),
        ui.stat(EVIL, EVIL),
        ui.field(EVIL, "<input>", hint=EVIL),
        ui.text_field("q", EVIL, value=EVIL, placeholder=EVIL, hint=EVIL),
        ui.select_field("s", EVIL, [EVIL], chosen=EVIL, blank=EVIL, hint=EVIL),
        ui.checkbox("c", EVIL, hint=EVIL),
        ui.button(EVIL, hint=EVIL),
        ui.tabs([("/a", EVIL)], "/a"),
        ui.crumbs([("/a", EVIL), ("", EVIL)]),
        ui.table([EVIL], [], empty_text=EVIL, caption=EVIL),
        ui.dropzone("/upload", label=EVIL, hint=EVIL),
        ui.sortable([(1, "safe")], endpoint="/x", label=EVIL, frozen=EVIL),
        ui.confirm_button(EVIL, "/x", EVIL, hidden={"a": EVIL}),
    ],
)
def test_no_component_lets_markup_through(html: str) -> None:
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_an_id_from_a_row_cannot_break_out_of_its_attribute() -> None:
    html = ui.sortable([('1" onclick="x', "body")], endpoint="/x")
    assert 'onclick="x' not in html
    assert "&quot;" in html


# --- rendering nothing rather than an empty box -------------------------------


def test_a_card_with_nothing_in_it_is_not_drawn() -> None:
    assert ui.card("") == ""


def test_a_panel_with_nothing_in_it_is_not_drawn() -> None:
    assert ui.panel("Contacts", "") == ""


def test_a_panel_marked_always_is_drawn_empty() -> None:
    """Absent and none-yet are different facts, and some sections must say so."""
    assert "Fit" in ui.panel("Fit", "", always=True)


def test_an_empty_sortable_is_not_drawn() -> None:
    assert ui.sortable([], endpoint="/x") == ""


def test_a_banner_with_no_text_is_not_drawn() -> None:
    assert ui.banner("") == ""


# --- counting the Swiss way ---------------------------------------------------


def test_counts_are_grouped_with_apostrophes_like_the_francs_beside_them() -> None:
    assert ui.num(184939) == "184'939"
    assert ui.num(999) == "999"
    assert ui.num(1000) == "1'000"
    assert ui.num(-12345) == "-12'345"


def test_a_non_integer_count_is_escaped_rather_than_formatted() -> None:
    assert ui.num(EVIL) == ui.esc(EVIL)


# --- attributes ---------------------------------------------------------------


def test_an_empty_attribute_is_omitted_rather_than_rendered_blank() -> None:
    assert ui.attrs(title="", href=None, id=False) == ""


def test_a_boolean_attribute_is_rendered_bare() -> None:
    assert ui.attrs(disabled=True) == " disabled"


def test_underscores_become_hyphens_so_htmx_attributes_can_be_keywords() -> None:
    assert ui.attrs(hx_get="/x") == ' hx-get="/x"'
    assert ui.attrs(for_="a") == ' for="a"'


def test_classes_drops_the_falsy_parts_and_the_attribute_when_empty() -> None:
    assert ui.classes("card", False, "", None) == ' class="card"'
    assert ui.classes(None, "") == ""


# --- tables -------------------------------------------------------------------


def test_a_table_has_a_caption_and_scoped_headers() -> None:
    html = ui.table(["name", "net"], ["<tr><td>a</td><td>1</td></tr>"],
                    caption="Lines", numeric=["net"])
    assert "<caption>Lines</caption>" in html
    assert '<th scope="col">name</th>' in html
    assert 'class="n"' in html


def test_an_empty_table_says_so_across_the_full_width() -> None:
    html = ui.table(["a", "b", "c"], [], empty_text="Nothing yet.")
    assert 'colspan="3"' in html
    assert "Nothing yet." in html


# --- the three paths a row can be moved by ------------------------------------


def test_a_sortable_row_can_be_dragged_and_nudged_and_carried() -> None:
    html = ui.sortable([(1, "one"), (2, "two")], endpoint="/quote/1/reorder",
                       label="line")
    assert 'draggable="true"' in html               # pointer
    assert "data-sort-grip" in html                 # keyboard
    assert "data-sort-up" in html and "data-sort-down" in html   # single pointer
    assert 'data-sort-post="/quote/1/reorder"' in html


def test_the_ends_of_the_list_cannot_be_nudged_off_it() -> None:
    html = ui.sortable([(1, "one"), (2, "two")], endpoint="/x")
    first, second = html.split('data-sort-id="2"')
    assert "data-sort-up aria-label=\"Move this row up\" disabled" in first
    assert "data-sort-down aria-label=\"Move this row down\" disabled" in second


def test_a_frozen_list_offers_no_way_to_reorder_it_and_says_why() -> None:
    """A sent quote is a document somebody else holds a copy of."""
    html = ui.sortable([(1, "one"), (2, "two")], endpoint="/x",
                       frozen="This version was sent on 12.08.2026.")
    assert "data-sortable" not in html
    assert "data-sort-up" not in html
    assert 'draggable="true"' not in html
    assert "sent on 12.08.2026" in html


def test_a_sortable_announces_its_moves_to_a_screen_reader() -> None:
    html = ui.sortable([(1, "one")], endpoint="/x")
    assert 'aria-live="polite"' in html


# --- dropping files -----------------------------------------------------------


def test_a_drop_zone_also_has_a_control_for_people_who_cannot_drag() -> None:
    html = ui.dropzone("/dossier/1/documents")
    assert 'type="file"' in html
    assert "<button" in html
    assert "multipart/form-data" in html


def test_a_drop_zone_accepts_more_than_one_file() -> None:
    assert " multiple" in ui.dropzone("/x")


# --- navigation pieces --------------------------------------------------------


def test_exactly_one_tab_is_marked_as_where_you_are() -> None:
    html = ui.tabs([("/quotes", "Quotes"), ("/invoices", "Invoices")], "/invoices")
    assert html.count('aria-current="page"') == 1
    assert '<a href="/invoices" class="on" aria-current="page">' in html


def test_the_last_crumb_is_the_page_itself_and_is_not_a_link() -> None:
    html = ui.crumbs([("/clients", "Clients"), ("", "Bois-Gentil")])
    assert '<a href="/clients">Clients</a>' in html
    assert "<b>Bois-Gentil</b>" in html


# --- the shell ----------------------------------------------------------------


def test_a_pages_own_rules_arrive_intact_with_no_brace_doubled() -> None:
    """The failure that rendered two whole pages unstyled, in one assertion.

    `.crumbs {{ ... }}` reaching the browser is a selector followed by an
    invalid declaration, and CSS error recovery discards the rest of the file.
    """
    css = ".crumbs { color:red; }\n@media (max-width:700px) { .x { display:none; } }"
    out = render.page("t", "<p>b</p>", css=css)
    assert css in out
    assert "{{" not in out
    assert "}}" not in out


def test_a_pages_rules_are_put_in_the_layer_that_outranks_the_components() -> None:
    out = render.page("t", "", css=".card { border:0; }")
    assert "@layer tokens, base, components, page;" in out
    assert "@layer page {\n.card { border:0; }\n}" in out


def test_a_page_with_no_rules_of_its_own_declares_no_empty_layer() -> None:
    assert "@layer page" not in render.page("t", "<p>b</p>")


def test_the_shell_marks_the_section_you_are_on() -> None:
    chrome = render.Chrome.of(modules.load(modules.DEFAULT), "Vaud", "/board")
    out = render.page("t", "", chrome)
    assert '<a href="/board" aria-current="page">Board</a>' in out
    assert out.count('aria-current="page"') == 1


# --- the header is the modules, not a list in a file --------------------------


def test_the_header_lists_what_this_deployment_actually_has() -> None:
    chrome = render.Chrome.of(modules.load(modules.DEFAULT))
    labels = [item.label for item in chrome.nav]
    assert labels == [
        "Clients", "Programmes", "Mail", "Calendar", "Quotes", "Invoices",
        "Survey", "Board", "People", "Map", "Reports",
        "Operations", "History", "Settings",
    ]


def test_turning_a_module_off_takes_its_links_with_it() -> None:
    """The check that the slot wiring is real rather than decorative.

    Dropping `sales` used to remove its tables and its stages and leave a
    header pointing at a schema that was no longer there.
    """
    without = render.Chrome.of(modules.load(("scout", "geo", "clients")))
    labels = [item.label for item in without.nav]
    assert "Quotes" not in labels and "Invoices" not in labels
    assert "Reports" not in labels          # analytics is off too
    assert "Clients" in labels and "Survey" in labels
    assert "Settings" in labels             # the console's own pages stay


def test_the_console_own_pages_belong_to_no_module_and_always_appear() -> None:
    bare = render.Chrome.of(modules.load(("scout",)))
    assert [n.label for n in bare.nav if n.group == "system"] == [
        "Operations", "History", "Settings",
    ]


def test_a_rule_is_drawn_between_groups_and_not_at_the_ends() -> None:
    out = render.page("t", "", render.Chrome.of(modules.load(modules.DEFAULT)))
    header = out.split("<header>")[1].split("</header>")[0]
    assert header.count('class="sep"') == 2   # work | survey | system
    assert not header.split("<nav")[1].lstrip().startswith('<span class="sep"')


def test_every_navigation_entry_names_a_distinct_place() -> None:
    nav = render.Chrome.of(modules.load(modules.DEFAULT)).nav
    hrefs = [item.href for item in nav]
    assert len(hrefs) == len(set(hrefs))
    assert all(href.startswith("/") for href in hrefs)


def test_the_shell_has_landmarks_and_a_way_past_the_navigation() -> None:
    out = render.page("t", "<p>b</p>")
    assert '<a class="skip" href="#main">' in out
    assert '<main id="main">' in out
    assert '<nav aria-label="Sections">' in out


def test_htmx_is_pinned_and_checked_against_a_hash() -> None:
    """A floating CDN tag is a third party able to change what runs on a page
    holding a client's file."""
    out = render.page("t", "")
    assert "htmx.org@2.0.10" in out
    assert "integrity=" in out
    assert "unpkg.com" not in out


def test_the_script_url_carries_the_hash_of_the_script_it_serves() -> None:
    assert assets.CONSOLE_JS_HASH in assets.CONSOLE_JS_URL
    assert assets.CONSOLE_JS_URL in render.page("t", "")


def test_the_title_of_a_page_is_escaped() -> None:
    assert "<script>" not in render.page(EVIL, "")


# --- the dark scheme --------------------------------------------------------


def _tokens(block: str) -> set[str]:
    return set(re.findall(r"(--[a-z0-9-]+)\s*:", block))


def test_the_dark_scheme_answers_every_colour_the_light_one_names() -> None:
    """A token the dark block forgets is an element nobody can see.

    It would not fail, and it would not look broken in the light: it would be
    one card, on one page, that stayed white on a black ground for whoever has
    their machine set that way, which is nobody who is reading this test.
    """
    light, dark = render.STYLESHEET.split("@media (prefers-color-scheme: dark)")
    light_tokens = _tokens(light.split(":root {")[1].split("\n  }")[0])
    dark_tokens = _tokens(dark)

    # The scales are shared on purpose: spacing does not change when the lights
    # go out, and neither does the typeface.
    shared = {"--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--r", "--r-pill",
              "--t-xs", "--t-sm", "--t-lg", "--ui"}
    missing = light_tokens - dark_tokens - shared
    assert not missing, f"the dark scheme does not answer {sorted(missing)}"


def test_the_dark_scheme_changes_the_paper_rather_than_only_the_text() -> None:
    dark = render.STYLESHEET.split("@media (prefers-color-scheme: dark)")[1]
    assert "--paper:#1a1815" in dark
    assert "--ink:#ece7de" in dark


def test_a_table_can_be_scrolled_from_a_keyboard() -> None:
    """A seven-column table on a phone either pushes the page sideways or has
    three columns nobody can reach."""
    html = ui.table(["a", "b"], ["<tr><td>1</td><td>2</td></tr>"])
    assert '<div class="scroller" tabindex="0">' in html
