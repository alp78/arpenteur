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
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from .. import db
from ..config import Settings
from ..kernel.db import Connection, Row
from ..http import PoliteClient
from ..sources import web

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
    band: str | None = None,
    limit: int | None = None,
    refresh: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    companies = _select(
        conn, settings=settings, uids=uids, segment=segment, band=band,
        limit=limit, refresh=refresh,
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


# --- the tiers below this line ------------------------------------------------
#
# `pattern` would take the companies counted in for_pattern: a domain we know
# and no published address. Given one address elsewhere on the domain and the
# officers already in `person`, the house convention is usually inferable
# (prenom.nom@, initiale.nom@, nom@). It must not be stored as an address
# until verified, or the dataset fills with plausible strings nobody can tell
# from facts, and `method` stops meaning anything. That verification is the
# part that needs deciding: SMTP probing is rude and widely blocked, so the
# honest options are a paid verifier or storing the guess as a candidate with
# a status of its own.
#
# `provider` is a paid lookup and needs an account, a per-query cost and a
# processing-agreement read before it writes a single row. Under the nLPD a
# bought address has a different basis from a published one, and the `method`
# column is what would let anyone tell them apart afterwards.
