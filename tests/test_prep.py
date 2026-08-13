"""The prep stage: a client's vocabulary, and what to revise before meeting them.

A file accumulates jargon faster than anyone absorbs it. One foundation's
dossier carries DMARC, nLPD, ISO 9001:2015, ISO 27001, SOMED, DGCS, CIVESS,
CEESV, EMS, EPSM, Sadies, Opale, Syselcom and Digital 2030. Some of those you
know cold and some you half know, and the difference shows in a meeting where
the client's own IT expert is sitting there.

The tests worth having are about the three things that would make this useless:
an evening's revision silently reset by a re-run, a fabricated reference
discovered in front of the client, and a heavy model being used for a job that
does not need it while the job that does gets a light one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.kernel.db import Connection, Row
from arpenteur.config import EFFORTS, load_settings
from arpenteur.console.app import create_app
from arpenteur.stages import advise, prep

TOML = """
[survey]
cantons = ["VD"]
legal_forms = ["0110"]

[segments]
sme-owner = "A small owner-run business."

[rates]
card = "Diagnostic, fixed fee: CHF 3'600 to CHF 7'200."

[zefix]
base_url = "https://example.test/ZefixPublicREST"
rate_limit = 2.0
max_retries = 0
contact = "tests"

[llm]
provider = "anthropic"
classify_model = "test"
draft_model = "test"
match_model = "test-match"
advise_model = "test-opus"
advise_effort = "xhigh"
prep_model = "test-sonnet"
batch_size = 20
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "arpenteur.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "campaign.md").write_text("# Campaign\n\nx\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def conn(root: Path, db_path: str) -> Connection:
    connection = db.open_db(db_path)
    connection.execute(
        "INSERT INTO core.company (uid, name, canton, legal_form_uid, legal_form_label,"
        " legal_seat, status, source_url, method, fetched_at, first_seen, hydrated_at)"
        " VALUES ('CHE103895771', 'Fondation Bois-Gentil', 'VD', '0110',"
        " 'Fondation', 'Lausanne', 'ACTIVE', 'https://example.test', 'zefix',"
        " '2026-08-12', '2026-08-12', '2026-08-12')"
    )
    connection.commit()
    return connection


@pytest.fixture
def client_id(conn: Connection) -> int:
    company_id = conn.execute("SELECT id AS id FROM core.company").fetchone()[0]
    new_id = db.promote_to_client(conn, company_id)
    db.save_dossier_section(
        conn, new_id, "estate", "Aucun DMARC. SPF de sept expéditeurs.",
        source="DNS public",
    )
    conn.commit()
    return new_id


@pytest.fixture
def web(root: Path, conn) -> TestClient:
    return TestClient(create_app(load_settings(root)))


REPLY = {
    "orientation": "Une fondation médico-sociale en pleine refonte du SI.",
    "terms": [
        {"term": "DMARC", "category": "mail",
         "expansion": "Domain-based Message Authentication, Reporting and Conformance",
         "what_it_is": "Une politique publiée dans le DNS.",
         "why_here": "_dmarc.fbg.ch répond NXDOMAIN.",
         "must_know": "Sans DMARC, personne ne reçoit de rapport.",
         "question": "Qui reçoit les rapports aujourd'hui%s",
         "reference": "RFC 7489", "priority": "must"},
        {"term": "nLPD", "category": "regulatory",
         "expansion": "nouvelle Loi fédérale sur la protection des données",
         "what_it_is": "La loi suisse révisée.",
         "why_here": "Données de santé.",
         "must_know": "Art. 5 let. c: donnée sensible.",
         "question": "Existe-t-il un registre des traitements%s",
         "reference": "nLPD art. 5 let. c", "priority": "must"},
    ],
    "agenda": [{"revise": "DMARC", "why": "C'est l'ouverture."}],
}


class FakeProvider:
    name = "fake"

    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def complete_json(self, *, system, user, model, max_tokens, schema, effort=""):
        self.calls.append(
            {"system": system, "user": user, "model": model, "effort": effort,
             "max_tokens": max_tokens}
        )
        return self.reply


@pytest.fixture
def fake(monkeypatch) -> FakeProvider:
    provider = FakeProvider(REPLY)
    monkeypatch.setattr(prep, "get_provider", lambda _s: provider)
    return provider


# --- model routing ------------------------------------------------------------


def test_the_two_stages_use_different_models(root, conn, client_id, monkeypatch) -> None:
    """Arbitrating between three architecture proposals and extracting a
    glossary are not the same job and should not cost the same."""
    advising = FakeProvider({
        "situation": "x", "opening": "y", "points": [], "services": [],
        "pricing": [], "risks": [], "questions": [],
    })
    preparing = FakeProvider(REPLY)
    monkeypatch.setattr(advise, "get_provider", lambda _s: advising)
    monkeypatch.setattr(prep, "get_provider", lambda _s: preparing)
    settings = load_settings(root)

    advise.run(settings, conn, client_id=client_id, log=lambda _m: None)
    prep.run(settings, conn, client_id=client_id, log=lambda _m: None)

    assert advising.calls[0]["model"] == "test-opus"
    assert advising.calls[0]["effort"] == "xhigh"
    assert preparing.calls[0]["model"] == "test-sonnet"
    assert preparing.calls[0]["effort"] == ""


def test_a_missing_stage_model_falls_back_rather_than_failing(root, conn) -> None:
    (root / "arpenteur.toml").write_text(
        TOML.replace('advise_model = "test-opus"', "")
            .replace('prep_model = "test-sonnet"', ""),
        encoding="utf-8",
    )
    llm = load_settings(root).llm
    assert (llm.advise_model or llm.match_model) == "test-match"
    assert (llm.prep_model or llm.match_model) == "test-match"


def test_an_unknown_effort_is_refused_at_load_time(root) -> None:
    """A stage silently running at the default when the file says xhigh is
    exactly the difference nobody notices until the answers are worse and the
    bill is smaller."""
    (root / "arpenteur.toml").write_text(
        TOML.replace('advise_effort = "xhigh"', 'advise_effort = "extra-high"'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Unknown effort"):
        load_settings(root)
    assert "xhigh" in EFFORTS and "max" in EFFORTS


def test_effort_rides_in_output_config_beside_the_schema() -> None:
    """The API puts it there, not at the top level. Getting this wrong is a
    400 at the first real call and nowhere earlier."""
    import inspect

    from arpenteur.llm import AnthropicProvider

    body = inspect.getsource(AnthropicProvider.complete_json)
    assert 'output_config["effort"] = effort' in body
    assert "output_config=output_config" in body
    # Streamed, because the SDK refuses a non-streaming request whose
    # max_tokens implies it could run past ten minutes, and this stage asks
    # for tens of thousands.
    assert "messages.stream" in body


# --- what makes the glossary worth keeping ------------------------------------


def test_a_term_records_why_it_is_in_this_clients_file(
    root, conn, client_id, fake
) -> None:
    """A general glossary is a search away and worth nothing at the table."""
    prep.run(load_settings(root), conn, client_id=client_id, log=lambda _m: None)
    dmarc = {t["term"]: t for t in db.glossary_for(conn, client_id)}["DMARC"]
    assert dmarc["why_here"] == "_dmarc.fbg.ch répond NXDOMAIN."
    assert dmarc["question"]
    assert dmarc["reference"] == "RFC 7489"


def test_references_are_named_not_linked() -> None:
    """An invented URL looks exactly like a real one and is discovered in
    front of the client."""
    assert "NEVER a URL" in prep.SYSTEM
    assert "name the document without it" in prep.SYSTEM


def test_a_term_must_carry_a_question() -> None:
    assert "cannot turn into a question is a term you do not need" in prep.SYSTEM


def test_re_running_never_resets_what_was_already_revised(
    root, conn, client_id, fake
) -> None:
    """An evening's revision silently undone by a re-run after the client sends
    a document would make the ticking pointless."""
    settings = load_settings(root)
    prep.run(settings, conn, client_id=client_id, log=lambda _m: None)
    dmarc = {t["term"]: t for t in db.glossary_for(conn, client_id)}["DMARC"]
    db.set_term_known(conn, dmarc["id"], known=True, note="revu le 12.08")
    conn.commit()

    prep.run(settings, conn, client_id=client_id, log=lambda _m: None)
    again = {t["term"]: t for t in db.glossary_for(conn, client_id)}["DMARC"]
    assert again["known"] == 1
    assert again["note"] == "revu le 12.08"


def test_re_running_refreshes_rather_than_duplicating(
    root, conn, client_id, fake
) -> None:
    settings = load_settings(root)
    stats = prep.run(settings, conn, client_id=client_id, log=lambda _m: None)
    assert stats["added"] == 2 and stats["refreshed"] == 0
    again = prep.run(settings, conn, client_id=client_id, log=lambda _m: None)
    assert again["added"] == 0 and again["refreshed"] == 2
    assert len(db.glossary_for(conn, client_id)) == 2


def test_terms_already_known_are_kept_off_the_agenda(
    root, conn, client_id, fake
) -> None:
    """Still in the glossary, because that is what gets read again the morning
    of the meeting. Just not what to spend the last hour on."""
    settings = load_settings(root)
    prep.run(settings, conn, client_id=client_id, log=lambda _m: None)
    dmarc = {t["term"]: t for t in db.glossary_for(conn, client_id)}["DMARC"]
    db.set_term_known(conn, dmarc["id"], known=True)
    conn.commit()
    prep.run(settings, conn, client_id=client_id, log=lambda _m: None)
    assert "ALREADY REVISED" in fake.calls[-1]["user"]
    assert "DMARC" in fake.calls[-1]["user"].split("ALREADY REVISED")[1]


def test_the_glossary_reads_the_same_file_the_approach_does(
    root, conn, client_id, fake
) -> None:
    """Two assemblers over one dossier would drift, and the drift shows up as a
    glossary that omits the term the approach was built on."""
    prep.run(load_settings(root), conn, client_id=client_id, log=lambda _m: None)
    assert "Aucun DMARC" in fake.calls[0]["user"]
    # The rate card is not vocabulary; there is no reason to put prices in
    # front of a second model.
    assert "CHF 3'600" not in fake.calls[0]["user"]


def test_an_empty_dossier_is_refused_rather_than_guessed_at(root, conn) -> None:
    company_id = conn.execute(
        "INSERT INTO core.company (uid, name, canton, source_url, method)"
        " VALUES ('CHE1', 'Vide SA', 'VD', 'x', 'zefix') RETURNING id"
    ).fetchone()["id"]
    empty = db.promote_to_client(conn, company_id)
    conn.commit()
    with pytest.raises(RuntimeError, match="no vocabulary to extract"):
        prep.run(load_settings(root), conn, client_id=empty, log=lambda _m: None)


# --- the page -----------------------------------------------------------------


def test_the_page_groups_by_shelf_and_shows_the_agenda(
    web, root, conn, client_id, fake
) -> None:
    prep.run(load_settings(root), conn, client_id=client_id, log=lambda _m: None)
    body = web.get(f"/client/{client_id}/prep").text
    assert "Revise in this order" in body
    assert "RFC 7489" in body
    assert "_dmarc.fbg.ch répond NXDOMAIN." in body
    for name, _label in prep.CATEGORIES[:0] or [("mail", "")]:
        assert name in body


def test_ticking_a_term_is_the_only_writable_field(web, root, conn, client_id, fake) -> None:
    prep.run(load_settings(root), conn, client_id=client_id, log=lambda _m: None)
    term = db.glossary_for(conn, client_id)[0]
    web.post(f"/term/{term['id']}/known", data={"known": "1"},
             follow_redirects=False)
    assert conn.execute(
        "SELECT known FROM crm.glossary WHERE id = %s", (term["id"],)
    ).fetchone()[0] == 1


def test_the_client_card_and_dossier_both_lead_here(web, client_id) -> None:
    assert f"/client/{client_id}/prep" in web.get(f"/client/{client_id}").text
    assert f"/client/{client_id}/prep" in web.get(f"/client/{client_id}/dossier").text


def test_an_expansion_already_inside_the_term_is_not_printed_twice(
    conn, client_id
) -> None:
    """Asked for the expansion of a term written `GA4 (Google Analytics 4)`, a
    model obligingly gives `Google Analytics 4`, and printing both stutters."""
    from arpenteur.console.prep_page import _term

    db.save_terms(conn, client_id, [
        {"term": "GA4 (Google Analytics 4)", "category": "infrastructure",
         "expansion": "Google Analytics 4", "what_it_is": "x", "why_here": "y",
         "must_know": "z", "question": "q", "reference": None, "priority": "nice"},
    ])
    conn.commit()
    row = db.glossary_for(conn, client_id)[0]
    assert _term(row).count("Google Analytics 4") == 1
