"""Audiences and campaigns: the SQL, and nothing else.

The rules live in `sources/audience.py` and `sources/waves.py`, the order of
operations in `stages/audience.py` and `stages/campaign.py`. This file reads
and writes rows. Like the scout store it knows no vocabulary: anything that is
a list of words, the free-mail providers for instance, is handed in by the
caller.

Kept beside `store.py` rather than inside it, the way the scout keeps
`scoring.py` beside its own, because the clients store is already the longest
file in the module and these tables answer one question of their own.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from ...kernel.db import Connection, Row, json_param
from ..scout.store import delete_unsent_drafts, record_state_change

# --- audiences -----------------------------------------------------------------


def add_audience(conn: Connection, *, code: str, name: str, rule: Mapping) -> int:
    code = code.strip().upper()
    if not code:
        raise ValueError("An audience needs a code, e.g. DRYMOS-1.")
    if audience_by_code(conn, code):
        raise ValueError(f"An audience called {code} already exists.")
    row = conn.execute(
        "INSERT INTO crm.audience (code, name, rule) VALUES (%s, %s, %s) RETURNING id",
        (code, name.strip() or code, json_param(dict(rule))),
    ).fetchone()
    return int(row["id"])


def audience(conn: Connection, audience_id: int) -> Row | None:
    return conn.execute("SELECT * FROM crm.audience WHERE id = %s", (audience_id,)).fetchone()


def audience_by_code(conn: Connection, code: str) -> Row | None:
    return conn.execute(
        "SELECT * FROM crm.audience WHERE code = %s", (code.strip().upper(),)
    ).fetchone()


def audiences(conn: Connection) -> list[Row]:
    """Every list, with how many firms it keeps and drops and who uses it."""
    return conn.execute(
        """
        SELECT a.*,
               count(m.id) FILTER (WHERE m.verdict = 'kept')    AS kept,
               count(m.id) FILTER (WHERE m.verdict = 'dropped') AS dropped,
               (SELECT count(*) FROM crm.campaign c WHERE c.audience_id = a.id) AS campaigns
          FROM crm.audience a
          LEFT JOIN crm.audience_member m ON m.audience_id = a.id
         GROUP BY a.id
         ORDER BY a.created_at DESC, a.id DESC
        """
    ).fetchall()


def set_rule(conn: Connection, audience_id: int, rule: Mapping) -> None:
    row = audience(conn, audience_id)
    if row is None:
        raise ValueError(f"No audience {audience_id}.")
    if row["frozen_at"]:
        raise ValueError(
            f"{row['code']} is frozen: a campaign has sent from it. Copy it to "
            "change the rule."
        )
    conn.execute(
        "UPDATE crm.audience SET rule = %s, updated_at = now() WHERE id = %s",
        (json_param(dict(rule)), audience_id),
    )


def freeze(conn: Connection, audience_id: int) -> None:
    conn.execute(
        "UPDATE crm.audience SET frozen_at = coalesce(frozen_at, now()) WHERE id = %s",
        (audience_id,),
    )


def candidates(
    conn: Connection, *, segments: Sequence[str], cantons: Sequence[str] = (),
    programme_id: int | None = None, members_only: bool = False,
) -> tuple[list[Row], list[Row], dict[str, str]]:
    """The firms a rule could keep, every email they hold, and recent domains.

    Firms carry what the rule reads: register status, legal form, capital, the
    bands, the note the address hunt left, the firm's stage on the rule's
    programme, and when the firm was last written to. Addresses carry the same
    per mailbox. The third value maps a domain to when a message last went to
    any mailbox at it, so the cooldown reaches a sibling firm too.
    """
    where = ["TRUE"]
    params: list[Any] = [list(segments), programme_id]
    if cantons:
        where.append("c.canton = ANY(%s)")
        params.append([c.upper() for c in cantons])
    if members_only and programme_id is not None:
        where.append("m.id IS NOT NULL")
    firms = conn.execute(
        f"""
        SELECT c.id AS company_id, c.name, c.canton, c.legal_form_uid,
               c.status = 'ACTIVE' AS active, c.capital::float AS capital,
               array_agg(DISTINCT s.segment ORDER BY s.segment) AS segments,
               h.note AS hunt_note, m.stage AS programme_stage,
               p.stages[1] AS first_stage,
               greatest(
                 (SELECT max(o.posted_at) FROM crm.outbound o
                    JOIN intel.draft d ON d.id = o.draft_id
                   WHERE d.company_id = c.id AND o.kind = 'message'),
                 (SELECT max(d.sent_at) FROM intel.draft d WHERE d.company_id = c.id)
               )::text AS written_at
          FROM core.company c
          JOIN intel.segment s ON s.company_id = c.id AND s.segment = ANY(%s)
          LEFT JOIN intel.address_hunt h ON h.company_id = c.id
          LEFT JOIN crm.programme p ON p.id = %s
          LEFT JOIN crm.programme_member m
                 ON m.programme_id = p.id AND m.company_id = c.id
         WHERE {' AND '.join(where)}
         GROUP BY c.id, h.note, m.stage, m.id, p.stages
         ORDER BY c.id
        """,
        tuple(params),
    ).fetchall()
    ids = [int(f["company_id"]) for f in firms]
    addresses = conn.execute(
        """
        SELECT k.company_id, k.id AS contact_id, lower(k.value) AS value, k.status,
               k.status_at::text AS status_at, k.method, k.suppressed,
               EXISTS (SELECT 1 FROM intel.draft r
                        WHERE r.refused_at IS NOT NULL
                          AND lower(r.address) = lower(k.value)) AS refused,
               greatest(
                 (SELECT max(o.posted_at) FROM crm.outbound o
                   WHERE lower(o.address) = lower(k.value) AND o.kind = 'message'),
                 (SELECT max(d.sent_at) FROM intel.draft d
                   WHERE lower(d.address) = lower(k.value))
               )::text AS written_at
          FROM core.contact k
         WHERE k.kind = 'email' AND k.company_id = ANY(%s)
         ORDER BY k.company_id, k.id
        """,
        (ids,),
    ).fetchall()
    recent = {
        str(r["domain"]): str(r["at"])
        for r in conn.execute(
            """
            SELECT domain, max(at)::text AS at FROM (
                SELECT split_part(lower(address), '@', 2) AS domain, posted_at AS at
                  FROM crm.outbound WHERE kind = 'message' AND posted_at IS NOT NULL
                UNION ALL
                SELECT split_part(lower(address), '@', 2), sent_at
                  FROM intel.draft WHERE sent_at IS NOT NULL AND address IS NOT NULL
            ) t GROUP BY domain
            """
        ).fetchall()
    }
    return firms, addresses, recent


def pins(conn: Connection, audience_id: int) -> dict[int, dict]:
    """Every operator decision on this list, keyed by company."""
    return {
        int(r["company_id"]): dict(r["pinned"])
        for r in conn.execute(
            """
            SELECT company_id, pinned FROM crm.audience_member
             WHERE audience_id = %s AND pinned IS NOT NULL
            """,
            (audience_id,),
        ).fetchall()
    }


def save_members(conn: Connection, audience_id: int, outcomes: Sequence[Any], *,
                 rule_digest: str) -> dict[str, int]:
    """Write a build, keeping row ids, pins and every row a plan refers to.

    Every row is first set to dropped, so a firm handing its mailbox to a
    sibling cannot collide with it on the one-mailbox index halfway through.
    A firm that has left the rule's scope is deleted, unless a campaign has
    planned a message to it: that row stays, dropped, and says why.
    """
    row = audience(conn, audience_id)
    if row is None:
        raise ValueError(f"No audience {audience_id}.")
    if row["frozen_at"]:
        raise ValueError(f"{row['code']} is frozen: a campaign has sent from it.")
    conn.execute(
        "UPDATE crm.audience_member SET verdict = 'dropped', covered_by = NULL "
        "WHERE audience_id = %s", (audience_id,),
    )
    ids: dict[int, int] = {}
    for o in outcomes:
        option = o.option
        written = conn.execute(
            """
            INSERT INTO crm.audience_member
                (audience_id, company_id, contact_id, address, language,
                 language_basis, segment, score, score_parts, verdict,
                 reason_code, reason, built_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (audience_id, company_id) DO UPDATE SET
                contact_id = excluded.contact_id, address = excluded.address,
                language = excluded.language, language_basis = excluded.language_basis,
                segment = excluded.segment, score = excluded.score,
                score_parts = excluded.score_parts, verdict = excluded.verdict,
                reason_code = excluded.reason_code, reason = excluded.reason,
                built_at = now()
            RETURNING id
            """,
            (audience_id, o.company_id,
             option.contact_id if option else None,
             option.address if option else None,
             o.language or None, o.language_basis or None, o.segment,
             o.score, json_param(o.parts or {}), o.verdict, o.reason_code, o.reason),
        ).fetchone()
        ids[o.company_id] = int(written["id"])
    for o in outcomes:
        if o.covered_by is not None and o.covered_by in ids:
            conn.execute(
                "UPDATE crm.audience_member SET covered_by = %s WHERE id = %s",
                (ids[o.covered_by], ids[o.company_id]),
            )
    planned = {
        int(r["member_id"]) for r in conn.execute(
            """
            SELECT cm.member_id FROM crm.campaign_message cm
              JOIN crm.audience_member m ON m.id = cm.member_id
             WHERE m.audience_id = %s
            """,
            (audience_id,),
        ).fetchall()
    }
    gone = [
        int(r["id"]) for r in conn.execute(
            "SELECT id FROM crm.audience_member WHERE audience_id = %s "
            "AND NOT (company_id = ANY(%s))",
            (audience_id, list(ids)),
        ).fetchall()
    ]
    kept_planned = [g for g in gone if g in planned]
    removed = [g for g in gone if g not in planned]
    if kept_planned:
        conn.execute(
            """
            UPDATE crm.audience_member
               SET verdict = 'dropped', reason_code = 'out-of-scope',
                   reason = 'no longer within the list''s rule; kept because a campaign planned it'
             WHERE id = ANY(%s)
            """,
            (kept_planned,),
        )
    if removed:
        conn.execute("DELETE FROM crm.audience_member WHERE id = ANY(%s)", (removed,))
    conn.execute(
        "UPDATE crm.audience SET built_at = now(), rule_digest = %s, updated_at = now() "
        "WHERE id = %s",
        (rule_digest, audience_id),
    )
    kept = sum(1 for o in outcomes if o.verdict == "kept")
    return {"firms": len(outcomes), "kept": kept, "dropped": len(outcomes) - kept,
            "removed": len(removed), "retained": len(kept_planned)}


def members(conn: Connection, audience_id: int, *, verdict: str | None = None,
            canton: str | None = None, reason: str | None = None) -> list[Row]:
    """The list's rows with the firm's name and canton, by canton then score."""
    where = ["m.audience_id = %s"]
    params: list[Any] = [audience_id]
    if verdict:
        where.append("m.verdict = %s")
        params.append(verdict)
    if canton:
        where.append("c.canton = %s")
        params.append(canton.upper())
    if reason:
        where.append("m.reason_code = %s")
        params.append(reason)
    return conn.execute(
        f"""
        SELECT m.*, c.name AS company_name, c.uid, c.canton, c.legal_seat,
               k.method AS contact_method, k.status AS contact_status,
               cov.company_id AS covered_by_company,
               (SELECT name FROM core.company WHERE id = cov.company_id) AS covered_by_name
          FROM crm.audience_member m
          JOIN core.company c ON c.id = m.company_id
          LEFT JOIN core.contact k ON k.id = m.contact_id
          LEFT JOIN crm.audience_member cov ON cov.id = m.covered_by
         WHERE {' AND '.join(where)}
         ORDER BY c.canton NULLS LAST, m.score DESC NULLS LAST, c.name
        """,
        tuple(params),
    ).fetchall()


def member(conn: Connection, member_id: int) -> Row | None:
    return conn.execute(
        """
        SELECT m.*, c.name AS company_name, c.canton, a.code AS audience_code,
               a.frozen_at AS audience_frozen_at
          FROM crm.audience_member m
          JOIN core.company c ON c.id = m.company_id
          JOIN crm.audience a ON a.id = m.audience_id
         WHERE m.id = %s
        """,
        (member_id,),
    ).fetchone()


def member_addresses(conn: Connection, member_id: int) -> list[Row]:
    """Every email the member's firm holds, for an operator choosing another."""
    return conn.execute(
        """
        SELECT k.id, lower(k.value) AS value, k.status, k.method, k.suppressed
          FROM crm.audience_member m
          JOIN core.contact k ON k.company_id = m.company_id AND k.kind = 'email'
         WHERE m.id = %s
         ORDER BY k.suppressed, k.value
        """,
        (member_id,),
    ).fetchall()


def set_pin(conn: Connection, member_id: int, pin: Mapping | None) -> None:
    """Store an operator's decision, or clear it with None. Applied at the next build."""
    conn.execute(
        "UPDATE crm.audience_member SET pinned = %s WHERE id = %s",
        (json_param(dict(pin)) if pin else None, member_id),
    )


def funnel_counts(conn: Connection, audience_id: int) -> dict[str, int]:
    return {
        str(r["reason_code"]): int(r["n"])
        for r in conn.execute(
            """
            SELECT reason_code, count(*) AS n FROM crm.audience_member
             WHERE audience_id = %s GROUP BY reason_code
            """,
            (audience_id,),
        ).fetchall()
    }


def stale_domains(conn: Connection, audience_id: int, *, max_age_days: int) -> list[str]:
    """Domains this list holds whose DNS verdict is missing or older than allowed."""
    return [
        str(r["domain"]) for r in conn.execute(
            """
            SELECT DISTINCT split_part(lower(k.value), '@', 2) AS domain
              FROM crm.audience_member m
              JOIN core.contact k ON k.company_id = m.company_id AND k.kind = 'email'
             WHERE m.audience_id = %s AND NOT k.suppressed
               AND (k.status_at IS NULL OR k.status_at < now() - make_interval(days => %s))
             ORDER BY 1
            """,
            (audience_id, max_age_days),
        ).fetchall()
    ]


# --- campaigns -------------------------------------------------------------------

CAMPAIGN_FIELDS = (
    "name", "templates", "strategy", "strategy_params", "pilot_size", "wave_size",
    "gap_days", "start_on", "bounce_ceiling", "settle_hours", "cooldown_days",
    "programme_id", "bilingual",
)


def add_campaign(conn: Connection, *, code: str, name: str, audience_id: int,
                 templates: str, strategy: str, programme_id: int | None = None,
                 **fields: Any) -> int:
    code = code.strip().upper()
    if not code:
        raise ValueError("A campaign needs a code, e.g. DRYMOS-A.")
    if campaign_by_code(conn, code):
        raise ValueError(f"A campaign called {code} already exists.")
    extra = {k: v for k, v in fields.items() if k in CAMPAIGN_FIELDS and v is not None}
    columns = ["code", "name", "audience_id", "templates", "strategy", "programme_id",
               *extra]
    values = [code, name.strip() or code, audience_id, templates, strategy, programme_id,
              *[json_param(v) if k == "strategy_params" else v for k, v in extra.items()]]
    row = conn.execute(
        f"INSERT INTO crm.campaign ({', '.join(columns)}) "
        f"VALUES ({', '.join(['%s'] * len(columns))}) RETURNING id",
        tuple(values),
    ).fetchone()
    return int(row["id"])


_CAMPAIGN_SELECT = """
    SELECT k.*, a.code AS audience_code, a.name AS audience_name,
           a.frozen_at AS audience_frozen_at, p.code AS programme_code
      FROM crm.campaign k
      JOIN crm.audience a ON a.id = k.audience_id
      LEFT JOIN crm.programme p ON p.id = k.programme_id
"""


def campaign(conn: Connection, campaign_id: int) -> Row | None:
    return conn.execute(_CAMPAIGN_SELECT + " WHERE k.id = %s", (campaign_id,)).fetchone()


def campaign_by_code(conn: Connection, code: str) -> Row | None:
    return conn.execute(
        _CAMPAIGN_SELECT + " WHERE k.code = %s", (code.strip().upper(),)
    ).fetchone()


def campaigns(conn: Connection) -> list[Row]:
    return conn.execute(
        _CAMPAIGN_SELECT.replace(
            "SELECT k.*,",
            "SELECT k.*, (SELECT count(*) FROM crm.campaign_wave w WHERE w.campaign_id = k.id) AS waves,"
            " (SELECT count(*) FROM crm.campaign_wave w WHERE w.campaign_id = k.id"
            "   AND w.sent_at IS NOT NULL) AS waves_sent,",
        ) + " ORDER BY k.created_at DESC, k.id DESC"
    ).fetchall()


def update_campaign(conn: Connection, campaign_id: int, **fields: Any) -> None:
    chosen = {k: v for k, v in fields.items() if k in CAMPAIGN_FIELDS}
    if not chosen:
        return
    sets = ", ".join(f"{k} = %s" for k in chosen)
    conn.execute(
        f"UPDATE crm.campaign SET {sets}, updated_at = now() WHERE id = %s",
        (*[json_param(v) if k == "strategy_params" else v for k, v in chosen.items()],
         campaign_id),
    )


def has_sent(conn: Connection, campaign_id: int) -> bool:
    return bool(conn.execute(
        """
        SELECT 1 FROM crm.outbound o JOIN crm.campaign_wave w ON w.id = o.wave_id
         WHERE w.campaign_id = %s AND o.kind = 'message' LIMIT 1
        """,
        (campaign_id,),
    ).fetchone())


def kept_members(conn: Connection, audience_id: int) -> list[Row]:
    """What a plan cuts into waves: the kept rows, with the firm's canton."""
    return conn.execute(
        """
        SELECT m.id AS member_id, c.name AS company, coalesce(c.canton, '?') AS canton,
               coalesce(m.language, 'de') AS language, m.segment,
               coalesce(m.score, 0)::float AS score
          FROM crm.audience_member m
          JOIN core.company c ON c.id = m.company_id
         WHERE m.audience_id = %s AND m.verdict = 'kept'
        """,
        (audience_id,),
    ).fetchall()


def replace_plan(conn: Connection, campaign_id: int, waves: Sequence[Any]) -> None:
    """Throw away the unsent plan and its unsent drafts, and write this one.

    Refused once anything has gone out: the waves already sent are a record of
    what happened, and re-cutting the list around them would rewrite it.
    """
    if has_sent(conn, campaign_id):
        raise ValueError(
            "This campaign has already sent a wave, so its plan is fixed. Pause "
            "it, or start another campaign from a copy of its list."
        )
    drafts = [
        int(r["draft_id"]) for r in conn.execute(
            "SELECT draft_id FROM crm.campaign_message WHERE campaign_id = %s "
            "AND draft_id IS NOT NULL", (campaign_id,),
        ).fetchall()
    ]
    conn.execute("DELETE FROM crm.campaign_wave WHERE campaign_id = %s", (campaign_id,))
    delete_unsent_drafts(conn, drafts)
    for wave in waves:
        wave_id = int(conn.execute(
            """
            INSERT INTO crm.campaign_wave (campaign_id, number, pilot, label, planned_for)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (campaign_id, wave.number, wave.pilot, wave.label, wave.planned_for),
        ).fetchone()["id"])
        for member_id in wave.member_ids:
            conn.execute(
                "INSERT INTO crm.campaign_message (campaign_id, wave_id, member_id) "
                "VALUES (%s, %s, %s)",
                (campaign_id, wave_id, member_id),
            )
    conn.execute(
        "UPDATE crm.campaign SET planned_at = now(), updated_at = now() WHERE id = %s",
        (campaign_id,),
    )


def waves(conn: Connection, campaign_id: int) -> list[Row]:
    """The waves in order, with how far each has got."""
    return conn.execute(
        """
        SELECT w.*,
               count(cm.id) AS messages,
               count(cm.id) FILTER (WHERE cm.dropped_at IS NULL) AS live,
               count(cm.draft_id) FILTER (WHERE cm.dropped_at IS NULL) AS drafted,
               count(d.id) FILTER (WHERE d.reviewed_at IS NOT NULL
                                     AND cm.dropped_at IS NULL) AS reviewed,
               count(d.id) FILTER (WHERE d.sent_at IS NOT NULL) AS sent,
               max(d.sent_at) AS last_sent_at
          FROM crm.campaign_wave w
          LEFT JOIN crm.campaign_message cm ON cm.wave_id = w.id
          LEFT JOIN intel.draft d ON d.id = cm.draft_id
         WHERE w.campaign_id = %s
         GROUP BY w.id
         ORDER BY w.number
        """,
        (campaign_id,),
    ).fetchall()


def wave(conn: Connection, campaign_id: int, number: int) -> Row | None:
    for row in waves(conn, campaign_id):
        if int(row["number"]) == int(number):
            return row
    return None


def wave_messages(conn: Connection, wave_id: int) -> list[Row]:
    """A wave's recipients, each with its member row and its draft."""
    return conn.execute(
        """
        SELECT cm.id AS message_id, cm.member_id, cm.draft_id, cm.variant,
               cm.dropped_at, cm.dropped_reason, cm.replacement_draft_id,
               m.company_id, m.address, m.language, m.segment, m.score,
               m.verdict AS member_verdict, m.reason AS member_reason,
               c.name AS company_name, c.canton,
               d.subject, d.body, d.reviewed_at, d.sent_at, d.refused_at,
               k.status AS address_status,
               rd.address AS replacement_address, rd.sent_at AS replacement_sent_at,
               accepted.address AS accepted_address
          FROM crm.campaign_message cm
          JOIN crm.audience_member m ON m.id = cm.member_id
          JOIN core.company c ON c.id = m.company_id
          LEFT JOIN intel.draft d ON d.id = cm.draft_id
          LEFT JOIN intel.draft rd ON rd.id = cm.replacement_draft_id
          LEFT JOIN core.contact k ON k.company_id = m.company_id AND k.kind = 'email'
                                  AND lower(k.value) = lower(m.address)
          LEFT JOIN LATERAL (
                SELECT p.address FROM intel.address_proposal p
                  JOIN core.contact pk ON pk.id = p.contact_id
                 WHERE p.company_id = m.company_id AND p.decision = 'accepted'
                   AND pk.suppressed IS NOT TRUE AND coalesce(pk.status, '') <> 'bounced'
                 ORDER BY p.decided_at DESC, p.id DESC LIMIT 1
          ) accepted ON TRUE
         WHERE cm.wave_id = %s
         ORDER BY m.score DESC NULLS LAST, c.name
        """,
        (wave_id,),
    ).fetchall()


def bounced_messages_of(conn: Connection, company_id: int) -> list[Row]:
    """The campaign messages to this firm whose address bounced, newest first."""
    return conn.execute(
        """
        SELECT k.id AS campaign_id, k.code, w.number, m.address,
               rd.address AS replacement_address, rd.sent_at AS replacement_sent_at
          FROM crm.campaign_message cm
          JOIN crm.audience_member m ON m.id = cm.member_id
          JOIN crm.campaign k ON k.id = cm.campaign_id
          JOIN crm.campaign_wave w ON w.id = cm.wave_id
          JOIN intel.draft d ON d.id = cm.draft_id AND d.sent_at IS NOT NULL
          JOIN core.contact c ON c.company_id = m.company_id AND c.kind = 'email'
                             AND lower(c.value) = lower(m.address) AND c.status = 'bounced'
          LEFT JOIN intel.draft rd ON rd.id = cm.replacement_draft_id
         WHERE m.company_id = %s
         ORDER BY d.sent_at DESC
        """,
        (company_id,),
    ).fetchall()


def link_replacement(conn: Connection, message_id: int, draft_id: int) -> None:
    """Record the second draft a bounced message was sent again as."""
    conn.execute(
        "UPDATE crm.campaign_message SET replacement_draft_id = %s WHERE id = %s",
        (draft_id, message_id),
    )


def link_draft(conn: Connection, message_id: int, draft_id: int | None,
               variant: str | None) -> None:
    conn.execute(
        "UPDATE crm.campaign_message SET draft_id = %s, variant = %s WHERE id = %s",
        (draft_id, variant, message_id),
    )


def drop_message(conn: Connection, message_id: int, reason: str) -> None:
    conn.execute(
        """
        UPDATE crm.campaign_message SET dropped_at = coalesce(dropped_at, now()),
               dropped_reason = %s
         WHERE id = %s
        """,
        (reason, message_id),
    )


def set_rendered(conn: Connection, wave_id: int, digest: str) -> None:
    """A render clears the test and the approval: they were of other text."""
    conn.execute(
        """
        UPDATE crm.campaign_wave
           SET rendered_at = now(), template_digest = %s,
               tested_at = NULL, approved_at = NULL, approved_by = NULL
         WHERE id = %s
        """,
        (digest, wave_id),
    )


def delivered_tests(conn: Connection, wave_id: int) -> list[Row]:
    """Every test the operator received from another wave, oldest first, with its words.

    Any campaign counts: a new round of the same text is the text the operator
    already read. A wave counts only while it is marked tested, which its next
    render clears, so its drafts are still the words its test carried. A test that
    failed counts for nothing. One row per wave and variant, carrying the subject
    and text of one draft of that variant, which every draft of it shares.
    """
    return conn.execute(
        """
        SELECT DISTINCT ON (w.tested_at, w.id, o.variant)
               c.code, w.number, o.variant, d.subject, d.body
          FROM crm.campaign_wave w
          JOIN crm.campaign c ON c.id = w.campaign_id
          JOIN crm.outbound o ON o.wave_id = w.id AND o.kind = 'test'
                             AND o.posted_at IS NOT NULL
          JOIN crm.campaign_message cm ON cm.wave_id = w.id AND cm.variant = o.variant
          JOIN intel.draft d ON d.id = cm.draft_id
         WHERE w.id <> %s AND w.tested_at IS NOT NULL
         ORDER BY w.tested_at, w.id, o.variant, d.id
        """,
        (wave_id,),
    ).fetchall()


def set_tested(conn: Connection, wave_id: int) -> None:
    conn.execute(
        "UPDATE crm.campaign_wave SET tested_at = now(), approved_at = NULL, "
        "approved_by = NULL WHERE id = %s", (wave_id,),
    )


def approve(conn: Connection, wave_id: int, *, by: str) -> None:
    conn.execute(
        "UPDATE crm.campaign_wave SET approved_at = now(), approved_by = %s WHERE id = %s",
        (by, wave_id),
    )


def unapprove(conn: Connection, wave_id: int) -> None:
    conn.execute(
        "UPDATE crm.campaign_wave SET approved_at = NULL, approved_by = NULL "
        "WHERE id = %s AND sent_at IS NULL", (wave_id,),
    )


def set_sent(conn: Connection, wave_id: int) -> None:
    conn.execute(
        "UPDATE crm.campaign_wave SET sent_at = coalesce(sent_at, now()) WHERE id = %s",
        (wave_id,),
    )


def pause(conn: Connection, campaign_id: int, *, reason: str, by: str) -> None:
    row = campaign(conn, campaign_id)
    if row is None or row["paused_at"]:
        return
    conn.execute(
        "UPDATE crm.campaign SET paused_at = now(), paused_reason = %s, "
        "updated_at = now() WHERE id = %s", (reason, campaign_id),
    )
    record_state_change(conn, entity="campaign", entity_id=campaign_id,
                        from_state="running", to_state="paused", by=by, reason=reason)


def resume(conn: Connection, campaign_id: int, *, reason: str, by: str) -> None:
    if not reason.strip():
        raise ValueError("Resuming a paused campaign needs a written reason.")
    row = campaign(conn, campaign_id)
    if row is None or not row["paused_at"]:
        return
    conn.execute(
        "UPDATE crm.campaign SET paused_at = NULL, paused_reason = NULL, "
        "updated_at = now() WHERE id = %s", (campaign_id,),
    )
    record_state_change(conn, entity="campaign", entity_id=campaign_id,
                        from_state="paused", to_state="running", by=by, reason=reason)


def resumed_after_gate(conn: Connection, campaign_id: int, wave_number: int) -> bool:
    """Whether a person resumed the campaign after the gate paused it over this
    wave's bounces. That resume, with its written reason, is the answer the
    pause asked for; a resume that came before the pause, or after a pause for
    another reason, is not. Rows are ordered by id rather than by time: the
    log keeps whole seconds, and a resume can share its pause's second."""
    return conn.execute(
        """
        SELECT 1 FROM audit.state_change r
         WHERE r.entity = 'campaign' AND r.entity_id = %s
           AND r.from_state = 'paused' AND r.to_state = 'running'
           AND r.id > (SELECT max(p.id) FROM audit.state_change p
                        WHERE p.entity = 'campaign' AND p.entity_id = %s
                          AND p.to_state = 'paused' AND p."by" = 'gate'
                          AND p.reason LIKE %s)
         LIMIT 1
        """,
        (campaign_id, campaign_id, f"wave {int(wave_number)}:%"),
    ).fetchone() is not None


def tests_of_wave(conn: Connection, wave_id: int) -> list[Row]:
    """Every test this wave sent the operator, newest first."""
    return conn.execute(
        "SELECT * FROM crm.outbound WHERE wave_id = %s AND kind = 'test' "
        "ORDER BY attempted_at DESC", (wave_id,),
    ).fetchall()


def wave_of_draft(conn: Connection, draft_id: int) -> Row | None:
    """The campaign wave a draft belongs to, if any, with its campaign."""
    return conn.execute(
        """
        SELECT w.id AS wave_id, w.number, k.id AS campaign_id, k.code AS campaign,
               p.code AS programme_code
          FROM crm.campaign_message cm
          JOIN crm.campaign_wave w ON w.id = cm.wave_id
          JOIN crm.campaign k ON k.id = cm.campaign_id
          LEFT JOIN crm.programme p ON p.id = k.programme_id
         WHERE cm.draft_id = %s
        """,
        (draft_id,),
    ).fetchone()


def programme_for_draft(conn: Connection, draft_id: int) -> str | None:
    row = wave_of_draft(conn, draft_id)
    return str(row["programme_code"]) if row and row["programme_code"] else None


# --- the outbound log ----------------------------------------------------------


def record_attempt(conn: Connection, *, kind: str, address: str, subject: str,
                   message_id: str, draft_id: int | None = None,
                   wave_id: int | None = None, variant: str | None = None) -> int:
    """Log a post before the server is asked. The caller commits straight after."""
    row = conn.execute(
        """
        INSERT INTO crm.outbound (kind, address, subject, message_id, draft_id,
                                  wave_id, variant)
        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (kind, address.strip().lower(), subject, message_id, draft_id, wave_id, variant),
    ).fetchone()
    return int(row["id"])


def record_posted(conn: Connection, outbound_id: int) -> None:
    conn.execute("UPDATE crm.outbound SET posted_at = now() WHERE id = %s", (outbound_id,))


def record_failed(conn: Connection, outbound_id: int, failure: str) -> None:
    conn.execute(
        "UPDATE crm.outbound SET failed_at = now(), failure = %s WHERE id = %s",
        (failure[:500], outbound_id),
    )


def attempts_since(conn: Connection, *, hours: int = 24) -> int:
    """Every attempt in the window, whatever its outcome: the host counts them all."""
    return int(conn.execute(
        "SELECT count(*) AS n FROM crm.outbound "
        "WHERE attempted_at > now() - make_interval(hours => %s)", (hours,),
    ).fetchone()["n"])


def oldest_attempt_since(conn: Connection, *, hours: int = 24) -> str | None:
    """When the oldest attempt in the window was made, so the room's return is known."""
    row = conn.execute(
        "SELECT min(attempted_at)::text AS at FROM crm.outbound "
        "WHERE attempted_at > now() - make_interval(hours => %s)", (hours,),
    ).fetchone()
    return row["at"] if row and row["at"] else None


def unsettled_for_draft(conn: Connection, draft_id: int) -> Row | None:
    return conn.execute(
        """
        SELECT * FROM crm.outbound
         WHERE draft_id = %s AND posted_at IS NULL AND failed_at IS NULL
         ORDER BY attempted_at DESC LIMIT 1
        """,
        (draft_id,),
    ).fetchone()


def unsettled(conn: Connection) -> list[Row]:
    return conn.execute(
        "SELECT * FROM crm.outbound WHERE posted_at IS NULL AND failed_at IS NULL "
        "ORDER BY attempted_at"
    ).fetchall()


def settle(conn: Connection, outbound_id: int, *, posted: bool, note: str = "") -> None:
    """A person's answer to an attempt whose outcome the program never learned."""
    if posted:
        conn.execute(
            "UPDATE crm.outbound SET posted_at = attempted_at WHERE id = %s "
            "AND posted_at IS NULL AND failed_at IS NULL", (outbound_id,),
        )
    else:
        conn.execute(
            "UPDATE crm.outbound SET failed_at = now(), failure = %s WHERE id = %s "
            "AND posted_at IS NULL AND failed_at IS NULL",
            (note or "settled by hand: not delivered", outbound_id),
        )


def last_written(conn: Connection, *, address: str = "", company_id: int | None = None,
                 domain: str = "") -> str | None:
    """When a real message last went to this mailbox, firm or domain, if ever."""
    clauses, params = [], []
    if address:
        clauses.append("lower(address) = %s")
        params.append(address.strip().lower())
    if domain:
        clauses.append("split_part(lower(address), '@', 2) = %s")
        params.append(domain.strip().lower())
    if company_id is not None:
        clauses.append("company_id = %s")
        params.append(company_id)
    if not clauses:
        return None
    row = conn.execute(
        f"""
        SELECT max(at)::text AS at FROM (
            SELECT o.address, o.posted_at AS at, d.company_id
              FROM crm.outbound o LEFT JOIN intel.draft d ON d.id = o.draft_id
             WHERE o.kind = 'message' AND o.posted_at IS NOT NULL
            UNION ALL
            SELECT address, sent_at, company_id FROM intel.draft WHERE sent_at IS NOT NULL
        ) t WHERE {' OR '.join(clauses)}
        """,
        tuple(params),
    ).fetchone()
    return row["at"] if row and row["at"] else None


# --- results -------------------------------------------------------------------


def wave_outcomes(conn: Connection, campaign_id: int, *,
                  free_domains: Sequence[str] = ()) -> list[Row]:
    """One row per message sent, with what came of it, read from existing data.

    No open tracking, by design. A bounce is a mailbox that turned `bounced`
    after the send; a refusal is a STOP that suppressed it after the send. A
    reply is matched by the Message-ID the send recorded, then by the address
    written to, then by its domain unless that domain is a consumer provider,
    because over shared mailboxes `match_company` files a reply under whichever
    sibling it meets first.
    """
    return conn.execute(
        """
        SELECT w.id AS wave_id, w.number AS wave, cm.id AS message_id,
               m.company_id, c.name AS company_name, m.address,
               d.sent_at::text AS sent_at, o.message_id AS smtp_id,
               EXISTS (SELECT 1 FROM core.contact k
                        WHERE lower(k.value) = m.address AND k.status = 'bounced'
                          AND k.status_at >= d.sent_at) AS bounced,
               EXISTS (SELECT 1 FROM core.contact k
                        WHERE lower(k.value) = m.address AND k.status = 'deferred'
                          AND k.status_at >= d.sent_at) AS deferred,
               EXISTS (SELECT 1 FROM core.contact k
                        WHERE lower(k.value) = m.address AND k.suppressed
                          AND k.status = 'refused' AND k.status_at >= d.sent_at) AS refused,
               (pm.stage = ANY(k2.enrolled_stages) OR pm.platform IS NOT NULL) AS enrolled,
               coalesce((
                 SELECT jsonb_agg(jsonb_build_object(
                          'subject', e.subject, 'from', e.from_email,
                          'at', e.sent_at::text) ORDER BY e.sent_at)
                   FROM crm.email_message e
                  WHERE e.direction = 'in' AND e.sent_at >= d.sent_at
                    AND (e.thread_key = o.message_id
                         OR lower(e.from_email) = m.address
                         OR (split_part(lower(e.from_email), '@', 2)
                               = split_part(m.address, '@', 2)
                             AND NOT (split_part(m.address, '@', 2) = ANY(%s))))
               ), '[]'::jsonb) AS inbound
          FROM crm.campaign_message cm
          JOIN crm.campaign_wave w ON w.id = cm.wave_id
          JOIN crm.campaign k2 ON k2.id = cm.campaign_id
          JOIN crm.audience_member m ON m.id = cm.member_id
          JOIN core.company c ON c.id = m.company_id
          JOIN intel.draft d ON d.id = cm.draft_id AND d.sent_at IS NOT NULL
          LEFT JOIN LATERAL (
              SELECT message_id FROM crm.outbound
               WHERE draft_id = d.id AND kind = 'message' AND posted_at IS NOT NULL
               ORDER BY posted_at DESC LIMIT 1
          ) o ON TRUE
          LEFT JOIN crm.programme_member pm
                 ON pm.programme_id = k2.programme_id AND pm.company_id = m.company_id
         WHERE cm.campaign_id = %s
         ORDER BY w.number, c.name
        """,
        (list(free_domains), campaign_id),
    ).fetchall()
