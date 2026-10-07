"""Fit scoring: the first stage that takes a position rather than describing.

Two things are worth pinning here beyond the plumbing.

A score is only meaningful against the brief it was taken against, so the
brief's digest is stored on every row and a rewritten brief must invalidate
the scores rather than leave old and new sitting together looking equally
current.

And the judge writes the approach advice, which is the one output that gets
put in front of a stranger. It must never see the restricted material, because
the surest way to keep debt-enforcement findings out of an email is for the
writer not to know them.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.config import Settings
from arpenteur.stages import match

# The reply as the model now returns it: four components, each with its own
# score and the evidence it was scored on. The two computed components are
# absent on purpose, because the model cannot see what they are counted from.
REPLY: dict[str, Any] = {
    "fit": {
        "score": 85, "reasoning": "Fiduciaire de 20 personnes, dans la cible.",
        "for": ["fiduciaire de 10 à 80 personnes"],
        "against": ["en croissance, peut-être déjà accompagnée"],
    },
    "need": {
        "score": 80, "reasoning": "Site ancien, aucun partenaire IT nommé.",
        "for": ["site vitrine ancien", "aucun partenaire informatique nommé"],
        "against": [],
    },
    "timing": {
        "score": 60, "reasoning": "Rien de daté dans la matière.",
        "for": [], "against": ["aucun signal de calendrier"],
    },
    "value": {
        "score": 70, "reasoning": "Vingt personnes, plusieurs sites.",
        "for": ["vingt personnes"], "against": [],
    },
    "reasoning": "Fiduciaire de 20 personnes, site ancien, aucun partenaire IT nommé.",
    "advice": "Bonjour, j'ai vu que votre site date de 2016...",
}


class FakeProvider:
    name = "fake"

    def __init__(self, reply: dict[str, Any] | None = None, fail: bool = False) -> None:
        self.reply = reply if reply is not None else REPLY
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("model unavailable")
        return self.reply

    def complete(self, **_kw: Any) -> str:  # pragma: no cover
        raise AssertionError("match must use complete_json")


@pytest.fixture
def brief(tmp_path: Path) -> Path:
    path = tmp_path / "campaign.md"
    path.write_text("We serve fiduciaires of 10 to 80 people.\n", encoding="utf-8")
    return path


@pytest.fixture
def settings(tmp_path: Path, brief: Path) -> SimpleNamespace:
    fake = SimpleNamespace(
        root=tmp_path,
        brief_path=brief,
        statuses=("ACTIVE",),
        llm=SimpleNamespace(match_model="claude-sonnet-5", draft_model="claude-sonnet-5",
                            match_max_tokens=4000),
    )
    fake.status_clause = lambda column="c.status": Settings.status_clause(fake, column)
    return fake


@pytest.fixture
def conn(db_path: Path) -> Connection:
    connection = db.open_db(db_path)
    db.upsert_company(
        connection,
        {"uid": "CHE1", "name": "Fiduciaire Sàrl", "canton": "GE", "legal_seat": "Genève",
         "purpose": "Tenue de comptabilité.", "status": "ACTIVE", "hydrated": True,
         "method": "zefix"},
    )
    db.save_site(
        connection, 1,
        {"status": "found", "url": "https://f.ch", "summary": "Fiduciaire genevoise.",
         "signals": ["site ancien"],
         "restricted": ["poursuite de la Ville de Genève"], "method": "web-search"},
    )
    connection.commit()
    return connection


def match_with(monkeypatch, provider: FakeProvider, settings, conn, **kwargs):
    monkeypatch.setattr(match, "get_provider", lambda _s: provider)
    return match.run(settings, conn, log=lambda _m: None, **kwargs)


# --- the brief ----------------------------------------------------------------


def test_no_brief_is_an_error_not_an_empty_prompt(settings, conn, tmp_path) -> None:
    """Scoring against nothing would produce confident numbers built on air."""
    settings.brief_path = tmp_path / "absent.md"
    with pytest.raises(RuntimeError, match="No campaign brief"):
        match.run(settings, conn, log=lambda _m: None)


def test_the_brief_reaches_the_model_verbatim(monkeypatch, settings, conn) -> None:
    provider = FakeProvider()
    match_with(monkeypatch, provider, settings, conn)
    assert "fiduciaires of 10 to 80 people" in provider.calls[0]["user"]


def test_rewriting_the_brief_invalidates_the_scores(
    monkeypatch, settings, conn, brief
) -> None:
    """The reason the digest is stored. Otherwise a re-run would skip every
    company as "already scored" and the new brief would never take effect."""
    provider = FakeProvider()
    match_with(monkeypatch, provider, settings, conn)
    assert len(provider.calls) == 1

    match_with(monkeypatch, provider, settings, conn)
    assert len(provider.calls) == 1, "an unchanged brief must not re-score"

    brief.write_text("We serve engineering offices instead.\n", encoding="utf-8")
    match_with(monkeypatch, provider, settings, conn)
    assert len(provider.calls) == 2, "a rewritten brief must re-score"


# --- what the judge may see ---------------------------------------------------


def test_the_judge_never_sees_restricted_material(monkeypatch, settings, conn) -> None:
    """It writes the approach advice. The surest way to keep a poursuite out
    of an email is for the writer not to know about it."""
    provider = FakeProvider()
    match_with(monkeypatch, provider, settings, conn)
    assert "poursuite" not in provider.calls[0]["user"]
    assert "site ancien" in provider.calls[0]["user"], "the ordinary signals must arrive"


def test_a_company_with_no_web_research_says_so(monkeypatch, settings, conn) -> None:
    """Silence about the web would read as "nothing found there", which is a
    different fact from "nobody looked"."""
    db.upsert_company(
        conn,
        {"uid": "CHE2", "name": "Autre Sàrl", "canton": "GE", "status": "ACTIVE",
         "purpose": "Conseil.", "hydrated": True, "method": "zefix"},
    )
    conn.commit()
    provider = FakeProvider()
    match_with(monkeypatch, provider, settings, conn, researched_only=False)
    prompts = [c["user"] for c in provider.calls]
    assert any("not done" in p for p in prompts)


# --- what is stored -----------------------------------------------------------


def test_a_score_is_stored_with_its_reasoning_and_advice(
    monkeypatch, settings, conn
) -> None:
    match_with(monkeypatch, FakeProvider(), settings, conn)
    row = conn.execute("SELECT * FROM intel.fit").fetchone()
    # The model scored this company 85 for fit and 80 for need, and it lands
    # at medium rather than high because nobody can reach it and nobody knows
    # anyone there. That is the rework: an excellent prospect with no way in
    # is not the same as an excellent prospect, and one number said it was.
    assert row["band"] == "medium" and 55 <= row["score"] <= 70
    assert "aucun partenaire" in row["reasoning"]
    assert row["advice"].startswith("Bonjour")
    assert row["matched"]
    assert row["against"], "reservations must survive a high score"
    assert row["source"] == "model" and row["brief_hash"]


def test_a_manual_score_survives_re_scoring(monkeypatch, settings, conn) -> None:
    """Same guarantee as a manual segment, for the same reason."""
    match_with(monkeypatch, FakeProvider(), settings, conn)
    db.set_fit_manually(conn, 1, score=10, band="low", reasoning="already a client")
    conn.commit()

    match_with(monkeypatch, FakeProvider(), settings, conn, rescore=True)
    row = conn.execute("SELECT * FROM intel.fit").fetchone()
    assert (row["score"], row["source"]) == (10, "manual")


# --- selection and resilience -------------------------------------------------


def test_by_default_only_researched_companies_are_scored(
    monkeypatch, settings, conn
) -> None:
    """Scoring on the register alone is possible but weaker, so it is opt-in."""
    db.upsert_company(
        conn,
        {"uid": "CHE2", "name": "Autre Sàrl", "canton": "GE", "status": "ACTIVE",
         "hydrated": True, "method": "zefix"},
    )
    conn.commit()
    stats = match_with(monkeypatch, FakeProvider(), settings, conn)
    assert stats["considered"] == 1


def test_a_company_in_liquidation_is_not_scored(monkeypatch, settings, conn) -> None:
    db.upsert_company(
        conn,
        {"uid": "CHE9", "name": "Mourante Sàrl", "canton": "GE",
         "status": "BEING_CANCELLED", "hydrated": True, "method": "zefix"},
    )
    db.save_site(conn, 2, {"status": "found", "url": "https://m.ch", "method": "web-search"})
    conn.commit()
    stats = match_with(monkeypatch, FakeProvider(), settings, conn)
    assert stats["considered"] == 1


def test_one_failure_does_not_end_the_run(monkeypatch, settings, conn) -> None:
    stats = match_with(monkeypatch, FakeProvider(fail=True), settings, conn)
    assert stats["failed"] == 1 and stats["scored"] == 0


def test_dry_run_needs_no_credential(monkeypatch, settings, conn) -> None:
    def no_credential(_settings):
        raise RuntimeError("No Anthropic credential found.")

    monkeypatch.setattr(match, "get_provider", no_credential)
    stats = match.run(settings, conn, dry_run=True, log=lambda _m: None)
    assert stats["considered"] == 1


def test_the_schema_is_a_shape_the_api_accepts() -> None:
    assert match.SCHEMA["additionalProperties"] is False
    assert set(match.SCHEMA["required"]) == set(match.SCHEMA["properties"])
    for name, prop in match.SCHEMA["properties"].items():
        assert not {"minLength", "maxLength", "minimum", "maximum"} & set(prop), name


def test_re_running_enrich_makes_a_score_stale(monkeypatch, settings, conn) -> None:
    """The second staleness gap, found the honest way.

    Re-enriching three companies to re-file some restricted material left
    their scores reasoning about text that no longer existed. Nothing was
    wrong with the numbers and there was no way to tell, which is the same
    failure as a score taken against a rewritten brief.
    """
    provider = FakeProvider()
    match_with(monkeypatch, provider, settings, conn)
    assert len(provider.calls) == 1

    match_with(monkeypatch, provider, settings, conn)
    assert len(provider.calls) == 1, "unchanged inputs must not re-score"

    # What enrich --refresh does: replaces the research wholesale.
    db.save_site(
        conn, 1,
        {"status": "found", "url": "https://f.ch", "summary": "Nouvelle lecture.",
         "signals": ["autre signal"], "method": "web-search"},
    )
    conn.commit()

    match_with(monkeypatch, provider, settings, conn)
    assert len(provider.calls) == 2, "new research must re-score"


def test_a_manual_score_is_never_stale(monkeypatch, settings, conn) -> None:
    """A person is not an input that expired."""
    match_with(monkeypatch, FakeProvider(), settings, conn)
    db.set_fit_manually(conn, 1, score=10, band="low", reasoning="already a client")
    db.save_site(
        conn, 1, {"status": "found", "url": "https://f.ch", "method": "web-search"}
    )
    conn.commit()
    provider = FakeProvider()
    stats = match_with(monkeypatch, provider, settings, conn)
    assert stats["considered"] == 0 and provider.calls == []
