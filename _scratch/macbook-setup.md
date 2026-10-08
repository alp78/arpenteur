# Standing up Arpenteur on a MacBook Pro

Everything below was run and measured on the Windows machine before being written down. The figures are that machine's, so they are what the Mac should show when it has finished.

Two things travel separately. The code is in git and arrives with a clone. The data is not in git: it lives in a Docker volume and under `data/`, both outside the repository deliberately, so a clone on its own gives an empty Arpenteur. The workspace file is what fills it.

`data/` is deliberately not in git, and its size is the reason as much as its
content. Measured on 08.10.2026:

| what | size | travels? |
|---|---|---|
| `data/http-cache` | 3.4 GB in 64'610 files | no, refetchable |
| `data/arpenteur.sqlite3` | 90 MB | no, pre-Postgres and dead |
| `data/attachments` | 2.5 MB in 12 files | yes, in the workspace |
| `data/*.jsonl` | 2.2 MB in 3 files | yes, in the workspace |
| `data/logs` | 988 KB | no, machine-local |

So the Mac needs 4.7 MB of files beside the database, not 3.5 GB, and the
workspace file carries all of it. Committing `data/` would put the cache in
every clone and the FBG attachments in the remote, which is the fault the
`.gitignore` comment above that line already names.

## What the workspace file holds

| Piece | Size |
|---|---|
| the database, as a `pg_dump` custom archive | 64.0 MB compressed, from 603 MB live |
| documents clients have sent | 12 files, 2.4 MB |
| `arpenteur.toml` as a reference copy | 25.6 KB |
| the manifest | 2.0 KB |
| total | 66.1 MB |

Inside the database, 744'370 rows across the 17 tables the import verifies, among them 598'036 companies, 11'842 email contacts, 27'789 segment rows and 15'475 address hunts. The whole export takes about seven seconds and the restore about twelve.

`.env` is not in the file. The Zefix and Infomaniak passwords stay behind unless `--with-secrets` is given, and the section on secrets below says how to carry them across instead.

## On the Windows machine

Docker has to be running, since the export reads the database.

```bash
cd /home/alex/arpenteur
uv run arpenteur workspace export --out ~/arpenteur-$(date +%F).arpw --note "move to the MacBook"
uv run arpenteur workspace inspect ~/arpenteur-$(date +%F).arpw
```

The `inspect` output is worth reading before carrying the file anywhere. It prints the row counts per table, and those are the numbers the Mac will be checked against.

The file holds email addresses of identifiable people under the nLPD, and the attachments include documents covered by the FBG non-disclosure agreement. Encrypt it before it leaves the machine. GnuPG 2.4.4 is already installed here, and the passphrase is prompted for rather than passed on the command line.

```bash
gpg -c --cipher-algo AES256 ~/arpenteur-$(date +%F).arpw
```

That writes `arpenteur-2026-10-07.arpw.gpg` beside the original. Carry the `.gpg`, and delete the plain file once the Mac has it.

Copy it over the local network or on a USB stick. Not through a public file-sharing service, for the reason above.

## The tools on the Mac

Homebrew first, if the machine does not have it.

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Then the four things Arpenteur needs. GnuPG is here to decrypt the workspace file, and Node is here only because the Claude command-line tool is distributed through npm.

```bash
brew install uv git gnupg node
brew install --cask docker
```

`uv` brings its own Python, so nothing needs to be installed to satisfy `requires-python = ">=3.13"`.

Open Docker Desktop once from Applications and leave it running. It has to be running before the database container will start.

## The clone

```bash
git clone git@github.com:alp78/arpenteur.git ~/arpenteur
cd ~/arpenteur
uv sync
```

If the SSH key has not been carried over, clone over HTTPS instead and set the remote later.

## The Apple Silicon problem, and the fix

The compose file pins `postgis/postgis:16-3.4`. That image is published for amd64 only: a query against the Docker registry returns a single-architecture manifest, and the same holds for `16-3.5`, `17-3.5` and the `-alpine` tags. On an M-series Mac it therefore runs under emulation, which is slower and is the wrong property for the system of record.

`imresamu/postgis:16-3.4` is the same PostgreSQL 16 with PostGIS 3.4 built for both amd64 and arm64. Point the Mac at it with an override file, which `docker compose` reads automatically and which leaves the tracked compose file alone so the Windows machine keeps working.

```bash
cat > ~/arpenteur/docker-compose.override.yml <<'YAML'
services:
  db:
    image: imresamu/postgis:16-3.4
YAML
echo "docker-compose.override.yml" >> ~/arpenteur/.git/info/exclude
docker compose up -d
```

Wait for the container to report healthy before going on.

```bash
docker compose ps
docker exec arpenteur-db pg_isready -U arpenteur -d arpenteur
```

Should the override give trouble, the fallback is emulation: delete the override file, turn on **Use Rosetta for x86_64/amd64 emulation** in Docker Desktop under Settings, General, and add `platform: linux/amd64` under `db` instead.

## Restoring the data

Decrypt first. GnuPG writes the archive without the `.gpg` suffix.

```bash
cd ~/arpenteur
gpg -o ~/arpenteur-2026-10-07.arpw -d ~/arpenteur-2026-10-07.arpw.gpg
uv run arpenteur workspace inspect ~/arpenteur-2026-10-07.arpw
uv run arpenteur workspace import ~/arpenteur-2026-10-07.arpw
```

The import does not need PostgreSQL client tools installed on the Mac. When `pg_restore` is absent from the PATH it borrows the one inside the container and rewrites the connection string from the published port 5433 to the 5432 the server listens on internally. That route was tested on the Windows machine by hiding the host binaries, and it restored all 598'036 companies with no count mismatch.

Three things happen in order, and each can refuse. The manifest is compared against the migrations this clone carries, so a workspace written by a newer Arpenteur stops here rather than restoring a schema the code has never seen. The database is then recreated from `template0`, because the PostGIS image installs its extensions into `template1` and an archive that also creates them would otherwise bury its real errors in harmless ones. Afterwards the row counts are compared table by table against what the manifest promised, and a mismatch is an error.

A successful import prints the tables it verified and the total.

```text
imported arpenteur-2026-10-07.arpw
  17 table(s) verified against the manifest
  744,370 rows
```

## Checking it independently

The import checks itself, so this checks the import. Run it against the Mac and compare with the figures in the first section.

```bash
docker exec arpenteur-db psql -U arpenteur -d arpenteur -tAc "
SELECT 'companies='||(SELECT count(*) FROM core.company)
  ||' contacts='||(SELECT count(*) FROM core.contact)
  ||' hunts='||(SELECT count(*) FROM intel.address_hunt)
  ||' matviews='||(SELECT count(*) FROM pg_matviews)
  ||' schemas='||(SELECT count(*) FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema')
  ||' migrations='||(SELECT count(*) FROM kernel.migration)"
```

Expected on the Mac:

```text
companies=598036 contacts=11842 hunts=15475 matviews=4 schemas=12 migrations=27
```

The collation matters on its own. Company names carry accents and the survey is ordered by name, so a database restored under a different collation sorts differently and nothing announces it. The compose file pins `C.UTF-8` for exactly this reason and the import carries the setting over from the manifest.

```bash
docker exec arpenteur-db psql -U arpenteur -d arpenteur -tAc "SELECT datcollate FROM pg_database WHERE datname='arpenteur'"
docker exec arpenteur-db psql -U arpenteur -d arpenteur -tAc "SELECT round(ST_Distance(point, ST_SetSRID(ST_MakePoint(6.63,46.52),4326)::geography)) FROM geo.establishment WHERE point IS NOT NULL ORDER BY 1 LIMIT 1"
```

The first returns `C.UTF-8`. The second returns `2520`, which proves PostGIS is working on the arm64 image rather than merely installed.

Then the suite, which is the real verdict. It takes about four minutes and creates its own test database.

```bash
uv run pytest
```

829 tests passed on the Windows machine.

## The secrets, which the file left behind

Nine keys live in `.env` and none of them are in the workspace archive. Retype them on the Mac, or carry the file across the same encrypted channel as the archive.

```bash
cd ~/arpenteur
cp .env.example .env
```

`ZEFIX_USER` and `ZEFIX_PASSWORD` come from the credentials the federal register issued by email. The five `INFOMANIAK_*` keys are the mail account used by the console. Without the Zefix pair the survey cannot fetch new companies, though everything already in the database stays readable.

`arpenteur.toml` arrives with the clone, so nothing needs doing there. The copy inside the workspace archive is a reference rather than something the import writes, which is deliberate: overwriting a working configuration on restore would be the wrong default. Read it if the two machines have drifted.

```bash
unzip -p ~/arpenteur-2026-10-07.arpw arpenteur.toml | diff - arpenteur.toml
```

## The paid search tier

`llm.auth_mode` is set to `subscription` in `arpenteur.toml`, which means the address hunt spends the Max plan through `claude -p` rather than the metered API. The OAuth credentials do not travel: on macOS they go into the Keychain, so the sign-in has to happen on the Mac.

```bash
npm install -g @anthropic-ai/claude-code
claude
```

Sign in at that prompt, then leave it. A probe confirms the transport before any survey work depends on it.

```bash
claude -p "reply with the single word ready" --output-format json | head -c 400
```

## Running it

```bash
cd ~/arpenteur
uv run arpenteur stats
uv run arpenteur console
```

The console serves on `http://127.0.0.1:8000`. It binds to localhost and has no authentication, because it holds contact details for thousands of companies and, where sole proprietorships are included, for identifiable people.

## If something goes wrong

**The import refuses the file as newer**

The clone is behind the machine that wrote the archive. Run `git pull` and import again. The refusal is deliberate: the alternative is a schema the code has never seen.

**The import says the database already holds data**

Expected on a second run. It prints what it counted and stops. Pass `--force` to replace it.

**`pg_restore` is not found and no container is running**

Start Docker Desktop. Failing that, `brew install libpq` and add its `bin` to the PATH, or set `ARPENTEUR_PG_RESTORE` to the binary.

**The container will not start, or stops immediately**

Read its own log before changing anything.

```bash
docker compose logs db | tail -40
```

An architecture mismatch names the platform in that output. An existing volume from a failed attempt is removed with `docker compose down -v`, which destroys the database and means importing again.

**A collation warning during the import**

The import says so and takes the server's default rather than refusing. Names may then sort differently from the Windows machine. Both images here are Debian-based and carry `C.UTF-8`, so this should not appear; if it does, the override file is probably pointing at an Alpine image.

**The console starts but pages are blank**

htmx and MapLibre are loaded from a CDN, so the interface needs the network. Nothing else about the application does.
