"""Load addresses found by an operator's own web search into the survey.

Tier 2 of the address cascade calls the metered Messages API, which needs an
API credit balance. A Claude subscription is a different ledger and does not
fund it, so on a machine with a subscription and no credits that tier is dead
and tiers 1, 3, 4 and 5 are all that run.

This is the way round it. An operator searching from a session that their own
plan pays for produces the same two facts the tier would have returned, a
website and the addresses on it, and this writes them in with the same
provenance discipline the stage uses.

The discipline is the reason this is a loader and not a paste. Each record
declares whether an address was **read off the company's own page** or
**constructed** from a directory listing or a naming pattern, and that decides
`core.contact.method`. A dataset that cannot tell a published address from a
plausible one has stopped being evidence, and the search tier is exactly where
that distinction is easiest to lose.

Input is JSONL, one company per line:

    {"uid": "CHE105851230", "domain": "bernard-nicod.ch",
     "emails": [{"address": "info@bernard-nicod.ch", "seen": "masked"}],
     "source_url": "https://www.local.ch/...", "note": "site blocks crawlers"}

`seen` is per address and is the only field that decides provenance:

    page      read unmasked off the company's own site   -> published
    directory read unmasked in a public directory        -> published
    masked    a directory showed it partly obscured      -> inferred
    guessed   constructed from the domain                -> inferred

`directory` counts as published because the company did publish the address;
a third-party listing merely reproduced it. What separates the two is not the
truth of the address but where it was seen, and `source_url` records that
exactly. `masked` is different in kind: a directory obscuring part of an
address means the local part was reconstructed, and a reconstruction is a
candidate however obvious it looks.

Every address is shape-checked and its domain is MX-checked before storage, so
a typo in a hand-built file cannot become a contact row.

    uv run python scripts/load_found_addresses.py found.jsonl --dry-run
    uv run python scripts/load_found_addresses.py found.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arpenteur import db  # noqa: E402
from arpenteur.config import load_settings  # noqa: E402
from arpenteur.modules.scout import store as scout_store  # noqa: E402
from arpenteur.sources import mailcheck  # noqa: E402
from arpenteur.stages import reach  # noqa: E402

# Seen-values that assert the company published the address. Anything else is a
# candidate, however plausible.
PUBLISHED = frozenset({"page", "directory"})


class Hunt:
    """The shape `save_address_hunt` expects, built from one search result."""

    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="JSONL of search results")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    settings = load_settings()
    conn = db.open_db(settings.database_url)
    mx: dict[str, bool] = {}
    loaded = kept = refused = missing = 0

    for line_no, line in enumerate(Path(args.path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        record = json.loads(line)
        uid = str(record.get("uid") or "").strip()
        try:
            company = scout_store.find_company(conn, uid)
        except ValueError:
            company = None
        if company is None:
            print(f"  line {line_no}: no company with uid {uid!r}", file=sys.stderr)
            missing += 1
            continue

        domain = (record.get("domain") or "").strip().lower().lstrip("@") or None
        good: list[str] = []
        method = "inferred"
        for item in record.get("emails") or []:
            address = str(item.get("address") or "").strip().lower()
            seen = str(item.get("seen") or "guessed").strip()
            if not mailcheck.shape_ok(address):
                print(f"  line {line_no}: {address!r} is not a usable address",
                      file=sys.stderr)
                refused += 1
                continue
            if not mailcheck.accepts_mail(mailcheck.domain_of(address), cache=mx):
                print(f"  line {line_no}: {address} domain takes no mail",
                      file=sys.stderr)
                refused += 1
                continue
            good.append(address)
            # One published address makes the record published; the rest are
            # still listed. Mixing methods on one row would need a column per
            # address, and the per-address `seen` stays in the notes instead.
            if seen in PUBLISHED:
                method = "published"

        ranked, others = reach.own_domain_only(reach.rank_addresses(good), domain)
        if others:
            print(f"  line {line_no}: {len(others)} address(es) not at {domain}, "
                  "set aside", file=sys.stderr)

        hunt = Hunt(
            # This loader stands in for tier 2 and for nothing else, so a
            # record that yielded no address settles that one tier. Calling it
            # `none` would claim the whole cascade came back empty, and would
            # take the company out of the selection that reads its own site.
            outcome="search" if ranked else "partial",
            tier=2 if ranked else None,
            domain=domain,
            addresses=ranked,
            method=method if ranked else "",
            pages=0,
            searches=1,
            tried=[{"what": a, "why": "not at the company's own domain"} for a in others]
            or None,
            note=(record.get("note") or "found by operator web search").strip(),
            where={a: record.get("source_url") or "" for a in ranked},
        )
        print(f"  {company['name'][:34]:36} {hunt.outcome:7} {method:9} "
              + (", ".join(ranked[:2]) or "-"))
        if args.dry_run:
            loaded += 1
            continue

        for address in ranked:
            kept += db.save_contact(
                conn, company_id=int(company["id"]), kind="email", value=address,
                source_url=record.get("source_url") or (f"https://{domain}" if domain else None),
                method=method,
            )
        scout_store.save_address_hunt(conn, int(company["id"]), hunt)
        conn.commit()
        loaded += 1

    print(f"\n{loaded} record(s) {'read' if args.dry_run else 'loaded'}, "
          f"{kept} new contact(s), {refused} address(es) refused, "
          f"{missing} unknown uid(s)")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
