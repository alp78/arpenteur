"""The draft stage: propose a message for a human to judge.

Design constraints fixed before a line of it was written, and kept:

  a draft is a proposal      rows land in `draft` with sent_at null. Nothing
                             in this pipeline sends anything by itself.
  written from evidence      the segment, the purpose text and the sector,
                             not a template with a name substituted in.
  language follows canton    French for the romand cantons, German elsewhere.
  low volume by design       Swiss law (LCD art. 3 al. 1 let. o) draws its
                             line at mass advertising email. Researched,
                             individual, human-reviewed messages sit on the
                             right side of it; a bulk sender does not.

**The last constraint is a guard here, not a note.** `send_list` returns 5'126
firms and nothing in it knows what LCD art. 3 al. 1 let. o says. So a run is
capped, the cap has to be lifted deliberately, and the reason is printed when
it bites. A stage that drafted five thousand messages on one command would have
turned the design constraint into a comment.

**The opt-out is appended, never generated.** A model asked for a message with
an unsubscribe line produces one most of the time, and most of the time is not
a standard a legal obligation is met by. The sentence is composed here, in the
recipient's language, and the address it names is the mailbox `bounces` reads,
so a refusal arrives where somebody is already looking.

**Prices and features come from the brief or not at all.** The same rule
`advise` applies to the rate card, for the same reason: a model asked what a
handover app does answers fluently, and the first time an invented feature is
read by a régie the message stops being an approach and becomes a false claim.
`limen.md` carries what is established and marks what is not, and the prompt
forbids filling the gaps.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable

from ..config import Settings
from ..kernel.db import Connection, Row
from ..llm import get_provider
from ..modules.scout import store as scout_store

BRIEF = "limen.md"

# The prose faults the brief names, checked on what came back.
#
# WHY HERE AND NOT BY THE VAULT'S CHECKER. That checker lives outside this
# repository and runs on files a person writes, which a generated message is
# not. The brief lists these four turns as proscribed and a model told not to
# use a construction uses it anyway: the first live draft wrote « Nous restons
# à votre disposition », where `rester` stands in for the verb of the act. So
# the brief states the rule and this enforces it, which is the pattern a
# repeated prose fault earns.
#
# Flagged, never rewritten. Editing a model's sentence here would put this
# stage in the business of writing French, and a reviewer who is shown the
# fault fixes it better than a regex does.
PROSE_FAULTS = (
    (r"\b(?:reste|restent|restons|restez|restait|resteront)\b",
     "« rester » stands in for the verb of the act"),
    (r",\s+et\s+(?:il|elle|ils|elles|nous|vous|je|on|ce|cela|c'est|le|la|les)\b",
     "« , et » splices two clauses"),
    (r"\b(?:acheter|achat|achète|achetez)\b.{0,40}?"
     r"(?:service|prestation|abonnement|licence)",
     "« acheter » applied to a service"),
    (r";[^;\n]{0,120};",
     "a chain of semicolons"),
)

# The cap, and the reason it exists. Lifted with `--yes`, which the CLI makes
# the operator type rather than imply.
SAFE_BATCH = 25

SYSTEM = (
    "You draft one short business email in the recipient's language. "
    "You write only from the brief and the register facts given to you. "
    "You never invent a product feature, a price, a reference or a figure. "
    "Where the brief marks something 'À compléter', you leave it out of the "
    "message rather than guessing it, and you say so in `missing`."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "body": {"type": "string"},
        "missing": {
            "type": "array",
            "items": {"type": "string"},
            "description": "what the brief left blank that this message needed",
        },
    },
    "required": ["subject", "body", "missing"],
}

# The opt-out, per language. Composed rather than asked for, and worded as a
# reply to a named mailbox because that is the route a recipient of a cold
# message actually takes, and the one `bounces` already watches.
OPT_OUT = {
    "fr": ("Si vous ne souhaitez plus recevoir de message de notre part, "
           "répondez « STOP » à cette adresse ({address}) et votre adresse "
           "sera retirée de nos listes."),
    "de": ("Wenn Sie keine weiteren Nachrichten von uns erhalten möchten, "
           "antworten Sie mit « STOP » an diese Adresse ({address}); Ihre "
           "Adresse wird dann aus unseren Listen entfernt."),
    "it": ("Se non desidera più ricevere messaggi da parte nostra, risponda "
           "« STOP » a questo indirizzo ({address}) e il suo indirizzo sarà "
           "rimosso dai nostri elenchi."),
}

ROMAND = ("GE", "VD", "NE", "JU", "FR", "VS")


def language_of(canton: str | None) -> str:
    """The language to write in, from the canton of the seat.

    The approximation `arpenteur.toml` already makes, and the same one
    `reach._language_of` uses: a canton is placed by its majority language.
    Being wrong at the bilingual edges costs a message in the wrong language
    out of two, which a human review catches, and no canton list selects
    Romansh.
    """
    canton = (canton or "").upper()
    if canton in ROMAND:
        return "fr"
    if canton == "TI":
        return "it"
    return "de"


def brief_text(settings: Settings) -> str:
    path = Path(settings.root) / BRIEF
    if not path.exists():
        raise RuntimeError(
            f"{BRIEF} is missing from {settings.root}. The brief is what the "
            "message says; there is no default to fall back on."
        )
    return path.read_text(encoding="utf-8")


def brief_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def prose_faults(text: str) -> list[str]:
    """Which of the brief's proscribed turns this body uses.

    Reported on the row and on the review page, so the reviewer reads a named
    fault rather than hunting for it. A clean result is not a guarantee that
    the French is good; it is a guarantee that these four are absent.
    """
    found: list[str] = []
    for pattern, said in PROSE_FAULTS:
        if re.search(pattern, text, re.IGNORECASE):
            found.append(said)
    return found


def gaps_in(brief: str) -> list[str]:
    """The headings the brief still marks as blank.

    Reported before anything is drafted. A brief whose product section is
    unwritten produces a message about nothing, and finding that out from a
    model's `missing` field one company at a time wastes the calls.
    """
    out: list[str] = []
    heading = ""
    for line in brief.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
        if "À compléter" in line:
            out.append(heading or "the preamble")
    return sorted(dict.fromkeys(out))


def evidence(conn: Connection, company_id: int) -> dict[str, Any]:
    """What the register and the survey hold about one firm.

    No web access and no model call. Everything here was gathered by `source`,
    `profile` and `reach`, so a second round of looking would pay again for
    what is already in front of the stage.
    """
    company = conn.execute(
        "SELECT id, uid, name, canton, legal_seat, purpose, status"
        "  FROM core.company WHERE id = %s", (company_id,)
    ).fetchone()
    if company is None:
        raise RuntimeError(f"No company {company_id}.")
    # The rationale travels with the band, because it is the sentence the
    # classifier wrote about this firm's own purpose text. A band name alone
    # says which bucket; the rationale says what in the register put it there,
    # which is the only thing the message is allowed to assert about them.
    segments = [
        (r["segment"], r["confidence"], r["rationale"] or "")
        for r in conn.execute(
            "SELECT segment, confidence, rationale FROM intel.segment"
            " WHERE company_id = %s ORDER BY segment", (company_id,)
        )
    ]
    sector = conn.execute(
        "SELECT sector FROM intel.sector WHERE company_id = %s LIMIT 1",
        (company_id,),
    ).fetchone()
    return {
        "company": company,
        "segments": segments,
        "sector": sector["sector"] if sector else "",
    }


def build_prompt(
    brief: str, found: dict[str, Any], *, language: str, address: str
) -> str:
    company = found["company"]
    segments = "\n".join(
        f"  - {name} (confidence {confidence if confidence is not None else '?'})"
        + (f"\n      because: {rationale}" if rationale else "")
        for name, confidence, rationale in found["segments"]
    ) or "  (none recorded)"
    return "\n".join([
        "## The brief, verbatim",
        "",
        brief,
        "",
        "## The firm, from the Swiss commercial register",
        "",
        f"Name: {company['name']}",
        f"UID: {company['uid']}",
        f"Canton: {company['canton']}",
        f"Seat: {company['legal_seat'] or '(not recorded)'}",
        f"Sector: {found['sector'] or '(not classified)'}",
        "Bands:",
        segments,
        "",
        "Registered purpose, verbatim:",
        f"  {company['purpose'] or '(not recorded)'}",
        "",
        "## What to write",
        "",
        f"One email in {language}, to {address}, which is a mailbox at this "
        "firm and may be a desk rather than a person. Use a neutral greeting; "
        "do not infer a person's identity from the email address.",
        "",
        "Write from the brief and from the purpose text above. The purpose "
        "text is the one thing you know this firm does, so what you say about "
        "them has to be traceable to it. Where the brief marks something "
        "'À compléter', leave it out and list it in `missing`.",
        "",
        "Do not write a closing opt-out sentence: one is appended afterwards.",
        "Do not write a signature block: one is appended when sending.",
    ])


# The enrolment block, per language. Everything factual in it is substituted
# from settings, so no sentence here depends on a model having got a URL or a
# date right.
ENROL = {
    "fr": (
        "Comment participer\n"
        "\n"
        "iOS, sans condition. Ouvrez ce lien, installez TestFlight, "
        "ajoutez-y Limen : {ios_link}\n"
        "\n"
        "Android, sous deux conditions. Google exige {testers} testeurs "
        "pendant {days} jours continus avant qu'une application paraisse à "
        "son catalogue. Il nous faut donc l'adresse de votre compte Google, "
        "sans laquelle Google ne vous ouvre pas la version de test, et votre "
        "accord pour garder l'application du {start} au {end} en l'ouvrant "
        "sur vos états des lieux réels. Ni séance de test, ni rapport.\n"
        "\n"
        "Pour vous inscrire, répondez à ce courriel avec le nom de votre "
        "régie, la plateforme retenue, et l'adresse de votre compte Google "
        "s'il s'agit d'Android."
    ),
    "de": (
        "So nehmen Sie teil\n"
        "\n"
        "iOS, ohne Bedingung. Öffnen Sie diesen Link, installieren Sie "
        "TestFlight, fügen Sie Limen hinzu: {ios_link}\n"
        "\n"
        "Android, unter zwei Bedingungen. Google verlangt {testers} Tester "
        "über {days} zusammenhängende Tage, bevor eine App in den Store "
        "kommt. Wir benötigen daher die Adresse Ihres Google-Kontos, ohne die "
        "Google Ihnen die Testversion nicht freigibt, und Ihr "
        "Einverständnis, die App vom {start} bis zum {end} zu behalten und "
        "bei Ihren echten Abnahmen zu öffnen. Keine Testsitzung, kein "
        "Bericht.\n"
        "\n"
        "Antworten Sie zur Anmeldung auf diese E-Mail mit dem Namen Ihrer "
        "Verwaltung, der gewählten Plattform und bei Android der Adresse "
        "Ihres Google-Kontos."
    ),
    "it": (
        "Come partecipare\n"
        "\n"
        "iOS, senza condizioni. Apra questo link, installi TestFlight, "
        "aggiunga Limen: {ios_link}\n"
        "\n"
        "Android, a due condizioni. Google richiede {testers} tester per "
        "{days} giorni continuativi prima che un'applicazione compaia nel "
        "suo catalogo. Ci serve quindi l'indirizzo del suo account Google, "
        "senza il quale Google non le apre la versione di prova, e il suo "
        "accordo a conservare l'applicazione dal {start} al {end} aprendola "
        "durante i suoi verbali reali. Nessuna sessione di test, nessun "
        "rapporto.\n"
        "\n"
        "Per iscriversi, risponda a questa e-mail con il nome della sua "
        "agenzia, la piattaforma scelta e, per Android, l'indirizzo del suo "
        "account Google."
    ),
}

# What a reviewer cannot miss. A draft whose link is unset must be unsendable
# by inspection, not merely wrong.
PLACEHOLDER = "[[ MANQUE : {what} ]]"


def enrol_block(enrol: Any, language: str) -> str:
    """The enrolment instructions, with the links and dates substituted.

    Composed here for the reason the opt-out is: a model asked for a
    TestFlight URL writes a plausible, well-formed one that goes nowhere, and
    a recipient who tries it once files the message as a scam. Google's window
    has to be the same two dates for every recipient, which a model asked for
    "a fortnight" cannot do either.
    """
    template = ENROL.get(language, ENROL["fr"])
    return template.format(
        ios_link=enrol.ios_link or PLACEHOLDER.format(what="lien TestFlight"),
        start=enrol.android_window_start or PLACEHOLDER.format(what="date de début"),
        end=enrol.android_window_end or PLACEHOLDER.format(what="date de fin"),
        testers=enrol.android_testers,
        days=enrol.android_days,
    )


def compose(
    body: str, *, language: str, address: str, enrol: Any | None = None
) -> str:
    """The model's text, optional enrolment instructions, then the opt-out.

    Only the first of the three comes from a model. The order is the reading
    order a recipient needs: why they are being written to, what to do, how to
    refuse.
    """
    parts = [body.rstrip()]
    if enrol is not None and enrol.include_in_draft:
        parts.append(enrol_block(enrol, language))
    parts.append(OPT_OUT.get(language, OPT_OUT["fr"]).format(address=address))
    return "\n\n".join(parts) + "\n"


def save(
    conn: Connection, company_id: int, *, address: str, language: str,
    subject: str, body: str, model: str, digest: str,
) -> int:
    """Write a proposal. `sent_at` stays null, which is the whole point."""
    row = conn.execute(
        """
        INSERT INTO intel.draft (company_id, address, language, subject, body,
                                 model, brief_digest)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id, address) WHERE sent_at IS NULL
          AND refused_at IS NULL AND address IS NOT NULL
        DO UPDATE SET language = excluded.language,
                      subject = excluded.subject,
                      body = excluded.body,
                      model = excluded.model,
                      brief_digest = excluded.brief_digest,
                      created_at = now(),
                      reviewed_at = NULL
        RETURNING id
        """,
        (company_id, address, language, subject, body, model, digest),
    ).fetchone()
    return int(row["id"])


def run(
    settings: Settings,
    conn: Connection,
    *,
    desk_parts: tuple[str, ...],
    segments: list[str] | None = None,
    cantons: list[str] | None = None,
    limit: int = SAFE_BATCH,
    confirmed: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Draft one message per firm, for review, never for sending."""
    stats: dict[str, Any] = {
        "selected": 0, "drafted": 0, "skipped": 0, "gaps": [], "missing": {},
    }
    brief = brief_text(settings)
    digest = brief_digest(brief)
    gaps = gaps_in(brief)
    stats["gaps"] = gaps
    if gaps:
        log("The brief still marks these sections 'À compléter':")
        for gap in gaps:
            log(f"  - {gap}")
        log("A message drafted now leaves them out rather than inventing "
            "them. Fill them in and run again for a message that asks for "
            "something.")

    if not settings.mail.configured:
        raise RuntimeError(
            "No mailbox is configured, so the opt-out sentence has no address "
            "to name. A cold message without a working opt-out route is what "
            "LCD art. 3 al. 1 let. o is about; set MAIL_IMAP_SERVER, "
            "MAIL_USERNAME and MAIL_PASSWORD in .env first."
        )
    opt_out_address = settings.mail.user

    # Reported before any model call, because a draft carrying a placeholder
    # cannot be sent and a run that produced twenty-five of them wasted
    # twenty-five calls.
    gaps_in_enrol = (
        settings.enrol.missing() if settings.enrol.include_in_draft else []
    )
    stats["enrol_missing"] = gaps_in_enrol
    if gaps_in_enrol:
        log("These are unset in arpenteur.toml [enrol]:")
        for gap in gaps_in_enrol:
            log(f"  - {gap}")
        log("Each draft will carry a visible [[ MANQUE : ... ]] marker in "
            "its place, so none of them can be sent as it stands.")

    # The campaign's own bands when the caller named none. A stage that swept
    # both bands by default would contradict the brief it is reading.
    wanted = segments or list(settings.enrol.segments) or None
    if wanted and not segments:
        log(f"Writing to {', '.join(wanted)} (arpenteur.toml [enrol] "
            "segments). Pass --segment to widen.")
    rows: list[Row] = scout_store.send_list(
        conn, desk_parts=desk_parts, segments=wanted, cantons=cantons
    )
    stats["selected"] = len(rows)
    if not rows:
        log("Nothing selected. `verify` has to have run: the send list takes "
            "only addresses with a current deliverable verdict.")
        return stats

    # A refused proposal blocks a second identical one. Deleting it used to
    # lose the fact as well as the reason, so the next run proposed the same
    # message to the same mailbox and the reviewer refused it again.
    refused = scout_store.refused_addresses(conn)
    if refused:
        before = len(rows)
        rows = [r for r in rows if (r["address"] or "").lower() not in refused]
        if before != len(rows):
            log(f"{before - len(rows)} mailbox(es) skipped: a proposal to them "
                "was refused and the refusal is kept.")
            stats["selected"] = len(rows)

    # `is not None`, not truthiness: a limit of 0 is a cap of nothing, and read
    # as "no cap" it drafted the whole list, one model call per row.
    capped = rows[:limit] if limit is not None else rows
    if len(capped) < len(rows) and not confirmed:
        log(f"{len(rows):,} firms match and this run will draft "
            f"{len(capped)}.")
        log("The cap is deliberate. LCD art. 3 al. 1 let. o draws its line at "
            "mass advertising email, and a researched, individually reviewed "
            "message sits on the right side of it only while somebody is "
            "actually reading each one. Pass --yes with a larger --limit to "
            "go further.")

    provider = None if dry_run else get_provider(settings)
    model = settings.llm.enrich_model
    for row in capped:
        language = language_of(row["canton"])
        found = evidence(conn, int(row["company_id"]))
        prompt = build_prompt(
            brief, found, language=language, address=row["address"]
        )
        if dry_run:
            log(f"  {row['canton']:<3} {row['name'][:34]:36} {row['address']:<34} "
                f"{language}  {len(prompt):,} chars")
            stats["skipped"] += 1
            continue

        reply = provider.complete_json(
            system=SYSTEM, user=prompt, model=model,
            max_tokens=settings.llm.enrich_max_tokens, schema=SCHEMA,
        )
        body = compose(
            str(reply.get("body") or ""), language=language,
            address=opt_out_address, enrol=settings.enrol,
        )
        save(
            conn, int(row["company_id"]), address=row["address"],
            language=language, subject=str(reply.get("subject") or ""),
            body=body, model=model, digest=digest,
        )
        conn.commit()
        stats["drafted"] += 1
        faults = prose_faults(body)
        for fault in faults:
            stats.setdefault("prose", {})
            stats["prose"][fault] = stats["prose"].get(fault, 0) + 1
        for item in reply.get("missing") or []:
            stats["missing"][str(item)] = stats["missing"].get(str(item), 0) + 1
        log(f"  {row['canton']:<3} {row['name'][:34]:36} {language}  "
            f"{reply.get('subject', '')[:40]}")

    if stats.get("prose"):
        log("\nThe brief proscribes these, and they came back anyway:")
        for fault, count in sorted(
            stats["prose"].items(), key=lambda kv: -kv[1]
        ):
            log(f"  {count:>4}  {fault}")
        log("Shown on each draft's page. Not rewritten here: a reviewer who "
            "is told the fault fixes it better than a regex does.")

    if stats["missing"]:
        log("\nThe model reported these gaps while writing:")
        for item, count in sorted(
            stats["missing"].items(), key=lambda kv: -kv[1]
        ):
            log(f"  {count:>4}  {item[:70]}")

    log(f"\n{stats['drafted']} draft(s) written, none sent. They sit in "
        "intel.draft with sent_at null, awaiting review.")
    return stats
