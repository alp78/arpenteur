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
from dataclasses import dataclass
from typing import Any, Iterator

from ..http import PoliteClient

# The alphabet a company name can start with. Digits included because
# "3D Concept Sàrl" and friends are real registrations.
ALPHABET = string.ascii_lowercase + string.digits


class ResultListTooLarge(RuntimeError):
    """The registry refused a search for matching too much.

    Measured on 11.08.2026: the largest response the API returned was 4874
    rows, and anything broader came back 400 RESULTLIST_TO_LARGE. There is no
    truncation and no paging, so the only way through a broad prefix is a
    narrower one.
    """

    def __init__(self, prefix: str) -> None:
        super().__init__(f"Zefix refused {prefix!r}: too many results")
        self.prefix = prefix


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


def _number(value: Any) -> float | None:
    """Capital arrives as a string, and occasionally as something unparseable.

    Returned as None rather than raising: a company with an odd capital field
    is still a company worth surveying, and the sweep must not die on one row.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("'", "").replace(" ", ""))
    except ValueError:
        return None


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
                    index[str(key).strip().lower()] = entry
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
        """Search one name prefix.

        Raises ResultListTooLarge when the registry refuses the query. It never
        answers a too-broad search with a truncated page, so a caller that
        treats the refusal as "no companies" would silently drop every company
        under that prefix. The sweep catches this and narrows instead.
        """
        query: dict[str, Any] = {"name": name, "activeOnly": active_only}
        if canton:
            query["canton"] = canton
        if legal_form_id:
            query["legalFormId"] = legal_form_id

        url = f"{self.base}/api/v1/company/search"
        reply = self.client.post(url, json_body=query)

        if isinstance(reply.body, dict):
            error = reply.body.get("error") or {}
            if error.get("type") == "RESULTLIST_TO_LARGE":
                raise ResultListTooLarge(name)
            raise RuntimeError(
                f"Zefix search {name!r} failed ({reply.status}): "
                f"{error.get('type') or reply.body}"
            )

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
            # capitalNominal arrives as a string ("20000"), not a number.
            capital=_number(body.get("capitalNominal")),
            capital_currency=body.get("capitalCurrency"),
            cantonal_excerpt_url=body.get("cantonalExcerptWeb"),
            # zefixDetailWeb is per-language, unlike cantonalExcerptWeb which
            # is a plain string. Both end up as one URL in the database.
            zefix_detail_url=_text(body.get("zefixDetailWeb")),
            source_url=url,
            hydrated=True,
        )


def walk_prefixes(
    search_fn: Any,
    *,
    skip: set[str] | None = None,
    root: str = "",
    max_depth: int = 6,
    log: Any = None,
) -> Iterator[tuple[str, list[Company], bool]]:
    """Walk name prefixes, narrowing wherever the registry refuses a query.

    Yields (prefix, companies, refused). A refused prefix returned no rows of
    its own: its children carry the data. It is yielded anyway, and recorded,
    so a resumed sweep can tell "asked and refused, children hold the answer"
    apart from "never asked".

    The registry answers a search either completely or not at all, so unlike a
    paged API there is no truncation to detect: a 200 is the whole set and the
    only failure mode is the refusal. That makes the sweep exact rather than
    best-effort, provided every refusal is narrowed until it is accepted.

    max_depth bounds a pathological branch. Reaching it means a six-character
    prefix still matches more than the registry will return, which would be
    surprising; it is surfaced through `log` rather than passed over, because
    silently abandoning the branch loses companies.
    """
    skip = skip or set()

    for prefix in [root + ch for ch in ALPHABET]:
        if prefix in skip:
            continue
        try:
            companies = search_fn(f"{prefix}*")
        except ResultListTooLarge:
            if len(prefix) >= max_depth:
                if log:
                    log(f"  ! {prefix}* still too large at depth {max_depth}, companies lost")
                yield prefix, [], True
                continue
            yield prefix, [], True
            yield from walk_prefixes(
                search_fn,
                skip=skip,
                root=prefix,
                max_depth=max_depth,
                log=log,
            )
        else:
            yield prefix, companies, False
