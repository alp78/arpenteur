# The dossier, the papers and the approach

The register says what a company **is**. The dossier says what it **runs**, who
decides, what is exposed and what has already been tried, none of which is in
any register.

```bash
uv run arpenteur dossier CLAIR-LOGIS              # everything held, as text
uv run arpenteur dossier CLAIR-LOGIS --documents  # plus every document in full
uv run arpenteur advise  CLAIR-LOGIS --dry-run    # what it would read, free
uv run arpenteur advise  CLAIR-LOGIS              # suggest an approach
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
uv run arpenteur prep CLAIR-LOGIS          # extract the vocabulary
uv run arpenteur prep CLAIR-LOGIS --list   # read it back, no model call
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
