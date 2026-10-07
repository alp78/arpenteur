"""The prep stage: every term in a client's file, and what to revise.

A client's file accumulates vocabulary faster than anyone absorbs it. One
foundation's dossier carries DMARC, SPF, DKIM, FortiMail, Exchange Online,
VMware Cloud Director, nLPD art. 5 let. c, ISO 9001:2015, ISO 27001, SOMED,
DGCS, CIVESS, CEESV, Medidata, EMS, EPSM, CAT, Sadies DIR, Opale, Syselcom,
Infomaniak and Digital 2030. Some of those you know cold and some you half
know, and the difference shows in a meeting where the client's own IT expert is
sitting there.

This reads the whole file and returns that list: what each term is, why it
appears in *this* client's file, what you must be able to say about it without
looking it up, one question it lets you ask, and where to check it. Plus the
agenda: which four of the twenty decide the conversation.

Three decisions.

**Why it is here, not just what it is.** A general glossary is a search away
and worth nothing at the table. The line that earns the entry is the one tying
the term to this client: DMARC matters here because health records and because
their own SPF authorises 65'536 Citycable addresses. That sentence cannot be
looked up.

**References are named, never linked.** `RFC 7489`, `nLPD art. 5 let. c`,
`ISO 9001:2015 §7.5`. A name can be checked; an invented URL looks exactly like
a real one and is discovered in front of the client.

**A different model from `advise`, deliberately.** This is recall and
organisation over material already in front of it. Arbitrating between three
architecture proposals is judgement, gets the strongest model at a high effort
and is worth the wait. Extracting a glossary at that setting would be slow and
expensive for no gain, so the two stages carry their own model and effort in
`arpenteur.toml`.
"""

from __future__ import annotations

from typing import Any, Callable

from .. import db
from ..config import Settings
from ..kernel.db import Connection
from ..llm import Spend, get_provider
from . import advise

# Stand-in for a provider that does not account for itself, which is every
# fake in the test suite. Reporting zeros is right: nothing was billed.
_NO_SPEND = Spend()


# The shelves the glossary is arranged on. A working vocabulary rather than a
# constraint: the schema does not enforce it, so retiring one leaves the old
# rows readable.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("regulatory", "Law, standards and reporting obligations"),
    ("mail", "Mail, identity and domain security"),
    ("infrastructure", "Infrastructure, hosting and networks"),
    ("sector", "The client's own trade: its jargon and its institutions"),
    ("vendor", "Named companies, products and services"),
    ("governance", "Who decides, and the bodies that decide"),
    ("project", "This client's project and its internal vocabulary"),
)

CATEGORY_LABELS = dict(CATEGORIES)
PRIORITIES = ("must", "should", "nice")

SYSTEM = """You prepare an independent IT consultant for a meeting with a \
client. You are given everything known about that client. You return the \
vocabulary of the meeting and what to revise before it.

Extract EVERY term, acronym, technology, standard, law, institution, product, \
vendor and project name that appears in the material, plus the ones a \
competent participant will use in the room even though the file has not \
written them down yet. Do not filter for what you assume the reader knows. A \
term they know cold costs them two seconds to skip; a term they do not know \
costs them the room.

For each one:
- term: exactly as it appears in the file, and nothing else. Not the expansion, \
not a gloss, not the two joined: `DMARC`, not `DMARC (Domain-based Message \
Authentication...)`. The expansion has its own field and repeating it here \
prints it twice.
- expansion: what the letters stand for. Empty when it is not an acronym.
- what_it_is: plainly, for someone who does not already know. Two or three \
sentences. No padding, no "it is important to understand that".
- why_here: why this term appears in THIS client's file. Quote the specific \
fact from the material that makes it relevant. This is the line that earns the \
entry: a general glossary is a search away and worth nothing at the table.
- must_know: what they must be able to say about it without looking it up. The \
one or two concrete facts that make the difference between sounding informed \
and sounding briefed.
- question: one question this term lets them ask the client. A term you cannot \
turn into a question is a term you do not need before the meeting.
- reference: where to check it. A standard number, a legal article, a \
specification, a clause: RFC 7489, nLPD art. 5 let. c, ISO 9001:2015 §7.5. \
NEVER a URL, and never a guessed identifier. If you are not certain of the \
number, name the document without it. An invented reference looks exactly like \
a real one and is discovered in front of the client.
- priority: must, should or nice. `must` is reserved for what decides the \
conversation, and being reserved is what makes it useful: if a third of the \
list is `must`, the list has told them nothing.

Then the orientation and the agenda.
- orientation: what this client's world is, in a few sentences, for someone \
who has read the file once and needs the shape of it rather than the detail.
- agenda: what to revise first, in order, each with why it comes there. Three \
to six items. Twenty terms is a list; three hours before a meeting the useful \
thing is knowing which four decide the conversation.

Be accurate over complete on facts, and complete over selective on terms. \
Where the material marks something as assumed or unconfirmed, carry that \
through rather than promoting it. Where you are unsure of a technical detail, \
say so in must_know instead of asserting it.

Write in the language the file is written in, except for terms of art, which \
keep their usual form."""

TERM: dict[str, Any] = {
    "type": "object",
    "properties": {
        "term": {"type": "string"},
        "category": {"type": "string", "enum": [name for name, _ in CATEGORIES]},
        "expansion": {"type": ["string", "null"]},
        "what_it_is": {"type": "string"},
        "why_here": {"type": "string"},
        "must_know": {"type": "string"},
        "question": {"type": "string"},
        "reference": {"type": ["string", "null"]},
        "priority": {"type": "string", "enum": list(PRIORITIES)},
    },
    "required": ["term", "category", "expansion", "what_it_is", "why_here",
                 "must_know", "question", "reference", "priority"],
    "additionalProperties": False,
}

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "orientation": {"type": "string"},
        "terms": {"type": "array", "items": TERM},
        "agenda": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "revise": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["revise", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["orientation", "terms", "agenda"],
    "additionalProperties": False,
}


def build_prompt(
    conn: Connection, client_id: int, settings: Settings
) -> tuple[str, dict[str, Any]]:
    """The same file the advise stage reads, minus the rate card.

    Reusing `advise.build_prompt` on purpose. Two assemblers over one dossier
    would drift, and the drift would show up as a glossary that omits the term
    the approach was built on. The rate card is passed empty because prices are
    not vocabulary and there is no reason to put them in front of a second
    model.
    """
    client, company, sections, projects, papers, officers, notes = advise.gather(
        conn, client_id
    )
    prompt, read = advise.build_prompt(
        company, client, sections, projects, papers, officers, "", notes=notes
    )
    read.pop("rate_card", None)
    known = db.known_terms(conn, client_id)
    if known:
        # Told rather than hidden: a term already marked as revised should
        # still appear in the glossary, because the entry is what gets read
        # again the morning of the meeting. What changes is that there is no
        # point spending the agenda on it.
        prompt += (
            "\n\nALREADY REVISED\n" + "=" * 60 + "\n"
            "The consultant has marked these as known. Still return them as "
            "terms, but do not put them in the agenda unless something in the "
            "material has changed what they mean here:\n"
            + ", ".join(known)
        )
        read["already_known"] = list(known)
    return prompt, read


def run(
    settings: Settings,
    conn: Connection,
    *,
    client_id: int,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    prompt, read = build_prompt(conn, client_id, settings)
    model = settings.llm.prep_model or settings.llm.match_model or settings.llm.draft_model
    effort = settings.llm.prep_effort
    digest = advise.inputs_digest([prompt])

    stats: dict[str, Any] = {
        "sections": len(read["sections"]),
        "documents": len(read["documents"]),
        "model": model,
        "effort": effort or "default",
        "inputs": digest,
        "chars": len(prompt),
    }
    if not read["sections"] and not read["documents"]:
        raise RuntimeError(
            "The dossier is empty and no readable document is held, so there is "
            "no vocabulary to extract. Fill in at least one section first."
        )

    log(
        f"  reading {len(read['sections'])} dossier sections, "
        f"{len(read['documents'])} documents, {len(prompt):,} characters "
        f"({model}, effort {effort or 'default'})"
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
        max_tokens=settings.llm.prep_max_tokens,
        schema=SCHEMA,
        effort=effort,
    )

    briefing_id = db.save_briefing(
        conn, client_id,
        {"model": model, "effort": effort, "inputs_hash": digest, "read": read,
         "orientation": reply.get("orientation"), "agenda": reply.get("agenda")},
    )
    added, refreshed = db.save_terms(
        conn, client_id, reply.get("terms") or [], briefing_id=briefing_id
    )
    conn.commit()

    terms = reply.get("terms") or []
    must = sum(1 for t in terms if t.get("priority") == "must")
    stats.update({
        "saved": briefing_id, "terms": len(terms), "added": added,
        "refreshed": refreshed, "must": must,
        "agenda": len(reply.get("agenda") or []),
    })
    log(
        f"  {len(terms)} terms ({added} new, {refreshed} refreshed), "
        f"{must} to know cold, {stats['agenda']} on the agenda"
    )
    # What the run cost, from what the API reported rather than estimated. A
    # stage that cannot be priced afterwards cannot be tuned, and four of the
    # five paid stages were dropping this on the floor.
    stats.update(getattr(provider, "spent", _NO_SPEND).as_stats())
    return stats
