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

## One pytest run at a time, because the suite owns named databases

The suite creates `arpenteur_ws_src` and `arpenteur_ws_dst` for the workspace
tests and drops them at the end. Two runs overlapping means the second finds
them already there, or the first drops them under the second, and the result is
hundreds of errors in files that have nothing to do with the change under test.
This has now happened twice, both times because a second run was started while
a background one was still going.

**The rule.** Before starting a suite run, check that none is running. A second
run is never faster than waiting for the first, and the clean-up afterwards
costs more than the wait: the stray databases have to be dropped by hand, and
the live `arpenteur` database sits one typo away in the same list.

## Every command runs once for real before it is called done

`arpenteur campaign pause` refused with "name the wave": the check that a wave
was named sat before the branch for pause, which takes none. Seven CLI tests
passed, and none of them ran pause.

**The rule.** Each new subcommand action gets one test through `cli.main`, and
the live run exercises every action the operator will type, not only the ones
the plan's verification lists.

## An assertion that cannot fail asserts nothing

A console test ended `assert A or B` where B was a status code that is always
200. It passed whatever the export held. Caught on rereading, not by a run.

**The rule.** Before keeping a test, say what change to the code would make it
fail. If the answer is nothing, rewrite it.

## The signature rule holds in test code too

Two more guesses this build, both in tests: `draft.save(brief_digest=)` where
the keyword is `digest`, and `intel.run_line.line` where the column is `text`.
The first was guessed one line after printing the signature that said so.

## A red test must fail for the reason it names

**What happened.** Two of the fifteen tests written for the money fixes passed before any fix: one asserted that a town appeared on the printed invoice, and the town was there in the letterhead, not in the payment part the test was about. The other was a page test that passed because a different fault, an invoice page that crashed on every line, masked the behaviour it meant to pin.

**The rule.** When a new test passes before the fix, read why before writing the fix: either the assertion looks at the wrong element, or something else is broken underneath. Narrow the assertion to the element the finding names, and a page test opens the page with the data the finding describes, not with the empty record the fixture gives for free.
