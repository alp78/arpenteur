"""Settings changed from the application rather than from a file.

The territory, the segments, the signal brief and the campaign brief decide
what every stage does. Keeping them in `arpenteur.toml` made the most
consequential decisions in the tool the ones you had to leave the tool to make,
and hid them: a survey containing no foundations at all, because `0110` was
absent from a list in a file, is not a discoverable state. The canton was in
scope, the sweep ran, and nothing anywhere said an entire class of entity had
never been asked for.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.kernel.db import Connection, Row
from arpenteur.config import load_settings
from arpenteur.console.app import create_app

TOML = """
[survey]
cantons = ["GE"]
legal_forms = ["0107"]

[segments]
sme-owner = "A small owner-run business."

[enrich]
signal_brief = "Ageing website, manual processes."

[zefix]
base_url = "https://example.test/ZefixPublicREST"
rate_limit = 2.0
max_retries = 0
contact = "tests"

[llm]
provider = "anthropic"
classify_model = "test"
draft_model = "test"
batch_size = 20
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "arpenteur.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "campaign.md").write_text("# Campaign\n\nOriginal.\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def conn(root: Path, db_path: str) -> Connection:
    return db.open_db(db_path)


@pytest.fixture
def client(root: Path, conn) -> TestClient:
    return TestClient(create_app(load_settings(root)))


# --- the overlay --------------------------------------------------------------


def test_the_file_is_the_default_when_nothing_was_changed(root) -> None:
    settings = load_settings(root)
    assert settings.territory.cantons == ("GE",)
    assert settings.territory.legal_forms == ("0107",)


def test_a_saved_setting_wins_over_the_file(root, conn) -> None:
    db.save_setting(conn, "legal_forms", ["0107", "0110"])
    conn.commit()
    assert load_settings(root).territory.legal_forms == ("0107", "0110")


def test_the_file_keeps_its_comments(root, conn) -> None:
    """Rewriting arpenteur.toml from a web form was the obvious alternative
    and would have destroyed the explanation that makes it readable."""
    before = (root / "arpenteur.toml").read_text(encoding="utf-8")
    db.save_setting(conn, "cantons", ["VD", "GE"])
    conn.commit()
    load_settings(root)
    assert (root / "arpenteur.toml").read_text(encoding="utf-8") == before
    assert tomllib.loads(before)["survey"]["cantons"] == ["GE"]


def test_a_region_in_the_file_does_not_narrow_a_chosen_canton_list(
    root, conn
) -> None:
    """A canton list picked in the console is the whole answer. Leaving the
    file's region to apply afterwards would silently discard the choice."""
    (root / "arpenteur.toml").write_text(
        TOML.replace('cantons = ["GE"]', 'region = "romandie"\n[regions]\nromandie = ["GE"]\n[survey2]'),
        encoding="utf-8",
    )
    (root / "arpenteur.toml").write_text(
        '[survey]\nregion = "romandie"\nlegal_forms = ["0107"]\n'
        '[regions]\nromandie = ["GE", "VD"]\n'
        '[segments]\nsme-owner = "x"\n'
        '[zefix]\nbase_url = ""\nrate_limit = 2.0\nmax_retries = 0\ncontact = "t"\n'
        '[llm]\nprovider = "anthropic"\nclassify_model = "t"\ndraft_model = "t"\nbatch_size = 20\n',
        encoding="utf-8",
    )
    assert set(load_settings(root).territory.cantons) == {"GE", "VD"}

    db.save_setting(conn, "cantons", ["ZH"])
    conn.commit()
    assert load_settings(root).territory.cantons == ("ZH",)


def test_an_unreachable_database_is_not_a_startup_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """Configuration must never be the reason the tool will not start.

    Under SQLite this meant a file that was not there. It now means a server
    that is not running, which is the ordinary state of a laptop that has not
    started the container, and the shipped defaults have to carry it.
    """
    (tmp_path / "arpenteur.toml").write_text(TOML, encoding="utf-8")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://nobody@127.0.0.1:1/nowhere"
    )
    assert load_settings(tmp_path).territory.cantons == ("GE",)


def test_only_known_settings_can_be_saved(conn) -> None:
    """A typo should be an error, not a setting that silently does nothing."""
    with pytest.raises(ValueError, match="not an operator setting"):
        db.save_setting(conn, "db_path", "/tmp/elsewhere")


# --- the territory, which is the one that bit us ------------------------------


def test_the_legal_form_picker_is_the_registers_own_catalogue(client, conn) -> None:
    """A code typed from memory is how 0110 came to be missing."""
    db.save_catalogue(
        conn,
        [{"uid": "0107", "label": "Société à responsabilité limitée"},
         {"uid": "0110", "label": "Fondation"}],
    )
    body = client.get("/settings").text
    assert "Fondation" in body and 'value="0110"' in body


def test_adding_a_legal_form_takes_effect_without_touching_a_file(
    client, root
) -> None:
    """The whole point. A survey with no foundations in it was invisible: the
    canton was in scope, the sweep ran, and nothing said an entire class of
    entity had never been asked for."""
    assert "0110" not in load_settings(root).territory.legal_forms
    client.post(
        "/settings/territory",
        data={"cantons": ["GE"], "legal_forms": ["0107", "0110"]},
    )
    assert "0110" in load_settings(root).territory.legal_forms


def test_saving_nothing_is_refused_rather_than_widening_everything(
    client, root
) -> None:
    """An empty canton list means "no restriction" everywhere else in this
    codebase, so an untouched form would sweep Switzerland."""
    reply = client.post("/settings/territory", data={}, follow_redirects=True)
    assert "at least one canton" in reply.text
    assert load_settings(root).territory.cantons == ("GE",)


def test_the_territory_records_when_it_changed(client, conn) -> None:
    """Widening it changes what every later stage will spend money on, and
    that is a question asked after the bill."""
    client.post(
        "/settings/territory", data={"cantons": ["GE"], "legal_forms": ["0107"]}
    )
    keys = {row["key"]: row["updated_by"] for row in db.setting_history(conn)}
    assert keys.get("cantons") == "console"


# --- segments and the briefs --------------------------------------------------


def test_segments_are_edited_as_a_set(client, root) -> None:
    client.post(
        "/settings/segments",
        data={"seg_name": ["sme-owner", "foundation"],
              "seg_text": ["A small owner-run business.", "A grant-making body."]},
    )
    assert set(load_settings(root).segments) == {"sme-owner", "foundation"}


def test_a_segment_can_be_dropped(client, root) -> None:
    client.post(
        "/settings/segments",
        data={"seg_name": ["sme-owner", "foundation"],
              "seg_text": ["x", "y"], "seg_drop": ["foundation"]},
    )
    assert set(load_settings(root).segments) == {"sme-owner"}


def test_the_last_segment_cannot_be_dropped(client, root) -> None:
    """A survey with no segments cannot be sliced by anything."""
    reply = client.post(
        "/settings/segments", data={"seg_drop": ["sme-owner"]}, follow_redirects=True
    )
    assert "at least one" in reply.text
    assert load_settings(root).segments


def test_the_signal_brief_is_collapsed_to_one_line(client, root) -> None:
    """It goes into a JSON Schema description, where newlines are noise."""
    client.post("/settings/signal", data={"signal_brief": "One thing.\n\nAnother."})
    assert load_settings(root).signal_brief == "One thing. Another."


def test_the_campaign_brief_stays_a_versioned_file(client, root) -> None:
    """When a run produces surprising scores the first question is what
    changed in the brief, and a diff answers that where a column does not."""
    client.post("/settings/campaign", data={"campaign": "# Campaign\n\nRewritten.\n"})
    assert "Rewritten" in (root / "campaign.md").read_text(encoding="utf-8")


def test_an_empty_campaign_brief_is_refused(client, root) -> None:
    reply = client.post(
        "/settings/campaign", data={"campaign": "   "}, follow_redirects=True
    )
    assert "built on" in reply.text
    assert "Original" in (root / "campaign.md").read_text(encoding="utf-8")


# --- the sweep, which was the last command-line-only stage ---------------------


def test_the_sweep_is_offered_in_the_console(client) -> None:
    body = client.get("/pipeline").text
    assert "/run/source" in body and "Sweep the register" in body


def test_the_sweep_form_maps_onto_the_stage(client) -> None:
    """The control reads better as `canton` and `names only`; the stage takes
    `cantons` and `hydrate`. The mapping belongs to the form, not the stage."""
    from arpenteur.console.runner import STAGES, coerce

    got = coerce(
        STAGES["source"],
        {"canton": ["VD", "GE"], "legal_form": ["0110"], "no_hydrate": "1"},
    )
    assert got == {
        "cantons": ["VD", "GE"], "legal_forms": ["0110"],
        "hydrate": False, "resume": True,
    }


def test_selecting_no_canton_means_the_configured_territory(client) -> None:
    """Not "no cantons at all", which would sweep nothing and look broken."""
    from arpenteur.console.runner import STAGES, coerce

    assert "cantons" not in coerce(STAGES["source"], {"no_hydrate": "1"})


# --- the header is composed by the modules ------------------------------------


def test_every_link_in_the_header_leads_somewhere(client) -> None:
    """A header composed by the modules can name a route nobody wrote.

    The navigation used to be a hardcoded string in `render.py`, so a link and
    a route could drift apart in silence, and two of them had: quotes and
    invoices existed with no way to reach them, and every link that did exist
    was styled with an active state that was never applied.

    This lives here rather than in `test_console.py` because it needs the one
    fixture built from a real settings file. The console's own fixture is a
    stub with three attributes, which is enough for the pages those tests open
    and not enough for `/settings`, and a page nobody has a test for is a page
    nobody knows is reachable.
    """
    header = client.get("/").text.split("<header>")[1].split("</header>")[0]
    hrefs = re.findall(r'<a href="([^"]+)"', header)
    assert len(hrefs) >= 12
    for href in hrefs:
        assert client.get(href).status_code == 200, href


def test_turning_off_a_module_removes_its_pages_from_the_header(
    root: Path, conn
) -> None:
    """The check that the slot wiring is real rather than decorative.

    Dropping `sales` used to remove its tables and its stages and leave the
    header pointing at a schema that was no longer there.
    """
    settings = load_settings(root)
    settings = replace(settings, modules=("scout", "geo", "clients"))
    client = TestClient(create_app(settings))
    header = client.get("/").text.split("<header>")[1].split("</header>")[0]
    labels = re.findall(r'<a href="[^"]+"[^>]*>([^<]+)</a>', header)
    assert "Quotes" not in labels and "Invoices" not in labels
    assert "Reports" not in labels
    assert "Clients" in labels and "Survey" in labels and "Settings" in labels


# --- the furniture, over every page the console serves ------------------------


def _pages(client: TestClient) -> dict[str, str]:
    """Every page reachable from the header, plus the records under them."""
    header = client.get("/").text.split("<header>")[1].split("</header>")[0]
    out = {}
    for href in re.findall(r'<a href="([^"]+)"', header):
        out[href] = client.get(href).text
    return out


def test_every_table_says_what_it_is_and_scopes_its_headers(client) -> None:
    """A table of numbers with no title is a table somebody screenshots.

    Fourteen page modules each built their own, and they disagreed about all
    three of these: some had a caption, none had `scope`, and the empty case
    was a different colspan guess per site.
    """
    for url, body in _pages(client).items():
        for table in re.findall(r"<table[^>]*>.*?</table>", body, re.S):
            assert "<caption>" in table, f"{url}: a table with no caption"
            # `<th...>` and not `<thead>`, which the lazier pattern also matches.
            for header in re.findall(r"<th(?:\s[^>]*)?>", table):
                assert "scope=" in header, f"{url}: {header} has no scope"


def test_no_page_spells_a_colour_the_shell_already_names(client) -> None:
    """Thirty-nine hex literals across nine files could not say that a band is
    not green, it is good.

    Two exceptions, both deliberate. The printed document uses ink black and
    paper white against a physical page, not the screen palette. The map's
    layer colours are JSON a mapping library reads as data, not CSS.
    """
    from pathlib import Path as P

    console = P(__file__).resolve().parent.parent / "arpenteur" / "console"
    allowed = {"render.py", "money_page.py", "map_page.py"}
    for path in sorted(console.glob("*.py")):
        if path.name in allowed:
            continue
        for block in re.findall(r'STYLES\s*=\s*"""(.*?)"""', path.read_text(), re.S):
            spelled = re.findall(r"#[0-9a-fA-F]{3,8}\b", block)
            assert not spelled, (
                f"{path.name} spells {spelled} rather than using a token. "
                "If none of the tokens fits, add one in render.py."
            )
