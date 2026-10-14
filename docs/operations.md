# Running it

Backups, updates, moving to another machine, uninstalling, and what to do when something refuses.

## Backup and restore

The code is in git. The data is not: it lives in the Docker volume and under `data/`, both outside the repository on purpose. One command carries all of it:

```bash
uv run arpenteur workspace export --out ~/arpenteur-2026-10-14.arpw
```

The file is a zip holding a logical dump of the database, the documents clients have sent, the configuration file and a manifest. `--note` records a line in the manifest; `--no-attachments` leaves the documents out; `--with-secrets` adds `.env`, which is left out by default because the file is made to travel and that is where the passwords are.

Nothing in the archive is encrypted. The dump holds personal data and the attachments may hold documents under a non-disclosure agreement, so protect it in transit with a tool built for that:

```bash
gpg -c --cipher-algo AES256 arpenteur-2026-10-14.arpw
```

To read what an archive holds without touching anything:

```bash
uv run arpenteur workspace inspect ~/arpenteur-2026-10-14.arpw
```

To restore on a machine where the database is empty:

```bash
uv run arpenteur workspace import ~/arpenteur-2026-10-14.arpw
```

The import reads the manifest before touching anything and refuses an archive whose schema is ahead of the code, or whose migrations differ from the code's. Migrations the archive lacks run after the restore. The restore is then checked against the row counts the manifest promised, table by table. Importing into a database that already holds rows is refused unless `--force` is given, and the refusal says what it counted.

A weekly export, encrypted and copied off the machine, is the whole backup plan: the archive is logical, so it restores across operating systems and CPU architectures.

## Updating the code

```bash
git pull
uv sync
uv run arpenteur init-db
```

The third command applies the migrations that are new and prints the version of every module. Every command and the console apply them too on their first connection, so running it by hand is a way of seeing the result rather than a requirement.

**A migration that changed**

The database records a digest of every migration it applied, and compares it at every start. A refusal reads:

```text
error: clients migration 0011_wave_order has changed since it was applied (a1b2c3d4e5f60718 -> 1f2e3d4c5b6a7980). Migrations are immutable once run; add a new one.
```

A migration file edited after it was applied cannot be reconciled by the program, because the database's schema and the file no longer describe the same thing. Restore the file from git to the version that was applied. If the change was yours and wanted, put it in a new numbered migration file instead and leave the applied one as it was.

## Moving to another machine

Install Arpenteur on the new machine as [the installation page](install.md) describes, stop at the doctor, then import the archive exported from the old one. Copy `.env` by hand, or export with `--with-secrets`. The sign-in of the `claude` command is per machine: run `claude` once on the new one.

## Uninstalling

```bash
docker compose down -v
```

removes the database container and its volume, which is where every row lives. Delete the project folder for the code, the virtual environment, the cache under `data/` and the documents. The `claude` command and uv are removed the way they were installed. Nothing is written anywhere else on the machine.

## When something refuses

**The database is not reachable**

```text
error: cannot connect to PostgreSQL at 127.0.0.1:5433 (Connection refused). Start it with `docker compose up -d`.
```

On Windows, Docker Desktop must be running first; [the installation page](install.md) says how to start it from the terminal. A test suite that finishes in under a second with hundreds of tests skipped is the same cause: without the database, every test that needs it skips.

**The register refuses**

The sweep, the detail fetch and the sync say "ZEFIX_USER and ZEFIX_PASSWORD are not set" until the credentials are in `.env`. Once they are, a 401 from the register means they are wrong or not yet active; the email that issued them says from when.

**The model is not signed in**

The doctor says "The claude command is installed but not signed in". Run `claude` in a terminal and sign in. On a machine that cannot sign in, set `ANTHROPIC_API_KEY` and choose the key under Settings, Model access.

**The mailbox refuses the password**

Most Swiss hosts refuse the account password over IMAP and issue an application password from their web interface. Use that one as `MAIL_PASSWORD`.

**A campaign cannot render**

The wave page names the reason: no mailbox to receive the opt-out, no signature written, replies going to an address Arpenteur does not read, or a message file that cannot be sent. Each names the page to fix it on.

**An invoice cannot be issued**

Under Settings, Identity, the name and a valid IBAN are needed before the payment part can be drawn. The invoice page says which is missing.

**A run shows as running and nothing is running**

The console settles its own runs as interrupted at the next start. A run started in a terminal that was closed is settled from its page on History, with "Mark as interrupted".

**A settings file that is not where it should be**

Every command runs from the project folder, where `arpenteur.toml` is. The error names the path it expected.

**A page answers with an error**

The page shows the message, and History has the detail of the run if one was involved. The log of the console itself is in the terminal that started it.
