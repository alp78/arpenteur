"""Small recorded Zefix dataset for exercising the source stage offline.

The fixture client speaks the same narrow interface as ``ZefixClient`` but
never opens a network connection. Its provenance uses the ``fixture`` scheme,
so demo rows can never be mistaken for registry data.
"""

from __future__ import annotations

from typing import Any

from .zefix import Company

FIXTURE_BASE_URL = "fixture://zefix"
DEFAULT_CANTONS = ("VD",)
DEFAULT_LEGAL_FORMS = ("sarl",)

_FORM = {
    "id": 9,
    "uid": "0108",
    "name": "Société à responsabilité limitée",
    "short": "Sàrl",
}

_COMPANIES: dict[str, dict[str, Any]] = {
    "CHE-100.000.001": {
        "name": "Atelier Bornand Sàrl",
        "chid": "CH-550.1.000.001-3",
        "ehraid": 1234567,
        "legal_seat": "Lausanne",
        "legal_seat_id": 5586,
        "canton": "VD",
        "status": "EXISTIEREND",
        "purpose": (
            "Menuiserie, agencement d'intérieur et pose sur mesure; conception "
            "assistée par ordinateur et pilotage de machines à commande numérique."
        ),
        "address": {
            "organisation": "Atelier Bornand Sàrl",
            "street": "Chemin des Vignes",
            "houseNumber": "14",
            "city": "Lausanne",
            "swissZipCode": "1004",
        },
        "capital": 20000,
        "capital_currency": "CHF",
        "cantonal_excerpt_url": "https://vd.chregister.ch/fixture/CHE-100.000.001",
        "zefix_detail_url": "https://www.zefix.ch/fixture/CHE-100.000.001",
    },
    "CHE-100.000.002": {
        "name": "Aubert Fiduciaire Sàrl",
        "chid": "CH-550.1.000.002-1",
        "ehraid": 1234568,
        "legal_seat": "Vevey",
        "legal_seat_id": 5938,
        "canton": "VD",
        "status": "EXISTIEREND",
        "purpose": (
            "Exploitation d'une fiduciaire, tenue de comptabilités, conseils "
            "fiscaux et administratifs aux entreprises."
        ),
        "address": {
            "organisation": "Aubert Fiduciaire Sàrl",
            "street": "Rue du Simplon",
            "houseNumber": "8",
            "city": "Vevey",
            "swissZipCode": "1800",
        },
        "capital": 20000,
        "capital_currency": "CHF",
        "cantonal_excerpt_url": "https://vd.chregister.ch/fixture/CHE-100.000.002",
        "zefix_detail_url": "https://www.zefix.ch/fixture/CHE-100.000.002",
    },
}


class FixtureZefixClient:
    """Replay two recorded-style companies without credentials or HTTP."""

    def legal_form_index(self) -> dict[str, dict[str, Any]]:
        return {key: _FORM for key in ("0108", "sàrl", "sarl", "société à responsabilité limitée")}

    def search(
        self,
        *,
        name: str,
        canton: str | None = None,
        legal_form_id: int | None = None,
        active_only: bool = True,
    ) -> list[Company]:
        if name != "a*" or canton != "VD" or legal_form_id != _FORM["id"] or not active_only:
            return []
        return [self._company(uid, hydrated=False) for uid in _COMPANIES]

    def company(self, uid: str) -> Company | None:
        if uid not in _COMPANIES:
            return None
        return self._company(uid, hydrated=True)

    def _company(self, uid: str, *, hydrated: bool) -> Company:
        record = _COMPANIES[uid]
        return Company(
            uid=uid,
            name=record["name"],
            chid=record["chid"],
            ehraid=record["ehraid"],
            legal_form_uid=_FORM["uid"],
            legal_form_label=_FORM["name"],
            legal_seat=record["legal_seat"],
            legal_seat_id=record["legal_seat_id"],
            canton=record["canton"],
            status=record["status"],
            purpose=record["purpose"] if hydrated else None,
            address=record["address"] if hydrated else None,
            capital=record["capital"] if hydrated else None,
            capital_currency=record["capital_currency"] if hydrated else None,
            cantonal_excerpt_url=record["cantonal_excerpt_url"] if hydrated else None,
            zefix_detail_url=record["zefix_detail_url"] if hydrated else None,
            source_url=(
                f"{FIXTURE_BASE_URL}/api/v1/company/uid/{uid}"
                if hydrated
                else f"{FIXTURE_BASE_URL}/api/v1/company/search"
            ),
            hydrated=hydrated,
        )
