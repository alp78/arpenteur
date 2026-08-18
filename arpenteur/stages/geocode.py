"""The geocode stage: turn the addresses we hold into points on a map.

It reads `core.company.address_json`, which the register supplies at
hydration, writes the address into `geo.address`, opens a seat establishment
for the company, and asks api3.geo.admin.ch where that is.

Three things decided here rather than left to the caller.

**It is narrow on purpose.** Measured on 12.08.2026, 3'690 of 184'939
companies carried an address at all, and none carried a point. Mapping the
whole survey would mean roughly 181'000 hydration calls to Zefix, which asks
users to renounce regular mass querying, and as many again to a free federal
service, in order to draw a map of companies nobody has decided to work on. So
the default is the shortlist: pass `--worked` for the companies on the board,
or a canton, a segment or a band. A run with no limit at all is refused, like
every other stage that talks to somebody else's server.

**A refused address is a result.** An address that resolves to nothing leaves
the establishment with no point and is counted. It is not retried on the next
run by accident and it is not filled in with the middle of the commune: see
`sources/geoadmin.py` for why an invented point is worse than none.

**Nothing is overwritten.** A point placed by hand, or corrected, is left
alone unless `--refresh` says otherwise. Same rule as a hand-set segment: what
a person decided outranks what a service returned.
"""

from __future__ import annotations

from typing import Any, Callable

from ..config import Settings
from ..http import PoliteClient
from ..kernel.db import Connection
from ..modules.geo import store as geo
from ..sources import geoadmin

# Companies per run without an explicit limit. The service is free and public,
# which is a reason to be careful with it rather than a licence: one request
# per company, one company per second, and a burst larger than this is
# something to ask for out loud.
POLITE_LIMIT = 500


def client_for(settings: Settings) -> PoliteClient:
    """A polite client aimed at the federal geocoder.

    No credentials: it is a public service. One request a second, and answers
    cached on disk without expiry, because an address that geocoded yesterday
    geocodes to the same point today and re-asking would be traffic spent on a
    known answer.
    """
    return PoliteClient(
        contact=settings.zefix.contact,
        rate_limit=1.0,
        max_retries=2,
        cache_dir=settings.cache_dir,
        timeout=20.0,
    )


def place_one(
    client: PoliteClient, conn: Connection, company: dict[str, Any]
) -> tuple[int, geoadmin.Place | None]:
    """Record one company's registered address and place it.

    Returns the establishment id and what the geocoder made of the address, or
    None. The row is written either way: an address we hold and could not
    place is a state worth being able to see, and the alternative is a table
    that quietly contains only the successes.
    """
    asked = geoadmin.parts(company["address_json"])
    address_id = geo.save_address(
        conn,
        {
            **asked,
            "canton": company.get("canton"),
            "query": geoadmin.one_line(asked),
            "method": "zefix",
        },
    )
    establishment_id = geo.save_establishment(
        conn, int(company["id"]), address_id=address_id, is_seat=True,
        method="zefix",
    )
    if not geoadmin.is_domiciled(asked):
        # The register's own way of saying there is nowhere to put a marker.
        # Asking anyway would be a request spent to learn what the address
        # already says.
        return establishment_id, None
    found = geoadmin.geocode(client, asked)
    if found is not None:
        geo.place(
            conn, establishment_id, lat=found.lat, lon=found.lon,
            precision=found.precision, confidence=found.confidence,
            geocoder="geoadmin",
            source_url=geoadmin.source_url(geoadmin.one_line(asked)),
        )
    return establishment_id, found


def run(
    settings: Settings,
    conn: Connection,
    *,
    uids: list[str] | None = None,
    canton: str = "",
    segment: str = "",
    sector: str = "",
    band: str = "",
    worked: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    companies = geo.waiting(
        conn, uids=uids, canton=canton, segment=segment, sector=sector,
        band=band, only_worked=worked, limit=limit or POLITE_LIMIT + 1,
    )
    stats: dict[str, Any] = {
        "considered": len(companies), "placed": 0, "unplaced": 0,
        "no_domicile": 0, "address": 0, "street": 0, "postcode": 0,
    }
    if not companies:
        log(
            "Nothing to geocode. Only companies whose detail has been fetched "
            "carry an address; run hydrate first, or widen the filter."
        )
        return stats

    if limit is None and not uids and len(companies) > POLITE_LIMIT:
        raise RuntimeError(
            f"{len(companies)} companies selected, one request each to a free "
            "public service. Pass a limit, a canton, a band, or --worked to "
            "confirm the size of the run."
        )

    if dry_run:
        log(f"{len(companies)} companies would be geocoded:")
        for company in companies[:20]:
            asked = geoadmin.parts(company["address_json"])
            log(f"  {company['uid']}  {company['name'][:34]:36} {geoadmin.one_line(asked)}")
        if len(companies) > 20:
            log(f"  ... and {len(companies) - 20} more")
        return stats

    with client_for(settings) as client:
        for index, company in enumerate(companies, 1):
            try:
                _id, found = place_one(client, conn, dict(company))
            except Exception as exc:
                stats["unplaced"] += 1
                log(f"  ! {company['name'][:34]:36} {exc}")
                continue
            conn.commit()
            if found is None:
                asked = geoadmin.parts(company["address_json"])
                if not geoadmin.is_domiciled(asked):
                    stats["no_domicile"] += 1
                    log(f"  . {company['name'][:34]:36} no domicile on the register")
                else:
                    stats["unplaced"] += 1
                    log(f"  - {company['name'][:34]:36} address did not resolve")
                continue
            stats["placed"] += 1
            stats[found.precision] += 1
            log(
                f"  + {company['name'][:34]:36} {found.lat:.5f},{found.lon:.5f} "
                f"{found.precision}"
            )
            if index % 50 == 0:
                log(f"  {index}/{len(companies)}")

    # The counts by precision are the useful part of this stage's report. "412
    # placed" says nothing about whether the map is worth looking at; "380 on a
    # door, 32 on a town" says exactly how much of it to believe.
    return stats
