"""Findings that inform a judgement but must never be quoted back.

Web research turns up debt-enforcement records: a poursuite against a
one-person Sàrl was the case that prompted this. It is worth knowing, because
a company under enforcement is a different prospect. It must not be repeated,
because for a single-manager Sàrl the company's finances are an identifiable
person's finances, the source is the open web rather than the register, and an
email alluding to it would be indefensible however true it is.

Both halves are tested. Losing the collection would make the fit score worse;
losing the containment would put it in a stranger's inbox.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection, Row
from arpenteur.stages import enrich


@pytest.fixture
def conn(db_path: Path) -> Connection:
    connection = db.open_db(db_path)
    db.upsert_company(
        connection, {"uid": "CHE1", "name": "Solo Sàrl", "method": "zefix"}
    )
    db.save_site(
        connection,
        1,
        {
            "status": "found",
            "url": "https://solo.ch",
            "summary": "Conseil en finance d'entreprise.",
            "signals": ["site sans HTTPS"],
            "restricted": ["poursuite de la Ville de Genève, taxe 2021-2023"],
            "method": "web-search",
        },
    )
    connection.commit()
    return connection


def test_the_finding_is_kept(conn) -> None:
    """Collected, because it changes whether a company is worth approaching."""
    stored = conn.execute("SELECT restricted FROM intel.site").fetchone()[0]
    assert "poursuite" in stored[0]


def test_the_draft_view_cannot_see_it(conn) -> None:
    """The one accessor any outward-facing path is required to use."""
    view = db.site_for_draft(conn, 1)
    assert view is not None
    assert "restricted" not in view
    assert "poursuite" not in json.dumps(view, ensure_ascii=False)


def test_the_draft_view_still_carries_what_drafting_needs(conn) -> None:
    """Containment that emptied the row would just be deleted by the next person."""
    view = db.site_for_draft(conn, 1)
    assert view["url"] == "https://solo.ch"
    assert "finance" in view["summary"]
    assert view["signals"] == ["site sans HTTPS"]


def test_a_company_with_no_research_yields_nothing_rather_than_raising(conn) -> None:
    db.upsert_company(conn, {"uid": "CHE2", "name": "Other Sàrl", "method": "zefix"})
    conn.commit()
    assert db.site_for_draft(conn, 2) is None


def test_the_model_is_given_somewhere_to_put_it() -> None:
    """Without a field of its own the model puts it in signals, which is
    what happened live and is precisely the column drafting reads."""
    assert "restricted" in enrich.SCHEMA["properties"]
    assert "restricted" in enrich.SCHEMA["required"]
    assert "poursuite" in enrich.SYSTEM
