# Verify the address bank before Limen goes out

## What was measured first, 08.10.2026

12'111 addresses, 5'139 domains. DNS over all of them took 8 seconds.

| | |
|---|---|
| domain has MX | 11'908 |
| A record only | 181 |
| domain is dead (NXDOMAIN) | 16 |
| resolver gave no answer | 6 |

`core.contact.status` is NULL on every row, and `mailcheck.verify` with its
`Verdict` were written for this and never called. So the check exists, the
column exists, and nothing joined them.

## What this builds

1. A migration adding `status_at` and `status_note` to `core.contact`. A
   verdict goes stale, so a verdict with no date cannot be trusted a month
   later, and `fetched_at` answers a different question (when the address was
   found).
2. `arpenteur verify`, which resolves one DNS answer per *domain* and writes it
   to every address at that domain. 5'139 lookups for 12'111 rows.
3. `arpenteur sendlist`, one desk address per firm, deliverable only, which is
   the list a staged send actually uses.
4. Tests for all of it.

## What this deliberately does not build

**No SMTP `RCPT TO` probing.** Catch-all domains answer yes to everything, so
the result is not a mailbox verdict; greylisting makes it need retries; and
12'000 probes from this IP invites a blacklisting that costs more than the
answer is worth. The definitive existence test is a staged send with bounce
capture, which the send list feeds.

**No stored attribution flag.** The discriminator is `domain_echoes_name`, and
it is broken for umlauts, hyphens and short words (see `findings.md`). Storing
a verdict computed from a broken comparison would freeze the bug into the data,
where it would later be read as a fact. Attribution stays computed on demand
until the comparison is fixed.

## Tasks

- [x] migration `0014_contact_verified.sql`, applied as scout v14
- [x] `domains_to_check`, `save_domain_verdict`, `send_list` in the scout store
- [x] `arpenteur verify`, with `--refresh`, `--stale-after` and `--dry-run`
- [x] `arpenteur sendlist`, one address per firm, `--out` writes CSV
- [x] `mailcheck.DESK_FUNCTIONS` and `mailbox_kind`, three-way
- [x] 14 tests in `test_verify.py`, 11 added to `test_mailcheck.py`
- [x] run over the live bank

## Review

`arpenteur verify` over the whole bank: 5'139 domains, 12'111 addresses, 20.8
seconds. 4'939 deliverable, 178 maybe, 16 dead, 6 unknown. Every row now
carries a verdict and the date it was taken; all 12'111 were NULL before.

`arpenteur sendlist` produces 5'126 firms, all `deliverable`, one address each,
no firm twice, no malformed address. Checked with `csv.DictReader` rather than
by eye.

| | |
|---|---|
| a desk address | 4'316 |
| a named person | 351 |
| neither clearly | 459 |

**The three-way classification was not cosmetic.** A binary over `ROLE_PARTS`
reported 1'033 addresses as reaching a named person. 351 do. The 682
difference was 590 departmental desks that `ROLE_PARTS` does not list
(`locations@agenceacc.ch`, `courtage@imogroup-geneve.ch`) and 86 firms using
their own name as a mailbox (`actimmo@actimmo-ge.ch`). Reported as a binary the
advice would have been wrong twice over: skip 223 real desks, and treat 682
company mailboxes as personal data.

The 16 dead domains all resolved on the day their address was found, which is
why `status_at` exists.

# Reading bounces back into the verdict

## Why this and not a sender

`stages/mail.py` states it plainly: "There is no send path anywhere in this
codebase", and `MailConfig` repeats it. That is not an omission to fill in
passing. A hand-rolled SMTP sender would also own SPF, DKIM, DMARC alignment,
rate pacing, List-Unsubscribe and a suppression list, and getting any of them
wrong costs the sending domain its reputation permanently.

So the send stays outside: the operator's own provider does it, from a CSV
`sendlist` already writes. What comes back is inbound mail, and the read-only
IMAP path for that already exists and is configured.

## What a bounce settles that DNS cannot

`verify` answers about the domain. A bounce answers about the mailbox, which is
the question that was left open: `info@` at a live domain may reach a desk or
may reach nothing, and only a delivery attempt separates them.

A permanent failure (SMTP 5.x.x) is therefore a *stronger* verdict than
`deliverable` and must outrank it. `save_domain_verdict` writes to every
address at a domain, so a later `verify` run would otherwise erase a bounce
with a DNS answer about a domain that is working perfectly well.

## Tasks

- [x] `sources/bounce.py`: RFC 3464 reports and the plain-text ones
- [x] `bounced` for 5.x.x, `deferred` for 4.x.x
- [x] `save_mailbox_verdict`, scoped to the address and not the domain
- [x] `save_domain_verdict` skips an address that bounced
- [x] `Mailbox.raw`, because `body` discards the part that carries the verdict
- [x] `stages/bounces.py`, read-only, envelope screen before any full fetch
- [x] `arpenteur bounces` with `--folder`, `--since`, `--limit`, `--dry-run`
- [x] 22 tests in `test_bounce.py`

## Review

898 tests pass, lint clean.

Run against the live mailbox at `alexis@peringer.ch`: 30 messages screened, 0
reports, which is the right answer because nothing has been sent yet. The
login, the read-only select and the envelope screen all ran.

**Two faults the tests caught, both in code I had just written.**

`_text_parts` decoded every part and got nothing from the one that matters.
RFC 3464 makes each per-recipient block a set of header fields with no body,
so Python parses them as sub-messages: `get_payload(decode=True)` returns None
on the container and an empty string on the blocks, because `Final-Recipient`
and `Status` are headers, not content. Every standard report was falling
through to the plain-text fallback, which read `550` off the prose instead of
the `5.1.1` the report stated, and a delay notice with no code in its prose
produced no verdict at all. The blocks are now serialised with `as_string()`.

I also wrote the stage against a `Mailbox` API I had not read: `search` and
`fetch_raw` do not exist. The real surface is `uids`, `headers` and `body`,
and `body` is the wrong reader here for the reason above, so `Mailbox.raw` was
added beside it.

**The guard that matters most.** `save_domain_verdict` writes to every address
at a domain, so a `verify` run after a send would have marked a mailbox that
answered 550 deliverable again, on the strength of an MX record that was never
in doubt. `test_a_dns_sweep_does_not_erase_a_bounce` pins it.

# The draft stage, built 08.10.2026

## Review

922 tests pass, lint clean. 24 of them in `test_draft.py`, written as the four
design constraints the stub's docstring fixed before any of it existed.

Proved against the live database and the live model: one draft for AB2 Sàrl,
`claude-sonnet-5`, `sent_at` null, `reviewed_at` null. Its body asserts only
what the register says, names no feature, quotes no price, and carries the
opt-out sentence with the real mailbox appended after the model's text. The
proof row was then deleted.

## What the volume cap is for

`send_list` returns 5'126 firms. The stub's fourth constraint reads "low
volume by design. Swiss law (LCD art. 3 al. 1 let. o) draws its line at mass
advertising email. Researched, individual, human-reviewed messages sit on the
right side of it; a bulk sender does not."

A stage that drafted 5'126 messages on one command would have turned that
constraint into a comment. So `SAFE_BATCH` is 25, exceeding it prints the
reason, and `--yes` is what the operator types to go further. The cap is on the
draft and not on a send, because there is still no send path.

## What the opt-out taught

Asking the model for an unsubscribe line produces one most of the time. Most of
the time is not a standard a legal obligation is met by, so `compose` appends
the sentence in the recipient's language and names `settings.mail.user`, which
is the mailbox `bounces` already reads. A refusal therefore arrives where
somebody is looking, and `run` refuses outright when no mailbox is configured.

## Two things found while building

`intel.segment` has no `grade` column. It holds `confidence` and `rationale`,
and the rationale is the better evidence: it is the sentence the classifier
wrote about this firm's own purpose text, which is the only thing the message
may assert about them.

`cmd_stub` existed solely to print draft's "not implemented" message and is
now deleted. Its parser was also registered without binding `p`, so the
options I added first landed on the `resolve` parser and argparse refused a
second `--dry-run`.

## Still open before a first send

- The brief marks four sections `À compléter`, and the model reported all ten
  of their contents as missing while writing. A message drafted now says
  almost nothing, which is the honest failure rather than an invented one.
- The generated French is not held to the house rules. The live draft wrote
  « Nous restons à votre disposition », and `rester` is build-gated prose in
  the vault. The brief's registre section should carry the banned-word list,
  or `draft` should check the body the way the vault hook checks a file.
- No review surface. The drafts sit in `intel.draft` with no console page and
  no `arpenteur draft list`, so reviewing them means reading SQL.

# The review surface, and the reply proposal

## Review

946 tests pass, lint clean. 15 of them in `test_draft_review.py`.

`/drafts` lists the queue oldest first, with a tally separating awaiting,
approved and sent. `/draft/{id}` shows one message whole, because a card with
a subject and a first line is enough to approve by and not enough to catch
what is wrong, and what goes wrong in these sits in the body.

`arpenteur programme replies LIMEN` lists members at `contacted` who have
written since they were moved there, then moves them on `--yes`.

## Why the move needs a flag

`members_for` already carried the rule: nothing in this schema files itself,
and a stage that advanced because a message arrived would be the first thing
that did. The request was for replies to move the card, so the move happens,
but the accept is the flag rather than the arrival of mail. An inbound message
is a prompt, not a reply, and an out-of-office is both.

"Since they were moved there" is the part that matters. The comparison runs
against `updated_at`, which `move_member` sets, so a firm that wrote last year
and was contacted yesterday does not appear.

## The marker is refused twice

A draft carrying `[[ MANQUE : ... ]]` cannot be sent, so the page withholds
the approve button and the route refuses the request anyway. A form can be
posted without the page that drew it, and the page not drawing a button is not
a guarantee.

Approving is also not sending. Nothing in this program sends mail, so
`reviewed_at` says a person read it and `sent_at` is set by hand afterwards.
Collapsing them would make the record claim what it cannot know.

## Three faults the tests caught

A timestamp arrives from this codebase's row factory as an ISO **string**, not
a datetime. My `_ago` subtracted a datetime from a str and raised on every
row. `programme_page._sitting` already took a `str` and would have said so.

`audit.state_change` names its column `by`, not `changed_by`.

The console style test refuses a page that spells a colour: I had written
`var(--bad-line, #c33)` with hex fallbacks, where `--bad` and `--bad-bg`
already exist. The nav test then failed honestly, because the header really
did gain a page.

# Closing the gaps, 08.10.2026

975 tests pass, lint clean.

## The promise nothing kept

Every message `draft` composes closes on a « STOP » instruction, promising the
reader their address leaves the lists. The sentence was appended
deterministically to every draft and honoured by no code at all. A written
undertaking that no routine performs is worse than no undertaking, and this was
the largest of the gaps rather than the smallest.

`sources/bounce.refusal_in` reads a refusal out of an inbound message in the
four languages, and the `bounces` stage sets `suppressed`, which is the flag
`send_list` and every selection through it already obey.

Three decisions inside it:

- **The match is lenient on purpose.** Suppressing an address that did not ask
  costs one contact out of twelve thousand. Failing to suppress one that did
  breaks the promise in writing. The errors are not symmetric, so a plausible
  refusal is honoured and reported rather than held back pending certainty.
- **Only the subject and the opening are read.** A refusal is short and sits at
  the top; the same word thirty lines down is in a quoted thread or a
  signature.
- **A colleague's refusal names the address we wrote to.** A reply from
  `secretariat@autre.ch` saying "retirez info@regie-x.ch" refuses for the firm,
  and suppressing the colleague would leave the firm on the list.

The envelope screen had to widen for this. A failure notice announces itself in
the subject; a refusal does not, so anything from an address the bank holds is
now read in full.

## A refused draft is kept

Discarding deleted the row, which lost the reason and, worse, the fact. The
stage selects from the send list, so a deleted proposal is indistinguishable
from one never written: the next run proposed the same message to the same
mailbox and the reviewer refused it again. `refused_at` and `refused_reason`
join the table's existing vocabulary of timestamps, and `0016` rebuilt the
one-per-mailbox index so a refusal blocks a second identical proposal.

## The campaign's own bands

`[enrol] segments = ["regie-core"]`, read when `--segment` names none. A stage
that swept both bands by default contradicted the brief it was reading.

## The prose the brief proscribes

`prose_faults` checks the four turns `limen.md` names, on what came back. The
first live draft wrote « Nous restons à votre disposition ». Flagged on the
draft's page and counted over a run, never rewritten: a reviewer who is told
the fault fixes it better than a regex does. The vault's own checker runs on
files a person writes, which a generated message is not, and it lives outside
this repository.

## The bug the gaps uncovered

`send_list` bound its arguments in arrival order while the JOIN's placeholder
sits textually before the WHERE's, so `k.status = ANY(...)` read the segment
list. **Every selection narrowed by band returned nothing**, against 2'450 real
firms, and no test caught it because none passed `segments`. Found by running
the new default. Two tests now pin it.

## Still open, and not mine to close

- `limen.md` still marks the product, the sender and the return offer `À
  compléter`. Nothing in the repository states what Limen does.
- `[enrol] ios_link` and the two window dates are unset, so every draft carries
  a visible marker and cannot be sent.

---

# Plan: the answer, the status, the second message

## Verified against the live database, not recalled

The outbound chain works end to end. `sendlist` names 2'450 firms, `draft`
writes one proposal per mailbox, `/drafts` shows it whole, a person approves
and marks it sent. The failure chain works: `bounces` writes a verdict onto
`core.contact` and suppresses an address that refused.

The LIMEN programme exists with its eight stages and **no members**.
`intel.draft` holds **no rows**, because the brief is incomplete.

## Four links are missing

**A sent draft never reaches the programme.** `mark_draft_sent` writes
`intel.draft.sent_at`, and the LIMEN card keeps whatever stage it had. There is
no card at all today, so the campaign's own tracker knows nothing about the
campaign.

**An answer is read by nothing.** `bounces` classifies a delivery failure and a
refusal, then skips every other message. The answer the brief asks for names
four points, and no code captures one of them. This is the gap, since the
campaign exists to collect those answers.

**No query names who is due a second message.** That answer needs a join
across `intel.draft.sent_at`, the inbound rows of `crm.email_message` and
`core.contact.status`. Nothing performs it.

**Status has no single answer.** It is spread over four tables, so "where does
this firm stand" is four queries and a human join.

## Design decisions

**The enrolment answer belongs to the member, not to the feedback log.**
`crm.programme_feedback` was kept thin on purpose, and the plan it came from
says why. The platform, the tester's account and the agreement to the dates are
what the next step reads when it adds that tester to TestFlight or to the Play
track, so they are attributes of the member.

**The tester's Google account never enters `core.contact`.** The survey bank
holds addresses taken from public sources. This one is volunteered by a named
person for a single purpose. On the member it dies with the programme, and the
send list can never read it.

**The stage proposes a reading and a person confirms it.** Nothing in this
schema files itself, which is the rule `mail.py` states and `members_who_replied`
obeys. The parse is deterministic: the platform is a closed vocabulary in three
languages, the account is a pattern. Agreement to the dates is not parseable,
so it is asked and never inferred.

**Answers are read from `crm.email_message`, never from IMAP a second time.**
The mail stage already imports inbound mail and matches it to a company. A
second reader would duplicate the matching and disagree with it.

**Nothing composes a second message, and nothing sends.** The operator sends
every message from their own client. So no relance is drafted, no selector for
one is built, and `limen.md` gains no section. What the program owes is the
record of what went out, the reading of what came back, and the stage each firm
sits at.

## What the mailbox already gives

`alexis@peringer.ch` at Infomaniak is configured and reachable. `mail.py`
sweeps every readable folder, so the Sent folder arrives with the inbox and a
message the operator sent is stored with `direction = 'out'`. `match_company`
attributes each one by published address, then by link, then by domain, and
excludes `peringer.ch` throughout. History and attribution exist. What no code
does is read a message's meaning or move a card.

## Items

- [x] `0009_enrolment.sql`: `platform`, `tester_account`, `window_ok`,
      `answered_at`, `answer_message_id` on `crm.programme_member`
- [x] `sources/enrol.py`: classify one message as an answer, a refusal or
      neither, then read the platform and the account from an answer
- [x] `clients.note_contacted`: insert the member when absent, move to
      `contacted`, called by the console's `/sent` route
- [x] `clients.record_answer`: the platform, the account, the cited message, a
      move to `replied`
- [x] `clients.campaign_ledger`: one row per member, every status in it
- [x] `clients.history_for`: every message to and from one firm, in order
- [x] `stages/answers.py`: classify each matched inbound message, propose a
      reading, write nothing but a suppression
- [x] Console: the ledger, one firm's history, the answer form
- [x] CLI: `arpenteur answers`, `programme ledger`, `programme history`,
      `programme answer`
- [x] Tests for each promise above

---

# Review: what the campaign now records

## The link that was missing

Marking a draft sent wrote `intel.draft.sent_at` and nothing else. The LIMEN
programme held eight stages and no members while the outbound chain was
complete, so the campaign's own tracker knew nothing about the campaign.
`/draft/{id}/sent` now also calls `note_contacted`, which inserts the member
when the firm was never on the programme. A send list selects from the survey
rather than from the membership, so first contact is usually the first the
programme hears of a firm.

It moves forward only. Marking a second draft sent to a firm at `testing` says
nothing about its stage, and walking that card back would erase the only record
of how far it got.

## Reading a reply without filing it

`bounces` classified two things and skipped every other message. `enrol.read`
now says whether a reply is an answer, a refusal, or neither, and reads the
platform and the tester's account out of an answer. `arpenteur answers` reports
one proposal per message against a member id, and a person accepts it from the
status page or from `arpenteur programme answer`.

Three judgements in it, each chosen over an easier one.

- A reply naming both platforms names neither. Enrolling a tester on the wrong
  store track costs a place in a cohort of twelve, so two platforms in one
  message is a question for the operator.
- Agreement to the dated window is reported as a hint and never as a verdict.
  « Android nous convient, mais pas ces dates » carries every token that
  accepting the dates carries. A test pins that case.
- The address offered as the tester's account is never the firm's own, which
  appears in nearly every body because a reply quotes what it answers.

The one thing the stage writes is a suppression, and that is an instruction
rather than a conclusion. `bounces` already honours a refusal from the mailbox;
one reaching here means its envelope screen missed it.

## The tester's Google account

Held on `crm.programme_member` and nowhere else. `core.contact` holds addresses
taken from public registers and public websites, and a Google account
volunteered by a named person in a reply is different in kind. On the member it
cannot be read by the send list, which selects from `core.contact` alone, and
it is deleted with the programme. Both are tests.

## The row the ledger exists for

Four tables answered "where does this firm stand" and no query joined them.
`campaign_ledger` returns one row per member holding what went out, what came
back, whether the address survived, and the stage. A firm reading
`3 out, 0 in, address failed` was never reached, and on a board that one is
indistinguishable from a firm which ignored three messages.

## What running it caught

`campaign_ledger`'s `enrollable` gate read `m.platform` from outside the
subquery that defines `m`, and Postgres refused it on the first execution
against the live database. Reading the function had not caught it. This is the
second time in two sessions that a new query's defect surfaced within a minute
of being run and not at all from being read, so `tasks/lessons.md` now carries
it as a rule.

## Still open, and still not mine to close

- `limen.md` marks the product, the sender and the return offer `À compléter`.
- `[enrol] ios_link` and the two window dates are unset, so every draft carries
  a visible marker and cannot be sent.
- LIMEN holds no members. `arpenteur programme bring-in LIMEN --segment
  regie-core` puts the shortlist on it when the brief is ready.
