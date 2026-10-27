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

- [x] Five nav groups with captions; the brand links home.
- [x] Home as a dashboard; the company list at `/companies`.
- [x] One flash banner from the query string (the shell draws it); `?error=` on every refusal.
- [x] Confirmations on the remaining destructive actions.
- [x] Crumbs fixed; Settings in the shell; the signature and route pages linked.
- [x] Runs: cancel, a live header, honest status, advise and prep through the runner, History paged.
- [x] Operations lists bounces, draft and verify.
- [x] The vocabulary applied; titles per page; labels, required marks, empty states with a next step.
- [x] `/help` renders `docs/`; every page carries its help link.
- [x] Suite green, ruff clean, live smoke, commit stack.

## Stage 4. Settings inside the app, setup, model access

- [x] One overlay key per configuration table, merged in `load_settings`.
- [x] Settings sections: identity, sending and mailbox, register, model access, enrolment, scoring weights, catalogue, secrets as set or missing.
- [x] `arpenteur settings import-toml`, run once here; `arpenteur.toml` cleaned of every personal value.
- [x] The draft stage reads its brief and its invitation from settings and templates.
- [x] `health.py`, `arpenteur doctor`, the Home checklist.
- [x] Template editor and "start from the example".
- [x] Model access: the sign-in documented as it works, ids validated and current, the placeholder refused, the prompt over stdin.
- [x] Suite green, ruff clean, live smoke, commit stack.

## Stage 5. Documentation and in-app help

- [x] `docs/`: start, install, setup, one guide per menu entry, operations, the command reference generated from the parsers, glossary, legal.
- [x] README rewritten; the design essays moved under `docs/design/`.
- [x] The wrong README claims corrected; the help strings filled; `arpenteur help`.
- [x] Every page's help link resolves; a test keeps it so.
- [x] Suite green, ruff clean, live smoke, commit stack.

## Stage 6. Internals and packaging

- [x] Per-module routers mounted when enabled; migrations of enabled modules only; `modules drop` consistent; panels through the slots.
- [x] Parsers per module; one lookup per entity. Left: the shared upload and note import, one date and one money formatter.
- [x] Dead code, the legacy migrations, the unused indexes, the dependency extras.
- [x] Time tracking on the project page, billed into a draft invoice.
- [x] The survey-half fixes S1 to S6 and S8 to S10, each with a test. Left: S7 (the classifier scripts are the operator's, outside git) and S12 beyond the UID lookup.
- [x] Packaging: Python version pinned, HTMX vendored, a Dockerfile and an app service, `ARPENTEUR_HOME`.
- [x] Suite green, ruff clean, live smoke, commit stack.

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

### Stage 3, 14.10.2026

What exists now, in the shell:

- The header is five captioned groups in the order of the work, Prospecting to System, with the current group marked and an empty group not drawn; the brand links to Home; `load` refuses a module whose entry names a group the header does not draw.
- Home is a page of its own: tiles that are links, the setup checklist from `health.py` (database, modules, territory for now), the last three runs. The company list lives at `/companies` and its fragment at `/companies/rows`.
- The shell draws what a redirect asked it to say, once, red for `?error=` and green for `?note=`, read by a middleware into a context variable; no page draws its own banner any more, and every refusal in the console redirects with `?error=`.
- The tab is named after the page's heading. Every page carries a help link to its section's guide, and `/help` renders `docs/` inside the shell with links rewritten; a page that is not written yet says so. An address no route answers renders inside the shell.

What exists now, on the pages:

- An action that cannot be taken back asks a question that names the thing: quote marked sent or closed, a line removed, a draft refused, a site, an event, a project, a note, a folder, a document, a mail link.
- Crumbs on the list, campaign and wave pages point the right way; quotes and invoices go through Sales; the company page leads back to Companies; Settings is in the shell with the menu and a sentence for every save; the signature page is linked from Settings and the campaign pages; each company in the overlap panel links to its route page.
- Every control has a label, required fields carry a mark and the attribute, a budget steps by centimes, the redirect guard refuses `//host`, every empty page says what would fill it, no page quotes a command line or a path on disk, the signature editor is in English, counts are grouped the Swiss way.
- The vocabulary holds: Companies, scoring brief, List, Campaign, Draft, ledger, Operations, History.

What exists now, for runs:

- A run can be stopped from its page; the stage stops at its next log line, what it committed stays, and the run reads `cancelled`. The stages that post mail and the one-call stages refuse the stop.
- The run page's state block rides along with the poll, so it follows the run to its end; the announced sentence says failed, cancelled or finished; the arguments read as fields; the page leads back to History and to the client it was about.
- Advice and preparation run through the runner on their own lane, never behind a sweep and never inside a request.
- Operations names every running run whatever its lane and disables only the lane's own cards; it offers the address check, the draft stage and the bounce read, each card stating what it needs in the console's words.
- History is paged by run id and polls only while a run shown is unfinished.

The live check: every header page answers 200 with its own title, Home shows the live counts and the three checks, Operations shows fifteen cards, a run page carries its state block and the way back, a refusal in the query string is one red banner, an unknown address is a 404 in the shell.

Found and fixed on the way:

- The two background agents were cut off by the account's session limit with their edits on disk and their tests unrun; the work was finished in the tree. Six of their tests were wrong rather than the code: two matched the stylesheet, one expected an Issue form the fixture cannot show, one a double-quoted link the page wrote single-quoted, one an unlabelled file input in the drop zone, one an empty mail queue hidden behind the "no mailbox" note.
- The draft card named the operator's own brief file; it names the message brief, which stage 4 moves into Settings.
- A run started with an exception carrying no message recorded an empty error and showed as done; the type name stands in.
- A dry run of the bounce read counted as the read a campaign's next wave waits for; dry runs no longer open that gate.

The suite: 1,654 passed, 246 of them new, ruff clean. The one red test in the full run asserted the old word "survey" on the client page and was updated to the new wording; the file passed on its own afterwards.

### Stage 6, 14.10.2026

What exists now:

- The console is composed from one route file per module under `arpenteur/console/routes/`, each exposing `router(ctx)` and its `CONTRIBUTIONS`, found by name and mounted only when the module is enabled. `context.Console` is what they close over; `app.py` keeps the composer's own pages and is 1,082 lines where it was 4,285. FastAPI keeps an included router as one entry of `app.routes`, so `routes.every_route` expands them and the six no-send gate tests walk that list.
- Modules meet through slots: the company page's actions and panels, the board's client chips, the sent draft's programme move, the client page's panels, the project page's panels and the home tiles. `Registry.add` takes a route file's contributions and `Registry.gather` serves the slots that carry data rather than markup.
- `db.open_db(url, enabled)` migrates the enabled modules only; the loader's default names all five; `modules drop` refuses while the module is still enabled or still required, naming the dependant.
- The command line is split the same way, under `arpenteur/commands/`: `common.py` for what every command shares, `system.py` for the application's own commands, one file per module registered when it is enabled; `cli.py` is 140 lines. `ARPENTEUR_HOME` names the project folder for an installed copy and for a test.
- Time is logged on a project's page and billed into a draft invoice at an hourly rate, as a sales panel in a clients page.
- Gone: eleven functions and constants nothing called, the legacy `arpenteur/migrations/` folder, two unused indexes and the `btree_gin` extension, the `pool` extra and the duplicate `httpx`. The decimal parser moved to the kernel, since a budget is typed like a quote line.
- The survey half: a gazette day read while it was still filling is read again the next day, from the register rather than the cache; a refusal from the register is an error and a record without a name is no record; the overlap query answers what the relationship score reads, a bring-in is audited, a published address names the one officer it spells; the parser reads a departure only from a capitalised name, keeps a particle, tells a vice-president from a president and reads the feminine labels; officers who left stay out of the research and scoring prompts; an empty reparse prunes; a directory note no longer wipes the research and a hunt that found nothing keeps what an earlier one found; a domain echoes an umlaut in either spelling and a two-letter name word counts; one `find_company` and one `find_client` serve the command line, the console and the scripts; a re-added document keeps its kind and note; an owner, a next action, a budget and a due date can be cleared; an address with a missing part is one address.
- Packaging: Python 3.14 pinned, htmx shipped with the package and checked against the hash the CDN tag carried, a Dockerfile and an `app` profile in the compose file that mounts the project folder.

Left, and why:

- S7: the classifier scripts are the operator's, outside git since stage 1; the console marks only what a person set by hand, so a rule's label shows no mark rather than a wrong one.
- S12: the UID lookup is index-friendly through `find_company`; the run log's commit per line is deliberate, it is what makes the log live; the count queries and the list's join order are as they were.
- A6: the document upload and the note import are still written twice, and the date and money formatters are still several.
- The empty reparse has no stage-level test; `prune_people` with an empty keep list has its own.

Found and fixed on the way:

- The migration-digest test wrote a tampered digest into the shared test database and never restored it. The old migration cache hid it; keying the cache by the module list exposed it. The test restores the digest.
- The CLI tests patched `cli.load_settings` and reached stage modules through `cli`; they go through `commands.common` and import the stage modules by name.
- The test suite's spy on `open_db` took one argument.

The live check: every page answers 200 on the live installation, the company page offers the client action, the client page draws the sites and the money panels, the project page offers the time log, a path nothing answers to is a 404 in the shell; `arpenteur doctor --quick` is green except the known Drymos templates gap; `modules list` shows scout at 18, geo at 2 and clients at 12 after the three new migrations ran on the live database.

The suite: 1,753 passed, 37 more than at the end of stage 5, ruff clean.

### Stage 5, 14.10.2026

What exists now:

- The manual under `docs/`: installation, setup, running it, one guide per menu entry (seventeen), the command reference generated from the parsers, the glossary, the law, and twelve design pages moved out of the README with their measurements marked as the first deployment's. The index lists every page and the ten-minute path to a first ranked list.
- One module, `arpenteur/manual.py`, finds the pages for the console's Help routes and for `arpenteur help [topic]` and `arpenteur help --list`, and generates the command reference that `scripts/cli_reference.py` writes.
- The README is a front page: what it is, the parts, starting, the manual, modules, tests, licence. Every claim the audit named as wrong is gone: the draft stage is built, no slot fill is claimed, five commands are named as needing the register, the model access is the `claude` command, the example templates folder replaces the operator's, the pages are the five groups, the contact comes from Settings, the tests need the container.
- Every command-line argument carries a help string, including the action word of `audience` and `campaign`, which now lists what each action takes.
- `tests/test_help.py` keeps it so: every menu entry has a guide file, every page opens on a title, every link between pages and from the README resolves, the index reaches every page, no page carries an em dash or a personal mailbox, a page name cannot leave `docs/`, the help command prints a page as written and refuses an unknown one, every argument and command explains itself, and the written reference matches the parsers. `tests/test_shell.py` opens every guide the header links and renders every page of the manual with its title in the tab.

Found and fixed on the way:

- The page inventory showed three components not connected: a draft invoice's reference could not be changed from its page although the route existed; a draft invoice could not be deleted although the refusal said "a draft is deleted, not credited"; the register's year filter had no control. A rename form, a delete with its store function and confirmation (an issued invoice refuses), and a year select, with four tests.
- Copy: the geocode card named a count from the first deployment; the size refusal printed the raw stage key and the whole cost sentence; Home counted warnings as things to set before the first run while the doctor counted only what is missing. All three aligned, and the three runner tests that pinned the old refusal updated.
- Writing `tests/test_manual.py` for the new tests overwrote a tracked file of the same name; restored from git, the new tests moved to `tests/test_help.py`, lesson recorded.

The live check: every page of the manual answers 200 in the console, a path that tries to leave `docs/` answers 404, the register offers its year, `arpenteur help --list` names the 36 pages, and the quick doctor on the live installation is green except the Drymos campaign's empty templates folder, which is the known pending item.

The suite: 1,716 passed, 22 of them new, ruff clean.

### Stage 4, 14.10.2026

What exists now, in the configuration:

- Eight configuration tables live in the database as one overlay key each and are merged shallowly over the file's table of the same name, so a section saved on the page keeps the file's keys it did not mention. Secrets keep their environment precedence and are never stored.
- `arpenteur settings import-toml` moves those tables out of the file once, keeping what the database already holds unless forced; `settings show` prints them with the IBAN masked. The command ran here, the values read back, and the file now carries no identity: an empty issuer, no sender name, a neutral register contact, no programme, no catalogue rows, no prose card. A test pins the file as impersonal.
- A wrong model id, an unknown provider and a mistyped payment mode are refused at load with one sentence; a model named nowhere takes the current family's id for its role; the subscription is the default in the dataclass, the loader and the README alike.

What exists now, on the Settings page:

- Sixteen sections with an index at the top: identity, catalogue as rows, rate card, sending, mailbox, enrolment, register, model access and the secrets listed as set or not, beside the ones that were there. A field the environment decides renders disabled and keeps what the database held. A bad IBAN, a limit out of its bounds, a duplicate code, a lower price above the higher one and a brief that is not on disk are each refused with the reason.
- Weights saved open a new version only when they differ from the set in force; catalogue rows saved open a rate card only when the lines changed. Every save reaches the next run without a restart.

What exists now, for drafting and setup:

- The draft stage names no operator file: the message brief is a setting, and the invitation is read from the programme's templates folder, one file per language, parsed as a block without a subject and filled from settings. Every invitation a run needs is read before the first model call. The operator's three texts moved to their folder on disk, untracked.
- `health.py` holds every check a first run needs; the quick ones feed Home, the live ones sign in to the mailbox and open a session with the mail server, sending nothing, from `arpenteur doctor` or the Doctor page.
- A campaign's page lists its message files with their subject or their fault, each with an editor that refuses a file it cannot send and keeps the text typed; a new campaign can start from the example folder, copied under its own name.
- Model access runs the prompt over stdin, trusts only a Claude Code sign-in and the installed command on a Mac, and no longer selects the unwired provider.

The live check: every section renders on the real settings, the printed invoice still carries the issuer and its payment part from the database, the campaign page lists three messages, and the Doctor signed in to the mailbox and opened a session with the mail server. It also found two true gaps: the message brief was named nowhere after the move, so the operator's existing file was named in the database; the Drymos campaign's folder holds no message yet.

Found and fixed on the way:

- The prose rate card lived only in the file and the import carries rows, so it was saved into the overlay from a copy of the file before the clean.
- The invitation check fired on a deployment with no programme; it waits for one.
- The French invitation text, moved verbatim from the code, carried four turns the house rules refuse; it was rewritten on the way out.
- The route that saves the sending section was first named after the protocol, and five tests pin that no path but the two wave routes says "send" or "smtp"; it is `/settings/delivery`.
- Every test fixture naming a model id outside the family was renamed into it, since an id is now checked at load.

The suite: 1,694 passed, 40 of them new, ruff clean.

---

## A replacement for an address that bounced (19.10.2026)

The operator asked for a button that does what was done by hand for three bounced Limen addresses: look further, propose what was found, and let the operator accept or refuse each one. A refused proposal is never proposed again; an accepted one becomes the firm's address and can receive the wave's message.

Decisions:
- The domain verdict checks that a named mail server exists. `agencefj.ch` passed as deliverable with an MX record pointing at a host that does not exist; one function now gives the verdict for the list check, the hunt and the search.
- The search reuses the hunt's pieces: the firm's own pages under robots.txt, one paid web search asked for every address the firm publishes anywhere, and the role address at a domain that takes mail. Addresses outside the firm's domain are proposed with that said, since a person decides; the hunt itself still drops them.
- The message to an accepted address goes through the wave's own send route and stage, with the wave's typed confirmation. No third route sends.

- [x] Domain verdict: a mail server that does not exist, and a null MX, read as dead. Tests.
- [x] Schema: `intel.address_search` and `intel.address_proposal` (scout 0019); `crm.campaign_message.replacement_draft_id` (clients 0014).
- [x] Store: record a search, list proposals, accept (stores the address with its source) or refuse.
- [x] Stage `replace`: diagnose the domain, read the own pages, search, guess the role address, classify, store. Tests with a fake site and a fake model.
- [x] Console: "Find a replacement" on a bounced address, the proposals panel with Use and Refuse, a run for the search. Tests.
- [x] Wave: a bounced recipient links to its replacement; an accepted one is sent the wave's message through the wave's send route. Tests, the sending guards kept.
- [x] Command line: `arpenteur replace UID [--accept A | --refuse A]`, `campaign send CODE N --replacement UID`.
- [x] Manual: companies, campaigns, legal; the command reference regenerated.
- [x] Suite, ruff, a live search for the one bounced agency still without an address.

### Review, 19.10.2026

What exists now:

- A domain whose named mail server does not exist, or that declares a null MX, reads as dead in the list check, the hunt and the search alike.
- A bounced address says so on the company page, which then offers Find a replacement. The search runs as a run on its own lane: the firm's mail domains with their verdict, its own pages under robots.txt, one web search told which addresses bounced, and one desk guess per domain at a domain that takes mail. Each proposal names what it reaches, where it was found and what is uncertain; Use this address stores it with its source, Refuse keeps it from ever being proposed again.
- A wave's page lists its bounced recipients. Once a replacement is accepted, the recipient is sent the wave's own draft, word for word, through the wave's send route and stage with the wave's code typed again; no cooldown, allowed while paused, once per message. The company page links to the wave where its message bounced.
- `arpenteur replace UID` with `--show`, `--accept`, `--refuse`, and `campaign send CODE N --replacement UID`.

On the live database: the two replacements sent by hand this morning are recorded as accepted proposals and as their messages' replacements, saying they were made by hand. A real search for Axis Immobilier read its site, which still prints only the bounced `contact@`, ran three web searches that found nothing else, and proposed `info@axis-immobilier.com` as a guess, undecided.

The suite: 1,797 passed, 27 of them new, ruff clean.

## One test per campaign, not per wave (23.10.2026)

The operator received a test message before every wave of the fourth Limen round, 13 waves of the same three texts, and asked for the test to happen once. A wave keeps its four steps; the test step stops sending what the operator already received.

Decision: a variant is covered when an earlier wave of the same campaign, still marked tested, had a test of that variant delivered and holds a message with the same subject and body. The test step sends only the variants not covered, names the wave whose test covers each other one, and marks the wave tested when every variant is covered or was sent. A changed word, a failed test or a re-rendered earlier wave covers nothing. Approval stays per wave.

- [x] Tests first: a covered wave sends nothing and is tested; only a new variant is sent; changed words are tested again; a test that failed covers nothing.
- [x] Store: `campaigns.tested_in`, the earlier wave whose delivered test carried these words.
- [x] Stage: `test_wave` sends the uncovered variants only and logs the covering wave.
- [x] Manual: the wave steps in `docs/guide/campaigns.md`, the stage docstring.
- [x] Suite, ruff, then waves 9 to 13 of LIMEN-ROMANDIE-4 with no new test.

### Review, 23.10.2026

A wave's test step now sends only the variants no other wave of the campaign delivered as a test with the same words, names the covering wave for the others, and marks the wave tested. Waves 9 to 13 of LIMEN-ROMANDIE-4 sent no test: wave 8 had delivered both bilingual variants and waves 1 to 7 the French one. A failed test, a changed word or a re-rendered wave covers nothing; approval stays per wave.

The suite: 1,801 passed, 4 of them new, ruff clean.

## A mailbox is written to once (23.10.2026)

The operator decided that an address which received a message is never used again, after 180 days or ever. The firm and domain cooldown of 180 days stays; the mailbox itself is spent by its first accepted message. A bounced address is already excluded for good, so "sent and successful" reads as "accepted by the mail server once". Tests to the operator do not count; an attempt the server refused does not count.

- [x] Tests first: the list drops a mailbox written to 400 days ago, a pin cannot keep it, a sibling address of the same firm can still be kept; the send refuses it whatever the cooldown; a refused attempt and a test spend nothing.
- [x] List rule: an absolute address check `written`, before the cooldown.
- [x] Send guard: every send but a test refuses a mailbox that already received a message.
- [x] Manual and help: lists, campaigns, the cooldown flags.
- [x] Suite, ruff, then the next list (addresses found elsewhere, unclear, unconfirmed) built and sent as the provider allows. Stopped by the operator on 25.10.2026 after wave 2 bounced 8 of 16 guessed addresses; see tasks/lessons.md.

## A test reaches the operator once per text, whatever the campaign (24.10.2026)

Round 5 is a new campaign with the three texts of round 1, byte for byte, and its first wave sent two tests again. The operator wants the test once, at the start. The words are what a test proves, so a variant whose exact words were received as a test in any campaign is not sent again.

- [x] Test first: a second campaign with the same words sends no test and names the earlier campaign and wave.
- [x] `tested_in` looks across campaigns and returns the earlier wave's code and number.
- [x] Log line, docstrings and the Campaigns manual say "any campaign".
- [x] Suite and ruff.

**Review.** `tested_in` no longer filters on the campaign and returns the code and wave of the earliest delivered test of the same words. On the live campaign, round 5's waves 2 and 3 sent no test and named LIMEN-ROMANDIE wave 4, the round 1 wave that first carried these texts. Suite 1,806 passed, ruff clean.

## Round 6: the letting firms of Romandie (26.10.2026)

Rounds 1 to 5 wrote to `regie-core` only. The operator asked to send to the `regie-letting` firms of the six French-speaking cantons, of which 196 hold a published desk mailbox that passed the deliverability check and was never written to.

- [x] List LIMEN-ROMANDIE-6: round 4's rule (published desks, deliverable) with `--segment regie-letting=1`; refresh its DNS verdicts, build, read every kept mailbox for a person's name. 196 kept, every local part a desk or the firm's own name
- [x] Templates `templates/limen/fr.regie-letting.md` and `de.regie-letting.md`: the round 1 to 5 message with the owner's letting opening from the notes of `fr.md` and `de.md`. The vault prose hook refused three French paragraphs and the German lead-in to the link, inherited from the base files; those were rewritten in the letting files only
- [x] Campaign LIMEN-ROMANDIE-6 with round 4's settings: canton per wave, 20 per wave, FR and VS bilingual, 5 % bounce ceiling. 14 waves
- [x] Plan shown, then kept; waves 1, 5 and 14 rendered, their four variants tested to the operator on 26.10.2026
- [x] The operator approved on 26.10.2026; waves 1 to 11 sent the same day, 169 firms, bounces read before every wave, none from this round
- [ ] Waves 12 to 14, 24 firms in VD and JU, once the 24-hour quota has room: from 27.10.2026 about 13:45 UTC

Three addresses were refused at submission, their domains dead since the list's DNS check of early October: `immovisiongeneve.ch` and `troene-immo.com` (domain not found), `acres.ch` (null MX). A refused message keeps its wave unsent and blocks the next one, and no command drops it: each was recorded dead through `verify.run` and dropped from its wave with `drop_message` and a dated reason. A DNS pass over every remaining domain found no other. Two gaps to decide on: a list's DNS verdicts are re-checked only past 30 days, and a wave has no command to set aside a message the server refused for good.

## A test is keyed on each language's text, not on the message (26.10.2026)

Round 6 sent the operator four tests carrying two texts: French then German, German then French, French alone, German alone. The gate compared whole messages, so a bilingual message and the single-language messages made of the same words counted as four texts. The operator's rule since 23.10.2026 is that a test proves words, and words already received are not sent again.

- [x] Tests first: a wave holding both bilingual orders sends one test; a single-language message whose text went inside a bilingual test sends none; a word changed in one language sends only the message carrying it
- [x] `delivered_tests` replaces `tested_in`: every delivered test of a tested wave, with its words; `template.versions` splits a message back into its language versions, matched whole
- [x] `test_wave` reads each variant's language texts from the templates, skips a variant whose every text was received before or rides in a message chosen earlier in the same run
- [x] Manual (campaigns guide, step 2) and the stage docstrings
- [x] Suite and ruff

**Review.** Four new or rewritten tests failed on the old gate at the count of tests sent, and pass now. On round 6's live data, a read-only check finds each language text of waves 1, 5 and 14 in another wave's test, so wave 1's French and German message alone would have covered all four variants. Suite 1,809 passed, ruff clean.
