# Arpenteur handover

Written 08.08.2026 at the end of the session that created the repo. Everything
below is verified against the working tree, not remembered.

## What this is and why it exists

Arpenteur surveys the Swiss SME market from public sources: who exists, which
segment they fall into, who decides, how to reach them, and a drafted message
a human reviews before anything is sent.

It started as a reverse-engineering of **Explee** (explee.com), a paid tool
that claims AI-driven prospecting. Taking its funnel apart showed the machine
underneath is four rented ingredients: crawl the customer's site, have an LLM
invent ICP segments, filter a licensed scraped-contact database, then run an
email-finder waterfall (their UI shows the badges: hunter, exreacher,
findymail, leadmagic) and mail-merge with an LLM.

The bet here is that for Switzerland specifically you can source **better data
legally and nearly free**, because Swiss SMEs are unusually well documented in
public registries. Zefix gives the company universe; cantonal excerpts name the
officers, so for an SME the decision maker is a matter of public record rather
than a scraped guess.

Owner context: the tool serves an independent IT consultancy in Suisse romande
(peringer.ch). It may later become a product in its own right.

## Where it stands

Foundation is **built, tested and uncommitted**. `uv run pytest` is 19 passed.
The tree has no commits yet: `git log` says *"your current branch 'main' does
not have any commits yet"*, and every file is staged with `A`.

**First thing the next session should do is make that first commit.**

```
uv run pytest                 # 19 passed, no network needed
uv run arpenteur --help
uv run arpenteur init-db && uv run arpenteur stats
```

Built and working:

- `arpenteur/config.py` — `arpenteur.toml` (committed, describes the survey)
  plus `.env` (secret, never committed). Minimal hand-rolled env reader; real
  environment variables win over the file so a systemd unit can override.
- `arpenteur/db.py` + `arpenteur/migrations/0001_init.sql` — SQLite, migrations
  keyed on `PRAGMA user_version`.
- `arpenteur/http.py` — `PoliteClient`: token-bucket rate limit, retry with
  backoff, on-disk response cache under `data/http-cache`, identifying
  User-Agent.
- `arpenteur/llm.py` — provider interface; `anthropic` implemented, an
  `infomaniak` slot left deliberately unwired.
- `arpenteur/sources/zefix.py` — client, catalogue lookup, prefix walker.
- `arpenteur/stages/source.py` — sweep and hydrate, resumable.
- `arpenteur/stages/profile.py` — segment classification from purpose text.
- `arpenteur/stages/{people,reach,draft}.py` — stubs that print which phase
  will add them.
- `arpenteur/cli.py` — `init-db, source, hydrate, profile, stats, people,
  reach, draft`.
- `tests/` — 19 tests on recorded fixtures, no network.

## The one thing blocking real data

**Zefix API credentials.** Auth is HTTP Basic; credentials are free but issued
by a human. A French request email was drafted this session and is ready to
send to **zefix@bj.admin.ch** — it is saved next to this file as
`zefix-email.txt`. Nothing has been sent yet.

Until they arrive: tests run on fixtures, `source` and `hydrate` fail fast with
a message pointing at `.env.example`. Everything else works.

When they arrive, put them in `.env` and smoke-test on the smallest canton
first, not the whole territory:

```
uv run arpenteur source --canton VS --legal-form sarl
uv run arpenteur hydrate --limit 50
uv run arpenteur stats
```

## Design decisions already taken (do not relitigate without reason)

- **Zefix only** for sourcing in this phase. Google Places (needs a GCP key and
  billing), website crawling, cantonal excerpt parsing, the email waterfall and
  any sending machinery are later phases.
- **LLM: Anthropic, behind a swappable interface.** Haiku classifies, Sonnet
  drafts. Infomaniak (Swiss-hosted open models) is the intended second provider
  because it fits the owner's sovereignty positioning; the slot exists, the
  implementation does not.
- **Provenance is per-row, not a provenance table.** Every table holding an
  outside fact carries `source_url`, `method`, `fetched_at`. A join would let a
  row exist without an answer to "where did this come from"; under the nLPD
  that answer is the difference between a defensible dataset and an
  indefensible one.
- **A company can hold several segments at once.** A regulated laboratory is
  both `compliance-sector` and `data-driven`; forcing one would be a lie.
- **Sweep is resumable by construction.** The `sweep` table records each
  completed `(canton, legal_form_uid, prefix)`. A full romand sweep is tens of
  thousands of requests and will be interrupted.
- **Nothing sends by itself.** `draft.sent_at` stays null unless a human acts.
  `contact.suppressed` is honoured before any draft is produced.

## Legal frame, which is a design constraint here

- **LCD art. 3 al. 1 let. o** prohibits *mass* advertising email without prior
  consent. Individually researched, personalised B2B contact to a published
  professional address sits in the tolerated zone; volume automation does not.
  The tool is deliberately built to stay on the research side: low volume,
  human review, no autopilot.
- **nLPD** applies to the names and addresses collected. Keep the dataset
  minimal, keep provenance, honour suppression and deletion.

These are not decoration. If a future phase adds a "send all" button it breaks
the premise the whole design rests on.

## Environment

- WSL2 Ubuntu, repo at `/home/alex/arpenteur` on **ext4** — never under
  `/mnt/c`, where cross-OS file I/O is an order of magnitude slower and a
  crawler writing many small files would feel it.
- Python 3.13.13, SQLite 3.50.4, `uv` at `~/.local/bin/uv`, systemd running
  (for a timer in a later phase).
- git identity is set per-repo to `alp78 / alexper.recovery@gmail.com`,
  matching `~/quant`. The WSL global git config is empty.
- `.git/info/exclude` already ignores `.claude/`, `CLAUDE.md`, `AGENTS.md`,
  `_scratch/` — so this handover is local and never committed.
- `.gitignore` covers `.env*`, `.venv/`, `__pycache__/`, `data/`, `*.sqlite3`.

## House conventions carried from the owner's other work

Project memories do not follow across repos, so the ones that matter here:

- **Never add `Co-Authored-By: Claude`** or any AI attribution to commits.
- **Work on `main`.** No feature branches, no PRs, commit and push directly.
- **Never hard-wrap prose** in anything meant to be read or copied (this file
  is documentation, so it wraps; emails and chat answers must not).
- Prose style: no em-dashes, no title ending in a period. French copy follows
  Swiss typography (NBSP before `: ; ? !`, curly apostrophes, dates as
  `08.08.2026`).
- Comments explain **why**, not what. The existing modules set the standard;
  match their density rather than stripping it.

## Suggested next steps, in order

1. **Commit the foundation.** It is finished and green and sitting unsaved.
2. **Send the Zefix email** (`_scratch/zefix-email.txt`), after adding the
   signature name and a postal address.
3. While waiting on credentials, either:
   - the **people** stage design: cantonal excerpt sources differ per canton
     and this is the real research problem, or
   - a `--fixture` mode for `source` so the pipeline can be exercised
     end-to-end without live access.
4. On credentials: smoke-test one canton, confirm the real `page_cap`
   (currently 1000 in `arpenteur.toml`, an assumption, and the sweep's
   deepening logic depends on it), then sweep the territory.
5. `profile` needs an `ANTHROPIC_API_KEY` to run for real; the parsing is
   already tested against a canned response.

## Known open questions

- **`page_cap = 1000` is unverified.** It is a guess from the API docs. If the
  real cap is lower, sweeps silently lose the tail of common prefixes. Confirm
  on the first live run by finding a prefix that hits it.
- **No delta refresh yet.** `GET /api/v1/sogc/bydate/{date}` is the daily
  mutations feed and is the right way to keep a survey current instead of
  re-sweeping. Not implemented.
- **Registry excerpt access per canton is unresearched.** Some cantons publish
  excerpts freely, some charge, some rate-limit. This decides whether the
  `people` stage is cheap or hard.
- **The name.** `arpenteur.ch` was free on 08.08.2026 and is not registered.
  If this becomes a product, register it and run a Swissreg trademark search
  in classes 9/35/42.
