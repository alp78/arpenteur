"""Walk the immobilier.ch agency directory and match it to the register.

Two phases, for the same reason `source` and `hydrate` are two stages: the
crawl is the part that touches somebody else's server, and re-running a
matching rule must not repeat it.

    uv run python scripts/immobilier_directory.py crawl --out data/immobilier.jsonl
    uv run python scripts/immobilier_directory.py match data/immobilier.jsonl --dry-run
    uv run python scripts/immobilier_directory.py match data/immobilier.jsonl

**Why this source.** Every core-band firm in the survey reached the address
cascade with no website known, so tier 1 had nothing to open and the search
tier — the one that finds the website — has never had credit to run. This
directory publishes the website and the address together, 2,104 agencies over
2,104 pages, which is the same answer without the metered call.

**What it writes that the other sources do not.** The agency's own site goes to
`intel.site`. That is the durable half: an address can be superseded, but a
domain is what lets tier 1 read the firm's own pages on every later run, and
none of these companies had one.

**Politeness.** One page per agency, paced, through the same client every other
stage uses. `robots.txt` permits `/fr/agences/...` for all agents and disallows
only the query-parameter variants, which this never builds.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arpenteur import db  # noqa: E402
from arpenteur.config import load_settings  # noqa: E402
from arpenteur.modules.scout import store as scout_store  # noqa: E402
from arpenteur.sources import immobilier_ch as ic  # noqa: E402
from arpenteur.sources import immoscout as im  # noqa: E402
from arpenteur.sources import mailcheck  # noqa: E402
from arpenteur.stages import reach  # noqa: E402


class Hunt:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def _plain(text: str) -> str:
    """A name with only case, accents and punctuation normalised.

    Keeps the legal form and trade words that `match_key` strips: they are
    noise when reaching across two spellings of one firm, and the entire signal
    when telling two firms of the same family apart.
    """
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(ch)
    )
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", folded).lower().split())


def crawl(out: Path, *, pace: float = 1.2, limit: int = 0, log=print) -> int:
    """Fetch every agency profile, resumably.

    Resumable for the reason the other directory crawl is: a national walk that
    has to restart from nothing on any interruption will never finish, and the
    cost of a stall is then the whole run rather than one page.
    """
    settings = load_settings()
    seen: set[str] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.add(json.loads(line)["profile_url"])
        log(f"resuming: {len(seen)} profiles already held")

    written = empty = 0
    with out.open("a", encoding="utf-8") as sink, reach._client(settings) as client:
        index = reach._page(client, ic.SITEMAP)
        if not index:
            log("the agency sitemap did not answer")
            return 1
        urls = [u for u in ic.agency_urls(index) if u not in seen]
        if limit:
            urls = urls[:limit]
        log(f"{len(urls)} profiles to fetch")
        for number, url in enumerate(urls, 1):
            page = reach._page(client, url)
            agency = ic.agency_in(page, url) if page else None
            if agency is None:
                empty += 1
            else:
                sink.write(json.dumps({
                    "profile_url": agency.profile_url or url,
                    "slug": agency.slug, "name": agency.name,
                    "filed_name": agency.filed_name, "canton": agency.canton,
                    "town": agency.town, "postcode": agency.postcode,
                    "street": agency.street, "site": agency.site,
                    "emails": list(agency.emails),
                }, ensure_ascii=False) + "\n")
                written += 1
            sink.flush()
            if number % 25 == 0:
                log(f"  {number}/{len(urls)}: {written} held, {empty} carried nothing")
            time.sleep(pace)
    log(f"{written} agencies written to {out}, {empty} pages carried no agency")
    return 0


def match(path: Path, *, dry_run: bool, log=print) -> int:
    settings = load_settings()
    conn = db.open_db(settings.database_url)

    # Two keys, failing in opposite directions. `match_key` strips legal forms
    # and trade vocabulary so "Regie Naef SA" can meet "Naef Immobilier"; that
    # same stripping is what collides "Böni Immobilien AG" with "Böni Holding
    # AG", and what loses a register entry whose distinguishing word was
    # `Verwaltungs`. An exact name match needs no tiebreak, so it is tried
    # first and the loose key is the fallback.
    index: dict[tuple[str, str], list[dict]] = {}
    exact: dict[tuple[str, str], list[dict]] = {}
    for row in conn.execute(
        "SELECT id, uid, name, canton FROM core.company"
        " WHERE status = 'ACTIVE' AND legal_form_uid IN ('0106','0107','0108')"
    ):
        company = dict(row)
        canton = row["canton"] or ""
        key = im.match_key(row["name"])
        if key:
            index.setdefault((key, canton), []).append(company)
        exact.setdefault((_plain(row["name"]), canton), []).append(company)

    mx: dict[str, bool] = {}
    matched = ambiguous = unmatched = kept = refused = sited = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        # The registered name first, the trading name second. "abc geneve sarl"
        # under the logo is what the register holds; "ABC GENEVE Swiss Real
        # Estate" over the door is not.
        canton = record["canton"] or ""
        names = [n for n in (record.get("filed_name"), record.get("name")) if n]
        hits: list[dict] = []
        # Exact on either spelling before loose on either, so a firm whose
        # registered name is carried verbatim is never sent to a tiebreak.
        for candidate in names:
            found = exact.get((_plain(candidate), canton), [])
            if len(found) == 1:
                hits = found
                break
        if not hits:
            for candidate in names:
                key = im.match_key(candidate)
                if not key:
                    continue
                hits = index.get((key, canton), [])
                if hits:
                    break
        if not hits:
            unmatched += 1
            continue
        if len(hits) > 1:
            ambiguous += 1
            log(f"  ambiguous: {record['name']} -> "
                + ", ".join(h["name"] for h in hits[:3]))
            continue
        company = hits[0]

        good = [
            a for a in record["emails"]
            if mailcheck.shape_ok(a)
            and mailcheck.accepts_mail(mailcheck.domain_of(a), cache=mx)
        ]
        refused += len(record["emails"]) - len(good)
        site = (record.get("site") or "").strip()
        domain = reach._host(site) if site else None
        # Only addresses at the firm's own domain. A directory listing a
        # portal's contact address beside an agency is the same failure the
        # cascade guards against, and it looks identical here.
        ranked, others = reach.own_domain_only(reach.rank_addresses(good), domain)
        matched += 1

        if dry_run:
            log(f"  {(record['name'] or '')[:28]:30} -> {company['name'][:28]:30} "
                f"{domain or '(no site)':26} {len(ranked)} address(es)")
            continue

        if site:
            sited += 1
            db.note_site(conn, int(company["id"]), status="found", url=site,
                method="directory")
        for address in ranked:
            kept += db.save_contact(
                conn, company_id=int(company["id"]), kind="email", value=address,
                source_url=record["profile_url"] or None, method="published",
            )
        scout_store.save_address_hunt(conn, int(company["id"]), Hunt(
            outcome="search" if ranked else "partial",
            tier=2 if ranked else None,
            domain=domain, addresses=ranked,
            method="published" if ranked else "",
            pages=1, searches=0,
            tried=[{"what": a, "why": "not at the company's own domain"}
                   for a in others] or None,
            note="immobilier.ch agency directory"
            + ("" if ranked else "; the directory had none, the cascade is still owed"),
            where={a: record["profile_url"] or "" for a in ranked},
        ))
        conn.commit()

    log(f"\nmatched {matched}, ambiguous {ambiguous}, unmatched {unmatched}")
    log(f"{kept} new contact(s), {sited} website(s), {refused} address(es) refused")
    conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="action", required=True)
    c = sub.add_parser("crawl")
    c.add_argument("--out", required=True)
    c.add_argument("--pace", type=float, default=1.2)
    c.add_argument("--limit", type=int, default=0)
    m = sub.add_parser("match")
    m.add_argument("path")
    m.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.action == "crawl":
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        return crawl(out, pace=args.pace, limit=args.limit)
    return match(Path(args.path), dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
