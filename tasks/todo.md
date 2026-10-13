# Arpenteur as a product: six stages

The approved plan of 13.10.2026 is the reference for every detail; this file tracks progress. The findings are in `audit-2026-10-13.md`, each with the stage that fixes it.

## Standing rules for this build

- Develop on main; the operator commits from a bare command stack ending in `git push`.
- Red test first for every behaviour a stage fixes; ruff clean; the whole suite green in one run; a live smoke of the console before each commit stack.
- One pytest run at a time: the suite owns named databases.
- A migration is immutable once applied to the live database; a new file for every change.
- Never seed fabricated data into the live database; never print a password or the IBAN.
- The two wave routes stay the only senders.
- No stage starts before the previous one is committed.

## Stage 1. Stop the leaks, make the repository handable

- [x] The audit register and this tracker.
- [x] `.gitignore` for the operator's material; the files untracked and left on disk; `uv.lock` tracked.
- [x] Neutral examples: `campaign.example.md`, `templates/example/`, `words/example.txt`; the signature image default empty.
- [x] LICENSE, the pyproject fields, the copyright line.
- [x] `.env.example` from the names the code reads; the two wrong names in error messages fixed; a test that keeps them honest.
- [x] The docstring path and the 63 fixtures with the operator's identity.
- [x] Suite green, ruff clean, commit stack.

## Stage 2. Correctness: money, runs, crashes

- [x] `money.parse_amount`; line deletes refused on issued invoices and sent quotes.
- [x] Issue validates the IBAN before drawing a number; the payment part reads the register's town and leaves out a debtor it cannot encode; credit notes once, issued at once; payments on issued invoices only, prefilled with what is owed.
- [x] Still owed and revenue net credit notes, in decimals; the printed document shows the reference.
- [x] Every money route reports; confirmation on issue and credit.
- [x] `run_scope` for the command-line handlers; one sentence for a stopped database and a bad setting; the three `.date()` crashes; `--limit` positive; stale command-line runs can be settled.
- [x] The console's error page and 404 shell; the parse fixes; the upload reply escaped; settings reload without a restart.
- [x] Suite green, ruff clean, live smoke, commit stack.

## Stage 3. Navigation, Home, feedback, help plumbing

- [ ] Five nav groups with captions; the brand links home.
- [ ] Home as a dashboard; the company list at `/companies`.
- [ ] One flash banner from the query string; `?error=` on every refusal.
- [ ] Confirmations on the remaining destructive actions.
- [ ] Crumbs fixed; Settings in the shell; the signature and route pages linked.
- [ ] Runs: cancel, a live header, honest status, advise and prep through the runner, History paged.
- [ ] Operations lists bounces, draft and verify.
- [ ] The vocabulary applied; titles per page; labels, required marks, empty states with a next step.
- [ ] `/help` renders `docs/`; every page carries its help link.
- [ ] Suite green, ruff clean, live smoke, commit stack.

## Stage 4. Settings inside the app, setup, model access

- [ ] One overlay key per configuration table, merged in `load_settings`.
- [ ] Settings sections: identity, sending and mailbox, register, model access, enrolment, scoring weights, catalogue, secrets as set or missing.
- [ ] `arpenteur settings import-toml`, run once here; `arpenteur.toml` cleaned of every personal value.
- [ ] The draft stage reads its brief and its invitation from settings and templates.
- [ ] `health.py`, `arpenteur doctor`, the Home checklist.
- [ ] Template editor and "start from the example".
- [ ] Model access: the sign-in documented as it works, ids validated and current, the placeholder refused, the prompt over stdin.
- [ ] Suite green, ruff clean, live smoke, commit stack.

## Stage 5. Documentation and in-app help

- [ ] `docs/`: start, install, setup, one guide per menu entry, operations, the command reference generated from the parsers, glossary, legal.
- [ ] README rewritten; the design essays moved under `docs/design/`.
- [ ] The wrong README claims corrected; the help strings filled; `arpenteur help`.
- [ ] Every page's help link resolves; a test keeps it so.
- [ ] Commit stack.

## Stage 6. Internals and packaging

- [ ] Per-module routers mounted when enabled; migrations of enabled modules only; `modules drop` consistent; panels through the slots.
- [ ] Parsers per module; one lookup per entity; shared upload and note import; one date and one money formatter.
- [ ] Dead code, the legacy migrations, the unused indexes, the dependency extras.
- [ ] Time tracking on the project page, billed into a draft invoice.
- [ ] The survey-half fixes S1 to S10 and S12, each with a test.
- [ ] Packaging: Python version pinned, HTMX vendored, a Dockerfile and an app service, `ARPENTEUR_HOME`.
- [ ] Suite green, ruff clean, live smoke, commit stack.

## Review

Filled in at the end of each stage: what exists now, what the live check showed, what was found and fixed on the way.

### Stage 1, 14.10.2026

What exists now:

- The repository tracks no file of the operator's: the scoring brief, the enrolment brief, the Limen templates, the Drymos word list, the signature and its picture, the seven campaign and client scripts and their test are ignored and stay on disk, where they keep working.
- A neutral example of each kind ships in their place: `campaign.example.md`, `templates/example/` with three messages the parser accepts, `words/example.txt`.
- `LICENSE` states the proprietary terms; `pyproject.toml` names the licence and the author; `uv.lock` is tracked.
- `.env.example` lists the 26 names the code and the compose file read, grouped, with nothing else; `tests/test_settings.py` refuses a message that names a variable nothing reads and an example that drifts from the code.
- The 63 fixtures that carried the operator's name and mailbox, and the ones that carried a person at a client, use a fictional sender and a fictional foundation.

The live check: every one of the eighteen menu pages answers 200 with the operator's files untracked, `arpenteur stats` and `arpenteur campaign show DRYMOS-A` run, and the clients module is at version 11.

Found and fixed on the way:

- `0010_campaign.sql` compared two clocks: `tested_at >= rendered_at` and `approved_at >= tested_at`, each stamp being its own transaction's `now()`. A backwards clock step of 0.67 s under WSL2 refused a wave its own test during the first suite run. `0011_wave_order.sql` keeps the order rules on nullness alone; two tests pin it.
- One test read the operator's own `signature.html` from the repository root; it reads a fixture shaped like a mail client's paste instead.
- The suite: 1,304 passed, ruff clean.

### Stage 2, 14.10.2026

What exists now, for money:

- A line cannot leave an issued invoice or a sent quote; the store refuses and the page says so.
- A credit note is issued with its own rank in the transaction that marks the invoice credited; an invoice is credited once, a credit note never, a draft is deleted instead.
- A payment is recorded only on an issued invoice and only for a positive amount; the form is prefilled with what is owed; an amount typed the Swiss way is read, and nonsense is a refusal rather than a 500.
- "Still owed" nets credit notes and is summed in decimals, on the ledger, on the register and in `analytics.revenue` (migration `0003_credit_notes.sql`).
- The printed document is headed by the reference; the rank never prints. The payment part reads the register's `city` as the town and, when a debtor cannot be encoded, prints the blank frame the standard prescribes instead of a malformed block.
- A refused issue draws no rank. Every money route reports its refusal on the page, and issuing or crediting asks before it acts.

What exists now, for runs and the command line:

- One `run_scope` carries every handler: rollback, `finish_run`, Ctrl-C recorded as interrupted, the connection closed.
- A stopped database, a bad setting, a missing or malformed `arpenteur.toml` are one sentence each, and never a traceback.
- `programme replies`, `ledger` and `history` print.
- `--limit` refuses zero on all sixteen flags, and the five stages read the limit the same way the guard does.
- A run nothing is running any more can be marked interrupted from its page.

What exists now, for the console:

- A fault renders inside the shell with its name; the 22 bare 404s render inside the shell.
- The calendar, a budget, a plan size, an unknown contact and the brief save refuse instead of crashing; the upload reply is escaped.
- A setting saved on the Settings page reaches the next run without a restart.

The live check: the fixed pages answer on the real database, the invoice page with lines renders where it was a 500, the printed invoice is headed by its reference, `enrich --limit 0` is refused, `programme ledger DRYMOS` lists its 347 members, and analytics is at version 3.

Found and fixed on the way:

- X2: the invoice page read a column only quote lines carry, so it was a 500 for any invoice with a line.
- X3: the real client was the worked example in 21 files; a fictional foundation stands in now.

One deviation from the plan: M7 is handled where the payment part is made rather than at issue. The standard allows a payment part without a debtor, so an invoice to a client whose register address lacks a town is issued and printed with the blank frame rather than refused.

The suite: 1,408 passed, 104 of them new, ruff clean.
