"""The boundaries stage: put the cantonal outlines in the database.

Named for what it fetches rather than for the table it fills. `geo.region`
holds them, but `arpenteur regions` already means "the territories named in
arpenteur.toml", and two commands a letter apart doing unrelated things is a
mistake somebody makes once and then distrusts both.

The last thing the map was missing. Markers on a blank tile answer "where is
this company"; an outline answers "is this company inside the territory I am
actually working", which is the question a survey scoped to six cantons is
read for, and it was being answered by a text comparison on a canton column
that nothing verified against geography.

Small and one-off by nature. Six cantons is six requests to a free federal
service, and boundaries change when a commune merges, which is a handful of
times a year and never on the outer cantonal ring. So there is no freshness
check, no schedule and no re-fetch: the row carries the date it was taken and
`--refresh` is how somebody asks for it again.

It defaults to the configured territory rather than to all twenty-six. A
survey of Romandie has no use for Appenzell's outline, and fetching it anyway
is twenty requests to somebody else's server for a shape nobody will draw.
"""

from __future__ import annotations

from typing import Any, Callable

from ..config import Settings
from ..http import PoliteClient
from ..kernel.db import Connection
from ..modules.geo import store as geo
from ..sources import swissboundaries


def client_for(settings: Settings) -> PoliteClient:
    """A polite client aimed at the federal boundary layer.

    No credentials: it is a public service. Cached on disk without expiry,
    because a cantonal outline fetched yesterday is the same outline today and
    re-asking would be several hundred kilobytes spent on a known answer.
    """
    return PoliteClient(
        contact=settings.zefix.contact,
        rate_limit=1.0,
        max_retries=2,
        cache_dir=settings.cache_dir,
        timeout=60.0,
    )


def run(
    settings: Settings,
    conn: Connection,
    *,
    cantons: list[str] | None = None,
    refresh: bool = False,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    wanted = [c.strip().upper() for c in (cantons or settings.cantons) if c.strip()]
    stats: dict[str, Any] = {
        "considered": len(wanted), "stored": 0, "held": 0, "failed": 0,
    }
    if not wanted:
        log(
            "No cantons named and none configured. Set survey.cantons in "
            "arpenteur.toml, or pass --canton."
        )
        return stats

    held = {row["code"] for row in geo.regions(conn, "canton")}
    todo = wanted if refresh else [c for c in wanted if c not in held]
    stats["held"] = len(wanted) - len(todo)

    if dry_run:
        log(f"{len(todo)} of {len(wanted)} canton(s) would be fetched:")
        for code in todo:
            log(f"  {code}  {swissboundaries.source_url(swissboundaries.CANTON_NUMBERS[code])}")
        if stats["held"]:
            log(f"  {stats['held']} already held; --refresh to take them again")
        return stats

    if not todo:
        log(f"All {len(wanted)} canton(s) already held. --refresh to take them again.")
        return stats

    with client_for(settings) as client:
        for code in todo:
            try:
                region = swissboundaries.fetch(client, code)
            except Exception as exc:
                stats["failed"] += 1
                log(f"  ! {code}  {exc}")
                continue
            geo.save_region(
                conn, code=region.code, name=region.name, kind="canton",
                geojson=region.geojson, source_url=region.source_url,
                method="swissboundaries3d",
            )
            conn.commit()
            stats["stored"] += 1
            # The area is reported because it is the only property of a
            # polygon a person can sanity-check at a glance. Vaud is about
            # 3'212 km2; an outline that says 300 is the wrong canton.
            area = f"{region.area_km2:,.0f} km2" if region.area_km2 else "area unknown"
            log(f"  + {region.code}  {region.name[:28]:30} {area}")

    return stats
