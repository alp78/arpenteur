"""The reach stage: resolve a point of contact.

Order matters, and it is cheapest-and-most-consented first:

  published   an address the company itself put on its own site. Free, and
              the only tier where the company chose to be reachable.
  pattern     infer the house convention from addresses already published on
              the domain, then verify before use.
  provider    a paid lookup, last, for what the first two missed.

Whatever the tier, the result carries `method` and `source_url` so the
dataset can always answer where an address came from, and `suppressed` is
checked before anything is ever drafted against it.

**Only `published` is implemented.** The other two are described below where
they would begin, and the stage counts what it leaves for them rather than
reporting a company with no address as simply done.

Three decisions worth stating.

**No model.** `enrich` already reads these sites with one, at about forty
centimes a company, and returned no address at all across the three it has
run on. Finding an address is not a task that needs judgement: it is a
`mailto:` href or a string with an `@` in it. Regex over the fetched page is
free, deterministic, and can name the exact page the address was on, which is
what `source_url` is for. The parsing lives in `sources/web.py`, with no I/O
in it.

**robots.txt is honoured.** Every other fetch this tool makes is either an
API we hold credentials for or a fetch Anthropic's own server performs. This
is the first time it opens a stranger's website itself, and a tool that reads
company sites without checking is a scraper whatever its rate limit says.

**A page budget per company.** Home page plus a handful of links that look
like they carry contact details. A crawler that follows everything would find
marginally more addresses and would be a different kind of program.
"""

from __future__ import annotations

import urllib.robotparser
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from .. import db
from ..config import Settings
from ..kernel.db import Connection, Row
from ..modules.scout import store as scout_store
from ..http import PoliteClient
from ..sources import mailcheck, web

# Home page plus this many candidate contact pages. Four covers the site that
# splits contact, impressum and team across separate pages, and stops well
# short of walking the whole thing.
PAGE_BUDGET = 4

# Companies per run without an explicit --limit. This tier costs nothing but
# tokens are not the only budget: each company is up to five requests to
# someone else's server, so an accidental whole-corpus run is a burst worth
# making the operator ask for.
POLITE_COMPANY_LIMIT = 200

# A company's own site is not a register feed, and a fortnight-old copy of a
# contact page is a fair thing to answer from while the shortlist is worked.
CACHE_MAX_AGE = 14 * 24 * 3600


@dataclass(frozen=True)
class Reading:
    """What one site yielded, and what it cost to ask.

    `pages` and `sitemaps` are kept apart because they answer different
    questions. Pages read with no address means the site genuinely does not
    publish one; sitemaps fetched is politeness accounting.
    """

    addresses: list[tuple[str, str]]
    pages: int
    sitemaps: int
    refused: str | None


def _client(settings: Settings) -> PoliteClient:
    """A polite client aimed at company websites rather than at Zefix.

    No credentials: these are public pages. The rate limit is deliberately
    below the registry's, since here it applies per stranger's server rather
    than to one API sized for the traffic.
    """
    return PoliteClient(
        contact=settings.zefix.contact,
        rate_limit=1.0,
        max_retries=2,
        cache_dir=settings.cache_dir,
        accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        timeout=20.0,
    )


def _page(client: PoliteClient, url: str) -> str:
    """One page as text, or empty on anything that is not a readable page."""
    try:
        reply = client.get(url, max_age=CACHE_MAX_AGE)
    except Exception:
        return ""
    if reply.status != 200 or not isinstance(reply.body, str):
        return ""
    return reply.body


def robots_for(client: PoliteClient, url: str) -> urllib.robotparser.RobotFileParser:
    """The site's robots.txt, fetched through the same polite client.

    RobotFileParser.read() would fetch it with urllib, outside the rate limit
    and outside the cache, so the file is fetched here and handed to the
    parser as lines. A site with no robots.txt, or one we cannot read, is
    treated as allowing: absence of a rule is not a prohibition.
    """
    parts = urlsplit(url)
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", "")))
    body = _page(client, parser.url)
    parser.parse(body.splitlines() if body else [])
    if not body:
        parser.allow_all = True
    return parser


def sitemap_candidates(
    client: PoliteClient,
    url: str,
    robots: urllib.robotparser.RobotFileParser,
    *,
    budget: int,
) -> tuple[list[str], int]:
    """Contact pages named in the site's own sitemap, and what it cost to ask.

    The way in when the home page carries no links. A site built by a page
    builder serves its navigation as JavaScript, so the markup that arrives
    has no `<a href>` in it at all, and the first live run of this stage
    found nothing on such a site while the address sat two clicks away. The
    same site publishes a complete index of its pages at a well known
    address, meant for readers like this one.

    A sitemap index is followed one level, which is where the pages are.
    """
    parts = urlsplit(url)
    named = list(robots.site_maps() or [])
    named.append(urlunsplit((parts.scheme, parts.netloc, "/sitemap.xml", "", "")))
    # A robots.txt may name a sitemap on another host. Following it would file
    # someone else's pages under this company, which is the same mistake as
    # following an off-site link.
    roots = list(dict.fromkeys(root for root in named if web.same_site(root, url)))

    fetched = 0
    for root in roots[:2]:
        body = _page(client, root)
        fetched += 1
        if not body:
            continue
        if not web.is_sitemap_index(body):
            return web.sitemap_links(body, url, limit=budget), fetched
        # An index lists other sitemaps. The pages live in the one named for
        # them; the blog and product feeds are not where an address is.
        children = sorted(
            web.locations(body), key=lambda loc: 0 if "page" in loc.lower() else 1
        )
        for child in children[:2]:
            leaf = _page(client, child)
            fetched += 1
            links = web.sitemap_links(leaf, url, limit=budget) if leaf else []
            if links:
                return links, fetched
    return [], fetched


def published(
    client: PoliteClient, url: str, agent: str, *, budget: int = PAGE_BUDGET
) -> Reading:
    """Addresses the company publishes on its own site.

    Returns the addresses paired with the page each was found on, how many
    pages were read, how many sitemaps it cost to find them, and why nothing
    was read when that is the answer. The pairing is the point: `source_url`
    should be the page carrying the address, not the company's home page, so
    a suppression decision months later can be checked against what was
    actually published.
    """
    robots = robots_for(client, url)
    if not robots.can_fetch(agent, url):
        return Reading([], 0, 0, "robots.txt")

    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def harvest(page_url: str, page: str) -> None:
        for address in web.emails_in(page):
            if address not in seen:
                seen.add(address)
                found.append((address, page_url))

    home = _page(client, url)
    if not home:
        return Reading([], 0, 0, "unreadable")
    pages = 1
    harvest(url, home)

    links = web.contact_links(home, url, limit=budget)
    sitemaps = 0
    if not links:
        links, sitemaps = sitemap_candidates(client, url, robots, budget=budget)

    for link in links:
        if not robots.can_fetch(agent, link):
            continue
        page = _page(client, link)
        if not page:
            continue
        pages += 1
        harvest(link, page)
    return Reading(found, pages, sitemaps, None)


def _select(
    conn: Connection,
    *,
    settings: Settings,
    uids: list[str] | None,
    segment: str | None,
    sector: str | None,
    band: str | None,
    limit: int | None,
    refresh: bool,
) -> list[Row]:
    where = ["w.url IS NOT NULL", "w.url <> ''", "w.status = 'found'"]
    params: list[Any] = []
    if uids:
        where.append(f"c.uid IN ({','.join(['%s'] * len(uids))})")
        params.extend(uids)
    else:
        clause, values = settings.status_clause()
        if clause:
            where.append(clause)
            params.extend(values)
    if segment:
        where.append(
            "EXISTS (SELECT 1 FROM intel.segment s WHERE s.company_id = c.id AND s.segment = %s)"
        )
        params.append(segment)
    if sector:
        where.append(
            "EXISTS (SELECT 1 FROM intel.sector x WHERE x.company_id = c.id AND x.sector = %s)"
        )
        params.append(sector)
    if band:
        where.append("EXISTS (SELECT 1 FROM intel.fit f WHERE f.company_id = c.id AND f.band = %s)")
        params.append(band)
    if not refresh:
        where.append("w.reached_at IS NULL")
    sql = (
        "SELECT c.id, c.uid, c.name, w.url FROM core.company c "
        "JOIN intel.site w ON w.company_id = c.id "
        f"WHERE {' AND '.join(where)} ORDER BY c.id"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def run(
    settings: Settings,
    conn: Connection,
    *,
    uids: list[str] | None = None,
    segment: str | None = None,
    sector: str | None = None,
    band: str | None = None,
    limit: int | None = None,
    refresh: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    companies = _select(
        conn, settings=settings, uids=uids, segment=segment, sector=sector,
        band=band, limit=limit, refresh=refresh,
    )
    # Companies whose research found no site at all cannot be reached this
    # way. Counted and named rather than silently absent, because "no address
    # found" and "no site to look at" are different answers and only the
    # second is a job for the later tiers.
    siteless = conn.execute(
        "SELECT COUNT(*) FROM intel.site WHERE status <> 'found' OR url IS NULL OR url = ''"
    ).fetchone()[0]

    stats: dict[str, Any] = {
        "considered": len(companies), "with_address": 0, "without": 0,
        "addresses": 0, "new": 0, "pages": 0, "sitemaps": 0, "blocked": 0,
        "unreadable": 0, "for_pattern": siteless,
    }
    if not companies:
        log("Nothing to reach. Run enrich first, or pass --refresh.")
        return stats

    if limit is None and not uids and len(companies) > POLITE_COMPANY_LIMIT:
        raise RuntimeError(
            f"{len(companies)} companies selected, each up to {PAGE_BUDGET + 2} "
            "requests to someone else's server. Pass --limit to confirm the "
            "size of the run."
        )

    if dry_run:
        log(f"{len(companies)} companies would have their sites read:")
        for company in companies[:20]:
            log(f"  {company['uid']}  {company['name'][:38]:40} {company['url']}")
        if len(companies) > 20:
            log(f"  ... and {len(companies) - 20} more")
        return stats

    agent = settings.zefix.contact
    with _client(settings) as client:
        for index, company in enumerate(companies, 1):
            try:
                reading = published(client, company["url"], agent)
            except Exception as exc:
                stats["unreadable"] += 1
                log(f"  ! {company['name'][:38]:40} {exc}")
                continue

            stats["pages"] += reading.pages
            stats["sitemaps"] += reading.sitemaps
            if reading.refused == "robots.txt":
                stats["blocked"] += 1
                log(f"  x {company['name'][:38]:40} robots.txt asks us not to")
                continue
            if reading.refused == "unreadable":
                stats["unreadable"] += 1
                log(f"  ! {company['name'][:38]:40} site did not answer")
                continue

            addresses = reading.addresses
            for address, page_url in addresses:
                stats["addresses"] += 1
                stats["new"] += db.save_contact(
                    conn,
                    company_id=company["id"],
                    kind="email",
                    value=address,
                    source_url=page_url,
                    method="published",
                )
            # The marker records the looking, not the finding. A site that
            # publishes only a web form is a settled answer, not a company to
            # re-fetch on every run.
            db.mark_reached(conn, company["id"], pages=reading.pages)
            conn.commit()

            if addresses:
                stats["with_address"] += 1
                log(
                    f"  + {company['name'][:38]:40} "
                    f"{addresses[0][0][:34]}"
                    + (f" (+{len(addresses) - 1})" if len(addresses) > 1 else "")
                )
            else:
                stats["without"] += 1
                stats["for_pattern"] += 1
                log(
                    f"  - {company['name'][:38]:40} "
                    f"{reading.pages} pages, none published"
                )
            if index % 25 == 0:
                log(f"  {index}/{len(companies)}")

    return stats


# --- the cascade --------------------------------------------------------------
#
# Five tiers, cheapest and most consented first, and a company stops at the
# first one that answers. The ordering is not a preference: tier 1 is an
# address the company chose to publish, tier 3 is a string this program made
# up, and a dataset that cannot tell them apart has stopped being evidence.
#
#   1  site      read off the company's own pages          free, certain
#   2  search    a web search finds the site, then read it  costs a search
#   3  inferred  a role address at the known domain         free, a candidate
#   4  variants  other domains the name suggests            free, a candidate
#   5  none      every tier ran, there is nothing           free, and settled
#
# Tier 5 is a result, not a failure. Storing "looked everywhere, found none"
# is what stops the paid tier being spent twice on the same company, and it is
# what lets an export print a dash and mean it.
#
# WHICH IS WHY `none` HAS TO BE EARNED. A run with a tier switched off — no API
# credit for the search, or `--no-search` — has exhausted the free tiers and
# still owes the paid one. Recording that as `none` claims a company publishes
# nothing when nobody finished looking, and the lie is self-concealing: the
# next run selects on the negative and skips exactly the companies the search
# was owed to. So an unfinished cascade returns `partial`, which reads as still
# open everywhere `none` reads as settled.

SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "website": {"type": ["string", "null"]},
        "emails": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["website", "emails", "confidence", "reasoning"],
    "additionalProperties": False,
}

SEARCH_SYSTEM = (
    "You find the official website and published contact addresses of one "
    "named Swiss company. Return only what you can see on a page you actually "
    "opened. Swiss company names collide constantly, so a site that belongs to "
    "a namesake in another canton is a wrong answer, not a partial one: when "
    "the town or the register name does not agree, return null and say so. "
    "Never construct an address from a pattern; that is another tier's job and "
    "it is recorded differently."
)


@dataclass
class Hunt:
    """What the cascade established about one company."""

    outcome: str
    tier: int | None = None
    domain: str | None = None
    addresses: list[str] = dc_field(default_factory=list)
    method: str = ""
    pages: int = 0
    searches: int = 0
    tried: list[dict[str, str]] = dc_field(default_factory=list)
    note: str = ""
    # Page each address was on, so `core.contact.source_url` stays truthful.
    where: dict[str, str] = dc_field(default_factory=dict)


def own_domain_only(
    addresses: list[str], domain: str | None
) -> tuple[list[str], list[str]]:
    """Split what was found into this company's addresses and other people's.

    A company's site carries other companies' addresses: a partner, a group
    sister, the agency that built the site, a syndicate member. Reading naef.ch
    returned `info@acanthe.ch`, `info@sioux.immo` and `info@alios-sa.ch`, and
    because they are role addresses the ranking put all three above Naef's own.

    Filing another firm's address under this one is worse than finding nothing:
    nothing is a gap, and this is a wrong fact that a later mail-merge acts on.
    So only addresses at the site's own domain are kept, and the rest are
    recorded as seen and set aside.
    """
    if not domain:
        return list(addresses), []
    root = domain.lower().lstrip(".")
    mine, theirs = [], []
    for address in addresses:
        at = mailcheck.domain_of(address)
        (mine if at == root or at.endswith("." + root) else theirs).append(address)
    return mine, theirs


def rank_addresses(addresses: list[str]) -> list[str]:
    """Every address found, role addresses first.

    All of them are kept: a régie publishes a switchboard, a lettings desk and
    often a named manager, and which one to write to about a handover is the
    operator's call rather than this program's. The ordering only decides which
    one an export shows when it has room for one.
    """
    ranked = sorted(
        dict.fromkeys(a.strip().lower() for a in addresses if a.strip()),
        key=lambda a: (
            a.partition("@")[0] not in mailcheck.ROLE_PARTS,  # roles first
            len(a),
            a,
        ),
    )
    return ranked


def _search_for_site(
    provider: Any, company: Row, *, model: str, max_tokens: int
) -> tuple[dict[str, Any], int, int]:
    """Tier 2: ask a search to find the site and any address on it.

    Deliberately a much smaller call than `enrich`. That stage researches a
    company in depth and costs about a franc; this one asks two questions and
    caps the searching, because the answer needed here is a domain and a
    mailto, not a picture of the business.
    """
    town = company["legal_seat"] or company["canton"] or "Switzerland"
    user = (
        f"Company: {company['name']}\n"
        f"Registered seat: {town}, Switzerland\n"
        f"Swiss UID: {company['uid']}\n\n"
        "Find its official website and any contact email addresses published "
        "on it. If several addresses are published, return all of them."
    )
    result = provider.research(
        system=SEARCH_SYSTEM, user=user, model=model,
        max_tokens=max_tokens, schema=SEARCH_SCHEMA, max_searches=2,
    )
    return result.data, result.searches, result.input_tokens + result.output_tokens


def hunt_one(
    client: PoliteClient,
    company: Row,
    *,
    agent: str,
    url: str | None = None,
    provider: Any = None,
    model: str = "",
    max_tokens: int = 1200,
    language: str = "",
    mx_cache: dict[str, bool] | None = None,
    places: frozenset[str] | None = None,
    allow_search: bool = True,
    allow_inference: bool = True,
    search_when: Callable[[Row], bool] | None = None,
) -> Hunt:
    """Run the cascade for one company and stop at the first tier that answers.

    `search_when` decides whether this company is worth the paid tier. A
    company it refuses is not a failure and not settled: the cascade records
    `partial`, so a later run with a different policy picks it up again.
    """
    mx_cache = mx_cache if mx_cache is not None else {}
    tried: list[dict[str, str]] = []
    domain = _host(url) if url else None
    pages = searches = 0
    # Whether tier 2 got an answer, as opposed to being switched off or failing
    # on the way out. It decides whether an empty result may call itself
    # settled, so it is set only where the call returns.
    searched = False
    # Whether it was entered at all. `searched` alone cannot tell a tier that
    # was never tried from one that was tried and raised, and reporting the
    # second as the first blames a policy for a timeout.
    attempted = False

    # --- tier 1: the site we already know about -------------------------------
    if url:
        reading = published(client, url, agent)
        pages += reading.pages
        if reading.refused:
            tried.append({"what": url, "why": reading.refused})
        found, others = own_domain_only(
            [a for a, _ in reading.addresses if mailcheck.shape_ok(a)], domain
        )
        found = rank_addresses(found)
        if others:
            tried.append({"what": f"{len(others)} address(es) at other domains",
                          "why": "not this company's domain"})
        if found:
            where = {a.lower(): p for a, p in reading.addresses}
            return Hunt("site", 1, domain, found, "published", pages, 0, tried,
                        where=where)

    # --- tier 2: search the web for the site, then read it --------------------
    #
    # WHY THIS TIER IS GATED AND THE FREE ONES ARE NOT. Every other tier costs
    # a DNS lookup or a page fetch. This one spawns a process, loads a model
    # and bills a turn whether or not it finds anything, so it is the only tier
    # where being called on a hopeless company has a price.
    #
    # Measured over one national run: 6,438 companies reached this tier, 765 of
    # them ran a search that returned something, and the other 5,673 paid the
    # same startup to learn that a firm with no web presence has no web
    # presence. That is 88% of the spend buying nothing, and it is not a tuning
    # problem — it is this tier being asked a question the cheap ones already
    # answered.
    #
    # `search_when` is the caller's policy rather than a rule written here,
    # because what makes a company worth searching for is a survey question and
    # not a property of the cascade. Default None keeps the old behaviour.
    if allow_search and provider is not None and (
        search_when is None or search_when(company)
    ):
        attempted = True
        try:
            data, used, _tokens = _search_for_site(
                provider, company, model=model, max_tokens=max_tokens
            )
            searched = True
            searches += used
            site = (data.get("website") or "").strip()
            said = [a for a in (data.get("emails") or []) if mailcheck.shape_ok(a)]
            if site:
                domain = _host(site) or domain
            # An address the search reported is still hearsay until the page is
            # read, so the site is opened and the address taken from it. What
            # the search is really for is the domain.
            if site:
                reading = published(client, site, agent)
                pages += reading.pages
                seen, others = own_domain_only(
                    [a for a, _ in reading.addresses if mailcheck.shape_ok(a)],
                    _host(site),
                )
                seen = rank_addresses(seen)
                if others:
                    tried.append({"what": f"{len(others)} address(es) at other domains",
                                  "why": "not this company's domain"})
                if seen:
                    where = {a.lower(): p for a, p in reading.addresses}
                    return Hunt("search", 2, domain, seen, "published", pages,
                                searches, tried, where=where)
                if reading.refused:
                    tried.append({"what": site, "why": reading.refused})
            if said:
                # The page could not be read but the search saw addresses on
                # it. Kept, marked as the weaker thing it is, and held to the
                # same domain rule as a page that was read. Without that rule
                # this branch reinstates exactly what the branch above refused:
                # `roydermakler.ch` was found for Rocskai Immobilien GmbH, its
                # page was read, its two `follow.immo` addresses were set aside
                # as another firm's, and this fallback filed them anyway.
                hearsay, outside = own_domain_only(
                    said, _host(site) if site else domain
                )
                if outside:
                    tried.append({
                        "what": f"{len(outside)} address(es) the search reported",
                        "why": "not this company's domain",
                    })
                if hearsay:
                    ranked = rank_addresses(hearsay)
                    return Hunt("search", 2, domain, ranked, "published", pages,
                                searches, tried,
                                note="reported by search, page not readable",
                                where={a: site for a in ranked})
        except Exception as exc:  # noqa: BLE001
            tried.append({"what": "web search", "why": f"{type(exc).__name__}: {exc}"})

    # --- tiers 3 and 4: construct, and verify what can be verified ------------
    if allow_inference:
        wanted: list[str] = []
        if domain:
            wanted.append(domain)
        # Tier 4 is domains the name suggests. It is a real tier rather than a
        # restatement of tier 3 because the *domain* is checkable by DNS where
        # an individual mailbox is not: `nicod.ch` either answers or it does
        # not, and that is a fact.
        for guess in mailcheck.domain_candidates(
            company["name"], seat=company["legal_seat"] or "",
            places=places or frozenset(),
        ):
            if guess not in wanted:
                wanted.append(guess)

        for index, candidate_domain in enumerate(wanted):
            if not mailcheck.accepts_mail(candidate_domain, cache=mx_cache):
                tried.append({"what": candidate_domain, "why": "takes no mail"})
                continue
            # A domain that resolves is not a domain that belongs to this
            # company, and DNS cannot tell the difference. Guessing from branch
            # names produced `nyon.ch`, `lancy.ch` and `fribourg.ch`, which are
            # a commune, a commune and a city: all real, all accepting mail,
            # all the wrong recipient. Only the page settles it, so the page is
            # read and has to name the firm.
            if candidate_domain != _host(url or ""):
                probe = _page(client, f"https://{candidate_domain}/")
                pages += 1
                if not probe:
                    tried.append({"what": candidate_domain, "why": "no page to read"})
                    continue
                if not mailcheck.page_confirms(
                    web.visible_text(probe), company["name"],
                    seat=company["legal_seat"] or "", domain=candidate_domain,
                    places=places or frozenset(),
                ):
                    tried.append(
                        {"what": candidate_domain, "why": "page does not name this firm"}
                    )
                    continue
                # It is theirs, and it was fetched. An address printed on it
                # beats one this program made up, so the site is read properly
                # before anything is constructed: the whole contact-page budget,
                # not just the home page, because a régie prints one address per
                # branch on a contact page and one switchboard on the front.
                reading = published(client, f"https://{candidate_domain}/", agent)
                pages += reading.pages
                seen, others = own_domain_only(
                    [a for a, _ in reading.addresses if mailcheck.shape_ok(a)],
                    candidate_domain,
                )
                seen = rank_addresses(seen)
                if others:
                    tried.append({"what": f"{len(others)} address(es) at other domains",
                                  "why": "not this company's domain"})
                if seen:
                    where = {a.lower(): p for a, p in reading.addresses}
                    return Hunt("site", 1, candidate_domain, seen, "published",
                                pages, searches, tried, where=where)
            role = mailcheck.candidates(candidate_domain, language=language)
            if not role:
                continue
            # Every role address at a domain that accepts mail passes the same
            # check, because MX answers for the domain and never for the
            # mailbox. So one is offered rather than twelve: a list of twelve
            # equally unverified strings is not twelve times the evidence.
            address = role[0]
            return Hunt(
                "inferred", 3 if index == 0 and domain else 4,
                candidate_domain, [address], "inferred", pages, searches, tried,
                note="constructed at a confirmed domain, mailbox unverified",
            )

    # Nothing answered. Whether that is settled depends on what ran: a tier
    # that was switched off, or that raised on its way out, leaves the company
    # owed a look rather than proven unreachable.
    missing = [
        name for name, ran in (("search", searched), ("inference", allow_inference))
        if not ran
    ]
    if missing:
        for name in missing:
            if name == "search" and attempted:
                # Tried and it raised. The exception is already in `tried` with
                # its own reason, so a second line here would only restate it —
                # and the line this used to write blamed a policy that was
                # never set, for what was usually a timeout. A record that
                # contradicts itself is worse than a terse one: the next reader
                # has to decide which half to believe.
                continue
            why = "it did not run"
            if name == "search" and search_when is not None and not search_when(company):
                # Refused on purpose, which is a different fact from switched
                # off or crashed, and the one a later run needs: this company
                # was judged not worth a paid search, on evidence that may
                # change. Saying so is what stops the policy hardening into a
                # permanent exclusion nobody remembers making.
                why = "not worth a paid search under the current policy"
            tried.append({"what": f"the {name} tier", "why": why})
        return Hunt("partial", None, domain, [], "", pages, searches, tried,
                    note="ran without the " + " or ".join(missing) + " tier")
    return Hunt("none", None, domain, [], "", pages, searches, tried,
                note="every tier ran")


# A NOTE ON GATING THIS TIER, BECAUSE THE OBVIOUS GATE IS A TRAP.
#
# 88% of paid calls on one national run bought nothing, so restricting tier 2
# to companies that look promising is the natural fix. The natural signal is a
# known website: the cheap tiers already have a domain, they failed anyway, and
# a search can read what a fetch could not.
#
# Measured against the real queue, that gate skips 5,612 of 5,618 companies.
# Only six companies without an address have a website on file — because tier 2
# is the tier that *discovers* websites, and gating it on already having one
# switches it off completely while appearing to be a refinement.
#
# The cost is real and the gate is not the answer to it. Ordering is: take the
# addresses a directory publishes first, which costs crawling instead of quota
# and fills `intel.site` as a side effect, and let the paid tier work on what
# is left. `search_when` stays available for a caller that wants to bound a run
# on its own terms.


def _host(url: str) -> str | None:
    host = urlsplit(url if "//" in url else f"https://{url}").netloc.lower()
    host = host.split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host or None


def _language_of(canton: str | None) -> str:
    """The language to try role addresses in, from the canton.

    An approximation, and the same one `arpenteur.toml` makes: a canton is
    placed by its majority language. It decides only which of `info@`,
    `kontakt@` or `contatto@` is offered first, so being wrong at the bilingual
    edges costs an ordering rather than a result.
    """
    canton = (canton or "").upper()
    if canton in ("GE", "VD", "NE", "JU"):
        return "fr"
    if canton == "TI":
        return "it"
    if canton in ("FR", "VS"):
        return "fr"
    return "de"


def hunt_all(
    settings: Settings,
    conn: Connection,
    *,
    company_ids: list[int] | None = None,
    sector: str | None = None,
    canton: str | None = None,
    limit: int | None = None,
    refresh: bool = False,
    allow_search: bool = True,
    allow_inference: bool = True,
    search_when: Callable[[Row], bool] | None = None,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
    stop_when: Callable[[], str | None] = lambda: None,
) -> dict[str, Any]:
    """Run the cascade over a selection, cheapest tier first, one pass.

    Every company gets a row in `intel.address_hunt`, including the ones where
    nothing was found. That is the point of the negative: without it the tier
    that costs money is paid for again on the next run, for the same companies,
    for the same nothing.

    `stop_when` is asked before each company and returns a reason to stop, or
    None to carry on. It exists so a caller can end a long run between
    companies rather than inside one: every company commits on its own, so a
    stop here leaves no half-written firm, and the queue is read from the
    database, so a restart resumes. `scripts/limen_hunt.py` uses it to stop
    when a module it loaded has been edited underneath it.
    """
    where = ["c.status = 'ACTIVE'"]
    params: list[Any] = []
    if company_ids:
        where.append("c.id = ANY(%s)")
        params.append(list(company_ids))
    if sector:
        where.append(
            "EXISTS (SELECT 1 FROM intel.sector x"
            " WHERE x.company_id = c.id AND x.sector = %s)"
        )
        params.append(sector)
    if canton:
        where.append("c.canton = ANY(%s)")
        params.append([p.strip().upper() for p in canton.split(",") if p.strip()])
    if not refresh:
        # "Already has a result" and "already has a row" are not the same
        # claim, and treating them as one is how 2,360 companies left the
        # cascade without being looked at. A row can record that one source was
        # checked and had nothing — the agency directory writes exactly that —
        # which is a fact worth keeping and not a reason to stop looking.
        # `partial` is the outcome that says so, and it stays selectable.
        where.append(
            "NOT EXISTS (SELECT 1 FROM intel.address_hunt h"
            " WHERE h.company_id = c.id AND h.outcome <> 'partial')"
        )
    sql = (
        "SELECT c.id, c.uid, c.name, c.canton, c.legal_seat, w.url"
        "  FROM core.company c"
        "  LEFT JOIN intel.site w ON w.company_id = c.id"
        f" WHERE {' AND '.join(where)} ORDER BY c.name"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    companies = conn.execute(sql, params).fetchall()

    stats: dict[str, Any] = {
        "considered": len(companies), "site": 0, "search": 0, "inferred": 0,
        "none": 0, "partial": 0, "addresses": 0, "new": 0, "pages": 0,
        "searches": 0, "failed": 0,
    }
    if not companies:
        log("Nothing to hunt. Everything selected already has a result; "
            "pass --refresh to look again.")
        return stats

    if dry_run:
        log(f"{len(companies)} companies would be hunted:")
        for company in companies[:20]:
            log(f"  {company['uid']}  {company['name'][:40]:42} "
                f"{company['url'] or '(no site known)'}")
        if len(companies) > 20:
            log(f"  ... and {len(companies) - 20} more")
        return stats

    provider = None
    model = ""
    if allow_search:
        try:
            from .. import llm

            provider = llm.get_provider(settings)
            model = settings.llm.enrich_model
        except Exception as exc:  # noqa: BLE001
            log(f"  no search tier: {exc}")

    agent = settings.zefix.contact
    mx_cache: dict[str, bool] = {}
    # Every Swiss commune name, from the register itself rather than a list
    # kept by hand. It is what stops a branch office's town becoming its
    # domain, and it costs one query.
    places = frozenset(
        word
        for row in conn.execute(
            "SELECT DISTINCT legal_seat FROM core.company"
            " WHERE legal_seat IS NOT NULL AND legal_seat <> ''"
        )
        for word in mailcheck.WORD.findall(mailcheck.deaccent(row["legal_seat"]).lower())
        if len(word) > 2
    )
    with _client(settings) as client:
        for index, company in enumerate(companies, 1):
            halt = stop_when()
            if halt:
                stats["stopped"] = halt
                log(f"  stopping after {index - 1} of {len(companies)}: {halt}")
                break
            try:
                hunt = hunt_one(
                    client, company, agent=agent, url=company["url"],
                    provider=provider, model=model,
                    language=_language_of(company["canton"]),
                    mx_cache=mx_cache, places=places, allow_search=allow_search,
                    allow_inference=allow_inference, search_when=search_when,
                )
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += 1
                log(f"  ! {company['name'][:40]:42} {type(exc).__name__}: {exc}")
                continue

            stats[hunt.outcome] = stats.get(hunt.outcome, 0) + 1
            stats["pages"] += hunt.pages
            stats["searches"] += hunt.searches
            stats["addresses"] += len(hunt.addresses)
            for address in hunt.addresses:
                # A consumer mailbox is kept and suppressed rather than
                # dropped. The firm published it, so discarding it would lose
                # the fact that this is the only address it offers; but it
                # reaches a named individual rather than a desk, and an
                # outreach export must not pick it up by default.
                private = mailcheck.is_personal(address)
                stats["personal"] = stats.get("personal", 0) + int(private)
                stats["new"] += db.save_contact(
                    conn, company_id=company["id"], kind="email", value=address,
                    source_url=hunt.where.get(address) or (
                        f"https://{hunt.domain}" if hunt.domain else None
                    ),
                    method=hunt.method or "inferred",
                    suppressed=private,
                )
            scout_store.save_address_hunt(conn, int(company["id"]), hunt)
            conn.commit()

            if hunt.addresses:
                shown = ", ".join(hunt.addresses[:2])
                extra = f" (+{len(hunt.addresses) - 2})" if len(hunt.addresses) > 2 else ""
                log(f"  {hunt.tier} {company['name'][:36]:38} {shown[:44]}{extra}")
            else:
                log(f"  - {company['name'][:36]:38} nothing published")
            if index % 25 == 0:
                log(f"  {index}/{len(companies)}")
    return stats
