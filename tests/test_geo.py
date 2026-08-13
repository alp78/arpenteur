"""Geography: grading a geocode, storing a point, and drawing the map.

The load-bearing decision is in `sources/geoadmin.py` and it is not obvious:
the federal search calls everything it returns an address, including the
arbitrary building it picks for a bare postcode. Measured on 13.08.2026,
"1018 Lausanne" comes back as "Rue Traversière 1" with `origin: address`,
indistinguishable in the reply from a real match. Believing that field would
have put markers on strangers' doors and labelled them exact.

So precision is graded here, against what was asked, and most of this file is
about that grading. The rest is the two properties the schema exists for: an
establishment is a place rather than a company, and a point is separate from
the address it was made from.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import Territory
from arpenteur.console import map_page
from arpenteur.console.app import create_app
from arpenteur.kernel.db import Connection
from arpenteur.modules.geo import store as geo
from arpenteur.modules.scout import store as scout
from arpenteur.sources import geoadmin
from arpenteur.stages import geocode

FIXTURES = Path(__file__).parent / "fixtures"

BOIS_GENTIL = {
    "city": "Lausanne", "addon": "", "poBox": "", "careOf": "",
    "street": "Chemin du Petit-Flon", "houseNumber": "49",
    "organisation": "Fondation Bois-Gentil", "swissZipCode": "1018",
}


def payload(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Recorded:
    """A client that answers from a recorded payload and asks nobody."""

    def __init__(self, name: str, status: int = 200) -> None:
        self.body = payload(name)
        self.status = status
        self.asked: list[str] = []

    def get(self, url: str, **_: object):
        self.asked.append(url)
        return SimpleNamespace(status=self.status, body=self.body, url=url)


# --- reading an address -------------------------------------------------------


def test_a_register_address_becomes_the_line_the_service_expects() -> None:
    assert geoadmin.one_line(BOIS_GENTIL) == "Chemin du Petit-Flon 49, 1018 Lausanne"


def test_the_organisation_and_the_care_of_are_left_out() -> None:
    """Correct on an envelope, and they only ever confuse a gazetteer."""
    line = geoadmin.one_line({**BOIS_GENTIL, "careOf": "c/o Fiduciaire X"})
    assert "Fiduciaire" not in line and "Fondation" not in line


def test_both_spellings_of_an_address_are_read() -> None:
    """The register writes swissZipCode; a site typed on the client page
    arrives already in this schema's column names."""
    typed = {"street": "Place du Vallon", "house_number": "1",
             "postal_code": "1005", "city": "Lausanne"}
    assert geoadmin.parts(typed)["postal_code"] == "1005"
    assert geoadmin.parts(BOIS_GENTIL)["house_number"] == "49"


# --- grading what comes back --------------------------------------------------


def test_a_matching_address_is_graded_exact() -> None:
    place = geoadmin.parse(payload("geoadmin-petit-flon.json"))[0]
    graded = geoadmin.grade(place, geoadmin.parts(BOIS_GENTIL))
    assert graded is not None
    assert graded.precision == "address" and graded.confidence == 1.0
    assert round(graded.lat, 5) == 46.54238 and round(graded.lon, 5) == 6.62475


def test_a_postcode_answered_with_a_building_is_not_an_address() -> None:
    """The finding this module exists for.

    Asked for "1018 Lausanne", the service returns Rue Traversière 1 and calls
    it an address. It is a real building in the right postcode and has nothing
    to do with the question, and its `origin` field is identical to a true
    match's. Graded here against what was asked, it comes out as a town.
    """
    place = geoadmin.parse(payload("geoadmin-postcode-only.json"))[0]
    assert place.origin == "address"
    graded = geoadmin.grade(
        place, {"street": None, "house_number": None,
                "postal_code": "1018", "city": "Lausanne"}
    )
    assert graded is not None and graded.precision == "postcode"


def test_the_right_street_and_the_wrong_number_is_a_street() -> None:
    place = geoadmin.parse(payload("geoadmin-postcode-only.json"))[0]
    graded = geoadmin.grade(
        place, {"street": "Rue Traversière", "house_number": "7",
                "postal_code": "1018", "city": "Lausanne"}
    )
    assert graded is not None and graded.precision == "street"


def test_a_number_inside_another_number_is_not_a_match() -> None:
    """`4` is inside `1400` and inside the postcode. House numbers are
    compared as whole tokens for that reason."""
    place = geoadmin.parse(payload("geoadmin-postcode-only.json"))[0]
    graded = geoadmin.grade(
        place, {"street": "Rue Traversière", "house_number": "8",
                "postal_code": "1018", "city": "Lausanne"}
    )
    assert graded is not None and graded.precision == "street"


def test_the_wrong_postcode_is_refused_outright() -> None:
    """The search will happily return a street of the same name in another
    canton. A point in the wrong town is worse than no point, because on a map
    it looks exactly like a right one."""
    place = geoadmin.parse(payload("geoadmin-petit-flon.json"))[0]
    assert geoadmin.grade(
        place, {"street": "Chemin du Petit-Flon", "house_number": "49",
                "postal_code": "1200", "city": "Genève"}
    ) is None


def test_an_address_that_resolves_to_nothing_gets_no_point() -> None:
    client = Recorded("geoadmin-nothing.json")
    found = geoadmin.geocode(
        client, {"street": "Rue Inexistante", "house_number": "999",
                 "postal_code": "9999", "city": "Nulpart"}
    )
    assert found is None


def test_accents_and_case_do_not_decide_a_match() -> None:
    assert geoadmin.fold("Rue Henri-MUSSARD") == geoadmin.fold("rue henri mussard")
    assert geoadmin.fold("60 E") == geoadmin.fold("60e")


def test_an_empty_address_is_never_asked_about() -> None:
    client = Recorded("geoadmin-petit-flon.json")
    assert geoadmin.geocode(client, {}) is None
    assert client.asked == []


def test_a_company_with_no_domicile_is_not_a_failed_geocode() -> None:
    """The register writes it in the address field of a company that has none.
    38 of those held carry it, all winding up. It is a statement, not an
    address that happens to be hard, and reporting it as a failure would put
    the tool's inability where the register's own answer belongs."""
    assert not geoadmin.is_domiciled(
        {"street": "", "house_number": "", "postal_code": "",
         "city": "ohne Domizil-sans domicile"}
    )
    assert geoadmin.is_domiciled(geoadmin.parts(BOIS_GENTIL))


def test_no_domicile_costs_no_request(conn) -> None:
    scout.upsert_company(
        conn,
        {"uid": "CHE000000001", "name": "ABISS Sàrl en liquidation",
         "canton": "GE", "method": "zefix", "hydrated": True,
         "address": {"street": "", "houseNumber": "", "swissZipCode": "",
                     "city": "ohne Domizil-sans domicile"}},
    )
    conn.commit()
    company = dict(geo.waiting(conn, uids=["CHE000000001"])[0])
    client = Recorded("geoadmin-petit-flon.json")
    _id, found = geocode.place_one(client, conn, company)
    conn.commit()
    assert found is None and client.asked == []


# --- storing it ---------------------------------------------------------------


@pytest.fixture
def settings(db_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("VD",), legal_forms=("0110",), name="vaud"),
        segments={"ems": "A nursing home."},
        # The add-a-site route builds a real polite client before it geocodes,
        # so these have to be here. Without them the route caught its own
        # AttributeError, reported "the geocoder did not answer" and stored the
        # site with no point, which is a plausible enough outcome to have gone
        # unnoticed had the test not asserted the point.
        zefix=SimpleNamespace(contact="arpenteur tests"),
        cache_dir=None,
    )


@pytest.fixture
def conn(settings) -> Connection:
    connection = db.open_db(settings.database_url)
    scout.upsert_company(
        connection,
        {"uid": "CHE103895771", "name": "Fondation Bois-Gentil", "canton": "VD",
         "legal_seat": "Lausanne", "status": "ACTIVE", "method": "zefix",
         "hydrated": True, "address": BOIS_GENTIL},
    )
    connection.commit()
    return connection


@pytest.fixture
def client(settings, conn) -> TestClient:
    return TestClient(create_app(settings))


def test_a_company_has_no_location_and_its_establishments_do(conn) -> None:
    """One entity across seven sites is seven markers. A single point for the
    company would be wrong in six places, which is why this is a table."""
    address_id = geo.save_address(conn, geoadmin.parts(BOIS_GENTIL))
    seat = geo.save_establishment(
        conn, 1, address_id=address_id, name="Siège", is_seat=True
    )
    other = geo.save_establishment(conn, 1, name="EPSM Vallon")
    conn.commit()
    assert seat != other
    assert [e["name"] for e in geo.establishments_for(conn, 1)] == [
        "Siège", "EPSM Vallon"
    ]


def test_the_seat_is_not_added_twice_when_the_register_is_re_read(conn) -> None:
    address_id = geo.save_address(conn, geoadmin.parts(BOIS_GENTIL))
    first = geo.save_establishment(conn, 1, address_id=address_id, is_seat=True)
    second = geo.save_establishment(conn, 1, address_id=address_id, is_seat=True)
    conn.commit()
    assert first == second
    assert len(geo.establishments_for(conn, 1)) == 1


def test_one_address_is_stored_once_however_many_companies_share_it(conn) -> None:
    """Two companies at one street number are two establishments of one
    address, and geocoding it twice would be two requests for one answer."""
    first = geo.save_address(conn, geoadmin.parts(BOIS_GENTIL))
    second = geo.save_address(conn, geoadmin.parts(BOIS_GENTIL))
    conn.commit()
    assert first == second


def test_a_point_carries_how_much_to_believe_it(conn) -> None:
    site = geo.save_establishment(conn, 1, name="Siège", is_seat=True)
    geo.place(
        conn, site, lat=46.542381, lon=6.624754, precision="address",
        confidence=1.0, source_url="https://api3.geo.admin.ch/...",
    )
    conn.commit()
    row = geo.establishment(conn, site)
    assert round(row["lat"], 5) == 46.54238 and round(row["lon"], 5) == 6.62475
    assert row["precision"] == "address" and row["geocoder"] == "geoadmin"


def test_longitude_goes_first_and_lausanne_stays_in_switzerland(conn) -> None:
    """The one thing about ST_MakePoint that catches everybody. Swapping them
    puts Lausanne in the Indian Ocean and raises nothing at all."""
    site = geo.save_establishment(conn, 1, name="Siège", is_seat=True)
    geo.place(conn, site, lat=46.542381, lon=6.624754, precision="address",
              confidence=1.0)
    conn.commit()
    near = geo.within(conn, lat=46.5197, lon=6.6323, metres=5000)
    assert [row["site_name"] for row in near] == ["Siège"]
    assert 2000 < near[0]["metres"] < 4000


def test_a_wrong_point_can_be_taken_off_without_losing_the_site(conn) -> None:
    site = geo.save_establishment(conn, 1, name="Siège", is_seat=True)
    geo.place(conn, site, lat=46.5, lon=6.6, precision="postcode", confidence=0.4)
    conn.commit()
    geo.forget_point(conn, site)
    conn.commit()
    row = geo.establishment(conn, site)
    assert row is not None and row["lat"] is None and row["name"] == "Siège"


def test_a_radius_query_uses_metres_without_anyone_choosing_a_projection(conn) -> None:
    for name, lat, lon in (
        ("Lausanne", 46.5197, 6.6323),
        ("Morges", 46.5094, 6.4981),
        ("Zürich", 47.3769, 8.5417),
    ):
        site = geo.save_establishment(conn, 1, name=name)
        geo.place(conn, site, lat=lat, lon=lon, precision="address", confidence=1.0)
    conn.commit()
    near = geo.within(conn, lat=46.5197, lon=6.6323, metres=30_000)
    assert [row["site_name"] for row in near] == ["Lausanne", "Morges"]


# --- the stage ----------------------------------------------------------------


def test_the_stage_writes_the_address_even_when_it_cannot_place_it(conn) -> None:
    """An address we hold and could not place is a state worth seeing. The
    alternative is a table that quietly contains only the successes."""
    company = dict(geo.waiting(conn, uids=["CHE103895771"])[0])
    geocode.place_one(Recorded("geoadmin-nothing.json"), conn, company)
    conn.commit()
    held = geo.establishments_for(conn, 1)
    assert len(held) == 1
    assert held[0]["street"] == "Chemin du Petit-Flon" and held[0]["lat"] is None


def test_the_stage_places_what_it_can(conn) -> None:
    company = dict(geo.waiting(conn, uids=["CHE103895771"])[0])
    _id, found = geocode.place_one(Recorded("geoadmin-petit-flon.json"), conn, company)
    conn.commit()
    assert found is not None and found.precision == "address"
    assert geo.counts(conn) == {"with_address": 1, "placed": 1, "establishments": 1}


def test_a_company_already_placed_is_not_offered_again(conn) -> None:
    company = dict(geo.waiting(conn, uids=["CHE103895771"])[0])
    geocode.place_one(Recorded("geoadmin-petit-flon.json"), conn, company)
    conn.commit()
    assert geo.waiting(conn) == []


def test_the_stage_refuses_an_undeclared_sweep(conn, settings) -> None:
    """Free and public is a reason to be careful with a service, not a licence.
    Every stage that talks to somebody else's server asks for its size."""
    for n in range(geocode.POLITE_LIMIT + 1):
        scout.upsert_company(
            conn,
            {"uid": f"CHE{n:012d}", "name": f"Company {n}", "canton": "VD",
             "method": "zefix", "hydrated": True, "address": BOIS_GENTIL},
        )
    conn.commit()
    with pytest.raises(RuntimeError, match="confirm the size"):
        geocode.run(settings, conn, log=lambda _m: None)


# --- the map ------------------------------------------------------------------


def test_a_marker_says_whether_it_is_on_a_door_or_a_town(conn) -> None:
    exact = geo.save_establishment(conn, 1, name="Siège", is_seat=True)
    geo.place(conn, exact, lat=46.5423, lon=6.6247, precision="address", confidence=1.0)
    rough = geo.save_establishment(conn, 1, name="Quelque part")
    geo.place(conn, rough, lat=46.52, lon=6.63, precision="postcode", confidence=0.4)
    conn.commit()
    marks = {
        f["properties"]["site"]: f["properties"]["exact"]
        for f in map_page.collection(geo.points(conn))["features"]
    }
    assert marks == {"Siège": True, "Quelque part": False}


def test_the_map_serves_its_markers_as_geojson(client, conn) -> None:
    site = geo.save_establishment(conn, 1, name="Siège", is_seat=True)
    geo.place(conn, site, lat=46.542381, lon=6.624754, precision="address",
              confidence=1.0)
    conn.commit()
    body = client.get("/map/points.geojson").json()
    assert body["type"] == "FeatureCollection"
    assert body["features"][0]["geometry"]["coordinates"] == [6.624754, 46.542381]
    assert body["features"][0]["properties"]["name"] == "Fondation Bois-Gentil"


def test_the_map_names_swisstopo(client, conn) -> None:
    """Both the base map and the geocoder are open data on the one condition
    that the source is stated."""
    page = client.get("/map").text
    assert "swisstopo" in page and "geo.admin.ch" in page


def test_an_empty_map_says_what_to_run(client) -> None:
    page = client.get("/map").text
    assert "Nothing is placed yet" in page and "/pipeline" in page


def test_a_client_site_is_typed_and_placed(client, conn, monkeypatch) -> None:
    """The half of the geography the register does not hold: it knows one
    address per legal entity, and a foundation running seven sites has seven."""
    monkeypatch.setattr(
        geoadmin, "geocode",
        lambda _client, asked, **_: geoadmin.grade(
            geoadmin.parse(payload("geoadmin-petit-flon.json"))[0], asked
        ),
    )
    client.post("/company/1/client")
    client.post(
        "/client/1/sites",
        data={"name": "EMS Petit-Flon", "street": "Chemin du Petit-Flon",
              "house_number": "49", "postal_code": "1018", "city": "Lausanne"},
    )
    sites = geo.establishments_for(conn, 1)
    assert [s["name"] for s in sites] == ["EMS Petit-Flon"]
    assert sites[0]["lat"] is not None
    assert "EMS Petit-Flon" in client.get("/client/1").text


def test_removing_a_site_leaves_the_client(client, conn) -> None:
    client.post("/company/1/client")
    site = geo.save_establishment(conn, 1, name="Studios Châtelard")
    conn.commit()
    reply = client.post(f"/establishment/{site}/delete", follow_redirects=False)
    assert reply.status_code == 303
    assert geo.establishments_for(conn, 1) == []
    assert client.get("/client/1").status_code == 200
