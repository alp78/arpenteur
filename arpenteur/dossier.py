"""The client dossier: the named parts of what is known, and what fills them.

The register says what a company is. This says what it runs, who decides, what
is exposed, and what has already been tried. None of that is in any register.
It arrives from four different places with four different degrees of certainty:
public evidence you gathered, documents the client sent, what someone said in a
meeting, and what you inferred. The sections keep them apart, and the `source`
line on each keeps the fourth from being read as the first.

The vocabulary lives here rather than in the schema because it is a working
vocabulary, not a data model. Adding a section is a one-line change and needs
no migration; retiring one leaves the old rows readable.

The same list drives three things, which is the point of having it in one
place: the form in the console, the order the dossier prints in, and the
material the advise stage is given. A section nobody can fill in from the form
can never reach the prompt, and a section the prompt ignores has no business
being on the form.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Section:
    name: str
    title: str
    # What belongs here, in one or two sentences. Shown above the field.
    what: str
    # A real example, shown greyed inside the empty field. The complaint that
    # produced these was exact: a form with no example of the expected shape is
    # a form people fill in wrongly and then distrust.
    example: str
    # Which of the three groups the form puts it in.
    group: str = "organisation"
    rows: int = 5


GROUPS: tuple[tuple[str, str, str], ...] = (
    (
        "organisation",
        "The organisation",
        "What they do, at what scale, and who decides. This is the part a "
        "second person joining the engagement needs before anything else.",
    ),
    (
        "systems",
        "The systems",
        "What actually runs the business, who supplies it, and what can be "
        "seen from outside. Keep observed facts and reported ones apart in "
        "the source line: it is the difference between a diagnostic and a "
        "guess.",
    ),
    (
        "engagement",
        "The engagement",
        "What constrains the work, what has been tried before, and what is "
        "still unknown. The last of those is the one that decides the first "
        "meeting.",
    ),
)


SECTIONS: tuple[Section, ...] = (
    Section(
        name="situation",
        title="What they do and at what scale",
        what="Activity, size, sites, and how they are funded. Numbers where "
             "you have them, because every later judgement leans on scale: a "
             "fix that is proportionate at 280 staff is absurd at 12.",
        example="Foundation of public utility, care sector. ~280 staff across "
                "7 sites in one city. Two divisions with separate regulators. "
                "Funded by cantonal tariffs and residents' contributions, not "
                "by sales. Opened a new 60-bed site in 2025 jointly with "
                "another foundation, so one building holds two legal entities.",
        group="organisation",
    ),
    Section(
        name="people",
        title="Who decides, and who is missing",
        what="Names and roles of the people who can say yes, who holds the "
             "budget, and who will actually live with the result. Note the "
             "role that does not exist: an absent owner is why the work is "
             "available.",
        example="Directeur général since Oct 2024 (predecessor struck from the "
                "register July 2025, so old contact lists are wrong). Board "
                "has a finance commission president and an infrastructure "
                "commission president. Management college of seven: care, "
                "care, HR, logistics, projects, kitchen, catering. No IT or "
                "data role at any level. Nearest owner is Projects and "
                "Infrastructure, whose remit reads as buildings.",
        group="organisation",
    ),
    Section(
        name="constraints",
        title="What constrains the work",
        what="Regulator, reporting obligations, procurement rules, budget "
             "cycle, calendar, languages, and anything that decides how a "
             "proposal has to be shaped before it can be accepted.",
        example="Cantonal supervision; mandatory statistical returns on a "
                "fixed annual calendar. Budget voted by the board once a "
                "year, so anything above a threshold waits for the cycle. "
                "Health data, so nLPD art. 5 let. c sensitive personal data "
                "and a processing register is expected. Working language "
                "French.",
        group="engagement",
    ),
    Section(
        name="systems",
        title="What runs the business",
        what="The applications, by function: the operational record, "
             "accounting, payroll, planning, purchasing. Name versions and "
             "who administers each where you know. Mark what is assumed.",
        example="Resident record: assumed to be the sector-standard package, "
                "not yet confirmed. Payroll and accounting: unknown, likely "
                "the same vendor's suite or a separate ERP with an interface. "
                "Rotas: reported as spreadsheets. Mandatory statistical "
                "returns: reported as compiled by hand each January.",
        group="systems",
    ),
    Section(
        name="data",
        title="Where the data lives, and who reconciles it",
        what="Which system is authoritative for which fact, where the same "
             "fact exists twice, and who fixes it when the two disagree. This "
             "is usually one named person and a spreadsheet, and finding them "
             "is most of the diagnostic.",
        example="Head count exists in payroll, in the rota tool and in the "
                "annual return, and the three do not agree. Reconciled by one "
                "person in HR, monthly, in Excel. No single register of "
                "residents across the two divisions: each keeps its own.",
        group="systems",
    ),
    Section(
        name="providers",
        title="Who supplies and supports IT",
        what="The managed service provider, the software vendors, the web "
             "agency, the auditor. Contract terms and renewal dates if known. "
             "A named incumbent is a constraint on the proposal and sometimes "
             "a reason not to make one.",
        example="Infrastructure and mail: a regional managed-service provider, "
                "ISO 27001, private cloud. Website: a local agency, "
                "WordPress. Statutory auditor changed in 2022. No evidence of "
                "any independent advisor between the foundation and its "
                "providers, which is the gap this engagement fills.",
        group="systems",
    ),
    Section(
        name="estate",
        title="What is visible from outside",
        what="Domains, mail routing, hosting, published surface. Observed "
             "facts only, with the date. Everything here is checkable by a "
             "third party, which is what makes it usable as evidence in a "
             "first meeting.",
        example="MX points to the provider's mail gateway. SPF authorises "
                "seven senders across six providers, two of them whole /16 "
                "ranges, and includes both the provider and a Microsoft 365 "
                "tenant: a migration that was started and not finished. Web "
                "on a Swiss host, WordPress, kept current. Certificate "
                "transparency shows three names ever issued, so no public "
                "portal exists. Observed 12.08.2026.",
        group="systems",
    ),
    Section(
        name="exposure",
        title="What is exposed or missing",
        what="The concrete gaps, each one checkable. Keep this to what you "
             "can demonstrate rather than what you suspect: one verifiable "
             "finding shown on screen is worth ten plausible ones.",
        example="No DMARC record at all, so nobody receives a report of who is "
                "sending as this domain and nobody could say which of the "
                "seven authorised senders is still real. No DKIM at the "
                "standard selectors. Privacy notice is undated, names no "
                "processor, and cites no legal basis, while the site loads two "
                "US analytics and mapping services.",
        group="systems",
    ),
    Section(
        name="history",
        title="What has been tried before",
        what="Previous projects, previous advisors, what was abandoned and "
             "why. A proposal that unknowingly repeats a failure is dead on "
             "arrival, and the client will not warn you.",
        example="Quality certification maintained since 2000, so the habit of "
                "auditable process exists and does not need selling. No "
                "record of any prior IT or data mandate. Two institutions "
                "absorbed from another organisation in 2020, whose systems "
                "may never have been merged.",
        group="engagement",
    ),
    Section(
        name="questions",
        title="What we still do not know",
        what="The open questions, in the order you would ask them. This "
             "section is the agenda of the next meeting, and the advise stage "
             "is told to leave it alone rather than answer it by guessing.",
        example="Which package holds the resident record, and which version? "
                "Who administers it, them or the provider? Who compiles the "
                "annual statistical return, and how long does it take? Is the "
                "Microsoft 365 tenant in use or abandoned? Are the two "
                "absorbed sites on the same systems as the rest?",
        group="engagement",
    ),
)

BY_NAME: dict[str, Section] = {section.name: section for section in SECTIONS}


def group_sections(group: str) -> list[Section]:
    return [section for section in SECTIONS if section.group == group]


def as_text(rows: dict[str, dict[str, str]]) -> str:
    """The dossier as plain text, in section order.

    Used for the advise prompt and for the CLI dump an agent reads, so the two
    cannot show different things. Empty sections are named rather than dropped:
    that a dossier says nothing about who decides is itself information, and a
    reader who cannot tell an empty section from an absent one will assume the
    first.
    """
    out: list[str] = []
    for section in SECTIONS:
        entry = rows.get(section.name) or {}
        body = (entry.get("body") or "").strip()
        source = (entry.get("source") or "").strip()
        out.append(f"## {section.title}")
        out.append(body if body else "(not filled in)")
        if source:
            out.append(f"[source: {source}]")
        out.append("")
    return "\n".join(out).strip()


def filled(rows: dict[str, dict[str, str]]) -> list[str]:
    """Which sections actually carry something. Recorded on every approach."""
    return [
        section.name
        for section in SECTIONS
        if ((rows.get(section.name) or {}).get("body") or "").strip()
    ]
