"""Move a SQLite arpenteur database into PostgreSQL. One-off, idempotent.

Run once, after the schema is migrated:

    uv run python scripts/import_sqlite.py data/arpenteur.sqlite3

Three things it does that a naive copy would not.

**It keeps the ids.** Every table here is referenced by integer id from
somewhere else, and renumbering would mean rewriting every foreign key in
flight. The identity sequences are reset afterwards, or the next insert would
collide with a row that already has that number.

**It converts what SQLite could not type.** JSON columns were text and are
jsonb now; booleans were 0 and 1; timestamps were ISO strings in a text column
and are TIMESTAMPTZ. A date stored as the empty string, which SQLite accepted
happily, becomes NULL rather than an error halfway through 184'939 rows.

**It reports counts on both sides.** The only useful verification of a copy is
that both databases agree afterwards, table by table.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arpenteur.kernel import db as kernel  # noqa: E402
from arpenteur.kernel import modules  # noqa: E402

# (sqlite table, postgres table, json columns, boolean columns, timestamp
# columns, date columns). Ordered so a row's referents already exist.
TABLES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]], ...] = (
    ("company", "core.company", ("address_json",), (),
     ("fetched_at", "first_seen", "hydrated_at", "stale_since"), ("last_mutation_date",)),
    ("person", "core.person", (), (), ("fetched_at",), ("last_seen_date",)),
    ("contact", "core.contact", (), ("suppressed",), ("fetched_at",), ()),
    ("publication", "core.publication", (), (), ("fetched_at",),
     ("sogc_date", "journal_date")),
    ("segment", "intel.segment", (), (), ("classified_at",), ()),
    ("site", "intel.site",
     ("offerings", "signals", "partnerships", "tech_stack", "restricted"), (),
     ("fetched_at", "reached_at"), ()),
    ("fit", "intel.fit", ("against", "matched"), (), ("scored_at",), ()),
    ("prospect", "intel.prospect", (), (), ("created_at", "updated_at"), ("next_at",)),
    ("note", "intel.note", (), (), ("occurred_at", "created_at"), ()),
    ("draft", "intel.draft", (), (),
     ("created_at", "reviewed_at", "sent_at"), ()),
    ("sweep", "intel.sweep", (), ("deepened",), ("completed_at",), ()),
    ("sync_day", "intel.sync_day", (), (), ("fetched_at",), ("sogc_date",)),
    ("run", "intel.run", ("args_json", "stats_json"), (),
     ("started_at", "finished_at"), ()),
    ("run_line", "intel.run_line", (), (), ("at",), ()),
    ("setting", "intel.setting", ("value_json",), (), ("updated_at",), ()),
    ("client", "crm.client", (), (), ("created_at", "updated_at"), ("since",)),
    ("project", "crm.project", (), (), ("created_at", "updated_at"),
     ("started_at", "due_at", "closed_at")),
    ("event", "crm.interaction", (), (),
     ("occurred_at", "ends_at", "created_at"), ()),
    ("dossier", "crm.dossier", (), (), ("updated_at",), ()),
    ("approach", "crm.approach",
     ("read_json", "points_json", "services_json", "pricing_json", "risks_json",
      "questions_json"), (), ("created_at", "accepted_at"), ()),
    ("attachment", "crm.attachment", (), (), ("created_at", "extracted_at"), ()),
    ("briefing", "crm.briefing", ("read_json", "agenda_json"), (),
     ("created_at",), ()),
    ("glossary", "crm.glossary", (), ("known",), ("first_seen", "updated_at"), ()),
)


def convert(
    row: sqlite3.Row, json_cols: tuple[str, ...], bool_cols: tuple[str, ...],
    ts_cols: tuple[str, ...], date_cols: tuple[str, ...],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key in json_cols:
            if isinstance(value, str) and value.strip():
                try:
                    value = kernel.json_param(json.loads(value))
                except ValueError:
                    value = kernel.json_param(value)
            else:
                value = None
        elif key in bool_cols:
            value = bool(value) if value is not None else None
        elif key in ts_cols or key in date_cols:
            # SQLite stored these as text and accepted anything, including the
            # empty string. Postgres will not, and a run that dies on row
            # 90'000 has to be restarted from the beginning.
            value = value if isinstance(value, str) and value.strip() else None
        out[key] = value
    return out


def copy_table(
    src: sqlite3.Connection, dst: kernel.Connection, spec: tuple, *, batch: int = 2000
) -> tuple[int, int]:
    sqlite_name, pg_name, json_cols, bool_cols, ts_cols, date_cols = spec
    exists = src.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (sqlite_name,)
    ).fetchone()
    if not exists:
        return 0, 0

    pg_columns = {
        r["column_name"]
        for r in dst.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = %s",
            tuple(pg_name.split(".")),
        )
    }
    # Columns the new schema requires and fills itself. A row written before
    # such a column existed carries NULL, and inserting that NULL fails where
    # simply not mentioning the column would have let the default apply. This
    # is not hypothetical: `run.started_by` arrived after the first sweeps.
    defaulted = {
        r["column_name"]
        for r in dst.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name = %s"
            "   AND is_nullable = 'NO' AND column_default IS NOT NULL",
            tuple(pg_name.split(".")),
        )
    }

    rows = src.execute(f"SELECT * FROM {sqlite_name}").fetchall()
    if not rows:
        return 0, 0

    # Only columns both sides have. The SQLite schema grew in ways the fresh
    # baseline tidied, and a column that no longer exists is not a reason to
    # abandon the row that carries it.
    keys = [k for k in rows[0].keys() if k in pg_columns]
    written = 0
    for start in range(0, len(rows), batch):
        chunk = [
            {k: v for k, v in convert(r, json_cols, bool_cols, ts_cols, date_cols).items()
             if k in keys}
            for r in rows[start : start + batch]
        ]
        # Grouped by which required columns this row leaves empty, so each
        # group can omit exactly those and let Postgres supply them. Almost
        # always one group; the grouping costs nothing when it is.
        groups: dict[frozenset[str], list[dict[str, Any]]] = {}
        for row in chunk:
            missing = frozenset(k for k in defaulted if row.get(k) is None)
            groups.setdefault(missing, []).append(row)
        for missing, payload in groups.items():
            used = [k for k in keys if k not in missing]
            columns = ", ".join(f'"{k}"' for k in used)
            marks = ", ".join(f"%({k})s" for k in used)
            with dst.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO {pg_name} ({columns}) VALUES ({marks})"
                    " ON CONFLICT DO NOTHING",
                    [{k: row[k] for k in used} for row in payload],
                )
            dst.commit()
            written += len(payload)
    return len(rows), written


def reset_identity(dst: kernel.Connection, pg_name: str) -> None:
    """Move the sequence past the highest imported id.

    Without this the next insert asks for 1, finds it taken, and fails. It is
    the classic omission when ids are carried across.
    """
    has_id = dst.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_schema = %s"
        " AND table_name = %s AND column_name = 'id'",
        tuple(pg_name.split(".")),
    ).fetchone()
    if not has_id:
        return
    dst.execute(
        f"SELECT setval(pg_get_serial_sequence('{pg_name}', 'id'),"
        f" GREATEST((SELECT COALESCE(MAX(id), 0) FROM {pg_name}), 1))"
    )
    dst.commit()


def main(path: str) -> int:
    source = Path(path)
    if not source.is_file():
        print(f"no such file: {source}", file=sys.stderr)
        return 1

    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = kernel.connect()
    kernel.ensure_extensions(dst)
    kernel.migrate(dst, modules.load(modules.AVAILABLE).migration_dirs())

    print(f"{'table':<20} {'sqlite':>9} {'written':>9} {'postgres':>9}")
    ok = True
    for spec in TABLES:
        read, written = copy_table(src, dst, spec)
        reset_identity(dst, spec[1])
        here = dst.execute(f"SELECT COUNT(*) AS n FROM {spec[1]}").fetchone()["n"]
        flag = "" if here == read else "   <- MISMATCH"
        if here != read:
            ok = False
        print(f"{spec[1]:<20} {read:>9,} {written:>9,} {here:>9,}{flag}")

    src.close()
    dst.close()
    print("\nEvery table matches." if ok else "\nSome tables differ; see above.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "data/arpenteur.sqlite3"))
