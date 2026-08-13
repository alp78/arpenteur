"""Cantonal boundaries, from swisstopo's own swissBOUNDARIES3D.

The federal boundary layer, served through the same keyless API the geocoder
already uses. One request per canton, returning the outline as GeoJSON in
WGS84, which is the projection the column and the map both want.

Three things decided here.

**The canton is asked for by its federal number, and the answer is checked.**
The BFS numbering is a published standard, stable since 1979, and hardcoding
it saves fetching all twenty-six outlines to find the six a survey wants. What
makes that safe is that the reply carries the abbreviation, so a wrong
assumption is caught rather than silently filing Zürich's outline under Vaud.
A mismatch raises; it does not warn.

**The outline is stored as it arrives.** Vaud is 270 KB of coordinates and
simplifying before storing would be a decision taken once, at the wrong moment,
for every future use. Simplification belongs at the read, where the caller
knows whether it is drawing a map at 1:500'000 or asking whether a point falls
inside.

**It is a one-off.** Boundaries change when a commune merges, which is a
handful of times a year and never for the outer cantonal ring. There is no
freshness check and no re-fetch schedule: a boundary held is a boundary good
until somebody says otherwise, and the fetched date is on the row.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..http import PoliteClient

BASE_URL = "https://api3.geo.admin.ch/rest/services/api/MapServer"

# The cantonal outline layer. `.fill` is the polygon; the layer without it is
# the boundary line, which cannot answer whether a point is inside.
LAYER = "ch.swisstopo.swissboundaries3d-kanton-flaeche.fill"

# The federal canton numbers, which are the feature ids of that layer. A
# published constant of the Confederation rather than a guess: BFS numbering,
# unchanged since 1979, and the same ordering used on vehicle plates and in
# every federal statistic.
CANTON_NUMBERS: dict[str, int] = {
    "ZH": 1, "BE": 2, "LU": 3, "UR": 4, "SZ": 5, "OW": 6, "NW": 7, "GL": 8,
    "ZG": 9, "FR": 10, "SO": 11, "BS": 12, "BL": 13, "SH": 14, "AR": 15,
    "AI": 16, "SG": 17, "GR": 18, "AG": 19, "TG": 20, "TI": 21, "VD": 22,
    "VS": 23, "NE": 24, "GE": 25, "JU": 26,
}


class BoundaryError(RuntimeError):
    """Something that must not be filed as a canton's outline."""


@dataclass(frozen=True)
class Region:
    """One canton's outline, as the Confederation draws it."""

    code: str
    name: str
    geojson: str
    source_url: str
    # Square kilometres, as the layer reports it. Carried because it is the
    # cheapest possible check that an outline is the right one: a canton whose
    # area is out by an order of magnitude is a canton somebody has filed
    # wrongly, and nothing else about a polygon is inspectable by eye.
    area_km2: float | None = None


def source_url(number: int) -> str:
    return f"{BASE_URL}/{LAYER}/{number}?geometryFormat=geojson&sr=4326"


def parse(payload: dict[str, Any], *, expect: str = "") -> Region:
    """One feature into a Region, refusing anything that is not one.

    `expect` is the canton abbreviation that was asked for. When it is given
    and the reply disagrees, this raises: the failure it prevents is an outline
    stored under the wrong name, which is invisible on a map until somebody
    notices the shading is in the wrong place and by then the survey has been
    filtered by it.
    """
    feature = payload.get("feature") or payload
    properties = feature.get("properties") or {}
    code = str(properties.get("ak") or "").strip().upper()
    name = str(properties.get("name") or properties.get("label") or "").strip()
    geometry = feature.get("geometry")

    if not code or not geometry:
        raise BoundaryError(
            "the reply carries no canton abbreviation or no geometry, so there "
            "is nothing that could safely be stored"
        )
    if expect and code != expect.upper():
        raise BoundaryError(
            f"asked for {expect.upper()} and the layer returned {code}. The "
            "canton numbering in this file is wrong, and storing this would "
            f"file {code}'s outline under {expect.upper()}."
        )
    if geometry.get("type") not in ("Polygon", "MultiPolygon"):
        raise BoundaryError(
            f"{code} came back as {geometry.get('type')!r}, which is not an "
            "outline"
        )
    area = properties.get("flaeche")
    return Region(
        code=code,
        name=name or code,
        geojson=json.dumps(geometry),
        source_url=source_url(int(feature.get("featureId") or 0)),
        # The layer reports hectares.
        area_km2=round(float(area) / 100, 1) if area else None,
    )


def fetch(client: PoliteClient, canton: str) -> Region:
    """One canton's outline. Raises rather than returning a partial answer."""
    code = canton.strip().upper()
    number = CANTON_NUMBERS.get(code)
    if number is None:
        raise BoundaryError(
            f"{canton!r} is not a Swiss canton. Expected one of "
            + ", ".join(sorted(CANTON_NUMBERS))
        )
    url = source_url(number)
    reply = client.get(url)
    if reply.status != 200:
        raise BoundaryError(
            f"{code}: the boundary layer answered {reply.status}. Nothing is "
            "stored: a canton with no outline is better than a canton with a "
            "guess at one."
        )
    # `PoliteClient` parses JSON on the way out, so the body is already a
    # dict. Stated rather than assumed: a client that returned text here would
    # fail on the next line with a message about the geometry rather than
    # about the parsing.
    payload = reply.body
    if not isinstance(payload, dict):
        raise BoundaryError(f"{code}: the layer returned no usable reply")
    return parse(payload, expect=code)
