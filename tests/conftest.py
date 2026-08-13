from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from arpenteur.config import LlmConfig, Settings, Territory, ZefixConfig

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "pg: needs a live PostgreSQL. Skipped when none is reachable, so the "
        "suite still runs on a machine that has not started the container.",
    )
    # A console run is a thread, and the next test's cleanup terminates its
    # connection to get an exclusive lock on the tables. The thread then
    # reports a closed connection, which is exactly what was asked of it. See
    # the note in the `db_path` fixture for why the alternative is worse.
    config.addinivalue_line(
        "filterwarnings", "ignore::pytest.PytestUnhandledThreadExceptionWarning"
    )


@pytest.fixture(scope="session")
def pg_url() -> str:
    """A migrated database, or a skip.

    One template per session rather than per test: applying every module's
    migrations takes about a second, and doing it two hundred times would make
    the suite unpleasant enough that people stop running it.
    """
    import psycopg

    from arpenteur.kernel import db, modules

    # Only the last path segment, not every occurrence: the username is
    # `arpenteur` too, and a blanket replace quietly renames it as well.
    base, _, _name = db.dsn().rpartition("/")
    url = os.environ.get("TEST_DATABASE_URL") or f"{base}/arpenteur_test"
    try:
        admin = psycopg.connect(db.dsn(), autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"no PostgreSQL reachable: {exc}")
    name = url.rsplit("/", 1)[-1]
    with admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}"')
    conn = db.connect(url)
    db.ensure_extensions(conn)
    db.migrate(conn, modules.load(modules.AVAILABLE).migration_dirs())
    conn.close()
    # Point the whole process at it. `load_settings` reads the operator
    # overlay from the database itself, and without this the settings tests
    # would write to the test database and read back from the real one.
    os.environ["DATABASE_URL"] = url
    return url


@pytest.fixture
def pg(pg_url: str):
    """A connection whose work is rolled back when the test ends.

    Every test therefore starts from the same migrated, empty database without
    anything having to remember to clean up after itself.
    """
    from arpenteur.kernel import db

    conn = db.connect(pg_url)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _settings(root: Path, **overrides) -> Settings:
    fields = {
        "root": root,
        "territory": Territory(cantons=("GE",), legal_forms=("0106",), name="test"),
        "regions": {},
        "admit_new": True,
        "statuses": ("ACTIVE",),
        "segments": {},
        "signal_brief": "",
        "zefix": ZefixConfig(
            base_url="https://example.test/ZefixPublicREST",
            rate_limit=2.0,
            max_retries=0,
            contact="tests",
        ),
        "llm": LlmConfig(
            provider="anthropic",
            classify_model="test",
            draft_model="test",
            batch_size=20,
        ),
    }
    fields.update(overrides)
    return Settings(**fields)


@pytest.fixture
def build_settings() -> callable:
    """A real Settings for the tests that need one rather than a stand-in.

    Shared because two test modules were carrying identical copies, which is
    how a change to the constructor breaks a test that has nothing to do with
    it: adding `territory` broke both at once.
    """
    return _settings


@pytest.fixture
def fixture() -> callable:
    def load(name: str):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    return load


@pytest.fixture
def db_path(pg_url: str) -> str:
    """An empty, migrated database, under the name the suite has always used.

    It is a connection string now rather than a file path, and the name stayed
    because every test spells it and renaming them all would have hidden the
    port's real failures under a hundred cosmetic diffs.

    Emptied by truncating rather than by recreating: dropping and re-migrating
    a database costs about a second and there are two hundred tests. One
    TRUNCATE across the module schemas is a few milliseconds, and CASCADE means
    the order does not have to be maintained by hand as tables are added.
    """
    from arpenteur.kernel import db as kernel_db

    conn = kernel_db.connect(pg_url)
    try:
        # A test that opened a connection and did not close it holds locks, and
        # TRUNCATE then waits for it rather than failing — which is a suite that
        # hangs with no output instead of a test that names itself. Evict the
        # strays, then refuse to wait: a second is far longer than this needs.
        # Everything, not only the idle ones. Narrowing it to `state = 'idle'`
        # was tried and is worse: a console run's worker thread holds an active
        # connection, TRUNCATE then waits out its two seconds and sixty-six
        # unrelated tests fail on a timeout. Killing a worker mid-flight
        # produces one "connection is closed" warning from that thread, which
        # is the cheaper of the two noises and is filtered below.
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )
        conn.execute("SET lock_timeout = '2s'")
        tables = [
            f'"{row["table_schema"]}"."{row["table_name"]}"'
            for row in conn.execute(
                "SELECT table_schema, table_name FROM information_schema.tables"
                # Every schema any module owns. A new one missing from this list does
                # not fail: it silently keeps its rows across tests, and the
                # test that notices is the one that counts them.
                " WHERE table_schema IN ('core','intel','audit','crm','commercial','geo')"
                "   AND table_type = 'BASE TABLE'"
            )
        ]
        if tables:
            conn.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
        conn.commit()
    finally:
        conn.close()
    return pg_url
