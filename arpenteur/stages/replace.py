"""Looking again for a firm whose address bounced.

A bounce says one mailbox does not work. It does not say the firm cannot be
written to: in the first Limen campaign three agencies bounced, and two of
them were reachable at once, one at its own `info@` and one at the owner's
mailbox printed in a directory. This stage does what found them, for one firm,
and stops short of deciding: every address it turns up is a proposal a person
accepts or refuses.

**The domain first.** A domain whose mail server does not exist, or that
refuses mail, makes every address at it useless, and saying so is half the
answer: `agencefj.ch` named a Microsoft host that no longer exists. Each of the
firm's domains gets the same verdict the list check gives.

**Then three places, cheapest first.**

  the firm's own pages   home and contact pages, robots.txt honoured, as the
                         hunt reads them. An address at the firm's own domain
                         is the strongest find there is.
  one web search         asked for every address the firm publishes anywhere,
                         with the page each is on, and told which ones bounced.
                         This is where a directory listing turns up.
  the role address       at a domain that takes mail, when nothing at it was
                         found: `info@` beside a `contact@` that bounced. A
                         guess, and proposed as one.

**Unlike the hunt, an address outside the firm's domain is proposed.** The hunt
drops them because nobody reads its output before a list does, and another
firm's address filed under this one is a wrong fact a mail-merge acts on. Here
a person reads each proposal, so a free mailbox or a listing at another domain
is offered with that said in its note, never silently accepted.

**Nothing is proposed twice.** An address the firm already holds, bounced or
not, and one proposed before, accepted or refused, are left out.
"""

from __future__ import annotations

from typing import Any, Callable

from ..config import Settings
from ..kernel.db import Connection, Row
from ..modules.scout import store as scout_store
from ..sources import mailcheck
from . import reach

SEARCH_SYSTEM = (
    "You find the email addresses one named Swiss company publishes, on its own "
    "website or on a public page about it such as a directory listing (local.ch, "
    "search.ch, a trade directory, a listing site). Return only addresses you saw "
    "on a page you actually opened, each with that page's address. Swiss company "
    "names collide constantly: a page about a namesake in another town is a wrong "
    "answer, so leave out anything whose town or register name does not agree. "
    "Never construct an address from a pattern. Some of the company's addresses "
    "are known not to work; never return those."
)

SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "website": {"type": ["string", "null"]},
        "addresses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "address": {"type": "string"},
                    "page": {"type": "string"},
                },
                "required": ["address", "page"],
                "additionalProperties": False,
            },
        },
        "reasoning": {"type": "string"},
    },
    "required": ["website", "addresses", "reasoning"],
    "additionalProperties": False,
}

# The order proposals are shown in: the firm's own page before a listing, a
# listing before a guess, and a desk before anything else from the same place.
FOUND_ORDER = {"page": 0, "listing": 1, "inferred": 2}
USABLE = (mailcheck.DELIVERABLE, mailcheck.MAYBE)


def _firm(conn: Connection, ref: str | int) -> Row:
    if isinstance(ref, int) or str(ref).strip().isdigit():
        row = scout_store.company(conn, int(ref))
        if row is None:
            raise ValueError(f"No company {ref}.")
        return row
    # The lookup by UID or name returns a few columns; the search needs the
    # seat and the canton, so the whole row is read.
    return scout_store.company(conn, int(scout_store.find_company(conn, str(ref))["id"]))


def _ask(provider: Any, firm: Row, *, site: str, replaces: list[str], model: str,
         max_tokens: int) -> tuple[dict[str, Any], int]:
    seat = firm["legal_seat"] or firm["canton"] or "Switzerland"
    user = (
        f"Company: {firm['name']}\n"
        f"Registered seat: {seat}, Switzerland\n"
        f"Swiss UID: {firm['uid']}\n"
        + (f"Website on file: {site}\n" if site else "")
        + (f"These addresses no longer work: {', '.join(replaces)}\n" if replaces else "")
        + "\nFind every other email address the company publishes, and the page "
          "each one is on."
    )
    result = provider.research(
        system=SEARCH_SYSTEM, user=user, model=model, max_tokens=max_tokens,
        schema=SEARCH_SCHEMA, max_searches=3,
    )
    return result.data or {}, int(result.searches or 0)


def search(
    settings: Settings,
    conn: Connection,
    *,
    company: str | int,
    log: Callable[[str], None] = print,
    client: Any = None,
    provider: Any = None,
    resolver: Any = None,
    allow_search: bool = True,
) -> dict[str, Any]:
    """Look again for one firm's address and propose what turns up."""
    firm = _firm(conn, company)
    company_id = int(firm["id"])
    held = conn.execute(
        "SELECT value, status FROM core.contact WHERE company_id = %s AND kind = 'email'",
        (company_id,),
    ).fetchall()
    previous = scout_store.address_proposals(conn, company_id)
    known = {r["value"].lower() for r in held} | {p["address"].lower() for p in previous}
    # One guess per domain, ever: a refused `info@` is not followed by
    # `secretariat@` on the next search, then `reception@` on the one after.
    guessed = {mailcheck.domain_of(p["address"]) for p in previous
               if p["found_by"] == "inferred"}
    replaces = [r["value"] for r in held if r["status"] == "bounced"]
    site_row = conn.execute(
        "SELECT url FROM intel.site WHERE company_id = %s AND status = 'found'", (company_id,)
    ).fetchone()
    site = (site_row["url"] if site_row else "") or ""
    own = list(dict.fromkeys(
        d for d in [*(mailcheck.domain_of(a) for a in replaces),
                    reach._host(site) if site else None] if d))
    log(f"{firm['name']}: looking again"
        + (f" for {', '.join(replaces)}" if replaces else ""))

    verdicts: dict[str, tuple[str, str]] = {}

    def verdict(domain: str) -> tuple[str, str]:
        if domain not in verdicts:
            verdicts[domain] = mailcheck.mail_verdict(domain, resolver=resolver)
        return verdicts[domain]

    for domain in own:
        state, why = verdict(domain)
        log(f"  {domain}: {why}" + ("" if state in USABLE else ", so nothing at it can work"))

    tried: list[dict[str, str]] = []
    found: dict[str, dict[str, Any]] = {}

    def offer(address: str, *, found_by: str, source_url: str | None, note: str = "") -> None:
        address = address.strip().lower()
        if not mailcheck.shape_ok(address) or address in known or address in found:
            return
        domain = mailcheck.domain_of(address)
        state, why = verdict(domain)
        if state not in USABLE:
            tried.append({"what": address, "why": f"{domain}: {why}"})
            return
        kind, _reason = mailcheck.mailbox_role(address, firm["name"])
        notes = [note] if note else []
        if domain in mailcheck.FREE_MAIL:
            notes.append("a free mailbox, not at the firm's own domain")
        elif own and domain not in own:
            notes.append(f"at {domain}, not the firm's own domain")
        found[address] = {"kind": kind, "found_by": found_by, "source_url": source_url,
                          "verdict": state, "note": "; ".join(notes)}

    pages = searches = 0
    agent = settings.zefix.contact
    with (client or reach._client(settings)) as web:
        # The firm's own pages: the site on file, then the bounced domains.
        urls = list(dict.fromkeys(
            [site] * bool(site) + [f"https://{d}/" for d in own
                                   if not site or d != reach._host(site)]))
        for url in urls:
            reading = reach.published(web, url, agent)
            pages += reading.pages
            if reading.refused:
                tried.append({"what": url, "why": "its robots.txt forbids reading it"
                              if reading.refused == "robots.txt" else "it could not be read"})
                continue
            where = {a.lower(): p for a, p in reading.addresses}
            mine, others = reach.own_domain_only([a for a, _ in reading.addresses],
                                                 reach._host(url))
            for address in mine:
                offer(address, found_by="page", source_url=where.get(address.lower()))
            if others:
                tried.append({"what": f"{len(others)} address(es) on {url} at other domains",
                              "why": "another firm's, by their domain"})

        # One web search, which is where a directory listing turns up.
        if allow_search and provider is None:
            try:
                from .. import llm

                provider = llm.get_provider(settings)
            except Exception as exc:  # noqa: BLE001
                tried.append({"what": "web search", "why": f"no model access: {exc}"})
        if allow_search and provider is not None:
            model = getattr(getattr(settings, "llm", None), "enrich_model", "")
            try:
                data, used = _ask(provider, firm, site=site, replaces=replaces,
                                  model=model, max_tokens=1500)
                searches += used
                for item in data.get("addresses") or []:
                    page = (item.get("page") or "").strip()
                    host = reach._host(page) if page else None
                    on_own = bool(host) and any(host == d or host.endswith("." + d)
                                                for d in own)
                    offer(item.get("address") or "",
                          found_by="page" if on_own else "listing",
                          source_url=page or None,
                          note="" if on_own else "seen by a web search on another site")
            except Exception as exc:  # noqa: BLE001
                tried.append({"what": "web search", "why": f"{type(exc).__name__}: {exc}"})

        # The role address, at a domain that takes mail and gave nothing better.
        for domain in own:
            if verdict(domain)[0] not in USABLE or domain in guessed:
                continue
            if any(mailcheck.domain_of(a) == domain and f["kind"] == mailcheck.DESK
                   for a, f in found.items()):
                continue
            for guess in mailcheck.candidates(domain,
                                              language=reach._language_of(firm["canton"])):
                if guess.lower() not in known and guess.lower() not in found:
                    offer(guess, found_by="inferred", source_url=None,
                          note="constructed at a domain that takes mail; the mailbox "
                               "itself is unverified")
                    break

    ranked = sorted(found.items(), key=lambda item: (
        FOUND_ORDER[item[1]["found_by"]], item[1]["kind"] != mailcheck.DESK, item[0]))
    new = 0
    for address, f in ranked:
        new += scout_store.propose_address(conn, company_id, address=address, **f)
        log(f"  + {address}  {f['kind']}, {f['found_by']}"
            + (f" ({f['note']})" if f["note"] else ""))
    for t in tried:
        log(f"  - {t['what']}: {t['why']}")
    scout_store.record_address_search(
        conn, company_id, replaces=replaces,
        domains=[{"domain": d, "verdict": v[0], "why": v[1]}
                 for d, v in verdicts.items() if d in own],
        tried=tried, searches=searches, pages=pages,
    )
    conn.commit()
    log(f"{firm['name']}: {new} address(es) proposed" if new
        else f"{firm['name']}: nothing new found")
    return {"proposed": new, "pages": pages, "searches": searches,
            "domains": {d: v[0] for d, v in verdicts.items() if d in own}}
