"""Analytics: reading everything back without writing to any of it.

Two properties matter more than the individual figures.

**Nothing here writes.** The whole schema is views, and the test that matters
is the one that says so: if a table ever appears in `analytics`, something has
started keeping state where the design says there is none, and the separation
that makes a report safe to run has quietly gone.

**A missing module is an absent report, not a broken page.** These views read
`crm` and `commercial` while depending on neither, and the guard that makes
that safe is only exercised on a deployment that lacks them, which is not the
one anybody develops on. So it is tested directly.

The figures themselves are checked where getting them wrong would be quiet and
misleading: a funnel that counts only where things currently are makes every
step look narrow but the last, and a conversion report read without the
propensity concludes that the score works no matter what the score does.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import Territory
from arpenteur.console.app import create_app
from arpenteur.kernel.db import Connection
from arpenteur.modules.analytics import store as analytics
from arpenteur.modules.sales import store as sales
from arpenteur.modules.scout import store as scout

pytestmark = pytest.mark.pg


def _company(conn: Connection, uid: str, name: str, canton: str = "GE") -> int:
    return db.upsert_company(conn, {"uid": uid, "name": name, "canton": canton})


def _moved(conn: Connection, company_id: int, states: list[tuple[str, int]]) -> None:
    """Put a company through a sequence of states, days apart.

    Written straight into the audit table rather than through `set_prospect`,
    because the dates are the point and `now()` cannot be moved.
    """
    when = datetime(2026, 6, 1, tzinfo=timezone.utc)
    previous = None
    for state, days in states:
        when = when + timedelta(days=days)
        conn.execute(
            "INSERT INTO audit.state_change (entity, entity_id, company_id,"
            " from_state, to_state, at) VALUES ('prospect', %s, %s, %s, %s, %s)",
            (company_id, company_id, previous, state, when),
        )
        previous = state
    # Written straight in, not through `set_prospect`: that helper records a
    # transition of its own at now(), which would land a second row for the
    # last state two months after the one this function just dated and pull
    # every average with it.
    conn.execute(
        "INSERT INTO intel.prospect (company_id, state, created_at, updated_at)"
        " VALUES (%s, %s, %s, %s) ON CONFLICT (company_id) DO UPDATE"
        " SET state = EXCLUDED.state",
        (company_id, previous, when, when),
    )


# --- the shape of the schema -----------------------------------------------------


def test_the_analytics_schema_holds_no_tables(db_path):
    """The separation the whole design rests on. A table here is state, and
    state here means a report can disagree with the thing it reports on."""
    conn = db.open_db(db_path)
    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables"
        " WHERE table_schema = 'analytics' AND table_type = 'BASE TABLE'"
    ).fetchall()
    assert [row["table_name"] for row in tables] == []
    conn.close()


def test_nothing_upstream_depends_on_analytics(db_path):
    """A view may read a table. A table must never reference a view, and no
    foreign key anywhere may point into this schema."""
    conn = db.open_db(db_path)
    found = conn.execute(
        "SELECT COUNT(*) AS n FROM information_schema.table_constraints"
        " WHERE constraint_schema = 'analytics' AND constraint_type = 'FOREIGN KEY'"
    ).fetchone()
    assert found["n"] == 0
    conn.close()


def test_every_named_report_exists_on_a_full_deployment(db_path):
    conn = db.open_db(db_path)
    have = analytics.available(conn)
    for name, _title, _module in analytics.REPORTS:
        assert name in have, f"{name} is listed on the page and does not exist"
    conn.close()


def test_a_report_name_that_is_not_one_is_refused(db_path):
    conn = db.open_db(db_path)
    with pytest.raises(ValueError, match="not a report name"):
        analytics.read(conn, "coverage; DROP SCHEMA core CASCADE")
    conn.close()


def test_a_missing_view_reads_as_empty_rather_than_breaking_the_page(db_path):
    """A survey-only deployment has no pipeline report, and the page shows a
    dozen reports in a row: one failure must not take the rest with it."""
    conn = db.open_db(db_path)
    conn.execute("DROP VIEW IF EXISTS analytics.pipeline")
    assert analytics.read(conn, "pipeline") == []
    # And the connection is still usable, which is the half that is easy to
    # get wrong: a failed statement poisons the transaction.
    assert analytics.read(conn, "coverage")
    conn.close()


# --- the survey ------------------------------------------------------------------


def test_coverage_counts_what_is_left_rather_than_what_is_done(db_path):
    conn = db.open_db(db_path)
    _company(conn, "CHE1", "One")
    hydrated = _company(conn, "CHE2", "Two")
    conn.execute(
        "UPDATE core.company SET hydrated_at = now() WHERE id = %s", (hydrated,)
    )
    db.save_fit(conn, hydrated, {"score": 80, "band": "high"})
    row = analytics.read(conn, "coverage")[0]
    assert row["companies"] == 2
    assert row["hydrated"] == 1
    assert row["scored"] == 1
    conn.close()


def test_the_band_profile_says_how_reachable_each_band_is(db_path):
    """A band full of companies nobody can write to will not convert whatever
    its score says, and that was invisible while the score was one number."""
    conn = db.open_db(db_path)
    reachable = _company(conn, "CHE1", "Reachable")
    unreachable = _company(conn, "CHE2", "Unreachable")
    suppressed = _company(conn, "CHE3", "Suppressed")
    for company_id in (reachable, unreachable, suppressed):
        db.save_fit(conn, company_id, {"score": 80, "band": "high"})
    db.save_contact(conn, company_id=reachable, kind="email", value="a@f.ch",
                    method="published")
    db.save_contact(conn, company_id=suppressed, kind="email", value="b@f.ch",
                    method="published")
    conn.execute("UPDATE core.contact SET suppressed = TRUE WHERE company_id = %s",
                 (suppressed,))
    conn.commit()
    analytics.refresh(conn)

    row = [r for r in analytics.read(conn, "band_profile") if r["band"] == "high"][0]
    assert row["companies"] == 3
    # A suppressed address is not a way to reach somebody.
    assert row["reachable"] == 1
    conn.close()


def test_spend_is_read_from_what_the_api_reported(db_path):
    conn = db.open_db(db_path)
    run_id = db.start_run(conn, "match", {})
    db.finish_run(conn, run_id, {"scored": 4, "model_calls": 4,
                                 "input_tokens": 12000, "output_tokens": 900})
    second = db.start_run(conn, "match", {})
    db.finish_run(conn, second, {"scored": 1, "model_calls": 1,
                                 "input_tokens": 3000, "output_tokens": 200})
    row = [r for r in analytics.read(conn, "spend_by_stage")
           if r["stage"] == "match"][0]
    assert row["runs"] == 2
    assert row["input_tokens"] == 15000
    assert row["model_calls"] == 5
    conn.close()


def test_a_run_reporting_no_usage_is_unaccounted_not_free(db_path):
    """Zero tokens against a real run is a gap in the accounting, and it has
    to read as one rather than as a stage that costs nothing."""
    conn = db.open_db(db_path)
    run_id = db.start_run(conn, "profile", {})
    db.finish_run(conn, run_id, {"classified": 20})
    row = [r for r in analytics.read(conn, "spend_by_stage")
           if r["stage"] == "profile"][0]
    assert row["runs"] == 1 and row["input_tokens"] == 0
    conn.close()


# --- the funnel -------------------------------------------------------------------


def test_the_funnel_counts_states_ever_reached(db_path):
    """Counting only where things currently are makes every step of a funnel
    look narrow except the last."""
    conn = db.open_db(db_path)
    first = _company(conn, "CHE1", "One")
    second = _company(conn, "CHE2", "Two")
    _moved(conn, first, [("new", 0), ("contacted", 5), ("meeting", 10)])
    _moved(conn, second, [("new", 0), ("contacted", 3)])

    rows = {r["state"]: r for r in analytics.read(conn, "funnel")}
    assert rows["new"]["reached"] == 2
    assert rows["contacted"]["reached"] == 2
    # The one that went furthest is still counted at every step it passed.
    assert rows["meeting"]["reached"] == 1
    conn.close()


def test_the_funnel_reports_how_long_each_step_took(db_path):
    """The question a pipeline is actually read for, and the payoff for
    recording transitions rather than overwriting a status."""
    conn = db.open_db(db_path)
    company_id = _company(conn, "CHE1", "One")
    _moved(conn, company_id, [("new", 0), ("contacted", 4), ("meeting", 10)])
    rows = {r["state"]: r for r in analytics.read(conn, "funnel")}
    assert float(rows["contacted"]["mean_days_from_previous"]) == pytest.approx(4.0)
    assert float(rows["meeting"]["mean_days_from_previous"]) == pytest.approx(10.0)
    # The first state has nothing before it, which is null rather than zero.
    assert rows["new"]["mean_days_from_previous"] is None
    conn.close()


def test_what_has_gone_quiet_excludes_what_is_finished(db_path):
    conn = db.open_db(db_path)
    live = _company(conn, "CHE1", "Still open")
    done = _company(conn, "CHE2", "Won already")
    _moved(conn, live, [("new", 0), ("contacted", 5)])
    _moved(conn, done, [("new", 0), ("won", 5)])
    names = {row["name"] for row in analytics.read(conn, "gone_quiet")}
    assert "Still open" in names and "Won already" not in names
    conn.close()


def test_the_outcome_report_carries_the_propensity_it_needs(db_path):
    """Without it, a reading of what converted concludes that the score works
    no matter what the score does: outcomes exist only for what was picked,
    and what was picked ranked well."""
    conn = db.open_db(db_path)
    for uid, score, band in (("CHE1", 90, "high"), ("CHE2", 60, "medium"),
                             ("CHE3", 20, "low")):
        company_id = _company(conn, uid, uid)
        db.save_fit(conn, company_id, {"score": score, "band": band})
    picked = conn.execute(
        "SELECT company_id FROM intel.fit WHERE score = 90"
    ).fetchone()["company_id"]
    scout.set_prospect(conn, picked, state="contacted")

    rows = analytics.read(conn, "selection_outcomes")
    assert len(rows) == 1
    assert rows[0]["band_when_picked"] == "high"
    assert rows[0]["picked"] == 1
    assert rows[0]["mean_propensity"] is not None
    conn.close()


# --- what the other modules add ----------------------------------------------------


def test_the_client_report_shows_how_full_each_file_is(db_path):
    """An empty dossier is the best single predictor of a meeting that goes
    badly, and it is invisible on a page that shows one client at a time."""
    conn = db.open_db(db_path)
    company_id = _company(conn, "CHE1", "Fondation Bois-Gentil")
    client_id = db.promote_to_client(conn, company_id)
    db.save_dossier_section(conn, client_id, "systems", "Two servers, one on site.")
    db.record_event(conn, client_id, kind="call", occurred_at=db.now())
    row = analytics.read(conn, "client_coverage")[0]
    assert row["code"] == "BOIS-GENTIL"
    assert row["dossier_sections"] == 1
    assert row["interactions"] == 1
    conn.close()


def test_the_pipeline_values_the_newest_version_of_each_quote(db_path):
    """An earlier version is not a second opportunity."""
    conn = db.open_db(db_path)
    company_id = _company(conn, "CHE1", "Bois-Gentil")
    client_id = db.promote_to_client(conn, company_id)
    quote_id = sales.create_quote(conn, client_id)

    first = sales.add_version(conn, quote_id)
    sales.add_line(conn, first, {"label": "Diagnostic", "unit_price": 3600})
    sales.recompute(conn, first, vat_rate=None)
    sales.mark_sent(conn, first)

    second = sales.add_version(conn, quote_id)
    sales.add_line(conn, second, {"label": "Diagnostic", "unit_price": 5000})
    sales.recompute(conn, second, vat_rate=None)

    rows = analytics.read(conn, "pipeline")
    assert len(rows) == 1
    assert rows[0]["quotes"] == 1
    assert rows[0]["value"] == Decimal("5000.00")
    conn.close()


def test_aged_debt_shows_what_is_not_yet_due_as_a_negative(db_path):
    """Folding those to zero would make a healthy ledger look like a chased
    one."""
    conn = db.open_db(db_path)
    company_id = _company(conn, "CHE1", "Bois-Gentil")
    client_id = db.promote_to_client(conn, company_id)
    invoice_id = sales.create_invoice(conn, client_id)
    sales.add_invoice_line(conn, invoice_id, {"label": "Diagnostic",
                                              "unit_price": 3600})
    sales.recompute_invoice(conn, invoice_id, vat_rate=None)
    sales.issue_invoice(conn, invoice_id, issued_on=date.today(), days=30)

    row = analytics.read(conn, "aged_debt")[0]
    assert row["owed"] == Decimal("3600.00")
    assert int(row["days_late"]) < 0
    conn.close()


def test_effort_separates_logged_billable_and_unbilled(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "CHE1", "Bois-Gentil")
    client_id = db.promote_to_client(conn, company_id)
    sales.log_time(conn, client_id, {"occurred_on": date(2026, 8, 10),
                                     "minutes": 240})
    sales.log_time(conn, client_id, {"occurred_on": date(2026, 8, 10),
                                     "minutes": 120, "billable": False})
    row = analytics.read(conn, "effort")[0]
    assert (row["minutes"], row["billable_minutes"], row["unbilled_minutes"]) == \
        (360, 240, 240)
    conn.close()


# --- refreshing ---------------------------------------------------------------------


def test_refreshing_brings_the_materialised_half_up_to_date(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn, "CHE1", "One")
    db.save_fit(conn, company_id, {"score": 80, "band": "high"})
    conn.commit()

    # Stale by design: the view has not been refreshed since the row appeared.
    assert analytics.read(conn, "band_profile") == []
    done = analytics.refresh(conn)
    assert "band_profile" in done
    assert analytics.read(conn, "band_profile")[0]["companies"] == 1
    conn.close()


def test_the_connection_is_left_as_it_was_found(db_path):
    """The refresh turns autocommit on because REFRESH CONCURRENTLY cannot run
    in a transaction. Leaving it on would silently change how every subsequent
    write behaves."""
    conn = db.open_db(db_path)
    was = conn.autocommit
    analytics.refresh(conn)
    assert conn.autocommit == was
    conn.close()


# --- the page ---------------------------------------------------------------------


def _app(db_path):
    from types import SimpleNamespace

    return TestClient(create_app(SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("GE",), legal_forms=("0107",), name="geneve"),
        segments={},
        sectors={},
    )))


def test_the_reports_page_renders_every_report_it_has(db_path):
    conn = db.open_db(db_path)
    _company(conn, "CHE1", "Fiduciaire Sàrl")
    conn.commit()
    conn.close()

    page = _app(db_path).get("/reports")
    assert page.status_code == 200
    for name, _title, _module in analytics.REPORTS:
        assert f"analytics.{name}" in page.text


def test_the_page_says_the_aggregates_are_as_old_as_the_last_refresh(db_path):
    """A figure with no date is a figure somebody quotes in a meeting."""
    page = _app(db_path).get("/reports")
    assert "as old as the last refresh" in page.text
    assert "Refresh the aggregates" in page.text


def test_the_page_says_how_to_ask_what_it_does_not_answer(db_path):
    """Otherwise somebody builds an export, which is exactly what keeping
    analytics in the same database was meant to avoid."""
    page = _app(db_path).get("/reports").text
    assert "duckdb" in page.lower()
    assert "READ_ONLY" in page


def test_refreshing_from_the_page_reports_what_it_did(db_path):
    client = _app(db_path)
    reply = client.post("/reports/refresh", follow_redirects=False)
    assert reply.status_code == 303
    assert "Refreshed" in reply.headers["location"]


def test_a_survey_only_deployment_installs_and_reports_what_it_can(pg_url):
    """The claim the guarded blocks in the migration make, tested rather than
    asserted in a comment.

    Someone licensing the scout alone has no `crm` and no `commercial`. An
    installation that failed because a report wanted a table it does not have
    would make the modular design a fiction, and this is the only case that
    exercises it: it is not the deployment anybody develops on.

    A throwaway database, because there is no other way to have a schema be
    genuinely absent.
    """
    import psycopg

    from arpenteur.kernel import db as kernel_db, modules

    base, _, _name = kernel_db.dsn().rpartition("/")
    url = f"{base}/arpenteur_scoutonly"
    with psycopg.connect(kernel_db.dsn(), autocommit=True) as admin:
        admin.execute('DROP DATABASE IF EXISTS "arpenteur_scoutonly" WITH (FORCE)')
        admin.execute('CREATE DATABASE "arpenteur_scoutonly"')
    try:
        conn = kernel_db.connect(url)
        kernel_db.ensure_extensions(conn)
        kernel_db.migrate(conn, modules.load(("scout", "analytics")).migration_dirs())
        conn.commit()

        have = analytics.available(conn)
        assert "coverage" in have and "funnel" in have
        # Absent, not broken.
        assert "pipeline" not in have and "client_coverage" not in have
        assert analytics.read(conn, "pipeline") == []
        assert analytics.read(conn, "coverage")[0]["companies"] == 0
        conn.close()
    finally:
        with psycopg.connect(kernel_db.dsn(), autocommit=True) as admin:
            admin.execute('DROP DATABASE IF EXISTS "arpenteur_scoutonly" WITH (FORCE)')
