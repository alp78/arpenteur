# ARPENTEUR

The arpenteur is the surveyor who paces out a territory parcel by
parcel. This one surveys a market from the Swiss commercial register: it
builds a qualified map of a territory (who exists, which segment, who
decides, how to reach them) from public sources, then prepares
personalised approaches for human review.

What the survey is *for* is configuration rather than code. The territory,
the segment vocabulary and what counts as a signal all live in
`arpenteur.toml`, so the same pipeline serves a consultancy scouting for
clients, a recruiter reading for hiring signals, an association looking
for members or a distributor looking for partners. The examples shipped
are one campaign's; they are meant to be replaced.

Pipeline stages, each a standalone command:

1. **source** — companies from the Zefix public register, by canton and
   legal form *(built)*
2. **sync** — keep that copy current from the FOSC change feed, one
   request per day rather than one per company *(built)*
3. **profile** — sort companies into segments from their registered
   purpose text *(built)*
4. **people** — officers from the SHAB notices the register publishes
   alongside each company *(built)*
5. **enrich** — find and read a company's own website: offerings, IT
   signals, partnerships, published email *(built)*
6. **match** — score each company against `campaign.md`, with the
   reasoning and what to open with *(built)*
7. **reach** — a point of contact: published addresses *(built)*, then
   the domain's own naming pattern, then a paid lookup for the
   remainder *(planned)*
8. **draft** — outreach proposals, always human-reviewed, never
   auto-sent *(planned)*

And a **console** over the result: `arpenteur console`.

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

## The territory

A survey covers a set of cantons and a set of legal forms. Name a region, or
give cantons directly; both live in `arpenteur.toml` and both can be
overridden per command.

```bash
uv run arpenteur regions           # what is defined, and what is active
uv run arpenteur legal-forms       # the registry's catalogue, live

uv run arpenteur source --region geneve
uv run arpenteur sync   --region suisse
uv run arpenteur source --canton VD,GE --legal-form 0107
```

`[regions]` in `arpenteur.toml` is an ordinary table you can extend:
`romandie`, `arc-lemanique`, `suisse` and the single cantons ship with it
because they are the obvious ones, not because the code knows them.

The territory is not only what gets swept. It is also what `sync` will
**admit**: a company registered inside it joins the survey when its first FOSC
notice appears, so new incorporations arrive without re-sweeping. Set
`admit_new = false` (or pass `--no-admit`) to freeze the population at what
the sweep found while still keeping those companies current. A company already
held is always maintained, whatever the territory says now, so narrowing the
scope never leaves rows silently frozen.

An unknown region name is an error rather than an empty list, because an empty
canton list means "no restriction": `--region romandy` would otherwise sweep
Switzerland.

**On legal forms.** `0101`, the raison individuelle, is registered in a natural
person's own name, so its entry is personal data in a way a company's is not,
and a campaign addressing sole traders carries obligations under the nLPD that
one addressing legal entities does not. Include it or not according to what the
survey is for. The tool ships with it on and does not decide for you.

### Offline fixture mode

While Zefix credentials are pending, exercise the complete source and hydration
path with two bundled, recorded-style responses. It makes no network requests
and needs no secrets:

```bash
uv run arpenteur source zefix --fixture
uv run arpenteur stats --fixture
```

The fixture source defaults to its small VD/Sàrl sample. It writes to
`data/arpenteur.fixture.sqlite3`, never the live survey database, and marks
every row with a `fixture://zefix/...` provenance URL. To run its passes
separately, use `source zefix --fixture --no-hydrate` followed by
`hydrate --fixture`.

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

# score what was researched against campaign.md, with an approach for each
uv run arpenteur match --limit 20

# read those sites for a published address. No model, so no cost per company
uv run arpenteur reach --band high

uv run arpenteur stats

# then read, correct and annotate the result in a browser
uv run arpenteur console
```

The segment definitions in `arpenteur.toml` *are* the classifier's
prompt, and `enrich.signal_brief` *is* what the web research looks for.
Both are the operator's own words: editing them changes what the survey
sorts for and what it reports, without touching code.

## The campaign brief

`campaign.md` at the repo root is what `match` scores companies against: what
this campaign offers, who it serves well, who it serves badly, what to look
for, what warns off, and how it opens a conversation. It is read verbatim into
the prompt.

It ships filled in as an example and says so at the top. **Replace it.** A
brief written for a different campaign produces confident scores built on
nothing, which is worse than no scores. Write it in the language you will
write to companies in; the approach advice comes back in the same one.

It is committed and versioned because the score is only as good as this file,
and when a run produces surprising results the first question is what changed
here, which a diff answers and a database field would not. Every score stores
a digest of the brief it was taken against, so rewriting the brief invalidates
the scores rather than leaving old and new sitting together looking equally
current: the next `match` re-scores exactly what the rewrite made stale.

## Finding an address

```bash
uv run arpenteur reach --band high --dry-run   # which sites would be read
uv run arpenteur reach --band high
```

`reach` opens the websites `enrich` found and takes the addresses the company
publishes on them. It costs nothing per company: an address is a `mailto:`
href or a string with an `@` in it, so a regex finds it, always gives the same
answer twice, and can name the exact page it came from. `enrich` already reads
these sites with a model at around forty centimes each and returned no address
at all on the companies it has run on, which is the honest argument for not
using one here.

Home page first, then a handful of links that look like they carry contact
details, ranked so a page called `contact` is read before an `about` page. A
site whose navigation is JavaScript serves markup with no links in it at all,
so when there are none to follow the sitemap is used instead: that is what a
sitemap is for, and it is cheaper and politer than crawling. The first live
run found nothing on such a site while its address sat on `mentions-légales`,
which is also why the hints are matched with accents and percent-encoding
folded away.

**robots.txt is honoured.** Everything else this tool fetches is either an API
it holds credentials for or a fetch Anthropic's servers perform. This is the
one stage that opens a stranger's website itself, and a tool that reads
company sites without asking is a scraper whatever its rate limit says. A site
with no robots.txt is treated as allowing; absence of a rule is not a
prohibition.

Every address stores the page it was on, so a suppression decision months
later can be checked against what was actually published. `method` is
`published`, which is not in the derived set: an address copied from a
`mailto:` link is the company's own string, not our reading of anything.

Having looked is recorded separately from having found. A site publishing only
a web form is a settled answer, not a company to re-fetch on every run, and
those companies are counted as waiting for the pattern tier rather than
reported as done. Re-running `enrich` onto a different URL clears the marker;
onto the same URL it does not.

The other two tiers are not built. `pattern` would infer the house convention
from an address already on the domain and the officers in `person`, and needs
a way to verify a guess before it is stored as a fact. `provider` is a paid
lookup and needs an account and a processing agreement read first. What each
still needs is written at the foot of `arpenteur/stages/reach.py`.

## The console

```bash
uv run arpenteur console          # http://127.0.0.1:8000
```

Filter the survey by canton, segment, fit band and pipeline state; open a
company to see its register entry, officers, web research, fit reasoning and
suggested opening; correct a label, set a state and an owner, log a call, or
suppress an address.

A correction made here goes through the same helpers the stages use, so it is
the same row a re-run has to respect. Labels set by hand are marked and the
classifier will not overwrite them.

**It cannot send anything.** Not a disabled button: there is no route, no mail
configuration and no `sent_at` write anywhere in the codebase. The suggested
opening is shown for a human to rewrite and send from their own mail client,
under their own name. Swiss law (LCD art. 3 al. 1 let. o) draws the line at
*mass* advertising email without prior consent, and the defence against ever
crossing it is that the software has no mechanism, which a test asserts.

**It runs the stages, and shows the work.** `/run` starts any stage and drops
you on its log, which fills in as the stage talks. Every stage already took an
injectable `log` callable, so this swapped the sink rather than touching a
single stage's internals: lines go to a `run_line` table, which means the log
survives a reload, a run started from the command line is watchable in the
same view, and a stage that emits sixty thousand lines is polled for only what
the page has not seen.

The objection was never to the button but to spending real money from a page
that looks like a filter, so the guards that made the command line safe are
enforced on the server, in `console/runner.py`:

- **One at a time.** Two `enrich` runs would spend twice and interleave their
  writes.
- **A declared size.** `enrich` and `match` will not start without a limit, a
  named list of UIDs, or a dry run. Checked in the handler, because a
  `required` attribute is a convenience for the person, not a constraint on
  the request.
- **A dry run offered first**, and ticked by default. Note that `profile` is
  the exception and the form says so: its dry run still calls the model for
  every batch and skips only the write.
- **Every stage's own refusal still applies.** This adds guards, it does not
  replace them: a wide `enrich` is still refused by `enrich`.

A run is a thread inside the console, so stopping the console kills it. Those
runs are settled as interrupted at the next startup rather than left claiming
to be running; a run started from the command line is left alone, since it may
belong to a process still working in another terminal.

It binds to localhost and has no authentication, because there is no intent
that it be reachable. The database holds contact details for thousands of
companies and, where sole proprietorships are included, for identifiable
people. Exposing it wants a reverse proxy with auth in front.

## Staying current

A copy of the register goes stale the moment it is taken. Asking 183'361
companies whether anything has changed is 183'361 requests, and it would have
to be repeated for ever.

It is also unnecessary. Nothing changes in the Swiss commercial register
without a notice in the FOSC, and Zefix publishes those a day at a time for
the whole country. So the local copy is a subscription rather than a poll, and
staying current costs **one request per elapsed day** no matter how many
companies are held.

```bash
# resume from the last day consumed, up to today
uv run arpenteur sync

# or a named range, and a dry run that fetches nothing
uv run arpenteur sync --since 2026-08-04 --until 2026-08-10
uv run arpenteur sync --dry-run

# notices say which companies moved; this fetches only those
uv run arpenteur hydrate
```

Measured on 11.08.2026: seven days of the feed is 5'001 notices nationwide, of
which 1'050 concerned companies in this survey, for seven requests.

**Running it twice does nothing twice.** Days already consumed are recorded
and skipped, companies are keyed on UID, and notices are keyed on
`(company, sogc_id)` so the schema refuses a duplicate rather than trusting
the caller to check. Forced to re-read the same week with `--no-resume`, the
second pass reports 1'050 companies seen and 0 changed, and writes no rows.
That is what makes the sync safe to put on a timer.

**The verdict is per company per day, not per notice.** A company can appear
in several notices on one day whose short records disagree; applying each in
turn flips the row, and every flip would read as an amendment. Found live: a
re-read of an unchanged day reported six changes that way. The digest is now
taken once before the day and compared once after.

**Each notice carries the company with it,** so a rename, a move or a striking
off is applied from the feed alone. Only `purpose` and `address` need the
detail call, and only for the companies actually named: `sync` flags them,
`hydrate` serves them first, and the flag clears only once the row is written,
so an interrupted run leaves the work queued rather than losing it.

**Nothing is ever deleted.** A striking off is a change of `status`. A
prospect that has closed is a fact worth keeping, and dropping the row would
take its notices, its officers and any notes on it as well.

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
they reserve is blocking that user.

This is the constraint that shaped the design rather than a note bolted on to
it. The sweep is wide and cheap, a few hundred requests for all of Romandie.
Keeping the copy current uses the FOSC change feed at one request per day
instead of one per company, which is the difference between 7 requests a week
and 183'361. Hydration, the one pass that is genuinely per company, refuses to
run over `POLITE_HYDRATION_LIMIT` rows unless you pass `--limit` or `--bulk`,
and a backfill over more than `POLITE_DAY_LIMIT` days of feed has to be asked
for by date. All traffic goes through `PoliteClient`: rate limited, backing
off on 429 and 5xx, and caching on disk, for as long as the caller says a
cached copy may answer.

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
protocol is the seam. `reach` is tested the same way against a fake HTTP
client that records every URL it was asked for, which is how the claims
about robots.txt and the page budget are actually checked rather than
asserted in prose.
