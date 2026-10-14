# The survey, and the register it reads

> The figures on this page were measured on the first deployment, in 2026, over a survey of five cantons of French-speaking Switzerland. Yours will differ.

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

## Sector and segment

The sector and segment definitions in `arpenteur.toml` *are* the
classifier's prompt, and `enrich.signal_brief` *is* what the web research
looks for. All three are the operator's own words: editing them changes what
the survey sorts for and what it reports, without touching code.

**Sector and segment are two different questions asked in one call.** A
*sector* is what a company does for a living: one of them, from a closed
list, so `--sector hospitality` is a filter rather than a search for the word
"restaurant" in a purpose text that says *exploitation d'établissements
publics*. A *segment* is how a company fits this campaign, several at once,
because a regulated laboratory is both `compliance-sector` and `data-driven`
and forcing one label throws away the more interesting half. Both come from the
same reading of the same purpose text, so the sector costs a few output tokens
rather than a second pass over the register.

## The scoring brief

`campaign.md` at the repo root is what `match` scores companies against: what
this campaign offers, who it serves well, who it serves badly, what to look
for, what warns off, and how it opens a conversation. It is read verbatim into
the prompt.

The repository ships `campaign.example.md`, a worked example for a fictional business. Copy it to `campaign.md` and **replace every section**: a brief written for a different business produces confident scores built on nothing, which is worse than no scores. Write it in the language you will write to companies in; the approach advice comes back in the same one.

`campaign.md` is yours: it stays out of git, and it is edited in the console under Settings, Scoring brief, and written back to the file.

Every score stores a digest of the brief it was taken against, so rewriting the brief invalidates the scores rather than leaving old and new sitting together looking equally current: the next `match` re-scores exactly what the rewrite made stale.

## Finding an address

```bash
uv run arpenteur reach --band high --dry-run   # which sites would be read
uv run arpenteur reach --band high
```

`reach` opens the websites `enrich` found and takes the addresses the company
publishes on them. It costs nothing per company: an address is a `mailto:`
href or a string with an `@` in it, so a regex finds it, always gives the same
answer twice, and can name the exact page it came from. `enrich` already reads
these sites with a model at around a franc each and returned no address
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
# resume from the last day consumed, up to the current day
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
detail call, and only for the companies actually named: `sync` marks them,
`hydrate` serves them first, and the flag clears only once the row is written,
so an interrupted run leaves the work queued rather than losing it.

**Nothing is ever deleted.** A striking off is a change of `status`. A
prospect that has closed is a fact worth keeping, and dropping the row would
take its notices, its officers and any notes on it as well.

## The terms of the register's API

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
off on 429 and 5xx, caching on disk for as long as the caller says a
cached copy may answer, and identifying itself with the contact set under
Settings, Register.

**The data carries no warranty and has no legal effect.** Only the certified
cantonal excerpt and the FOSC publication text are binding, and the OFRC
accepts no liability for unavailability. Nothing this tool emits may read as
though the register vouches for it. That matters most for `person`: those rows
are a parse of French registrar prose, not registry fields, and a parse can
name someone who has since resigned.

**No support is provided** for building against the API.

## Provenance, refusals and politeness

**Provenance is per row.** Every table that holds a fact about the
outside world carries `source_url`, `method` and `fetched_at`. Not a
separate provenance table, because "where did this come from and when"
is asked of single rows, and a join would let a row exist without an
answer. Under the nLPD that answer is the difference between a
defensible dataset and an indefensible one.

**Sweeps narrow on refusal, not on truncation.** Zefix answers a
name-prefix search, not "list everything". It never truncates:
a query that is too broad comes back `400 RESULTLIST_TO_LARGE` and
returns nothing at all. So the sweep walks `a*` … `9*` and deepens
(`aa*`, `ab*`, …) only the prefixes that were refused, to a depth of six.
This distinction is the whole design. Assuming truncation would mean
reading a refusal as an empty result and silently dropping every company
under that letter; the largest response actually observed was 4874 rows,
so the ceiling is real but never signalled by a short answer.

**The stages that cost money refuse to run wide.** `enrich` takes an
explicit UID list or a filter, and a filter that resolves to more than
200 companies raises rather than spends. `--limit` is how you say you
meant it.

Its bill is tokens, not searches, which took a measurement to establish.
The cost was assumed to be per search because that is the conspicuous
thing about a search tool, and only searches and fetches were recorded.
One company measured on 12.08.2026 used four searches and **465'812 input
tokens**: the tokens were about a franc and the searches a few centimes.
Every search result read comes back as input on the following turn, and a
turn the model pauses and resumes re-sends the whole conversation, so a
company that searches hard costs superlinearly. Runs now record tokens
and continuations alongside the search counts, so the next figure is read
off a run rather than remembered.

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
