# Arpenteur: pipeline foundation

## Context

Arpenteur surveys the Swiss SME market the way an arpenteur surveys land:
parcel by parcel, from public sources, into a qualified map (who exists, which
segment, who decides, how to reach them) with personalised outreach drafted for
human review. The repo at `~/arpenteur` (WSL Ubuntu, ext4) holds only a README
and .gitignore; this plan builds the foundation: project scaffold, storage,
shared HTTP machinery, a complete Zefix source stage, the LLM provider
abstraction, and a first working `profile` pass that classifies companies from
their registered purpose text alone.

Decisions already made with the user:
- **LLM**: Anthropic (Haiku for classification) behind a thin provider
  interface so Infomaniak can be swapped in later. Key via `.env`.
- **Sourcing scope**: Zefix only. Google Places, site crawling, cantonal
  excerpt parsing, email waterfall and drafting are later phases; their stages
  exist as stubs.
- **Environment**: WSL Ubuntu, Python 3.13.13, SQLite 3.50, uv (installed),
  systemd available for a later timer. Everything lives in ext4, never /mnt/c.

Zefix facts verified against the live OpenAPI spec (v2.7.2.3):
- `POST /api/v1/company/search` with `name` (min 3 chars, `*` wildcard),
  `canton`, `legalFormId`, `activeOnly`. Returns `CompanyShort` rows.
- `GET /api/v1/company/uid/{id}` (and ehraid/chid) returns `CompanyFull`
  incl. `purpose`, `address`, `capitalNominal`, `cantonalExcerptWeb`.
- `GET /api/v1/legalForm`, `/registryOfCommerce`, `/community` are catalogs.
- `GET /api/v1/sogc/bydate/{date}` is the daily mutations feed (later: delta
  refresh instead of re-sweeping).
- Auth is HTTP Basic; free credentials by email to zefix@bj.admin.ch (human
  turnaround). The foundation therefore runs its tests on recorded fixtures
  and treats live access as a smoke test once credentials arrive.

## Layout

```
~/arpenteur/
  pyproject.toml            # uv-managed, requires-python >=3.13
  .env.example              # ZEFIX_USER/PASSWORD, ANTHROPIC_API_KEY, INFOMANIAK_* placeholders
  arpenteur.toml            # cantons, legal forms, segments, rate limits
  arpenteur/
    __init__.py
    cli.py                  # argparse: init-db, source, profile, people, reach, draft, stats
    config.py               # loads arpenteur.toml + .env; typed Settings dataclass
    db.py                   # sqlite3 connection, PRAGMA user_version migrations, upsert helpers
    migrations/0001_init.sql
    http.py                 # httpx client: token-bucket rate limit, retries w/ backoff,
                            # on-disk cache under data/http-cache keyed by request sha
    llm.py                  # complete(task, system, user) -> str; provider=anthropic now,
                            # infomaniak slot behind the same interface
    sources/zefix.py        # auth, catalogs, prefix sweep, CompanyFull hydration
    stages/source.py        # orchestrates sources -> db
    stages/profile.py       # purpose-text classification via llm.py
    stages/{people,reach,draft}.py   # stubs: explain what phase adds them
  tests/
    fixtures/zefix/*.json   # recorded catalog + search + company responses
    test_db.py  test_zefix.py  test_sweep.py  test_profile.py
  data/                     # gitignored: arpenteur.sqlite3, http-cache/
```

Dependencies (kept deliberately short): `httpx`, `anthropic`, `pytest` (dev).
CLI is stdlib argparse; TOML via stdlib tomllib; no ORM.

## Schema (migrations/0001_init.sql)

- `company(id PK, uid UNIQUE, chid, ehraid, name, legal_form_uid, legal_form_label,
  legal_seat, canton, status, purpose, address_json, capital, capital_currency,
  cantonal_excerpt_url, zefix_detail_url, source_url, fetched_at, first_seen)`
- `segment(company_id FK, segment, confidence REAL, rationale, model,
  classified_at, PRIMARY KEY(company_id, segment))`
- `sweep(canton, legal_form_uid, prefix, result_count, completed_at,
  PRIMARY KEY(canton, legal_form_uid, prefix))` — resumability record
- `run(id PK, stage, args_json, started_at, finished_at, stats_json)`
- Empty-for-now tables the stubs will fill later phases: `person`, `contact`,
  `draft` — created in 0001 so the schema tells the whole story, each with
  provenance columns (`source_url`, `method`, `fetched_at`).

Provenance is per-row columns, not a separate table: every fact carries where
it came from and when. That is the nLPD-defensibility requirement built into
the storage from day one.

## Zefix source stage

1. On first run, fetch and cache the `legalForm` catalog; `arpenteur.toml`
   maps friendly names (`sa`, `sarl`, `ri`, `association`, `fondation`) to
   eCH-0097 UIDs from that catalog.
2. Sweep per (canton × legal form): search `a*`…`z*`, `0*`…`9*` with
   `activeOnly=true`. When a prefix returns the API's page cap, deepen
   (`aa*`, `ab*`, …) recursively. Record each completed prefix in `sweep`
   so an interrupted run resumes where it stopped.
3. Upsert `CompanyShort` rows, then hydrate `CompanyFull` (purpose, address,
   excerpt URL) per new/changed company via `/company/uid/{id}`.
4. Politeness: ~2 req/s token bucket, exponential backoff on 429/5xx,
   User-Agent naming the tool and a contact address.
5. Default cantons: GE, VD, FR, NE, VS (JU available in config).

## Profile stage (foundation version)

Classify from the registered `purpose` text alone — no crawling yet. Batch
companies into Haiku calls via `llm.py`; output one or more of the six
segments (`sme-owner`, `mature-sme`, `data-driven`, `ai-adopter`,
`compliance-sector`, `lean-startup` — labels + one-line definitions live in
`arpenteur.toml`) with confidence and a one-line rationale, stored in
`segment`. `--dry-run` prints instead of writing. Purpose text is public
registry data, so no confidentiality constraint applies to the provider.

## CLI surface

```
arpenteur init-db
arpenteur source zefix [--canton VD,GE] [--legal-form sarl,sa] [--resume]
arpenteur profile [--limit N] [--dry-run]
arpenteur stats            # counts per canton/form/segment, last runs
arpenteur people|reach|draft   # stubs: print the phase that adds them
```

## Verification

1. `uv run pytest` — all green without network: zefix client against recorded
   fixtures; sweep-deepening logic (cap-overflow → child prefixes; resume
   skips completed prefixes); migration applies to a fresh db and is
   idempotent; profile parsing of a canned LLM response.
2. `uv run arpenteur init-db && uv run arpenteur stats` on a fresh clone.
3. Live smoke (deferred until credentials arrive, documented in README):
   `arpenteur source zefix --canton VS --legal-form sarl` then `stats`;
   expected order of magnitude: thousands of rows, purpose text populated.
4. README gains a "getting credentials" section: email zefix@bj.admin.ch,
   free, put them in `.env`.

## Out of scope (later phases, stubs only)

Google Places enrichment, website discovery + crawler, cantonal excerpt
parsing for people, email resolution waterfall, draft generation and any
sending machinery, systemd timer, Infomaniak provider implementation.
