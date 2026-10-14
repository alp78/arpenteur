"""Map: establishments on swisstopo's base map, and the sites of a client.

Routes of the geo module, mounted when it is enabled. Moved out of
`app.py` verbatim: the bodies close over `ctx` where they closed over the
factory's locals, and read `ctx.settings` at request time so a saved
setting reaches the next request.
"""

from __future__ import annotations


from typing import Any
from urllib.parse import quote


from fastapi import Form, Request
from fastapi.responses import (
    HTMLResponse, JSONResponse, RedirectResponse,
)

from ... import attribution, db
from ...modules.geo import store as geo_store
from ...modules.scout import store as scout_store
from ...sources import geoadmin
from ...stages import (
    geocode,
)
from .. import map_page as map_view_page
from fastapi import APIRouter

from ...kernel.db import Connection
from ...kernel.modules import Contribution
from .. import ui
from ..context import Console


# --- what this module puts on other modules' pages ---------------------------


def _sites(conn: Connection, client_id: int, company_id: int) -> str:
    """A client's establishments, and the form that adds one."""
    return map_view_page.sites_panel(
        company_id, geo_store.establishments_for(conn, company_id), back=f"/client/{client_id}",
    )


CONTRIBUTIONS = (Contribution("client.panels", _sites, order=30),)


def router(ctx: Console) -> APIRouter:
    router = APIRouter()
    connect = ctx.connect
    at = ctx.at
    _back = ctx.back

    # Markers as a GeoJSON fetch rather than as rendered HTML: the map draws
    # them on the GPU, pans and zooms without asking again, and the same URL is
    # something an analyst can open with anything that reads GeoJSON.

    def _map_filters(args: Any) -> dict[str, str]:
        return {
            "canton": args.get("canton", ""), "segment": args.get("segment", ""),
            "sector": args.get("sector", ""),
            "band": args.get("band", ""), "state": args.get("state", ""),
        }

    @router.get("/map", response_class=HTMLResponse)
    def map_view(request: Request) -> HTMLResponse:
        conn = connect()
        try:
            chosen = _map_filters(request.query_params)
            counts = geo_store.counts(conn)
            shown = len(geo_store.points(conn, **chosen))
            return HTMLResponse(
                map_view_page.shell(
                    map_view_page.map_page(
                        count=shown,
                        placed=counts["placed"],
                        with_address=counts["with_address"],
                        cantons=scout_store.cantons_present(conn),
                        segments=sorted(ctx.settings.segments),
                        sectors=sorted(ctx.settings.sectors),
                        states=list(db.PROSPECT_STATES),
                        attribution_notice=attribution.notice(
                            ["zefix", "geoadmin", "swissboundaries"]
                        ),
                        regions=len(geo_store.regions(conn, "canton")),
                        **chosen,
                    ),
                    at("/map"),
                )
            )
        finally:
            conn.close()

    @router.get("/map/regions.geojson")
    def map_regions() -> Any:
        """The cantonal outlines, simplified for drawing.

        Separate from the points so the browser can start drawing markers
        without waiting for a megabyte of boundary, and so a boundary that has
        not been fetched is an absent layer rather than a failed page.
        """
        conn = connect()
        try:
            return geo_store.region_outlines(conn)
        finally:
            conn.close()

    @router.get("/map/points.geojson")
    def map_points(request: Request) -> JSONResponse:
        conn = connect()
        try:
            rows = geo_store.points(conn, **_map_filters(request.query_params))
            return JSONResponse(map_view_page.collection(rows))
        finally:
            conn.close()

    @router.post("/company/{company_id}/sites")
    def add_site(
        company_id: int,
        back: str = Form(""),
        name: str = Form(""),
        street: str = Form(""),
        house_number: str = Form(""),
        postal_code: str = Form(""),
        city: str = Form(""),
        kind: str = Form(""),
    ) -> RedirectResponse:
        """Record one of a client's sites, and place it.

        Typed by a person, because this is the half of the geography the
        register does not hold: it knows one registered address per legal
        entity, and a foundation running seven sites has seven. Geocoded
        inline: it is one request to a free service and finishes while the
        redirect is being written.
        """
        # Posted from a client's page, which says where to return; an
        # establishment belongs to a company, so the route is the company's.
        target = ui.local_path(back, "/clients")
        conn = connect()
        try:
            asked = {
                "street": street.strip() or None,
                "house_number": house_number.strip() or None,
                "postal_code": postal_code.strip() or None,
                "city": city.strip() or None,
            }
            address_id = geo_store.save_address(
                conn, {**asked, "query": geoadmin.one_line(asked), "method": "manual"}
            )
            site_id = geo_store.save_establishment(
                conn, company_id, address_id=address_id,
                name=name.strip() or None, kind=kind.strip() or None,
                method="manual",
            )
            note = ""
            try:
                with geocode.client_for(ctx.settings) as http:
                    found = geoadmin.geocode(http, asked)
                if found is None:
                    note = (
                        f"{name or 'The site'} was saved, but the address did not "
                        "resolve to a point. Check the street and the postcode; "
                        "nothing is placed on a guess."
                    )
                else:
                    geo_store.place(
                        conn, site_id, lat=found.lat, lon=found.lon,
                        precision=found.precision, confidence=found.confidence,
                        source_url=geoadmin.source_url(geoadmin.one_line(asked)),
                    )
            except Exception as exc:
                note = f"Saved, but the geocoder did not answer: {exc}"
            conn.commit()
            if note:
                return RedirectResponse(
                    f"{target}?error={quote(note)}#sites", status_code=303
                )
            saved = f"{name.strip() or 'The site'} was saved and placed on the map."
            return RedirectResponse(
                f"{target}?note={quote(saved)}#sites", status_code=303
            )
        finally:
            conn.close()

    @router.post("/establishment/{establishment_id}/delete")
    def remove_site(establishment_id: int, back: str = Form("")) -> RedirectResponse:
        conn = connect()
        try:
            geo_store.delete_establishment(conn, establishment_id)
            conn.commit()
        finally:
            conn.close()
        return _back(ui.local_path(back, "/clients"), note="The site was removed.")

    return router
