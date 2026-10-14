"""Walk the immobiliensektor.ch trade directory and match it to the register.

    uv run python scripts/immobiliensektor_directory.py crawl --out data/isektor.jsonl
    uv run python scripts/immobiliensektor_directory.py match data/isektor.jsonl --dry-run
    uv run python scripts/immobiliensektor_directory.py match data/isektor.jsonl

**Cheapest first, and this is what that means in practice.** The paid search
tier costs a model call per company whether or not it finds anything: on one
national run, 6,438 calls produced 765 searches worth having. This directory
answers the same question — what is this firm's website — for the price of a
fetch, and it covers the German-speaking cantons that the French-first portal
does not.

It publishes no email addresses, which sounds like the wrong half and is not.
The website is what the cascade lacks; with `intel.site` filled in, tier 1
reads the firm's own contact page for free. The order is crawl, then read, then
buy a search only for what is still missing.

**Two fetches per firm**, because the listing carries the address but the
website is on the detail page. 110 listing pages plus about 1,091 detail pages.

**What is written.** `intel.site` only. No contact rows: this source sees no
addresses, and a hunt row claiming otherwise would be a lie about provenance.
A firm whose site is newly known becomes selectable by tier 1 on the next
cascade run, which is where its address will actually come from.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import re  # noqa: E402
import unicodedata  # noqa: E402

from arpenteur import db  # noqa: E402
from arpenteur.config import load_settings  # noqa: E402
from arpenteur.sources import immobiliensektor as isk  # noqa: E402
from arpenteur.sources import immoscout as im  # noqa: E402
from arpenteur.stages import reach  # noqa: E402

# "Altrimo AG, Frauenfeld" — the directory disambiguates its own listings with
# a town the register never puts in a company name.
TRAILING_TOWN = re.compile(r",\s*[A-ZÄÖÜÀ-Þ][\w.'’ -]{2,24}$")


def _plain(text: str) -> str:
    """A name with only case, accents and punctuation normalised.

    Deliberately keeps the legal form and the trade words that `match_key`
    strips. Those words are noise when reaching across two spellings of one
    firm and they are the entire signal when telling "Böni Immobilien AG" from
    "Böni Holding AG", so the two keys are complementary rather than rival.
    """
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(ch)
    )
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", folded).lower().split())


def crawl(out: Path, *, pace: float = 1.2, limit: int = 0, log=print) -> int:
    """Listing pages, then a detail page each, resumably."""
    settings = load_settings()
    seen: set[str] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.add(json.loads(line)["key"])
        log(f"resuming: {len(seen)} firms already held")

    written = nosite = 0
    with out.open("a", encoding="utf-8") as sink, reach._client(settings) as client:
        first = reach._page(client, isk.listing_url(1))
        if not first:
            log("the directory did not answer")
            return 1
        total = isk.total_firms(first) or 0
        pages = max(1, -(-int(total) // isk.PER_PAGE))
        log(f"{total} firms over {pages} listing pages")

        for number in range(1, pages + 1):
            page = first if number == 1 else reach._page(client, isk.listing_url(number))
            if not page:
                log(f"  listing {number}: no answer")
                continue
            listed = isk.firms_on(page)
            if not listed:
                log(f"  listing {number}: empty, stopping")
                break
            for firm in listed:
                if firm.key in seen:
                    continue
                seen.add(firm.key)
                time.sleep(pace)
                detail = reach._page(client, isk.detail_url(firm.key, firm.slug))
                site = isk.website_on(detail) if detail else ""
                if not site:
                    nosite += 1
                sink.write(json.dumps({
                    "key": firm.key, "name": firm.name,
                    "postcode": firm.postcode, "town": firm.town,
                    "site": site,
                    "source_url": isk.detail_url(firm.key, firm.slug),
                }, ensure_ascii=False) + "\n")
                written += 1
                if limit and written >= limit:
                    log(f"stopped at --limit {limit}")
                    return 0
            sink.flush()
            log(f"  listing {number}/{pages}: {written} held, {nosite} with no website")
            time.sleep(pace)
    log(f"{written} firms written to {out}, {nosite} published no website")
    return 0


def match(path: Path, *, dry_run: bool, log=print) -> int:
    settings = load_settings()
    conn = db.open_db(settings.database_url)

    # Two keys per company, because one is not enough and they fail in
    # opposite directions.
    #
    # `match_key` strips legal forms and the trade vocabulary, which is what
    # lets "Regie Naef SA" meet "Naef Immobilier". The same stripping is why
    # "Aare Immobilien-Treuhand AG" collides with "Aare Treuhand AG", "Aare
    # Group AG" and "Aare AG": everything distinguishing them was vocabulary.
    # Meanwhile "Alpin Verwaltungs AG" loses `verwaltungs` and misses a
    # register entry it would have matched on the full name.
    #
    # So the loose key stays for reach, and an exact key — the name with only
    # punctuation and case normalised — is tried first. An exact hit needs no
    # tiebreak and no town: it is the same name.
    index: dict[str, list[dict]] = {}
    exact: dict[str, list[dict]] = {}
    for row in conn.execute(
        "SELECT id, uid, name, canton, legal_seat FROM core.company"
        " WHERE status = 'ACTIVE' AND legal_form_uid IN ('0106','0107','0108')"
    ):
        company = dict(row)
        key = im.match_key(row["name"])
        if key:
            index.setdefault(key, []).append(company)
        exact.setdefault(_plain(row["name"]), []).append(company)

    matched = ambiguous = unmatched = sited = nosite = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        site = (record.get("site") or "").strip()
        if not site:
            nosite += 1
            continue
        # The directory writes a trailing town on some names — "Altrimo AG,
        # Frauenfeld" — which no register entry carries.
        listed = TRAILING_TOWN.sub("", record["name"]).strip()
        hits = exact.get(_plain(listed), [])
        how = "exact"
        if len(hits) != 1:
            loose = index.get(im.match_key(listed), [])
            if loose:
                hits, how = loose, "loose"
        if not hits:
            unmatched += 1
            continue
        if len(hits) > 1:
            # Same key, more than one company. The town is the tiebreak the
            # directory actually gives us, and `legal_seat` is the same
            # commune, so it is a real discriminator rather than a guess.
            town = _plain(record.get("town") or "").split()
            narrowed = [
                h for h in hits
                if town and town[0] and town[0] in _plain(h["legal_seat"] or "")
            ]
            if len(narrowed) != 1:
                ambiguous += 1
                log(f"  ambiguous ({how}): {record['name']} -> "
                    + ", ".join(h["name"] for h in hits[:3]))
                continue
            hits = narrowed
        company = hits[0]

        if dry_run:
            log(f"  {record['name'][:30]:32} -> {company['name'][:30]:32} "
                f"{reach._host(site)}")
            matched += 1
            continue

        db.note_site(conn, int(company["id"]), status="found", url=site,
            method="directory")
        conn.commit()
        matched += 1
        sited += 1

    log(f"\nmatched {matched}, ambiguous {ambiguous}, unmatched {unmatched}")
    log(f"{sited} website(s) recorded, {nosite} record(s) had none to give")
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
