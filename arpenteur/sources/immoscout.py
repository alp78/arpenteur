"""The ImmoScout24 agency directory: parsing only, no I/O.

A second source of addresses, and a better-shaped one than the register for
this question. The commercial register cannot be searched by activity, so
finding rental agencies in it means hydrating a name-gated shortlist and
grading purpose text. This directory is already a curated list of Swiss estate
agencies, it covers all 26 cantons, and it publishes each agency's own contact
addresses.

**Why the parsing lives here and the fetching does not.** Same split as
`web.py`: a pure function over a page is testable without a network, and the
politeness rules belong to whichever stage does the fetching.

**The pages are a Vue application.** The rendered HTML carries no agency links
at all, so an anchor scrape finds nothing. The data is in a
`window.__INITIAL_STATE__` assignment, twenty agencies per page with the
canton, the town and the agents. Reading that is not a trick: it is the same
document the browser reads, and it is what the site serves to a plain GET.

**`isPublic` is honoured, and it is the whole ethic of this module.** Each
agent record carries a flag saying whether that person's address may be shown.
An address with the flag false is skipped, not stored. The site is stating a
consent decision on behalf of a named individual and there is no version of
this work where overriding it is acceptable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

STATE = "window.__INITIAL_STATE__"

# Canton slugs the directory uses, keyed by the register's two-letter code, so
# a caller can walk the country without hard-coding twenty-six URLs.
CANTON_SLUG = {
    "AG": "kanton-aargau", "AI": "kanton-appenzell-ir",
    "AR": "kanton-appenzell-ar", "BE": "kanton-bern", "BL": "kanton-basel-land",
    "BS": "kanton-basel-stadt", "FR": "kanton-freiburg", "GE": "kanton-genf",
    "GL": "kanton-glarus", "GR": "kanton-graubuenden", "JU": "kanton-jura",
    "LU": "kanton-luzern", "NE": "kanton-neuenburg", "NW": "kanton-nidwalden",
    "OW": "kanton-obwalden", "SG": "kanton-st-gallen",
    "SH": "kanton-schaffhausen", "SO": "kanton-solothurn", "SZ": "kanton-schwyz",
    "TG": "kanton-thurgau", "TI": "kanton-tessin", "UR": "kanton-uri",
    "VD": "kanton-waadt", "VS": "kanton-wallis", "ZG": "kanton-zug",
    "ZH": "kanton-zuerich",
}

BASE = "https://www.immoscout24.ch/agenturverzeichnis"


def index_url(canton: str = "", page: int = 1) -> str:
    """The directory page for a canton, or the national one with no canton."""
    root = f"{BASE}/{CANTON_SLUG[canton]}" if canton else BASE
    return root if page <= 1 else f"{root}?page={page}"


@dataclass(frozen=True)
class Agency:
    """One agency as the directory describes it."""

    customer_id: str
    name: str
    canton: str
    town: str
    postcode: str
    url: str
    # Addresses whose owner allowed publication, in listing order.
    emails: tuple[str, ...] = ()
    # Addresses present but flagged private. Counted, never stored, so a run
    # can report how much it deliberately left alone.
    withheld: int = 0
    agents: tuple[dict[str, str], ...] = field(default=())


def _balanced(text: str, start: int) -> str:
    """The JSON object beginning at `start`, brace-balanced.

    A regex to the next `};` breaks on the first brace inside a description,
    and the descriptions here are HTML. Balancing also has to ignore braces
    inside strings, which is why this counts rather than matches.
    """
    depth = 0
    in_string = escaped = False
    for index, char in enumerate(text[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ValueError("unbalanced state object")


def state_of(page: str) -> dict:
    """The page's own data, or an empty dict when the page carries none."""
    at = page.find(STATE)
    if at < 0:
        return {}
    brace = page.find("{", at)
    if brace < 0:
        return {}
    try:
        return json.loads(_balanced(page, brace))
    except (ValueError, json.JSONDecodeError):
        return {}


def total_agencies(page: str) -> int | None:
    profiles = state_of(page).get("agencyProfiles") or {}
    count = profiles.get("totalAgenciesCount")
    return int(count) if isinstance(count, (int, float)) else None


def agencies_in(page: str) -> list[Agency]:
    """Every agency on one directory page.

    Emails are taken only from agents flagged public, and the flag is read
    strictly: anything other than a true value counts as withheld, so a schema
    change that drops the field fails closed rather than open.
    """
    profiles = state_of(page).get("agencyProfiles") or {}
    out: list[Agency] = []
    for raw in profiles.get("agencies") or []:
        allowed: list[str] = []
        withheld = 0
        people: list[dict[str, str]] = []
        for agent in raw.get("agents") or []:
            address = str(agent.get("email") or "").strip().lower()
            if not address:
                continue
            if agent.get("isPublic") is not True:
                withheld += 1
                continue
            if address not in allowed:
                allowed.append(address)
            people.append({
                "email": address,
                "name": " ".join(
                    part for part in (agent.get("firstName"), agent.get("lastName"))
                    if part
                ).strip(),
                "role": str(agent.get("jobTitle") or "").strip(),
            })
        keyword = str(raw.get("profilePageUrlKeyword") or "").strip()
        customer = str(raw.get("customerId") or "").strip()
        out.append(Agency(
            customer_id=customer,
            name=str(raw.get("customerName") or "").strip(),
            canton=str(raw.get("state") or "").strip().upper(),
            town=str(raw.get("city") or "").strip(),
            postcode=str(raw.get("postalCode") or "").strip(),
            url=(
                f"https://www.immoscout24.ch/anbieter/{customer}/{keyword}"
                if customer and keyword else ""
            ),
            emails=tuple(allowed),
            withheld=withheld,
            agents=tuple(people),
        ))
    return out


# A directory name and a registered name are never spelt the same. "Regie Naef
# SA" is registered, "Naef Immobilier Genève" is on the door, and matching them
# needs the noise gone from both sides.
TRIM = re.compile(
    r"\b(sa|ag|sarl|sagl|gmbh|s\.a\.|sàrl|immobilien|immobilier|immobiliere|"
    r"immobiliare|immo|regie|régie|gerance|gérance|verwaltung|treuhand|"
    r"group|groupe|gruppe|holding|partners?|co|und|and|et)\b",
    re.IGNORECASE,
)


def match_key(name: str) -> str:
    """A name reduced to what two spellings of one firm have in common."""
    import unicodedata

    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", name or "")
        if not unicodedata.combining(ch)
    ).lower()
    return re.sub(r"[^a-z0-9]+", "", TRIM.sub(" ", folded))
