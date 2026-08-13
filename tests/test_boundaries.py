"""Cantonal outlines: fetching them, checking them, and what they make askable.

The survey has always been scoped to a set of cantons and has always decided
what was inside it by comparing a text column, which is what the register
happens to say rather than where a company actually is. Holding the boundary
turns that into a geometric question.

What the tests below are mostly about is the failure that would be invisible.
A polygon filed under the wrong canton draws a map that looks entirely
plausible, and every filter built on it is quietly wrong; nothing about a
multipolygon is inspectable by eye. So the abbreviation in the reply is checked
against the one asked for, and a mismatch raises rather than warns.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.modules.geo import store as geo
from arpenteur.sources import swissboundaries as boundaries
from arpenteur.stages import boundaries as stage

FIXTURES = Path(__file__).parent / "fixtures"


def _reply(name: str = "swissboundaries-ge.json") -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeClient:
    """Stands in for `PoliteClient`. Returns parsed JSON, as the real one does."""

    def __init__(self, payload: dict | None = None, status: int = 200) -> None:
        self.payload, self.status = payload if payload is not None else _reply(), status
        self.asked: list[str] = []

    def get(self, url: str, **_kw: object) -> SimpleNamespace:
        self.asked.append(url)
        return SimpleNamespace(status=self.status, body=self.payload, url=url,
                               from_cache=False)

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


# --- reading the reply ------------------------------------------------------------


def test_a_canton_is_read_with_its_code_name_and_area():
    region = boundaries.parse(_reply(), expect="GE")
    assert region.code == "GE"
    assert region.name == "Genève"
    # The layer reports hectares; Genève is 282 km2.
    assert region.area_km2 == pytest.approx(282.5, abs=1.0)
    assert json.loads(region.geojson)["type"] == "MultiPolygon"


def test_a_reply_for_the_wrong_canton_is_refused():
    """The failure this whole check exists for. Nothing about a polygon is
    inspectable by eye, so an outline filed under the wrong name draws a map
    that looks right and makes every filter built on it wrong."""
    with pytest.raises(boundaries.BoundaryError, match="returned GE"):
        boundaries.parse(_reply(), expect="VD")


def test_a_reply_with_no_geometry_is_refused():
    payload = _reply()
    payload["feature"].pop("geometry")
    with pytest.raises(boundaries.BoundaryError, match="no geometry"):
        boundaries.parse(payload)


def test_a_line_is_not_an_outline():
    """The layer without `.fill` returns the border rather than the area, and
    a line cannot answer whether a point is inside."""
    payload = _reply()
    payload["feature"]["geometry"] = {"type": "LineString", "coordinates": []}
    with pytest.raises(boundaries.BoundaryError, match="not an"):
        boundaries.parse(payload)


def test_the_canton_numbers_are_the_federal_ones():
    """Published constants, and the reason a survey of six cantons makes six
    requests instead of twenty-six."""
    assert boundaries.CANTON_NUMBERS["ZH"] == 1
    assert boundaries.CANTON_NUMBERS["VD"] == 22
    assert boundaries.CANTON_NUMBERS["GE"] == 25
    assert len(boundaries.CANTON_NUMBERS) == 26


def test_something_that_is_not_a_canton_says_what_is():
    with pytest.raises(boundaries.BoundaryError, match="not a Swiss canton"):
        boundaries.fetch(FakeClient(), "Romandie")


def test_a_refused_request_stores_nothing():
    """A canton with no outline is better than a canton with a guess at one."""
    with pytest.raises(boundaries.BoundaryError, match="answered 503"):
        boundaries.fetch(FakeClient(status=503), "GE")


def test_the_url_names_the_fill_layer_and_asks_for_wgs84():
    url = boundaries.source_url(25)
    assert "kanton-flaeche.fill/25" in url
    assert "sr=4326" in url and "geometryFormat=geojson" in url


# --- storing them -------------------------------------------------------------------


def test_an_outline_is_stored_as_a_valid_multipolygon(db_path):
    conn = db.open_db(db_path)
    region = boundaries.parse(_reply())
    geo.save_region(conn, code=region.code, name=region.name, kind="canton",
                    geojson=region.geojson, source_url=region.source_url)
    row = conn.execute(
        "SELECT code, ST_IsValid(shape) AS valid,"
        "       ST_GeometryType(shape) AS kind FROM geo.region"
    ).fetchone()
    assert row["code"] == "GE"
    assert row["valid"] is True
    # Forced to MultiPolygon whether the source drew one ring or twelve.
    assert row["kind"] == "ST_MultiPolygon"
    conn.close()


def test_taking_a_boundary_again_replaces_it_rather_than_duplicating(db_path):
    conn = db.open_db(db_path)
    region = boundaries.parse(_reply())
    for _ in range(2):
        geo.save_region(conn, code=region.code, name=region.name, kind="canton",
                        geojson=region.geojson)
    assert len(geo.regions(conn, "canton")) == 1
    conn.close()


def test_the_stage_skips_what_is_already_held(db_path, build_settings, tmp_path,
                                              monkeypatch):
    conn = db.open_db(db_path)
    settings = build_settings(tmp_path)
    client = FakeClient()
    monkeypatch.setattr(stage, "client_for", lambda _s: client)

    first = stage.run(settings, conn, cantons=["GE"], log=lambda _m: None)
    assert first["stored"] == 1
    second = stage.run(settings, conn, cantons=["GE"], log=lambda _m: None)
    assert (second["stored"], second["held"]) == (0, 1)
    # And no second request was made for something already on disk.
    assert len(client.asked) == 1
    conn.close()


def test_refresh_takes_it_again(db_path, build_settings, tmp_path, monkeypatch):
    conn = db.open_db(db_path)
    settings = build_settings(tmp_path)
    monkeypatch.setattr(stage, "client_for", lambda _s: FakeClient())
    stage.run(settings, conn, cantons=["GE"], log=lambda _m: None)
    again = stage.run(settings, conn, cantons=["GE"], refresh=True,
                      log=lambda _m: None)
    assert again["stored"] == 1
    conn.close()


def test_a_dry_run_writes_nothing(db_path, build_settings, tmp_path, monkeypatch):
    conn = db.open_db(db_path)
    settings = build_settings(tmp_path)
    monkeypatch.setattr(stage, "client_for", lambda _s: FakeClient())
    stage.run(settings, conn, cantons=["GE"], dry_run=True, log=lambda _m: None)
    assert geo.regions(conn, "canton") == []
    conn.close()


def test_one_canton_failing_does_not_stop_the_rest(db_path, build_settings,
                                                   tmp_path, monkeypatch):
    """A run over six cantons that abandons everything because the fourth
    timed out is a run somebody has to work out how to resume."""
    conn = db.open_db(db_path)
    settings = build_settings(tmp_path)

    class Flaky(FakeClient):
        def get(self, url: str, **kw: object):
            if "/22?" in url:  # Vaud
                return SimpleNamespace(status=500, body={}, url=url,
                                       from_cache=False)
            return SimpleNamespace(status=200, body=_reply(), url=url,
                                   from_cache=False)

    monkeypatch.setattr(stage, "client_for", lambda _s: Flaky())
    stats = stage.run(settings, conn, cantons=["VD", "GE"], log=lambda _m: None)
    assert stats["failed"] == 1 and stats["stored"] == 1
    conn.close()


# --- what they make askable ----------------------------------------------------------


def _ge(conn: Connection) -> None:
    """Genève's real outline, as the fixture holds it.

    Fine for anything about parsing and storing, and useless for containment:
    the fixture's geometry is trimmed to ten points so the file stays readable,
    which is a shape that shares a name with the canton and not much else. The
    containment tests use `_box` instead.
    """
    region = boundaries.parse(_reply())
    geo.save_region(conn, code=region.code, name=region.name, kind="canton",
                    geojson=region.geojson)


def _box(conn: Connection, code: str, *, west: float, south: float,
         east: float, north: float) -> None:
    """A rectangle standing in for a canton.

    Containment is a property of PostGIS and of the query, not of Genève's
    coastline, and a test that depends on the real outline fails for reasons
    that have nothing to do with what it is checking.
    """
    geo.save_region(conn, code=code, name=code, kind="canton", geojson=json.dumps({
        "type": "Polygon",
        "coordinates": [[[west, south], [east, south], [east, north],
                         [west, north], [west, south]]],
    }))


def test_a_point_can_be_asked_which_canton_it_is_in(db_path):
    """The question the outlines were fetched for, and the one a text column
    was answering by proxy."""
    conn = db.open_db(db_path)
    _box(conn, "GE", west=5.95, south=46.13, east=6.32, north=46.37)
    inside = geo.region_of(conn, 46.2044, 6.1432)      # Geneva
    outside = geo.region_of(conn, 47.3769, 8.5417)     # Zurich
    assert inside["code"] == "GE"
    assert outside is None
    conn.close()


def test_the_outlines_are_simplified_for_drawing_and_not_in_the_column(db_path):
    """Stored at full fidelity because containment wants it; simplified at the
    read because a line three hundred pixels long does not."""
    conn = db.open_db(db_path)
    _ge(conn)
    stored = conn.execute(
        "SELECT ST_NPoints(shape) AS n FROM geo.region"
    ).fetchone()["n"]
    drawn = geo.region_outlines(conn, tolerance=0.05)
    points = sum(
        len(ring)
        for feature in drawn["features"]
        for polygon in feature["geometry"]["coordinates"]
        for ring in polygon
    )
    assert drawn["type"] == "FeatureCollection"
    assert drawn["features"][0]["properties"]["code"] == "GE"
    assert points < stored
    # The column is untouched by the read.
    assert conn.execute(
        "SELECT ST_NPoints(shape) AS n FROM geo.region"
    ).fetchone()["n"] == stored
    conn.close()


def test_no_boundaries_held_is_an_empty_collection_not_a_failure(db_path):
    conn = db.open_db(db_path)
    assert geo.region_outlines(conn) == {"type": "FeatureCollection", "features": []}
    conn.close()


def test_a_seat_outside_its_register_canton_is_reported(db_path):
    """Each row is one of three things and all three are worth knowing: a
    geocoder that missed, a stale register entry, or a company that moved and
    told the register but not its website."""
    conn = db.open_db(db_path)
    _box(conn, "GE", west=5.95, south=46.13, east=6.32, north=46.37)
    inside = db.upsert_company(conn, {"uid": "CHE1", "name": "In Geneva",
                                      "canton": "GE"})
    displaced = db.upsert_company(conn, {"uid": "CHE2", "name": "Says Vaud",
                                         "canton": "VD"})
    for company_id in (inside, displaced):
        establishment = geo.save_establishment(conn, company_id, is_seat=True)
        geo.place(conn, establishment, lat=46.2044, lon=6.1432,
                  precision="address", confidence=1.0, geocoder="test")

    found = geo.placed_outside_their_canton(conn)
    assert [row["name"] for row in found] == ["Says Vaud"]
    assert found[0]["register_canton"] == "VD" and found[0]["placed_in"] == "GE"
    conn.close()


# --- the map --------------------------------------------------------------------------


def test_the_map_serves_the_outlines_separately_from_the_points(db_path,
                                                                build_settings,
                                                                tmp_path):
    """So the browser draws markers without waiting for the boundary, and an
    absent boundary is a missing layer rather than a failed page."""
    from fastapi.testclient import TestClient

    from arpenteur.config import Territory
    from arpenteur.console.app import create_app

    conn = db.open_db(db_path)
    _ge(conn)
    conn.commit()
    conn.close()

    client = TestClient(create_app(build_settings(
        tmp_path,
        territory=Territory(cantons=("GE",), legal_forms=("0106",), name="test"),
    )))
    shapes = client.get("/map/regions.geojson")
    assert shapes.status_code == 200
    assert shapes.json()["features"][0]["properties"]["name"] == "Genève"

    page = client.get("/map").text
    assert "regions-fill" in page and "/map/regions.geojson" in page
    # swisstopo asks only that the source be named.
    assert "swissBOUNDARIES3D" in page


def test_the_map_says_when_no_boundary_is_held(db_path, build_settings, tmp_path):
    from fastapi.testclient import TestClient

    from arpenteur.config import Territory
    from arpenteur.console.app import create_app

    client = TestClient(create_app(build_settings(
        tmp_path,
        territory=Territory(cantons=("GE",), legal_forms=("0106",), name="test"),
    )))
    page = client.get("/map").text
    assert "No cantonal boundaries held" in page
    assert "regions-fill" not in page
