# Drymos: a second campaign, and the cohort to carry it

## What this is for

Drymos is an offline atlas of Swiss nature: 14'692 listed lakes, summits, woods
and protected areas, a map on bundled SWISSIMAGE tiles, sourced notes in five
languages, GPS the one permission and no network at all.

The campaign offers it for testing to Swiss tourism offices and the
organisations around them, and presents custom development alongside it. The
recipient is a recommender rather than a user: its visitors ask about the places
the atlas lists, and no message may claim the office itself works from it.

## What the register answered, 08.10.2026

Measured live through Arpenteur's own credentials before anything was built.

| | |
|---|---|
| `*tourisme*` in Valais, leading wildcard | 19 |
| `Tourisme*` in Valais, trailing only | 0 |
| distinct companies over 26 words, four tiers | 798 |
| a wider 32-word probe, five tiers | 1'310 |
| of that wider probe, new to `core.company` | 371 |
| associations among the 798 | 104 |
| associations in `core.company` today | 0 |

The register accepts a leading wildcard, so discovery is one search per word
rather than a national prefix sweep. A search answer carries no canton, which
the canton loop supplies.

## The scope, narrowed 08.10.2026

The apps reach no store yet. This build creates the project and collects the
targets with their addresses, so the cohort stands ready the day a build is.
Nothing composes or sends a message, and the brief and the templates wait for
the app.

## The work

1. [x] `arpenteur/sources/purpose.py`: `fold`, `head`, `near`, `BOILERPLATE`,
       `NEAR`, lifted out of `scripts/classify_regies.py` so both classifiers
       read one definition.
2. [x] `scripts/classify_regies.py` imports them, with no change in behaviour.
3. [x] `arpenteur source --name`: targeted ZEFIX name search, looping cantons,
       every legal form, writing no `intel.sweep` row.
4. [x] `FixtureZefixClient.search` answers one name pattern, so the mode is
       testable with no network.
5. [x] Four bands in `[segments]`: `tourism-office`, `nature-park`,
       `mountain-transport`, `outdoor-guide`.
6. [x] `scripts/classify_tourism.py`: rules over the registered purpose, a
       proximity gate, written to `intel.segment` at `source='manual'`.
7. [x] The `DRYMOS` programme, with the cohort brought in as members.
8. [~] Addresses over the four bands: `reach`, then `verify`.
9. [x] `.env`'s `ZEFIX_URL` either honoured as an override or dropped.
10. [x] Tests: the name mode, the shared helpers, the tourism classifier.
11. [~] The run: source, classify, programme, reach, verify.

## What waits for the app

`drymos.md`, the brief the draft stage reads. `templates/drymos/{fr,de,it,rm}.md`.
The per-campaign brief plumbing, which is `BRIEF` out of `draft.py` and a
`[campaign.<code>]` table. The Romansh commune test in `draft.language_of`.

Each of those decides what a message says, and the message cannot be written
before the app it describes can be installed.

## What stays out

A communal tourist office run as a department of its commune has no register
entry, so no ZEFIX search reaches that tier. The 798 are the registered
organisations.

A Drymos page on peringer.ch, a Play closed test and a TestFlight group are all
prerequisites for sending modalities, and none is a survey task. The first
message therefore asks whether the office wants them.

No message leaves the mailbox in this build.

## Review

### What was built

`arpenteur source --name` and `--name-file`, beside the prefix sweep in the same
file. It loops cantons, asks for every legal form, attaches the canton the
answer does not carry, and writes no `intel.sweep` row, because no prefix was
completed and a sweep resuming on that row would skip the territory.

`arpenteur/sources/purpose.py`, holding the three rules that read a registered
purpose. They were written for `classify_regies` and are now shared; that script
imports them and its output is unchanged, 7'380 / 5'228 / 4'794 / 4'002 before
and after.

`scripts/classify_tourism.py`, four bands over the cohort, with the high-precision
phrases taken from real register entries and the noise families named rather than
corrected row by row.

`--legal-form` on `programme bring-in`. `arpenteur.toml` raises the nLPD point
itself: a sole proprietorship is registered in a natural person's name, so
writing to one carries obligations that writing to a company does not.

`ZEFIX_URL` is read. It sat in a working `.env` and nothing looked at it.

### The numbers

| | |
|---|---|
| canton-word pairs searched | 1'170 |
| distinct companies found | 1'140 |
| mountain-transport | 212 |
| tourism-office | 124 |
| outdoor-guide | 14 |
| nature-park | 7 |
| members on DRYMOS | 347 |
| tests | 1'093 passed |

### What the classifier got wrong first, and why

The first run graded every hydrated company and returned landscapers on
`paysage`, travel agents on `forfaits touristiques` and a sports agency on
`encadrement`. Two rounds went into the patterns before the scope turned out to
be the fault. Reading the rejects afterwards found one real miss, Villars
Loisirs et Tourisme, dropped because the rules held the noun `promotion` and the
company wrote the verb `promouvoir`.

### Two things left as they are

`scripts/limen_hunt.py` runs the address cascade for any band list and takes
`--band`, so the Drymos hunt needed no new code. Its name now describes one
campaign and its behaviour describes none, which is worth a rename and three
files of mechanical edits that this change does not need.

Valais/Wallis Promotion and the four Ticino regional organisations are
public-law bodies with no register entry, so no ZEFIX search reaches them. They
are named here because a gap nobody wrote down is a gap somebody rediscovers.
