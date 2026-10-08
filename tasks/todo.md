# Workspace export and import

One file that carries an installation's whole working set between machines, and
the instructions to stand that installation up on a Mac from scratch.

## Why it is not just `pg_dump`

A dump restores data. It does not say which schema the data belongs to, which
modules were running, or whether the code reading it afterwards is the same
code that wrote it. Those three omissions are how a restore succeeds and the
application is still wrong, so the file carries a manifest and the import
checks it before it touches anything.

## Decisions

- **A zip, not a tar.** The manifest must be readable without unpacking 64 MB,
  which is random access, which is zip. The dump inside is already compressed
  by `pg_dump`, so it is stored rather than deflated twice.
- **A logical dump.** A PostgreSQL data directory is not portable across CPU
  architectures and the first real use of this is Windows x86 to an arm64 Mac.
- **The manifest carries every applied migration digest.** `kernel/db.py`
  already computes them and already refuses a migration whose body changed.
  The same comparison across machines answers a question a dump cannot: is the
  code about to read this data the code that wrote it.
- **A file from a newer Arpenteur is refused.** A file holding a migration this
  build does not have cannot be restored safely, and restoring most of it is
  worse than restoring none.
- **A file from an older Arpenteur is migrated forward.** The outstanding
  migrations run after the restore, which is the normal path and not a special
  case.
- **Counts are verified after the restore.** The import proves itself rather
  than trusting an exit code.
- **Secrets are left out unless asked for.** This file is made to travel on a
  USB stick. `.env` holds the Zefix and Infomaniak passwords and they do not
  go in by default.
- **No encryption in the exporter.** The dump holds personal data under the
  nLPD and `data/attachments` holds documents under NDA, so the file wants
  protecting in transit — by a tool built for that, named in the instructions,
  not by crypto rolled into an archiver.
- **Import into a populated database is refused without `--force`**, and says
  what it would have destroyed.

## File layout

```
arpenteur-2026-10-07.arpw        a zip
├── manifest.json
├── database.dump               pg_dump --format=custom --compress=9
├── arpenteur.toml
├── .env                        only with --with-secrets
└── attachments/ab/abcd...pdf
```

## Tasks

- [x] `arpenteur/workspace.py` — manifest, export, inspect, import
- [x] `pg_dump`/`pg_restore` resolution: explicit setting, then PATH, then the
      running container, with a usable error when none is found
- [x] `arpenteur/cli.py` — `cmd_workspace` with `export`, `inspect`, `import`
- [x] `tests/test_workspace.py`
- [x] `README.md` — a section after the database one
- [x] `_scratch/macbook-setup.md` — the from-scratch Mac instructions

## Verification

```bash
uv run pytest tests/test_workspace.py tests/test_modules.py
uv run pytest
uv run ruff check arpenteur tests scripts
uv run arpenteur workspace export --out /tmp/w.arpw
uv run arpenteur workspace inspect /tmp/w.arpw
```

End to end against a scratch database, since a round trip is the only check
that means anything.

## Review

### What was built

`arpenteur/workspace.py`, 530 lines. `cmd_workspace` in the CLI with `export`,
`inspect` and `import`. 28 tests in `tests/test_workspace.py`. A README section
and `_scratch/macbook-setup.md`.

### What the round trip proved

The live database exported in 7 seconds to a 66.1 MB file holding 744'370 rows
across 17 tables, and restored into a fresh database in 11.6 seconds with every
count matching. Checked again from outside the exporter: both databases report
598'036 companies, 11'842 contacts, 4 populated materialized views, 12 schemas,
27 migrations, the same `C.UTF-8` collation, the same `ST_Distance` of 2520
metres, and the same name ordering. Full suite 829 passed.

The container route was tested separately by hiding the host binaries, because
that is the route a Mac without the PostgreSQL client tools will take. It
restored all 598'036 companies with no mismatch.

### Three faults the work found in itself

`crm.document` sat in `COUNTED` and matched no table, so the client documents
were never part of the verification that claimed to cover them. `counts()`
skips a table it cannot find, which is right for a disabled module and wrong
for a typo, and the skip hid this. `test_every_counted_table_exists` now reads
the catalogue, and the list went from 12 names to 17.

The occupancy check connected to the target before creating it, so importing
into a database that did not exist yet raised a raw psycopg error. That is the
first import on every new machine, which is the whole point of the feature.

Borrowing `pg_dump` from the container while passing the host's connection
string sends the tool to port 5433, which inside the container belongs to
nothing. `Tool.dsn` rewrites it to 5432.

### What was left out

Encryption. `gpg -c --cipher-algo AES256` is named instead, in the CLI output
and in the instructions, and the round trip was checked byte-identical.

The import does not write `arpenteur.toml` or `.env` back. Overwriting a
working configuration on restore is the wrong default, and the clone carries
the config anyway. The instructions say how to read the archived copy.

A workspace carrying a `geo` schema imported into a build without PostGIS
fails at the restore with Postgres's own error rather than at the manifest
check. One `if`, once a build without PostGIS exists.

`tasks/` is untracked and was left that way rather than added to the repository.
