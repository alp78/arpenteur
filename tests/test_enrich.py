"""The enrich stage, driven by a fake provider.

No network and no credential: the provider protocol is the seam, so the stage
can be exercised end to end against a recorded reply. What is asserted here is
everything that is ours rather than the model's, and in particular the two
rules that stop bad web data reaching a draft:

  a website is a guess    only a "found" result may contribute an email, so a
                          namesake's address never enters `contact`.
  spend is bounded        a filter that selects thousands must refuse to run
                          rather than quietly bill for all of them.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.config import Settings
from arpenteur.llm import Research
from arpenteur.stages import enrich

REPLY: dict[str, Any] = {
    "status": "found",
    "url": "https://example-fiduciaire.ch",
    "confidence": 0.9,
    "language": "fr",
    "summary": "Fiduciaire à Genève: comptabilité, fiscalité et paie pour PME.",
    "offerings": ["comptabilité", "déclarations fiscales", "gestion de la paie"],
    "signals": ["site sans HTTPS", "processus décrits comme manuels"],
    "partnerships": ["Fiduciaire Suisse"],
    "tech_stack": ["WordPress"],
    "size_hint": "moins de 10 personnes",
    "emails": ["Info@Example-Fiduciaire.CH"],
    "notes": None,
}


class FakeProvider:
    """Records what it was asked, returns a fixed reply."""

    name = "fake"

    def __init__(self, reply: dict[str, Any] | None = None, fail: bool = False) -> None:
        self.reply = reply if reply is not None else REPLY
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> str:  # pragma: no cover - unused here
        raise AssertionError("enrich must not use complete()")

    def research(self, **kwargs: Any) -> Research:
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("search unavailable")
        return Research(data=self.reply, searches=2, fetches=1)


@pytest.fixture
def settings(tmp_path: Path) -> SimpleNamespace:
    fake = SimpleNamespace(
        statuses=("ACTIVE",),
        signal_brief="",
        llm=SimpleNamespace(
            provider="anthropic",
            enrich_model="claude-sonnet-5",
            draft_model="claude-sonnet-5",
            enrich_max_tokens=8000,
            enrich_max_searches=4,
        ),
    )
    # Bound from the real class rather than reimplemented, so a change to what
    # counts as workable cannot pass here and fail in production.
    fake.status_clause = lambda column="c.status": Settings.status_clause(fake, column)
    return fake


@pytest.fixture
def conn(db_path: Path) -> Connection:
    connection = db.open_db(db_path)
    db.upsert_company(
        connection,
        {
            "uid": "CHE111111111",
            "name": "Example Fiduciaire Sàrl",
            "canton": "GE",
            "legal_seat": "Genève",
            "legal_form_label": "Société à responsabilité limitée",
            "purpose": "Tenue de comptabilité et conseil fiscal.",
            "status": "ACTIVE",
            "hydrated": True,
            "method": "zefix",
        },
    )
    connection.commit()
    return connection


def enrich_with(monkeypatch, provider: FakeProvider, settings, conn, **kwargs):
    monkeypatch.setattr(enrich, "get_provider", lambda _s: provider)
    return enrich.run(settings, conn, log=lambda _m: None, **kwargs)


# --- the money guard ---------------------------------------------------------


def test_a_large_unbounded_selection_refuses_to_run(monkeypatch, settings, conn) -> None:
    """The stage bills per company, so a wide filter must stop, not spend.

    A mistyped canton selecting thousands is a plausible accident; billing for
    it is not a recoverable one.
    """
    for index in range(205):
        db.upsert_company(
            conn,
            {
                "uid": f"CHE{index:09d}",
                "name": f"Company {index}",
                "canton": "VD",
                "status": "ACTIVE",
                "hydrated": True,
                "method": "zefix",
            },
        )
    conn.commit()
    provider = FakeProvider()
    with pytest.raises(RuntimeError, match="costs money"):
        enrich_with(monkeypatch, provider, settings, conn, canton="VD")
    assert provider.calls == [], "nothing may be spent before the guard fires"


def test_an_explicit_limit_permits_the_run(monkeypatch, settings, conn) -> None:
    provider = FakeProvider()
    stats = enrich_with(monkeypatch, provider, settings, conn, limit=1)
    assert stats["found"] == 1


def test_a_limit_of_zero_researches_nothing(monkeypatch, settings, conn) -> None:
    """The selection read zero as no limit while the guard above read it as a
    limit given, so over the guard's size a zero skipped the check and billed
    for every company."""
    for index in range(205):
        db.upsert_company(
            conn,
            {
                "uid": f"CHE{index:09d}",
                "name": f"Company {index}",
                "canton": "VD",
                "status": "ACTIVE",
                "hydrated": True,
                "method": "zefix",
            },
        )
    conn.commit()
    provider = FakeProvider()
    stats = enrich_with(monkeypatch, provider, settings, conn, canton="VD", limit=0)
    assert stats["considered"] == 0
    assert provider.calls == [], "nothing may be spent on a limit of zero"


def test_dry_run_spends_nothing(monkeypatch, settings, conn) -> None:
    provider = FakeProvider()
    stats = enrich_with(monkeypatch, provider, settings, conn, dry_run=True)
    assert provider.calls == []
    assert stats["found"] == 0


def test_dry_run_needs_no_credential(monkeypatch, settings, conn) -> None:
    """Listing the shortlist is how you check a filter before paying for it.

    It has to work on a machine with no key and no sign-in, so the provider
    must not be resolved until after the dry-run branch returns. Stubbing the
    provider in the other tests hides this, hence the explicit failure here.
    """

    def no_credential(_settings):
        raise RuntimeError("No Anthropic credential found.")

    monkeypatch.setattr(enrich, "get_provider", no_credential)
    stats = enrich.run(settings, conn, dry_run=True, log=lambda _m: None)
    assert stats["considered"] == 1


# --- what reaches the database -----------------------------------------------


def test_a_found_site_is_stored_with_its_lists(monkeypatch, settings, conn) -> None:
    enrich_with(monkeypatch, FakeProvider(), settings, conn)
    row = conn.execute("SELECT * FROM intel.site").fetchone()
    assert row["status"] == "found"
    assert row["url"] == "https://example-fiduciaire.ch"
    assert row["offerings"][0] == "comptabilité"
    assert row["signals"]
    # Provenance, as every outside fact in this schema carries.
    assert row["method"] == "web-search"
    assert row["fetched_at"]
    assert row["model"] == "claude-sonnet-5"
    # Spend, so a run can be priced afterwards.
    assert row["searches"] == 2 and row["fetches"] == 1


def test_a_published_email_becomes_a_contact_lowercased(
    monkeypatch, settings, conn
) -> None:
    enrich_with(monkeypatch, FakeProvider(), settings, conn)
    row = conn.execute("SELECT * FROM core.contact").fetchone()
    # Stored folded: the same address in two cases is one address, and the
    # unique constraint is what stops a company being written to twice.
    assert row["value"] == "info@example-fiduciaire.ch"
    assert row["kind"] == "email"
    assert row["method"] == "site"
    assert row["suppressed"] == 0


def test_an_uncertain_site_contributes_no_email(monkeypatch, settings, conn) -> None:
    """The rule that keeps a namesake's address out of a draft.

    Swiss SME names collide. An "ambiguous" result is still worth recording so
    a human can resolve it, but it must not put an address in `contact`, from
    where the draft stage would happily use it.
    """
    reply = dict(REPLY, status="ambiguous", confidence=0.4)
    enrich_with(monkeypatch, FakeProvider(reply), settings, conn)

    assert conn.execute("SELECT COUNT(*) AS n FROM intel.site").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM core.contact").fetchone()[0] == 0


def test_suppression_survives_re_enrichment(monkeypatch, settings, conn) -> None:
    """A human said do not write here. Finding the address again must not undo that."""
    enrich_with(monkeypatch, FakeProvider(), settings, conn)
    conn.execute("UPDATE core.contact SET suppressed = TRUE")
    conn.commit()

    enrich_with(monkeypatch, FakeProvider(), settings, conn, refresh=True)
    assert conn.execute("SELECT suppressed FROM core.contact").fetchone()[0] == 1


# --- resilience --------------------------------------------------------------


def test_one_failure_does_not_end_the_run(monkeypatch, settings, conn) -> None:
    """The shortlist was chosen deliberately; the rest of it is still worth having."""
    db.upsert_company(
        conn,
        {"uid": "CHE222222222", "name": "Second Sàrl", "canton": "GE",
         "status": "ACTIVE", "hydrated": True, "method": "zefix"},
    )
    conn.commit()
    stats = enrich_with(monkeypatch, FakeProvider(fail=True), settings, conn)
    assert stats["failed"] == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM intel.site").fetchone()[0] == 0


def test_re_running_skips_companies_already_done(monkeypatch, settings, conn) -> None:
    provider = FakeProvider()
    enrich_with(monkeypatch, provider, settings, conn)
    assert len(provider.calls) == 1
    enrich_with(monkeypatch, provider, settings, conn)
    assert len(provider.calls) == 1, "a second run must not re-buy the same research"
    enrich_with(monkeypatch, provider, settings, conn, refresh=True)
    assert len(provider.calls) == 2


# --- the brief ---------------------------------------------------------------


def test_officers_are_offered_to_disambiguate_namesakes(
    monkeypatch, settings, conn
) -> None:
    """A director's name is the fastest way to tell two same-named companies apart."""
    company_id = conn.execute("SELECT id AS id FROM core.company").fetchone()[0]
    db.save_people(
        conn,
        company_id,
        [{"full_name": "Turbé Christian", "role": "gérant", "method": "shab"}],
    )
    conn.commit()

    provider = FakeProvider()
    enrich_with(monkeypatch, provider, settings, conn)
    prompt = provider.calls[0]["user"]
    assert "Turbé Christian" in prompt
    assert "CHE111111111" in prompt
    assert "Genève" in prompt


def test_the_schema_is_a_shape_the_api_accepts() -> None:
    """Structured outputs reject the parts of JSON Schema the API does not implement."""
    assert enrich.SCHEMA["additionalProperties"] is False
    assert set(enrich.SCHEMA["required"]) == set(enrich.SCHEMA["properties"])
    for name, prop in enrich.SCHEMA["properties"].items():
        assert not {"minLength", "maxLength", "minimum", "maximum"} & set(prop), name


# --- what counts as a signal is the operator's question ----------------------


def test_the_signal_brief_reaches_the_model(settings) -> None:
    """The field was `it_signals`, described as "evidence bearing on IT need".

    That is one campaign's question welded into the schema. The same pipeline
    serves a recruiter reading for hiring signals and a distributor reading
    for import volume, and neither is asking about IT.
    """
    brief = "Evidence the company is hiring: open roles, growth, new sites."
    schema = enrich.schema_for(brief)
    assert schema["properties"]["signals"]["description"] == brief


def test_a_survey_with_no_brief_still_gets_a_usable_field() -> None:
    """An empty description would leave the model guessing at the field name."""
    assert enrich.schema_for("")["properties"]["signals"]["description"]


def test_building_a_schema_does_not_mutate_the_shared_one() -> None:
    """Two surveys in one process must not overwrite each other's brief."""
    enrich.schema_for("first brief")
    assert enrich.SCHEMA["properties"]["signals"]["description"] == ""


# --- companies not worth spending on -----------------------------------------


def test_a_company_in_liquidation_is_not_researched(monkeypatch, settings, conn) -> None:
    """Found on live data: 6% of the register was winding up or cancelled.

    Classifying those is cheap; researching one costs real money and writing
    to one would be embarrassing. The filter belongs here rather than in the
    caller's head.
    """
    db.upsert_company(
        conn,
        {"uid": "CHE333333333", "name": "Dying Sàrl", "canton": "GE",
         "status": "BEING_CANCELLED", "hydrated": True, "method": "zefix"},
    )
    conn.commit()
    provider = FakeProvider()
    stats = enrich_with(monkeypatch, provider, settings, conn)
    assert stats["considered"] == 1, "the cancelled company should not be selected"
    assert len(provider.calls) == 1


def test_naming_a_uid_overrides_the_status_filter(monkeypatch, settings, conn) -> None:
    """Asking for one company by name is a deliberate act, and someone
    checking why a company was skipped needs to be able to look at it."""
    db.upsert_company(
        conn,
        {"uid": "CHE333333333", "name": "Dying Sàrl", "canton": "GE",
         "status": "BEING_CANCELLED", "hydrated": True, "method": "zefix"},
    )
    conn.commit()
    stats = enrich_with(
        monkeypatch, FakeProvider(), settings, conn, uids=["CHE333333333"]
    )
    assert stats["considered"] == 1


def test_a_survey_can_ask_for_the_whole_register(monkeypatch, settings, conn) -> None:
    """statuses = [] means no restriction, for a survey that wants everything."""
    settings.statuses = ()
    db.upsert_company(
        conn,
        {"uid": "CHE333333333", "name": "Dying Sàrl", "canton": "GE",
         "status": "BEING_CANCELLED", "hydrated": True, "method": "zefix"},
    )
    conn.commit()
    stats = enrich_with(monkeypatch, FakeProvider(), settings, conn)
    assert stats["considered"] == 2
