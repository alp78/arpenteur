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

## A green suite in under a second is a suite that ran nothing

**What happened.** After a resumed session, the test files reported "95 passed, 343 skipped in 0.42 s". Docker Desktop had stopped with the machine, the Postgres container with it, and every database-backed test skipped with "no PostgreSQL reachable". Read as a pass, that run would have sent a commit stack for code nothing had exercised.

**The rule.** Read the skip count in every pytest summary, not only the failures. A database-backed suite that finishes in seconds has not run. Check the container before trusting the number, and start Docker Desktop from WSL when `docker` is not found.

## Two agents cut off mid-task leave a tree, not a report

**What happened.** Both background agents were terminated by the account's session limit before they could run their tests. Their edits were on disk and ruff-clean, their test files unrun, and neither had reported.

**The rule.** When an agent ends without a report, read `git status` and the diff before anything else, run its test files yourself, and finish the work in the tree rather than relaunching the agent on a tree it no longer knows.

## A new file is checked against the tree before it is written (14.10.2026)

Writing `tests/test_manual.py` for the new manual overwrote a tracked test file of the same name about manual labels; the tool answered "updated" rather than "created" and the word went unread. Before writing a file under a new name, run `git ls-files <path>` or look at the Write result's first word; a file that exists is edited or the new one takes another name. The original was restored from git and the new tests moved to `tests/test_help.py`.

## A moved body keeps its imports at the old depth (14.10.2026)

Moving route and command bodies into a deeper package by script carried their top-level imports with one more dot, but the imports written inside function bodies kept the old depth and failed at request time, not at import time. A move script deepens every `from .` line, indented or not, with a regex anchored on spaces and tabs: `^([ \t]+)from (\.+)`. The first attempt used `\s+`, which matches a newline and so deepened the top-level imports that follow a blank line as well.

## A decorator is above the line `ast` reports (14.10.2026)

Cutting a function by `node.lineno` drops its decorators: `@contextlib.contextmanager` and `@dataclass` were lost in the command-line split and surfaced as a generator that was not a context manager. The start of a definition is `node.decorator_list[0].lineno` when there are decorators.

## A framework's list is not the flat list it was (14.10.2026)

FastAPI 0.141 keeps an included router as one entry of `app.routes`, so a test that walks that list for the paths that can send would miss everything a module mounted and pass. The walk goes through one helper that expands them, and the gate tests use it.

## A cache can hide a leak between tests (14.10.2026)

A test wrote a tampered migration digest into the shared database and never restored it; nothing noticed for as long as `open_db` migrated each database once per process. Keying that cache by the module list made later tests migrate again and refuse. A test that changes shared state restores it in `finally`, and a cache that hides a fault is not evidence the fault is absent.

## A pilot samples every market the operator targets (19.10.2026)

Asked for a sending strategy for Limen, I proposed the 25 Jura agencies because a list already held them, and the operator refused it: the pilot is 20 agencies in each French-speaking canton. A list that exists is not a market that was chosen. Before proposing a first wave, size it from the markets the campaign is for, one sample per canton, and ask which cantons when the brief does not say. In Fribourg and Valais the operator writes in French and German together, so a bilingual canton is one message in both languages, not two audiences.

## A gate repeated on unchanged input is noise (23.10.2026)

Arpenteur sent the operator a test before each of 13 waves carrying the same three texts, and the operator said the test is done once, not for every wave. A safety step earns its repetition only when what it checks has changed. When a gate runs per unit, ask what it verifies and key it on that: here the words of a variant, which the drafts already hold, so a wave whose words were already received needs no new test.

The first fix still keyed the test on the campaign, and round 5, a new campaign carrying the same three texts, sent the operator two tests again on 24.10.2026. The operator reads a round as the same campaign continued. Key the gate on the fact it checks and nothing narrower: a variant's words, wherever they were received.

## An unclear mailbox is a person until shown otherwise (24.10.2026)

Round 5 was built with `--allow unclear` to reach more firms, and its first wave went to seven mailboxes named after a person, such as `lenny@` and `fabregues@`, against the operator's standing rule to leave personal addresses out. The classifier leaves `unclear` undecided because it cannot tell a first name or an initial from a desk; of round 5's 68 such mailboxes, about six were desks. Never widen a list to `unclear` for volume. Read the mailboxes of any new class before the first wave leaves, not after.

The same round allowed `inferred`, the info@ guessed at the firm's own domain. Wave 2, made only of those, bounced 8 of 16 on 25.10.2026, while the addresses a firm published or a search reported bounced 0 of 20, and the operator stopped the round. An address nobody published gets its first check from a real send, since SMTP probing is ruled out. Keep campaign lists to published desks.

## A client letter asserted an action nobody had performed (27.10.2026)

The reply drafted on 25.10 for the Swiss Immo Gest tester said his Apple ID « figure désormais parmi les testeurs » and that Apple was sending him a code. I could not add him: the App Store Connect key lives on the Mac, and the draft only carried a note to send it after the operator had. The tester received nothing and wrote back. The Agencies group had a public TestFlight link all along, recorded in the vault on 01.10.2026 and shown on the Limen page.

**The rule.** A client text carries only claims with a proof I produced: a command's output, a fetched page, a record read back. An action left to someone else is not written as done. Look for the route that needs no unverified step before drafting, here the public link.

## A bilingual message is two texts, not a third one (26.10.2026)

Round 6 sent the operator four tests, one per message variant: French then German, German then French, French alone, German alone. They carried two texts. The operator had already said the test is done once, and the gate had been keyed on "a variant's words", which still treated a bilingual message and its halves as different words. I also rendered three waves at once to send every test up front, which turned the gap into four emails without a word to the operator first.

**The rule.** Key a gate on the smallest unit the operator reads, here one language's subject and text, and check composite units through their parts. Before a step mails the operator, say how many messages it will send and what each carries.

## A letter whose sentences all open on « Je » (26.10.2026)

The reply drafted for a Limen tester opened four of its seven sentences with « Je » or « J' », one at the head of each of its four paragraphs. The operator called it bad style. Each sentence passed `fr/correspondance.md` on its own, because no rule there counted openers across a whole text. The vault note now carries the rule: one sentence per paragraph at most opens on « Je », never two paragraphs in a row, and between the thanks and the salutation a sentence takes as its subject the thing it is about, such as the account or the invitation. Count the openers of a draft before handing it over.

## A tester reply copied from a draft that had dropped its courtesy (28.10.2026)

The reply for the Android tester at Reysus Gérance SA was modelled on the iPhone replies of 27.10, which had been cut to about 55 words. It opened on « Merci pour votre inscription. » and ended on the link and the Android version, with no closing offer of help. The operator called it too narrow: the reply should first thank the person for their interest in Limen and end by offering support. Compressing that earlier draft had cut the courtesy, the one thing `fr/correspondance.md` says a reply never cuts, and copying it carried the fault forward.

**The rule.** A reply to a tester opens with a full, conjugated thanks that names the interest in Limen and the sign-up. Before the close, it tells the tester that feedback reaches the operator by a simple reply to the email. It closes with a sentence offering help if any step fails. The cut falls on explanation only. An earlier draft is a source of facts, never a model for courtesy: check its shape against the reply section of the vault note before reusing it.

