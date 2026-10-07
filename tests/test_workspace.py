"""The workspace file: what it promises and what it refuses.

A round trip that loses rows silently is the failure worth testing for, so the
central test exports a populated database, restores it into an empty one, and
compares the counts rather than trusting an exit code.

The rest are refusals. A file written by a later build, a file whose shared
migration has a different body, and an import aimed at a database that already
holds data are each a way to end up with a schema the code has never seen, and
each of them has to stop before anything is written.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import psycopg
import pytest

from arpenteur import workspace
from arpenteur.kernel import db as kernel_db
from arpenteur.kernel import modules as module_registry

pytestmark = pytest.mark.pg

TOML = """
[survey]
cantons = ["VD"]
legal_forms = ["0106"]
"""


def _dsn_for(name: str) -> str:
    base, _, _old = kernel_db.dsn().rpartition("/")
    return f"{base}/{name}"


def _make(name: str, *, migrate: bool) -> str:
    """A throwaway database, dropped by the fixture that made it."""
    url = _dsn_for(name)
    base, _, _ = url.rpartition("/")
    admin = psycopg.connect(f"{base}/postgres", autocommit=True)
    try:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}" TEMPLATE template0 ENCODING \'UTF8\'')
    finally:
        admin.close()
    if migrate:
        conn = kernel_db.connect(url)
        try:
            kernel_db.ensure_extensions(conn)
            kernel_db.migrate(
                conn, module_registry.load(module_registry.AVAILABLE).migration_dirs()
            )
        finally:
            conn.close()
    return url


def _drop(name: str) -> None:
    base, _, _ = _dsn_for(name).rpartition("/")
    admin = psycopg.connect(f"{base}/postgres", autocommit=True)
    try:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        admin.close()


@pytest.fixture
def source(pg_url: str) -> str:
    """A migrated database holding a few companies, committed."""
    url = _make("arpenteur_ws_src", migrate=True)
    conn = kernel_db.connect(url)
    try:
        for n, (uid, name, canton) in enumerate(
            [
                ("CHE-100.000.001", "Régie du Lac SA", "VD"),
                ("CHE-100.000.002", "Immobilière des Alpes Sàrl", "VS"),
                ("CHE-100.000.003", "Zürcher Liegenschaften AG", "ZH"),
            ],
            start=1,
        ):
            conn.execute(
                "INSERT INTO core.company (uid, name, canton, status)"
                " VALUES (%s, %s, %s, 'ACTIVE')",
                (uid, name, canton),
            )
            conn.execute(
                "INSERT INTO core.contact (company_id, kind, value, method)"
                " SELECT id, 'email', %s, 'test' FROM core.company WHERE uid = %s",
                (f"info{n}@example.test", uid),
            )
        conn.commit()
    finally:
        conn.close()
    yield url
    _drop("arpenteur_ws_src")


@pytest.fixture
def target(pg_url: str) -> str:
    """An empty database for the import to land in."""
    url = _make("arpenteur_ws_dst", migrate=False)
    yield url
    _drop("arpenteur_ws_dst")


@pytest.fixture
def settings_for(build_settings, tmp_path: Path, monkeypatch):
    def make(url: str, root: Path | None = None):
        root = root or tmp_path
        (root / "arpenteur.toml").write_text(TOML, encoding="utf-8")
        (root / "data").mkdir(exist_ok=True)
        monkeypatch.setenv("DATABASE_URL", url)
        return build_settings(root, modules=module_registry.AVAILABLE)

    return make


# --- the round trip -----------------------------------------------------------


def test_a_round_trip_keeps_every_row(source, target, settings_for, tmp_path):
    out = tmp_path / "w.arpw"
    settings = settings_for(source)
    conn = kernel_db.connect(source)
    try:
        report = workspace.export(settings, conn, out)
    finally:
        conn.close()
    assert report.manifest is not None
    assert report.manifest.counts["core.company"] == 3
    assert report.manifest.counts["core.contact"] == 3

    landed = workspace.import_(settings_for(target), out)
    # Not "it did not raise": the counts the manifest promised are the counts
    # the database has, table by table.
    assert landed.mismatches == {}
    assert landed.verified["core.company"] == (3, 3)

    conn = kernel_db.connect(target)
    try:
        names = [
            row["name"]
            for row in conn.execute("SELECT name FROM core.company ORDER BY name")
        ]
    finally:
        conn.close()
    # Accents survive the trip, which is the thing a wrong encoding breaks.
    assert names == [
        "Immobilière des Alpes Sàrl",
        "Régie du Lac SA",
        "Zürcher Liegenschaften AG",
    ]


def test_an_import_refuses_a_database_that_holds_data(
    source, target, settings_for, tmp_path
):
    out = tmp_path / "w.arpw"
    settings = settings_for(source)
    conn = kernel_db.connect(source)
    try:
        workspace.export(settings, conn, out)
    finally:
        conn.close()

    # Importing into the source itself: it already holds the three companies.
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.import_(settings_for(source), out)
    assert "already holds data" in str(caught.value)
    assert "core.company 3" in str(caught.value)
    assert "--force" in str(caught.value)

    # And the refusal left it alone.
    conn = kernel_db.connect(source)
    try:
        assert conn.execute(
            "SELECT count(*) AS n FROM core.company"
        ).fetchone()["n"] == 3
    finally:
        conn.close()


def test_force_replaces_what_was_there(source, target, settings_for, tmp_path):
    out = tmp_path / "w.arpw"
    settings = settings_for(source)
    conn = kernel_db.connect(source)
    try:
        workspace.export(settings, conn, out)
    finally:
        conn.close()
    landed = workspace.import_(settings_for(source), out, force=True)
    assert landed.mismatches == {}


def test_attachments_travel_with_the_database(source, settings_for, tmp_path):
    root = tmp_path / "src"
    (root / "data" / "attachments" / "ab").mkdir(parents=True)
    (root / "data" / "attachments" / "ab" / "abcd.pdf").write_bytes(b"%PDF-1.4 x")
    settings = settings_for(source, root)
    out = tmp_path / "w.arpw"
    conn = kernel_db.connect(source)
    try:
        report = workspace.export(settings, conn, out)
    finally:
        conn.close()
    assert report.manifest is not None
    assert report.manifest.attachments == 1
    assert report.manifest.attachment_bytes == 10
    with zipfile.ZipFile(out) as zf:
        assert "attachments/ab/abcd.pdf" in zf.namelist()


# --- what the file says about itself ------------------------------------------


def test_secrets_are_left_out_unless_asked_for(source, settings_for, tmp_path):
    root = tmp_path / "src"
    root.mkdir()
    (root / ".env").write_text("ZEFIX_PASSWORD=hunter2\n", encoding="utf-8")
    settings = settings_for(source, root)
    conn = kernel_db.connect(source)
    try:
        plain = workspace.export(settings, conn, tmp_path / "plain.arpw")
        withem = workspace.export(
            settings, conn, tmp_path / "secret.arpw", with_secrets=True
        )
    finally:
        conn.close()

    with zipfile.ZipFile(plain.path) as zf:
        assert ".env" not in zf.namelist()
    with zipfile.ZipFile(withem.path) as zf:
        assert ".env" in zf.namelist()
        assert b"hunter2" in zf.read(".env")
    assert plain.manifest is not None and withem.manifest is not None
    assert plain.manifest.has_secrets is False
    assert withem.manifest.has_secrets is True


def test_a_file_without_a_manifest_is_not_a_workspace(tmp_path):
    path = tmp_path / "plain.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("database.dump", b"not really")
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.inspect(path)
    assert "not an Arpenteur workspace" in str(caught.value)
    # The message has to say what to do instead, or it is a dead end.
    assert "pg_restore" in str(caught.value)


def test_a_newer_format_is_refused(tmp_path):
    path = tmp_path / "future.arpw"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            workspace.MANIFEST,
            json.dumps({"format": workspace.FORMAT + 1, "created_at": "2030-01-01"}),
        )
        zf.writestr(workspace.DUMP, b"x")
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.inspect(path)
    assert "Update Arpenteur" in str(caught.value)


def test_an_unknown_manifest_field_is_refused_as_newer(tmp_path):
    path = tmp_path / "odd.arpw"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            workspace.MANIFEST,
            json.dumps(
                {
                    "format": workspace.FORMAT,
                    "created_at": "2030-01-01",
                    "encryption": "age",
                }
            ),
        )
        zf.writestr(workspace.DUMP, b"x")
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.inspect(path)
    assert "encryption" in str(caught.value)


def test_a_corrupt_file_says_so(tmp_path):
    path = tmp_path / "torn.arpw"
    path.write_bytes(b"PK\x03\x04 truncated")
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.inspect(path)
    assert "not readable as a zip" in str(caught.value)


def test_a_missing_file_says_so(tmp_path):
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.inspect(tmp_path / "nope.arpw")
    assert "no such workspace file" in str(caught.value)


# --- the migration comparison -------------------------------------------------


def _manifest(migrations: dict[str, dict[str, str]]) -> workspace.Manifest:
    return workspace.Manifest(
        format=workspace.FORMAT, created_at="2026-10-07T00:00:00+00:00",
        app_version="0.1.0", server_version="16.4", database="arpenteur",
        collate="C.UTF-8", ctype="C.UTF-8",
        modules=module_registry.AVAILABLE, migrations=migrations,
        counts={}, attachments=0, attachment_bytes=0, has_secrets=False,
    )


def test_this_builds_own_migrations_are_compatible():
    mine = workspace._known_digests()
    assert mine, "this build carries no migrations, so the test proves nothing"
    assert workspace.check_compatible(_manifest(mine)) == []


def test_a_workspace_from_a_newer_build_is_refused():
    mine = workspace._known_digests()
    ahead = {k: dict(v) for k, v in mine.items()}
    highest = max(int(v) for v in ahead["scout"])
    ahead["scout"][str(highest + 1)] = "0" * 16
    problems = workspace.check_compatible(_manifest(ahead))
    assert len(problems) == 1
    assert "is newer" in problems[0]
    assert f"{highest + 1:04d}" in problems[0]


def test_the_same_version_with_a_different_body_is_refused():
    """Same number, different schema, and nothing downstream would notice."""
    mine = workspace._known_digests()
    forked = {k: dict(v) for k, v in mine.items()}
    forked["scout"]["1"] = "deadbeefdeadbeef"
    problems = workspace.check_compatible(_manifest(forked))
    assert len(problems) == 1
    assert "differs" in problems[0]
    assert "deadbeefdeadbeef" in problems[0]


def test_a_workspace_from_an_older_build_is_accepted():
    """The ordinary case: the outstanding migrations run after the restore."""
    mine = workspace._known_digests()
    behind = {k: dict(v) for k, v in mine.items()}
    highest = max(int(v) for v in behind["scout"])
    del behind["scout"][str(highest)]
    assert workspace.check_compatible(_manifest(behind)) == []


def test_a_module_this_build_lacks_is_refused():
    mine = workspace._known_digests()
    extra = {k: dict(v) for k, v in mine.items()}
    extra["cadastre"] = {"1": "0" * 16}
    problems = workspace.check_compatible(_manifest(extra))
    assert any("does not have" in p for p in problems)


# --- the small pieces ---------------------------------------------------------


def test_borrowing_the_binary_from_the_container_moves_the_port():
    """The published port belongs to nothing inside the container."""
    host = workspace.Tool(["pg_dump"], False)
    inside = workspace.Tool(["docker", "exec", "-i", "arpenteur-db", "pg_dump"], True)
    url = "postgresql://arpenteur:arpenteur@127.0.0.1:5433/arpenteur"
    assert host.dsn(url) == url
    assert inside.dsn(url) == (
        "postgresql://arpenteur:arpenteur@127.0.0.1:5432/arpenteur"
    )


def test_a_password_is_not_printed_in_an_error():
    hidden = workspace._safe("postgresql://arpenteur:hunter2@127.0.0.1:5433/arpenteur")
    assert "hunter2" not in hidden
    assert "127.0.0.1:5433/arpenteur" in hidden


def test_counting_skips_a_module_that_is_not_installed(target):
    """A disabled module has no schema, and asking anyway is not an error."""
    conn = kernel_db.connect(target)
    try:
        assert workspace.counts(conn) == {}
    finally:
        conn.close()


def test_an_export_leaves_no_half_file_behind(source, settings_for, tmp_path):
    out = tmp_path / "w.arpw"
    settings = settings_for(source)
    conn = kernel_db.connect(source)
    try:
        workspace.export(settings, conn, out)
    finally:
        conn.close()
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert leftovers == []
    assert out.exists()


def test_the_manifest_records_the_collation(source, settings_for, tmp_path):
    """Company names are ordered by name and a different collation reorders them."""
    settings = settings_for(source)
    conn = kernel_db.connect(source)
    try:
        report = workspace.export(settings, conn, tmp_path / "w.arpw")
    finally:
        conn.close()
    assert report.manifest is not None
    assert report.manifest.collate
    assert report.manifest.ctype


def test_the_manifest_is_readable_without_unpacking(source, settings_for, tmp_path):
    settings = settings_for(source)
    conn = kernel_db.connect(source)
    try:
        report = workspace.export(settings, conn, tmp_path / "w.arpw")
    finally:
        conn.close()
    manifest = workspace.inspect(report.path)
    assert manifest.counts["core.company"] == 3
    assert "postgis" in manifest.extensions
    assert "written" in manifest.describe()


def test_a_note_survives_in_the_manifest(source, settings_for, tmp_path):
    settings = settings_for(source)
    conn = kernel_db.connect(source)
    try:
        report = workspace.export(
            settings, conn, tmp_path / "w.arpw", note="before the Mac move"
        )
    finally:
        conn.close()
    assert workspace.inspect(report.path).note == "before the Mac move"


def test_the_dump_is_stored_rather_than_deflated_twice(source, settings_for, tmp_path):
    settings = settings_for(source)
    conn = kernel_db.connect(source)
    try:
        report = workspace.export(settings, conn, tmp_path / "w.arpw")
    finally:
        conn.close()
    with zipfile.ZipFile(report.path) as zf:
        info = zf.getinfo(workspace.DUMP)
        assert info.compress_type == zipfile.ZIP_STORED
        manifest = zf.getinfo(workspace.MANIFEST)
        assert manifest.compress_type == zipfile.ZIP_DEFLATED


def test_the_environment_can_name_the_binary(monkeypatch):
    monkeypatch.setenv("ARPENTEUR_PG_DUMP", "/opt/pg/bin/pg_dump")
    tool = workspace._tool("pg_dump")
    assert tool.command == ["/opt/pg/bin/pg_dump"]
    assert tool.in_container is False


def test_a_missing_binary_says_how_to_fix_it(monkeypatch):
    monkeypatch.delenv("ARPENTEUR_PG_DUMP", raising=False)
    monkeypatch.setattr(workspace.shutil, "which", lambda _name: None)
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace._tool("pg_dump")
    assert "ARPENTEUR_PG_DUMP" in str(caught.value)



def test_every_counted_table_exists(pg_url):
    """A name spelt wrongly is skipped, not reported, so it must be impossible.

    `crm.document` sat in COUNTED and matched nothing, which meant the client
    documents were never part of the verification that claims to cover them.
    """
    conn = kernel_db.connect(pg_url)
    try:
        present = {
            f"{row['table_schema']}.{row['table_name']}"
            for row in conn.execute(
                "SELECT table_schema, table_name FROM information_schema.tables"
                " WHERE table_type = 'BASE TABLE'"
            )
        }
    finally:
        conn.close()
    missing = sorted(set(workspace.COUNTED) - present)
    assert missing == [], f"COUNTED names no such table: {missing}"


def test_importing_into_a_database_that_does_not_exist_yet(
    source, settings_for, tmp_path
):
    """The first import on a new machine has no database to look at."""
    out = tmp_path / "w.arpw"
    conn = kernel_db.connect(source)
    try:
        workspace.export(settings_for(source), conn, out)
    finally:
        conn.close()

    absent = _dsn_for("arpenteur_ws_absent")
    _drop("arpenteur_ws_absent")
    try:
        landed = workspace.import_(settings_for(absent), out)
        assert landed.mismatches == {}
        assert landed.verified["core.company"] == (3, 3)
    finally:
        _drop("arpenteur_ws_absent")


def test_an_unreachable_server_is_not_mistaken_for_an_empty_one(settings_for):
    """A refused connection must not read as a database with nothing in it."""
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace._standing(
            "postgresql://arpenteur:arpenteur@127.0.0.1:1/arpenteur"
        )
    assert "not reachable" in str(caught.value)


def test_an_attachment_that_climbs_out_of_the_data_directory_is_refused(
    source, settings_for, tmp_path
):
    """A zip member carries whatever name was written into it."""
    out = tmp_path / "w.arpw"
    conn = kernel_db.connect(source)
    try:
        workspace.export(settings_for(source), conn, out)
    finally:
        conn.close()

    doctored = tmp_path / "evil.arpw"
    with zipfile.ZipFile(out) as src, zipfile.ZipFile(doctored, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        dst.writestr("attachments/../../../tmp/escaped.pdf", b"out")

    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.import_(settings_for(_dsn_for("arpenteur_ws_dst")), doctored)
    assert "outside" in str(caught.value)
    assert not Path("/tmp/escaped.pdf").exists()


def test_an_absolute_attachment_name_is_refused(source, settings_for, tmp_path):
    """`base / "/etc/x"` is `/etc/x`: the base is discarded, not joined."""
    out = tmp_path / "w.arpw"
    conn = kernel_db.connect(source)
    try:
        workspace.export(settings_for(source), conn, out)
    finally:
        conn.close()

    doctored = tmp_path / "abs.arpw"
    with zipfile.ZipFile(out) as src, zipfile.ZipFile(doctored, "w") as dst:
        for item in src.infolist():
            dst.writestr(item, src.read(item.filename))
        dst.writestr("attachments//tmp/arpenteur-absolute-probe", b"out")

    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.import_(settings_for(_dsn_for("arpenteur_ws_dst")), doctored)
    assert "outside" in str(caught.value)
    assert not Path("/tmp/arpenteur-absolute-probe").exists()


def test_a_missing_restore_binary_leaves_the_database_alone(
    source, settings_for, tmp_path, monkeypatch
):
    """Nothing destructive may run before everything that can refuse has."""
    out = tmp_path / "w.arpw"
    conn = kernel_db.connect(source)
    try:
        workspace.export(settings_for(source), conn, out)
    finally:
        conn.close()

    real = workspace.shutil.which

    def absent(name, *a, **k):
        return None if name in ("pg_restore", "docker") else real(name, *a, **k)

    monkeypatch.setattr(workspace.shutil, "which", absent)
    with pytest.raises(workspace.WorkspaceError) as caught:
        workspace.import_(settings_for(source), out, force=True)
    assert "pg_restore" in str(caught.value)

    # The three companies are still there, which is the whole point.
    conn = kernel_db.connect(source)
    try:
        assert conn.execute(
            "SELECT count(*) AS n FROM core.company"
        ).fetchone()["n"] == 3
    finally:
        conn.close()
