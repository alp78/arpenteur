from __future__ import annotations

from typing import Any

from arpenteur.http import Response
from arpenteur.sources.zefix import ZefixClient, _text


class FakeHttp:
    """Stands in for PoliteClient: records calls, replays fixtures."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, str, Any]] = []

    def get(self, url: str, **_: Any) -> Response:
        self.calls.append(("GET", url, None))
        for key, body in self.replies.items():
            if key in url:
                return Response(200, body, url)
        return Response(404, None, url)

    def post(self, url: str, json_body: Any = None, **_: Any) -> Response:
        self.calls.append(("POST", url, json_body))
        return Response(200, self.replies.get("search", []), url)


def test_text_prefers_french(fixture) -> None:
    forms = fixture("zefix/legal_form.json")
    assert _text(forms[1]["name"]) == "Société anonyme"
    # Falls through to another language rather than returning nothing.
    assert _text({"de": "Nur Deutsch"}) == "Nur Deutsch"
    assert _text(None) is None


def test_legal_form_index_keys_on_code_and_short_name(fixture) -> None:
    http = FakeHttp({"legalForm": fixture("zefix/legal_form.json")})
    client = ZefixClient(http, "https://example.test/ZefixPublicREST")
    index = client.legal_form_index()

    assert index["sàrl"]["id"] == 9
    assert index["0108"]["id"] == 9
    assert index["sa"]["id"] == 4
    assert index["ri"]["id"] == 3


def test_search_maps_short_rows(fixture) -> None:
    http = FakeHttp({"search": fixture("zefix/search_a.json")})
    client = ZefixClient(http, "https://example.test/ZefixPublicREST")

    companies = client.search(name="a*", canton="VD", legal_form_id=9)

    assert [c.uid for c in companies] == ["CHE-100.000.001", "CHE-100.000.002"]
    assert companies[0].legal_form_label == "Société à responsabilité limitée"
    assert companies[0].legal_seat == "Lausanne"
    assert companies[0].hydrated is False

    _, _, body = http.calls[0]
    assert body == {"name": "a*", "activeOnly": True, "canton": "VD", "legalFormId": 9}


def test_company_hydrates_detail(fixture) -> None:
    http = FakeHttp({"company/uid": fixture("zefix/company_full.json")})
    client = ZefixClient(http, "https://example.test/ZefixPublicREST")

    company = client.company("CHE-100.000.001")

    assert company is not None
    assert company.canton == "VD"
    assert company.purpose.startswith("Menuiserie")
    assert company.capital == 20000
    assert company.address["city"] == "Lausanne"
    assert company.cantonal_excerpt_url.startswith("https://vd.chregister.ch/")
    assert company.hydrated is True


def test_company_handles_list_response(fixture) -> None:
    # A UID with branch entries returns a list; the head is the principal record.
    http = FakeHttp({"company/uid": [fixture("zefix/company_full.json")]})
    client = ZefixClient(http, "https://example.test/ZefixPublicREST")
    assert client.company("CHE-100.000.001").uid == "CHE-100.000.001"
