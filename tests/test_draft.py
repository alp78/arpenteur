"""What a draft is allowed to be, and what it must carry before it exists.

The stage's four design constraints were written down before any of it was
built. These are the constraints as tests: a draft is a proposal, it is
written from evidence, its language follows the canton, and the volume stays
where LCD art. 3 al. 1 let. o leaves it defensible.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.modules.scout import store as scout_store
from arpenteur.stages import draft, verify

DESKS = ("info", "contact")

BRIEF_FULL = """# Brief d'approche Limen

## Le produit

Une application mobile destinée aux états des lieux de logements en Suisse.

## L'expéditeur

Alexis Peringer, ingénieur informatique.
"""

BRIEF_GAPS = """# Brief d'approche Limen

## Le produit

**À compléter avant tout envoi**

- Ce que l'application exécute

## L'expéditeur

**À compléter**
"""


@pytest.fixture
def conn(db_path) -> Connection:
    connection = db.open_db(db_path)
    for uid, name, canton, seat in (
        ("CHE1", "Régie Genevoise SA", "GE", "Genève"),
        ("CHE2", "Immo Zürich AG", "ZH", "Zürich"),
        ("CHE3", "Immobili Ticino SA", "TI", "Lugano"),
    ):
        db.upsert_company(connection, {
            "uid": uid, "name": name, "canton": canton, "legal_seat": seat,
            "purpose": "gérance d'immeubles pour le compte de tiers",
            "status": "ACTIVE", "hydrated": True, "method": "zefix",
        })
    connection.commit()
    for company_id, value in ((1, "info@regie-ge.ch"), (2, "info@immo-zh.ch"),
                              (3, "info@immobili-ti.ch")):
        scout_store.save_contact(
            connection, company_id=company_id, kind="email", value=value,
            method="published",
        )
    connection.commit()
    verify.run(connection, log=lambda _m: None,
               resolve=lambda _d, **_k: (verify.DELIVERABLE, "MX"))
    return connection


def _settings(tmp_path, brief: str = BRIEF_FULL, *, mail: bool = True):
    (tmp_path / draft.BRIEF).write_text(brief, encoding="utf-8")
    return SimpleNamespace(
        root=tmp_path,
        mail=SimpleNamespace(configured=mail, user="alexis@peringer.ch"),
        llm=SimpleNamespace(enrich_model="test-model", enrich_max_tokens=2000),
    )


class FakeProvider:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete_json(self, *, system, user, model, max_tokens, schema, **_kw):
        self.prompts.append(user)
        return {"subject": "Limen", "body": "Madame, Monsieur,\n\nUn texte.",
                "missing": ["le prix"]}


# --- the language follows the canton ------------------------------------------


@pytest.mark.parametrize("canton,expected", [
    ("GE", "fr"), ("VD", "fr"), ("VS", "fr"), ("JU", "fr"),
    ("ZH", "de"), ("BE", "de"), ("GR", "de"),
    ("TI", "it"),
])
def test_the_language_follows_the_canton(canton, expected) -> None:
    assert draft.language_of(canton) == expected


def test_an_unknown_canton_falls_to_german() -> None:
    """German is the majority language of the survey, so an unplaced canton
    costs a review rather than a wrong-language send to the larger half."""
    assert draft.language_of(None) == "de"
    assert draft.language_of("XX") == "de"


# --- the opt-out is appended, never generated ---------------------------------


@pytest.mark.parametrize("language,marker", [
    ("fr", "ne souhaitez plus"),
    ("de", "keine weiteren Nachrichten"),
    ("it", "non desidera"),
])
def test_the_opt_out_is_added_in_the_recipients_language(language, marker) -> None:
    body = draft.compose("Texte.", language=language, address="a@b.ch")
    assert marker in body
    assert "a@b.ch" in body


def test_the_opt_out_survives_a_model_that_forgot_it() -> None:
    """The reason it is composed and not asked for: a model produces an
    unsubscribe line most of the time, and most of the time is not a standard
    a legal obligation is met by."""
    body = draft.compose("Pas un mot de désinscription.", language="fr",
                         address="a@b.ch")
    assert "STOP" in body


def test_a_draft_cannot_be_written_without_an_opt_out_address(conn, tmp_path) -> None:
    settings = _settings(tmp_path, mail=False)
    with pytest.raises(RuntimeError) as caught:
        draft.run(settings, conn, desk_parts=DESKS, log=lambda _m: None)
    assert "opt-out" in str(caught.value)
    assert "art. 3" in str(caught.value)


# --- a draft is a proposal ----------------------------------------------------


def test_a_draft_is_never_marked_sent(conn, tmp_path, monkeypatch) -> None:
    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    stats = draft.run(_settings(tmp_path), conn, desk_parts=DESKS,
                      log=lambda _m: None)
    assert stats["drafted"] == 3
    rows = conn.execute("SELECT sent_at, reviewed_at FROM intel.draft").fetchall()
    assert len(rows) == 3
    assert all(r["sent_at"] is None for r in rows)
    assert all(r["reviewed_at"] is None for r in rows)


def test_a_second_run_replaces_rather_than_duplicates(conn, tmp_path, monkeypatch) -> None:
    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    draft.run(_settings(tmp_path), conn, desk_parts=DESKS, log=lambda _m: None)
    draft.run(_settings(tmp_path), conn, desk_parts=DESKS, log=lambda _m: None)
    held = conn.execute("SELECT count(*) AS n FROM intel.draft").fetchone()["n"]
    assert held == 3, "one proposal per company and mailbox, not two"


def test_a_dry_run_calls_no_model(conn, tmp_path, monkeypatch) -> None:
    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    stats = draft.run(_settings(tmp_path), conn, desk_parts=DESKS,
                      dry_run=True, log=lambda _m: None)
    assert provider.prompts == []
    assert stats["drafted"] == 0 and stats["skipped"] == 3
    assert conn.execute(
        "SELECT count(*) AS n FROM intel.draft"
    ).fetchone()["n"] == 0


# --- written from evidence ----------------------------------------------------


def test_the_prompt_carries_the_register_purpose_verbatim(
    conn, tmp_path, monkeypatch
) -> None:
    """A template with a name substituted in is what the second design
    constraint forbids, so the purpose text has to reach the model."""
    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    draft.run(_settings(tmp_path), conn, desk_parts=DESKS, limit=1,
              log=lambda _m: None)
    assert provider.prompts
    prompt = provider.prompts[0]
    assert "gérance d'immeubles pour le compte de tiers" in prompt
    assert "Une application mobile destinée aux états des lieux" in prompt


def test_the_brief_digest_changes_with_the_brief() -> None:
    """Rewriting the brief has to invalidate the drafts taken against the old
    text, the way `match` treats campaign.md."""
    assert draft.brief_digest(BRIEF_FULL) != draft.brief_digest(BRIEF_GAPS)


def test_a_missing_brief_is_refused_rather_than_defaulted(conn, tmp_path) -> None:
    settings = SimpleNamespace(
        root=tmp_path,
        mail=SimpleNamespace(configured=True, user="a@b.ch"),
        llm=SimpleNamespace(enrich_model="m", enrich_max_tokens=10),
    )
    with pytest.raises(RuntimeError) as caught:
        draft.run(settings, conn, desk_parts=DESKS, log=lambda _m: None)
    assert draft.BRIEF in str(caught.value)


def test_the_gaps_in_a_brief_are_named_before_any_call(conn, tmp_path, monkeypatch) -> None:
    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    said: list[str] = []
    stats = draft.run(_settings(tmp_path, BRIEF_GAPS), conn, desk_parts=DESKS,
                      limit=1, log=said.append)
    assert "Le produit" in stats["gaps"]
    assert "L'expéditeur" in stats["gaps"]
    assert any("À compléter" in line for line in said)


# --- low volume by design -----------------------------------------------------


def test_the_batch_is_capped_and_says_why(conn, tmp_path, monkeypatch) -> None:
    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    said: list[str] = []
    stats = draft.run(_settings(tmp_path), conn, desk_parts=DESKS, limit=2,
                      log=said.append)
    assert stats["selected"] == 3
    assert stats["drafted"] == 2
    assert any("art. 3 al. 1 let. o" in line for line in said)


def test_an_acknowledged_batch_does_not_lecture(conn, tmp_path, monkeypatch) -> None:
    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    said: list[str] = []
    draft.run(_settings(tmp_path), conn, desk_parts=DESKS, limit=2,
              confirmed=True, log=said.append)
    assert not any("art. 3 al. 1 let. o" in line for line in said)


def test_only_verified_addresses_are_drafted_for(conn, tmp_path, monkeypatch) -> None:
    """The send list takes a current deliverable verdict, so a bounced mailbox
    cannot acquire a draft."""
    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    scout_store.save_mailbox_verdict(
        conn, "info@regie-ge.ch", status="bounced", note="5.1.1"
    )
    conn.commit()
    draft.run(_settings(tmp_path), conn, desk_parts=DESKS, log=lambda _m: None)
    held = {
        r["address"] for r in conn.execute("SELECT address FROM intel.draft")
    }
    assert "info@regie-ge.ch" not in held
    assert len(held) == 2
