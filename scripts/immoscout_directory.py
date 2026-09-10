"""Walk the ImmoScout24 agency directory and match it to the register.

Two phases, for the same reason `source` and `hydrate` are two stages: the
crawl is the part that touches somebody else's server, and re-running a
matching rule must not repeat it.

    uv run python scripts/immoscout_directory.py crawl --out data/immoscout.jsonl
    uv run python scripts/immoscout_directory.py match data/immoscout.jsonl --dry-run
    uv run python scripts/immoscout_directory.py match data/immoscout.jsonl

**Why this source at all.** The register cannot be queried by activity, so
finding rental agencies in it costs a name-gated hydration of twenty thousand
companies and a purpose-text grading. This directory is already the list: 4,164
curated Swiss agencies across all 26 cantons, most publishing an address. It is
twenty agencies per request rather than one search per company.

**Politeness.** Around 210 requests at one per second, through the same client
every other stage uses. `robots.txt` permits the directory for `*` and names
`Claude-User` and `Claude-SearchBot` under an explicit `Allow: /`.

**Consent.** Each agent record carries `isPublic`. Addresses flagged private
are counted and discarded, never written. See `sources/immoscout.py`.

**What these addresses are.** Mostly named individuals at an agency, which is
personal data under the nLPD in a way `info@` is not. They are stored as
`published` because the agency published them, and role addresses are ranked
first so an export reaches a desk rather than a person by default.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arpenteur import db  # noqa: E402
from arpenteur.config import load_settings  # noqa: E402
from arpenteur.modules.scout import store as scout_store  # noqa: E402
from arpenteur.sources import immoscout as im  # noqa: E402
from arpenteur.sources import mailcheck  # noqa: E402
from arpenteur.stages import reach  # noqa: E402

PAGE_SIZE = 20


class Hunt:
    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def crawl(out: Path, *, start: int = 1, pace: float = 1.5, log=print) -> int:
    """One national walk, resumable, paced.

    The canton slug in a URL is decoration. Measured:
    `/agenturverzeichnis/kanton-appenzell-ir` reports
    `totalFilteredAgenciesCount` of 4,164 and returns agencies from eleven
    different cantons on its first page. The directory filters in the browser,
    not on the server, so asking per canton would fetch the whole country
    twenty-six times. Each record carries its own canton anyway.

    **Why it resumes and paces.** The directory answers about sixty pages at a
    request a second and then stops answering, holding the connection open
    rather than returning 429. That reads as a hang, not a refusal, and it
    happened twice at the same place. Appending to what is already held, and
    skipping ids already seen, makes a stall cost a re-run instead of the whole
    walk; `pace` keeps the burst under whatever the real threshold is.
    """
    settings = load_settings()
    seen: set[str] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.add(json.loads(line)["customer_id"])
        log(f"resuming: {len(seen)} agencies already held")
    written = 0
    with out.open("a", encoding="utf-8") as sink, reach._client(settings) as client:
        first = reach._page(client, im.index_url())
        if not first:
            log("the directory did not answer")
            return 1
        total = im.total_agencies(first) or 0
        pages = max(1, -(-int(total) // PAGE_SIZE))
        log(f"{total} agencies over {pages} pages, from page {start}")
        for number in range(start, pages + 1):
            page = first if number == 1 else reach._page(client, im.index_url("", number))
            if not page:
                log(f"  page {number}: no answer")
                continue
            found = im.agencies_in(page)
            if not found:
                log(f"  page {number}: empty, stopping")
                break
            for agency in found:
                if agency.customer_id not in seen:
                    seen.add(agency.customer_id)
                    sink.write(json.dumps({
                        "customer_id": agency.customer_id,
                        "name": agency.name,
                        "canton": agency.canton,
                        "town": agency.town,
                        "postcode": agency.postcode,
                        "url": agency.url,
                        "emails": list(agency.emails),
                        "withheld": agency.withheld,
                        "agents": list(agency.agents),
                    }, ensure_ascii=False) + "\n")
                    written += 1
            sink.flush()
            log(f"  page {number}/{pages}: +{written} new, {len(seen)} held")
            time.sleep(pace)
    log(f"{written} unique agencies written to {out}")
    return 0


def match(path: Path, *, dry_run: bool, log=print) -> int:
    settings = load_settings()
    conn = db.open_db(settings.database_url)

    # One pass over the register's own names, keyed the same way, so matching
    # is a dictionary lookup rather than 4,000 fuzzy queries.
    index: dict[tuple[str, str], list[dict]] = {}
    for row in conn.execute(
        "SELECT id, uid, name, canton, legal_seat FROM core.company"
        " WHERE status = 'ACTIVE' AND legal_form_uid IN ('0106','0107','0108')"
    ):
        key = im.match_key(row["name"])
        if key:
            index.setdefault((key, row["canton"] or ""), []).append(dict(row))

    mx: dict[str, bool] = {}
    matched = ambiguous = unmatched = kept = refused = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        key = im.match_key(record["name"])
        hits = index.get((key, record["canton"] or ""), [])
        if not hits:
            unmatched += 1
            continue
        if len(hits) > 1:
            # Two registered companies reduce to the same key in one canton.
            # Guessing which one the directory means would attach a real
            # address to possibly the wrong legal entity.
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
        ranked = reach.rank_addresses(good)
        matched += 1
        if dry_run:
            log(f"  {record['name'][:30]:32} -> {company['name'][:30]:32} "
                f"{len(ranked)} address(es)")
            continue
        for address in ranked:
            kept += db.save_contact(
                conn, company_id=int(company["id"]), kind="email", value=address,
                source_url=record["url"] or None, method="published",
            )
        # A directory that lists no public address for a firm has established
        # one thing: this directory has nothing. It has not established that
        # the firm publishes nothing, because no tier of the cascade ran. The
        # difference is not pedantry — `none` is the settled negative the
        # cascade skips on, so writing it here retires the firm from the search
        # before anyone has read its own website.
        scout_store.save_address_hunt(conn, int(company["id"]), Hunt(
            outcome="search" if ranked else "partial",
            tier=2 if ranked else None,
            domain=(mailcheck.domain_of(ranked[0]) if ranked else None),
            addresses=ranked, method="published" if ranked else "",
            pages=1, searches=0,
            tried=[{"what": f"{record['withheld']} address(es)",
                    "why": "the directory flags them private"}]
            if record["withheld"] else None,
            note="ImmoScout24 agency directory, public agent addresses only"
            + ("" if ranked else "; the directory had none, the cascade is still owed"),
            where={a: record["url"] or "" for a in ranked},
        ))
        conn.commit()

    log(f"\nmatched {matched}, ambiguous {ambiguous}, unmatched {unmatched}")
    log(f"{kept} new contact(s), {refused} address(es) refused")
    conn.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="action", required=True)
    c = sub.add_parser("crawl")
    c.add_argument("--out", required=True)
    c.add_argument("--start-page", type=int, default=1)
    c.add_argument("--pace", type=float, default=1.5,
                   help="seconds between pages; the directory tarpits a burst")
    m = sub.add_parser("match")
    m.add_argument("path")
    m.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.action == "crawl":
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        return crawl(out, start=args.start_page, pace=args.pace)
    return match(Path(args.path), dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
