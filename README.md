# ARPENTEUR

The arpenteur is the surveyor who paces out a territory parcel by
parcel. This one surveys markets for Swiss SMEs: it builds a qualified
map of a territory (who exists, which segment, who decides, how to
reach them) from public sources, then drafts personalised outreach for
human review.

Pipeline stages, each a standalone command:

1. **source** — companies from the Zefix public register, by canton and
   legal form *(built)*
2. **profile** — sort companies into segments from their registered
   purpose text *(built)*
3. **people** — decision makers from cantonal register excerpts and
   published team pages *(planned)*
4. **reach** — a point of contact: published addresses first, then the
   domain's own naming pattern, then a paid lookup for the remainder
   *(planned)*
5. **draft** — outreach proposals, always human-reviewed, never
   auto-sent *(planned)*

Storage is SQLite. Secrets live in `.env` (never committed). Outreach
volume stays low by design: Swiss law (LCD art. 3 al. 1 let. o) draws
the line at mass advertising email, and this tool is built to stay on
the research side of it.

## Getting started

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --dev
cp .env.example .env      # then fill it in, see below
uv run arpenteur init-db
uv run arpenteur stats
```

### Credentials

**Zefix** is the federal index of commercial register entries and the
source of the company universe. Access is free: email
<zefix@bj.admin.ch> to request a username and password, then put them in
`.env` as `ZEFIX_USER` and `ZEFIX_PASSWORD`. Until they arrive everything
except the `source` stage still runs, and the test suite runs entirely on
recorded fixtures.

**Anthropic** powers the `profile` stage. Set `ANTHROPIC_API_KEY`.

## Running a survey

```bash
# sweep a canton for one legal form, then fetch the detail for each hit
uv run arpenteur source zefix --canton VS --legal-form sarl

# a sweep is resumable: re-running skips prefixes already covered
uv run arpenteur source zefix --canton VS --legal-form sarl

# sort what was found into segments (--dry-run prints without writing)
uv run arpenteur profile --limit 100 --dry-run
uv run arpenteur profile

uv run arpenteur stats
```

Both the territory (`survey.cantons`, `survey.legal_forms`) and the
segment vocabulary live in `arpenteur.toml`. The segment definitions in
that file *are* the classifier's prompt, so editing them changes how
companies are sorted.

## Design notes

**Provenance is per row.** Every table that holds a fact about the
outside world carries `source_url`, `method` and `fetched_at`. Not a
separate provenance table, because "where did this come from and when"
is asked of single rows, and a join would let a row exist without an
answer. Under the nLPD that answer is the difference between a
defensible dataset and an indefensible one.

**Sweeps are resumable and cap-aware.** Zefix answers a name-prefix
search, not "list everything", and truncates long responses. The sweep
walks `a*` … `9*`, and any prefix that comes back at the cap is deepened
(`aa*`, `ab*`, …) rather than trusted. Truncated prefixes are recorded
as `deepened` so a resumed run knows their rows are incomplete. Without
this, companies past the cap are invisible and nothing ever raises an
error.

**Two passes, not one.** The sweep is wide and cheap (one request per
prefix, many companies per response); hydration is one request per
company. Keeping them separate means an interrupted run never redoes the
wide pass to finish the narrow one.

**The HTTP client is polite by construction.** Rate limited to a couple
of requests a second, retrying with backoff on 429 and 5xx, caching every
response on disk under `data/http-cache`, and identifying itself with a
contact address. A public register is run for everyone.

## Tests

```bash
uv run pytest
```

No network required: the Zefix client runs against recorded fixtures in
`tests/fixtures/`, and the sweep, migration and classification-parsing
logic are tested directly.
