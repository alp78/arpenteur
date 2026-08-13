"""The Confederation's own address search, as a source.

`api3.geo.admin.ch/rest/services/api/SearchServer` resolves a Swiss address to
a coordinate. It is free, needs no key, and is authoritative for this country's
addresses, which is why it is here rather than a commercial geocoder: sending
every client's and prospect's address to a third party in order to learn what
swisstopo publishes would be a data-protection decision taken for no gain.

**The service always claims to have matched an address, and often has not.**
This is the finding that shaped the module, measured on 13.08.2026:

    "Chemin du Petit-Flon 49, 1018 Lausanne"  ->  origin=address,
                                                  Chemin du Petit-Flon 49
    "1018 Lausanne"                           ->  origin=address,
                                                  Rue Traversière 1

The second answer is a real building in the right postcode and has nothing to
do with the question. Its `origin` field says `address` exactly like the
first's, and its `rank` and `weight` order it against other candidates without
saying anything about how well it answers. Trusting either would have put a
marker on a stranger's door and labelled it exact.

So precision is not read from the reply. It is *graded* here, by comparing what
came back with what was asked: the postcode must agree or the answer is thrown
away, and the point is called an address only when the street and the house
number are in the label too. A marker on a town centre must never look like a
marker on a door.

No I/O in the parsing or the grading, so a real answer is recorded once as a
fixture and every later change is tested against it offline.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from ..http import PoliteClient

BASE_URL = "https://api3.geo.admin.ch/rest/services/api/SearchServer"

# What the service will search. Its request vocabulary is not its reply
# vocabulary: `street` is a valid value in neither direction and asking for it
# is a 400. Buildings, postcodes and communes are what an address can be.
ORIGINS = "address,zipcode,gg25"

# How well the answer matched the question, and how much of it to believe.
# Ordered, so a grade can be compared.
GRADES: tuple[tuple[str, float], ...] = (
    ("address", 1.0),   # street, number and postcode all agree
    ("street", 0.7),    # street and postcode agree, the number does not
    ("postcode", 0.4),  # only the postcode agrees: somewhere in the right town
)

_TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Place:
    """One candidate, with what it was and how well it answered."""

    lat: float
    lon: float
    label: str
    origin: str
    feature_id: str
    detail: str
    precision: str = "postcode"
    confidence: float = 0.4

    def graded(self, precision: str, confidence: float) -> Place:
        return Place(
            self.lat, self.lon, self.label, self.origin, self.feature_id,
            self.detail, precision, confidence,
        )


def source_url(text: str) -> str:
    """The request that produced a point, so a wrong one can be re-asked."""
    return f"{BASE_URL}?{urlencode({'searchText': text, 'type': 'locations'})}"


def fold(text: str | None) -> str:
    """Lowercase, unaccented, alphanumeric only.

    Comparison material, not display material. `Rue Henri-MUSSARD` and `rue
    henri mussard` are the same street written twice, and `60 E` and `60E` are
    the same house.
    """
    stripped = unicodedata.normalize("NFKD", str(text or ""))
    ascii_only = "".join(c for c in stripped if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", ascii_only.lower())


def one_line(address: dict[str, Any] | None) -> str:
    """A register address as the single line the search expects.

    Zefix gives the parts separately and the service wants them run together,
    the way they would be written on an envelope: street, number, postcode,
    town. `careOf`, `addon` and the organisation name are left out on purpose;
    they are correct on an envelope and only ever confuse a gazetteer.
    """
    if not address:
        return ""
    fields = parts(address)
    street = " ".join(
        part for part in (fields["street"], fields["house_number"]) if part
    )
    town = " ".join(
        part for part in (fields["postal_code"], fields["city"]) if part
    )
    return ", ".join(part for part in (street, town) if part)


def parts(address: dict[str, Any] | None) -> dict[str, str | None]:
    """A register address as this schema's columns, whichever spelling it uses.

    Zefix writes `swissZipCode` and `houseNumber`; a site entered by hand
    arrives already in the column names. Both are accepted here rather than
    converted twice at two call sites that can disagree.
    """
    address = address or {}

    def value(*keys: str) -> str | None:
        for key in keys:
            text = str(address.get(key) or "").strip()
            if text:
                return text
        return None

    return {
        "street": value("street"),
        "house_number": value("houseNumber", "house_number"),
        "po_box": value("poBox", "po_box"),
        "postal_code": value("swissZipCode", "postal_code"),
        "city": value("city"),
    }


# What the register writes in the address of a company that has none. It is a
# statement, not a place: 38 of the companies held carry it, all of them
# winding up. Recognised so the stage reports "no domicile" instead of "the
# address did not resolve", and spends no request finding that out.
NO_DOMICILE = ("sansdomicile", "ohnedomizil")


def is_domiciled(asked: dict[str, str | None]) -> bool:
    haystack = fold(" ".join(str(v) for v in asked.values() if v))
    return not any(marker in haystack for marker in NO_DOMICILE)


def parse(payload: Any) -> list[Place]:
    """The service's answer as places, in the order it gave them."""
    results = (payload or {}).get("results") if isinstance(payload, dict) else None
    out: list[Place] = []
    for entry in results or []:
        attrs = (entry or {}).get("attrs") or {}
        try:
            lat = float(attrs["lat"])
            lon = float(attrs["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(
            Place(
                lat=lat,
                lon=lon,
                label=_TAGS.sub("", str(attrs.get("label") or "")).strip(),
                origin=str(attrs.get("origin") or ""),
                feature_id=str(attrs.get("featureId") or ""),
                detail=str(attrs.get("detail") or ""),
            )
        )
    return out


def grade(place: Place, asked: dict[str, str | None]) -> Place | None:
    """How well one candidate answers the address that was asked for.

    None means it does not, and nothing should be stored. That happens when
    the postcode disagrees, which is the case worth refusing outright: the
    search will cheerfully return a street of the same name in another canton,
    and a point in the wrong town is worse than no point at all, because it
    looks exactly like a right one on a map.
    """
    haystack = fold(f"{place.label} {place.detail}")
    postcode = fold(asked.get("postal_code"))
    city = fold(asked.get("city"))
    # Either identifier for the place is enough; the register gives both and
    # some hand-entered addresses give only one.
    if postcode and postcode not in haystack:
        return None
    if not postcode and city and city not in haystack:
        return None

    street = fold(asked.get("street"))
    number = fold(asked.get("house_number"))
    if street and street in haystack:
        if number and _has_number(place.label, number):
            return place.graded(*GRADES[0])
        return place.graded(*GRADES[1])
    return place.graded(*GRADES[2])


def _has_number(label: str, number: str) -> bool:
    """Whether the label carries this house number, and not merely contains it.

    Folded substring matching is wrong here in a way it is not for a street
    name: `4` is inside `1400`, and the postcode is in the same string. Numbers
    are compared as whole tokens.
    """
    tokens = {fold(token) for token in re.split(r"[\s,]+", label) if token}
    return number in tokens


def geocode(
    client: PoliteClient,
    asked: dict[str, str | None],
    *,
    base_url: str = BASE_URL,
    limit: int = 8,
) -> Place | None:
    """The best place for one address, graded, or None when there is none.

    None rather than a fallback. An address that resolves to nothing is a fact
    about that address, and a point invented for it would be indistinguishable
    from a real one the moment it reached the table.
    """
    text = one_line(asked)
    if not text.strip():
        return None
    query = urlencode({
        "searchText": text,
        "type": "locations",
        # WGS84 out, because that is what the database column and every web
        # map want. The service defaults to the Swiss projection.
        "sr": "4326",
        "limit": str(limit),
        "origins": ORIGINS,
    })
    reply = client.get(f"{base_url}?{query}")
    if reply.status != 200:
        return None
    graded = [g for g in (grade(p, asked) for p in parse(reply.body)) if g]
    if not graded:
        return None
    # Best match, not first answer. The service orders by text relevance,
    # which can put "Place du Vallon 1a" above "Place du Vallon 1"; for
    # placing a marker the one that actually answers the question wins.
    graded.sort(key=lambda place: -place.confidence)
    return graded[0]
