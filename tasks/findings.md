# Sweep of 07.10.2026: what is left

Three audits ran over the app: correctness, dead code, performance. What was
fixed on the day is in the git log and in `todo.md`. This file holds the rest.

Each item says who found it and whether I checked it myself. An item marked
"reported, not verified here" carries the auditor's confidence and nothing
more, so it wants its own reproduction before anyone edits code for it.

## Verified by me, fixed

- `console/render.py` `band()` read `source`, a column `board_overlap` and
  `roles_for_party` never selected. Reproduced as a `KeyError`, so
  `/company/{id}` and `/party/{id}` were a 500 for any company sharing an
  officer with a scored one.
- `console/app.py` `start_run` read the form through `dict()`, which keeps one
  value per key. Picking FR, GE and VD swept VD alone.
- `llm.py` `extract_json` tried `[` before `{` whatever their positions, so a
  prose-wrapped object came back as its first inner array.
- `workspace.py` wrote archive attachments without checking where they landed,
  and dropped the database before resolving `pg_restore`.
- `sources/mailcheck.py` held two entries no token could match and one twice.
- `stages/source.py` bound one loop variable into a closure and not the other.
- `/find` paid for a COUNT it discarded: 739 ms to 354 ms for `ge`.
- `cantons_present` scanned 598'036 rows to find 26 values: 27.8 ms to 0.59 ms.

## Money, and the documents a client sees

Reported, not verified here. Certain per the auditor except where noted.

- `console/app.py` `drop_bill_line` deletes a line and recomputes net, VAT and
  gross with no status check, so a stale tab rewrites an issued invoice at the
  current rate. `drop_quote_line` does the same to a sent quote while its
  totals stay frozen. The `order_*_lines` routes do enforce the rule, so the
  pattern to copy is already in the file.
- `modules/sales/store.py` `credit_invoice` refuses only drafts, so two clicks
  make two credit notes, and it marks the original credited while the note is
  still a draft. `record_payment` has no status guard, so a payment on a
  credited invoice flips it to paid.
- Once a credit note is issued with a negative gross, `outstanding()` and
  `analytics.revenue` count it while dropping the credited original, so
  crediting an unpaid invoice of 1000 lowers "still owed" by 1000. Probable.
- The payment form is prefilled with `gross_total` rather than the amount
  still owed.
- `modules/sales/store.py` draws the invoice rank from `nextval` before the
  IBAN and reference checks that can raise, and a sequence value does not roll
  back. Each failed Issue leaves a gap in a series the migration calls gapless.
  `scripts/fbg_first_invoice.py` defaults to a dry run that consumes rank 1.
- `console/money_page.py` prints the rank on the client document, in the title
  and in the QR block, where `0003_invoice_reference.sql` says the rank is
  never printed and `reference` is what the client quotes.
- The QR debtor block reads `address["town"]`, and Zefix addresses carry
  `city`. `_addressee` in the same file already handles both. The town is
  mandatory in the QR-bill standard, so a bank may reject it. Probable.

## Runs that never finish

- Nothing calls `rollback()` after a failed statement, so the following `log()`
  raises `InFailedSqlTransaction`, that is swallowed, and `finish_run` raises
  the same. The run row stays "running" and the error text is lost. 15 CLI
  handlers and `console/runner.py` `_work` share the fault, and `close_orphans`
  settles console runs only, so CLI rows stay open for ever.
- Reproduction given: `arpenteur hydrate --name-like '(immo'` makes the
  Postgres regex fail.

## The register parser and what reaches the model

- `sources/shab.py` compiles `DEPARTURE` with `IGNORECASE`, so its `[A-Z]`
  anchor matches lowercase and "le gérant Pascual Carlos n'est plus gérant"
  captures the role with the name. The key then matches no officer and a
  departed person is returned as current.
- The same file never matches a feminine role label, stores "Vice-président"
  as "président", and drops a leading particle so "de Mesquita Pedro" becomes
  "Mesquita Pedro".
- `stages/match.py` and `stages/enrich.py` select every `core.person` row with
  no filter on `left_date`, so past officers are given to the model that writes
  the approach advice, against the rule in `0006_officer_dates.sql`.
- `stages/people.py` skips `prune_people` on a reparse that finds nobody, so
  stale rows survive.
- The parser spends about 3.4 ms per company, mostly on regex work repeated
  three times per publication. A substring guard before each pattern measured
  6.1× faster with identical output on 4'193 companies.

## Scoring that cannot see its inputs

- `modules/scout/scoring.py` reads `left_on`, `worked`, `full_name` and
  `other_name`. `board_overlap` returns none of them, so every one is `None`
  and a director shared with a company being worked scores 20 instead of 85.
  Only the unit tests, which build dicts by hand, reach the upper tiers.
- `core.contact.person_id` is never populated by any caller of `save_contact`,
  so the reach score is capped at 55 and its 90 tier is unreachable.
- `scout/store.py` `bring_in` writes no `audit.state_change` row, so the board
  cannot date those cards and two analytics views have no history for them.

## Credentials and feeds

- `sources/zefix.py` never checks `reply.status`. A 401 makes `search` and
  `sogc_by_date` return empty, and the prefix or day is then recorded as
  complete and never retried. `company()` turns an error body into a nameless
  company, and `upsert_company` writes that name over the real one.
- `stages/sync.py` records the current day as fully consumed from a morning
  run, so notices filed later that day are never read, and the HTTP cache then
  serves the stale file for ever.

## Loaders that overwrite better data

- `scripts/immobilier_directory.py` and `scripts/immobiliensektor_directory.py`
  call `save_site` with status, url and method only, and `save_site` replaces
  the row, wiping the summary, the signals and the restricted findings. Their
  `save_address_hunt` calls overwrite real cascade rows with an empty
  `partial`.
- Personal mailboxes are suppressed only in `stages/reach.py` `hunt_all`. The
  console stage, `enrich` and the directory scripts store them unsuppressed,
  where they then count as reachable. The docstring of
  `scripts/suppress_personal_mail.py` claims otherwise.

## Guards that do not hold

- The cost guards test `limit is None` while the slice tests `if limit:`, so
  `--limit 0` runs the whole selection with no confirmation in `enrich`,
  `match`, `source` and `reach`.
- `reach._select` and the `enrich` selection read every matching row to produce
  a count for an error message: 541'962 rows for `reach`. Fetch `guard + 1`, or
  count first.
- `decimal.InvalidOperation` is not a `ValueError`, so typing `1,5` or `1'400`
  into a quote line, a bill line or a payment returns 500.

## Dead code

The auditor deleted the whole first group in a scratch copy and the suite still
passed there.

- `arpenteur/migrations/`, 15 files and 840 lines of SQLite-era DDL that
  `Registry.migration_dirs` can never reach. They do ship in the built wheel.
  Three comments point at them and need repointing first: `console/app.py` in
  two places and `config.py`.
- Functions with no reference in the app, the tests, the scripts or the README:
  `map_page.as_json`, `ui.pills`, `clients.update_programme`,
  `clients.update_member`, `clients.feedback_for`, `clients.emails_for_company`,
  `clients.thread_of`, `geo.address`, `sales.outstanding`, `sales.time_for`,
  `scout.address_hunt`, `scout.hunt_counts`, `mailcheck.verify` with its
  `Verdict`, `mail.Body.snippet`, and the constants `modules.SLOTS`,
  `geo.PRECISIONS`, `scoring.JUDGED`, `scoring.COMPUTED`,
  `sales.INVOICE_STATES`.
- `core.company_search_idx`, a 30 MB GIN index with zero scans and no query
  that could use it. Also `core.person_name_trgm_idx` and `crm.note_tags_idx`,
  which no query shape can serve, and the `btree_gin` extension, which no index
  uses. Dropping any of them needs a new migration.
- `intel.draft` is read and written by nothing, and `stages/draft.py` prints
  "not implemented".
- The module slot registry is exercised only by `tests/test_modules.py`: no
  module declares `contributions`, nothing calls `Registry.fill`, and
  `Module.router`, `Module.stages` and `Registry.stages()` are never set or
  read. `modules/sales/__init__.py` and the README both say sales fills
  `client.panels`, which it does not.
- Time tracking has no route and no CLI caller, so `commercial.time_entry`
  cannot gain rows and the effort report is always empty.
- `psycopg[binary,pool]` can drop the `pool` extra; `psycopg_pool` is never
  imported. The dev group repeats `httpx`.

## Performance, measured by the auditor and not re-measured here

- The survey list joins all 598'036 rows before sorting, 243 to 305 ms. Taking
  the top 200 ids in an inner query first measured 285 ms to 45 ms.
- `/pipeline` runs 23 count queries, 185 ms, nine of them over the company
  table. A short TTL cache was suggested, which changes how fresh the numbers
  are and so wants a decision.
- `core.company` has 18% of its pages marked all-visible and `shared_buffers`
  is 128 MB against a 299 MB heap. `VACUUM (ANALYZE)` and a larger buffer are
  free, with no code change.
- `_find_company` and `scripts/load_found_addresses.py` match a UID with
  `OR REPLACE(uid,'-','')`, which no index can serve: 87 ms per call, and
  `programme add` calls it per reference.
- `console/runner.py` commits on every log line, which also commits the running
  stage's own transaction.

## Smaller items

- `dossier_page.py` inserts an attachment `note` unescaped, and that note holds
  client-supplied filenames, so one named `<script>` runs.
- `GET /calendar?month=2026-13` returns 500: the date is built outside the
  `try` that guards the parse.
- `llm.py` passes the whole prompt as one argv element, and Linux caps a single
  argument at 131'072 bytes, so a large dossier fails `advise` with `E2BIG`.
  Only `FileNotFoundError` and `TimeoutExpired` are handled. Probable.
- `_find_client` fetches `LIMIT 2` and never checks for the second row, orders
  by id with no preference for an exact match, and matches case-sensitively.
- Re-adding a held document writes `kind` and `note` unconditionally while the
  folder and project use `COALESCE`, so a blank field clears a label. The
  console note route passes an empty tag list where the CLI passes `None`.
- `set_prospect` and `update_project` drop `None`, so an owner, a next action,
  a date or a budget cannot be cleared once set.
- `geo.address` deduplicates on a key whose parts can be NULL, and Postgres
  treats NULLs as distinct, so an address with no house number gains a row on
  every geocode run.
- Settings are frozen at console startup, so a new segment cannot be ticked and
  a narrowed territory still sweeps the old one until a restart.

## German transliteration, found 07.10.2026 during the run

`deaccent("Müller")` returns `Muller`, so a German umlaut never matches the
`ue`, `oe` or `ae` its owner writes into a domain. Verified in the running
survey: `Müller Immobilien + Verwaltungen GmbH` yields the words
`['verwaltungen', 'muller']` and its real site is `immo-mueller.ch`, which the
comparison scores as sharing nothing.

This is not only a reporting artefact. `domain_candidates` builds the tier 3
guesses from the same words, so a firm whose domain spells the umlaut out is
never guessed, and `domain_echoes_name` cannot confirm it when a search does
find it. German-speaking cantons hold the larger half of the survey.

Two further gaps in the same comparison, both seen in the same 25 firms:

- `distinctive_words` drops words of two letters or fewer, so `RX IMMO SA`
  produces an empty list and can match no domain at all.
- A hyphen in the domain splits a name word: `Rebag Verwaltungs AG` against
  `reb-ag.ch` scores zero.

Not changed during the run. A fix wants measuring against the addresses
already held, since loosening the comparison also loosens what the cascade
accepts as the right firm.

## Attribution worth a spot check

Two firms in the 07.10.2026 run were given a site whose name agrees with
neither the register name nor the seat. Both may be legitimate trading names,
which is why no code change was made.

- `Raffaello Radicchi immobilier SA`, La Chaux-de-Fonds, `insulae.ch`, 5 addresses
- `Roland Stöckli Immobilien AG`, Merenschwand, `derbaumanager.ch`, 17 addresses

`own_domain_only` cannot catch this class: the addresses do match the site's
own domain, so the rule that protects against a third party's address on the
right site says nothing about the right addresses on the wrong site.

## The domain rule is wrong for the search fallback, measured 08.10.2026

The fix committed as `0fd6212` holds a search-reported address to the
company's own domain, the same rule a read page is held to. Checked against
every row that branch has ever filed, the rule would delete mostly correct
data.

290 rows carry the note `reported by search, page not readable`, going back to
10.09.2026. Judged by `own_domain_only` itself rather than by a rule rewritten
in SQL:

- 120 rows lose every address they hold.
- 3 rows lose some and keep some.
- 167 rows are unaffected.

What the 123 affected rows actually contain:

| company | row domain | address the rule drops |
|---|---|---|
| Aeberli Immobilien & Concierge AG | `aeberli.immo` | `immo@aeberli.ch` |
| immo-floor immobilien ag | `immofloor.ch` | `immo@immo-floor.ch` |
| AMBOSS Immobilien AG | `amboss-immo.ch` | `info@amboss.immo` |
| Allianz Suisse Immobilien AG | `allianz-immo.ch` | `zuerich.immo@allianz.ch` |
| Baldegger Immobilien AG Oberuzwil | `baldeggerimmobilien.com` | `immobilien@baldegger.com` |

Those are the same firms at a second domain they own. The rule was written for
a different fault, and its own docstring says so: reading `naef.ch` returned
`info@acanthe.ch`, a *partner's* address on the right page. The two branches
have inputs of a different kind. A page read reports whatever is printed on
that page, including other firms. A search reports where the firm takes mail,
which is often not where it serves its site.

The off-domain rate measures the difference: 42% of search-fallback rows
against the handful the page-read branch sets aside.

So the retroactive cleanup was not run. Nothing was deleted.

A smaller group inside the same 123 does look wrong, and wants the same spot
check as `derbaumanager.ch`:

- `Fore Immobilien AG`, `foreimmobilien.ch`, dropped `info@namlha.ch`
- `AREMA Immobilien Aarau AG`, `arema-immobilien.ch`, dropped `marc.herzog@hkg.ch`
- `BRV IMMO AG`, `brv-immo.ch`, dropped `verena.buergi@baut.ch`

The discriminator that separates these from Aeberli is not the domain but
whether the address's domain echoes the company name, which is what
`domain_echoes_name` and `distinctive_words` already compute, and what the
German transliteration section above says is broken for umlauts and hyphens.
`immofloor.ch` against `immo-floor.ch` is the hyphen case exactly.

Fixing the comparison is therefore the prerequisite, not the domain rule. Until
then `0fd6212` should be read as narrowing the branch more than intended.

## Suppression checked across the whole table, 08.10.2026

The section above claims personal mailboxes are suppressed only in
`stages/reach.py` `hunt_all`, so the console stage, `enrich` and the directory
scripts would store them unsuppressed. Measured against every row: 12'111
email contacts, 130 personal, 130 suppressed, none leaking. Either the other
paths never stored one or the claim was wrong. No change needed.
