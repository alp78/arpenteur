"""The map: where everything placed actually is.

MapLibre GL from a CDN over swisstopo's own raster tiles, and the markers as
one GeoJSON fetch. No build step, no key, no tile bill, and no company address
sent to a third party in order to draw a dot: the base map and the geocoder are
both the Confederation's, published as open data on the single condition that
the source is named, which the page does at the bottom right.

Two things the drawing has to be honest about.

**Precision is visible.** A point graded `postcode` is somewhere in the right
town and nothing more, and it is drawn hollow so that it cannot be read as a
door. See `sources/geoadmin.py` for why the grade exists at all: the service
calls everything an address, including what it invents for a bare postcode.

**A marker is a place, not a company.** A foundation with seven sites has seven
markers, and clicking one names the site rather than only the entity. This is
why `geo.establishment` exists instead of two columns on `core.company`.
"""

from __future__ import annotations

import json
from typing import Any

from ..kernel.db import Row
from . import ui
from .render import esc, page

# swisstopo's raster tiles. `current` rather than a dated release, so the map
# does not quietly age; jpeg because it is a photograph-like base map and png
# would triple the bytes for no visible gain.
TILES = (
    "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-farbe/default/"
    "current/3857/{z}/{x}/{y}.jpeg"
)
ATTRIBUTION = '&copy; <a href="https://www.swisstopo.admin.ch">swisstopo</a>'

# Romandie, which is the territory this was built for: the whole of the
# French-speaking cantons plus a margin, as [west, south, east, north].
ROMANDIE = (5.85, 45.75, 7.75, 47.10)

# Fit band to colour. Deliberately the same three colours the rest of the
# console uses for a band, so a green dot and a green score mean one thing.
BAND_COLOURS = {
    "high": "#1d6b2f",
    "medium": "#c39a3a",
    "low": "#8a857c",
    "excluded": "#b9b4ab",
}

STYLES = """
#map { height:calc(100vh - 12rem); min-height:26rem; width:100%;
       border:1px solid var(--line); background:#e9e5dd; }
.maplibregl-popup-content { font:14px/1.45 -apple-system,BlinkMacSystemFont,
                            "Segoe UI",Roboto,sans-serif; padding:.6rem .7rem; }
.maplibregl-popup-content h4 { margin:0 0 .2rem; font-size:.95rem; }
.maplibregl-popup-content .dim { color:var(--dim); font-size:.8rem; }
.legend { display:flex; gap:1rem; flex-wrap:wrap; align-items:center;
          margin:.5rem 0 0; font-size:.8rem; color:var(--dim); }
.legend b { display:inline-block; width:.75rem; height:.75rem; border-radius:50%;
            margin-right:.25rem; vertical-align:-1px; }
.legend .hollow { background:var(--paper); box-shadow:inset 0 0 0 2px var(--dim); }
"""


def shell(body: str, chrome: Any = None) -> str:
    return page("arpenteur", body, chrome, css=STYLES)


def feature(row: Row) -> dict[str, Any]:
    """One establishment as a GeoJSON feature.

    Only what a popup shows. The temptation is to hand the whole row over
    because it is already loaded; the cost is a payload that grows with every
    column anyone adds to the query and is downloaded in full before the map
    draws its first dot.
    """
    address = " ".join(
        part for part in (
            row["street"], row["house_number"], row["postal_code"], row["city"]
        ) if part
    )
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [row["lon"], row["lat"]]},
        "properties": {
            "id": int(row["id"]),
            "company_id": int(row["company_id"]),
            "name": row["name"],
            "site": row["site_name"] or "",
            "seat": bool(row["is_seat"]),
            "canton": row["canton"] or "",
            "address": address,
            "score": row["score"],
            "band": row["band"] or "",
            "state": row["state"] or "",
            "precision": row["precision"] or "",
            "colour": BAND_COLOURS.get(row["band"] or "", "#6b665e"),
            # Drawn hollow when the point is a town rather than a door.
            "exact": (row["precision"] == "address"),
        },
    }


def collection(rows: list[Row]) -> dict[str, Any]:
    return {"type": "FeatureCollection",
            "features": [feature(row) for row in rows]}


def map_page(
    *, count: int, placed: int, with_address: int, canton: str = "",
    segment: str = "", sector: str = "", band: str = "", state: str = "",
    cantons: list[str], segments: list[str], states: list[str],
    sectors: list[str] | None = None, attribution_notice: str = "",
    regions: int = 0,
) -> str:
    query = {k: v for k, v in (
        ("canton", canton), ("segment", segment), ("sector", sector),
        ("band", band), ("state", state)
    ) if v}
    source_url = "/map/points.geojson" + (
        "?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else ""
    )

    filters = ui.toolbar(
        ui.select_field("canton", "Canton", cantons, chosen=canton,
                        blank="All cantons")
        + (ui.select_field("sector", "Sector", sectors, chosen=sector,
                           blank="All sectors") if sectors else "")
        + ui.select_field("segment", "Segment", segments, chosen=segment,
                          blank="All segments")
        + ui.select_field("band", "Fit band",
                          ["high", "medium", "low", "excluded"],
                          chosen=band, blank="Any band")
        + ui.select_field("state", "Pipeline state", states, chosen=state,
                          blank="Any state"),
        action="/map", css="filters",
    )

    legend = (
        '<div class="legend">'
        + "".join(
            f'<span><b style="background:{colour}"></b>{esc(name)}</span>'
            for name, colour in BAND_COLOURS.items()
        )
        + '<span><b class="hollow"></b>placed on a town, not an address</span>'
        + f"<span>{ui.num(count)} of {ui.num(placed)} placed shown</span>"
        + "</div>"
    )

    waiting = with_address - placed
    note = (
        f'<div class="warn">Nothing is placed yet. {ui.num(with_address)} companies '
        "hold a postal address from the register and none has been geocoded. "
        'Run <a href="/pipeline">geocode</a>, which is free.</div>'
        if placed == 0
        else (
            f'<p class="dim small">{ui.num(waiting)} companies hold an address that '
            'has not been placed yet. <a href="/pipeline">Geocode</a> is free '
            "and picks up where it left off.</p>"
            if waiting > 0
            else ""
        )
    )

    return (
        "<h1>Map</h1>"
        "<p class='lede'>Every establishment with a coordinate. A company is a "
        "legal entity and has no location; its establishments do, which is why "
        "a foundation across seven sites is seven markers rather than one.</p>"
        + filters + note
        + '<div id="map"></div>'
        + legend
        + f'<p class="dim small" style="white-space:pre-line">{esc(attribution_notice)}</p>'
        + (f'<p class="dim small">{regions} cantonal boundary(ies) drawn from '
           "swissBOUNDARIES3D. Without them the territory is a text comparison "
           'on a column.</p>' if regions else
           '<p class="dim small">No cantonal boundaries held. Run '
           '<a href="/pipeline">regions</a>, which is free and one request per '
           "canton.</p>")
        + _script(source_url, "/map/regions.geojson" if regions else "")
    )


# Built only when there are boundaries to draw. Emitting it behind a runtime
# guard on an empty URL would leave a page carrying JavaScript that can never
# run, which is the kind of thing a reader has to prove harmless before they
# can change anything near it.
REGIONS_BLOCK = """
    // The cantonal outlines first, so the markers sit on top of them rather
    // than under. Drawn from the boundary the Confederation publishes, not
    // from the canton column: a survey scoped to six cantons was deciding
    // what was inside it by comparing text.
    fetch('%s').then(r => r.json()).then(function (shapes) {
      map.addSource('regions', {type: 'geojson', data: shapes});
      map.addLayer({
        id: 'regions-fill', type: 'fill', source: 'regions',
        paint: {'fill-color': '#7a1f1f', 'fill-opacity': 0.05}
      });
      map.addLayer({
        id: 'regions-line', type: 'line', source: 'regions',
        paint: {'line-color': '#7a1f1f', 'line-width': 1.2, 'line-opacity': 0.55}
      });
      map.addLayer({
        id: 'regions-label', type: 'symbol', source: 'regions',
        layout: {
          'text-field': ['get', 'name'],
          'text-size': 12,
          'text-transform': 'uppercase',
          'text-letter-spacing': 0.12
        },
        paint: {
          'text-color': '#7a1f1f', 'text-opacity': 0.7,
          'text-halo-color': '#ffffff', 'text-halo-width': 1.5
        }
      });
    });
"""


def _script(source_url: str, regions_url: str = "") -> str:
    """MapLibre, the swisstopo base layer, and one circle layer over it.

    Circles from the data rather than DOM markers. Five thousand
    `maplibregl.Marker` objects are five thousand absolutely positioned
    elements the browser repositions on every frame of a pan; a circle layer is
    drawn on the GPU and stays smooth at the size this survey reaches.
    """
    regions_block = REGIONS_BLOCK % regions_url if regions_url else ""
    return f"""
<link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<script>
(function () {{
  const map = new maplibregl.Map({{
    container: 'map',
    style: {{
      version: 8,
      sources: {{
        swisstopo: {{
          type: 'raster',
          tiles: ['{TILES}'],
          tileSize: 256,
          attribution: '{ATTRIBUTION}'
        }}
      }},
      layers: [{{id: 'base', type: 'raster', source: 'swisstopo'}}]
    }},
    bounds: [[{ROMANDIE[0]}, {ROMANDIE[1]}], [{ROMANDIE[2]}, {ROMANDIE[3]}]],
    fitBoundsOptions: {{padding: 20}}
  }});
  map.addControl(new maplibregl.NavigationControl(), 'top-right');
  map.addControl(new maplibregl.ScaleControl({{unit: 'metric'}}));

  map.on('load', function () {{
{regions_block}
    fetch('{source_url}').then(r => r.json()).then(function (data) {{
      map.addSource('sites', {{type: 'geojson', data: data}});
      map.addLayer({{
        id: 'sites-circle', type: 'circle', source: 'sites',
        paint: {{
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 4, 12, 8, 16, 12],
          'circle-color': ['case', ['get', 'exact'], ['get', 'colour'], '#ffffff'],
          'circle-stroke-width': 2,
          'circle-stroke-color': ['get', 'colour'],
          'circle-opacity': 0.85
        }}
      }});
      if (data.features.length) {{
        const b = new maplibregl.LngLatBounds();
        data.features.forEach(f => b.extend(f.geometry.coordinates));
        map.fitBounds(b, {{padding: 60, maxZoom: 14, duration: 0}});
      }}
    }});
  }});

  map.on('click', 'sites-circle', function (e) {{
    const p = e.features[0].properties;
    const bits = [
      '<h4>' + p.name + '</h4>',
      p.site ? '<div>' + p.site + (p.seat === true || p.seat === 'true' ? ' (seat)' : '') + '</div>' : '',
      p.address ? '<div class="dim">' + p.address + '</div>' : '',
      p.score !== null && p.score !== '' ? '<div class="dim">fit ' + p.score + ' · ' + p.band + '</div>' : '',
      p.state ? '<div class="dim">' + p.state + '</div>' : '',
      p.precision !== 'address' ? '<div class="dim">placed on the ' + p.precision + ', not the address</div>' : '',
      '<div><a href="/company/' + p.company_id + '">Open the company</a></div>'
    ];
    new maplibregl.Popup({{closeButton: true}})
      .setLngLat(e.features[0].geometry.coordinates)
      .setHTML(bits.join(''))
      .addTo(map);
  }});
  map.on('mouseenter', 'sites-circle', () => map.getCanvas().style.cursor = 'pointer');
  map.on('mouseleave', 'sites-circle', () => map.getCanvas().style.cursor = '');
}})();
</script>
"""


def as_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
