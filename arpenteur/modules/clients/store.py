"""Reading and writing the client side.

Every statement touching `crm` lives here. The scout's store is a separate
module and this one calls into it only through `core.company` by id, which is
the single reference the client side needs and the only one the boundary
allows.

One rename came with the port. The table is `crm.interaction` rather than
`event`, because these rows are correspondence, calls, meetings, notes and
decisions read three ways — a timeline, a diary and an audit record — and
"event" reads as though only the calendar entries belonged. The functions here
follow the table.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from ...kernel.db import Connection, Row, json_param, now
# The clients module requires the scout, so it may reach for it. This is the
# only thing it takes: the audit table, which points at nothing and is written
# to by every module that has a status to overwrite.
from ..scout.store import record_state_change

CLIENT_STATES = ("active", "prospect", "dormant", "former")
PROJECT_STATES = ("proposed", "active", "paused", "delivered", "cancelled")
INTERACTION_KINDS = (
    "email", "call", "meeting", "deadline", "note", "milestone", "document",
    "decision",
)
INTERACTION_STATES = ("planned", "done", "cancelled")

# What belongs in the diary, as opposed to what belongs only in the file.
#
# These are two readings of one table and they are not the same set. A calendar
# answers "where do I have to be, and what falls due"; a timeline answers "what
# has happened with this client". An email logged at 09:20, a document
# arriving, a project status changing and a note typed after a call are all the
# record moving, and none of them is an appointment. Putting them in a month
# grid buries the one meeting that week under forty entries nobody has to turn
# up to, which is the failure this list exists to prevent.
#
# Anything still `planned` is in the diary whatever its kind, because something
# you have committed to on a stated date is exactly what a diary is for. A
# meeting or a deadline stays in it afterwards too, because "when did we
# actually meet them" is a question asked of a calendar.
APPOINTMENT_KINDS = ("meeting", "deadline")


# --- the client ---------------------------------------------------------------


def make_code(name: str, taken: set[str], *, length: int = 14) -> str:
    """A human reference from a company name: BOIS-GENTIL.

    Quoted on invoices and in conversation, so it is folded to ASCII rather
    than left carrying accents that a mail client or a bank form will mangle.
    """
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c))
    words = [w for w in re.split(r"[^A-Za-z0-9]+", ascii_only) if w]
    skip = {"la", "le", "les", "de", "du", "des", "sa", "sarl", "ag", "gmbh",
            "fondation", "association", "societe", "the"}
    kept = [w for w in words if w.lower() not in skip] or words
    base = "-".join(kept).upper()[:length].strip("-") or "CLIENT"
    if base not in taken:
        return base
    for n in range(2, 100):
        candidate = f"{base}-{n}"
        if candidate not in taken:
            return candidate
    raise RuntimeError(f"cannot find a free code for {name!r}")


def promote_to_client(
    conn: Connection, company_id: int, *, code: str = "", owner: str | None = None
) -> int:
    """Make a surveyed company a client. A reference, not a copy.

    The register entry, the officers, the research and the notes are exactly
    what is worth having on the first day of an engagement, so nothing is
    duplicated: the client row points at the company.
    """
    existing = conn.execute(
        "SELECT id FROM crm.client WHERE company_id = %s", (company_id,)
    ).fetchone()
    if existing:
        return int(existing["id"])
    company = conn.execute(
        "SELECT name FROM core.company WHERE id = %s", (company_id,)
    ).fetchone()
    if company is None:
        raise ValueError(f"no company with id {company_id}")
    taken = {row["code"] for row in conn.execute("SELECT code FROM crm.client")}
    row = conn.execute(
        "INSERT INTO crm.client (company_id, code, status, since, owner, created_at)"
        " VALUES (%s, %s, 'active', %s, %s, %s) RETURNING id",
        (company_id, code or make_code(company["name"], taken),
         now()[:10], owner, now()),
    ).fetchone()
    # Becoming a client is the transition the whole survey is pointed at, and
    # it belongs in the same history as every other one.
    record_state_change(
        conn, entity="client", entity_id=int(row["id"]), company_id=company_id,
        from_state=None, to_state="active", by=owner,
    )
    return int(row["id"])


def update_client(conn: Connection, client_id: int, **fields: Any) -> None:
    allowed = {"code", "status", "since", "owner", "brief"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return
    if "status" in sets:
        before = conn.execute(
            "SELECT status, company_id FROM crm.client WHERE id = %s", (client_id,)
        ).fetchone()
        if before is not None:
            record_state_change(
                conn, entity="client", entity_id=client_id,
                company_id=int(before["company_id"]),
                from_state=before["status"], to_state=sets["status"],
                by=fields.get("by"),
            )
    assignments = ", ".join(f"{k} = %({k})s" for k in sets)
    conn.execute(
        f"UPDATE crm.client SET {assignments}, updated_at = %(now)s WHERE id = %(id)s",
        {**sets, "now": now(), "id": client_id},
    )


def client(conn: Connection, client_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM crm.client WHERE id = %s", (client_id,)
    ).fetchone()


def client_for_company(conn: Connection, company_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM crm.client WHERE company_id = %s", (company_id,)
    ).fetchone()


def clients_by_company(conn: Connection, company_ids: list[int]) -> dict[int, Row]:
    """Which of these companies are already clients, in one query.

    For pages that draw many companies at once and want to say so. Asking per
    company is how a board of forty cards becomes forty round trips, and the
    board is a scout page: it cannot import this module, so the console passes
    the answer in.
    """
    if not company_ids:
        return {}
    return {
        int(row["company_id"]): row
        for row in conn.execute(
            "SELECT id, code, status, company_id FROM crm.client"
            " WHERE company_id = ANY(%s)",
            (list(company_ids),),
        )
    }


def clients(conn: Connection, *, status: str = "") -> list[Row]:
    """The portfolio, with the numbers that say whether anything is adrift."""
    where = "WHERE c.status = %s" if status else ""
    return conn.execute(
        f"""SELECT c.*, co.name, co.uid, co.canton, co.legal_form_label,
                   (SELECT COUNT(*) FROM crm.project p
                     WHERE p.client_id = c.id AND p.status = 'active') AS active_projects,
                   (SELECT COUNT(*) FROM crm.project p WHERE p.client_id = c.id) AS projects,
                   (SELECT MAX(e.occurred_at) FROM crm.interaction e
                     WHERE e.client_id = c.id AND e.state = 'done') AS last_contact,
                   (SELECT MIN(e.occurred_at) FROM crm.interaction e
                     WHERE e.client_id = c.id AND e.state = 'planned'
                       AND e.occurred_at >= current_date) AS next_at
              FROM crm.client c JOIN core.company co ON co.id = c.company_id
              {where}
             ORDER BY c.status, co.name""",
        ([status] if status else []),
    ).fetchall()


# --- projects -----------------------------------------------------------------


def add_project(
    conn: Connection, client_id: int, name: str, *, code: str = "",
    status: str = "proposed", billing: str | None = None,
    budget: float | None = None, summary: str | None = None,
    started_at: str | None = None, due_at: str | None = None,
) -> int:
    """A piece of work with its own reference.

    BOIS-GENTIL-01, because a project outlives the conversation that started it
    and has to be nameable in a contract, an invoice and a file name.
    """
    row = conn.execute(
        "SELECT code FROM crm.client WHERE id = %s", (client_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no client with id {client_id}")
    if not code:
        used = conn.execute(
            "SELECT COUNT(*) AS n FROM crm.project WHERE client_id = %s", (client_id,)
        ).fetchone()["n"]
        taken = {r["code"] for r in conn.execute("SELECT code FROM crm.project")}
        for n in range(used + 1, used + 100):
            candidate = f"{row['code']}-{n:02d}"
            if candidate not in taken:
                code = candidate
                break
    created = conn.execute(
        "INSERT INTO crm.project (client_id, code, name, status, billing, budget,"
        " summary, started_at, due_at, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (client_id, code, name, status, billing, budget, summary,
         started_at, due_at, now()),
    ).fetchone()
    return int(created["id"])


def update_project(conn: Connection, project_id: int, **fields: Any) -> None:
    allowed = {"name", "status", "billing", "budget", "summary", "started_at",
               "due_at", "closed_at", "code"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return
    if "status" in sets:
        before = conn.execute(
            "SELECT p.status, c.company_id FROM crm.project p"
            " JOIN crm.client c ON c.id = p.client_id WHERE p.id = %s",
            (project_id,),
        ).fetchone()
        if before is not None:
            record_state_change(
                conn, entity="project", entity_id=project_id,
                company_id=int(before["company_id"]),
                from_state=before["status"], to_state=sets["status"],
                by=fields.get("by"),
            )
    # Closing a project stamps the date if the caller did not, so a delivered
    # project always says when rather than only that.
    if sets.get("status") in ("delivered", "cancelled") and "closed_at" not in sets:
        sets["closed_at"] = now()[:10]
    assignments = ", ".join(f"{k} = %({k})s" for k in sets)
    conn.execute(
        f"UPDATE crm.project SET {assignments}, updated_at = %(now)s WHERE id = %(id)s",
        {**sets, "now": now(), "id": project_id},
    )


def project(conn: Connection, project_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM crm.project WHERE id = %s", (project_id,)
    ).fetchone()


def projects_for(conn: Connection, client_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM crm.project WHERE client_id = %s ORDER BY code", (client_id,)
    ).fetchall()


def project_count(conn: Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM crm.project").fetchone()["n"]


def attachment_count(conn: Connection, client_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM crm.attachment WHERE client_id = %s", (client_id,)
    ).fetchone()["n"]


# --- the interaction log ------------------------------------------------------


def record_interaction(
    conn: Connection, client_id: int, *, kind: str, occurred_at: str,
    subject: str | None = None, body: str | None = None,
    project_id: int | None = None, state: str = "done",
    ends_at: str | None = None, direction: str | None = None,
    with_whom: str | None = None, author: str | None = None,
    source: str = "manual", external_id: str | None = None,
) -> int:
    """Write one line of the record. Returns its id, or the existing one.

    Correspondence carries its Message-ID in `external_id`, so importing the
    same message twice is one row rather than two. Every established tool
    surveyed loses this and duplicates correspondence on re-import; it costs one
    column not to.
    """
    if external_id:
        seen = conn.execute(
            "SELECT id FROM crm.interaction WHERE client_id = %s AND external_id = %s",
            (client_id, external_id),
        ).fetchone()
        if seen:
            return int(seen["id"])
    row = conn.execute(
        "INSERT INTO crm.interaction (client_id, project_id, kind, state,"
        " occurred_at, ends_at, subject, body, direction, with_whom, author,"
        " source, external_id, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (client_id, project_id, kind, state, occurred_at, ends_at, subject,
         body, direction, with_whom, author, source, external_id, now()),
    ).fetchone()
    return int(row["id"])


def set_interaction_state(
    conn: Connection, interaction_id: int, state: str, *, by: str | None = None
) -> None:
    """A planned meeting becomes done, or cancelled."""
    if state not in INTERACTION_STATES:
        raise ValueError(f"unknown interaction state {state!r}")
    before = conn.execute(
        "SELECT i.state, c.company_id FROM crm.interaction i"
        " JOIN crm.client c ON c.id = i.client_id WHERE i.id = %s",
        (interaction_id,),
    ).fetchone()
    conn.execute(
        "UPDATE crm.interaction SET state = %s WHERE id = %s", (state, interaction_id)
    )
    if before is not None:
        record_state_change(
            conn, entity="interaction", entity_id=interaction_id,
            company_id=int(before["company_id"]),
            from_state=before["state"], to_state=state, by=by,
        )


def interaction(conn: Connection, interaction_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM crm.interaction WHERE id = %s", (interaction_id,)
    ).fetchone()


def timeline(
    conn: Connection, client_id: int, *, project_id: int | None = None,
    limit: int = 200,
) -> list[Row]:
    """The record for one client, or one project within it, newest first."""
    where = ["e.client_id = %s"]
    params: list[Any] = [client_id]
    if project_id is not None:
        where.append("e.project_id = %s")
        params.append(project_id)
    return conn.execute(
        f"""SELECT e.*, p.code AS project_code, p.name AS project_name
              FROM crm.interaction e LEFT JOIN crm.project p ON p.id = e.project_id
             WHERE {' AND '.join(where)}
             ORDER BY e.occurred_at DESC, e.id DESC LIMIT %s""",
        params + [limit],
    ).fetchall()


def diary(conn: Connection, *, since: str, until: str) -> list[Row]:
    """What is in the calendar between two dates, across every client.

    Still the same table as the timeline: a meeting cannot be in the diary and
    missing from the file, because they are one row read two ways. But only the
    appointments, per `APPOINTMENT_KINDS` above. Every update to a client goes
    on that client's timeline; only the things somebody has to attend, deliver
    or be ready for come here.

    Project dates are the second half of that, and they are read from the
    project rather than from a logged event. A project's start and due dates
    are fields that get corrected as the work is scheduled; a "project starts"
    row written once on the day the record was created says what was true in
    the morning it was typed and then goes quietly stale. Deriving them means
    moving a deadline moves the calendar.
    """
    return conn.execute(
        """
        SELECT * FROM (
            SELECT e.id, e.client_id, e.project_id, e.kind, e.state,
                   e.occurred_at, e.ends_at, e.subject, e.with_whom,
                   c.code AS client_code, p.code AS project_code,
                   co.name AS company_name
              FROM crm.interaction e
              JOIN crm.client c ON c.id = e.client_id
              JOIN core.company co ON co.id = c.company_id
              LEFT JOIN crm.project p ON p.id = e.project_id
             WHERE e.state <> 'cancelled'
               AND (e.state = 'planned' OR e.kind = ANY(%s))
               AND e.occurred_at::date BETWEEN %s::date AND %s::date
            UNION ALL
            SELECT NULL::bigint, p.client_id, p.id, m.kind,
                   CASE WHEN m.at < current_date THEN 'done' ELSE 'planned' END,
                   m.at::timestamptz, NULL::timestamptz, m.label || p.name, NULL,
                   c.code, p.code, co.name
              FROM crm.project p
              JOIN crm.client c ON c.id = p.client_id
              JOIN core.company co ON co.id = c.company_id
              CROSS JOIN LATERAL (VALUES
                  ('project-start', p.started_at, 'Starts: '),
                  ('project-due',   p.due_at,     'Due: ')
              ) AS m(kind, at, label)
             WHERE p.status <> 'cancelled'
               AND m.at BETWEEN %s::date AND %s::date
        ) d
        ORDER BY occurred_at, id NULLS FIRST
        """,
        (list(APPOINTMENT_KINDS), since, until, since, until),
    ).fetchall()


def upcoming(conn: Connection, days: int = 30) -> int:
    """How many appointments are ahead. Counted through `diary`, deliberately.

    The portfolio tile says "in the diary", and a separate COUNT over planned
    interactions would have drifted from what the calendar draws the moment
    project dates joined it. One definition, asked twice.
    """
    window = conn.execute(
        "SELECT current_date AS a, current_date + %s::integer AS b", (days,)
    ).fetchone()
    return sum(
        1
        for row in diary(conn, since=window["a"], until=window["b"])
        if row["state"] == "planned"
    )


# --- removing what is not true ------------------------------------------------


def _tombstone(
    conn: Connection, client_id: int, what: str, reason: str, by: str
) -> None:
    record_interaction(
        conn, client_id, kind="note", occurred_at=now(),
        subject=f"Removed: {what}",
        body=reason.strip() or "No reason given.",
        author=by, source="manual",
    )


def delete_interaction(
    conn: Connection, interaction_id: int, *, reason: str = "", by: str = "console"
) -> bool:
    """Remove one line of the record, and record that it was removed.

    The schema was written saying nothing is ever deleted, on the grounds that
    an audit trail with a delete button is a diary. That was half right, and
    the half it got wrong cost more: rows describing a meeting and a project
    that never existed were written into a live record and there was no way to
    take them out. A log that cannot be corrected is not more truthful than one
    that can, only more confident.

    Cancelled still asserts that a meeting was once planned, which is exactly
    the claim that has to stop being made when the row was never true.
    """
    row = conn.execute(
        "SELECT client_id, kind, subject, occurred_at FROM crm.interaction"
        " WHERE id = %s",
        (interaction_id,),
    ).fetchone()
    if row is None:
        return False
    what = (
        f"{row['kind']} {(row['occurred_at'] or '')[:16].replace('T', ' ')}"
        + (f" — {row['subject']}" if row["subject"] else "")
    )
    conn.execute("DELETE FROM crm.interaction WHERE id = %s", (interaction_id,))
    _tombstone(conn, row["client_id"], what, reason, by)
    return True


def delete_project(
    conn: Connection, project_id: int, *, reason: str = "", by: str = "console"
) -> int | None:
    """Remove a project. What was logged against it survives, detached.

    The foreign key is ON DELETE SET NULL for this reason: correspondence that
    happened is still correspondence that happened, even when the piece of work
    it was filed under turns out never to have existed.
    """
    row = conn.execute(
        "SELECT client_id, code, name FROM crm.project WHERE id = %s", (project_id,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM crm.project WHERE id = %s", (project_id,))
    _tombstone(
        conn, row["client_id"], f"project {row['code']} — {row['name']}", reason, by
    )
    return int(row["client_id"])


def delete_attachment(
    conn: Connection, attachment_id: int, *, reason: str = "", by: str = "console"
) -> tuple[int, str] | None:
    """Remove a document, and return the client and the content digest.

    The caller decides about the file itself: storage is content-addressed, so
    another client may legitimately hold the same document and unlinking here
    would take theirs too.
    """
    row = conn.execute(
        "SELECT client_id, filename, sha256 FROM crm.attachment WHERE id = %s",
        (attachment_id,),
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM crm.attachment WHERE id = %s", (attachment_id,))
    _tombstone(conn, row["client_id"], f"document {row['filename']}", reason, by)
    return int(row["client_id"]), str(row["sha256"])


def sha_is_orphaned(conn: Connection, digest: str) -> bool:
    return not conn.execute(
        "SELECT 1 FROM crm.attachment WHERE sha256 = %s LIMIT 1", (digest,)
    ).fetchone()


# --- notes --------------------------------------------------------------------
#
# The operator's own writing about a client: long-form, revised over weeks,
# belonging to the engagement rather than to any one day of it. See
# migrations/0003_note.sql for why this is none of the three tables that
# already hold text.


def save_note(
    conn: Connection, client_id: int, *, title: str, body: str,
    source_path: str | None = None, project_id: int | None = None,
    tags: list[str] | None = None, pinned: bool | None = None,
    author: str = "console", digest: str = "",
) -> tuple[int, str]:
    """Write a note, or update the one that came from the same file.

    Returns its id and what happened: `created`, `updated`, or `unchanged`.
    The third is the one worth having. These are files edited in an editor and
    imported again, and an import that reports "updated" for a file nobody
    touched teaches its user to ignore the word.
    """
    fingerprint = digest or ""
    held = None
    if source_path:
        held = conn.execute(
            "SELECT id, sha256 FROM crm.note WHERE client_id = %s AND source_path = %s",
            (client_id, source_path),
        ).fetchone()
    if held is not None:
        if held["sha256"] == fingerprint and pinned is None and tags is None:
            return int(held["id"]), "unchanged"
        conn.execute(
            "UPDATE crm.note SET title = %s, body = %s, sha256 = %s,"
            " project_id = COALESCE(%s, project_id),"
            " tags = COALESCE(%s, tags), pinned = COALESCE(%s, pinned),"
            " updated_at = %s WHERE id = %s",
            (title, body, fingerprint, project_id, tags, pinned, now(), held["id"]),
        )
        return int(held["id"]), "updated"
    created = conn.execute(
        "INSERT INTO crm.note (client_id, project_id, title, body, format,"
        " source_path, sha256, tags, pinned, author, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, 'markdown', %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (client_id, project_id, title, body, source_path, fingerprint,
         tags or [], bool(pinned), author, now(), now()),
    ).fetchone()
    return int(created["id"]), "created"


def note(conn: Connection, note_id: int) -> Row | None:
    return conn.execute("SELECT * FROM crm.note WHERE id = %s", (note_id,)).fetchone()


def notes_for(
    conn: Connection, client_id: int, *, q: str = "", tag: str = "",
    with_body: bool = True, limit: int = 200,
) -> list[Row]:
    """A client's notes, pinned first and newest after.

    `with_body` is not a micro-optimisation: the bodies are whole documents and
    a page that lists forty titles would otherwise read a megabyte to print a
    few hundred bytes.
    """
    columns = "*" if with_body else (
        "id, client_id, project_id, title, format, source_path, sha256, tags,"
        " pinned, author, created_at, updated_at, length(body) AS chars"
    )
    where = ["client_id = %s"]
    params: list[Any] = [client_id]
    if q:
        where.append(
            "to_tsvector('french', coalesce(title,'') || ' ' || coalesce(body,''))"
            " @@ plainto_tsquery('french', %s)"
        )
        params.append(q)
    if tag:
        where.append("%s = ANY(tags)")
        params.append(tag)
    return conn.execute(
        f"SELECT {columns} FROM crm.note WHERE {' AND '.join(where)}"
        " ORDER BY pinned DESC, updated_at DESC, id DESC LIMIT %s",
        params + [limit],
    ).fetchall()


def note_count(conn: Connection, client_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM crm.note WHERE client_id = %s", (client_id,)
    ).fetchone()["n"]


def set_note_pinned(conn: Connection, note_id: int, pinned: bool) -> int | None:
    row = conn.execute(
        "UPDATE crm.note SET pinned = %s, updated_at = updated_at WHERE id = %s"
        " RETURNING client_id",
        (pinned, note_id),
    ).fetchone()
    return int(row["client_id"]) if row else None


def delete_note(
    conn: Connection, note_id: int, *, reason: str = "", by: str = "console"
) -> int | None:
    """Remove a note, and record that it went.

    Same rule as the rest of this schema: what is removed leaves a tombstone
    naming it, because a file that can be emptied without trace is not a
    record.
    """
    row = conn.execute(
        "SELECT client_id, title FROM crm.note WHERE id = %s", (note_id,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM crm.note WHERE id = %s", (note_id,))
    _tombstone(conn, row["client_id"], f"note “{row['title']}”", reason, by)
    return int(row["client_id"])


# --- the dossier --------------------------------------------------------------


def save_dossier_section(
    conn: Connection, client_id: int, section: str, body: str, *,
    source: str | None = None, author: str = "console",
) -> None:
    """Write one part of what is known. An empty body clears the section.

    Clearing rather than refusing: a section that turns out to be wrong should
    be emptiable from the same field that filled it.
    """
    conn.execute(
        "INSERT INTO crm.dossier (client_id, section, body, source, author, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (client_id, section) DO UPDATE SET"
        " body = excluded.body, source = excluded.source,"
        " author = excluded.author, updated_at = excluded.updated_at",
        (client_id, section, body.strip() or None,
         (source or "").strip() or None, author, now()),
    )


def dossier_for(conn: Connection, client_id: int) -> dict[str, dict[str, Any]]:
    return {
        row["section"]: row
        for row in conn.execute(
            "SELECT * FROM crm.dossier WHERE client_id = %s", (client_id,)
        )
    }


# --- the suggested approach ---------------------------------------------------


def save_approach(conn: Connection, client_id: int, row: dict[str, Any]) -> int:
    """Record a suggested approach. Always a new row, never an update.

    When the client asks in September why March quoted what it quoted, the
    answer has to still exist along with what it was computed from.
    """
    created = conn.execute(
        "INSERT INTO crm.approach (client_id, created_at, source, model, effort,"
        " inputs_hash, read_json, situation, opening, points_json, services_json,"
        " pricing_json, risks_json, questions_json, note)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (
            client_id, now(), row.get("source", "model"), row.get("model"),
            row.get("effort"), row.get("inputs_hash"),
            json_param(row.get("read") or {}), row.get("situation"),
            row.get("opening"), json_param(row.get("points") or []),
            json_param(row.get("services") or []),
            json_param(row.get("pricing") or []),
            json_param(row.get("risks") or []),
            json_param(row.get("questions") or []), row.get("note"),
        ),
    ).fetchone()
    return int(created["id"])


def approach(conn: Connection, approach_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM crm.approach WHERE id = %s", (approach_id,)
    ).fetchone()


def has_approach(conn: Connection, client_id: int) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM crm.approach WHERE client_id = %s LIMIT 1", (client_id,)
        ).fetchone()
    )


def approaches_for(conn: Connection, client_id: int, *, limit: int = 20) -> list[Row]:
    return conn.execute(
        "SELECT * FROM crm.approach WHERE client_id = %s"
        " ORDER BY created_at DESC, id DESC LIMIT %s",
        (client_id, limit),
    ).fetchall()


def accept_approach(conn: Connection, approach_id: int) -> None:
    """Mark that a human took this and used it. Generated advice nobody acted
    on is not the same object as advice that went into a proposal."""
    conn.execute(
        "UPDATE crm.approach SET accepted_at = %s WHERE id = %s AND accepted_at IS NULL",
        (now(), approach_id),
    )


# --- the client's papers ------------------------------------------------------


def add_attachment(
    conn: Connection, client_id: int, row: dict[str, Any]
) -> tuple[int, bool]:
    """Record a document. Returns its id and whether it was already held.

    Deduplicated on the content digest within one client, never across:
    two clients may legitimately hold the same standard contract, and merging
    those would be a confidentiality failure rather than a deduplication.
    """
    known = conn.execute(
        "SELECT id FROM crm.attachment WHERE client_id = %s AND sha256 = %s",
        (client_id, row["sha256"]),
    ).fetchone()
    if known:
        conn.execute(
            "UPDATE crm.attachment SET filename = %s, kind = %s, note = %s,"
            " project_id = COALESCE(%s, project_id) WHERE id = %s",
            (row["filename"], row.get("kind"), row.get("note"),
             row.get("project_id"), known["id"]),
        )
        return int(known["id"]), True
    text = row.get("text") or None
    created = conn.execute(
        "INSERT INTO crm.attachment (client_id, project_id, filename, media_type,"
        " bytes, sha256, kind, note, text, text_chars, extracted_at,"
        " extract_error, uploaded_by, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (
            client_id, row.get("project_id"), row["filename"],
            row.get("media_type"), row["bytes"], row["sha256"], row.get("kind"),
            row.get("note"), text, len(text) if text else None,
            now() if text else None, row.get("extract_error") or None,
            row.get("uploaded_by", "console"), now(),
        ),
    ).fetchone()
    return int(created["id"]), False


def attachments_for(
    conn: Connection, client_id: int, *, with_text: bool = False
) -> list[Row]:
    """The documents held for a client.

    `text` is left out unless asked for. The listing renders a dozen rows and
    the column holds whole annual reports; selecting it to display a filename
    would read megabytes to print kilobytes.
    """
    columns = "*" if with_text else (
        "id, client_id, project_id, filename, media_type, bytes, sha256, kind,"
        " note, text_chars, extracted_at, extract_error, uploaded_by, created_at"
    )
    return conn.execute(
        f"SELECT {columns} FROM crm.attachment WHERE client_id = %s"
        " ORDER BY created_at DESC, id DESC",
        (client_id,),
    ).fetchall()


def attachment(conn: Connection, attachment_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM crm.attachment WHERE id = %s", (attachment_id,)
    ).fetchone()


# --- interview preparation ----------------------------------------------------


def save_briefing(conn: Connection, client_id: int, row: dict[str, Any]) -> int:
    created = conn.execute(
        "INSERT INTO crm.briefing (client_id, created_at, model, effort,"
        " inputs_hash, read_json, orientation, agenda_json)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (client_id, now(), row.get("model"), row.get("effort"),
         row.get("inputs_hash"), json_param(row.get("read") or {}),
         row.get("orientation"), json_param(row.get("agenda") or [])),
    ).fetchone()
    return int(created["id"])


def briefings_for(conn: Connection, client_id: int, *, limit: int = 20) -> list[Row]:
    return conn.execute(
        "SELECT * FROM crm.briefing WHERE client_id = %s"
        " ORDER BY created_at DESC, id DESC LIMIT %s",
        (client_id, limit),
    ).fetchall()


def save_terms(
    conn: Connection, client_id: int, terms: list[dict[str, Any]], *,
    briefing_id: int | None = None,
) -> tuple[int, int]:
    """Add what is new and refresh what is known. Returns (added, refreshed).

    `known` and `note` are never written here. They belong to the person, and a
    regeneration after the client sends a document must not reset what has
    already been learned.
    """
    added = refreshed = 0
    for term in terms:
        name = str(term.get("term") or "").strip()
        if not name:
            continue
        seen = conn.execute(
            "SELECT 1 FROM crm.glossary WHERE client_id = %s AND term = %s",
            (client_id, name),
        ).fetchone()
        conn.execute(
            "INSERT INTO crm.glossary (client_id, term, category, expansion,"
            " what_it_is, why_here, must_know, question, reference, priority,"
            " first_seen, updated_at, briefing_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (client_id, term) DO UPDATE SET"
            " category = excluded.category, expansion = excluded.expansion,"
            " what_it_is = excluded.what_it_is, why_here = excluded.why_here,"
            " must_know = excluded.must_know, question = excluded.question,"
            " reference = excluded.reference, priority = excluded.priority,"
            " updated_at = excluded.updated_at, briefing_id = excluded.briefing_id",
            (client_id, name, term.get("category"), term.get("expansion"),
             term.get("what_it_is"), term.get("why_here"), term.get("must_know"),
             term.get("question"), term.get("reference"), term.get("priority"),
             now(), now(), briefing_id),
        )
        if seen:
            refreshed += 1
        else:
            added += 1
    return added, refreshed


def glossary_for(conn: Connection, client_id: int) -> list[Row]:
    """Every term held for a client, unrevised first within each category."""
    return conn.execute(
        "SELECT * FROM crm.glossary WHERE client_id = %s"
        " ORDER BY category, CASE priority WHEN 'must' THEN 0 WHEN 'should'"
        " THEN 1 ELSE 2 END, known, term",
        (client_id,),
    ).fetchall()


def known_terms(conn: Connection, client_id: int) -> list[str]:
    return [
        row["term"]
        for row in conn.execute(
            "SELECT term FROM crm.glossary WHERE client_id = %s AND known"
            " ORDER BY term",
            (client_id,),
        )
    ]


def set_term_known(
    conn: Connection, term_id: int, *, known: bool, note: str | None = None
) -> None:
    if note is None:
        conn.execute(
            "UPDATE crm.glossary SET known = %s WHERE id = %s", (known, term_id)
        )
        return
    conn.execute(
        "UPDATE crm.glossary SET known = %s, note = %s WHERE id = %s",
        (known, note.strip() or None, term_id),
    )


def term_owner(conn: Connection, term_id: int) -> int | None:
    row = conn.execute(
        "SELECT client_id FROM crm.glossary WHERE id = %s", (term_id,)
    ).fetchone()
    return int(row["client_id"]) if row else None
