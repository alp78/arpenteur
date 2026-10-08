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
