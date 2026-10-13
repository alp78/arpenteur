"""Folders: the client's own filing over documents already stored.

The thing worth testing here is not that a row can be inserted. It is the two
promises the schema makes and that every filing system gets wrong sooner or
later: that naming a folder twice gives you the folder you already have rather
than a twin, and that removing a folder removes the filing and not the papers.

The third is a confidentiality one. A folder belongs to a client, and a
document moved into another client's folder is not a tidying mistake to be
reported kindly; it is a leak, and it is refused.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import load_settings
from arpenteur.console.app import create_app
from arpenteur.kernel.db import Connection

pytestmark = pytest.mark.pg

TOML = """
[survey]
cantons = ["VD"]
legal_forms = ["0110"]

[segments]
sme-owner = "A small owner-run business."
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "arpenteur.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "campaign.md").write_text("# Campaign\n\nx\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def conn(root: Path, db_path: str) -> Connection:
    connection = db.open_db(db_path)
    for uid, name in (
        ("CHE100000011", "Fondation Clair-Logis"),
        ("CHE100000001", "Autre Fondation"),
    ):
        connection.execute(
            "INSERT INTO core.company (uid, name, canton, legal_form_uid,"
            " legal_form_label, legal_seat, status, purpose, source_url, method,"
            " fetched_at, first_seen, hydrated_at)"
            " VALUES (%s, %s, 'VD', '0110', 'Fondation', 'Lausanne', 'ACTIVE',"
            " 'aide aux personnes agees', 'https://example.test', 'zefix',"
            " '2026-08-12', '2026-08-12', '2026-08-12')",
            (uid, name),
        )
    connection.commit()
    return connection


@pytest.fixture
def client_id(conn: Connection) -> int:
    company_id = conn.execute(
        "SELECT id FROM core.company WHERE uid = 'CHE100000011'"
    ).fetchone()["id"]
    new_id = db.promote_to_client(conn, company_id)
    conn.commit()
    return new_id


@pytest.fixture
def other_client_id(conn: Connection) -> int:
    company_id = conn.execute(
        "SELECT id FROM core.company WHERE uid = 'CHE100000001'"
    ).fetchone()["id"]
    new_id = db.promote_to_client(conn, company_id)
    conn.commit()
    return new_id


@pytest.fixture
def web(root: Path, conn) -> TestClient:
    return TestClient(create_app(load_settings(root)))


def _doc(conn: Connection, client_id: int, name: str, **extra) -> int:
    attachment_id, _existed = db.add_attachment(conn, client_id, {
        "filename": name, "media_type": "text/plain", "bytes": 11,
        "sha256": name, "text": "some words", **extra,
    })
    conn.commit()
    return attachment_id


# --- making one ---------------------------------------------------------------


def test_a_folder_is_made_on_a_client(conn, client_id) -> None:
    folder_id, existed = db.add_folder(
        conn, client_id, "Dossier de consultation", note="Remis le 09.09.2026"
    )
    conn.commit()
    assert not existed
    row = db.folder(conn, folder_id)
    assert row["name"] == "Dossier de consultation"
    assert row["note"] == "Remis le 09.09.2026"


def test_naming_a_folder_twice_is_the_same_folder(conn, client_id) -> None:
    """A batch that arrives in two deliveries is one folder.

    The useful answer to the second delivery is the first folder, not an error
    and not a twin: two folders of the same name is how a filing system stops
    being one.
    """
    first, _ = db.add_folder(conn, client_id, "Comptes 2025")
    again, existed = db.add_folder(conn, client_id, "  comptes 2025  ")
    conn.commit()
    assert existed
    assert again == first
    assert len(db.folders_for(conn, client_id)) == 1


def test_a_folder_needs_a_name(conn, client_id) -> None:
    with pytest.raises(ValueError):
        db.add_folder(conn, client_id, "   ")


def test_two_clients_may_each_have_a_folder_of_the_same_name(
    conn, client_id, other_client_id
) -> None:
    """The uniqueness is per client. "Comptes 2025" is what half of them will
    call it, and refusing the second one would be the tool deciding whose
    filing counts."""
    mine, _ = db.add_folder(conn, client_id, "Comptes 2025")
    theirs, existed = db.add_folder(conn, other_client_id, "Comptes 2025")
    conn.commit()
    assert not existed
    assert mine != theirs


# --- what is in one -----------------------------------------------------------


def test_a_document_says_which_folder_it_is_in(conn, client_id) -> None:
    """The name is joined in, not the id. Every caller holding a document wants
    to say where it was filed, and none of them wants a second query."""
    folder_id, _ = db.add_folder(conn, client_id, "Consultation")
    _doc(conn, client_id, "cahier.txt", folder_id=folder_id)
    row = db.attachments_for(conn, client_id)[0]
    assert row["folder"] == "Consultation"
    assert row["folder_id"] == folder_id


def test_a_folder_counts_what_is_in_it(conn, client_id) -> None:
    folder_id, _ = db.add_folder(conn, client_id, "Consultation")
    _doc(conn, client_id, "a.txt", folder_id=folder_id)
    _doc(conn, client_id, "b.txt", folder_id=folder_id)
    _doc(conn, client_id, "loose.txt")
    listed = db.folders_for(conn, client_id)[0]
    assert listed["documents"] == 2
    assert db.attachments_for(conn, client_id, folder_id=folder_id).__len__() == 2
    assert [r["filename"] for r in
            db.attachments_for(conn, client_id, unfiled=True)] == ["loose.txt"]


def test_an_empty_folder_counts_zero_rather_than_vanishing(conn, client_id) -> None:
    """An empty folder is a legitimate state: it is where the next delivery
    goes. A LEFT JOIN says so; an inner one would hide it until used."""
    db.add_folder(conn, client_id, "En attente")
    conn.commit()
    assert db.folders_for(conn, client_id)[0]["documents"] == 0


def test_a_document_is_moved_into_a_folder_and_back_out(conn, client_id) -> None:
    folder_id, _ = db.add_folder(conn, client_id, "Consultation")
    doc = _doc(conn, client_id, "cahier.txt")
    db.file_attachment(conn, doc, folder_id)
    conn.commit()
    assert db.attachments_for(conn, client_id)[0]["folder"] == "Consultation"
    db.file_attachment(conn, doc, None)
    conn.commit()
    assert db.attachments_for(conn, client_id)[0]["folder"] is None


def test_a_document_cannot_be_filed_in_another_clients_folder(
    conn, client_id, other_client_id
) -> None:
    """Not a friendly error. A paper in the wrong client's folder is a
    confidentiality failure, and it is refused here rather than trusted to
    whichever form happened to submit it."""
    theirs, _ = db.add_folder(conn, other_client_id, "Leur dossier")
    doc = _doc(conn, client_id, "cahier.txt")
    with pytest.raises(ValueError):
        db.file_attachment(conn, doc, theirs)


# --- removing one -------------------------------------------------------------


def test_deleting_a_folder_keeps_the_documents(conn, client_id) -> None:
    """The whole point of the column being ON DELETE SET NULL.

    Tidying the filing is not the same act as destroying the papers, and a
    delete that quietly did both is the button nobody dares press.
    """
    folder_id, _ = db.add_folder(conn, client_id, "Consultation")
    _doc(conn, client_id, "cahier.txt", folder_id=folder_id)
    db.delete_folder(conn, folder_id, reason="Filed under the project instead")
    conn.commit()
    rows = db.attachments_for(conn, client_id)
    assert [r["filename"] for r in rows] == ["cahier.txt"]
    assert rows[0]["folder"] is None
    assert db.folder(conn, folder_id) is None


def test_deleting_a_folder_leaves_a_tombstone_saying_how_many_came_loose(
    conn, client_id
) -> None:
    folder_id, _ = db.add_folder(conn, client_id, "Consultation")
    _doc(conn, client_id, "a.txt", folder_id=folder_id)
    _doc(conn, client_id, "b.txt", folder_id=folder_id)
    db.delete_folder(conn, folder_id, reason="Made it twice by mistake")
    conn.commit()
    said = [e["subject"] for e in db.timeline(conn, client_id)]
    assert any("Removed: folder Consultation" in s and "2 document(s)" in s
               for s in said)


def test_deleting_the_client_takes_the_folders(conn, client_id) -> None:
    """CASCADE from the client, unlike the documents. A folder means nothing
    without the client it files for."""
    db.add_folder(conn, client_id, "Consultation")
    conn.commit()
    conn.execute("DELETE FROM crm.client WHERE id = %s", (client_id,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS n FROM crm.folder").fetchone()["n"] == 0


# --- through the console ------------------------------------------------------


def test_a_drop_can_make_its_folder_as_it_arrives(web, conn, client_id) -> None:
    """Naming the folder in the drop is the ordinary case: the batch and the
    folder for it turn up at the same moment."""
    reply = web.post(
        f"/dossier/{client_id}/documents",
        files=[
            ("file", ("cahier.txt", b"Cahier des charges", "text/plain")),
            ("file", ("annexe.txt", b"Annexe firewall", "text/plain")),
        ],
        data={"kind": "tender", "new_folder": "Consultation 09.2026"},
        follow_redirects=False,
    )
    assert reply.status_code == 303
    listed = db.folders_for(conn, client_id)
    assert [f["name"] for f in listed] == ["Consultation 09.2026"]
    assert listed[0]["documents"] == 2


def test_a_second_drop_into_the_same_name_does_not_make_a_second_folder(
    web, conn, client_id
) -> None:
    for name, body in (("a.txt", b"first"), ("b.txt", b"second")):
        web.post(
            f"/dossier/{client_id}/documents",
            files={"file": (name, body, "text/plain")},
            data={"new_folder": "Consultation 09.2026"},
            follow_redirects=False,
        )
    assert len(db.folders_for(conn, client_id)) == 1
    assert db.folders_for(conn, client_id)[0]["documents"] == 2


def test_a_folder_can_be_made_empty_from_the_page(web, conn, client_id) -> None:
    reply = web.post(
        f"/dossier/{client_id}/folder",
        data={"name": "En attente", "note": "Where the next delivery goes"},
        follow_redirects=False,
    )
    assert reply.status_code == 303
    assert [f["name"] for f in db.folders_for(conn, client_id)] == ["En attente"]


def test_the_page_groups_the_documents_under_their_folders(
    web, conn, client_id
) -> None:
    folder_id, _ = db.add_folder(conn, client_id, "Consultation 09.2026")
    _doc(conn, client_id, "cahier.txt", folder_id=folder_id)
    _doc(conn, client_id, "vieux.txt")
    page = web.get(f"/client/{client_id}/dossier").text
    assert "Consultation 09.2026" in page
    assert "Not in a folder" in page
    # The unfiled one is reachable and says so, rather than being hidden
    # because it happens not to be filed.
    assert "vieux.txt" in page


def test_a_document_is_moved_from_the_page(web, conn, client_id) -> None:
    folder_id, _ = db.add_folder(conn, client_id, "Consultation")
    doc = _doc(conn, client_id, "cahier.txt")
    reply = web.post(
        f"/attachment/{doc}/folder", data={"folder_id": str(folder_id)},
        follow_redirects=False,
    )
    assert reply.status_code == 303
    assert db.attachments_for(conn, client_id)[0]["folder"] == "Consultation"


def test_the_page_deletes_the_folder_and_keeps_the_paper(
    web, conn, client_id
) -> None:
    folder_id, _ = db.add_folder(conn, client_id, "Consultation")
    _doc(conn, client_id, "cahier.txt", folder_id=folder_id)
    reply = web.post(
        f"/folder/{folder_id}/delete", data={"reason": "Wrong name"},
        follow_redirects=False,
    )
    assert reply.status_code == 303
    assert db.folders_for(conn, client_id) == []
    assert [r["filename"] for r in db.attachments_for(conn, client_id)] == [
        "cahier.txt"
    ]


# --- what the model is told ---------------------------------------------------


def test_the_prompt_says_which_folder_a_document_came_in(conn, client_id) -> None:
    """Ten papers that arrived as one consultation dossier are evidence of a
    different weight from ten collected one at a time over a year, and the
    model cannot tell which it is holding from the filenames."""
    from arpenteur.stages import advise

    folder_id, _ = db.add_folder(conn, client_id, "Consultation 09.2026")
    _doc(conn, client_id, "cahier.txt", folder_id=folder_id, kind="tender")
    client = db.client(conn, client_id)
    company = conn.execute(
        "SELECT * FROM core.company WHERE id = %s", (client["company_id"],)
    ).fetchone()
    prompt, _read = advise.build_prompt(
        company, client, {}, [],
        db.attachments_for(conn, client_id, with_text=True), [], "",
    )
    assert "filed in Consultation 09.2026" in prompt
