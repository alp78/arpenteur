"""Reading and writing where things are.

Every statement touching `geo` lives here. It reaches into `core.company` by id
and no further: an establishment belongs to a company, and that is the only
reference this module needs or the boundary allows. Nothing in `core` or
`intel` points back, so dropping the schema takes the map and leaves the
survey.

The shape to keep in mind is that an address and a point are separate rows for
a reason written in the migration: an address is what the register or the
client told us, a point is what a geocoder made of it. A bad geocode over a
good address is the ordinary case, and one row could not tell them apart.
"""

from __future__ import annotations

from typing import Any

from ...kernel.db import Connection, Row, now

# What a marker is allowed to claim. Kept here rather than only in the source
# module because the console reads it to decide how a marker is drawn, and a
# point placed on a town centre must not look like one placed on a door.
PRECISIONS = ("address", "street", "postcode")


# --- addresses ----------------------------------------------------------------


def save_address(conn: Connection, fields: dict[str, Any]) -> int:
    """Store a postal address, or find the one already held.

    Deduplicated on the address itself rather than on the company: two
    companies at the same street number are two establishments of one address,
    which is what the shared row says, and geocoding it twice would be two
    requests for one answer.
    """
    row = conn.execute(
        """
        INSERT INTO geo.address (street, house_number, po_box, postal_code, city,
                                 canton, country, query, source_url, method,
                                 fetched_at)
        VALUES (%(street)s, %(house_number)s, %(po_box)s, %(postal_code)s,
                %(city)s, %(canton)s, COALESCE(%(country)s, 'CH'), %(query)s,
                %(source_url)s, %(method)s, %(fetched_at)s)
        ON CONFLICT (street, house_number, postal_code, city, country)
        DO UPDATE SET query = COALESCE(excluded.query, geo.address.query),
                      canton = COALESCE(excluded.canton, geo.address.canton)
        RETURNING id
        """,
        {
            "street": None, "house_number": None, "po_box": None,
            "postal_code": None, "city": None, "canton": None, "country": "CH",
            "query": None, "source_url": None, "method": None,
            "fetched_at": None, **fields,
        },
    ).fetchone()
    return int(row["id"])


def address(conn: Connection, address_id: int) -> Row | None:
    return conn.execute(
        "SELECT * FROM geo.address WHERE id = %s", (address_id,)
    ).fetchone()


# --- establishments -----------------------------------------------------------


def save_establishment(
    conn: Connection, company_id: int, *, address_id: int | None = None,
    name: str | None = None, kind: str | None = None, is_seat: bool = False,
    method: str = "manual",
) -> int:
    """One place a company operates from.

    The seat is upserted on `(company_id, is_seat)` in effect: a company has
    one registered address and re-running the stage must not add a second row
    for it every time the register is re-read. Other sites are added, because
    a foundation with seven of them has seven and no key to tell them apart
    but their name.
    """
    if is_seat:
        held = conn.execute(
            "SELECT id FROM geo.establishment WHERE company_id = %s AND is_seat",
            (company_id,),
        ).fetchone()
        if held:
            conn.execute(
                "UPDATE geo.establishment SET address_id = COALESCE(%s, address_id),"
                " name = COALESCE(%s, name), updated_at = %s WHERE id = %s",
                (address_id, name, now(), held["id"]),
            )
            return int(held["id"])
    if name:
        same = conn.execute(
            "SELECT id FROM geo.establishment WHERE company_id = %s AND name = %s",
            (company_id, name),
        ).fetchone()
        if same:
            conn.execute(
                "UPDATE geo.establishment SET address_id = COALESCE(%s, address_id),"
                " kind = COALESCE(%s, kind), updated_at = %s WHERE id = %s",
                (address_id, kind, now(), same["id"]),
            )
            return int(same["id"])
    row = conn.execute(
        "INSERT INTO geo.establishment (company_id, address_id, name, kind,"
        " is_seat, method, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)"
        " RETURNING id",
        (company_id, address_id, name, kind, is_seat, method, now()),
    ).fetchone()
    return int(row["id"])


def place(
    conn: Connection, establishment_id: int, *, lat: float, lon: float,
    precision: str, confidence: float, geocoder: str = "geoadmin",
    source_url: str | None = None,
) -> None:
    """Put a point on an establishment, with what it is worth.

    `ST_MakePoint` takes longitude first. That is the one thing about this
    call that catches everybody, and swapping them silently puts Lausanne in
    the Indian Ocean rather than raising anything.
    """
    conn.execute(
        "UPDATE geo.establishment SET"
        " point = ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography,"
        " geocoder = %s, precision = %s, confidence = %s, geocoded_at = %s,"
        " source_url = %s, updated_at = %s WHERE id = %s",
        (lon, lat, geocoder, precision, confidence, now(), source_url,
         now(), establishment_id),
    )


def forget_point(conn: Connection, establishment_id: int) -> None:
    """Take a point off without taking the establishment off.

    The correction for a marker in the wrong place: the site is real, where
    the geocoder put it is not, and the next run should try again.
    """
    conn.execute(
        "UPDATE geo.establishment SET point = NULL, geocoder = NULL,"
        " precision = NULL, confidence = NULL, geocoded_at = NULL,"
        " updated_at = %s WHERE id = %s",
        (now(), establishment_id),
    )


def establishment(conn: Connection, establishment_id: int) -> Row | None:
    return conn.execute(
        """SELECT e.*, ST_Y(e.point::geometry) AS lat, ST_X(e.point::geometry) AS lon,
                  a.street, a.house_number, a.postal_code, a.city
             FROM geo.establishment e
             LEFT JOIN geo.address a ON a.id = e.address_id
            WHERE e.id = %s""",
        (establishment_id,),
    ).fetchone()


def delete_establishment(conn: Connection, establishment_id: int) -> int | None:
    row = conn.execute(
        "SELECT company_id FROM geo.establishment WHERE id = %s",
        (establishment_id,),
    ).fetchone()
    if row is None:
        return None
    conn.execute("DELETE FROM geo.establishment WHERE id = %s", (establishment_id,))
    return int(row["company_id"])


def establishments_for(conn: Connection, company_id: int) -> list[Row]:
    return conn.execute(
        """SELECT e.*, ST_Y(e.point::geometry) AS lat, ST_X(e.point::geometry) AS lon,
                  a.street, a.house_number, a.postal_code, a.city
             FROM geo.establishment e
             LEFT JOIN geo.address a ON a.id = e.address_id
            WHERE e.company_id = %s
            ORDER BY e.is_seat DESC, e.name NULLS LAST, e.id""",
        (company_id,),
    ).fetchall()


# --- what the stage works on --------------------------------------------------


def waiting(
    conn: Connection, *, uids: list[str] | None = None, canton: str = "",
    segment: str = "", band: str = "", only_worked: bool = False,
    limit: int = 200,
) -> list[Row]:
    """Companies holding a register address and no point yet.

    The selection is deliberately narrow. 3'690 of 184'939 companies carry an
    address at all, because an address only arrives with hydration, and
    geocoding all of them would be as many requests to a public service for a
    map nobody has asked to see. `only_worked` narrows it further to the
    companies on the board, which is the shortlist a map is actually read for.
    """
    where = [
        "c.address_json IS NOT NULL",
        "NOT EXISTS (SELECT 1 FROM geo.establishment e"
        " WHERE e.company_id = c.id AND e.is_seat AND e.point IS NOT NULL)",
    ]
    params: list[Any] = []
    if uids:
        # Matched on the digits, the way the search box does: a UID is written
        # `CHE-103.895.771` on a letterhead and `CHE103895771` in the register,
        # and somebody naming one company should not have to know which.
        where.append(
            "regexp_replace(c.uid, '[^0-9]', '', 'g') = ANY(%s)"
        )
        params.append(["".join(ch for ch in uid if ch.isdigit()) for uid in uids])
    if canton:
        where.append("c.canton = %s")
        params.append(canton)
    if segment:
        where.append(
            "EXISTS (SELECT 1 FROM intel.segment s"
            " WHERE s.company_id = c.id AND s.segment = %s)"
        )
        params.append(segment)
    if band:
        where.append(
            "EXISTS (SELECT 1 FROM intel.fit f"
            " WHERE f.company_id = c.id AND f.band = %s)"
        )
        params.append(band)
    if only_worked:
        where.append("EXISTS (SELECT 1 FROM intel.prospect p WHERE p.company_id = c.id)")
    return conn.execute(
        f"""SELECT c.id, c.uid, c.name, c.canton, c.address_json
              FROM core.company c
             WHERE {' AND '.join(where)}
             ORDER BY c.id LIMIT %s""",
        params + [limit],
    ).fetchall()


def counts(conn: Connection) -> dict[str, int]:
    """Where the map stands, for the operations page."""
    row = conn.execute(
        """SELECT
             (SELECT COUNT(*) FROM core.company WHERE address_json IS NOT NULL)
               AS with_address,
             (SELECT COUNT(*) FROM geo.establishment WHERE point IS NOT NULL)
               AS placed,
             (SELECT COUNT(*) FROM geo.establishment) AS establishments"""
    ).fetchone()
    return {k: int(v) for k, v in row.items()}


# --- reading the map ----------------------------------------------------------


def points(
    conn: Connection, *, canton: str = "", segment: str = "", band: str = "",
    state: str = "", company_ids: list[int] | None = None, limit: int = 5000,
) -> list[Row]:
    """Everything with a point, with what a marker has to say for itself.

    One query. A map fetches its markers once and then pans and zooms without
    asking again, which is the whole reason the layer is GeoJSON rather than a
    page of HTML.
    """
    where = ["e.point IS NOT NULL"]
    params: list[Any] = []
    if canton:
        where.append("c.canton = %s")
        params.append(canton)
    if segment:
        where.append(
            "EXISTS (SELECT 1 FROM intel.segment s"
            " WHERE s.company_id = c.id AND s.segment = %s)"
        )
        params.append(segment)
    if band:
        where.append("f.band = %s")
        params.append(band)
    if state:
        where.append("p.state = %s")
        params.append(state)
    if company_ids is not None:
        # A list of ids rather than a join into `crm.client`, which is what
        # "only my clients" would naturally be written as. This module requires
        # the scout and not the clients module: a query naming `crm` here would
        # break the moment somebody ran a survey without the client side, and
        # nothing would have warned them. The console knows both and passes the
        # answer in.
        where.append("e.company_id = ANY(%s)")
        params.append(list(company_ids))
    return conn.execute(
        f"""SELECT e.id, e.company_id, e.name AS site_name, e.is_seat,
                   e.precision, e.confidence,
                   ST_Y(e.point::geometry) AS lat, ST_X(e.point::geometry) AS lon,
                   c.name, c.uid, c.canton,
                   f.score, f.band, p.state,
                   a.street, a.house_number, a.postal_code, a.city
              FROM geo.establishment e
              JOIN core.company c ON c.id = e.company_id
              LEFT JOIN geo.address a ON a.id = e.address_id
              LEFT JOIN intel.fit f ON f.company_id = c.id
              LEFT JOIN intel.prospect p ON p.company_id = c.id
             WHERE {' AND '.join(where)}
             ORDER BY f.score DESC NULLS LAST, c.name
             LIMIT %s""",
        params + [limit],
    ).fetchall()


def within(
    conn: Connection, *, lat: float, lon: float, metres: float, limit: int = 200
) -> list[Row]:
    """Establishments within a distance of a point, nearest first.

    The query the geography column exists for. `ST_DWithin` on a geography
    takes metres and uses the GiST index; the same thing written as a distance
    comparison would compute one for every row in the table.
    """
    return conn.execute(
        """SELECT e.id, e.company_id, e.name AS site_name, c.name, c.canton,
                  ST_Y(e.point::geometry) AS lat, ST_X(e.point::geometry) AS lon,
                  ST_Distance(e.point, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography)
                    AS metres
             FROM geo.establishment e
             JOIN core.company c ON c.id = e.company_id
            WHERE e.point IS NOT NULL
              AND ST_DWithin(e.point, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography, %s)
            ORDER BY metres LIMIT %s""",
        (lon, lat, lon, lat, metres, limit),
    ).fetchall()


# --- regions ------------------------------------------------------------------


def save_region(
    conn: Connection, *, code: str, name: str, kind: str, geojson: str,
    source_url: str | None = None, method: str = "geoadmin",
) -> int:
    """A boundary, from GeoJSON. Forced to MultiPolygon so the column holds it
    whether the source drew one ring or twelve."""
    row = conn.execute(
        """INSERT INTO geo.region (code, name, kind, shape, source_url, method,
                                   fetched_at)
           VALUES (%s, %s, %s,
                   ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s, %s, %s)
           ON CONFLICT (kind, code) DO UPDATE SET
               name = excluded.name, shape = excluded.shape,
               source_url = excluded.source_url, fetched_at = excluded.fetched_at
           RETURNING id""",
        (code, name, kind, geojson, source_url, method, now()),
    ).fetchone()
    return int(row["id"])


def regions(conn: Connection, kind: str = "canton") -> list[Row]:
    return conn.execute(
        "SELECT id, code, name, kind, ST_AsGeoJSON(shape) AS geojson"
        " FROM geo.region WHERE kind = %s ORDER BY code",
        (kind,),
    ).fetchall()
