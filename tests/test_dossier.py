"""The client dossier, the documents, and the suggested approach.

The register says what a company is. The dossier says what it runs, who
decides, what is exposed and what has already been tried, and none of that is
in any register: it is assembled from public evidence, from the client's own
papers and from what was said in the room.

The tests worth having here are about the three places this could quietly go
wrong: a price invented by a model and repeated to a client, a document that
was never actually read being treated as though it had been, and a proposal
whose inputs cannot be reconstructed six months later.
"""

from __future__ import annotations

import io
import threading
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arpenteur import attachments as att
from arpenteur.kernel.db import Connection
from arpenteur import db, dossier
from arpenteur.config import load_settings
from arpenteur.console import runner as runner_module
from arpenteur.console.app import create_app
from arpenteur.stages import advise
from arpenteur.commands import common

TOML = """
[survey]
cantons = ["VD"]
legal_forms = ["0110"]

[segments]
sme-owner = "A small owner-run business."

[rates]
card = "Diagnostic, fixed fee, 2 to 4 days: CHF 3'600 to CHF 7'200."

[zefix]
base_url = "https://example.test/ZefixPublicREST"
rate_limit = 2.0
max_retries = 0
contact = "tests"

[llm]
provider = "anthropic"
classify_model = "claude-test"
draft_model = "claude-test"
match_model = "claude-test"
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
        " legal_seat, status, purpose, source_url, method, fetched_at, first_seen,"
        " hydrated_at)"
        " VALUES ('CHE100000011', 'Fondation Clair-Logis', 'VD', '0110',"
        " 'Fondation', 'Lausanne', 'ACTIVE', 'aide aux personnes agees',"
        " 'https://example.test', 'zefix', '2026-08-12', '2026-08-12', '2026-08-12')"
    )
    connection.commit()
    return connection


@pytest.fixture
def client_id(conn: Connection) -> int:
    company_id = conn.execute("SELECT id AS id FROM core.company").fetchone()[0]
    new_id = db.promote_to_client(conn, company_id)
    conn.commit()
    return new_id


@pytest.fixture
def web(root: Path, conn) -> TestClient:
    return TestClient(create_app(load_settings(root)))


# --- the dossier --------------------------------------------------------------


def test_a_section_is_saved_with_where_it_came_from(conn, client_id) -> None:
    """The source line is the difference between a fact you can show a client
    and one you assumed, and it is exactly what gets lost when notes are one
    long textarea."""
    db.save_dossier_section(
        conn, client_id, "estate", "No DMARC record at all.",
        source="public DNS, 12.08.2026",
    )
    conn.commit()
    row = db.dossier_for(conn, client_id)["estate"]
    assert row["body"] == "No DMARC record at all."
    assert row["source"] == "public DNS, 12.08.2026"


def test_saving_a_section_twice_replaces_it_rather_than_duplicating(
    conn, client_id
) -> None:
    db.save_dossier_section(conn, client_id, "people", "First reading.")
    db.save_dossier_section(conn, client_id, "people", "Corrected reading.")
    conn.commit()
    rows = conn.execute(
        "SELECT * FROM crm.dossier WHERE client_id = %s AND section = 'people'",
        (client_id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["body"] == "Corrected reading."


def test_an_empty_body_clears_a_section(conn, client_id) -> None:
    """A section that turns out to be wrong should be emptiable from the same
    field that filled it."""
    db.save_dossier_section(conn, client_id, "systems", "Assumed the wrong thing.")
    db.save_dossier_section(conn, client_id, "systems", "   ")
    conn.commit()
    assert db.dossier_for(conn, client_id)["systems"]["body"] is None
    assert "systems" not in dossier.filled(db.dossier_for(conn, client_id))


def test_an_empty_section_is_named_rather_than_dropped() -> None:
    """That a dossier says nothing about who decides is itself information, and
    a reader who cannot tell an empty section from an absent one assumes the
    first."""
    text = dossier.as_text({"situation": {"body": "280 staff."}})
    assert "280 staff." in text
    assert "(not filled in)" in text
    for section in dossier.SECTIONS:
        assert section.title in text


def test_every_section_carries_help_and_an_example() -> None:
    """A field with a bare label is a field people fill in wrongly and then
    stop trusting. This is the complaint that produced the form."""
    for section in dossier.SECTIONS:
        assert len(section.what) > 40, section.name
        assert len(section.example) > 60, section.name
        assert section.group in {name for name, _t, _w in dossier.GROUPS}


def test_the_form_shows_the_example_inside_the_empty_field(web, client_id) -> None:
    body = web.get(f"/client/{client_id}/dossier").text
    for section in dossier.SECTIONS:
        assert section.title in body
    assert "placeholder=" in body


def test_the_client_card_says_how_much_of_the_dossier_exists(
    web, conn, client_id
) -> None:
    """A bare link would say nothing. Two of ten sections and no approach is a
    different instruction from nine of ten with a proposal already made."""
    db.save_dossier_section(conn, client_id, "situation", "280 staff, 7 sites.")
    conn.commit()
    body = web.get(f"/client/{client_id}").text
    assert f"/client/{client_id}/dossier" in body
    assert f"1 of {len(dossier.SECTIONS)} sections filled" in body


# --- the documents ------------------------------------------------------------


def _docx(text: str) -> bytes:
    xml = (
        '<?xml version="1.0"?><w:document xmlns:w="x"><w:body>'
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def test_a_document_is_stored_under_its_own_digest(tmp_path: Path) -> None:
    stored = att.store(tmp_path, b"hello", "Rapport 2025.pdf")
    assert stored.path.read_bytes() == b"hello"
    assert stored.sha256 in stored.path.name
    assert not stored.existed


def test_the_same_document_twice_is_one_file(tmp_path: Path) -> None:
    """`Rapport 2025 VF (2).pdf` is visibly the same document as
    `Rapport 2025 final.pdf` rather than a second one."""
    first = att.store(tmp_path, b"identical", "Rapport 2025 final.pdf")
    second = att.store(tmp_path, b"identical", "Rapport 2025 VF (2).pdf")
    assert second.existed
    assert first.path == second.path


def test_text_comes_out_of_word_and_plain_files() -> None:
    text, error = att.extract(_docx("Contrat de mandat"), "contrat.docx")
    assert not error
    assert "Contrat de mandat" in text

    text, error = att.extract("Ligne une\nLigne deux".encode(), "notes.txt")
    assert not error
    assert "Ligne deux" in text


def test_a_file_with_no_text_is_an_error_not_an_empty_success() -> None:
    """A scanned PDF yields zero characters, and storing that as `extracted`
    is how a document nobody can read gets fed to a model as though it had
    been read."""
    text, error = att.extract(b"   \n \n  ", "scan.txt")
    assert text == ""
    assert "no text" in error


def test_an_unsupported_format_is_stored_and_says_why_it_is_unread() -> None:
    text, error = att.extract(b"\x00\x01binary", "plan.dwg")
    assert text == ""
    assert "dwg" in error


def test_uploading_a_document_extracts_it_and_logs_it(web, conn, client_id) -> None:
    reply = web.post(
        f"/dossier/{client_id}/documents",
        files={"file": ("politique.txt", b"Politique de securite 2024", "text/plain")},
        data={"kind": "policy", "note": "Sent after the kickoff"},
        follow_redirects=False,
    )
    assert reply.status_code == 303
    row = db.attachments_for(conn, client_id, with_text=True)[0]
    assert row["kind"] == "policy"
    assert "securite" in row["text"]
    assert row["extract_error"] is None
    # A document arriving is part of the record, so the timeline can answer
    # "when did we get the accounts" without anyone writing a note saying so.
    kinds = [e["kind"] for e in db.timeline(conn, client_id)]
    assert "document" in kinds


def test_the_same_document_uploaded_twice_is_one_row(web, conn, client_id) -> None:
    for note in ("first", "second"):
        web.post(
            f"/dossier/{client_id}/documents",
            files={"file": ("a.txt", b"same bytes", "text/plain")},
            data={"note": note},
            follow_redirects=False,
        )
    rows = db.attachments_for(conn, client_id)
    assert len(rows) == 1
    assert rows[0]["note"] == "second"


def test_several_documents_can_arrive_at_once(web, conn, client_id) -> None:
    """Dropping a folder is the ordinary case.

    Uploading one at a time was the interface deciding how somebody organises
    their week: a client sends the accounts, the policy and two contracts in
    one mail, and they arrive together or not at all.
    """
    reply = web.post(
        f"/dossier/{client_id}/documents",
        files=[
            ("file", ("comptes.txt", b"Comptes 2025", "text/plain")),
            ("file", ("politique.txt", b"Politique de securite", "text/plain")),
            ("file", ("contrat.txt", b"Contrat cadre", "text/plain")),
        ],
        data={"kind": "policy"},
        follow_redirects=False,
    )
    assert reply.status_code == 303
    rows = db.attachments_for(conn, client_id)
    assert sorted(r["filename"] for r in rows) == [
        "comptes.txt", "contrat.txt", "politique.txt",
    ]
    assert {r["kind"] for r in rows} == {"policy"}


def test_four_files_get_four_answers_not_one(web, conn, client_id) -> None:
    """Three being fine is not something a single banner can say.

    One file had one outcome and a redirect carrying a note could report it.
    Several have several, and one of them being a duplicate while another is
    unreadable is exactly the case somebody needs told.
    """
    web.post(
        f"/dossier/{client_id}/documents",
        files=[("file", ("deja.txt", b"already here", "text/plain"))],
        follow_redirects=False,
    )
    reply = web.post(
        f"/dossier/{client_id}/documents",
        files=[
            ("file", ("deja.txt", b"already here", "text/plain")),
            ("file", ("neuf.txt", b"Something new", "text/plain")),
        ],
        headers={"HX-Request": "true"},
    )
    assert reply.status_code == 200
    assert "<header>" not in reply.text          # the panel, not a whole page
    assert 'id="papers"' in reply.text
    assert "deja.txt is byte for byte a document already held" in reply.text
    assert "neuf.txt: stored and read." in reply.text


def test_an_upload_with_no_files_says_so_rather_than_failing(web, client_id) -> None:
    reply = web.post(
        f"/dossier/{client_id}/documents", files=[],
        headers={"HX-Request": "true"},
    )
    assert reply.status_code == 200
    assert "Nothing was sent" in reply.text


def test_the_documents_panel_can_still_be_uploaded_to_without_javascript(
    web, client_id
) -> None:
    """The drop zone is not the only way in.

    A zone with no visible control is unusable by anyone who cannot drag and
    unfindable by anyone who has not been told it is there, which on a first
    visit is everybody.
    """
    page = web.get(f"/client/{client_id}/dossier").text
    assert 'enctype="multipart/form-data"' in page
    assert 'type="file"' in page
    assert "data-dropzone" in page


def test_the_extracted_text_has_a_stable_url_for_an_agent(
    web, conn, client_id
) -> None:
    """An agent asked to analyse a client's documents needs something to fetch
    that is the document, not a page about the document."""
    web.post(
        f"/dossier/{client_id}/documents",
        files={"file": ("b.txt", b"Contenu du rapport", "text/plain")},
        follow_redirects=False,
    )
    row = db.attachments_for(conn, client_id)[0]
    reply = web.get(f"/attachment/{row['id']}/text")
    assert reply.status_code == 200
    assert reply.text.strip() == "Contenu du rapport"


def test_the_listing_does_not_read_the_document_bodies(conn, client_id) -> None:
    """The column holds whole annual reports; selecting it to print a filename
    would read megabytes to print kilobytes."""
    db.add_attachment(
        conn, client_id,
        {"filename": "x.txt", "bytes": 3, "sha256": "a" * 64, "text": "abc"},
    )
    conn.commit()
    assert "text" not in db.attachments_for(conn, client_id)[0].keys()
    assert "text" in db.attachments_for(conn, client_id, with_text=True)[0].keys()


# --- the suggested approach ---------------------------------------------------


class FakeProvider:
    name = "fake"

    def __init__(self, reply: dict) -> None:
        self.reply = reply
        self.seen: list[str] = []

    def complete_json(self, *, system, user, model, max_tokens, schema, effort=""):
        self.seen.append(user)
        return self.reply


REPLY = {
    "situation": "A foundation with no IT owner.",
    "opening": "Show them the SPF record.",
    "points": [{"point": "No DMARC", "why": "Health data.", "ask": "Who receives the reports?",
                "evidence": "public DNS"}],
    "services": [{"name": "Diagnostic", "what": "Two days on site.",
                  "why_them": "No owner.", "prerequisite": None}],
    "pricing": [{"item": "Diagnostic", "low": 3600, "high": 7200,
                 "currency": "CHF", "unit": "fixed",
                 "basis": "Diagnostic, fixed fee, 2 to 4 days"}],
    "risks": ["The provider may resist."],
    "questions": ["Which package holds the resident record?"],
}


@pytest.fixture
def fake(monkeypatch) -> FakeProvider:
    provider = FakeProvider(REPLY)
    monkeypatch.setattr(advise, "get_provider", lambda _s: provider)
    return provider


def test_advice_refuses_to_be_generated_from_nothing(root, conn, client_id) -> None:
    """Advice from a register entry alone is a horoscope."""
    with pytest.raises(RuntimeError, match="nothing to advise from"):
        advise.run(load_settings(root), conn, client_id=client_id, log=lambda _m: None)


def test_the_rate_card_is_quoted_into_the_prompt(root, conn, client_id, fake) -> None:
    db.save_dossier_section(conn, client_id, "situation", "280 staff.")
    conn.commit()
    advise.run(load_settings(root), conn, client_id=client_id, log=lambda _m: None)
    assert "CHF 3'600 to CHF 7'200" in fake.seen[0]
    assert "RATE CARD" in fake.seen[0]


def test_an_empty_rate_card_tells_the_model_to_price_nothing(
    root, conn, client_id, fake
) -> None:
    """A model asked what a Swiss IT diagnostic costs answers with a confident
    number from nowhere, and the first time it is repeated to a client it
    becomes a commitment."""
    (root / "arpenteur.toml").write_text(
        TOML.replace(
            "card = \"Diagnostic, fixed fee, 2 to 4 days: CHF 3'600 to CHF 7'200.\"",
            'card = ""',
        ),
        encoding="utf-8",
    )
    db.save_dossier_section(conn, client_id, "situation", "280 staff.")
    conn.commit()
    advise.run(load_settings(root), conn, client_id=client_id, log=lambda _m: None)
    assert "Every price must be null" in fake.seen[0]


def test_the_system_prompt_forbids_inventing_a_price() -> None:
    assert "Never invent a figure" in advise.SYSTEM
    assert "market rate" in advise.SYSTEM


def test_open_questions_are_passed_as_questions_not_as_material() -> None:
    """An advisor who answers "which package holds the resident record" by
    guessing is worse than one who puts it at the top of the agenda."""
    assert "Do not answer them" in advise.SYSTEM


def test_the_documents_go_into_the_prompt(root, conn, client_id, fake) -> None:
    db.save_dossier_section(conn, client_id, "situation", "280 staff.")
    db.add_attachment(
        conn, client_id,
        {"filename": "contrat.txt", "bytes": 20, "sha256": "b" * 64,
         "kind": "contract", "text": "Le present contrat lie les parties"},
    )
    conn.commit()
    advise.run(load_settings(root), conn, client_id=client_id, log=lambda _m: None)
    assert "Le present contrat" in fake.seen[0]
    assert "contrat.txt" in fake.seen[0]


def test_a_long_document_is_truncated_and_says_so(root, conn, client_id, fake) -> None:
    """A 200-page rapport de gestion would otherwise dominate everything else
    the stage was given, and a proposal built on a truncated reading has to be
    tellable from one built on the whole document."""
    db.save_dossier_section(conn, client_id, "situation", "280 staff.")
    db.add_attachment(
        conn, client_id,
        {"filename": "gros.txt", "bytes": 1, "sha256": "c" * 64,
         "text": "x" * (att.PROMPT_CHARS + 5000)},
    )
    conn.commit()
    advise.run(load_settings(root), conn, client_id=client_id, log=lambda _m: None)
    assert "truncated" in fake.seen[0]
    read = db.approaches_for(conn, client_id)[0]["read_json"]
    assert read["documents"][0]["truncated"] is True


def test_a_document_that_could_not_be_read_is_named_as_unread(
    root, conn, client_id, fake
) -> None:
    """Otherwise a proposal reads as though it had the accounts in front of it
    when it had a scan it could not open."""
    db.save_dossier_section(conn, client_id, "situation", "280 staff.")
    db.add_attachment(
        conn, client_id,
        {"filename": "scan.pdf", "bytes": 9, "sha256": "d" * 64,
         "extract_error": "no text"},
    )
    conn.commit()
    advise.run(load_settings(root), conn, client_id=client_id, log=lambda _m: None)
    assert "not machine-readable" in fake.seen[0]
    assert "scan.pdf" in fake.seen[0]


def test_an_approach_records_what_it_read(root, conn, client_id, fake) -> None:
    """"What did this proposal actually read" is the first question asked of a
    number that turns out to be wrong."""
    db.save_dossier_section(conn, client_id, "situation", "280 staff.")
    db.save_dossier_section(conn, client_id, "people", "No IT role.")
    conn.commit()
    advise.run(load_settings(root), conn, client_id=client_id, log=lambda _m: None)
    row = db.approaches_for(conn, client_id)[0]
    read = row["read_json"]
    assert set(read["sections"]) == {"situation", "people"}
    assert read["rate_card"] is True
    assert row["inputs_hash"]


def test_generating_again_keeps_the_earlier_one(root, conn, client_id, fake) -> None:
    """When the client asks in September why March quoted what it quoted, the
    answer has to still exist."""
    db.save_dossier_section(conn, client_id, "situation", "280 staff.")
    conn.commit()
    settings = load_settings(root)
    advise.run(settings, conn, client_id=client_id, log=lambda _m: None)
    db.save_dossier_section(conn, client_id, "situation", "280 staff, 7 sites.")
    conn.commit()
    advise.run(load_settings(root), conn, client_id=client_id, log=lambda _m: None)
    rows = db.approaches_for(conn, client_id)
    assert len(rows) == 2
    assert rows[0]["inputs_hash"] != rows[1]["inputs_hash"]


def test_a_dry_run_costs_nothing_and_saves_nothing(root, conn, client_id, fake) -> None:
    db.save_dossier_section(conn, client_id, "situation", "280 staff.")
    conn.commit()
    stats = advise.run(
        load_settings(root), conn, client_id=client_id, dry_run=True, log=lambda _m: None
    )
    assert stats["saved"] == 0
    assert fake.seen == []
    assert db.approaches_for(conn, client_id) == []


def _join(web: TestClient) -> None:
    """A run is a thread, so what it wrote is read after it has ended."""
    for thread in list(web.app.state.runner.threads.values()):
        thread.join(10)


def _hold_lane(lane: str, release: threading.Event, name: str) -> None:
    def work(_settings, _conn, *, log, **_kw):
        log("working")
        release.wait(5)
        return {}

    runner_module.STAGES[name] = runner_module.Stage(
        name=name, title=name, what="a stage that holds its lane", call=work,
        lane=lane, listed=False,
    )


def test_suggesting_an_approach_runs_through_the_runner(
    web, conn, client_id, fake
) -> None:
    """Inline, it held the request open for the whole model call and left no
    log; a run is watched, kept in the history and survives a reload."""
    db.save_dossier_section(conn, client_id, "situation", "280 staff.")
    conn.commit()
    reply = web.post(f"/dossier/{client_id}/advise", follow_redirects=False)
    assert reply.status_code == 303
    where = reply.headers["location"]
    assert where.startswith("/runs/")
    _join(web)
    run = db.run(conn, int(where.rsplit("/", 1)[1]))
    assert run["stage"] == "advise" and run["finished_at"] and run["error"] is None
    assert len(db.approaches_for(conn, client_id)) == 1
    page = web.get(where).text
    assert f"href='/client/{client_id}/dossier'" in page and "href='/runs'" in page


def test_a_dry_run_of_advice_through_the_runner_costs_nothing(
    web, conn, client_id, fake
) -> None:
    db.save_dossier_section(conn, client_id, "situation", "280 staff.")
    conn.commit()
    reply = web.post(f"/dossier/{client_id}/advise", data={"dry_run": "1"},
                     follow_redirects=False)
    _join(web)
    assert fake.seen == [] and db.approaches_for(conn, client_id) == []
    page = web.get(reply.headers["location"]).text
    assert "dry run: nothing sent to the model" in page


def test_advice_from_an_empty_dossier_fails_on_the_run_page(
    web, conn, client_id, fake
) -> None:
    """The stage's own refusal is kept: it reads on the run, with the way back
    to the dossier beside it."""
    reply = web.post(f"/dossier/{client_id}/advise", follow_redirects=False)
    _join(web)
    run_id = int(reply.headers["location"].rsplit("/", 1)[1])
    assert "nothing to advise from" in db.run(conn, run_id)["error"]
    page = web.get(reply.headers["location"]).text
    assert "nothing to advise from" in page
    assert f"href='/client/{client_id}/dossier'" in page


def test_advice_never_waits_behind_a_sweep(web, conn, client_id, fake) -> None:
    """A one-client call before a meeting must not queue behind an enrich run
    that takes the day."""
    db.save_dossier_section(conn, client_id, "situation", "280 staff.")
    conn.commit()
    release = threading.Event()
    _hold_lane("pipeline", release, "_sweep")
    try:
        web.app.state.runner.start("_sweep", {}, internal=True)
        reply = web.post(f"/dossier/{client_id}/advise", follow_redirects=False)
        assert reply.headers["location"].startswith("/runs/")
        for thread in list(web.app.state.runner.threads.values()):
            if thread.name.startswith("arpenteur-advise"):
                thread.join(10)
        assert len(db.approaches_for(conn, client_id)) == 1
    finally:
        release.set()
        _join(web)
        runner_module.STAGES.pop("_sweep", None)


def test_advice_for_a_client_nobody_holds_is_refused_on_the_page(web) -> None:
    reply = web.post("/dossier/999/advise", follow_redirects=False)
    assert reply.status_code == 303
    assert reply.headers["location"].startswith("/client/999/dossier?error=")
    assert "No%20client" in reply.headers["location"]


def test_a_second_advice_is_refused_on_the_page_it_came_from(
    web, conn, client_id, fake
) -> None:
    db.save_dossier_section(conn, client_id, "situation", "280 staff.")
    conn.commit()
    release = threading.Event()
    _hold_lane("dossier", release, "_busy")
    try:
        web.app.state.runner.start("_busy", {}, internal=True)
        reply = web.post(f"/dossier/{client_id}/advise", follow_redirects=False)
        assert reply.status_code == 303
        assert reply.headers["location"].startswith(
            f"/client/{client_id}/dossier?error=")
        assert "still%20going" in reply.headers["location"]
        assert fake.seen == []
    finally:
        release.set()
        _join(web)
        runner_module.STAGES.pop("_busy", None)


def test_accepting_an_approach_is_recorded_once(conn, client_id) -> None:
    """Advice nobody acted on and advice quoted to a client are different
    things, and only one should be repeated later."""
    approach_id = db.save_approach(conn, client_id, {"situation": "x"})
    db.accept_approach(conn, approach_id)
    first = conn.execute(
        "SELECT accepted_at FROM crm.approach WHERE id = %s", (approach_id,)
    ).fetchone()[0]
    db.accept_approach(conn, approach_id)
    assert conn.execute(
        "SELECT accepted_at FROM crm.approach WHERE id = %s", (approach_id,)
    ).fetchone()[0] == first


def test_the_page_warns_when_prices_were_impossible(web, conn, client_id) -> None:
    db.save_approach(
        conn, client_id,
        {"situation": "x", "opening": "y", "read": {"sections": ["situation"],
                                                    "documents": [], "rate_card": False},
         "pricing": [{"item": "Diagnostic", "low": None, "high": None,
                      "currency": "CHF", "unit": "fixed",
                      "basis": "the card does not cover it"}]},
    )
    conn.commit()
    body = web.get(f"/client/{client_id}/dossier").text
    assert "No rate card was set when this ran" in body
    assert "not priced" in body


# --- reading it without a browser ---------------------------------------------


def test_the_cli_prints_what_the_stage_would_read(
    root, conn, client_id, monkeypatch, capsys
) -> None:
    """So an agent can be pointed at a client with no browser and no schema."""
    from arpenteur import cli

    db.save_dossier_section(
        conn, client_id, "estate", "No DMARC.", source="public DNS"
    )
    db.add_attachment(
        conn, client_id,
        {"filename": "note.txt", "bytes": 5, "sha256": "e" * 64, "text": "Corps du document"},
    )
    conn.commit()
    monkeypatch.setattr(common, "load_settings", lambda: load_settings(root))
    assert cli.main(["dossier", "CLAIR-LOGIS", "--documents"]) == 0
    out = capsys.readouterr().out
    assert "No DMARC." in out
    assert "[source: public DNS]" in out
    assert "Corps du document" in out


def test_a_client_is_found_by_code_uid_or_name(
    root, conn, client_id, monkeypatch, capsys
) -> None:
    from arpenteur import cli

    monkeypatch.setattr(common, "load_settings", lambda: load_settings(root))
    for reference in ("CLAIR-LOGIS", "CHE-100.000.011", "Clair-Logis"):
        assert cli.main(["dossier", reference]) == 0
        assert "Fondation Clair-Logis" in capsys.readouterr().out


# --- removing what is not true ------------------------------------------------
#
# The schema was written saying nothing is ever deleted, because an audit trail
# with a delete button is a diary. That was half right, and the half it got
# wrong cost more: on 12.08.2026 rows describing a meeting and a project that
# never existed were written into a live record and there was no way to take
# them out. A log that cannot be corrected is not more truthful, only more
# confident.


def test_an_entry_that_is_not_true_can_be_removed(web, conn, client_id) -> None:
    event_id = db.record_event(
        conn, client_id, kind="meeting", occurred_at="2026-08-20T14:00:00",
        subject="Réunion qui n'a jamais existé",
    )
    conn.commit()
    web.post(f"/event/{event_id}/delete", data={"reason": "Never happened."},
             follow_redirects=False)
    subjects = [e["subject"] for e in db.timeline(conn, client_id)]
    assert "Réunion qui n'a jamais existé" not in subjects


def test_removing_an_entry_leaves_a_tombstone(conn, client_id) -> None:
    """Cancelled still asserts that a meeting was once planned, which is
    exactly the claim that has to stop being made. Deleting says it was never
    true, and says so on the record."""
    event_id = db.record_event(
        conn, client_id, kind="meeting", occurred_at="2026-08-20T14:00:00",
        subject="Réunion inventée",
    )
    db.delete_event(conn, event_id, reason="Seeded by mistake during development.")
    conn.commit()
    rows = db.timeline(conn, client_id)
    assert len(rows) == 1
    assert rows[0]["subject"].startswith("Removed: meeting 2026-08-20 14:00")
    assert "Seeded by mistake" in rows[0]["body"]


def test_a_deletion_with_no_reason_says_so_rather_than_leaving_it_blank(
    conn, client_id
) -> None:
    event_id = db.record_event(
        conn, client_id, kind="note", occurred_at="2026-08-01T09:00:00", subject="x"
    )
    db.delete_event(conn, event_id)
    conn.commit()
    assert db.timeline(conn, client_id)[0]["body"] == "No reason given."


def test_deleting_a_project_keeps_what_was_logged_against_it(
    conn, client_id
) -> None:
    """Correspondence that happened is still correspondence that happened, even
    when the piece of work it was filed under turns out never to have existed."""
    project_id = db.add_project(conn, client_id, "Projet inventé")
    kept = db.record_event(
        conn, client_id, kind="email", occurred_at="2026-08-05T10:00:00",
        subject="Un vrai message", project_id=project_id,
    )
    db.delete_project(conn, project_id, reason="Never proposed.")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM crm.project").fetchone()[0] == 0
    row = conn.execute("SELECT * FROM crm.interaction WHERE id = %s", (kept,)).fetchone()
    assert row["subject"] == "Un vrai message"
    assert row["project_id"] is None


def test_deleting_a_document_erases_the_file(web, conn, client_id, root) -> None:
    web.post(
        f"/dossier/{client_id}/documents",
        files={"file": ("a.txt", b"to be removed", "text/plain")},
        follow_redirects=False,
    )
    row = db.attachments_for(conn, client_id)[0]
    stored = list((root / "data" / "attachments").rglob(f"{row['sha256']}*"))
    assert stored and stored[0].exists()
    web.post(f"/attachment/{row['id']}/delete", data={"reason": "Wrong client."},
             follow_redirects=False)
    assert db.attachments_for(conn, client_id) == []
    assert not stored[0].exists()


def test_a_document_two_clients_hold_is_not_erased_from_under_the_other(
    conn, client_id
) -> None:
    """Storage is content-addressed, so two clients may legitimately hold the
    same standard contract and unlinking would take theirs too."""
    other_company = conn.execute(
        "INSERT INTO core.company (uid, name, canton, source_url, method)"
        " VALUES ('CHE999', 'Autre SA', 'VD', 'x', 'zefix') RETURNING id"
    ).fetchone()["id"]
    other = db.promote_to_client(conn, other_company)
    for owner in (client_id, other):
        db.add_attachment(
            conn, owner,
            {"filename": "contrat-type.txt", "bytes": 4, "sha256": "f" * 64,
             "text": "same"},
        )
    conn.commit()
    removed = db.delete_attachment(conn,
        db.attachments_for(conn, client_id)[0]["id"], reason="not ours")
    conn.commit()
    assert removed is not None
    assert db.sha_is_orphaned(conn, "f" * 64) is False


# --- the stylesheet defect this work uncovered --------------------------------


def test_a_sections_own_css_reaches_the_browser_intact(web, client_id) -> None:
    """Section stylesheets used to be passed inside the body, where every brace
    had to be doubled to survive an f-string. The doubled braces then reached
    the browser: `.crumbs {{ ... }}` is a selector followed by a block whose
    only contents are an invalid declaration, so CSS error recovery discarded
    every rule. The Clients and Settings pages rendered with none of their own
    styling and nothing said so."""
    for path in ("/clients", "/settings", f"/client/{client_id}/dossier"):
        body = web.get(path).text
        assert "{{" not in body, path
        assert "}}" not in body, path
    assert ".dossier-strip {" in web.get(f"/client/{client_id}").text


def test_an_uploaded_filename_is_escaped_in_the_reply(web, conn, client_id) -> None:
    """The HTMX reply names the files it stored, and a file is named by whoever
    sent it."""
    reply = web.post(
        f"/dossier/{client_id}/documents",
        files={"file": ("<script>alert(1)</script>.txt", b"plain", "text/plain")},
        headers={"HX-Request": "true"},
    )
    assert reply.status_code == 200
    assert "<script>" not in reply.text and "&lt;script&gt;" in reply.text
