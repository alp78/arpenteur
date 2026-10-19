"""Running a campaign: plan, render, test, approve, send, and read what came back.

The order of operations, and nothing else: rows from
`modules/clients/campaigns.py`, the wave rules from `sources/waves.py`, the
message from `sources/template.py`, the post from `stages/send.py`.

**Each wave walks the same five steps, in this order, and the schema refuses
any other order.**

  plan     cuts the list's kept firms into waves by the campaign's strategy.
           Free to repeat until the first message leaves.
  render   writes one draft per recipient from the templates, word for word:
           an opt-out sentence, when the campaign needs one, is part of the
           template. Refused, naming the files, when a template is missing.
  test     sends one message per variant to the operator, marked [TEST], and
           counts against the quota like any other.
  approve  a person says they received and read every test. Approval marks
           every draft of the wave reviewed, which is what `send` requires.
  send     a background job, behind a typed confirmation, through `send.run`
           with every guard it applies, capped by the wave and the quota.

**Between waves, the gate.** A wave waits until the previous one's bounces
have had time to arrive and have been read, and a campaign whose last wave
bounced above its ceiling pauses itself until a person resumes it with a
reason. That is what makes a small first wave a pilot.

**A template edited after the test makes the send refuse.** The wave stores a
fingerprint of the files it rendered from, so what was tested is what goes.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from ..config import Settings
from ..kernel.db import Connection, Row
from ..modules.clients import campaigns as campaign_store
from ..modules.clients import store as clients_store
from ..modules.scout import store as scout_store
from ..sources import language as language_source
from ..sources import template, waves
from . import audience as audience_stage
from . import send as send_stage

TEST_PREFIX = "[TEST] "


def resolve(conn: Connection, ref: str | int) -> Row:
    row = (campaign_store.campaign(conn, int(ref)) if str(ref).isdigit()
           else campaign_store.campaign_by_code(conn, str(ref)))
    if row is None:
        known = ", ".join(r["code"] for r in campaign_store.campaigns(conn)) or "none yet"
        raise ValueError(f"No campaign {ref!r}. Known: {known}.")
    return row


def _wave(conn: Connection, campaign: Row, number: int) -> Row:
    row = campaign_store.wave(conn, int(campaign["id"]), int(number))
    if row is None:
        raise ValueError(f"{campaign['code']} has no wave {number}. Plan it first.")
    return row


def _templates_dir(settings: Settings, folder: str) -> Path:
    """The campaign's template folder, which must sit under `templates/`."""
    relative = PurePosixPath(folder.strip().strip("/"))
    if relative.is_absolute() or ".." in relative.parts or (
            not relative.parts or relative.parts[0] != "templates"):
        raise ValueError(
            f"Templates live under templates/, e.g. templates/drymos; got {folder!r}."
        )
    return Path(settings.root) / relative


def bilingual_cantons(value: Any) -> list[str]:
    """The cantons a campaign writes in every official language, checked."""
    if isinstance(value, str):
        value = value.split(",")
    cantons = sorted({str(c).strip().upper() for c in (value or ()) if str(c).strip()})
    single = [c for c in cantons if c not in language_source.OFFICIAL]
    if single:
        raise ValueError(
            f"{', '.join(single)} {'has' if len(single) == 1 else 'have'} one official "
            "language. A message is written in several only in "
            f"{', '.join(sorted(language_source.OFFICIAL))}."
        )
    return cantons


def _languages(campaign: Row, canton: str | None, language: str) -> list[str]:
    """The languages of the message to one firm, its own first."""
    canton = (canton or "").upper()
    if canton not in (campaign.get("bilingual") or ()):
        return [language]
    return [language] + [lang for lang in language_source.OFFICIAL[canton]
                         if lang != language]


def _wave_language(campaign: Row, canton: str | None, language: str) -> str:
    """What the waves group a firm under. Every firm of a bilingual canton reads
    the same versions, so they make one group whatever each one speaks."""
    canton = (canton or "").upper()
    if canton in (campaign.get("bilingual") or ()):
        return "+".join(language_source.OFFICIAL[canton])
    return language


def _parts(found: Any, campaign: Row, message: Row) -> list[template.Template | None]:
    """The template of each language the message is written in, in order."""
    return [template.pick(found, lang, message["segment"])
            for lang in _languages(campaign, message["canton"], message["language"])]


def create(settings: Settings, conn: Connection, *, code: str, name: str,
           audience: str | int, templates: str, strategy: str,
           programme: str = "", **params: Any) -> int:
    if strategy not in waves.STRATEGIES:
        raise ValueError(f"Unknown strategy {strategy!r}. Known: "
                         + ", ".join(sorted(waves.STRATEGIES)))
    _templates_dir(settings, templates)
    if params.get("bilingual") is not None:
        params["bilingual"] = bilingual_cantons(params["bilingual"])
    listed = audience_stage.resolve(conn, audience)
    programme_id = None
    if programme:
        found = clients_store.programme_by_code(conn, programme)
        if found is None:
            raise ValueError(f"No programme {programme!r}.")
        programme_id = int(found["id"])
    campaign_id = campaign_store.add_campaign(
        conn, code=code, name=name, audience_id=int(listed["id"]),
        templates=templates.strip().strip("/"), strategy=strategy,
        programme_id=programme_id, **params,
    )
    conn.commit()
    return campaign_id


# --- plan -----------------------------------------------------------------------


def plan(settings: Settings, conn: Connection, *, campaign: str | int,
         dry_run: bool = False, log: Callable[[str], None] = print,
         **changes: Any) -> dict[str, Any]:
    """Cut the list into waves by the campaign's strategy, or show the cut."""
    row = resolve(conn, campaign)
    if changes:
        if "strategy" in changes and changes["strategy"] not in waves.STRATEGIES:
            raise ValueError(f"Unknown strategy {changes['strategy']!r}.")
        if "bilingual" in changes:
            changes["bilingual"] = bilingual_cantons(changes["bilingual"])
        if not dry_run:
            campaign_store.update_campaign(conn, int(row["id"]), **changes)
        row = {**row, **changes}
    if int(row["wave_size"]) > settings.smtp.per_run:
        raise ValueError(
            f"A wave of {row['wave_size']} is larger than one run may post "
            f"([smtp] per_run = {settings.smtp.per_run}). Raise per_run first, "
            "deliberately, or plan smaller waves."
        )
    people = [
        waves.Recipient(int(r["member_id"]), r["company"], r["canton"],
                        _wave_language(row, r["canton"], r["language"]),
                        r["segment"], float(r["score"]))
        for r in campaign_store.kept_members(conn, int(row["audience_id"]))
    ]
    if not people:
        raise ValueError(f"{row['audience_code']} keeps no firm. Build it first.")
    # Dates come back from the row factory as ISO strings, like timestamps.
    start = date.fromisoformat(str(row["start_on"])[:10]) if row["start_on"] else None
    cut = waves.plan(people, strategy=row["strategy"], wave_size=int(row["wave_size"]),
                     pilot_size=int(row["pilot_size"]), start=start,
                     gap_days=int(row["gap_days"]))
    log(f"{row['code']}: {len(people)} firms in {len(cut)} waves, "
        f"strategy {row['strategy']}")
    for w in cut:
        when = w.planned_for.isoformat() if w.planned_for else "unscheduled"
        log(f"  {w.number:>3}  {len(w.member_ids):>3}  {when}  {w.label}")
    stats = {"firms": len(people), "waves": len(cut),
             "sizes": [len(w.member_ids) for w in cut]}
    if dry_run:
        log("(dry run, nothing written)")
        return stats
    campaign_store.replace_plan(conn, int(row["id"]), cut)
    conn.commit()
    return stats


# --- render -----------------------------------------------------------------------


def _live(messages: list[Row]) -> list[Row]:
    return [m for m in messages if not m["dropped_at"]]


def _preflight(settings: Settings, conn: Connection, campaign: Row, wave: Row) -> None:
    if campaign["closed_at"]:
        raise ValueError(f"{campaign['code']} is closed.")
    if wave["sent_at"] or int(wave["sent"] or 0):
        raise ValueError(f"Wave {wave['number']} has already sent; it cannot change.")
    if not settings.mail.configured:
        raise ValueError(
            "No mailbox is configured, so a reply asking to stop and a bounce "
            "would reach nothing Arpenteur reads."
        )
    if not send_stage.signature_html(settings).strip():
        raise ValueError(
            "No signature is written, so the message would not name its sender. "
            "Write one at /signature first."
        )
    reply_to = (settings.smtp.reply_to or "").strip().lower()
    if reply_to and reply_to != (settings.mail.user or "").strip().lower():
        raise ValueError(
            f"Replies go to {reply_to}, which Arpenteur does not read, so a STOP "
            "would never reach the suppression list. Clear [smtp] reply_to or point "
            "it at the read mailbox."
        )


def render_wave(settings: Settings, conn: Connection, *, campaign: str | int,
                wave: int, log: Callable[[str], None] = print) -> dict[str, Any]:
    """One draft per recipient, from the templates, word for word."""
    row = resolve(conn, campaign)
    w = _wave(conn, row, wave)
    _preflight(settings, conn, row, w)
    folder = _templates_dir(settings, row["templates"])
    found = template.load(folder, root=Path(settings.root))
    messages = campaign_store.wave_messages(conn, int(w["id"]))
    for m in _live(messages):
        if m["member_verdict"] != "kept":
            campaign_store.drop_message(conn, int(m["message_id"]),
                                        f"dropped from the list since the plan: "
                                        f"{m['member_reason']}")
    messages = _live(campaign_store.wave_messages(conn, int(w["id"])))
    needs = Counter((lang, m["segment"]) for m in messages
                    for lang in _languages(row, m["canton"], m["language"]))
    gaps = template.missing(needs, found, row["templates"])
    if gaps:
        conn.rollback()
        raise ValueError("Wave {} cannot be rendered: {}.".format(
            w["number"], "; ".join(f"missing template {g}" for g in gaps)))

    used: list[template.Template] = []
    written = skipped = 0
    for m in messages:
        parts = _parts(found, row, m)
        subject, body = template.combine(parts)
        draft_id = scout_store.propose(
            conn, int(m["company_id"]), address=m["address"], language=m["language"],
            subject=subject, body=body,
            model="template:" + "+".join(f"{t.path}@{t.sha[:8]}" for t in parts),
            digest=parts[0].sha[:16] if len(parts) == 1 else template.digest(parts)[:16],
            replace_id=m["draft_id"],
        )
        if draft_id is None:
            campaign_store.drop_message(conn, int(m["message_id"]),
                                        "another open draft already holds this mailbox")
            skipped += 1
            continue
        campaign_store.link_draft(conn, int(m["message_id"]), draft_id,
                                  "+".join(t.variant for t in parts))
        used.extend(t for t in parts if t not in used)
        written += 1
    campaign_store.set_rendered(conn, int(w["id"]), template.digest(used))
    conn.commit()
    log(f"{row['code']} wave {w['number']}: {written} drafts rendered from "
        f"{len(used)} template(s)" + (f", {skipped} set aside" if skipped else ""))
    return {"rendered": written, "skipped": skipped,
            "variants": sorted(t.variant for t in used)}


def _current_digest(settings: Settings, conn: Connection, campaign: Row,
                    wave: Row) -> str:
    """The fingerprint the wave's templates would have now."""
    found = template.load(_templates_dir(settings, campaign["templates"]),
                         root=Path(settings.root))
    used = []
    for m in _live(campaign_store.wave_messages(conn, int(wave["id"]))):
        used.extend(t for t in _parts(found, campaign, m)
                    if t is not None and t not in used)
    return template.digest(used)


def _unchanged(settings: Settings, conn: Connection, campaign: Row, wave: Row) -> None:
    if not wave["rendered_at"]:
        raise ValueError(f"Wave {wave['number']} has not been rendered.")
    if _current_digest(settings, conn, campaign, wave) != wave["template_digest"]:
        raise ValueError(
            f"A template of wave {wave['number']} changed after it was rendered. "
            "Render it again, then test it again."
        )


# --- test and approve -------------------------------------------------------------


def _variants(conn: Connection, wave: Row) -> list[Row]:
    """One rendered message per variant: every other recipient gets the same words."""
    seen: dict[str, Row] = {}
    for m in _live(campaign_store.wave_messages(conn, int(wave["id"]))):
        if m["draft_id"] and m["variant"] not in seen:
            seen[m["variant"]] = m
    return list(seen.values())


def precheck_test(settings: Settings, form: dict[str, Any]) -> None:
    """Run synchronously by the console before the job starts."""
    from .. import db
    with db.open_db(settings.database_url, getattr(settings, "modules", ())) as conn:
        row = resolve(conn, form["campaign"])
        _unchanged(settings, conn, row, _wave(conn, row, int(form["wave"])))


def test_wave(settings: Settings, conn: Connection, *, campaign: str | int, wave: int,
              to: str = "", log: Callable[[str], None] = print,
              open_sender: Callable[..., Any] | None = None,
              sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Send the operator one message per variant, marked as a test."""
    row = resolve(conn, campaign)
    w = _wave(conn, row, wave)
    _unchanged(settings, conn, row, w)
    target = (to or settings.smtp.test_to or settings.smtp.sender).strip()
    letters = [
        send_stage.Letter(to=target, subject=TEST_PREFIX + (m["subject"] or ""),
                          body=m["body"] or "", firm=f"{m['variant']} test",
                          variant=m["variant"] or "")
        for m in _variants(conn, w)
    ]
    if not letters:
        raise ValueError(f"Wave {w['number']} has no rendered message to test.")
    stats = send_stage.run(settings, conn, letters=letters, confirm=True, kind="test",
                           programme="", wave_id=int(w["id"]), log=log,
                           open_sender=open_sender, sleep=sleep)
    if stats["sent"] == len(letters):
        campaign_store.set_tested(conn, int(w["id"]))
        conn.commit()
        log(f"wave {w['number']}: {len(letters)} test(s) sent to {target}")
    else:
        log(f"wave {w['number']}: {stats['sent']} of {len(letters)} tests went; "
            "the wave stays untested")
    return {**stats, "variants": len(letters), "to": target}


def approve(settings: Settings, conn: Connection, *, campaign: str | int, wave: int,
            by: str, read: bool) -> dict[str, Any]:
    """A person says they read every test; every draft of the wave is approved."""
    if not read:
        raise ValueError("Approve only after receiving and reading every test message.")
    row = resolve(conn, campaign)
    w = _wave(conn, row, wave)
    if not w["tested_at"]:
        raise ValueError(f"Wave {w['number']} has not been tested since its last render.")
    _unchanged(settings, conn, row, w)
    drafts = [int(m["draft_id"]) for m in _live(campaign_store.wave_messages(conn, int(w["id"])))
              if m["draft_id"]]
    reviewed = scout_store.review_drafts(conn, drafts)
    campaign_store.approve(conn, int(w["id"]), by=by)
    conn.commit()
    return {"approved": reviewed}


def unapprove(settings: Settings, conn: Connection, *, campaign: str | int,
              wave: int) -> None:
    row = resolve(conn, campaign)
    w = _wave(conn, row, wave)
    drafts = [int(m["draft_id"]) for m in campaign_store.wave_messages(conn, int(w["id"]))
              if m["draft_id"]]
    scout_store.unreview_drafts(conn, drafts)
    campaign_store.unapprove(conn, int(w["id"]))
    conn.commit()


# --- results and the gate -------------------------------------------------------


def _parse(stamp: Any) -> datetime | None:
    if not stamp:
        return None
    if isinstance(stamp, datetime):
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def results(settings: Settings, conn: Connection, *,
            campaign: str | int) -> dict[int, dict[str, Any]]:
    """Per wave: what came of it, whether it has settled, and the gate."""
    row = resolve(conn, campaign)
    rows = campaign_store.wave_outcomes(conn, int(row["id"]),
                                        free_domains=audience_stage.free_domains())
    bounces = scout_store.last_run(conn, "bounces")
    read_at = _parse(bounces["started_at"]) if bounces else None
    out: dict[int, dict[str, Any]] = {}
    for w in campaign_store.waves(conn, int(row["id"])):
        mine = [r for r in rows if r["wave_id"] == w["id"]]
        replied = auto = 0
        for r in mine:
            kinds = {waves.classify_inbound(e.get("subject") or "", e.get("from") or "")
                     for e in (r["inbound"] or [])}
            replied += "reply" in kinds
            auto += "auto" in kinds and "reply" not in kinds
        tally = waves.Tally(
            sent=len(mine), bounced=sum(1 for r in mine if r["bounced"]),
            deferred=sum(1 for r in mine if r["deferred"]),
            replied=replied, auto=auto,
            refused=sum(1 for r in mine if r["refused"]),
            enrolled=sum(1 for r in mine if r["enrolled"]),
        )
        last = _parse(w["last_sent_at"])
        settled = bool(last and read_at
                       and read_at >= last + timedelta(hours=int(row["settle_hours"])))
        out[int(w["number"])] = {
            "tally": tally, "settled": settled, "last_sent_at": last,
            "mail_read_at": read_at,
            "gate": waves.gate(tally, ceiling=float(row["bounce_ceiling"]), settled=settled),
        }
    return out


# --- send -------------------------------------------------------------------------


def confirmation(campaign: Row, wave: Row) -> str:
    """What the operator types to send a wave."""
    return f"{campaign['code']}/{wave['number']}"


def why_not_send(settings: Settings, conn: Connection, row: Row, w: Row,
                 confirm: str, *, act: bool = True) -> str:
    """The reason this wave may not be sent now, or an empty string.

    `act` lets a failing gate pause the campaign, which the job and the
    precheck do. A page drawing its send button passes False: looking at a
    wave must never change the campaign.
    """
    if (confirm or "").strip().upper() != confirmation(row, w).upper():
        return f"type {confirmation(row, w)} to send this wave"
    if row["closed_at"]:
        return f"{row['code']} is closed"
    if row["paused_at"]:
        return f"{row['code']} is paused: {row['paused_reason']}"
    if not w["approved_at"]:
        return f"wave {w['number']} is not approved"
    try:
        _unchanged(settings, conn, row, w)
    except ValueError as exc:
        return str(exc)
    number = int(w["number"])
    if number > 1:
        previous = campaign_store.wave(conn, int(row["id"]), number - 1)
        if previous is not None and not previous["sent_at"]:
            return f"wave {number - 1} has not been sent yet"
        if previous is not None:
            last = _parse(previous["last_sent_at"])
            ready = last + timedelta(days=int(row["gap_days"])) if last else None
            if ready and datetime.now(timezone.utc) < ready:
                return f"the gap after wave {number - 1} runs until {ready:%d.%m.%Y %H:%M}"
            gate = results(settings, conn, campaign=row["id"]).get(number - 1)
            answered = bool(gate and gate["gate"].pause and campaign_store.resumed_after_gate(
                conn, int(row["id"]), number - 1))
            if gate and not gate["gate"].open and not answered:
                if gate["gate"].pause and act:
                    campaign_store.pause(conn, int(row["id"]),
                                         reason=f"wave {number - 1}: {gate['gate'].why}",
                                         by="gate")
                    conn.commit()
                return f"wave {number - 1}: {gate['gate'].why}"
    room = settings.smtp.daily_quota - campaign_store.attempts_since(conn, hours=24)
    if room <= 0:
        return f"the daily quota of {settings.smtp.daily_quota} is used up"
    return ""


def why_not_replace(settings: Settings, conn: Connection, row: Row, w: Row,
                    company_id: int, confirm: str) -> tuple[str, Row | None]:
    """Why one bounced message may not be sent again now, or "" with the message.

    The wave's typed code is asked for again: this is the wave's send route
    posting one more message, and it takes the same deliberate act. A paused
    campaign may still send a replacement, since the pause holds the next
    wave and this one has already gone.
    """
    if (confirm or "").strip().upper() != confirmation(row, w).upper():
        return f"type {confirmation(row, w)} to send", None
    if row["closed_at"]:
        return f"{row['code']} is closed", None
    if not w["sent_at"]:
        return (f"wave {w['number']} has not been sent, and its own send writes to "
                "its recipients"), None
    m = next((m for m in campaign_store.wave_messages(conn, int(w["id"]))
              if int(m["company_id"]) == int(company_id)), None)
    if m is None:
        return f"that firm is not in wave {w['number']}", None
    if m["address_status"] != "bounced":
        return f"{m['address']} did not bounce", None
    if m["replacement_sent_at"]:
        return f"already sent again, to {m['replacement_address']}", None
    if not m["accepted_address"]:
        return ("no replacement address is accepted for this firm yet; find one on "
                "its page"), None
    room = settings.smtp.daily_quota - campaign_store.attempts_since(conn, hours=24)
    if room <= 0:
        return f"the daily quota of {settings.smtp.daily_quota} is used up", None
    return "", m


def precheck_send(settings: Settings, form: dict[str, Any]) -> None:
    """Run synchronously by the console before the job starts."""
    from .. import db
    with db.open_db(settings.database_url, getattr(settings, "modules", ())) as conn:
        row = resolve(conn, form["campaign"])
        w = _wave(conn, row, int(form["wave"]))
        confirm = str(form.get("confirm") or "")
        if str(form.get("replacement") or "").strip():
            reason, _m = why_not_replace(settings, conn, row, w,
                                         int(form["replacement"]), confirm)
        else:
            reason = why_not_send(settings, conn, row, w, confirm)
        if reason:
            raise ValueError(reason)


def send_replacement(settings: Settings, conn: Connection, row: Row, w: Row, *,
                     company_id: int, confirm: str, log: Callable[[str], None],
                     open_sender: Callable[..., Any] | None,
                     sleep: Callable[[float], None]) -> dict[str, Any]:
    """Send a bounced message again, word for word, to the address accepted for it.

    The words are the draft the wave approved and sent, not a fresh render, so
    what goes is what was tested even if a template changed since. No cooldown
    applies: the first message never arrived.
    """
    reason, m = why_not_replace(settings, conn, row, w, company_id, confirm)
    if reason:
        raise ValueError(reason)
    draft_id = scout_store.propose(
        conn, int(m["company_id"]), address=m["accepted_address"], language=m["language"],
        subject=m["subject"] or "", body=m["body"] or "",
        model=f"replacement of draft {m['draft_id']}", digest=f"draft-{m['draft_id']}",
        replace_id=m["replacement_draft_id"],
    )
    if draft_id is None:
        raise ValueError(f"another open draft already holds {m['accepted_address']}")
    scout_store.review_drafts(conn, [draft_id])
    campaign_store.link_replacement(conn, int(m["message_id"]), draft_id)
    conn.commit()
    log(f"{row['code']} wave {w['number']}: {m['company_name']}, {m['address']} "
        f"bounced; the same message goes to {m['accepted_address']}")
    stats = send_stage.run(
        settings, conn, draft_ids=[draft_id], confirm=True,
        programme=row["programme_code"] or "", wave_id=int(w["id"]), kind="message",
        log=log, open_sender=open_sender, sleep=sleep,
    )
    conn.commit()
    return {**stats, "replacement": m["accepted_address"]}


def send_wave(settings: Settings, conn: Connection, *, campaign: str | int, wave: int,
              confirm: str, replacement: int | None = None,
              log: Callable[[str], None] = print,
              open_sender: Callable[..., Any] | None = None,
              sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Post the wave's approved drafts, checked again inside the job.

    With `replacement`, a firm's number, it posts instead that firm's bounced
    message again to the address accepted for it, and nothing else.
    """
    row = resolve(conn, campaign)
    w = _wave(conn, row, wave)
    if replacement is not None:
        return send_replacement(settings, conn, row, w, company_id=int(replacement),
                                confirm=confirm, log=log, open_sender=open_sender,
                                sleep=sleep)
    reason = why_not_send(settings, conn, row, w, confirm)
    if reason:
        raise ValueError(reason)
    messages = _live(campaign_store.wave_messages(conn, int(w["id"])))
    waiting = [int(m["draft_id"]) for m in messages if m["draft_id"] and not m["sent_at"]]
    log(f"{row['code']} wave {w['number']}: {len(waiting)} message(s) to post")
    stats = send_stage.run(
        settings, conn, draft_ids=waiting, confirm=True,
        programme=row["programme_code"] or "", wave_id=int(w["id"]), kind="message",
        limit=int(row["wave_size"]), cooldown_days=int(row["cooldown_days"]),
        log=log, open_sender=open_sender, sleep=sleep,
    )
    if stats["sent"]:
        campaign_store.freeze(conn, int(row["audience_id"]))
    after = _live(campaign_store.wave_messages(conn, int(w["id"])))
    left = [m for m in after if m["draft_id"] and not m["sent_at"]]
    if not left:
        campaign_store.set_sent(conn, int(w["id"]))
    conn.commit()
    done = len(after) - len(left)
    log(f"wave {w['number']}: {done}/{len(after)} sent"
        + ("" if not left else f"; {len(left)} wait for the next run"))
    return {**stats, "wave_done": not left, "left": len(left)}
