"""The advise stage: what to discuss with a client, what to offer, what it costs.

Every stage before this one is about a company that has not answered yet. This
one runs after a yes, and the question changes: not "is this worth
approaching" but "we are in the room on Thursday, what is the conversation".

It reads the dossier, the register entry, the documents the client sent and the
rate card, and returns discussion points, the services that fit, a price range
for each, the risks and the questions still open.

Three decisions worth stating, because each was the difference between a useful
tool and a plausible one.

**Prices come from the rate card or not at all.** A model asked what a Swiss IT
diagnostic costs answers with a confident four-figure number, and the first
time that number is repeated to a client it stops being a suggestion and
becomes a commitment. So the rate card is quoted into the prompt and the model
is told to price only from it, to name which line it used, and to return a
price of null for work the card does not cover. An empty rate card yields
advice with no prices, which is the honest failure.

**It is told what it does not know.** The dossier's open questions section is
passed as questions, not as material, and the prompt says so: an advisor who
answers "which package holds the resident record" by guessing is worse than one
who puts it at the top of the agenda. Everything asserted has to be traceable
to something given.

**It reads the client's own documents.** An approach built on public evidence
alone is a cold pitch with better research. The accounts, the previous
supplier's proposal and the internal policy are what let it say something the
client has not already heard, and they are exactly what the client sends after
the first meeting. Which documents were read is recorded on the row.

No web access. Everything this stage needs was already gathered, by `enrich`
for a prospect and by hand for a client, so a second round of searching would
pay again for what is in front of it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Callable

from .. import attachments as att
from .. import db, dossier
from ..config import Settings
from ..llm import get_provider

SYSTEM = """You advise an independent consultant preparing to meet a client. \
You are given what is known about the client, the documents they sent, and the \
consultant's own rate card. You return the conversation: what to raise, what \
to offer, and what it costs.

You are not writing to the client. You are briefing the consultant, so be \
direct and skip the pleasantries.

Grounding:
- Everything you assert must trace to something you were given. If the dossier \
says a system is assumed rather than confirmed, treat it as assumed and say so.
- The dossier's open questions are questions. Do not answer them. Work out what \
follows if the answer goes each way, and put the question in the agenda.
- Where the evidence is thin, a shorter answer is the right answer. Padding a \
proposal with generic advice is how a consultant loses a room.

Prices:
- Quote ONLY from the rate card given. Name the line you used in `basis`.
- If the rate card does not cover a piece of work, return low and high as null \
and say in `basis` that the card does not cover it. Never invent a figure, \
never extrapolate one from a card line that does not apply, and never quote a \
market rate from your own knowledge.
- If the rate card is empty, every price is null. Identify the work anyway.
- Prices are in the currency the card uses. Do not convert.

Discussion points:
- Each is something to raise in the meeting, with why it matters to this \
client specifically and the question that opens it.
- Lead with what can be demonstrated. One finding the client can verify on \
their own screen is worth ten that sound plausible.
- Say what argues against acting, where there is something. A client who hears \
only reasons to buy stops believing the rest.

Services:
- Only work that follows from the evidence. A service nobody needs, listed \
because it is on the menu, discredits the ones that are needed.
- `prerequisite` names what must be true or known before it can start. Most \
implementation work is prerequisite on a diagnostic; say so rather than \
quoting it as though it could begin on Monday.
- Order them the way you would actually sequence them.

Write in the language the dossier is written in."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "situation": {
            "type": "string",
            "description": "Where this client is, in four or five sentences, "
                           "as the consultant would summarise it to a colleague.",
        },
        "opening": {
            "type": "string",
            "description": "What to say in the first two minutes of the meeting.",
        },
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "point": {"type": "string"},
                    "why": {"type": "string",
                            "description": "Why it matters to this client specifically."},
                    "ask": {"type": "string",
                            "description": "The question that opens it in the room."},
                    "evidence": {"type": "string",
                                 "description": "What in the material supports it, "
                                                "or 'assumed' where nothing does."},
                },
                "required": ["point", "why", "ask", "evidence"],
                "additionalProperties": False,
            },
        },
        "services": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "what": {"type": "string", "description": "The deliverable."},
                    "why_them": {"type": "string",
                                 "description": "What in this client's situation calls for it."},
                    "prerequisite": {"type": ["string", "null"],
                                     "description": "What must be true or known first."},
                },
                "required": ["name", "what", "why_them", "prerequisite"],
                "additionalProperties": False,
            },
        },
        "pricing": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string",
                             "description": "The service this prices, by its name above."},
                    "low": {"type": ["number", "null"]},
                    "high": {"type": ["number", "null"]},
                    "currency": {"type": "string"},
                    "unit": {"type": "string",
                             "description": "fixed | day | month, following the card."},
                    "basis": {"type": "string",
                              "description": "Which rate card line this comes from, "
                                             "or why the card does not cover it."},
                },
                "required": ["item", "low", "high", "currency", "unit", "basis"],
                "additionalProperties": False,
            },
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What could make this engagement go wrong, including "
                           "reasons not to propose at all.",
        },
        "questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What must be answered before anything can be quoted "
                           "firmly. Carry over the dossier's open questions and "
                           "add what this analysis newly needs.",
        },
    },
    "required": ["situation", "opening", "points", "services", "pricing",
                 "risks", "questions"],
    "additionalProperties": False,
}


def inputs_digest(parts: list[str]) -> str:
    """Identifies what an approach was computed from.

    Same device as `fit.brief_hash`: when the dossier moves or a new document
    arrives, the proposals taken against the old material become findable
    rather than merely older.
    """
    joined = "\n\x00\n".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def build_prompt(
    company: sqlite3.Row,
    client: sqlite3.Row,
    sections: dict[str, dict[str, Any]],
    projects: list[sqlite3.Row],
    papers: list[sqlite3.Row],
    officers: list[sqlite3.Row],
    rate_card: str,
) -> tuple[str, dict[str, Any]]:
    """The whole file, and a record of what went into it.

    The record is returned rather than reconstructed later, because "what did
    this proposal actually read" is the first question asked of a number that
    turns out to be wrong, and reconstructing it from a timestamp is guesswork.
    """
    lines = [
        "CLIENT",
        "=" * 60,
        f"Name: {company['name']}",
        f"Reference: {client['code']}",
        f"UID: {company['uid']}",
        f"Legal form: {company['legal_form_label'] or '?'}",
        f"Seat: {company['legal_seat'] or '?'} ({company['canton'] or '?'})",
        f"Register status: {company['status'] or '?'}",
    ]
    if company["purpose"]:
        lines.append(
            "Registered purpose: " + " ".join((company["purpose"] or "").split())[:2000]
        )
    if officers:
        lines.append(
            "Officers on the register: "
            + "; ".join(f"{o['full_name']} ({o['role']})" for o in officers[:12])
        )
    if client["brief"]:
        lines.append(f"\nStanding brief: {client['brief']}")

    if projects:
        lines.append("\nWORK ALREADY AGREED OR PROPOSED")
        lines.append("=" * 60)
        for project in projects:
            money = (
                f"{project['budget']:,.0f} {project['currency'] or 'CHF'}"
                if project["budget"] else "no budget set"
            )
            lines.append(
                f"- {project['code']} {project['name']} "
                f"[{project['status']}, {project['billing'] or 'billing undecided'}, {money}]"
            )
            if project["summary"]:
                lines.append(f"  {' '.join(project['summary'].split())}")

    lines.append("\nDOSSIER")
    lines.append("=" * 60)
    lines.append(dossier.as_text(sections))

    read: dict[str, Any] = {
        "sections": dossier.filled(sections),
        "documents": [],
        "projects": [p["code"] for p in projects],
        "rate_card": bool(rate_card.strip()),
    }

    readable = [p for p in papers if (p["text"] or "").strip()]
    if readable:
        lines.append("\nDOCUMENTS THE CLIENT PROVIDED")
        lines.append("=" * 60)
        for paper in readable:
            body, cut = att.for_prompt(paper["text"])
            label = att.KIND_LABELS.get(paper["kind"] or "", paper["kind"] or "unclassified")
            lines.append(f"\n--- {paper['filename']} ({label})")
            if paper["note"]:
                lines.append(f"Note from the file: {paper['note']}")
            if cut:
                lines.append(
                    f"[truncated: first {len(body):,} of {len(paper['text']):,} "
                    "characters. Say so if a conclusion needs the rest.]"
                )
            lines.append(body)
            read["documents"].append(
                {"filename": paper["filename"], "kind": paper["kind"] or "",
                 "chars": len(body), "truncated": cut}
            )
    unreadable = [p for p in papers if not (p["text"] or "").strip()]
    if unreadable:
        lines.append(
            "\nAlso held but not machine-readable, so not read here: "
            + ", ".join(p["filename"] for p in unreadable)
        )
        read["unread"] = [p["filename"] for p in unreadable]

    lines.append("\nRATE CARD")
    lines.append("=" * 60)
    lines.append(
        rate_card.strip()
        if rate_card.strip()
        else "(empty. Every price must be null. Identify the work anyway.)"
    )
    return "\n".join(lines), read


def gather(
    conn: sqlite3.Connection, client_id: int
) -> tuple[sqlite3.Row, sqlite3.Row, dict[str, Any], list[sqlite3.Row],
           list[sqlite3.Row], list[sqlite3.Row]]:
    client = conn.execute(
        "SELECT * FROM client WHERE id = ?", (client_id,)
    ).fetchone()
    if client is None:
        raise RuntimeError(f"No client {client_id}.")
    company = conn.execute(
        "SELECT * FROM company WHERE id = ?", (client["company_id"],)
    ).fetchone()
    return (
        client,
        company,
        db.dossier_for(conn, client_id),
        conn.execute(
            "SELECT * FROM project WHERE client_id = ? ORDER BY code", (client_id,)
        ).fetchall(),
        db.attachments_for(conn, client_id, with_text=True),
        conn.execute(
            "SELECT * FROM person WHERE company_id = ? ORDER BY role",
            (client["company_id"],),
        ).fetchall(),
    )


def run(
    settings: Settings,
    conn: sqlite3.Connection,
    *,
    client_id: int,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Suggest an approach for one client. One model call, no web access."""
    client, company, sections, projects, papers, officers = gather(conn, client_id)
    prompt, read = build_prompt(
        company, client, sections, projects, papers, officers, settings.rate_card
    )
    model = settings.llm.match_model or settings.llm.draft_model
    digest = inputs_digest([prompt])

    stats: dict[str, Any] = {
        "client": client["code"],
        "sections": len(read["sections"]),
        "documents": len(read["documents"]),
        "rate_card": read["rate_card"],
        "model": model,
        "inputs": digest,
        "chars": len(prompt),
    }

    if not read["sections"] and not read["documents"]:
        raise RuntimeError(
            "The dossier is empty and no readable document is held, so there is "
            "nothing to advise from. Fill in at least one section first: advice "
            "generated from a register entry alone is a horoscope."
        )
    if not read["rate_card"]:
        log(
            "  ! No rate card set. Work will be identified but every price will "
            "be empty. Settings > What the work costs."
        )

    log(
        f"  reading {len(read['sections'])} dossier sections, "
        f"{len(read['documents'])} documents, {len(prompt):,} characters"
    )
    if dry_run:
        log("  dry run: nothing sent to the model, nothing saved.")
        stats["saved"] = 0
        return stats

    provider = get_provider(settings)
    reply = provider.complete_json(
        system=SYSTEM,
        user=prompt,
        model=model,
        max_tokens=settings.llm.advise_max_tokens,
        schema=SCHEMA,
    )

    approach_id = db.save_approach(
        conn,
        client_id,
        {
            "source": "model",
            "model": model,
            "inputs_hash": digest,
            "read": read,
            "situation": reply.get("situation"),
            "opening": reply.get("opening"),
            "points": reply.get("points"),
            "services": reply.get("services"),
            "pricing": reply.get("pricing"),
            "risks": reply.get("risks"),
            "questions": reply.get("questions"),
        },
    )
    # The suggestion is part of the record, like a project opening. Otherwise
    # the timeline shows a meeting on Thursday and nothing about where the
    # numbers taken into it came from.
    db.record_event(
        conn,
        client_id,
        kind="note",
        occurred_at=db.now(),
        subject="Suggested approach generated",
        body=(
            f"Read {len(read['sections'])} dossier sections and "
            f"{len(read['documents'])} documents. Model {model}, inputs {digest}."
        ),
        author="advise",
        source="model",
    )
    conn.commit()

    priced = sum(1 for p in (reply.get("pricing") or []) if p.get("low") is not None)
    stats.update({
        "saved": approach_id,
        "points": len(reply.get("points") or []),
        "services": len(reply.get("services") or []),
        "priced": priced,
        "questions": len(reply.get("questions") or []),
    })
    log(
        f"  {stats['points']} discussion points, {stats['services']} services, "
        f"{priced} priced, {stats['questions']} open questions"
    )
    return stats
