# ARPENTEUR

The arpenteur is the surveyor who paces out a territory parcel by
parcel. This one surveys a market from the Swiss commercial register: it
builds a qualified map of a territory (who exists, what trade they are in,
which segment, who decides, how to reach them) from public sources, then prepares
personalised approaches for human review.

What the survey is *for* is configuration rather than code. The territory,
the sector and segment vocabularies and what counts as a signal all live in
`arpenteur.toml`, so the same pipeline serves a consultancy scouting for
clients, a recruiter reading for hiring signals, an association looking
for members or a distributor looking for partners. The examples shipped
are one campaign's; they are meant to be replaced.

Pipeline stages, each a standalone command:

1. **source** — companies from the Zefix public register, by canton and
   legal form *(built)*
2. **sync** — keep that copy current from the FOSC change feed, one
   request per day rather than one per company *(built)*
3. **profile** — from the registered purpose text, place each company in
   one sector and against any number of segments, in one call *(built)*
4. **people** — officers from the SHAB notices the register publishes
   alongside each company *(built)*
5. **resolve** — work out which officer rows are the same human, and the
   boards each of them sits on *(built)*
6. **enrich** — find and read a company's own website: offerings, IT
   signals, partnerships, published email *(built)*
7. **match** — score each company against `campaign.md`, with the
   reasoning and what to open with *(built)*
8. **reach** — a point of contact: published addresses *(built)*, then
   the domain's own naming pattern, then a paid lookup for the
   remainder *(planned)*
9. **geocode** — turn the register's postal address into a coordinate,
   through the Confederation's own address search *(built)*
10. **draft** — outreach proposals, always human-reviewed, never
   auto-sent *(planned)*

Then the other half, for the companies that say yes: **clients**, their
**projects**, one auditable **record** of every exchange, a **calendar**,
**notes** in Markdown, a sourced **dossier**, an AI-suggested **approach**
priced from your own rate card, and **interview preparation** *(built)*.

And a **console** over all of it: `arpenteur console`.

Secrets live in `.env` (never committed). Outreach volume stays low by
design: Swiss law (LCD art. 3 al. 1 let. o) draws the line at mass
advertising email, and this tool is built to stay on the research side
of it.

## Modules

Arpenteur is composed rather than written as one program. Five modules,
each owning one Postgres schema and its own numbered migrations:

| module | schema | what |
|---|---|---|
| `scout` | `core`, `intel`, `audit` | the survey: sweep, detail, officers, sectors and segments, website research, scoring, and the record of every state change |
| `geo` | `geo` | the geocode stage, establishments with coordinates, the map |
| `clients` | `crm` | clients, projects, programmes, the interaction log, notes, dossier, approach, prep |
| `sales` | `commercial` | services, rate cards, quotes, invoices, time entries |
| `analytics` | `analytics` | views over everything else: the funnel and its cycle times, spend by stage, the pipeline, what is outstanding. Written to by nothing |

```bash
uv run arpenteur modules list          # what is installed and how far its schema has got
uv run arpenteur modules drop sales --yes
```

Three rules keep them separable, and the third is what makes the first
two hold.

**Dependencies point one way.** `commercial` → `crm` → `core`; `geo` →
`core`; `intel` → `core`. Nothing points back, so dropping a downstream
schema cannot orphan an upstream row.

**Modules meet at named slots, never by import.** A page declares a slot
(`company.panels`, `client.panels`, `nav.primary`); a module fills it. An
unfilled slot renders nothing, which is exactly what "that module is not
installed" should look like.

**Both rules are tests.** `tests/test_modules.py` walks the import graph
and reads Postgres's constraint catalogue. Architecture that is only
documented decays.

## Getting started

Requires Python 3.13, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
docker compose up -d      # PostgreSQL 16 + PostGIS, on 127.0.0.1:5433
uv sync --dev
cp .env.example .env      # then fill it in, see below
uv run arpenteur init-db
uv run arpenteur stats
```

The database is bound to loopback deliberately: the console has no
authentication because it is not meant to be reachable, and a database
listening on every interface would quietly undo that. `DATABASE_URL`
overrides the default for a real deployment.

Coming from the SQLite version:

```bash
uv run python scripts/import_sqlite.py data/arpenteur.sqlite3
```

It keeps every id, converts the columns SQLite could not type, and
prints both row counts side by side, because the only useful check on a
copy is that the two databases agree afterwards.

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

Either way, `enrich` is the expensive stage: about a franc and anywhere from
seven to forty-five minutes per company, measured. Start a run with
`--limit 2` before pointing it at a shortlist.

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

The fixture source defaults to its small VD/Sàrl sample. It writes to a
separate `arpenteur_fixture` database on the same server, created on
demand and never the live survey, and marks every row with a
`fixture://zefix/...` provenance URL. To run its passes
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

# or find one trade across the whole country instead of sweeping a territory.
# The register accepts a leading wildcard, so a word inside a name is
# searchable: `*tourisme*` answers with 19 companies in Valais where
# `Tourisme*` answers with none. Every legal form comes back, which is the
# point: the associations and public-law institutes a company survey never
# sweeps are where a trade like this one lives.
uv run arpenteur source --name '*tourisme*,*bergbahn*' --region suisse --dry-run
uv run arpenteur source --name-file words/drymos.txt --region suisse

# sort what was found into sectors and segments (--dry-run writes nothing)
uv run arpenteur profile --limit 100 --dry-run
uv run arpenteur profile

# officers, parsed from the SHAB notices already stored. No network, no cost.
uv run arpenteur people

# websites and offerings, for a shortlist. This one costs money per company,
# so check the shortlist before paying for it.
uv run arpenteur enrich --canton VS --segment data-driven --dry-run
uv run arpenteur enrich --canton VS --segment data-driven --limit 20

# or by trade rather than by fit: every restaurant in the territory
uv run arpenteur enrich --sector hospitality --limit 20

# score what was researched against campaign.md, with an approach for each
uv run arpenteur match --limit 20

# read those sites for a published address. No model, so no cost per company
uv run arpenteur reach --band high

uv run arpenteur stats

# then read, correct and annotate the result in a browser
uv run arpenteur console
```

The sector and segment definitions in `arpenteur.toml` *are* the
classifier's prompt, and `enrich.signal_brief` *is* what the web research
looks for. All three are the operator's own words: editing them changes what
the survey sorts for and what it reports, without touching code.

**Sector and segment are two different questions asked in one call.** A
*sector* is what a company does for a living — one of them, from a closed
list, so `--sector hospitality` is a filter rather than a search for the word
"restaurant" in a purpose text that says *exploitation d'établissements
publics*. A *segment* is how a company fits this campaign — several at once,
because a regulated laboratory is both `compliance-sector` and `data-driven`
and forcing one label throws away the more interesting half. Both come from the
same reading of the same purpose text, so the sector costs a few output tokens
rather than a second pass over the register.

## The campaign brief

`campaign.md` at the repo root is what `match` scores companies against: what
this campaign offers, who it serves well, who it serves badly, what to look
for, what warns off, and how it opens a conversation. It is read verbatim into
the prompt.

It ships filled in as an example and says so at the top. **Replace it.** A
brief written for a different campaign produces confident scores built on
nothing, which is worse than no scores. Write it in the language you will
write to companies in; the approach advice comes back in the same one.

It is edited in the console under Settings and written back to the file, so it
stays a versioned artefact rather than a database column.

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

## Clients, projects and the record

Everything above finds companies. This is the half that starts when one says
yes, and it is a different job: a client has projects, a project has a history,
and the history has to survive an audit rather than merely jog a memory.

```bash
uv run arpenteur import CHE-103.895.771 --client   # a client the survey never saw
uv run arpenteur console                           # then Clients, Calendar
```

**Clients** is the portfolio: who, which code, how many projects running, when
you last spoke and what is next. **A client card** carries the register entry,
the officers, the research already done, the projects, and the record.
**A project** has its own reference (`BOIS-GENTIL-01`), because it outlives the
conversation that created it and has to be nameable in a contract, an invoice
and a file. **Calendar** is the month: meetings, interviews, deadlines and the
start and end of each project, and nothing else.

Five decisions, each taken against how the established professional-services
tools do it.

**One event log, not two.** The comparisons make the same point repeatedly: the
compliance audit trail and the narrative client timeline are the same stream of
events rendered for two audiences, and tools that build them separately drift.
The drift is always discovered during the audit. So there is one
`crm.interaction` table, written once and projected differently: the client page
reads it as a story, the calendar reads it as a diary, an export reads it as a
record. A meeting cannot be in the diary and missing from the file, because
they are one row.

**A planned event is an event.** A meeting next Tuesday and one last Tuesday
differ by a date and a state, not by their nature.

**One table, two readings, and they are not the same set.** A calendar answers
"where do I have to be, and what falls due"; a timeline answers "what has
happened with this client". An email logged at 09:20, a document arriving, a
status changing and a note typed after a call are all the record moving, and
none of them is an appointment: a month grid carrying every update buries the
one meeting of that week under forty entries nobody has to attend. So the
calendar holds what is still `planned`, whatever its kind, because something
committed to on a stated date is what a diary is for — plus meetings and
deadlines after the fact, because "when did we actually meet them" is a
question asked of a calendar. Everything else stays on the client's timeline,
where it can be read in order.

**A project carries its own dates into the calendar.** Its start and its due
date are read from the project rather than logged once as events, so moving a
deadline moves the calendar. A "project starts" row written on the day someone
typed it says what was true that morning and then goes quietly stale.

**Cancelling and deleting are different corrections, and the record needs
both.** This started out as "nothing is ever deleted", on the grounds that an
audit trail with a delete button is a diary. That was half right, and the half
it got wrong cost more: rows describing a meeting and a project that never
existed were once written into a live record, and there was no way to take them
out. A log that cannot be corrected is not more truthful than one that can, only
more confident. So *cancelled* says a thing was true and then did not happen,
and keeps its row; *delete* says it was never true, removes the row, and leaves
a tombstone naming what went and why. Events, projects and documents can all go
this way. Deleting a project detaches its events rather than destroying them,
because correspondence that happened is still correspondence that happened.

**A status is a column, and a change to it used to be an overwrite.** The
board made that visible the moment it drew columns: the question eight cards in
a column provoke is "how long has that one been sitting there", and the answer
was nowhere. Every transition now goes into `audit.state_change` — a prospect
moving, a company becoming a client, a project going active, a meeting
cancelled — so a card can say `4w`, and a client page can show how it got where
it is across the three tables it passed through.

That table names its subject as text and holds no foreign key, deliberately. An
audit row about a project that has since been deleted is exactly the row worth
having, and a foreign key would take it away at the moment it became
interesting. The same absence is what lets it live in the scout without
coupling anything: every module requires the scout, so every module may write
to it, and because it points at nothing, nothing points back.

**One timeline, across the handoff.** The client timeline lives in
`crm.interaction` and the notes written while a company was still a prospect
live in `intel.note`, on the survey side. Promoting a company used to leave
those behind: the page that carries the whole file opened empty, on the very
engagement the notes were written for. That is the handoff gap this product is
built against, still present in the one place nobody had looked. The two are
merged at the read, marked by origin, with the survey notes read-only and
linked back to the company that owns them.

The plan called for one table with both a `company_id` and a `client_id`. That
would put a `crm` column in the survey's path and make the scout depend on the
client module for its own notes, so a survey licensed on its own would lose
them. The console knows about both modules and is the right place to join them;
the two stores stay ignorant of each other.

**Correspondence carries its Message-ID.** Paste it with the email and the same
message can never be recorded twice. Every tool surveyed loses this and
duplicates correspondence on re-import; it costs one column not to.

And the client is the company, not a copy of it. Promoting a prospect keeps
everything already gathered. The comparisons call the alternative the handoff
gap: a system that stops at "closed won" makes the first day of an engagement a
copy-paste job, when the register entry, the officers, the research and the
notes are exactly what is worth having.

A client does not have to be inside the surveyed territory, and normally is
not: the survey covers a region and a set of legal forms, and the first real
client of this tool was a foundation in Vaud where the survey was limited
companies in five cantons. `import` is the deliberate single-company door.

## Programmes

A client has projects. A programme is the other shape: one endeavour across
many companies, most of which will never be clients.

Sending a flat-handover application to the régies of Suisse romande is one
programme with fifty members, each sitting at its own stage, and the question
every morning is how many replied, how many are testing, and who has gone
quiet.

```bash
uv run arpenteur programme new FLATCHECK "Etat des lieux app"
uv run arpenteur programme bring-in FLATCHECK --sector real-estate --canton VD,GE
uv run arpenteur programme move FLATCHECK CHE-123.456.789 testing
uv run arpenteur programme say  FLATCHECK CHE-123.456.789 "photo upload fails on iOS 18" --kind bug
uv run arpenteur programme show FLATCHECK
```

**A member is a company, not a client.** The same reasoning `intel.prospect`
already carries: most of a survey never converts, and here a refusal *is* a
member that never converts. Keying on the client would put the outcome most
worth studying outside the schema. Where a member has become a client the board
shows the chip, looked up by the console because it is allowed to know about
both modules where the page is not.

**A stage belongs to one programme.** This is what `intel.prospect` could not
do: it gives a company one global state, so a second endeavour would overwrite
the first one's pipeline. The same agency can be `testing` here and untouched
in next year's programme.

**The stages are the operator's words, per programme.** Sectors, segments and
dossier sections all work this way and a pipeline is no different: the next
programme may run a pilot or recruit partners, and neither speaks of
`onboarding`. Each column carries its own one-line gloss, for the reason the
board's columns do, that a column called `replied` with nothing under it gets
used for three different things by the end of a month.

Removing a stage that members are standing in is **refused**, and the refusal
counts them. Absorbing that edit would mean deleting those members or moving
them somewhere nobody chose, and both are decisions to take while looking at
the cards.

**Inbound mail is counted on a card and moves nothing.** A member's card says
`3 in` and when they last wrote, which is what answers "who replied" without
opening a mailbox. It does not advance the stage. Nothing in this schema files
itself, a human accept is the only path into the record, and a card that moved
because a message arrived would be the first thing to break that.

**What they said is a third log, and the scoping is why.** `crm.interaction`
requires a client and these are not clients; `intel.note` is keyed on the
company, so two programmes' feedback about one agency would sit in one
undifferentiated list. Feedback is genuinely programme × company. It is
deliberately thin — a kind, a body, a date, an author — because the moment it
carries a status and an assignee somebody runs the bug list from it and it
drifts from whatever really tracks the bugs.

**No history table.** Moves go to `audit.state_change`, which names its subject
as text and holds no foreign key precisely so a later module can log to it. The
board's existing "how long has this sat here" query dates these cards with no
new code, and the history outlives the programme if it is deleted.

### Tracking what was sent and what came back

A message leaves from the operator's own client, from `arpenteur send`, or from
a campaign wave, and `arpenteur mail` reads every one back out of the Sent
folder with the rest of the mailbox. What this half adds is the record that
joins the two directions and says where each firm stands.

```bash
uv run arpenteur programme ledger  LIMEN               # every firm, every status
uv run arpenteur programme ledger  LIMEN --flag silent # written to, nothing back
uv run arpenteur programme history LIMEN CHE-123.456.789
uv run arpenteur answers                               # read the replies
uv run arpenteur programme answer  LIMEN CHE-123.456.789 \
    --platform android --account tester@gmail.com --window-ok
```

**One row holds every status, because four tables held them separately.** What
went out is counted from `intel.draft.sent_at`, what came back from
`crm.email_message`, whether the address survived from `core.contact.status`,
and the stage from the member. A firm reading `3 out, 0 in, address failed` was
never reached, and on a board that one looks identical to a firm which ignored
three messages.

**Marking a draft sent moves the card.** The console's `Mark as sent` writes
`sent_at` and moves the member to `contacted` on the programme named by
`[enrol] programme`, inserting it when the firm was never a member. It moves
forward only: a second message to a firm at `testing` says nothing about its
stage.

**A reply is read and proposed, never filed.** `arpenteur answers` reads the
platform and the tester's account out of each reply and reports them against a
member. A person accepts the reading from the status page or from `programme
answer`. Agreement to the dated window is asked rather than read, because a
reply declining the dates carries the same words as one accepting them.

**A refusal is honoured on sight.** A reply asking to be left alone suppresses
the address, which is the one thing this stage writes. Honouring it is doing
what the firm asked rather than filing a conclusion about them, and the message
promises it in writing.

**The tester's account lives on the member and nowhere else.** `core.contact`
holds addresses taken from public registers and public websites. A Google
account volunteered in a reply is different in kind, so it sits on the
programme member, where the send list cannot read it and deleting the programme
deletes it.

## Lists and campaigns

A list keeps one mailbox per firm and says, for every firm it leaves out, why.
A campaign sends one message plan over a list, in waves, each wave rendered,
tested to the operator, approved and only then sent.

```bash
# a list from the survey's bands, each weighted between 0 and 1
uv run arpenteur audience new DRYMOS-1 "Tourisme" --programme DRYMOS \
    --segment tourism-office=1,nature-park=1,mountain-transport=0.7
uv run arpenteur audience refresh DRYMOS-1        # DNS again, this list only
uv run arpenteur audience build DRYMOS-1 --dry-run  # read the funnel first
uv run arpenteur audience build DRYMOS-1
uv run arpenteur audience show DRYMOS-1             # by canton, then score

# a campaign over it, waves cut by a strategy you choose
uv run arpenteur campaign strategies
uv run arpenteur campaign new DRYMOS-A "Drymos, essai" --audience DRYMOS-1 \
    --templates templates/drymos --programme DRYMOS --strategy canton-blocks
uv run arpenteur campaign plan DRYMOS-A --dry-run
uv run arpenteur campaign plan DRYMOS-A
uv run arpenteur campaign render  DRYMOS-A 1
uv run arpenteur campaign test    DRYMOS-A 1 --yes
uv run arpenteur campaign approve DRYMOS-A 1 --read
uv run arpenteur campaign send    DRYMOS-A 1 --yes --confirm DRYMOS-A/1
```

The same steps live in the console under Lists and Campaigns, where the send of
a wave runs as a background job with its log.

**What a list keeps.** By default, a desk mailbox read off the firm's own page,
with a DNS verdict under 30 days old, never written to in 180 days. A commune's
administration, a role one person holds, the firm's restaurant, a named person
and a consumer mailbox are each set aside with their reason. A mailbox shared
by sibling firms is written to once, for the best-scoring of them, and the
others say who covers them.

**The score** is arithmetic on stored facts, every part kept: the band's
weight, the address's quality, the verdict's freshness, and how certain the
language is. The language comes from the firm's registered name before its
canton, because 21 of the 38 reachable firms in Valais write German.

**Between waves, the gate.** The next wave waits until `arpenteur bounces` has
read the mailbox long enough after the last one, and a campaign whose wave
bounced above its ceiling pauses itself until somebody resumes it with a
reason. The first wave is a small pilot for that reason.

**Limits the host imposes.** `[smtp] daily_quota` caps what Arpenteur hands
the server in any 24 hours, tests and hand letters included, and
`pause_seconds` spaces the messages of a run. Set the quota from your own
plan: Infomaniak's published figures disagree between 100 and 1'440 a day.

## Notes

Your own writing about a client, in Markdown, written in whatever editor is
already open.

```bash
uv run arpenteur note BOIS-GENTIL add "C:\Users\you\notes\AUDIT-CONDITIONS.md" --pin
uv run arpenteur note BOIS-GENTIL list
uv run arpenteur note BOIS-GENTIL show 1        # the Markdown back out
```

A Windows path is accepted as typed. This runs under WSL as often as not, and
retyping `C:\Users\...` as `/mnt/c/Users/...` is a translation the program can
do and a person should not have to.

**The file stays the master when there is one.** The path is stored, so running
the same command again after editing updates that note rather than writing a
second one, and an unchanged file reports `unchanged` rather than claiming work
that did not happen. A note pasted into the console has no path and is edited
there. Both are the same row, and which is authoritative is shown rather than
assumed.

**Markdown is the stored form, never a rendering.** HTML is produced on the way
to a page and thrown away, because a stored rendering drifts from its source
the first time the source is edited. `/note/{id}.md` returns exactly what was
written, with `text/markdown`, so an agent has a document to fetch and a note
typed in the console can be got back out as a file.

Raw HTML inside a note is escaped rather than passed through, which CommonMark
specifies the other way. These are rendered into the console's own pages, a
note may be pasted from anywhere, and no note has ever needed a `<div>`.

**They reach the stages.** `advise` and `prep` read the notes alongside the
dossier, above the client's own documents, under a heading that says whose they
are: a document the client sent is evidence, a note is instruction, and the
model has to be able to tell them apart. Where a note states a position, a
constraint or a decision, the prompt says to follow it. `arpenteur dossier
CODE` prints them too, which is the path for an agent with no browser.

Notes are searched full-text in French, tagged, and pinned when one of them is
the standing brief rather than another meeting note.

## The dossier, the papers and the approach

The register says what a company **is**. The dossier says what it **runs**, who
decides, what is exposed and what has already been tried, none of which is in
any register.

```bash
uv run arpenteur dossier BOIS-GENTIL              # everything held, as text
uv run arpenteur dossier BOIS-GENTIL --documents  # plus every document in full
uv run arpenteur advise  BOIS-GENTIL --dry-run    # what it would read, free
uv run arpenteur advise  BOIS-GENTIL              # suggest an approach
```

In the console it is one page per client, reached from the client card: what is
known, what follows from it, and what it was built on, together, because
separating them is how a proposal comes to be read without its evidence.

**Sections, not columns.** A fixed schema of forty fields fits the first client
and is wrong for the second: a care foundation has a mail estate and a resident
record, a manufacturer has a shop floor and a stock system. So the dossier is
ten named sections with free text inside each, grouped as the organisation, the
systems and the engagement. The vocabulary lives in `arpenteur/dossier.py` and
grows without a migration.

**Every section carries its source.** In your own words: `public DNS,
12.08.2026`, `said by the director on the call`, `rapport annuel p. 12`,
`inferred`. The distinction between observed, reported and inferred is the
difference between a diagnostic and a guess, and it is exactly what is lost
when notes are one long textarea.

**Documents are content-addressed and extracted once.** The file is stored under
the sha256 of its contents, so `Rapport 2025 VF (2).pdf` is visibly the same
document as `Rapport 2025 final.pdf` rather than a second one. PDF, Word and
plain text are turned into text at upload, and each has a stable URL
(`/attachment/{id}/text`) so an agent asked to analyse a client's papers has
something to fetch that is the document rather than a page about it. Extraction
happens once because it fails on scanned paper, and a failure recorded on the
row is answerable where one that happens silently on every read is not. A file
nothing can open is still stored and still says so.

**Prices come from your rate card or not at all.** This is the one that decides
whether the suggested approach is usable. Asked what a Swiss IT diagnostic
costs, a model answers with a confident four-figure number from nowhere, and
the first time that number is repeated to a client it stops being a suggestion
and becomes a commitment. So the rate card, set under Settings, is quoted into
the prompt; the model must name the line it used, and work the card does not
cover comes back **unpriced** rather than invented. An empty rate card is a
legitimate state: the advice then identifies the work and prices none of it,
and the page says so.

**Open questions are passed as questions.** The dossier's last section is the
agenda of the next meeting, and the prompt says not to answer it. An advisor who
guesses which package holds the resident record is worse than one who puts the
question at the top of the list.

**Interview prep is its own page.** A client's file accumulates vocabulary
faster than anyone absorbs it: one foundation's dossier carries DMARC, SPF,
DKIM, FortiMail, Exchange Online, VMware Cloud Director, nLPD art. 5 let. c,
ISO 9001:2015, ISO 27001, SOMED, DGCS, CIVESS, CEESV, Medidata, EMS, EPSM, CAT,
Sadies DIR, Opale, Syselcom, Infomaniak and Digital 2030. Some of those you know
cold and some you half know, and the difference shows in a meeting where the
client's own IT expert is in the room.

```bash
uv run arpenteur prep BOIS-GENTIL          # extract the vocabulary
uv run arpenteur prep BOIS-GENTIL --list   # read it back, no model call
```

Each term gets what it is, **why it is in this client's file**, what to be able
to say without looking it up, one question it lets you ask, and where to check
it. That third line is the one that earns the entry: a general glossary is a
search away and worth nothing at the table. References are **named, never
linked** (`RFC 7489`, `nLPD art. 5 let. c`, `ISO 9001:2015 §7.5`) because a name
can be checked and an invented URL looks exactly like a real one. Above the
glossary sits the agenda: twenty terms is a list, and three hours before a
meeting the useful thing is which four decide the conversation.

Tick a term as known and no re-run ever unticks it. Same rule as a hand-set
segment label: what a person decided outranks what a model produced. Without
that, regenerating after the client sends a document would silently reset an
evening's revision.

**The two stages use different models, on purpose.** Arbitrating between three
architecture proposals is judgement whose output gets repeated out loud to a
client, so `advise` runs the strongest model at a high `effort`. Extracting a
glossary is recall over material already in front of it, so `prep` runs a
lighter one. Both are set in `arpenteur.toml`; `effort` is validated at load
time, because a stage silently running at the default when the file says `xhigh`
is exactly the difference nobody notices until the answers are worse and the
bill is smaller.

**Advice is versioned, never overwritten.** A new suggestion is a new row
carrying a digest of its inputs and a record of what it actually read: which
sections were filled, which documents were opened, whether a rate card existed.
When the client asks in September why March quoted what it quoted, the answer
still exists along with what it was computed from. Marking one as *used*
distinguishes advice that went into a real proposal from advice nobody acted
on, and only one of those should be quoted back later.

## Settings

```bash
uv run arpenteur console      # then Settings
```

The territory, the sector and segment vocabularies, what the research looks for, the campaign
brief and the rate card decide what every stage below them does. They used to
live only in `arpenteur.toml` and `campaign.md`, which made the most
consequential decisions in the tool the ones you had to leave the tool to make.

It also hid them. A survey that contained no foundations at all, because the
code `0110` was absent from a list in a file, is not a discoverable state: the
canton was in scope, the sweep ran, and nothing anywhere said that an entire
class of entity had never been asked for. The legal form picker is now the
register's own catalogue, fetched rather than typed from memory.

**Overlay, not replacement.** `arpenteur.toml` keeps its comments and its
defaults and stays the reference for anyone reading the repository; a setting
changed in the console is stored in the database and applied over it. Rewriting
the TOML from a web form was the obvious alternative and would have destroyed
sixty lines of explanation that are the reason the file is readable. The
campaign brief is the exception and is written back to `campaign.md`, because a
surprising run should be answerable with a diff. The rate card is the opposite
exception and lives only in the database: the same prices apply across
campaigns, and it is nobody's business but the operator's, so it does not want
to be in a versioned file that gets shown to people.

Every change records when it happened and by whom. Widening the territory
changes what every later stage will spend money on, and that is a question
asked after the bill rather than before it.

## The console

```bash
uv run arpenteur console          # http://127.0.0.1:8000
```

Filter the survey by canton, sector, segment, fit band and pipeline state; open a
company to see its register entry, officers, web research, fit reasoning and
suggested opening; correct a label, set a state and an owner, log a call, or
suppress an address.

A correction made here goes through the same helpers the stages use, so it is
the same row a re-run has to respect. Labels set by hand are marked and the
classifier will not overwrite them.

**It sends from one place only.** Two routes transmit, the test and the send
of a campaign wave, and a test asserts no third can appear. A wave goes only
after it was rendered, sent to the operator as a test, approved by a person who
read the tests, and confirmed by typing its code. Swiss law (UWG art. 3 al. 1
let. o) makes mass advertising by email without consent unfair; the campaign
controls keep every message reviewed, identified, refusable and slow, and the
consent question stays the operator's.

**It runs the stages, and shows the work.** Six views: **Companies**, the
survey with a progress stamp per company and a filter for where each one has
got to; **Board**, where each company being worked stands; **People**, who sits
on what; **Map**, where they are; **Operations**, every stage with how much it
has done, how much is queued for it, and the control to advance it; **History**,
what has been run.

Operations is one page rather than a form page and a counts page, because the
number that makes a button meaningful is how much work is waiting for it.
"3 done, 41'238 waiting" says what to press next; a bare button says nothing.
Each card states what the stage does, what must have happened first, what it
produces, what it costs, and exactly what pressing the button will do. Any
field with a knowable set of values is a list to pick from; anything that must
be typed carries an example and says where to get it.

### The board

The survey list answers "what is out there" and sorts 184'939 rows by score.
That is the wrong shape for the question asked every morning, which is "what is
moving and what has stalled". A list cannot show it: a company contacted in
June looks exactly like one contacted yesterday, and the eight waiting on a
reply are scattered through nine hundred nobody has touched.

So the board is one column per state — `new`, `researching`, `contacted`,
`replied`, `meeting`, `won`, `lost`, `parked` — and one card per company, moved
by dragging. The count in a column head is the number that matters: five
contacted and nothing replied is a different instruction from five replied and
no meetings booked.

**Only what somebody put there is on it.** A company is on the board because it
has a state, not because it scored well. Were a good score enough, the counts
would describe the scorer rather than the work. *Bring in a shortlist* is the
step between the two: it takes the best-scoring companies of a band that are
not already there and puts them in `new`. It only ever inserts, so running it
again after another sweep cannot walk a company at `meeting` back to the start.

**Swimlanes** cut the board horizontally by something a state change does not
alter: how overdue the next action is, fit band, canton, owner, or sector. The
first is the one the option exists for — overdue at the top, because a board is
read downwards and the thing that has slipped should not sit below the thing
that has not. Grouping by *segment* is deliberately not offered, and the reason
is arithmetic rather than taste: a company carries several segments, its card
would appear in several lanes, and a card you can drag in one lane while a
stale copy sits in another is worse than no grouping. A company carries one
sector, so a sector lane holds each card exactly once. Segment stays a filter,
where one company is one row.

Dragging is the fast way to move a card and not the only one: each card has a
plain form under it that posts the same change, so the board works from a
keyboard, without JavaScript, and on a phone. A card in `won` offers the
handoff — opening a client record that keeps the file rather than copying the
name.

The board is a scout view. It reads `intel.prospect` and knows nothing about
`crm`; the client chip on a won card is looked up by the console, which is
allowed to know about both modules where `modules/scout` is not.

### People, and who reaches whom

The register publishes a notice on every mutation, and those notices name
officers. Parsed, they are 4'943 rows across 2'530 companies, obtained without
a single request: the notices were already stored, and the `people` stage reads
them. `resolve` then works out which of those rows are the same human, and the
edges between them are what turn a list of companies into a map of who reaches
whom.

**The resolution splits when unsure, on purpose.** Two officer rows are one
person when their names fold to the same string *and* their registered origins
agree. The origin is what makes this possible at all: it is a legal attribute
of a Swiss person, printed in the notice, and unlike the domicile it does not
change when they move house. A row with no origin joins the others of its name
only when exactly one origin is known among them.

The direction of error is chosen. A person wrongly split is a missing edge: a
gap in a suggestion, and recoverable. A person wrongly merged is a false
statement about a named individual in a tool that goes on to propose writing to
them, and no later correction reaches the letter that was sent. So every party
records which branch of the rule produced it and how much to believe it, and
the weaker kinds say so where they are read rather than in a footnote.

Two live splits are worth knowing about, because neither is a fault in the
rule: `Macédoine` and `Macédoine du Nord` are one country renamed in 2019, and
the commune of `Rossens` was absorbed into `Gibloux` in 2016. Both move a
person's stated origin without moving the person. A rule loose enough to join
them would also join two genuinely different people, so the split stands and
the candidates are offered to a human on the party's page.

**Before trusting any of it, look at the most connected name.** The first time
that was done here it was `Carouge GE`, on twenty-three boards. It is a town:
a domicile is a capitalised run of two words exactly like a surname and a first
name, and the officer parser had been reading them as people. It would have
become the most valuable node in the graph and it was nobody at all. Three
parser faults and two write faults came out of that afternoon, and they are why
`people --reparse` can now withdraw a row and overwrite an attribute rather
than only add.

**Board history, not a snapshot.** Each role carries the date of the oldest
notice naming the person in it and, where the register says so, the date they
left. Departed officers used to be dropped at parse time, on the grounds that
addressing someone who left would be worse than missing them. That reasoning
stands and is why `left_date` is a column rather than a deletion: `people_for`
returns current officers unless asked otherwise, so nothing that addresses a
human sees a departure by accident. What it stops doing is throwing the history
away, because "they sat together at a company neither is at now" is a real link
and a poor introduction, and the two must not look the same in a list.

From 5'688 officer rows, of which 745 are seats somebody has since left: 4'440
people, 176 of them on more than one board. A company page names its officers
as people, says since when and how many other boards each sits on, and lists
the companies it shares one with. `/company/A/route/B` walks up to four hops of
shared officers and reports no route as an answer, because a chain longer than
that is a chain of strangers.

### The map

Every establishment with a coordinate, on swisstopo's own base map. Markers
are coloured by fit band, filtered by canton, sector, segment, band and pipeline state,
and each one opens the company.

**A company has no location; its establishments do.** A foundation running an
EMS, a day centre and three residences is one legal entity at seven places, and
a single marker for it would be wrong in six of them. So `geo.establishment` is
a table: the seat arrives from the register through the `geocode` stage, and
the other sites are typed on the client page, where they are geocoded as they
are saved.

**A point is separate from the address it was made from.** An address is what
the register or the client told us; a point is what a geocoder made of it, at a
precision, on a date. A bad geocode over a good address is the ordinary case,
and one row could not tell them apart.

**The precision is graded here, not read from the reply.** This is the part
worth knowing before trusting the map. The federal search calls everything it
returns an address, including the arbitrary building it picks when the query
was only a postcode. Measured on 13.08.2026:

```
"Chemin du Petit-Flon 49, 1018 Lausanne"  ->  origin=address, Chemin du Petit-Flon 49
"1018 Lausanne"                           ->  origin=address, Rue Traversière 1
```

The second is a real building in the right postcode with nothing to do with the
question, and its `origin`, `rank` and `weight` are indistinguishable from the
first's. So each answer is compared with what was asked: the postcode must
agree or nothing is stored, and a point is called an address only when the
street and the house number are in the answer too. A marker placed on a town is
drawn hollow and says so when clicked.

Across 500 Geneva companies: 469 landed on a door, 15 on a street, 9 on a town
only. Five got no point at all, and four of those are companies whose
registered address reads `ohne Domizil-sans domicile`, which is the register
saying they have none. That string is recognised, so the stage reports what the
register said rather than what this tool failed to do, and spends no request
finding out.

Both the base map and the geocoder are swisstopo, published as open government
data on the single condition that the source is named, which the page does.
Nothing is sent to a commercial geocoder: putting every client's address
through a third party to learn what the Confederation publishes would be a
data-protection decision taken for no gain.

A company page carries the same actions for that one company, since having
opened it and decided it is interesting, the alternative is going back and
constructing a filter that selects exactly it. Naming a company is itself a
declaration of size, which is why the paid stages accept it without a limit.

Starting a stage drops you on its log, which fills in as the stage talks.
Every stage already took an injectable `log` callable, so this swapped the sink
rather than touching a single stage's internals: lines go to a `run_line`
table, which means the log survives a reload, a run started from the command
line is watchable in the same view, and a stage that emits sixty thousand lines
is polled for only what the page has not seen.

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

## Moving an installation to another machine

The code is in git. The data is not: it lives in a Docker volume and under
`data/`, both outside the repository on purpose. A clone on a new machine is
therefore an empty Arpenteur, and `workspace` is what fills it.

```bash
uv run arpenteur workspace export --out ~/arpenteur-2026-10-07.arpw
uv run arpenteur workspace inspect ~/arpenteur-2026-10-07.arpw
uv run arpenteur workspace import  ~/arpenteur-2026-10-07.arpw
```

The file is a zip holding a `pg_dump` of the database, the documents clients
have sent, `arpenteur.toml`, and a manifest. It carried 744'370 rows in 66 MB
the first time it was used, and the export took seven seconds.

A dump on its own would be enough to restore data and not enough to trust it.
It does not say which modules were running, nor whether the code about to read
it is the code that wrote it, and a restore that succeeds against a schema the
application has never seen is worse than one that fails. So the manifest
carries every applied migration digest, the same ones `kernel/db.py` already
compares on every startup, and the import reads them before touching anything:

- A file holding a migration this build does not have is refused, because the
  schema would be ahead of the code.
- A file whose shared migration has a different body is refused, since that is
  the same version number over a different schema and nothing downstream would
  notice.
- A file missing migrations this build has is the ordinary case, and they run
  after the restore.

The restore is then checked against the row counts the manifest promised,
table by table, and a mismatch is an error rather than a warning.

Two details are not conveniences. The dump is logical rather than a copy of
the data directory, because a PostgreSQL data directory is not portable across
CPU architectures and the first real use of this was Windows on x86 to a Mac
on arm64. And the database is recreated from `template0`: the PostGIS image
installs its extensions into `template1`, so a database made the ordinary way
already holds `postgis`, `topology` and `tiger`, and restoring an archive that
also creates them buries the real errors in a hundred harmless ones.

`.env` is left out unless `--with-secrets` is given, because the file is made
to travel and that is where the Zefix and Infomaniak passwords are. Nothing in
the archive is encrypted: the dump holds personal data and the attachments may
hold documents under NDA, so it wants protecting in transit by a tool built
for that rather than by cryptography folded into an archiver.

```bash
gpg -c --cipher-algo AES256 arpenteur-2026-10-07.arpw
```

Importing into a database that already holds rows is refused unless `--force`
is given, and the refusal says what it counted.

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
