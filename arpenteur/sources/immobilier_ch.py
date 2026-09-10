"""The immobilier.ch agency directory: parsing only, no I/O.

A third source of addresses, and the one that answers the question the search
tier was meant to answer. Every core-band company in the survey reached the
cascade with no website known at all, so tier 1 had nothing to read and tiers 3
and 4 were guessing domains from names. What was missing was the domain, and a
directory that publishes it costs one page instead of one metered search.

**Why this one alongside ImmoScout24.** Different populations. ImmoScout24
lists agencies that advertise listings nationally; this portal is French-first
and its 2,104 profiles concentrate where the register work concentrates —
Vaud, Geneva, Valais, Fribourg, Neuchâtel. German-speaking cantons are thin
here and better covered by the other, so the two overlap far less than their
descriptions suggest.

**The data is schema.org, not scraped prose.** Each profile carries a
`RealEstateAgent` block in JSON-LD with the agency's name, its own website, its
published address and its postal address. Reading that is not a trick: it is
the structured description the site publishes for machines to read, which is
the whole purpose of embedding it.

**Consent.** These are the agency's own published business addresses, given on
a page whose reason for existing is to be contacted. That is a weaker claim
than ImmoScout24's explicit `isPublic` flag, and the difference is recorded
rather than smoothed over: there is no per-person flag here, so nothing here is
attributed to a named individual. Role addresses only. See `rank_addresses`.

**robots.txt.** The portal permits `/fr/agences/...` for every agent, and the
profile pages carry no crawl restriction. It does disallow the query-parameter
variants (`?gr=`, `?clrs=`) that the listing pages link to, so the canonical
profile URL — the one in the breadcrumb, without parameters — is the only one
this module builds.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

# The portal's canton slugs, keyed by the register's two-letter code. Only the
# cantons it actually carries; it is a French-language portal and its coverage
# east of the Sarine is thin.
CANTON_OF_SLUG = {
    "vaud": "VD", "geneve": "GE", "valais": "VS", "fribourg": "FR",
    "neuchatel": "NE", "berne": "BE", "zurich": "ZH", "jura": "JU",
    "lucerne": "LU", "tessin": "TI", "zoug": "ZG", "bale-ville": "BS",
    "bale-campagne": "BL", "soleure": "SO", "argovie": "AG", "st-gall": "SG",
    "nidwald": "NW", "obwald": "OW", "grisons": "GR", "glaris": "GL",
    "schaffhouse": "SH", "thurgovie": "TG", "schwyz": "SZ", "uri": "UR",
    "appenzell-rhodes-exterieures": "AR", "appenzell-rhodes-interieures": "AI",
}

SITEMAP = "https://www.immobilier.ch/fr/agences-sitemap"

# /fr/agences/<canton>/<town>/<slug>-<id>
PROFILE = re.compile(r"/fr/agences/([^/]+)/([^/]+)/([^/?#]+)$")
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
MAILTO = re.compile(r"mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# The JSON-LD `email` value is an anchor, and the clean address is in its
# attribute rather than its text, which is deliberately broken up on the page.
EMAIL_ATTR = re.compile(r'email="([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"')
# The logo path carries the *registered* name where the heading carries the
# trading one: "abc-geneve-sarl-1693" against "ABC GENEVE Swiss Real Estate".
# That is the better key for matching a commercial register.
MEDIA_SLUG = re.compile(r"/Medias/([a-z0-9-]+?)-\d+/", re.IGNORECASE)


@dataclass(frozen=True)
class Agency:
    """One agency as the directory describes it."""

    slug: str
    name: str
    # The name under the logo, which is usually the registered one.
    filed_name: str
    canton: str
    town: str
    postcode: str
    street: str
    # The agency's own website, which is the fact the cascade actually lacked.
    site: str
    profile_url: str
    emails: tuple[str, ...] = ()


def agency_urls(sitemap_xml: str) -> list[str]:
    """Every agency profile URL in the agency sitemap, in file order."""
    return [
        url for url in LOC.findall(sitemap_xml or "")
        if PROFILE.search(url)
    ]


def _ld_blocks(page: str) -> list[dict]:
    out: list[dict] = []
    for raw in re.findall(
        r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page or "", re.S
    ):
        try:
            data = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def _text(value: object) -> str:
    """A JSON-LD value that may be an HTML fragment, reduced to its text."""
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def agency_in(page: str, url: str = "") -> Agency | None:
    """The agency on one profile page, or None when the page carries none.

    Addresses are taken from the structured block and from any `mailto:` on the
    page, deduplicated in reading order. Several is normal — a régie publishes
    a switchboard, a lettings desk and a sales desk — and which one to write to
    is the operator's call, so all of them are kept.
    """
    agent = next(
        (b for b in _ld_blocks(page) if b.get("@type") == "RealEstateAgent"), None
    )
    if agent is None:
        return None

    found: list[str] = []
    for candidate in (
        EMAIL_ATTR.findall(str(agent.get("email") or ""))
        + MAILTO.findall(str(agent.get("email") or ""))
        + MAILTO.findall(page or "")
    ):
        address = candidate.strip().lower()
        if address not in found:
            found.append(address)

    postal = agent.get("address") or {}
    if not isinstance(postal, dict):
        postal = {}
    profile = str(agent.get("@id") or url or "")
    match = PROFILE.search(profile)
    slug_canton = (match.group(1) if match else "").lower()
    media = MEDIA_SLUG.search(str(agent.get("logo") or agent.get("image") or ""))

    return Agency(
        slug=(match.group(3) if match else ""),
        name=_text(agent.get("name")),
        filed_name=(media.group(1).replace("-", " ") if media else ""),
        canton=CANTON_OF_SLUG.get(slug_canton, ""),
        town=_text(postal.get("addressLocality")),
        postcode=_text(postal.get("postalCode")),
        street=_text(postal.get("streetAddress")),
        site=str(agent.get("url") or "").strip(),
        profile_url=profile,
        emails=tuple(found),
    )
