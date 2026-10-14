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
from typing import Any, Sequence

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
    """A human reference from a company name: CLAIR-LOGIS.

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


def search_clients(conn: Connection, q: str, *, limit: int = 10) -> list[Row]:
    """Clients by their own code or by the company's name.

    Both, because a client is referred to by whichever of the two the person
    speaking happens to hold: the code on a quote, the name in a conversation.
    """
    return conn.execute(
        "SELECT c.id, c.code, c.status, co.name, co.canton"
        "  FROM crm.client c JOIN core.company co ON co.id = c.company_id"
        " WHERE c.code ILIKE %s OR unaccent(co.name) ILIKE unaccent(%s)"
        " ORDER BY co.name LIMIT %s",
        (f"%{q}%", f"%{q}%", limit),
    ).fetchall()


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

    CLAIR-LOGIS-01, because a project outlives the conversation that started it
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
    """The sections, keyed by name, in the order they should be read.

    Ordered explicitly. Without an ORDER BY this came back in whatever order a
    heap scan produced, which is roughly insertion order until somebody edits a
    row, at which point the narrative silently rearranged itself because a typo
    was corrected. Python dictionaries keep insertion order, so the caller gets
    the sequence without having to sort it again.
    """
    return {
        row["section"]: row
        for row in conn.execute(
            "SELECT * FROM crm.dossier WHERE client_id = %s"
            " ORDER BY position, id",
            (client_id,),
        )
    }


def reorder_dossier(
    conn: Connection, client_id: int, names: Sequence[str], *, known: Sequence[str],
) -> int:
    """Put this client's sections in the order given. Returns how many moved.

    Keyed by section name rather than by row id, because a section that has
    never been filled in has no row and is still on the page and still
    draggable. So this upserts: the position is a fact about the dossier, not
    about whether anybody has written in it yet, and a row carrying only a
    position is empty by every test that asks (`filled` reads the body).

    `known` is the section vocabulary. A name that is not in it is dropped
    rather than stored, so a forged request cannot fill the table with rows for
    sections that do not exist and that nothing will ever render or clean up.
    """
    allowed = set(known)
    moved = 0
    for index, name in enumerate(names, 1):
        if name not in allowed:
            continue
        conn.execute(
            "INSERT INTO crm.dossier (client_id, section, position, updated_at)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (client_id, section) DO UPDATE SET position = %s",
            (client_id, name, index, now(), index),
        )
        moved += 1
    return moved


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
            " project_id = COALESCE(%s, project_id),"
            " folder_id = COALESCE(%s, folder_id) WHERE id = %s",
            (row["filename"], row.get("kind"), row.get("note"),
             row.get("project_id"), row.get("folder_id"), known["id"]),
        )
        return int(known["id"]), True
    text = row.get("text") or None
    created = conn.execute(
        "INSERT INTO crm.attachment (client_id, project_id, folder_id, filename,"
        " media_type, bytes, sha256, kind, note, text, text_chars, extracted_at,"
        " extract_error, uploaded_by, created_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (
            client_id, row.get("project_id"), row.get("folder_id"),
            row["filename"],
            row.get("media_type"), row["bytes"], row["sha256"], row.get("kind"),
            row.get("note"), text, len(text) if text else None,
            now() if text else None, row.get("extract_error") or None,
            row.get("uploaded_by", "console"), now(),
        ),
    ).fetchone()
    return int(created["id"]), False


def attachments_for(
    conn: Connection, client_id: int, *, with_text: bool = False,
    folder_id: int | None = None, unfiled: bool = False,
) -> list[Row]:
    """The documents held for a client, each carrying the folder it is in.

    `text` is left out unless asked for. The listing renders a dozen rows and
    the column holds whole annual reports; selecting it to display a filename
    would read megabytes to print kilobytes.

    The folder's *name* is joined in rather than left as an id, because every
    caller that has a document wants to say where it was filed, and none of
    them wants a second query to find out.
    """
    columns = "a.*" if with_text else (
        "a.id, a.client_id, a.project_id, a.folder_id, a.filename, a.media_type,"
        " a.bytes, a.sha256, a.kind, a.note, a.text_chars, a.extracted_at,"
        " a.extract_error, a.uploaded_by, a.created_at"
    )
    where = ["a.client_id = %s"]
    params: list[Any] = [client_id]
    if unfiled:
        where.append("a.folder_id IS NULL")
    elif folder_id is not None:
        where.append("a.folder_id = %s")
        params.append(folder_id)
    return conn.execute(
        f"SELECT {columns}, f.name AS folder FROM crm.attachment a"
        " LEFT JOIN crm.folder f ON f.id = a.folder_id"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY a.created_at DESC, a.id DESC",
        tuple(params),
    ).fetchall()


def attachment(conn: Connection, attachment_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM crm.attachment WHERE id = %s", (attachment_id,)
    ).fetchone()


# --- folders ------------------------------------------------------------------
#
# The client's own filing over the documents already held. See
# migrations/0007_folder.sql for why this is not a project.


def add_folder(
    conn: Connection, client_id: int, name: str, *, note: str | None = None
) -> tuple[int, bool]:
    """Make a folder, or hand back the one already called that.

    Returns its id and whether it existed. Creating a folder twice is what
    happens when a batch arrives in two deliveries, and the useful answer to
    the second one is the first folder rather than an error or a twin.
    """
    name = name.strip()
    if not name:
        raise ValueError("a folder needs a name")
    known = conn.execute(
        "SELECT id FROM crm.folder WHERE client_id = %s AND lower(name) = lower(%s)",
        (client_id, name),
    ).fetchone()
    if known:
        # The name stands as it was first typed; a note is added if this
        # delivery came with one and left alone if it did not.
        if note and note.strip():
            conn.execute(
                "UPDATE crm.folder SET note = %s WHERE id = %s",
                (note.strip(), known["id"]),
            )
        return int(known["id"]), True
    created = conn.execute(
        "INSERT INTO crm.folder (client_id, name, note, created_at)"
        " VALUES (%s, %s, %s, %s) RETURNING id",
        (client_id, name, (note or "").strip() or None, now()),
    ).fetchone()
    return int(created["id"]), False


def folders_for(conn: Connection, client_id: int) -> list[Row]:
    """A client's folders, each with what is in it.

    The count comes from the join rather than a column on the folder, because
    a stored count is a second copy of a fact the documents already state and
    is wrong the first time one is moved.
    """
    return conn.execute(
        "SELECT f.*, COUNT(a.id) AS documents,"
        " COALESCE(SUM(a.bytes), 0) AS bytes"
        "  FROM crm.folder f"
        "  LEFT JOIN crm.attachment a ON a.folder_id = f.id"
        " WHERE f.client_id = %s GROUP BY f.id ORDER BY lower(f.name)",
        (client_id,),
    ).fetchall()


def folder(conn: Connection, folder_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM crm.folder WHERE id = %s", (folder_id,)
    ).fetchone()


def rename_folder(
    conn: Connection, folder_id: int, *, name: str | None = None,
    note: str | None = None,
) -> None:
    sets: dict[str, Any] = {}
    if name is not None and name.strip():
        sets["name"] = name.strip()
    if note is not None:
        sets["note"] = note.strip() or None
    if not sets:
        return
    columns = ", ".join(f"{k} = %s" for k in sets)
    conn.execute(
        f"UPDATE crm.folder SET {columns} WHERE id = %s",
        (*sets.values(), folder_id),
    )


def file_attachment(
    conn: Connection, attachment_id: int, folder_id: int | None
) -> int | None:
    """Put a document in a folder, or take it out of one with None.

    The folder has to belong to the same client as the document. Moving a
    paper into another client's folder is not a mistake worth a friendly
    error; it is a confidentiality failure, and it is refused here rather than
    trusted to whichever form happened to submit it.
    """
    row = conn.execute(
        "SELECT client_id FROM crm.attachment WHERE id = %s", (attachment_id,)
    ).fetchone()
    if row is None:
        return None
    if folder_id is not None:
        owner = conn.execute(
            "SELECT client_id FROM crm.folder WHERE id = %s", (folder_id,)
        ).fetchone()
        if owner is None or int(owner["client_id"]) != int(row["client_id"]):
            raise ValueError("that folder belongs to another client")
    conn.execute(
        "UPDATE crm.attachment SET folder_id = %s WHERE id = %s",
        (folder_id, attachment_id),
    )
    return int(row["client_id"])


def delete_folder(
    conn: Connection, folder_id: int, *, reason: str = "", by: str = "console"
) -> int | None:
    """Remove a folder. Everything in it stays, unfiled.

    The foreign key is ON DELETE SET NULL for exactly this: tidying the filing
    is not the same act as destroying the papers, and a tool that conflates
    them teaches people never to tidy. The tombstone says how many documents
    came loose, because that is the number somebody will want when they
    wonder where they went.
    """
    row = conn.execute(
        "SELECT client_id, name FROM crm.folder WHERE id = %s", (folder_id,)
    ).fetchone()
    if row is None:
        return None
    held = conn.execute(
        "SELECT COUNT(*) AS n FROM crm.attachment WHERE folder_id = %s", (folder_id,)
    ).fetchone()["n"]
    conn.execute("DELETE FROM crm.folder WHERE id = %s", (folder_id,))
    _tombstone(
        conn, row["client_id"],
        f"folder {row['name']}"
        + (f" — {held} document(s) kept, now unfiled" if held else " — it was empty"),
        reason, by,
    )
    return int(row["client_id"])


# --- programmes ---------------------------------------------------------------
#
# One endeavour across many companies, each member at its own stage. See
# migrations/0008_programme.sql for why this is neither a prospect nor a
# project.


DEFAULT_STAGES = (
    "selected", "contacted", "replied", "onboarding",
    "testing", "live", "refused", "parked",
)

# What each default stage means, written for somebody deciding where to drop a
# card. The programme owns its own copy from creation, so editing these later
# changes what new programmes start with and never what a running one says.
DEFAULT_STAGE_NOTES = {
    "selected": "On the list. Not written to yet.",
    "contacted": "Sent to them. Nothing back.",
    "replied": "They answered. The conversation is live.",
    "onboarding": "Agreed to try it. Being set up.",
    "testing": "Using it. Feedback is coming in.",
    "live": "Released and in real use.",
    "refused": "Said no. Kept, with the reason.",
    "parked": "Interested, not now.",
}

FEEDBACK_KINDS = ("note", "bug", "request", "praise")


def add_programme(
    conn: Connection, code: str, name: str, *, what: str | None = None,
    stages: Sequence[str] | None = None, started_at: str | None = None,
) -> int:
    """Start a programme under its own reference.

    FLATCHECK, for the same reason a project has a code: it outlives the
    conversation that started it and has to be nameable in a contract, a file
    and a sentence said out loud.
    """
    code = code.strip().upper()
    name = name.strip()
    if not code:
        raise ValueError("a programme needs a code")
    if not name:
        raise ValueError("a programme needs a name")
    if conn.execute(
        "SELECT 1 FROM crm.programme WHERE code = %s", (code,)
    ).fetchone():
        raise ValueError(f"a programme already goes by {code}")
    wanted = _clean_stages(stages) if stages else list(DEFAULT_STAGES)
    notes = {k: v for k, v in DEFAULT_STAGE_NOTES.items() if k in wanted}
    created = conn.execute(
        "INSERT INTO crm.programme (code, name, what, stages, stage_notes,"
        " started_at, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (code, name, (what or "").strip() or None, wanted,
         json_param(notes), started_at, now()),
    ).fetchone()
    return int(created["id"])


def _clean_stages(stages: Sequence[str]) -> list[str]:
    """An ordered stage list with the blanks and the repeats taken out.

    Order is preserved because the board draws its columns in it, and a
    duplicate is dropped rather than refused: it is a typo in a textarea, not a
    decision worth an error page.
    """
    out: list[str] = []
    for raw in stages:
        stage = str(raw).strip()
        if stage and stage not in out:
            out.append(stage)
    if not out:
        raise ValueError("a programme needs at least one stage")
    return out


def programme(conn: Connection, programme_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM crm.programme WHERE id = %s", (programme_id,)
    ).fetchone()


def programme_by_code(conn: Connection, code: str) -> Row | None:
    return conn.execute(
        "SELECT * FROM crm.programme WHERE upper(code) = upper(%s)", (code.strip(),)
    ).fetchone()


def programmes(conn: Connection, *, status: str = "") -> list[Row]:
    """Every programme, with its members counted and broken down by stage.

    The counts come from the join rather than columns on the programme, for the
    reason a folder's document count does: a stored count is a second copy of a
    fact the members already state, and it is wrong the first time a card
    moves.

    The breakdown is returned whole rather than reduced to a headline number
    like "how many engaged". Which stage counts as engaged is a judgement the
    stage list does not carry, and it would be a different judgement in the
    next programme; the page can read the stages in their own order and say
    what it likes.
    """
    where = "WHERE p.status = %s" if status else ""
    return conn.execute(
        "SELECT p.*, COALESCE(SUM(m.n), 0)::int AS members,"
        " COALESCE(jsonb_object_agg(m.stage, m.n)"
        "   FILTER (WHERE m.stage IS NOT NULL), '{}'::jsonb) AS by_stage"
        "  FROM crm.programme p"
        "  LEFT JOIN (SELECT programme_id, stage, COUNT(*) AS n"
        "               FROM crm.programme_member GROUP BY programme_id, stage) m"
        "    ON m.programme_id = p.id"
        f" {where}"
        " GROUP BY p.id ORDER BY p.status, p.created_at DESC",
        ([status] if status else []),
    ).fetchall()


def update_programme(conn: Connection, programme_id: int, **fields: Any) -> None:
    allowed = {"name", "what", "status", "started_at", "released_at", "code"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return
    if "code" in sets:
        sets["code"] = str(sets["code"]).strip().upper()
    if "status" in sets:
        before = conn.execute(
            "SELECT status FROM crm.programme WHERE id = %s", (programme_id,)
        ).fetchone()
        if before is not None:
            record_state_change(
                conn, entity="programme", entity_id=programme_id,
                from_state=before["status"], to_state=sets["status"],
                by=fields.get("by"),
            )
    assignments = ", ".join(f"{k} = %({k})s" for k in sets)
    conn.execute(
        f"UPDATE crm.programme SET {assignments}, updated_at = %(now)s"
        " WHERE id = %(id)s",
        {**sets, "now": now(), "id": programme_id},
    )


def set_stages(
    conn: Connection, programme_id: int, stages: Sequence[str],
    *, notes: dict[str, str] | None = None,
) -> list[str]:
    """Rewrite the pipeline, refusing to strand anybody.

    Removing a stage that members are standing in is the one edit that has to
    be refused rather than absorbed. Absorbing it means either deleting those
    members or moving them somewhere nobody chose, and both are decisions the
    operator should make while looking at the cards. So the refusal names the
    stage and counts who is in it, which is the number needed to act on it.
    """
    wanted = _clean_stages(stages)
    occupied = {
        row["stage"]: int(row["n"])
        for row in conn.execute(
            "SELECT stage, COUNT(*) AS n FROM crm.programme_member"
            " WHERE programme_id = %s GROUP BY stage",
            (programme_id,),
        )
    }
    stranded = sorted(
        (stage, n) for stage, n in occupied.items() if stage not in wanted
    )
    if stranded:
        detail = ", ".join(f"{stage} ({n})" for stage, n in stranded)
        raise ValueError(
            f"move the members out first: {detail} would be left with no column"
        )
    kept = {}
    if notes is None:
        held = conn.execute(
            "SELECT stage_notes FROM crm.programme WHERE id = %s", (programme_id,)
        ).fetchone()
        notes = dict(held["stage_notes"] or {}) if held else {}
    for stage in wanted:
        gloss = (notes.get(stage) or DEFAULT_STAGE_NOTES.get(stage) or "").strip()
        if gloss:
            kept[stage] = gloss
    conn.execute(
        "UPDATE crm.programme SET stages = %s, stage_notes = %s, updated_at = %s"
        " WHERE id = %s",
        (wanted, json_param(kept), now(), programme_id),
    )
    return wanted


def add_member(
    conn: Connection, programme_id: int, company_id: int, *,
    stage: str = "", contact: str | None = None, note: str | None = None,
) -> tuple[int, bool]:
    """Put a company on a programme, or hand back the member already there.

    Returns its id and whether it existed, the same contract `add_folder`
    offers and for the same reason: adding a company a second time is what
    happens when a shortlist overlaps one already imported, and the useful
    answer is the member already on the board rather than an error or a twin.
    """
    stages = _stages_of(conn, programme_id)
    known = conn.execute(
        "SELECT id FROM crm.programme_member"
        " WHERE programme_id = %s AND company_id = %s",
        (programme_id, company_id),
    ).fetchone()
    if known:
        return int(known["id"]), True
    where = stage.strip() or stages[0]
    if where not in stages:
        raise ValueError(_no_such_stage(where, stages))
    created = conn.execute(
        "INSERT INTO crm.programme_member (programme_id, company_id, stage,"
        " contact, note, created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (programme_id, company_id, where, (contact or "").strip() or None,
         (note or "").strip() or None, now()),
    ).fetchone()
    member_id = int(created["id"])
    record_state_change(
        conn, entity="programme_member", entity_id=member_id,
        company_id=company_id, to_state=where, by="programme",
    )
    return member_id, False


def _stages_of(conn: Connection, programme_id: int) -> list[str]:
    row = conn.execute(
        "SELECT stages FROM crm.programme WHERE id = %s", (programme_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no programme with id {programme_id}")
    return list(row["stages"])


def _no_such_stage(stage: str, stages: Sequence[str]) -> str:
    """Say which stages were on offer.

    The valid set is a column on another row rather than a constant, so a bare
    "unknown stage" would send the reader looking for a list that is not in the
    code.
    """
    return f"{stage!r} is not a stage of this programme: {', '.join(stages)}"


def member(conn: Connection, member_id: int) -> Row | None:
    return conn.execute(
        "SELECT m.*, p.code AS programme_code, p.stages, co.name AS company_name,"
        " co.uid, co.canton"
        "  FROM crm.programme_member m"
        "  JOIN crm.programme p ON p.id = m.programme_id"
        "  JOIN core.company co ON co.id = m.company_id"
        " WHERE m.id = %s",
        (member_id,),
    ).fetchone()


def members_for(conn: Connection, programme_id: int) -> list[Row]:
    """Every member, with what a card needs and nothing that needs a second query.

    The inbound mail count is joined here and is read-only. It says a company
    has written back; it never moves the card. Nothing in this schema files
    itself, and a stage that advanced because a message arrived would be the
    first thing that did.
    """
    return conn.execute(
        """
        SELECT m.*, co.name AS company_name, co.uid, co.canton,
               co.legal_form_label,
               (SELECT COUNT(*) FROM crm.email_message e
                 WHERE e.company_id = m.company_id AND e.direction = 'in')
                   AS inbound,
               (SELECT MAX(e.sent_at) FROM crm.email_message e
                 WHERE e.company_id = m.company_id AND e.direction = 'in')
                   AS last_inbound,
               (SELECT COUNT(*) FROM crm.programme_feedback f
                 WHERE f.member_id = m.id) AS feedback
          FROM crm.programme_member m
          JOIN core.company co ON co.id = m.company_id
         WHERE m.programme_id = %s
         ORDER BY co.name
        """,
        (programme_id,),
    ).fetchall()


def move_member(
    conn: Connection, member_id: int, stage: str, *,
    by: str = "console", reason: str = "",
) -> Row | None:
    """Move one member to another stage of its own programme.

    The transition goes to `audit.state_change`, which is why there is no
    history table here: that one names its subject as text and holds no foreign
    key, so it takes a programme's moves without either schema knowing about
    the other, and the board's existing `time_in_state` query dates the card
    with no new code.
    """
    row = conn.execute(
        "SELECT m.stage, m.company_id, p.stages FROM crm.programme_member m"
        "  JOIN crm.programme p ON p.id = m.programme_id"
        " WHERE m.id = %s",
        (member_id,),
    ).fetchone()
    if row is None:
        return None
    stages = list(row["stages"])
    stage = stage.strip()
    if stage not in stages:
        raise ValueError(_no_such_stage(stage, stages))
    conn.execute(
        "UPDATE crm.programme_member SET stage = %s, updated_at = %s WHERE id = %s",
        (stage, now(), member_id),
    )
    record_state_change(
        conn, entity="programme_member", entity_id=member_id,
        company_id=int(row["company_id"]), from_state=row["stage"],
        to_state=stage, by=by, reason=reason.strip() or None,
    )
    return row


def members_who_replied(
    conn: Connection, programme_id: int, *, from_stage: str = "contacted"
) -> list[Row]:
    """Members sitting at `from_stage` who have written since they got there.

    A proposal, not a move. `members_for` says why: nothing in this schema
    files itself, and a stage that advanced because a message arrived would be
    the first thing that did. So this returns the cards a person should look
    at, and the move stays an act somebody takes.

    "Since they got there" is what separates a reply from an old thread. The
    comparison is against `updated_at`, which `move_member` sets, so a company
    that wrote last year and was contacted yesterday does not appear.
    """
    return conn.execute(
        """
        SELECT m.id, m.company_id, m.stage, m.updated_at,
               co.name AS company_name, co.uid, co.canton,
               e.last_inbound, e.inbound, e.subject, e.from_email
          FROM crm.programme_member m
          JOIN core.company co ON co.id = m.company_id
          JOIN LATERAL (
            SELECT MAX(x.sent_at) AS last_inbound,
                   COUNT(*) AS inbound,
                   (ARRAY_AGG(x.subject ORDER BY x.sent_at DESC))[1] AS subject,
                   (ARRAY_AGG(x.from_email ORDER BY x.sent_at DESC))[1]
                       AS from_email
              FROM crm.email_message x
             WHERE x.company_id = m.company_id AND x.direction = 'in'
               AND x.sent_at > m.updated_at
          ) e ON TRUE
         WHERE m.programme_id = %s AND m.stage = %s
           AND e.last_inbound IS NOT NULL
         ORDER BY e.last_inbound DESC
        """,
        (programme_id, from_stage),
    ).fetchall()


def unread_answers(
    conn: Connection, programme_id: int, *, from_stage: str = "contacted",
    limit: int = 200,
) -> list[Row]:
    """One row per inbound message a member has sent since it was contacted.

    `members_who_replied` counts messages, which answers who to look at.
    Classifying what they said needs the message, so this returns the rows
    themselves, newest last per firm, with the body the mail stage stored.

    A body is present only for a message somebody accepted into the record, so
    a message still sitting in the queue comes back with an empty one. That is
    the right behaviour rather than a gap to fill: reading the text of an
    unaccepted message would be the mail stage's rule broken from a second
    place.
    """
    return conn.execute(
        """
        SELECT e.id AS message_id, e.subject, e.body, e.from_email,
               e.from_name, e.sent_at, e.state, e.folder,
               m.id AS member_id, m.stage, m.updated_at, m.platform,
               m.tester_account, m.answer_message_id,
               co.id AS company_id, co.name AS company_name, co.canton
          FROM crm.programme_member m
          JOIN core.company co ON co.id = m.company_id
          JOIN crm.email_message e ON e.company_id = m.company_id
         WHERE m.programme_id = %s AND m.stage = %s
           AND e.direction = 'in'
           AND (m.updated_at IS NULL OR e.sent_at > m.updated_at)
         ORDER BY e.sent_at
         LIMIT %s
        """,
        (programme_id, from_stage, limit),
    ).fetchall()


def addresses_of(
    conn: Connection, company_ids: Sequence[int]
) -> dict[int, frozenset[str]]:
    """The email addresses the survey holds, per company.

    Read when an answer is searched for the tester's own account, to rule out
    the one the firm published. A reply quotes the message it answers, so the
    mailbox written to appears in nearly every body and would otherwise be
    offered as the tester's Google account.
    """
    if not company_ids:
        return {}
    rows = conn.execute(
        "SELECT company_id, lower(value) AS value FROM core.contact"
        " WHERE kind = 'email' AND company_id = ANY(%s)",
        (list(company_ids),),
    ).fetchall()
    held: dict[int, set[str]] = {}
    for row in rows:
        held.setdefault(int(row["company_id"]), set()).add(str(row["value"]))
    return {key: frozenset(value) for key, value in held.items()}


def note_contacted(
    conn: Connection, programme_id: int, company_id: int, *,
    address: str = "", by: str = "draft", stage: str = "contacted",
) -> tuple[int, bool]:
    """Record that a message went out to this firm, member or not yet.

    Called when a person marks a draft sent, which is the only moment this
    program learns that a message left. It inserts the member when the firm was
    never on the programme, because a send list selects from the survey and not
    from the membership, so the first contact is often also the first the
    programme hears of the firm.

    It moves forward and never back. A member already past the first stage
    keeps where it is: marking a second draft sent to a firm at `testing` says
    nothing about its stage, and walking that card back to `contacted` would
    lose the only record of how far it got.
    """
    member_id, existed = add_member(
        conn, programme_id, company_id,
        contact=address.strip() or None,
    )
    stages = _stages_of(conn, programme_id)
    if stage not in stages:
        raise ValueError(_no_such_stage(stage, stages))
    row = conn.execute(
        "SELECT stage, contact FROM crm.programme_member WHERE id = %s",
        (member_id,),
    ).fetchone()
    here = str(row["stage"]) if row else stages[0]
    moved = False
    if here == stages[0] and here != stage:
        move_member(conn, member_id, stage, by=by,
                    reason=f"message sent to {address}" if address else "message sent")
        moved = True
    if address.strip() and row is not None and not row["contact"]:
        conn.execute(
            "UPDATE crm.programme_member SET contact = %s WHERE id = %s",
            (address.strip(), member_id),
        )
    return member_id, moved


def record_answer(
    conn: Connection, member_id: int, *,
    platform: str = "", account: str = "", window_ok: bool | None = None,
    message_id: int | None = None, answered_at: Any = None,
    stage: str = "replied", by: str = "console", note: str = "",
) -> Row | None:
    """Write what a firm answered, and move the card to it.

    The platform and the account are the two facts a store test track needs,
    so they sit on the member. The text of the answer is cited by
    `answer_message_id` and never copied: it is already in
    `crm.email_message`, and a second copy would be the one that drifts.

    `window_ok` stays untouched when it arrives as None. A reply names the
    platform in a word and accepts the dates in a sentence, so the dates are
    confirmed by the person reading rather than inferred from a token.
    """
    row = conn.execute(
        "SELECT id, stage FROM crm.programme_member WHERE id = %s", (member_id,)
    ).fetchone()
    if row is None:
        return None
    platform = (platform or "").strip().lower()
    if platform and platform not in ("ios", "android"):
        raise ValueError(
            f"Unknown platform {platform!r}. A store test track is either "
            "'ios' or 'android'."
        )
    sets: dict[str, Any] = {"answered_at": answered_at or now()}
    if platform:
        sets["platform"] = platform
    if account.strip():
        sets["tester_account"] = account.strip()
    if window_ok is not None:
        sets["window_ok"] = window_ok
    if message_id is not None:
        sets["answer_message_id"] = message_id
    columns = ", ".join(f"{key} = %s" for key in sets)
    conn.execute(
        f"UPDATE crm.programme_member SET {columns} WHERE id = %s",
        (*sets.values(), member_id),
    )
    if stage and stage != row["stage"]:
        move_member(conn, member_id, stage, by=by,
                    reason=note.strip() or "answered")
    return row


def history_for(
    conn: Connection, company_id: int, *, limit: int = 200
) -> list[Row]:
    """Every message to and from one firm, oldest first.

    Both directions in one list, because the question a reviewer asks is what
    happened in order. The Sent folder arrives with the inbox, so a message the
    operator wrote from their own client is here with `direction = 'out'`
    without this program having sent anything.
    """
    return conn.execute(
        """
        SELECT id, direction, sent_at, subject, from_email, to_emails,
               state, folder, match_method, decided_at
          FROM crm.email_message
         WHERE company_id = %s
         ORDER BY sent_at NULLS LAST, id
         LIMIT %s
        """,
        (company_id, limit),
    ).fetchall()


# The vocabulary a ledger row can be narrowed to. Each one is a question the
# operator asks out loud while the campaign runs, which is why they are named
# rather than left to a free-text filter.
LEDGER_FLAGS = ("answered", "silent", "failed", "refused", "enrollable")


def campaign_ledger(
    conn: Connection, programme_id: int, *, stage: str = "", canton: str = "",
    flag: str = "", limit: int = 1000,
) -> list[Row]:
    """One row per member, holding every status the campaign has about it.

    Four tables answer "where does this firm stand" and no query joined them,
    so the answer was four queries and a join done by eye. What goes out lives
    in `intel.draft.sent_at`, what came back in `crm.email_message`, whether
    the address survived in `core.contact.status`, and the stage on the member.

    Reading `intel.draft` and `core.contact` from here is the direction the
    module boundary permits, the one `match_company` and `bring_in_members`
    already take.
    """
    where = ["m.programme_id = %s"]
    params: list[Any] = [programme_id]
    if stage:
        where.append("m.stage = %s")
        params.append(stage)
    if canton:
        where.append("co.canton = ANY(%s)")
        params.append([c.strip().upper() for c in canton.split(",") if c.strip()])
    # The flag reads the computed columns, so it is applied outside the select
    # rather than beside the other two. Writing it as a HAVING over the
    # aggregates would push the whole query into a GROUP BY it does not need.
    having = {
        "answered": "x.inbound > 0",
        "silent": "x.sent > 0 AND x.inbound = 0",
        "failed": "x.failed > 0",
        "refused": "x.refused > 0",
        # Every gate reads `x`, the subquery, and never `m`. The inner aliases
        # are out of scope out here, which the first version of this learned
        # from Postgres on the first live run.
        "enrollable": "x.platform IS NOT NULL",
    }.get(flag, "")
    gate = f" WHERE {having}" if having else ""
    params.append(limit)
    return conn.execute(
        f"""
        SELECT * FROM (
          SELECT m.id AS member_id, m.stage, m.updated_at, m.contact,
                 m.platform, m.tester_account, m.window_ok, m.answered_at,
                 m.answer_message_id,
                 co.id AS company_id, co.name AS company_name, co.uid,
                 co.canton, co.legal_seat,
                 (SELECT COUNT(*) FROM intel.draft d
                   WHERE d.company_id = m.company_id AND d.sent_at IS NOT NULL)
                     AS sent,
                 (SELECT MAX(d.sent_at) FROM intel.draft d
                   WHERE d.company_id = m.company_id AND d.sent_at IS NOT NULL)
                     AS last_sent,
                 (SELECT COUNT(*) FROM intel.draft d
                   WHERE d.company_id = m.company_id AND d.sent_at IS NULL
                     AND d.refused_at IS NULL) AS pending,
                 (SELECT COUNT(*) FROM crm.email_message e
                   WHERE e.company_id = m.company_id AND e.direction = 'in')
                     AS inbound,
                 (SELECT MAX(e.sent_at) FROM crm.email_message e
                   WHERE e.company_id = m.company_id AND e.direction = 'in')
                     AS last_inbound,
                 (SELECT COUNT(*) FROM core.contact k
                   WHERE k.company_id = m.company_id AND k.kind = 'email'
                     AND k.status = 'bounced') AS failed,
                 (SELECT COUNT(*) FROM core.contact k
                   WHERE k.company_id = m.company_id AND k.kind = 'email'
                     AND k.suppressed) AS refused,
                 (SELECT COUNT(*) FROM crm.programme_feedback f
                   WHERE f.member_id = m.id) AS feedback
            FROM crm.programme_member m
            JOIN core.company co ON co.id = m.company_id
           WHERE {' AND '.join(where)}
        ) x{gate}
         ORDER BY x.last_inbound DESC NULLS LAST, x.company_name
         LIMIT %s
        """,
        tuple(params),
    ).fetchall()


def ledger_tally(conn: Connection, programme_id: int) -> dict[str, int]:
    """The ledger counted by flag, for the heading above it."""
    rows = campaign_ledger(conn, programme_id, limit=100000)
    tally = {key: 0 for key in LEDGER_FLAGS}
    tally["members"] = len(rows)
    for row in rows:
        if row["inbound"]:
            tally["answered"] += 1
        elif row["sent"]:
            tally["silent"] += 1
        if row["failed"]:
            tally["failed"] += 1
        if row["refused"]:
            tally["refused"] += 1
        if row["platform"]:
            tally["enrollable"] += 1
    return tally


def update_member(conn: Connection, member_id: int, **fields: Any) -> None:
    allowed = {"contact", "note"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return
    columns = ", ".join(f"{k} = %s" for k in sets)
    conn.execute(
        f"UPDATE crm.programme_member SET {columns}, updated_at = %s WHERE id = %s",
        (*[str(v).strip() or None for v in sets.values()], now(), member_id),
    )


def remove_member(conn: Connection, member_id: int) -> int | None:
    """Take a company off a programme.

    No tombstone, and the asymmetry with `delete_folder` is deliberate: the
    moves are already in `audit.state_change`, which has no foreign key and
    therefore still holds them. A member removed in error is recoverable from
    that history; a document deleted with its folder is not.
    """
    row = conn.execute(
        "SELECT programme_id FROM crm.programme_member WHERE id = %s", (member_id,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM crm.programme_member WHERE id = %s", (member_id,))
    return int(row["programme_id"])


def bring_in_members(
    conn: Connection, programme_id: int, *, sector: str = "", canton: str = "",
    band: str = "", segment: str = "", legal_form: str = "", limit: int = 25,
) -> int:
    """Put companies matching a survey filter on the programme, in bulk.

    The step between a scored survey and a rollout, done in bulk because doing
    it one company at a time is the reason shortlists sit unworked. It is the
    same shape as the board's own `bring_in` and shares its one rule.

    It only ever inserts. A company already a member keeps whatever stage it
    has reached, so running this again after another sweep cannot walk an
    agency at `testing` back to `selected`.

    `legal_form` takes eCH-0097 codes and exists for a reason `arpenteur.toml`
    states in its own words: a sole proprietorship is registered in a natural
    person's name, so putting one on a programme that will write to it carries
    obligations under the nLPD that writing to a company does not. A campaign
    may choose to accept that; it should not do so without saying so.
    """
    stages = _stages_of(conn, programme_id)
    where = ["c.status = 'ACTIVE'",
             "NOT EXISTS (SELECT 1 FROM crm.programme_member m"
             " WHERE m.programme_id = %s AND m.company_id = c.id)"]
    params: list[Any] = [programme_id]
    joins = ""
    if band:
        joins = " JOIN intel.fit f ON f.company_id = c.id"
        where.append("f.band = %s")
        params.append(band)
    if sector:
        where.append(
            "EXISTS (SELECT 1 FROM intel.sector x"
            " WHERE x.company_id = c.id AND x.sector = %s)"
        )
        params.append(sector)
    if segment:
        where.append(
            "EXISTS (SELECT 1 FROM intel.segment s"
            " WHERE s.company_id = c.id AND s.segment = %s)"
        )
        params.append(segment)
    if canton:
        where.append("c.canton = ANY(%s)")
        params.append([part.strip().upper() for part in canton.split(",") if part.strip()])
    if legal_form:
        where.append("c.legal_form_uid = ANY(%s)")
        params.append([part.strip() for part in legal_form.split(",") if part.strip()])
    order = "f.score DESC NULLS LAST" if band else "c.name"
    rows = conn.execute(
        f"""
        SELECT c.id FROM core.company c{joins}
         WHERE {' AND '.join(where)}
         ORDER BY {order}
         LIMIT %s
        """,
        params + [limit],
    ).fetchall()
    for row in rows:
        add_member(conn, programme_id, int(row["id"]), stage=stages[0])
    return len(rows)


def add_feedback(
    conn: Connection, member_id: int, body: str, *, kind: str = "note",
    occurred_at: str | None = None, author: str | None = None,
) -> int:
    """What a member said back, dated when they said it.

    `occurred_at` is separate from `created_at` because a bug reported on the
    phone on Tuesday and typed up on Thursday belongs to Tuesday, and a
    feedback log whose dates are typing dates cannot answer how long a fault
    was known about.
    """
    body = body.strip()
    if not body:
        raise ValueError("feedback needs something in it")
    created = conn.execute(
        "INSERT INTO crm.programme_feedback (member_id, kind, body, occurred_at,"
        " author, created_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (member_id, (kind or "note").strip() or "note", body,
         occurred_at or now(), (author or "").strip() or None, now()),
    ).fetchone()
    return int(created["id"])


def feedback_for(conn: Connection, member_id: int, *, limit: int = 100) -> list[Row]:
    return conn.execute(
        "SELECT * FROM crm.programme_feedback WHERE member_id = %s"
        " ORDER BY occurred_at DESC, id DESC LIMIT %s",
        (member_id, limit),
    ).fetchall()


def feedback_on(conn: Connection, programme_id: int, *, limit: int = 200) -> list[Row]:
    """A whole programme's feedback in one stream, newest first.

    The testing phase's actual output. Read per programme rather than per
    member because "what are they all telling us" is the question that decides
    what gets fixed, and it cannot be asked one card at a time.
    """
    return conn.execute(
        "SELECT f.*, co.name AS company_name, m.company_id, m.stage"
        "  FROM crm.programme_feedback f"
        "  JOIN crm.programme_member m ON m.id = f.member_id"
        "  JOIN core.company co ON co.id = m.company_id"
        " WHERE m.programme_id = %s"
        " ORDER BY f.occurred_at DESC, f.id DESC LIMIT %s",
        (programme_id, limit),
    ).fetchall()


def delete_programme(
    conn: Connection, programme_id: int, *, reason: str = "", by: str = "console"
) -> str | None:
    """Remove a programme. The companies and their clients are untouched.

    The tombstone goes to `audit.state_change` rather than to a client's
    timeline, because a programme has no client to write to: its members are
    companies, and most of them never became one. That table takes it precisely
    because it holds no foreign key and can outlive what it describes.
    """
    row = conn.execute(
        "SELECT code, name FROM crm.programme WHERE id = %s", (programme_id,)
    ).fetchone()
    if row is None:
        return None
    held = conn.execute(
        "SELECT COUNT(*) AS n FROM crm.programme_member WHERE programme_id = %s",
        (programme_id,),
    ).fetchone()["n"]
    conn.execute("DELETE FROM crm.programme WHERE id = %s", (programme_id,))
    record_state_change(
        conn, entity="programme", entity_id=programme_id, to_state="deleted",
        by=by,
        reason=(reason.strip() or "No reason given.")
        + f" ({held} member(s) released, the companies kept)",
    )
    return str(row["code"])


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


# --- correspondence ------------------------------------------------------------
#
# The import lives here rather than in the scout because filing a message
# produces a `crm.interaction`, and the scout must not know that table exists.
# Matching reads `core.contact` and `intel.site`, which is the permitted
# direction: crm depends on scout, never the reverse.

EMAIL_STATES = ("pending", "filed", "ignored")


def known_messages(conn: Connection, message_ids: list[str]) -> set[str]:
    """Which of these Message-IDs are already held.

    Asked before any body is fetched, so a re-run over the same folder costs
    one query rather than one download per message.
    """
    if not message_ids:
        return set()
    rows = conn.execute(
        "SELECT message_id FROM crm.email_message WHERE message_id = ANY(%s)",
        (message_ids,),
    ).fetchall()
    return {row["message_id"] for row in rows}


def save_email(conn: Connection, row: dict[str, Any]) -> tuple[int, str]:
    """Queue one message. Returns its id and whether it was new.

    A message already held is left exactly as it was, including its state: a
    second import must not resurrect something a person has already ignored,
    which is the behaviour that makes people stop trusting an inbox integration.
    """
    seen = conn.execute(
        "SELECT id FROM crm.email_message WHERE message_id = %s",
        (row["message_id"],),
    ).fetchone()
    if seen:
        return int(seen["id"]), "known"
    made = conn.execute(
        "INSERT INTO crm.email_message (message_id, thread_key, folder, uid,"
        " direction, sent_at, subject, from_name, from_email, to_emails,"
        " cc_emails, body, body_kind, state, company_id, match_method,"
        " match_value, fetched_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
        " %s, %s, %s) RETURNING id",
        (row["message_id"], row.get("thread_key"), row["folder"], row.get("uid"),
         row["direction"], row.get("sent_at"), row.get("subject"),
         row.get("from_name"), row.get("from_email"),
         list(row.get("to_emails") or []), list(row.get("cc_emails") or []),
         row.get("body"), row.get("body_kind"), row.get("state", "pending"),
         row.get("company_id"), row.get("match_method"), row.get("match_value"),
         now()),
    ).fetchone()
    return int(made["id"]), "created"


def set_email_body(conn: Connection, email_id: int, *, text: str, kind: str) -> None:
    """Attach the text, once a person's decision has made it correspondence."""
    conn.execute(
        "UPDATE crm.email_message SET body = %s, body_kind = %s WHERE id = %s",
        (text, kind, email_id),
    )


def email(conn: Connection, email_id: int) -> Row | None:
    return conn.execute(
        "SELECT m.*, c.name AS company_name, cl.id AS client_id, cl.code AS client_code"
        "  FROM crm.email_message m"
        "  LEFT JOIN core.company c ON c.id = m.company_id"
        "  LEFT JOIN crm.client cl ON cl.company_id = m.company_id"
        " WHERE m.id = %s",
        (email_id,),
    ).fetchone()


def email_queue(
    conn: Connection, *, state: str = "pending", limit: int = 200
) -> list[Row]:
    """What is waiting to be decided, matched first.

    The ordering is the whole usability of the page: on a real mailbox most of
    what is fetched is nobody's business, and a queue sorted purely by date
    buries the two messages that matter under a month of newsletters.
    """
    return conn.execute(
        "SELECT m.*, c.name AS company_name, cl.id AS client_id, cl.code AS client_code"
        "  FROM crm.email_message m"
        "  LEFT JOIN core.company c ON c.id = m.company_id"
        "  LEFT JOIN crm.client cl ON cl.company_id = m.company_id"
        " WHERE m.state = %s"
        " ORDER BY (m.company_id IS NULL), m.sent_at DESC NULLS LAST, m.id DESC"
        " LIMIT %s",
        (state, limit),
    ).fetchall()


def email_counts(conn: Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT state, COUNT(*) AS n, COUNT(company_id) AS matched"
        "  FROM crm.email_message GROUP BY state"
    ).fetchall()
    counts = {state: 0 for state in EMAIL_STATES}
    counts["matched"] = 0
    for row in rows:
        counts[row["state"]] = int(row["n"])
        if row["state"] == "pending":
            counts["matched"] = int(row["matched"])
    return counts


def emails_for_company(
    conn: Connection, company_id: int, *, limit: int = 50
) -> list[Row]:
    return conn.execute(
        "SELECT * FROM crm.email_message WHERE company_id = %s"
        " ORDER BY sent_at DESC NULLS LAST LIMIT %s",
        (company_id, limit),
    ).fetchall()


def match_company(
    conn: Connection, addresses: list[str], *, own_domains: frozenset[str] = frozenset(),
    free_domains: frozenset[str] = frozenset(),
) -> tuple[int, str, str] | None:
    """Whose message this is, or None, with how it was decided.

    Five rules, tried in order of how much they claim. An address published by
    a company is nearly proof; a domain is a good guess; nothing else is
    attempted, because the alternatives that suggest themselves here — matching
    a person's name in the subject, or the company name in the body — produce
    exactly the confident wrong answers that make an inbox integration worse
    than no integration.

    Own domains are excluded throughout. A website contact form sends from the
    site's own address, so without this the operator's own domain matches every
    enquiry and the queue proposes the same wrong company for all of them.
    """
    wanted = [a.strip().lower() for a in addresses if a and "@" in a]
    wanted = [a for a in wanted if a.rpartition("@")[2] not in own_domains]
    if not wanted:
        return None
    domains = [a.rpartition("@")[2] for a in wanted]
    # Free-mail domains are matchable by address and never by domain: a gerant
    # writing from Bluewin is worth filing, and filing every Bluewin user in
    # Switzerland against the same client is not.
    corporate = [d for d in dict.fromkeys(domains) if d not in free_domains]

    found = conn.execute(
        "SELECT company_id, lower(value) AS value FROM core.contact"
        " WHERE kind = 'email' AND lower(value) = ANY(%s) LIMIT 1",
        (wanted,),
    ).fetchone()
    if found:
        return int(found["company_id"]), "address", found["value"]

    found = conn.execute(
        "SELECT company_id, value FROM crm.email_link"
        " WHERE kind = 'address' AND value = ANY(%s) LIMIT 1",
        (wanted,),
    ).fetchone()
    if found:
        return int(found["company_id"]), "link", found["value"]

    if not corporate:
        return None

    found = conn.execute(
        "SELECT company_id, value FROM crm.email_link"
        " WHERE kind = 'domain' AND value = ANY(%s) LIMIT 1",
        (corporate,),
    ).fetchone()
    if found:
        return int(found["company_id"]), "link", found["value"]

    found = conn.execute(
        "SELECT company_id, split_part(lower(value), '@', 2) AS domain"
        "  FROM core.contact WHERE kind = 'email'"
        "   AND split_part(lower(value), '@', 2) = ANY(%s) LIMIT 1",
        (corporate,),
    ).fetchone()
    if found:
        return int(found["company_id"]), "domain", found["domain"]

    # The website the enrich stage read. `intel.site.url` carries a scheme and
    # often a www, neither of which appears in a mail address.
    found = conn.execute(
        "SELECT company_id, regexp_replace(lower(url),"
        " '^https?://(www\\.)?([^/:]+).*$', '\\2') AS host"
        "  FROM intel.site WHERE url IS NOT NULL AND url <> ''"
        "   AND regexp_replace(lower(url), '^https?://(www\\.)?([^/:]+).*$', '\\2')"
        "       = ANY(%s) LIMIT 1",
        (corporate,),
    ).fetchone()
    if found:
        return int(found["company_id"]), "site", found["host"]
    return None


def save_email_link(
    conn: Connection, company_id: int, *, kind: str, value: str,
    source: str = "filed",
) -> str:
    """Remember that an address or domain belongs to a company.

    Returns 'saved', 'known', or 'conflict' when the value is already claimed
    by a different company. A conflict is reported rather than resolved: two
    entities sharing a mail domain is a real situation and picking a winner
    silently would file one group company's correspondence against another.
    """
    value = value.strip().lower().lstrip("@")
    if not value:
        return "known"
    seen = conn.execute(
        "SELECT company_id FROM crm.email_link WHERE kind = %s AND value = %s",
        (kind, value),
    ).fetchone()
    if seen:
        return "known" if int(seen["company_id"]) == company_id else "conflict"
    conn.execute(
        "INSERT INTO crm.email_link (company_id, kind, value, source, created_at)"
        " VALUES (%s, %s, %s, %s, %s)",
        (company_id, kind, value, source, now()),
    )
    return "saved"


def links_for(conn: Connection, company_id: int) -> list[Row]:
    return conn.execute(
        "SELECT * FROM crm.email_link WHERE company_id = %s"
        " ORDER BY kind, value",
        (company_id,),
    ).fetchall()


def delete_email_link(conn: Connection, link_id: int) -> int | None:
    row = conn.execute(
        "SELECT company_id FROM crm.email_link WHERE id = %s", (link_id,)
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM crm.email_link WHERE id = %s", (link_id,))
    return int(row["company_id"])


def counterparties(row: Row | dict[str, Any], *, own_domains: frozenset[str]) -> list[str]:
    """The addresses on a message that are not the operator's own."""
    if row["direction"] == "out":
        found = list(row["to_emails"] or []) + list(row["cc_emails"] or [])
    else:
        found = ([row["from_email"]] if row["from_email"] else []) \
            + list(row["cc_emails"] or [])
    return [a for a in dict.fromkeys(a.lower() for a in found if a)
            if a.rpartition("@")[2] not in own_domains]


def file_email(
    conn: Connection, email_id: int, *, client_id: int, project_id: int | None = None,
    by: str | None = None, own_domains: frozenset[str] = frozenset(),
    free_domains: frozenset[str] = frozenset(), learn: bool = True,
) -> tuple[int, list[str]]:
    """Accept a message into a client's timeline. Returns the interaction and
    what filing it taught the matcher.

    This is the only path from the queue into the record, and it is only ever
    reached from a person clicking accept. The Message-ID goes into
    `external_id`, so a message filed today and re-imported next month is
    recognised rather than duplicated.
    """
    row = email(conn, email_id)
    if row is None:
        raise ValueError(f"no such message {email_id}")
    interaction_id = record_interaction(
        conn, client_id,
        kind="email",
        occurred_at=(row["sent_at"] or row["fetched_at"]),
        subject=row["subject"] or "(no subject)",
        body=row["body"],
        project_id=project_id,
        direction="in" if row["direction"] == "in" else "out",
        with_whom=row["from_email"] if row["direction"] == "in"
        else ", ".join(row["to_emails"] or []),
        author=by,
        source="mail",
        external_id=row["message_id"],
    )
    company = conn.execute(
        "SELECT company_id FROM crm.client WHERE id = %s", (client_id,)
    ).fetchone()
    company_id = int(company["company_id"]) if company else None
    # The company is written from the client the person chose, not from
    # whatever the matcher had proposed. A message filed against a company the
    # matcher never found would otherwise keep an empty `company_id`, and the
    # decision would then be invisible to everything that reads the table: the
    # rest of the thread would stay unmatched, which is the case this whole
    # mechanism exists for.
    conn.execute(
        "UPDATE crm.email_message SET state = 'filed', interaction_id = %s,"
        " company_id = COALESCE(%s, company_id),"
        " match_method = COALESCE(match_method, 'manual'),"
        " decided_at = %s, decided_by = %s WHERE id = %s",
        (interaction_id, company_id, now(), by, email_id),
    )
    learned: list[str] = []
    if not learn or company_id is None:
        return interaction_id, learned
    for address in counterparties(row, own_domains=own_domains):
        if save_email_link(conn, company_id, kind="address", value=address) == "saved":
            learned.append(address)
        domain = address.rpartition("@")[2]
        if domain in free_domains:
            continue
        if save_email_link(conn, company_id, kind="domain", value=domain) == "saved":
            learned.append(domain)
    return interaction_id, learned


def ignore_email(conn: Connection, email_id: int, *, by: str | None = None) -> None:
    """Decline a message. It stays held, so a re-import does not offer it again."""
    conn.execute(
        "UPDATE crm.email_message SET state = 'ignored', decided_at = %s,"
        " decided_by = %s WHERE id = %s",
        (now(), by, email_id),
    )


def reopen_email(conn: Connection, email_id: int) -> None:
    """Undo a decision, short of unpicking what filing wrote."""
    conn.execute(
        "UPDATE crm.email_message SET state = 'pending', decided_at = NULL,"
        " decided_by = NULL WHERE id = %s",
        (email_id,),
    )


def thread_of(conn: Connection, thread_key: str) -> list[Row]:
    """Every held message in one chain, oldest first."""
    if not thread_key:
        return []
    return conn.execute(
        "SELECT * FROM crm.email_message WHERE thread_key = %s"
        " ORDER BY sent_at NULLS LAST, id",
        (thread_key,),
    ).fetchall()


def rematch_pending(
    conn: Connection, *, own_domains: frozenset[str] = frozenset(),
    free_domains: frozenset[str] = frozenset(), limit: int = 500,
) -> int:
    """Run the matcher again over what is waiting, and say how many moved.

    Without this the learning loop only helps the future. Filing the reply in a
    thread teaches the matcher the sender's domain, and the six earlier messages
    of the same thread would sit unmatched in the queue for ever, because they
    are already held and an import skips what it already has. So a decision
    reaches backwards as well as forwards.

    Only messages nobody has decided on and nobody has matched are touched: a
    proposal a person has already seen is not quietly replaced with a different
    one, and a filed message is never reconsidered.
    """
    rows = conn.execute(
        "SELECT * FROM crm.email_message"
        " WHERE state = 'pending' AND company_id IS NULL"
        " ORDER BY sent_at DESC NULLS LAST LIMIT %s",
        (limit,),
    ).fetchall()
    moved = 0
    for row in rows:
        found = match_company(
            conn, counterparties(row, own_domains=own_domains),
            own_domains=own_domains, free_domains=free_domains,
        )
        if found is None:
            found = _by_thread(conn, row["thread_key"])
        if found is None:
            continue
        conn.execute(
            "UPDATE crm.email_message SET company_id = %s, match_method = %s,"
            " match_value = %s WHERE id = %s",
            (found[0], found[1], found[2], row["id"]),
        )
        moved += 1
    return moved


def _by_thread(conn: Connection, thread_key: str | None) -> tuple[int, str, str] | None:
    """The company another message in the same chain was matched to.

    Worth doing because the addresses on a message are a poor guide to whose
    conversation it is. An enquiry arriving through a website contact form is
    sent by the site itself, so the envelope names the operator on both sides
    and the only mention of the person is in the text; the reply that follows
    it carries their real address. Two messages, one References chain, and the
    header is the only thing that connects them.

    It is the last rule tried and never overrides an address or a domain, and
    like every other rule it produces a proposal in a queue rather than a
    filing. A chain is strong evidence and not proof: mail threads do get
    hijacked, usually by somebody replying to the last message they can find
    rather than writing a new one, which is exactly the case a person spots at
    a glance and no header ever will.
    """
    if not thread_key:
        return None
    found = conn.execute(
        "SELECT company_id FROM crm.email_message"
        " WHERE thread_key = %s AND company_id IS NOT NULL"
        " ORDER BY (state = 'filed') DESC, sent_at LIMIT 1",
        (thread_key,),
    ).fetchone()
    if found is None:
        return None
    return int(found["company_id"]), "thread", thread_key


def home_counts(conn: Connection) -> dict[str, int]:
    """Clients, campaigns under way and mail waiting, for the landing page.

    A campaign is under way when it is neither closed nor paused and has a
    wave planned: the state is its timestamps, as 0010_campaign.sql says.
    """
    clients = conn.execute("SELECT COUNT(*) AS n FROM crm.client").fetchone()["n"]
    campaigns = conn.execute(
        "SELECT COUNT(*) AS n FROM crm.campaign c"
        " WHERE c.closed_at IS NULL AND c.paused_at IS NULL"
        "   AND EXISTS (SELECT 1 FROM crm.campaign_wave w WHERE w.campaign_id = c.id)"
    ).fetchone()["n"]
    waiting = conn.execute(
        "SELECT COUNT(*) AS n FROM crm.email_message WHERE state = 'pending'"
    ).fetchone()["n"]
    return {"clients": int(clients), "campaigns_running": int(campaigns),
            "mail_waiting": int(waiting)}
