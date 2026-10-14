# Moving an installation to another machine

> The figures on this page were measured on the first deployment, in 2026, over a survey of five cantons of French-speaking Switzerland. Yours will differ.

The code is in git. The data is not: it lives in a Docker volume and under
`data/`, both outside the repository on purpose. A clone on a new machine is
therefore an empty Arpenteur, and `workspace` is what fills it.

```bash
uv run arpenteur workspace export --out ~/arpenteur-2026-10-07.arpw
uv run arpenteur workspace inspect ~/arpenteur-2026-10-07.arpw
uv run arpenteur workspace import  ~/arpenteur-2026-10-07.arpw
```

The file is a zip holding a `pg_dump` of the database, the documents clients
have sent, `arpenteur.toml`, and a manifest. It carried 744'370 rows in 66 MB
the first time it was used, and the export took seven seconds.

A dump on its own would be enough to restore data and not enough to trust it.
It does not say which modules were running, nor whether the code about to read
it is the code that wrote it, and a restore that succeeds against a schema the
application has never seen is worse than one that fails. So the manifest
carries every applied migration digest, the same ones `kernel/db.py` already
compares on every startup, and the import reads them before touching anything:

- A file holding a migration this build does not have is refused, because the
  schema would be ahead of the code.
- A file whose shared migration has a different body is refused, since that is
  the same version number over a different schema and nothing downstream would
  notice.
- A file missing migrations this build has is the ordinary case, and they run
  after the restore.

The restore is then checked against the row counts the manifest promised,
table by table, and a mismatch is an error rather than a warning.

Two details are not conveniences. The dump is logical rather than a copy of
the data directory, because a PostgreSQL data directory is not portable across
CPU architectures and the first real use of this was Windows on x86 to a Mac
on arm64. And the database is recreated from `template0`: the PostGIS image
installs its extensions into `template1`, so a database made the ordinary way
already holds `postgis`, `topology` and `tiger`, and restoring an archive that
also creates them buries the real errors in a hundred harmless ones.

`.env` is left out unless `--with-secrets` is given, because the file is made
to travel and that is where the Zefix and Infomaniak passwords are. Nothing in
the archive is encrypted: the dump holds personal data and the attachments may
hold documents under NDA, so it wants protecting in transit by a tool built
for that rather than by cryptography folded into an archiver.

```bash
gpg -c --cipher-algo AES256 arpenteur-2026-10-07.arpw
```

Importing into a database that already holds rows is refused unless `--force`
is given, and the refusal says what it counted.
