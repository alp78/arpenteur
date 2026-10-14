from __future__ import annotations

from pathlib import Path

from arpenteur import db


def test_migrate_is_idempotent(db_path: str) -> None:
    """Every command calls it, so a second run must be a no-op rather than an
    error. Each module keeps its own version now, because a deployment that
    never enabled sales has no opinion about how far sales has got."""
    from arpenteur.kernel import db as kernel, modules

    conn = db.connect(db_path)
    dirs = modules.load(modules.AVAILABLE).migration_dirs()
    before = kernel.versions(conn)
    assert kernel.migrate(conn, dirs) == []
    assert kernel.versions(conn) == before
    assert before["scout"] >= 3

    tables = {
        f'{row["table_schema"]}.{row["table_name"]}'
        for row in conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables"
            " WHERE table_schema IN ('core','intel','crm','commercial','geo')"
        )
    }
    assert {
        "core.company", "core.person", "core.contact", "core.publication",
        "intel.segment", "intel.sweep", "intel.run", "intel.draft",
    } <= tables
    conn.close()


def test_an_edited_migration_is_caught_rather_than_skipped(db_path: str) -> None:
    """Two deployments claiming the same version with different schemas is the
    classic way a migration sequence goes wrong. The digest is what makes it an
    error instead of a silent divergence."""
    import pytest

    from arpenteur.kernel import db as kernel

    conn = db.connect(db_path)
    original = conn.execute(
        "SELECT digest FROM kernel.migration WHERE module = 'scout' AND version = 1"
    ).fetchone()["digest"]
    conn.execute(
        "UPDATE kernel.migration SET digest = 'tampered'"
        " WHERE module = 'scout' AND version = 1"
    )
    conn.commit()
    from arpenteur.kernel import modules

    try:
        with pytest.raises(RuntimeError, match="has changed since it was applied"):
            kernel.migrate(conn, modules.load(("scout",)).migration_dirs())
    finally:
        # The database is shared with the tests that follow, and a digest left
        # tampered would refuse every later migration of it.
        conn.rollback()
        conn.execute(
            "UPDATE kernel.migration SET digest = %s"
            " WHERE module = 'scout' AND version = 1", (original,)
        )
        conn.commit()
        conn.close()


def test_upsert_preserves_first_seen_and_purpose(db_path: Path) -> None:
    conn = db.open_db(db_path)

    company_id = db.upsert_company(
        conn, {"uid": "CHE-1", "name": "Atelier Sàrl", "canton": "VD"}
    )
    first_seen = conn.execute(
        "SELECT first_seen FROM core.company WHERE id = %s", (company_id,)
    ).fetchone()[0]

    # A hydration pass adds the purpose.
    db.upsert_company(
        conn,
        {"uid": "CHE-1", "name": "Atelier Sàrl", "purpose": "Menuiserie", "hydrated": True},
    )
    # A later cheap sweep sees the company again with no purpose in the payload.
    db.upsert_company(conn, {"uid": "CHE-1", "name": "Atelier Sàrl", "canton": "VD"})

    row = conn.execute("SELECT * FROM core.company WHERE id = %s", (company_id,)).fetchone()
    assert row["first_seen"] == first_seen, "first_seen must never be overwritten"
    assert row["purpose"] == "Menuiserie", "a sweep must not blank a hydrated field"
    assert row["hydrated_at"] is not None
    assert conn.execute("SELECT COUNT(*) AS n FROM core.company").fetchone()[0] == 1
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
    rows = conn.execute("SELECT * FROM intel.segment WHERE company_id = %s", (company_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.9
    assert rows[0]["model"] == "m2"
    conn.close()


# --- one company lookup --------------------------------------------------------------


def test_a_company_is_found_by_either_spelling_of_its_uid_or_by_its_name(db_path) -> None:
    conn = db.open_db(db_path)
    db.upsert_company(conn, {"uid": "CHE123456789", "name": "Atelier Sàrl", "canton": "VD"})
    db.upsert_company(conn, {"uid": "CHE987654321", "name": "Atelier du Nord SA", "canton": "VD"})
    from arpenteur.modules.scout import store

    assert store.find_company(conn, "CHE-123.456.789")["name"] == "Atelier Sàrl"
    assert store.find_company(conn, "che123456789")["name"] == "Atelier Sàrl"
    assert store.find_company(conn, "du nord")["uid"] == "CHE987654321"
    import pytest

    with pytest.raises(ValueError, match="more than one"):
        store.find_company(conn, "atelier")
    with pytest.raises(ValueError, match="No company"):
        store.find_company(conn, "nobody")
    conn.close()
