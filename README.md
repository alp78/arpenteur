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
3. **people** — officers from the SHAB notices the register publishes
   alongside each company *(built)*
4. **enrich** — find and read a company's own website: offerings, IT
   signals, partnerships, published email *(built)*
5. **reach** — a point of contact: published addresses first, then the
   domain's own naming pattern, then a paid lookup for the remainder
   *(planned)*
6. **draft** — outreach proposals, always human-reviewed, never
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

**Anthropic** powers the `profile`, `enrich` and `draft` stages. There are
two ways to pay for it, and `llm.auth_mode` in `arpenteur.toml` decides
which is used:

| `auth_mode` | Credential | For |
|---|---|---|
| `subscription` | a local `ant auth login` sign-in | running it yourself on a Claude plan |
| `api_key` | `ANTHROPIC_API_KEY` in `.env` | a published copy, a server, CI |
| `auto` *(default)* | the key if set, otherwise the sign-in | either |

#### Signing in with a Claude subscription

Install the `ant` CLI, then sign in once. macOS:

```bash
brew install anthropics/tap/ant
```

Linux or WSL, where there is no Homebrew. `~/.local/bin` needs to be on
your `PATH`, which avoids needing `sudo`:

```bash
VERSION=1.22.1
curl -fsSL "https://github.com/anthropics/anthropic-cli/releases/download/v${VERSION}/ant_${VERSION}_linux_amd64.tar.gz" \
  | tar -xz -C "$HOME/.local/bin" ant
ant --version
```

Newer releases are on the [releases page](https://github.com/anthropics/anthropic-cli/releases).
Then:

```bash
ant auth login      # opens a browser
ant auth status     # shows which credential and org won
```

That writes a profile to `~/.config/anthropic/`, which the Python SDK
finds by itself. Nothing is copied into `.env`: the token is short lived
(hours), the SDK refreshes it, and this codebase never reads it.

Two things worth knowing. Claude Code may warn about an auth conflict
afterwards, because its own `/login` credential lives elsewhere
(`~/.claude/`) and is a separate sign-in for the same account; that is
expected. And an `ANTHROPIC_API_KEY` that is set at all wins over the
profile, so unset it before expecting the subscription to be used.

#### Using an API key instead

Put a key from [console.anthropic.com](https://console.anthropic.com) in
`.env` as `ANTHROPIC_API_KEY`. Nothing else changes: this is the path a
published copy of the tool takes, and it needs no `ant` install.

Either way, `enrich` bills per web search rather than per token, so start
a run with `--limit 2` before pointing it at a shortlist.

## Running a survey

```bash
# sweep a canton for one legal form, then fetch the detail for each hit.
# 0107 is the eCH-0097 code for a Sàrl; 0106 is a SA, 0101 a raison
# individuelle. Codes rather than names because the catalogue's short
# name carries an accent (`Sàrl`) and is easy to mistype.
uv run arpenteur source zefix --canton VS --legal-form 0107

# a sweep is resumable: re-running skips prefixes already covered
uv run arpenteur source zefix --canton VS --legal-form 0107

# sort what was found into segments (--dry-run prints without writing)
uv run arpenteur profile --limit 100 --dry-run
uv run arpenteur profile

# officers, parsed from the SHAB notices already stored. No network, no cost.
uv run arpenteur people

# websites and offerings, for a shortlist. This one costs money per company,
# so check the shortlist before paying for it.
uv run arpenteur enrich --canton VS --segment data-driven --dry-run
uv run arpenteur enrich --canton VS --segment data-driven --limit 20

uv run arpenteur stats
```

Both the territory (`survey.cantons`, `survey.legal_forms`) and the
segment vocabulary live in `arpenteur.toml`. The segment definitions in
that file *are* the classifier's prompt, so editing them changes how
companies are sorted.

## Terms of the Zefix API, and how this tool meets them

Access is granted free on conditions, stated by the OFRC when credentials are
issued. They are not boilerplate: two of them constrain what may be said with
the data, and one of them can cost you the account.

**Use is free.** No further obligation.

**Origin must be indicated, and any modification of the data referenced.**
`arpenteur/attribution.py` holds the notices, keyed by the `method` column
that every row carries. Output built only from registry fields gets the source
and no-warranty notice; output built on anything derived also declares that.
This is a condition of use rather than a courtesy, so it has tests.

**Regular mass querying should be renounced.** There is no documented rate
cap, but the reasoning given is concrete: the web application, REST API and
mobile app share one system, the web app alone serves over 400'000 requests a
day, and a user who disturbs it degrades service on every route. The remedy
they reserve is blocking that user. So the sweep is wide and cheap (a few
hundred requests for all of Romandie), and hydration, which is one request per
company, refuses to run over `POLITE_HYDRATION_LIMIT` rows unless you pass
`--limit` or `--bulk`. All traffic goes through `PoliteClient`: rate limited,
backing off on 429 and 5xx, and caching on disk so a repeated question is
asked once.

**The data carries no warranty and has no legal effect.** Only the certified
cantonal excerpt and the FOSC publication text are binding, and the OFRC
accepts no liability for unavailability. Nothing this tool emits may read as
though the register vouches for it. That matters most for `person`: those rows
are a parse of French registrar prose, not registry fields, and a parse can
name someone who has since resigned.

**No support is provided** for building against the API.

## Design notes

**Provenance is per row.** Every table that holds a fact about the
outside world carries `source_url`, `method` and `fetched_at`. Not a
separate provenance table, because "where did this come from and when"
is asked of single rows, and a join would let a row exist without an
answer. Under the nLPD that answer is the difference between a
defensible dataset and an indefensible one.

**Sweeps narrow on refusal, not on truncation.** Zefix answers a
name-prefix search, not "list everything". Crucially it never truncates:
a query that is too broad comes back `400 RESULTLIST_TO_LARGE` and
returns nothing at all. So the sweep walks `a*` … `9*` and deepens
(`aa*`, `ab*`, …) only the prefixes that were refused, to a depth of six.
This distinction is the whole design. Assuming truncation would mean
reading a refusal as an empty result and silently dropping every company
under that letter; the largest response actually observed was 4874 rows,
so the ceiling is real but never signalled by a short answer.

**The stages that cost money refuse to run wide.** `enrich` bills per web
search, so it takes an explicit UID list or a filter, and a filter that
resolves to more than 200 companies raises rather than spends. `--limit`
is how you say you meant it. `site.searches` and `site.fetches` record
what each company actually cost, so a run can be priced afterwards.

**A found website is a guess until it is not.** Swiss SME names collide,
so `enrich` records `status` and `confidence` beside every result, and
only a `found` result may contribute an email to `contact`. A namesake's
address reaching a draft is the failure this prevents.

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

No network and no credential required. The Zefix client runs against
recorded fixtures in `tests/fixtures/`; the sweep, migrations, the SHAB
officer parser and the classification parsing are tested directly; and
`enrich` runs end to end against a fake provider, since the provider
protocol is the seam.
