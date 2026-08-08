"""Zefix: the federal index of Swiss commercial register entries.

The authoritative list of who exists. Free, public, and the reason this tool
does not need to buy a scraped company database: for a Swiss SME the registry
holds the name, the seat, the legal form, the registered purpose and a link
to the cantonal excerpt.

API shape, from the published OpenAPI (v2.7.2.3):

    POST /api/v1/company/search   name (min 3 chars, * wildcard), canton,
                                  legalFormId, activeOnly -> CompanyShort[]
    GET  /api/v1/company/uid/{id} -> CompanyFull, with purpose and address
    GET  /api/v1/legalForm        -> the legal form catalogue
    GET  /api/v1/community        -> communes

Access is HTTP Basic. Credentials are free from zefix@bj.admin.ch.

The search takes a name prefix, not a "list everything" flag, so the sweep
below walks the alphabet and deepens wherever a prefix returns more rows than
the API will hand over at once.
"""

from __future__ import annotations

import string
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterator

from ..http import PoliteClient

# The alphabet a company name can start with. Digits included because
# "3D Concept Sàrl" and friends are real registrations.
ALPHABET = string.ascii_lowercase + string.digits


@dataclass
class Company:
    """One registry row, flattened to what the database stores."""

    uid: str
    name: str
    chid: str | None = None
    ehraid: int | None = None
    legal_form_uid: str | None = None
    legal_form_label: str | None = None
    legal_seat: str | None = None
    legal_seat_id: int | None = None
    canton: str | None = None
    status: str | None = None
    purpose: str | None = None
    address: dict[str, Any] | None = None
    capital: float | None = None
    capital_currency: str | None = None
    cantonal_excerpt_url: str | None = None
    zefix_detail_url: str | None = None
    source_url: str | None = None
    hydrated: bool = False

    def as_row(self) -> dict[str, Any]:
        row = self.__dict__.copy()
        row["method"] = "zefix"
        return row


def _text(value: Any, lang: str = "fr") -> str | None:
    """Zefix returns translated strings as {de, fr, it, en}.

    French first for a romand survey, then the others, so a form that exists
    only in German still gets a label instead of a null.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in (lang, "fr", "de", "it", "en"):
            if value.get(key):
                return str(value[key])
    return None


def _key(value: str) -> str:
    """Make catalogue labels safe to use from an ASCII configuration file."""
    normalized = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


class ZefixClient:
    def __init__(self, client: PoliteClient, base_url: str) -> None:
        self.client = client
        self.base = base_url.rstrip("/")

    # --- catalogues ---------------------------------------------------------

    def legal_forms(self) -> list[dict[str, Any]]:
        url = f"{self.base}/api/v1/legalForm"
        reply = self.client.get(url)
        return reply.body if isinstance(reply.body, list) else []

    def legal_form_index(self) -> dict[str, dict[str, Any]]:
        """Map short names and codes to catalogue entries.

        Resolved live rather than hardcoded so that a code change at the
        registry surfaces as "unknown legal form" instead of an empty sweep.
        """
        index: dict[str, dict[str, Any]] = {}
        for form in self.legal_forms():
            entry = {
                "id": form.get("id"),
                "uid": form.get("uid"),
                "name": _text(form.get("name")),
                "short": _text(form.get("shortName")),
            }
            for key in (entry["uid"], entry["short"], entry["name"]):
                if key:
                    label = str(key).strip().casefold()
                    index[label] = entry
                    index[_key(label)] = entry
        return index

    # --- search -------------------------------------------------------------

    def search(
        self,
        *,
        name: str,
        canton: str | None = None,
        legal_form_id: int | None = None,
        active_only: bool = True,
    ) -> list[Company]:
        query: dict[str, Any] = {"name": name, "activeOnly": active_only}
        if canton:
            query["canton"] = canton
        if legal_form_id:
            query["legalFormId"] = legal_form_id

        url = f"{self.base}/api/v1/company/search"
        reply = self.client.post(url, json_body=query)
        rows = reply.body if isinstance(reply.body, list) else []
        return [self._short(row, url) for row in rows]

    def _short(self, row: dict[str, Any], source_url: str) -> Company:
        return Company(
            uid=row.get("uid", ""),
            name=row.get("name", ""),
            chid=row.get("chid"),
            ehraid=row.get("ehraid"),
            legal_form_uid=_text(row.get("legalForm", {}).get("uid"))
            if isinstance(row.get("legalForm"), dict)
            else None,
            legal_form_label=_text(row.get("legalForm", {}).get("name"))
            if isinstance(row.get("legalForm"), dict)
            else None,
            legal_seat=row.get("legalSeat"),
            legal_seat_id=row.get("legalSeatId"),
            status=row.get("status"),
            source_url=source_url,
        )

    # --- detail -------------------------------------------------------------

    def company(self, uid: str) -> Company | None:
        """Hydrate one company: purpose, address, capital, excerpt link."""
        url = f"{self.base}/api/v1/company/uid/{uid}"
        reply = self.client.get(url)
        body = reply.body
        # The endpoint returns a list for a UID that maps to several entries
        # (a company and its branches); the head is the principal record.
        if isinstance(body, list):
            body = body[0] if body else None
        if not isinstance(body, dict):
            return None

        form = body.get("legalForm") if isinstance(body.get("legalForm"), dict) else {}
        return Company(
            uid=body.get("uid", uid),
            name=body.get("name", ""),
            chid=body.get("chid"),
            ehraid=body.get("ehraid"),
            legal_form_uid=_text(form.get("uid")),
            legal_form_label=_text(form.get("name")),
            legal_seat=body.get("legalSeat"),
            legal_seat_id=body.get("legalSeatId"),
            canton=body.get("canton"),
            status=body.get("status"),
            purpose=_text(body.get("purpose")),
            address=body.get("address"),
            capital=body.get("capitalNominal"),
            capital_currency=body.get("capitalCurrency"),
            cantonal_excerpt_url=body.get("cantonalExcerptWeb"),
            zefix_detail_url=body.get("zefixDetailWeb"),
            source_url=url,
            hydrated=True,
        )


def walk_prefixes(
    search_fn: Any,
    *,
    page_cap: int,
    skip: set[str] | None = None,
    root: str = "",
    max_depth: int = 4,
) -> Iterator[tuple[str, list[Company], bool]]:
    """Walk name prefixes, deepening where the API caps the response.

    Yields (prefix, companies, deepened). `deepened` marks a prefix whose own
    result was truncated: its rows are incomplete and its children carry the
    real data. Recorded that way so a resumed sweep knows not to trust it.

    The API needs at least three characters, so the first level asks for
    "a*", "b*" and so on and lets the server treat the star as the rest.
    max_depth stops a pathological branch (many companies starting "asso")
    from recursing forever; at depth 4 a truncated branch is accepted with a
    warning rather than spidered further.
    """
    skip = skip or set()
    frontier = [root + ch for ch in ALPHABET] if not root else [root + ch for ch in ALPHABET]

    for prefix in frontier:
        if prefix in skip:
            continue
        companies = search_fn(f"{prefix}*")
        capped = len(companies) >= page_cap
        if capped and len(prefix) < max_depth:
            yield prefix, companies, True
            yield from walk_prefixes(
                search_fn,
                page_cap=page_cap,
                skip=skip,
                root=prefix,
                max_depth=max_depth,
            )
        else:
            yield prefix, companies, False
