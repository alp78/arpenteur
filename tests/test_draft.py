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

Nadia Voirol, ingénieur informatique.
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


# The invitation each language's file carries, in the test programme's
# folder. Short, and with every placeholder the stage knows.
INVITES = {
    "fr": "Comment participer\n\niOS, sans condition : {ios_link}\n\n"
          "Android, sous deux conditions. Google exige {testers} testeurs pendant "
          "{days} jours, du {start} au {end}, avec votre compte Google. "
          "Ni séance de test, ni rapport.",
    "de": "So nehmen Sie teil\n\niOS: {ios_link}\n\nAndroid: {testers} Tester, "
          "{days} Tage, vom {start} bis zum {end}.",
    "it": "Come partecipare\n\niOS: {ios_link}\n\nAndroid: {testers} tester, "
          "{days} giorni, dal {start} al {end}.",
}


def _write_invites(root, programme: str = "pilot") -> None:
    folder = root / "templates" / programme
    folder.mkdir(parents=True, exist_ok=True)
    for language, text in INVITES.items():
        (folder / f"invite.{language}.md").write_text(
            f"Notes for the editor.\n\n---\n\n{text}\n\n---\n", encoding="utf-8"
        )


def _settings(tmp_path, brief: str = BRIEF_FULL, *, mail: bool = True, invites: bool = True,
              **enrol_fields):
    """Settings over a project folder holding the brief and the invitations.

    The brief is named under enrolment, as Settings names it, and the
    invitation files sit in the programme's templates folder.
    """
    (tmp_path / "brief.md").write_text(brief, encoding="utf-8")
    if invites:
        _write_invites(tmp_path)
    from arpenteur.config import EnrolConfig

    fields = dict(
        brief="brief.md", programme="PILOT",
        ios_link="https://testflight.apple.com/join/abc123",
        android_window_start="20 octobre 2026",
        android_window_end="3 novembre 2026",
    )
    fields.update(enrol_fields)
    return SimpleNamespace(
        root=tmp_path,
        mail=SimpleNamespace(configured=mail, user="me@arpenteur-demo.ch"),
        llm=SimpleNamespace(enrich_model="test-model", enrich_max_tokens=2000),
        enrol=EnrolConfig(**fields),
    )


def _invite(tmp_path, language: str, **enrol_fields) -> str:
    """The invitation as the stage composes it for one language."""
    return draft.invitation(_settings(tmp_path, **enrol_fields), language)


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
    """The brief comes from Settings, never from a file name in the code."""
    from arpenteur.config import EnrolConfig

    assert not hasattr(draft, "BRIEF")
    settings = SimpleNamespace(
        root=tmp_path,
        mail=SimpleNamespace(configured=True, user="a@b.ch"),
        llm=SimpleNamespace(enrich_model="m", enrich_max_tokens=10),
        enrol=EnrolConfig(),
    )
    with pytest.raises(RuntimeError) as unset:
        draft.run(settings, conn, desk_parts=DESKS, log=lambda _m: None)
    assert "Settings, Enrolment" in str(unset.value)
    settings.enrol = EnrolConfig(brief="absent.md")
    with pytest.raises(RuntimeError) as absent:
        draft.run(settings, conn, desk_parts=DESKS, log=lambda _m: None)
    assert "absent.md" in str(absent.value)


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


def test_a_limit_of_zero_drafts_nothing(conn, tmp_path, monkeypatch) -> None:
    """Zero was falsy for the cap, so the cap that keeps the volume defensible
    gave way exactly when it was set to nothing."""
    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    stats = draft.run(_settings(tmp_path), conn, desk_parts=DESKS, limit=0,
                      log=lambda _m: None)
    assert stats["selected"] == 3 and stats["drafted"] == 0
    assert provider.prompts == []


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


# --- the links and dates are injected, never generated ------------------------
#
# The brief forbids the model from writing a web address. These pin the reason:
# a URL a model produces is a URL it invented, and Google's window has to be
# the same two dates for all twelve testers or the count never closes.


def _enrol(**kw):
    from arpenteur.config import EnrolConfig

    return EnrolConfig(**kw)


FULL_ENROL = dict(
    ios_link="https://testflight.apple.com/join/abc123",
    android_window_start="20 octobre 2026",
    android_window_end="3 novembre 2026",
)


@pytest.mark.parametrize("language", ["fr", "de", "it"])
@pytest.mark.parametrize("enrol_fields", [{}, FULL_ENROL])
def test_an_initial_invitation_keeps_the_opt_out_without_registration(
    language, enrol_fields
) -> None:
    from arpenteur.config import _enrol as read_enrol

    read_enrol({"include_in_draft": False, **enrol_fields})
    body = draft.compose(
        "An invitation to reply.", language=language, address="a@b.ch",
    )
    assert body == (
        "An invitation to reply.\n\n"
        + draft.OPT_OUT[language].format(address="a@b.ch") + "\n"
    )


def test_invitation_drafts_do_not_require_installation_settings(
    conn, tmp_path, monkeypatch
) -> None:
    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    settings = _settings(tmp_path, invites=False, include_in_draft=False)
    said: list[str] = []
    stats = draft.run(settings, conn, desk_parts=DESKS, log=said.append)

    assert stats["drafted"] == 3
    assert stats["enrol_missing"] == []
    assert not any("MANQUE" in line for line in said)
    rows = conn.execute("SELECT body, sent_at FROM intel.draft").fetchall()
    assert all("MANQUE" not in row["body"] for row in rows)
    assert all("TestFlight" not in row["body"] for row in rows)
    assert all("STOP" in row["body"] for row in rows)
    assert all(row["sent_at"] is None for row in rows)


@pytest.mark.parametrize("language,marker", [
    ("fr", "Comment participer"),
    ("de", "So nehmen Sie teil"),
    ("it", "Come partecipare"),
])
def test_the_invitation_is_read_from_the_programmes_file_for_the_language(
    tmp_path, language, marker
) -> None:
    body = draft.compose("Texte.", language=language, address="a@b.ch",
                         invitation=_invite(tmp_path, language))
    assert marker in body
    assert "https://testflight.apple.com/join/abc123" in body
    assert "Notes for the editor" not in body, "only the block between the rules"


def test_both_platforms_and_their_conditions_appear(tmp_path, language="fr") -> None:
    body = draft.compose("Texte.", language=language, address="a@b.ch",
                         invitation=_invite(tmp_path, language))
    assert "iOS, sans condition" in body
    assert "Android, sous deux conditions" in body
    assert "compte Google" in body
    assert "20 octobre 2026" in body and "3 novembre 2026" in body


def test_googles_own_numbers_come_from_settings(tmp_path) -> None:
    """Twelve and fourteen are Google's, not ours, and have changed once."""
    body = _invite(tmp_path, "fr", android_testers=20, android_days=21)
    assert "20 testeurs" in body
    assert "21 jours" in body


def test_a_missing_link_leaves_a_marker_a_reviewer_cannot_miss(tmp_path) -> None:
    body = _invite(tmp_path, "fr", ios_link="", android_window_start="",
                   android_window_end="")
    assert "[[ MANQUE : lien TestFlight ]]" in body
    assert "[[ MANQUE : date de début ]]" in body
    assert "https://" not in body, "no invented URL stands in for the real one"


def test_an_invitation_needs_no_subject_line(tmp_path) -> None:
    from arpenteur.sources import template

    text = "notes\n\n---\n\nComment participer\n\nOuvrez {ios_link}.\n\n---\n"
    subject, body = template.parse(text, language="fr", require_subject=False)
    assert subject == "" and body.startswith("Comment participer")
    with pytest.raises(template.TemplateError, match="must open with"):
        template.parse(text, language="fr")
    with pytest.raises(template.TemplateError, match="À compléter"):
        template.parse("---\nÀ compléter\n---\n", language="fr", require_subject=False)


def test_a_missing_invitation_is_refused_before_any_model_call(
    conn, tmp_path, monkeypatch
) -> None:
    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    settings = _settings(tmp_path, invites=False)
    with pytest.raises(RuntimeError) as caught:
        draft.run(settings, conn, desk_parts=DESKS, limit=3, log=lambda _m: None)
    assert "templates/pilot/invite." in str(caught.value)
    assert provider.prompts == [], "found out before the first call, not after"
    assert conn.execute("SELECT COUNT(*) AS n FROM intel.draft").fetchone()["n"] == 0


def test_the_invitation_needs_a_programme(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="programme"):
        _invite(tmp_path, "fr", programme="")


def test_the_invitation_folder_is_the_programme_in_lower_case(tmp_path) -> None:
    settings = _settings(tmp_path, programme="PILOT")
    assert draft.invitation_path(settings, "de").as_posix().endswith("templates/pilot/invite.de.md")


def test_the_unset_settings_are_named_before_any_model_call(
    conn, tmp_path, monkeypatch
) -> None:
    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    settings = _settings(tmp_path, ios_link="", android_window_start="",
                         android_window_end="")
    said: list[str] = []
    stats = draft.run(settings, conn, desk_parts=DESKS, limit=1, log=said.append)
    assert stats["enrol_missing"]
    assert any("ios_link" in line for line in said)
    assert any("MANQUE" in line for line in said)


def test_the_three_parts_arrive_in_reading_order(tmp_path) -> None:
    """Why they are written to, what to do, how to refuse."""
    body = draft.compose("Pourquoi.", language="fr", address="a@b.ch",
                         invitation=_invite(tmp_path, "fr"))
    assert body.index("Pourquoi.") < body.index("Comment participer")
    assert body.index("Comment participer") < body.index("ne souhaitez plus")


# --- the campaign's own bands, and the refusals it must respect ---------------


def test_the_campaign_bands_are_the_default(conn, tmp_path, monkeypatch) -> None:
    """Drafting respects the campaign's chosen audience rather than widening
    it to every firm that happens to have a deliverable address."""

    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    settings = _settings(tmp_path, segments=("regie-core",))
    asked: dict[str, object] = {}
    real = draft.scout_store.send_list

    def spy(conn_, **kw):
        asked.update(kw)
        return real(conn_, **kw)

    monkeypatch.setattr(draft.scout_store, "send_list", spy)
    said: list[str] = []
    draft.run(settings, conn, desk_parts=DESKS, limit=1, log=said.append)
    assert asked["segments"] == ["regie-core"]
    assert any("regie-core" in line for line in said)


def test_an_explicit_segment_overrides_the_setting(conn, tmp_path, monkeypatch) -> None:

    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    settings = _settings(tmp_path, segments=("regie-core",))
    asked: dict[str, object] = {}
    real = draft.scout_store.send_list
    monkeypatch.setattr(
        draft.scout_store, "send_list",
        lambda conn_, **kw: (asked.update(kw), real(conn_, **kw))[1],
    )
    draft.run(settings, conn, desk_parts=DESKS, limit=1,
              segments=["regie-letting"], log=lambda _m: None)
    assert asked["segments"] == ["regie-letting"]


def test_a_refused_mailbox_is_not_drafted_for_again(conn, tmp_path, monkeypatch) -> None:
    """The reason a refusal is kept rather than deleted. A deleted proposal is
    indistinguishable from one never written."""
    provider = FakeProvider()
    monkeypatch.setattr(draft, "get_provider", lambda _s: provider)
    draft.run(_settings(tmp_path), conn, desk_parts=DESKS, log=lambda _m: None)
    one = conn.execute(
        "SELECT id FROM intel.draft WHERE address = 'info@regie-ge.ch'"
    ).fetchone()
    scout_store.refuse_draft(conn, int(one["id"]), reason="ton")
    conn.commit()

    said: list[str] = []
    stats = draft.run(_settings(tmp_path), conn, desk_parts=DESKS, log=said.append)
    assert any("was refused" in line for line in said)
    held = {
        r["address"] for r in conn.execute(
            "SELECT address FROM intel.draft WHERE refused_at IS NULL"
        )
    }
    assert "info@regie-ge.ch" not in held
    assert stats["drafted"] == 2


# --- the prose the brief proscribes -------------------------------------------


@pytest.mark.parametrize("text,fault", [
    ("Nous restons à votre disposition.", "rester"),
    ("Il est utile, et il est simple.", ", et"),
    ("Vous pouvez acheter ce service.", "acheter"),
    ("un; deux; trois", "semicolons"),
])
def test_a_proscribed_turn_is_named(text, fault) -> None:
    found = draft.prose_faults(text)
    assert found and any(fault in said for said in found)


def test_clean_french_passes() -> None:
    assert draft.prose_faults(
        "Madame, Monsieur,\n\nLimen est une application mobile. "
        "Elle sert aux états des lieux.\n\nMeilleures salutations."
    ) == []


def test_the_faults_are_counted_over_a_run(conn, tmp_path, monkeypatch) -> None:
    class Sloppy(FakeProvider):
        def complete_json(self, **kw):
            self.prompts.append(kw["user"])
            return {"subject": "Limen",
                    "body": "Nous restons à votre disposition.",
                    "missing": []}

    monkeypatch.setattr(draft, "get_provider", lambda _s: Sloppy())
    said: list[str] = []
    stats = draft.run(_settings(tmp_path), conn, desk_parts=DESKS, limit=2,
                      log=said.append)
    assert stats["prose"]
    assert sum(stats["prose"].values()) == 2
    assert any("proscribes these" in line for line in said)
