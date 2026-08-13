"""Notes: the operator's own writing about a client, in Markdown.

Three tables already hold text about a client and none of them is this. The
dossier is fixed sections answering fixed questions; an interaction is what
happened on a date; an attachment is a document the *client* sent, stored by
digest and never edited. A note is long-form, revised over weeks, and belongs
to the engagement rather than to any one day of it.

What is tested here is mostly the seams, because those are what make it usable
rather than merely stored: a file edited in an editor and imported again must
update in place and say when nothing changed, a note must reach the stages that
suggest an approach, and the Markdown must arrive on a page without bringing
whatever else was pasted with it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arpenteur import db, markdown
from arpenteur.config import Territory
from arpenteur.console.app import create_app
from arpenteur.kernel.db import Connection
from arpenteur.modules.clients import store as clients

NOTE = """# Conditions d'accès · audit

Guide pour rédiger la proposition de mandat.

## 1. Séquence

- Le périmètre d'abord, l'accès ensuite.
  - **Compte invité nominatif**, jamais partagé.
  - Lecture seule, borné dans le temps.

## 2. Poste de travail

1. Disque chiffré.
2. Aucune donnée client sur mon disque.
"""


@pytest.fixture
def settings(db_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("VD",), legal_forms=("0110",), name="vaud"),
        segments={},
    )


@pytest.fixture
def conn(settings) -> Connection:
    connection = db.open_db(settings.database_url)
    db.upsert_company(
        connection,
        {"uid": "CHE103895771", "name": "Fondation Bois-Gentil", "canton": "VD",
         "status": "ACTIVE", "method": "zefix", "hydrated": True},
    )
    connection.commit()
    return connection


@pytest.fixture
def client_id(conn) -> int:
    cid = db.promote_to_client(conn, 1)
    conn.commit()
    return cid


@pytest.fixture
def client(settings, conn) -> TestClient:
    return TestClient(create_app(settings))


def add(conn, client_id: int, body: str = NOTE, **fields) -> tuple[int, str]:
    return clients.save_note(
        conn, client_id, title=markdown.title_of(body), body=body,
        digest=markdown.digest(body), **fields,
    )


# --- reading a note ------------------------------------------------------------


def test_the_title_is_the_note_s_own_first_heading() -> None:
    """A note written in an editor already begins with its title, and making
    somebody type it twice is how the two come to disagree."""
    assert markdown.title_of(NOTE) == "Conditions d'accès · audit"


def test_a_note_with_no_heading_falls_back_to_its_first_line() -> None:
    assert markdown.title_of("Rappel: relancer Nils avant le 3.\n\nEt puis.") == (
        "Rappel: relancer Nils avant le 3."
    )


def test_a_summary_skips_the_scaffolding() -> None:
    """A listing whose every line reads "1. Séquence" tells you the shape of
    the document and nothing about it."""
    assert markdown.summary(NOTE).startswith("Guide pour rédiger")


def test_markdown_becomes_html_including_the_nested_lists() -> None:
    html = markdown.render(NOTE)
    assert "<h1>" in html and "<ol>" in html and "<strong>" in html
    assert html.count("<ul>") == 2  # the outer list and the nested one


def test_raw_html_in_a_note_is_escaped() -> None:
    """A deliberate departure from CommonMark, which specifies the opposite.
    These are rendered into the console's own pages, a note may be pasted from
    anywhere, and no note has ever needed a div."""
    html = markdown.render("Bonjour <script>alert(1)</script> et <b>gras</b>")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<b>gras</b>" not in html


def test_a_javascript_link_does_not_become_a_link() -> None:
    """It comes back as the literal text somebody typed, which is both honest
    and inert. What must not happen is an `href` carrying it."""
    html = markdown.render("[x](javascript:alert(1))")
    assert "<a " not in html and "href" not in html


# --- storing one ---------------------------------------------------------------


def test_a_note_is_stored_exactly_as_written(conn, client_id) -> None:
    """The stored form is the Markdown. HTML is made on the way to a page and
    never kept: a stored rendering drifts the first time the source is edited."""
    note_id, what = add(conn, client_id)
    conn.commit()
    assert what == "created"
    assert clients.note(conn, note_id)["body"] == NOTE


def test_importing_the_same_file_again_updates_it(conn, client_id) -> None:
    first, _ = add(conn, client_id, source_path="/notes/audit.md")
    second, what = add(
        conn, client_id, NOTE + "\n## 3. Ajouté\n", source_path="/notes/audit.md"
    )
    conn.commit()
    assert first == second and what == "updated"
    assert len(clients.notes_for(conn, client_id)) == 1
    assert "Ajouté" in clients.note(conn, first)["body"]


def test_an_unchanged_file_says_so(conn, client_id) -> None:
    """An import that reports "updated" for a file nobody touched teaches its
    user to ignore the word."""
    add(conn, client_id, source_path="/notes/audit.md")
    conn.commit()
    _id, what = add(conn, client_id, source_path="/notes/audit.md")
    assert what == "unchanged"


def test_two_notes_typed_here_are_two_notes(conn, client_id) -> None:
    """The dedup key is the source path, and absent must not mean identical."""
    first, _ = add(conn, client_id, "# Un\n\nfoo")
    second, _ = add(conn, client_id, "# Deux\n\nbar")
    conn.commit()
    assert first != second and len(clients.notes_for(conn, client_id)) == 2


def test_pinned_notes_come_first(conn, client_id) -> None:
    """A standing brief among forty meeting notes is unfindable by date."""
    add(conn, client_id, "# Récent\n\nfoo")
    old, _ = add(conn, client_id, "# Le cadre\n\nbar", pinned=True)
    conn.commit()
    assert [n["id"] for n in clients.notes_for(conn, client_id)][0] == old


def test_notes_are_searched_by_what_is_in_them(conn, client_id) -> None:
    """The only way a folder of forty is usable."""
    add(conn, client_id)
    conn.commit()
    assert clients.notes_for(conn, client_id, q="chiffré")
    assert clients.notes_for(conn, client_id, q="facturation") == []


def test_removing_a_note_leaves_a_line_in_the_record(conn, client_id) -> None:
    """A file that can be emptied without trace is not a record."""
    note_id, _ = add(conn, client_id)
    conn.commit()
    clients.delete_note(conn, note_id, reason="Écrit pour le mauvais client")
    conn.commit()
    assert clients.notes_for(conn, client_id) == []
    subjects = [e["subject"] or "" for e in db.timeline(conn, client_id)]
    assert any("Removed: note" in s for s in subjects)


# --- what the stages read ------------------------------------------------------


def test_a_note_reaches_the_stage_that_suggests_an_approach(conn, client_id) -> None:
    """The seam that makes this more than storage. A note states what was
    decided and what not to say in the room, and a suggestion that contradicts
    it is worse than no suggestion."""
    from arpenteur.stages import advise

    add(conn, client_id)
    conn.commit()
    client, company, sections, projects, papers, officers, notes = advise.gather(
        conn, client_id
    )
    prompt, read = advise.build_prompt(
        company, client, sections, projects, papers, officers, "", notes=notes
    )
    assert "YOUR OWN NOTES ON THIS CLIENT" in prompt
    assert "Compte invité nominatif" in prompt
    # What the suggestion read, recorded on the run: the first question asked
    # of a number that turns out to be wrong is what produced it.
    assert [n["title"] for n in read["notes"]] == ["Conditions d'accès · audit"]
    assert read["notes"][0]["truncated"] is False


def test_the_prompt_says_a_note_outranks_what_it_inferred(conn, client_id) -> None:
    """A document the client sent is evidence; a note is instruction, and the
    model has to be able to tell them apart."""
    from arpenteur.stages import advise

    add(conn, client_id)
    conn.commit()
    client, company, sections, projects, papers, officers, notes = advise.gather(
        conn, client_id
    )
    prompt, _ = advise.build_prompt(
        company, client, sections, projects, papers, officers, "", notes=notes
    )
    said = prompt[prompt.index("YOUR OWN NOTES"):]
    assert "Written by the consultant" in said
    assert "outrank" in said
    # And above the client's own papers, which is where the ordering matters.
    assert prompt.index("YOUR OWN NOTES") < prompt.index("RATE CARD")


# --- through the console -------------------------------------------------------


def test_a_note_can_be_pasted_into_the_console(client, conn, client_id) -> None:
    reply = client.post(
        f"/client/{client_id}/notes", data={"body": NOTE, "tags": "accès, contrat"},
        follow_redirects=False,
    )
    assert reply.status_code == 303
    held = clients.notes_for(conn, client_id)
    assert [n["title"] for n in held] == ["Conditions d'accès · audit"]
    assert held[0]["tags"] == ["accès", "contrat"]


def test_a_pasted_note_lands_in_the_record(client, conn, client_id) -> None:
    client.post(f"/client/{client_id}/notes", data={"body": NOTE})
    subjects = [e["subject"] or "" for e in db.timeline(conn, client_id)]
    assert any("Note: Conditions d'accès" in s for s in subjects)


def test_a_markdown_file_is_uploaded_and_keeps_its_name(client, conn, client_id) -> None:
    client.post(
        f"/client/{client_id}/notes",
        files={"file": ("audit.md", NOTE.encode("utf-8"), "text/markdown")},
    )
    held = clients.notes_for(conn, client_id)
    assert held and held[0]["source_path"] == "audit.md"


def test_a_file_that_is_not_text_is_refused_with_the_reason(
    client, conn, client_id
) -> None:
    """A PDF belongs under the dossier as a document, and saying so is more
    use than a stack trace."""
    reply = client.post(
        f"/client/{client_id}/notes",
        files={"file": ("scan.pdf", b"%PDF-1.7\x00\xff\xfe binary", "application/pdf")},
        follow_redirects=True,
    )
    assert "not UTF-8" in reply.text and "as a document" in reply.text
    assert clients.notes_for(conn, client_id) == []


def test_the_page_renders_the_markdown(client, conn, client_id) -> None:
    add(conn, client_id)
    conn.commit()
    body = client.get(f"/client/{client_id}/notes").text
    assert "<h2>1. Séquence</h2>" in body
    assert "<strong>Compte invité nominatif</strong>" in body


def test_the_markdown_can_be_fetched_back_as_itself(client, conn, client_id) -> None:
    """A stable URL that is the document rather than a page about it, so an
    agent has something to fetch and a note typed here can be got back out."""
    note_id, _ = add(conn, client_id)
    conn.commit()
    reply = client.get(f"/note/{note_id}.md")
    assert reply.status_code == 200
    assert reply.text == NOTE
    assert reply.headers["content-type"].startswith("text/markdown")


def test_the_client_page_says_how_many_notes_there_are(client, conn, client_id) -> None:
    add(conn, client_id)
    conn.commit()
    body = client.get(f"/client/{client_id}").text
    # The apostrophe arrives escaped, which is the whole point of `esc`.
    assert "Notes (1)" in body and "Conditions d&#x27;acc" in body


def test_editing_a_note_keeps_its_title_in_step(client, conn, client_id) -> None:
    note_id, _ = add(conn, client_id)
    conn.commit()
    client.post(f"/note/{note_id}", data={"body": "# Renommé\n\nAutre chose."})
    assert clients.note(conn, note_id)["title"] == "Renommé"
