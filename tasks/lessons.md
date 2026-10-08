# Lessons

One entry per correction, with the rule that prevents it next time.

## Ask what the operator does by hand before designing what the program does

**The correction.** Asked how far the program should go in writing a second
message, the answer was that no message is ever composed or sent here: every
one leaves from `alexis@peringer.ch` by hand, and what was wanted is the record
around it, which is the status, the history and the classification.

**What I had done.** Offered three options, two of which composed text, and
sized the work around drafting rather than around tracking. A `## La relance`
section in the brief and a `draft --followup` selector were both in the plan.

**The rule.** Before planning a stage that produces anything outward-facing,
state which act stays manual and build inward from there. This codebase already
says so in several headers, `mail.py` first among them: there is no send path
anywhere in it. A plan that proposes one has stopped reading the repository.

## Run every new query against the live database before building on it

**What happened twice.** `send_list` bound its parameters in arrival order and
silently returned nothing for every band-narrowed selection. `campaign_ledger`
read an inner alias from outside its subquery and Postgres refused it outright.

Neither was caught by reading. Both surfaced within a minute of being executed
against real rows.

**The rule.** A new SQL function is executed once, against the live database,
before anything is written on top of it. A unit test over fixtures does not
replace it: the `send_list` defect was invisible to the suite because no test
had ever passed the argument that triggered it.

## Read the signature, never recall it

**Three in this session.** `suppress_address` takes `note` and not `reason`.
`core.contact` has `source_url`, `method` and `fetched_at`, not `source` and
`found_at`. `Mailbox` offers `uids`, `headers` and `body`, not `search` and
`fetch_raw`.

**The rule.** Before calling anything written outside the current turn, grep
for its `def` line. A guessed keyword argument fails loudly, which is the good
case; a guessed column name in an f-string fails only when that branch runs.

## An audit row ordered by its timestamp has no order

Two state changes written in one transaction share `at` to the microsecond, so
`ORDER BY at` returns them in whichever order Postgres likes. Order by `id`
when the sequence is the thing under test.

## A patch that matches nothing changes nothing, and says so to nobody

`--legal-form` was added to the programme parser by replacing a line of
`cli.py`. The line in the file carried `default=""` and the pattern did not, so
the replacement matched zero times, the file was rewritten unchanged, and
`ruff` passed. The flag was missing from `--help`, which is the only reason it
was caught.

**The rule.** A patch applied by pattern asserts its own match count, or the
edit is verified by reading the thing a user would see. `assert old in t`
before the replace; `--help` after it.

## A classifier is scoped before its rules are tuned

The first tourism run graded all 33'212 hydrated companies and the output was
mostly Geneva service firms: a landscaper on `paysage`, a travel agent on
`forfaits touristiques`, a sports agency on `encadrement`. Two rounds went into
tightening patterns before the real fault surfaced, which was the selection.
The cohort is the population the search went looking for, and once the grading
ran over that instead, the same patterns behaved.

**The rule.** State which rows are even candidates before writing a rule that
sorts them. A rule tuned against the wrong population is tuned against noise.

## The register matches accents literally

`*telecabine*` answers with nothing and `*télécabine*` finds the Vercorin
company; `*remontees*` answers with 1 against 8 for `*remontées*`. The same
holds for German umlauts.

**The rule.** A name search carries both spellings of every accented word, and
a word that answers with nothing is recorded as checked rather than quietly
dropped, so the next person does not spend the requests again.
