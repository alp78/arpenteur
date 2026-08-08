from __future__ import annotations

from pathlib import Path

from arpenteur import db


def test_migrate_is_idempotent(db_path: Path) -> None:
    conn = db.connect(db_path)
    assert db.migrate(conn) == 1
    # Running again must be a no-op rather than an error: every command calls it.
    assert db.migrate(conn) == 1
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {"company", "segment", "sweep", "run", "person", "contact", "draft"} <= tables
    conn.close()


def test_upsert_preserves_first_seen_and_purpose(db_path: Path) -> None:
    conn = db.open_db(db_path)

    company_id = db.upsert_company(
        conn, {"uid": "CHE-1", "name": "Atelier Sàrl", "canton": "VD"}
    )
    first_seen = conn.execute(
        "SELECT first_seen FROM company WHERE id = ?", (company_id,)
    ).fetchone()[0]

    # A hydration pass adds the purpose.
    db.upsert_company(
        conn,
        {"uid": "CHE-1", "name": "Atelier Sàrl", "purpose": "Menuiserie", "hydrated": True},
    )
    # A later cheap sweep sees the company again with no purpose in the payload.
    db.upsert_company(conn, {"uid": "CHE-1", "name": "Atelier Sàrl", "canton": "VD"})

    row = conn.execute("SELECT * FROM company WHERE id = ?", (company_id,)).fetchone()
    assert row["first_seen"] == first_seen, "first_seen must never be overwritten"
    assert row["purpose"] == "Menuiserie", "a sweep must not blank a hydrated field"
    assert row["hydrated_at"] is not None
    assert conn.execute("SELECT COUNT(*) FROM company").fetchone()[0] == 1
    conn.close()


def test_completed_prefixes_excludes_deepened(db_path: Path) -> None:
    conn = db.open_db(db_path)
    db.record_sweep(conn, "VD", "0108", "a", 12, deepened=False)
    db.record_sweep(conn, "VD", "0108", "b", 1000, deepened=True)
    done = db.completed_prefixes(conn, "VD", "0108")
    # A deepened prefix is not finished: its children hold the rows.
    assert done == {"a"}
    conn.close()


def test_save_segments_replaces_on_reclassify(db_path: Path) -> None:
    conn = db.open_db(db_path)
    company_id = db.upsert_company(conn, {"uid": "CHE-2", "name": "Labo SA"})
    db.save_segments(
        conn, company_id, [{"segment": "data-driven", "confidence": 0.4}], "m1"
    )
    db.save_segments(
        conn,
        company_id,
        [{"segment": "data-driven", "confidence": 0.9, "rationale": "laboratory"}],
        "m2",
    )
    rows = conn.execute("SELECT * FROM segment WHERE company_id = ?", (company_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.9
    assert rows[0]["model"] == "m2"
    conn.close()
