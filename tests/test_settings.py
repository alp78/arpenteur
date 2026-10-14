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

import re
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.config import load_settings
from arpenteur.console.app import create_app

TOML = """
[survey]
cantons = ["GE"]
legal_forms = ["0107"]

[segments]
sme-owner = "A small owner-run business."

[sectors]
hospitality = "Restaurants, cafés and hotels."
construction = "Building and civil works."

[enrich]
signal_brief = "Ageing website, manual processes."

[zefix]
base_url = "https://example.test/ZefixPublicREST"
rate_limit = 2.0
max_retries = 0
contact = "tests"

[llm]
provider = "anthropic"
classify_model = "claude-test"
draft_model = "claude-test"
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


def test_the_file_is_the_default_when_nothing_was_changed(root, conn) -> None:
    """`conn` is not used here and is not decoration.

    `load_settings` reads the operator overlay out of whatever `DATABASE_URL`
    names, and the fixture chain is what points that at the test database.
    Without it this test read the live one, so it passed only when some earlier
    test in the same process had already set the variable: green in the full
    suite, red on its own, and red for a reason that had nothing to do with
    what it was checking.
    """
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
        '[llm]\nprovider = "anthropic"\nclassify_model = "claude-t"\ndraft_model = "claude-t"\nbatch_size = 20\n',
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


def test_sectors_are_edited_as_a_set(client, root) -> None:
    client.post(
        "/settings/sectors",
        data={"sec_name": ["hospitality", "agriculture"],
              "sec_text": ["Restaurants and hotels.", "Farming and viticulture."]},
    )
    assert set(load_settings(root).sectors) == {"hospitality", "agriculture"}


def test_a_sector_can_be_dropped(client, root) -> None:
    client.post(
        "/settings/sectors",
        data={"sec_name": ["hospitality", "construction"],
              "sec_text": ["x", "y"], "sec_drop": ["construction"]},
    )
    assert set(load_settings(root).sectors) == {"hospitality"}


def test_the_last_sector_may_be_dropped(client, root) -> None:
    """Unlike segments, and the difference is what each absence means.

    No segments is a survey that cannot be sliced at all. No sectors is the
    survey this tool was before the vocabulary existed: the classify stage
    asks for the segments alone and everything else still works.
    """
    reply = client.post(
        "/settings/sectors", data={"sec_drop": ["hospitality", "construction"]},
        follow_redirects=True,
    )
    assert "at least one" not in reply.text
    assert load_settings(root).sectors == {}


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
    assert "Clients" in labels and "Companies" in labels and "Settings" in labels


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


def test_no_label_in_the_console_names_nothing(client) -> None:
    """A word above a field is not a label unless it says which field.

    Thirty-three of them across four filter bars named nothing: adjacent to
    their control, with no `for` and not wrapping it. A screen reader reads
    those as stray words, and clicking one does not focus the field. They are
    all built from the components now, which bind the two.
    """
    for url, body in _pages(client).items():
        labels = re.findall(r"<label(?:\s[^>]*)?>", body)
        unbound = [tag for tag in labels if "for=" not in tag and "class=" not in tag]
        wrapping = len(re.findall(
            r"<label(?:\s[^>]*)?>(?:(?!</label>).)*?<(?:input|select|textarea)",
            body, re.S,
        ))
        assert len(unbound) - wrapping <= 0, (
            f"{url}: {len(unbound) - wrapping} label(s) naming no control"
        )


# --- the environment names the code reads and the ones it cites ---------------

_ENV_NAME = re.compile(
    r"\b(?:MAIL|SMTP|ZEFIX|ANTHROPIC|DATABASE|INFOMANIAK|POSTGRES)_[A-Z_]+\b"
)
_READ = re.compile(r'(?:_env\(|os\.environ(?:\.get)?\(|os\.environ\[)\s*([^)\]]*)')
_PACKAGE = Path(__file__).resolve().parents[1] / "arpenteur"


def _names_read() -> set[str]:
    """Every environment name a `_env(...)` or `os.environ` call reads."""
    found: set[str] = set()
    for file in _PACKAGE.rglob("*.py"):
        for call in _READ.findall(file.read_text(encoding="utf-8")):
            found.update(_ENV_NAME.findall(call))
    return found


def _names_cited() -> dict[str, list[str]]:
    """Every environment name a string in any source file mentions, with where.

    Strings only, docstrings included: `smtplib.SMTP_SSL` is code, not advice
    to a user, and the regex alone cannot tell the two apart."""
    import ast

    cited: dict[str, list[str]] = {}
    for file in _PACKAGE.rglob("*.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for name in _ENV_NAME.findall(node.value):
                    cited.setdefault(name, []).append(f"{file.name}:{node.lineno}")
    return cited


def test_an_error_message_never_names_an_environment_variable_nothing_reads() -> None:
    """A message that says "set MAIL_IMAP_USER" sends the user to edit a name
    the code never looks at, which is how two of them stayed wrong for weeks."""
    read = _names_read()
    strays = {name: where for name, where in _names_cited().items() if name not in read}
    assert not strays, f"named but never read: {strays}"


def test_the_environment_template_lists_exactly_what_the_code_reads() -> None:
    example = _PACKAGE.parent / ".env.example"
    listed = set(re.findall(r"^([A-Z_]+)=", example.read_text(encoding="utf-8"), re.M))
    read = _names_read()
    # POSTGRES_PASSWORD is read by docker compose, not by Python.
    assert read <= listed, f"read but not in .env.example: {sorted(read - listed)}"
    assert listed - {"POSTGRES_PASSWORD"} <= read, (
        f"in .env.example but read by nothing: {sorted(listed - read)}"
    )


def test_a_territory_saved_here_is_the_one_the_next_run_sweeps(client, root) -> None:
    """The console loaded its settings once at startup and the runner kept that
    object, so a territory narrowed here still swept the old one until a
    restart. Saving rebinds both."""
    app = client.app
    assert set(app.state.runner.settings.cantons) != {"VD", "FR"}
    client.post("/settings/territory",
                data={"cantons": ["VD", "FR"], "legal_forms": ["0107"]})
    assert set(app.state.runner.settings.cantons) == {"VD", "FR"}
    assert set(app.state.settings.cantons) == {"VD", "FR"}


# --- whole configuration tables in the overlay -----------------------------------


def test_every_overlay_section_is_settable_and_lists_its_keys() -> None:
    from arpenteur import config as arpenteur_config
    from arpenteur.modules.scout import store as scout_store

    assert set(arpenteur_config.OVERLAY_SECTIONS) <= set(scout_store.SETTABLE)
    assert set(arpenteur_config.SECTION_KEYS) == set(arpenteur_config.OVERLAY_SECTIONS)
    for section, keys in arpenteur_config.SECTION_KEYS.items():
        assert keys and len(set(keys)) == len(keys), section


def test_a_section_saved_in_the_database_is_merged_over_the_file(tmp_path, conn) -> None:
    """One key saved on the page; the file's other keys of that table stay."""
    (tmp_path / "arpenteur.toml").write_text(
        TOML + '\n[issuer]\nname = "File Name"\npayment_days = 45\n'
        '\n[smtp]\nper_run = 7\nsender_name = "File Sender"\n',
        encoding="utf-8",
    )
    db.save_setting(conn, "issuer", {"name": "Console Name", "town": "Vevey"})
    db.save_setting(conn, "smtp", {"per_run": 3})
    conn.commit()
    loaded = load_settings(tmp_path)
    assert loaded.issuer.name == "Console Name"
    assert loaded.issuer.town == "Vevey"
    assert loaded.issuer.payment_days == 45
    assert loaded.smtp.per_run == 3 and loaded.smtp.sender_name == "File Sender"


def test_the_message_brief_is_a_setting(tmp_path, conn) -> None:
    from arpenteur.config import _enrol

    assert _enrol({}).brief == ""
    assert _enrol({"brief": " limen.md "}).brief == "limen.md"
    (tmp_path / "arpenteur.toml").write_text(TOML, encoding="utf-8")
    db.save_setting(conn, "enrol", {"brief": "brief.md", "programme": "pilot"})
    conn.commit()
    loaded = load_settings(tmp_path)
    assert loaded.enrol.brief == "brief.md" and loaded.enrol.programme == "PILOT"


def test_the_scoring_table_and_the_catalogue_can_be_held_in_the_database(tmp_path, conn) -> None:
    (tmp_path / "arpenteur.toml").write_text(TOML, encoding="utf-8")
    db.save_setting(conn, "scoring", {
        "name": "second pass", "note": "need first",
        "weights": {"icp_fit": 0.2, "need": 0.4, "timing": 0.1, "value": 0.1,
                    "reach": 0.1, "relationship": 0.1},
    })
    db.save_setting(conn, "rates", {
        "service": [{"code": "day", "name": "Day", "unit": "day", "low": 800, "high": 800}],
        "card_note": {"text": "Travel is not billed."},
    })
    conn.commit()
    loaded = load_settings(tmp_path)
    assert loaded.weight_set_name == "second pass" and loaded.weights["need"] == "0.4"
    assert [e.code for e in loaded.catalogue] == ["day"]
    assert loaded.catalogue[0].low == "800" and loaded.card_note == "Travel is not billed."


def test_the_model_ids_default_to_the_current_family(tmp_path, conn) -> None:
    (tmp_path / "arpenteur.toml").write_text(TOML, encoding="utf-8")
    loaded = load_settings(tmp_path)
    assert loaded.llm.classify_model == "claude-test"      # named in the file
    assert loaded.llm.enrich_model == "claude-sonnet-5-5"  # not named: the family
    assert loaded.llm.advise_model == "claude-opus-5-5"
    assert loaded.llm.auth_mode == "subscription"


# --- the configuration tables, saved from the page ---------------------------------


def _location(reply) -> str:
    from urllib.parse import unquote

    return unquote(reply.headers["location"])


ISSUER_FORM = {
    "name": "Nadia Example", "street": "Rue du Lac", "building": "4",
    "postcode": "1800", "town": "Vevey", "country": "ch",
    "iban": "CH93 0076 2011 6238 5295 7", "vat_number": "", "payment_terms": "Net 30.",
    "payment_days": "30",
}


def test_an_identity_saved_on_the_page_is_what_the_next_invoice_reads(client, root) -> None:
    reply = client.post("/settings/issuer", data=ISSUER_FORM, follow_redirects=False)
    assert _location(reply) == "/settings?note=Identity saved."
    loaded = load_settings(root)
    assert loaded.issuer.name == "Nadia Example" and loaded.issuer.town == "Vevey"
    assert loaded.issuer.country == "CH" and loaded.issuer.payment_days == 30
    assert loaded.issuer.can_bill
    assert client.app.state.settings.issuer.name == "Nadia Example", "live, no restart"
    page = client.get("/settings").text
    assert 'value="Nadia Example"' in page


def test_a_bad_iban_is_refused_and_nothing_is_written(client, root, conn) -> None:
    before = (root / "arpenteur.toml").read_text(encoding="utf-8")
    reply = client.post("/settings/issuer", data={**ISSUER_FORM, "iban": "CH00 1234"},
                        follow_redirects=False)
    assert "?error=" in reply.headers["location"] and "not a valid IBAN" in _location(reply)
    assert (root / "arpenteur.toml").read_text(encoding="utf-8") == before
    assert "issuer" not in db.settings_overlay(conn)
    nameless = client.post("/settings/issuer", data={**ISSUER_FORM, "name": " "},
                           follow_redirects=False)
    assert "Give the name that bills" in _location(nameless)
    days = client.post("/settings/issuer", data={**ISSUER_FORM, "payment_days": "soon"},
                       follow_redirects=False)
    assert "whole number" in _location(days)


def test_a_saved_sending_limit_reaches_the_runner_without_a_restart(client, root) -> None:
    reply = client.post("/settings/delivery", data={
        "sender": "nadia@example.ch", "sender_name": "Nadia", "reply_to": "",
        "security": "ssl", "sent_folder": "", "test_to": "", "per_run": "7",
        "daily_quota": "40", "pause_seconds": "15", "signature_image": "",
    }, follow_redirects=False)
    assert _location(reply) == "/settings?note=Sending saved."
    assert client.app.state.runner.settings.smtp.per_run == 7
    assert client.app.state.settings.smtp.security == "ssl"
    assert load_settings(root).smtp.daily_quota == 40
    out_of_range = client.post("/settings/delivery", data={"per_run": "0"}, follow_redirects=False)
    assert "between 1 and 1000" in _location(out_of_range)
    assert client.app.state.settings.smtp.per_run == 7, "a refused save changes nothing"


def test_a_field_the_environment_sets_is_shown_disabled_and_kept(client, root, monkeypatch, conn) -> None:
    db.save_setting(conn, "smtp", {"sender": "from@database.ch", "per_run": 5})
    conn.commit()
    monkeypatch.setenv("SMTP_SENDER", "from@environment.ch")
    page = client.get("/settings").text
    assert 'value="from@environment.ch" disabled' in page
    assert "Set by SMTP_SENDER in the environment" in page
    # The disabled field is not posted; what the database held for it stays.
    client.post("/settings/delivery", data={"sender_name": "Nadia", "per_run": "9"},
                follow_redirects=False)
    held = db.settings_overlay(conn)["smtp"]
    assert held["sender"] == "from@database.ch" and held["per_run"] == 9
    assert load_settings(root).smtp.sender == "from@environment.ch"


def test_secrets_are_listed_as_set_or_not_and_never_shown(client, monkeypatch) -> None:
    monkeypatch.setenv("ZEFIX_PASSWORD", "hunter2-very-secret")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    page = client.get("/settings").text
    assert "hunter2" not in page
    secrets = page.split('id="secrets"')[1].split("</section>")[0]
    assert "<code>ZEFIX_PASSWORD</code><span class='set'>set</span>" in secrets
    assert "<code>ANTHROPIC_API_KEY</code><span class='unset'>not set</span>" in secrets
    assert "POSTGRES_PASSWORD" not in secrets


def test_the_contact_and_the_own_domains_are_saved(client, root, monkeypatch) -> None:
    # An earlier test may have read the real .env into the process, and the
    # environment's register URL would win over the file's.
    monkeypatch.delenv("ZEFIX_URL", raising=False)
    assert "cannot be empty" in _location(
        client.post("/settings/zefix", data={"contact": " "}, follow_redirects=False)
    )
    client.post("/settings/zefix", data={"contact": "arpenteur (me@example.ch)"},
                follow_redirects=False)
    assert load_settings(root).zefix.contact == "arpenteur (me@example.ch)"
    assert load_settings(root).zefix.base_url == "https://example.test/ZefixPublicREST"
    client.post("/settings/mail", data={"own_domains": "Example.ch\n@mine.ch\n\nexample.ch"},
                follow_redirects=False)
    assert load_settings(root).mail.own_domains == ("example.ch", "mine.ch")


def test_model_access_is_saved_and_a_wrong_id_refused(client, root) -> None:
    wrong = client.post("/settings/llm", data={"auth_mode": "api_key", "match_model": "gpt-9"},
                        follow_redirects=False)
    assert "not a Claude model id" in _location(wrong)
    reply = client.post("/settings/llm", data={
        "auth_mode": "api_key", "classify_model": "claude-haiku-5-5",
        "enrich_model": "", "match_model": "claude-sonnet-5-5", "advise_model": "",
        "prep_model": "", "draft_model": "", "advise_effort": "high", "prep_effort": "",
    }, follow_redirects=False)
    assert _location(reply) == "/settings?note=Model access saved."
    loaded = load_settings(root)
    assert loaded.llm.auth_mode == "api_key" and loaded.llm.advise_effort == "high"
    assert loaded.llm.enrich_model == "claude-sonnet-5-5", "empty takes the family default"


def test_enrolment_names_a_brief_that_exists(client, root) -> None:
    missing = client.post("/settings/enrol", data={
        "brief": "nowhere.md", "android_testers": "12", "android_days": "14",
    }, follow_redirects=False)
    assert "No file named nowhere.md" in _location(missing)
    outside = client.post("/settings/enrol", data={
        "brief": "../etc/passwd", "android_testers": "12", "android_days": "14",
    }, follow_redirects=False)
    assert "under the project folder" in _location(outside)
    (root / "brief.md").write_text("# Brief\n", encoding="utf-8")
    reply = client.post("/settings/enrol", data={
        "programme": "pilot", "segments": ["sme-owner"], "brief": "brief.md",
        "include_in_draft": "1", "ios_link": "", "android_window_start": "1 mai",
        "android_window_end": "15 mai", "android_testers": "12", "android_days": "14",
    }, follow_redirects=False)
    assert _location(reply) == "/settings?note=Enrolment saved."
    enrol = load_settings(root).enrol
    assert enrol.programme == "PILOT" and enrol.segments == ("sme-owner",)
    assert enrol.brief == "brief.md" and enrol.include_in_draft is True
    assert enrol.android_window_start == "1 mai"


def test_weights_open_a_version_only_when_they_change(client, root, conn) -> None:
    from arpenteur.modules.scout import store as scout_store

    weights = {"icp_fit": "0.3", "need": "0.25", "timing": "0.15", "value": "0.1",
               "reach": "0.1", "relationship": "0.1"}
    first = client.post("/settings/scoring", data={**weights, "name": "pass one", "note": "n"},
                        follow_redirects=False)
    assert "weight set 1 is in force" in _location(first)
    set_id, _ = scout_store.active_weights(conn)
    assert set_id is not None
    again = client.post("/settings/scoring", data={**weights, "name": "pass one", "note": "n"},
                        follow_redirects=False)
    assert "no new version" in _location(again)
    assert scout_store.active_weights(conn)[0] == set_id
    changed = client.post("/settings/scoring", data={**weights, "need": "0.5", "name": "pass two"},
                          follow_redirects=False)
    assert "weight set 2 is in force" in _location(changed)
    assert scout_store.active_weights(conn)[0] != set_id
    assert load_settings(root).weight_set_name == "pass two"
    bad = client.post("/settings/scoring", data={**weights, "need": "lots", "name": "x"},
                      follow_redirects=False)
    assert "cannot be used" in _location(bad)
    nameless = client.post("/settings/scoring", data={**weights, "name": ""},
                           follow_redirects=False)
    assert "Name the weighting" in _location(nameless)


def test_the_catalogue_is_saved_as_rows_and_opens_a_rate_card(client, root, conn) -> None:
    from arpenteur.modules.sales import store as sales_store

    rows = {
        "code": ["Day", "diag", ""], "name": ["Day rate", "Diagnostic", ""],
        "unit": ["day", "fixed", "fixed"], "low": ["800", "1'500", ""],
        "high": ["800", "3000", ""], "sort_order": ["20", "10", ""],
        "what": ["Eight hours.", "Two days on site.", ""],
        "prerequisite": ["", "", ""], "basis": ["", "Sites counted.", ""],
        "card_note": "Travel is not billed.",
    }
    reply = client.post("/settings/services", data=rows, follow_redirects=False)
    assert "a rate card with 2 line(s) is in force" in _location(reply)
    loaded = load_settings(root)
    assert [e.code for e in loaded.catalogue] == ["diag", "day"], "sorted by order"
    assert loaded.catalogue[0].low == "1500.00" and loaded.card_note == "Travel is not billed."
    card = sales_store.current_card(conn)
    assert card is not None
    again = client.post("/settings/services", data=rows, follow_redirects=False)
    assert "unchanged" in _location(again)
    assert sales_store.current_card(conn)["id"] == card["id"]
    twice = client.post("/settings/services", data={**rows, "code": ["day", "day", ""]},
                        follow_redirects=False)
    assert "used twice" in _location(twice)
    upside_down = client.post("/settings/services", data={**rows, "low": ["900", "1500", ""]},
                              follow_redirects=False)
    assert "lower price is above" in _location(upside_down)
    page = client.get("/settings").text
    assert 'value="diag"' in page and 'value="Travel is not billed."' in page


def test_the_sales_sections_follow_the_sales_module(root, conn) -> None:
    from dataclasses import replace

    with_sales = TestClient(create_app(load_settings(root))).get("/settings").text
    assert 'id="identity"' in with_sales and 'id="catalogue"' in with_sales
    without = replace(load_settings(root), modules=("scout", "geo", "clients"))
    page = TestClient(create_app(without)).get("/settings").text
    assert 'id="identity"' not in page and 'id="catalogue"' not in page
    assert 'href="#identity"' not in page


# --- the shipped file carries nobody's identity ----------------------------------


def test_the_shipped_configuration_carries_no_personal_value() -> None:
    """The file is committed and read by every user; the operator's own values
    live in the database, where `settings import-toml` put them."""
    import tomllib

    shipped = Path(__file__).resolve().parent.parent / "arpenteur.toml"
    text = shipped.read_text(encoding="utf-8")
    raw = tomllib.loads(text)
    issuer = raw.get("issuer", {})
    for key in ("name", "street", "building", "postcode", "town", "iban", "payment_terms"):
        assert issuer.get(key, "") == "", key
    assert raw["zefix"]["contact"] == "arpenteur"
    assert raw.get("enrol", {}).get("programme", "") == ""
    assert raw.get("enrol", {}).get("segments", []) == []
    assert raw.get("smtp", {}).get("sender_name", "") == ""
    assert raw.get("smtp", {}).get("signature_image", "") == ""
    assert "service" not in raw.get("rates", {})
    assert raw.get("rates", {}).get("card", "") == ""
    mailboxes = set(re.findall(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", text)) - {"zefix@bj.admin.ch"}
    assert not mailboxes, f"no mailbox belongs in a committed file: {mailboxes}"
    assert not re.search(r"\bCH\d{2}\s?\d{4}", text), "no IBAN belongs in a committed file"
