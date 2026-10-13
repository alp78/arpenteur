"""Audiences and campaigns against a real database.

The store and the schema, first: what a build writes, what a rebuild keeps,
and the promises the tables hold whatever the code above them does. Every firm
here is created in the test database, which each test receives empty.
"""

from __future__ import annotations

from datetime import date

import psycopg
import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.modules.clients import campaigns as K
from arpenteur.modules.clients import store as clients_store
from arpenteur.modules.scout import store as scout_store
from arpenteur.sources import audience as A
from arpenteur.sources import waves as W

pytestmark = pytest.mark.pg

TODAY = date.today()
SEGMENTS = {"tourism-office": 1.0, "mountain-transport": 0.7}
RULE = A.Rule(segments=SEGMENTS)

FIRMS = (
    # uid, name, canton, segment, mailboxes
    ("CHE100000101", "Saastal Tourismus AG", "VS", "tourism-office", ("info@saas-fee.ch",)),
    ("CHE100000102", "Saastal Bergbahnen AG", "VS", "mountain-transport", ("info@saas-fee.ch",)),
    ("CHE100000103", "Zermatt Tourismus", "VS", "tourism-office", ("info@zermatt.ch",)),
    ("CHE100000104", "Champéry Tourisme", "VS", "tourism-office", ("info@champery.ch",)),
    ("CHE100000105", "Skilift Bumps AG", "BE", "mountain-transport", ()),
)


@pytest.fixture
def conn(db_path: str) -> Connection:
    connection = db.open_db(db_path)
    for uid, name, canton, segment, mailboxes in FIRMS:
        cid = connection.execute(
            "INSERT INTO core.company (uid, name, canton, legal_form_uid, legal_form_label,"
            " status, first_seen) VALUES (%s, %s, %s, '0106', 'SA', 'ACTIVE', now())"
            " RETURNING id", (uid, name, canton),
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO intel.segment (company_id, segment, confidence, source, classified_at)"
            " VALUES (%s, %s, 1, 'manual', now())", (cid, segment),
        )
        for mailbox in mailboxes:
            connection.execute(
                "INSERT INTO core.contact (company_id, kind, value, method, status, status_at,"
                " source_url, fetched_at) VALUES (%s, 'email', %s, 'published', 'deliverable',"
                " now(), 'https://example.test', now())", (cid, mailbox),
            )
    connection.commit()
    return connection


def company(conn: Connection, name: str) -> int:
    return int(conn.execute("SELECT id FROM core.company WHERE name = %s", (name,)).fetchone()["id"])


def build(conn: Connection, audience_id: int, rule: A.Rule = RULE) -> dict:
    """What `stages/audience.py` does, kept here so the store is tested on its own."""
    firms, addresses, recent = K.candidates(conn, segments=list(rule.segments))
    held: dict[int, list[A.Address]] = {}
    for a in addresses:
        held.setdefault(int(a["company_id"]), []).append(A.Address(
            int(a["contact_id"]), a["value"], a["status"], a["status_at"], a["method"],
            bool(a["suppressed"]), bool(a["refused"]), a["written_at"]))
    judged = [A.judge(A.Firm(int(f["company_id"]), f["name"], f["canton"], f["legal_form_uid"],
                             bool(f["active"]), tuple(f["segments"]),
                             tuple(held.get(int(f["company_id"]), ())), f["capital"],
                             f["hunt_note"], f["written_at"], f["programme_stage"],
                             f["first_stage"]), rule, today=TODAY, recent_domains=recent)
              for f in firms]
    pins = {cid: A.Pin(**{k: v for k, v in p.items() if k in ("verdict", "language",
                                                             "contact_id", "why")})
            for cid, p in K.pins(conn, audience_id).items()}
    stats = K.save_members(conn, audience_id, A.assign(judged, rule, pins),
                           rule_digest=rule.digest())
    conn.commit()
    return stats


@pytest.fixture
def audience_id(conn: Connection) -> int:
    new = K.add_audience(conn, code="tourism-1", name="Tourisme", rule=RULE.to_json())
    conn.commit()
    return new


# --- the build ----------------------------------------------------------------

def test_a_build_writes_one_row_per_firm_in_scope(conn, audience_id) -> None:
    stats = build(conn, audience_id)
    assert stats["firms"] == len(FIRMS)
    rows = K.members(conn, audience_id)
    assert len(rows) == len(FIRMS)
    assert sum(K.funnel_counts(conn, audience_id).values()) == len(FIRMS)


def test_a_shared_mailbox_is_written_once_and_the_sibling_says_by_whom(conn, audience_id) -> None:
    build(conn, audience_id)
    rows = {r["company_name"]: r for r in K.members(conn, audience_id)}
    assert rows["Saastal Tourismus AG"]["verdict"] == "kept"
    sibling = rows["Saastal Bergbahnen AG"]
    assert sibling["reason_code"] == "covered-mailbox"
    assert sibling["covered_by_name"] == "Saastal Tourismus AG"


def test_the_list_reads_by_canton_then_score(conn, audience_id) -> None:
    build(conn, audience_id)
    rows = K.members(conn, audience_id, verdict="kept")
    keys = [(r["canton"], -float(r["score"])) for r in rows]
    assert keys == sorted(keys)


def test_a_rebuild_keeps_row_ids_and_pins(conn, audience_id) -> None:
    build(conn, audience_id)
    before = {r["company_id"]: r["id"] for r in K.members(conn, audience_id)}
    zermatt = before[company(conn, "Zermatt Tourismus")]
    K.set_pin(conn, zermatt, {"verdict": "dropped", "why": "already a client"})
    conn.commit()
    build(conn, audience_id)
    after = {r["company_id"]: r for r in K.members(conn, audience_id)}
    assert {cid: r["id"] for cid, r in after.items()} == before
    row = after[company(conn, "Zermatt Tourismus")]
    assert (row["verdict"], row["reason_code"]) == ("dropped", "operator")


def test_a_frozen_audience_refuses_a_rebuild(conn, audience_id) -> None:
    build(conn, audience_id)
    K.freeze(conn, audience_id)
    conn.commit()
    with pytest.raises(ValueError, match="frozen"):
        build(conn, audience_id)


def test_the_schema_refuses_two_kept_members_on_one_mailbox(conn, audience_id) -> None:
    insert = ("INSERT INTO crm.audience_member (audience_id, company_id, address, verdict,"
              " reason_code, reason) VALUES (%s, %s, 'info@same.ch', 'kept', 'kept', 'x')")
    conn.execute(insert, (audience_id, company(conn, "Zermatt Tourismus")))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert, (audience_id, company(conn, "Champéry Tourisme")))
    conn.rollback()


# --- the campaign and its plan --------------------------------------------------

@pytest.fixture
def campaign_id(conn, audience_id) -> int:
    build(conn, audience_id)
    programme = clients_store.add_programme(conn, "drymos", "Drymos")
    new = K.add_campaign(conn, code="drymos-a", name="Essai", audience_id=audience_id,
                         templates="templates/drymos", strategy="canton-blocks",
                         programme_id=programme, pilot_size=1, wave_size=2)
    conn.commit()
    return new


def plan(conn, campaign_id) -> list:
    row = K.campaign(conn, campaign_id)
    people = [W.Recipient(int(r["member_id"]), r["company"], r["canton"], r["language"],
                          r["segment"], float(r["score"]))
              for r in K.kept_members(conn, row["audience_id"])]
    waves = W.plan(people, strategy=row["strategy"], wave_size=row["wave_size"],
                   pilot_size=row["pilot_size"])
    K.replace_plan(conn, campaign_id, waves)
    conn.commit()
    return waves


def test_a_plan_puts_every_kept_member_in_one_wave(conn, campaign_id) -> None:
    waves = plan(conn, campaign_id)
    stored = K.waves(conn, campaign_id)
    assert len(stored) == len(waves)
    assert sum(int(w["messages"]) for w in stored) == len(
        K.members(conn, K.campaign(conn, campaign_id)["audience_id"], verdict="kept"))
    assert stored[0]["pilot"] is True


def test_the_schema_refuses_a_send_before_approval(conn, campaign_id) -> None:
    plan(conn, campaign_id)
    first = K.waves(conn, campaign_id)[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE crm.campaign_wave SET sent_at = now() WHERE id = %s",
                     (first["id"],))
    conn.rollback()


def test_the_schema_refuses_an_approval_before_a_test(conn, campaign_id) -> None:
    plan(conn, campaign_id)
    first = K.waves(conn, campaign_id)[0]
    K.set_rendered(conn, first["id"], "digest")
    with pytest.raises(psycopg.errors.CheckViolation):
        K.approve(conn, first["id"], by="test")
    conn.rollback()


def test_a_render_clears_the_test_and_the_approval(conn, campaign_id) -> None:
    plan(conn, campaign_id)
    wave_id = K.waves(conn, campaign_id)[0]["id"]
    K.set_rendered(conn, wave_id, "one")
    K.set_tested(conn, wave_id)
    K.approve(conn, wave_id, by="test")
    K.set_rendered(conn, wave_id, "two")
    row = K.wave(conn, campaign_id, 1)
    assert row["tested_at"] is None and row["approved_at"] is None
    assert row["template_digest"] == "two"


def test_replanning_is_refused_once_a_message_has_left(conn, campaign_id) -> None:
    plan(conn, campaign_id)
    wave_id = K.waves(conn, campaign_id)[0]["id"]
    K.record_attempt(conn, kind="message", address="info@zermatt.ch", subject="s",
                     message_id="<1@test>", wave_id=wave_id)
    conn.commit()
    with pytest.raises(ValueError, match="already sent"):
        plan(conn, campaign_id)


def test_a_planned_member_is_kept_when_it_leaves_the_rule(conn, campaign_id) -> None:
    plan(conn, campaign_id)
    audience_id = K.campaign(conn, campaign_id)["audience_id"]
    narrower = A.Rule(segments={"mountain-transport": 0.7})
    build(conn, audience_id, rule=narrower)
    office = {r["company_name"]: r for r in K.members(conn, audience_id)}
    assert office["Zermatt Tourismus"]["reason_code"] == "out-of-scope"


def test_pausing_writes_an_audit_row_and_resuming_needs_a_reason(conn, campaign_id) -> None:
    K.pause(conn, campaign_id, reason="the pilot bounced", by="gate")
    assert K.campaign(conn, campaign_id)["paused_at"] is not None
    with pytest.raises(ValueError, match="reason"):
        K.resume(conn, campaign_id, reason=" ", by="operator")
    K.resume(conn, campaign_id, reason="addresses checked", by="operator")
    assert K.campaign(conn, campaign_id)["paused_at"] is None
    moves = conn.execute("SELECT to_state FROM audit.state_change WHERE entity = 'campaign'"
                         " AND entity_id = %s ORDER BY id", (campaign_id,)).fetchall()
    assert [m["to_state"] for m in moves] == ["paused", "running"]


# --- drafts and the outbound log --------------------------------------------------

def test_a_campaign_draft_never_overwrites_another_open_draft(conn) -> None:
    cid = company(conn, "Zermatt Tourismus")
    first = scout_store.propose(conn, cid, address="info@zermatt.ch", language="de",
                                subject="A", body="a", model="model:x", digest="1")
    scout_store.review_drafts(conn, [first])
    second = scout_store.propose(conn, cid, address="info@zermatt.ch", language="de",
                                 subject="B", body="b", model="template:y", digest="2")
    assert second is None
    row = scout_store.draft(conn, first)
    assert row["subject"] == "A" and row["reviewed_at"] is not None


def test_a_campaign_updates_only_its_own_draft(conn) -> None:
    cid = company(conn, "Zermatt Tourismus")
    own = scout_store.propose(conn, cid, address="info@zermatt.ch", language="de",
                              subject="A", body="a", model="template:x", digest="1")
    again = scout_store.propose(conn, cid, address="info@zermatt.ch", language="de",
                                subject="B", body="b", model="template:x", digest="2",
                                replace_id=own)
    assert again == own and scout_store.draft(conn, own)["subject"] == "B"


def test_a_refused_draft_survives_a_replan(conn) -> None:
    cid = company(conn, "Zermatt Tourismus")
    kept = scout_store.propose(conn, cid, address="info@zermatt.ch", language="de",
                               subject="A", body="a", model="template:x", digest="1")
    conn.execute("UPDATE intel.draft SET refused_at = now() WHERE id = %s", (kept,))
    assert scout_store.delete_unsent_drafts(conn, [kept]) == 0


def test_the_quota_counts_every_attempt_whatever_came_of_it(conn) -> None:
    for i, kind in enumerate(("message", "test", "letter")):
        oid = K.record_attempt(conn, kind=kind, address=f"a{i}@x.ch", subject="s",
                               message_id=f"<{i}@test>")
        (K.record_posted if i else K.record_failed)(conn, oid, *(() if i else ("550",)))
    assert K.attempts_since(conn, hours=24) == 3
    assert K.oldest_attempt_since(conn, hours=24) is not None


def test_an_attempt_without_an_outcome_is_found_and_can_be_settled(conn) -> None:
    cid = company(conn, "Zermatt Tourismus")
    draft = scout_store.propose(conn, cid, address="info@zermatt.ch", language="de",
                                subject="A", body="a", model="template:x", digest="1")
    oid = K.record_attempt(conn, kind="message", address="info@zermatt.ch", subject="A",
                           message_id="<9@test>", draft_id=draft)
    assert K.unsettled_for_draft(conn, draft)["id"] == oid
    K.settle(conn, oid, posted=True)
    assert K.unsettled_for_draft(conn, draft) is None


def test_last_written_reaches_the_firm_the_mailbox_and_the_domain(conn) -> None:
    cid = company(conn, "Zermatt Tourismus")
    draft = scout_store.propose(conn, cid, address="info@zermatt.ch", language="de",
                                subject="A", body="a", model="template:x", digest="1")
    oid = K.record_attempt(conn, kind="message", address="info@zermatt.ch", subject="A",
                           message_id="<7@test>", draft_id=draft)
    K.record_posted(conn, oid)
    assert K.last_written(conn, address="info@zermatt.ch")
    assert K.last_written(conn, company_id=cid)
    assert K.last_written(conn, domain="zermatt.ch")
    assert K.last_written(conn, address="info@elsewhere.ch") is None


# --- the audience stage -------------------------------------------------------

from arpenteur.stages import audience as audience_stage  # noqa: E402


def test_a_dry_run_build_reports_the_funnel_and_writes_nothing(conn, audience_id) -> None:
    lines: list[str] = []
    stats = audience_stage.build(None, conn, audience="TOURISM-1", dry_run=True,
                                 log=lines.append)
    assert stats["firms"] == len(FIRMS)
    assert K.members(conn, audience_id) == []
    assert any("covered-mailbox" in line for line in lines)


def test_a_stage_build_writes_the_list(conn, audience_id) -> None:
    stats = audience_stage.build(None, conn, audience=audience_id, log=lambda _: None)
    assert stats["kept"] == len(K.members(conn, audience_id, verdict="kept"))
    assert [code for code, _ in audience_stage.funnel(conn, audience_id)][-1] == "kept"


def test_refresh_reverifies_only_this_lists_stale_domains(conn, audience_id) -> None:
    audience_stage.build(None, conn, audience=audience_id, log=lambda _: None)
    conn.execute("UPDATE core.contact SET status_at = now() - interval '90 days'")
    outsider = conn.execute(
        "INSERT INTO core.company (uid, name, canton, status, first_seen)"
        " VALUES ('CHE100000199', 'Hors Liste SA', 'GE', 'ACTIVE', now()) RETURNING id"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO core.contact (company_id, kind, value, method, status, status_at)"
        " VALUES (%s, 'email', 'info@outside.ch', 'published', 'deliverable',"
        " now() - interval '90 days')", (outsider,),
    )
    conn.commit()
    asked: list[str] = []

    def resolver(domain: str) -> tuple[str, str]:
        asked.append(domain)
        return "deliverable", "MX"

    audience_stage.refresh(None, conn, audience=audience_id, log=lambda _: None,
                           resolve_domain=resolver)
    assert "outside.ch" not in asked
    assert set(asked) == {"saas-fee.ch", "zermatt.ch", "champery.ch"}


def test_segments_parse_with_their_weights() -> None:
    assert audience_stage.parse_segments("tourism-office=1, mountain-transport=0.7, nature-park") \
        == {"tourism-office": 1.0, "mountain-transport": 0.7, "nature-park": 1.0}
    with pytest.raises(ValueError):
        audience_stage.parse_segments("tourism-office=3")
