"""The immobiliensektor.ch trade directory: parsing only, no I/O.

The German-speaking half of the problem. `immobilier.ch` is a French-first
portal — 788 agencies in Vaud against 39 in Zurich — so the survey's largest
cantons were reached by neither directory. This one lists 1,091 firms under
"Immobilienverwaltung, Liegenschaftenverwaltung" and is weighted the other way.

**What it publishes, and why that is still the useful half.** No email
addresses: a detail page carries the firm's postal address, its telephone
number and a link to its own website. The website is the fact the cascade
actually lacks — of the companies in the survey with no address at all, six
have a website on file — and it is the durable half. An address can be
superseded; a domain is what lets tier 1 read the firm's own contact page on
this run and every later one, for the cost of a fetch rather than a model call.

So this source is not a shortcut to addresses. It is what makes the free tier
able to find them, which is the cheapest order available: crawl, then read,
and reach for the paid tier only for what is still missing afterwards.

**Two pages per firm.** The listing carries name, street and town but not the
website, so the domain costs a second fetch. 110 listing pages plus 1,091
detail pages, against 1,091 model calls for the same firms.

**robots.txt.** Disallows `/grafik/`, `/images/`, `/img/`, `/data/`, `/CFIDE/`,
`/netmon/`, `/_search/` and `/helpservices/`. Neither the listing nor the
detail path is restricted, and no AI-specific rule is present.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

BASE = "https://www.immobiliensektor.ch"
CATEGORY = "Immobilienverwaltung-Liegenschaftenverwaltung"
PER_PAGE = 10

# Hosts this directory links to on every page: its own network of sister sites,
# the postcode lookup it renders addresses with, and the usual social buttons.
# None of them is ever a listed firm's website.
NOT_A_FIRM = re.compile(
    r"immobiliensektor|help\.ch|help\.swiss|game\.ch|firmenstatistik|"
    r"konsumentenbewertung|rabattissimo|swiss-press|adressenshop|"
    r"facebook|twitter|linkedin|google|instagram|youtube|xing|w3\.org",
    re.IGNORECASE,
)

ENTRY = re.compile(r'href="(adresse\.cfm\?key=(\d+)&(?:amp;)?firma=([^"]*))"', re.I)
LINK = re.compile(r'href="(https?://[^"]+)"', re.I)
TOTAL = re.compile(r"(\d[\d'’]*)\s*Treffer", re.I)
# The postcode is rendered as a link to the directory's own lookup, which makes
# it the one reliably delimited field on the line.
PLACE = re.compile(r'plz=(\d{4})">\s*\d{4}\s*</a>\s*([^<\n]{2,40})', re.I)


def listing_url(page: int = 1) -> str:
    root = f"{BASE}/verzeichnissparte.cfm?sparte=1&kategorie={CATEGORY}"
    return root if page <= 1 else f"{root}&startseite={page}"


def detail_url(key: str, slug: str = "") -> str:
    return f"{BASE}/adresse.cfm?key={key}&firma={slug}"


@dataclass(frozen=True)
class Listed:
    """One firm as the listing page names it."""

    key: str
    slug: str
    name: str
    postcode: str
    town: str


def total_firms(page: str) -> int | None:
    found = TOTAL.search(page or "")
    if not found:
        return None
    return int(re.sub(r"[^\d]", "", found.group(1)))


def firms_on(page: str) -> list[Listed]:
    """Every firm on one listing page, in listing order.

    Anchored on the detail link rather than on the surrounding markup, which is
    hand-written table soup that differs between entries. The name is the text
    immediately after the link, cut at the first tag.
    """
    out: list[Listed] = []
    seen: set[str] = set()
    for match in ENTRY.finditer(page or ""):
        key, slug = match.group(2), match.group(3)
        if key in seen:
            continue
        seen.add(key)
        # The match ends inside the opening tag, so skip to the end of it
        # before reading the link text.
        after = page[match.end() : match.end() + 400]
        after = after[1:] if after.startswith(">") else after
        name = html.unescape(re.split(r"<", after, maxsplit=1)[0]).strip()
        if not name:
            name = html.unescape(slug.replace("-", " ")).strip()
        place = PLACE.search(after)
        out.append(Listed(
            key=key,
            slug=slug,
            name=" ".join(name.split()),
            postcode=(place.group(1) if place else ""),
            town=(" ".join(place.group(2).split()) if place else ""),
        ))
    return out


def website_on(page: str) -> str:
    """The firm's own website from its detail page, or an empty string.

    Taken as the first outbound link that is not part of the directory's own
    network. The order matters: the firm's site is rendered above the sister
    sites in the page footer, so first-wins is the right rule and a later
    sponsored link cannot displace it.
    """
    for url in LINK.findall(page or ""):
        if not NOT_A_FIRM.search(url):
            return url.strip()
    return ""
