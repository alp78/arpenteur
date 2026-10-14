"""Geo: where companies and clients actually are.

Owns `geo`: establishments with a point, the addresses they were geocoded
from, and territory polygons. Adds the geocode stage and the map.

Depends on the scout because an establishment belongs to a company. Removable:
dropping `geo` leaves every company and every client untouched, and the console
simply stops offering a map.

Geocoding goes through api3.geo.admin.ch, the Confederation's own search
service. Free, keyless, and authoritative for Swiss addresses, which matters
because a commercial geocoder would put client addresses through a third party
for no gain.
"""

from __future__ import annotations

from ...kernel.modules import Module, Nav

MODULE = Module(
    name="geo",
    title="Map",
    what=(
        "Places companies and clients on a map of Switzerland: establishments "
        "with coordinates from the federal geocoder, cantonal boundaries, and "
        "radius and territory queries."
    ),
    requires=("scout",),
    schemas=("geo",),
    nav=(Nav("Map", "/map", group="prospecting", order=140),),
)
