# A campaign manager: curated lists, scores and waves

The approved plan is the reference for every detail; this file tracks progress.

## Why

From the raw addresses, curated lists that are tested; from a list, a campaign
sent in waves ranked by a score of potential. Measured on the Drymos cohort,
`sendlist` sends `info@saas-fee.ch` three letters, nothing ranks or waves, and
21 German-named Valais firms would receive French.

## Standing rules for this build

- From step 6 until `0010` is final, no `arpenteur` command, no console start
  and no `db.open_db` against the live database. Read-only checks go through
  `docker exec … psql`. The migration is iterated against `arpenteur_test`.
- One pytest run at a time.
- Every patch applied by pattern asserts its match.

## The work

0. [x] `test_modules.py`: the foreign-key rule derived from module ownership.
1. [x] `sources/language.py`.
2. [x] `sources/mailcheck.py`: trade desks, administration, role, venue.
3. [x] `sources/audience.py`: rule, judge, score, assign, funnel.
4. [x] `sources/template.py`.
5. [x] `sources/waves.py`: strategy registry, plan, gate, inbound.
6. [x] Migration `0010_campaign.sql` and the store functions.
7. [x] `stages/audience.py` and `verify.run(domains=)`.
8. [x] `send.run`: programme, quota, pause, outbound log, refusals; `[smtp]` settings with `signature_image`.
9. [x] `stages/campaign.py`.
10. [x] Runner: lanes, `listed`, `precheck`, rollback, registrations.
11. [x] CLI `audience` and `campaign`.
12. [x] Console pages, navigation, honest header and copy, token fix and its test.
13. [x] The no-send tests rewritten around `TRANSMITTING`.
14. [x] Live: Drymos audience and plan; Limen test send to the operator only.

## Review

### What exists now

A list is a curated audience: one mailbox per firm, chosen by rule, with one
reason for every firm in scope. A campaign cuts a list into waves by a strategy
the operator picks, and each wave walks render, test, approve, send, which the
schema enforces. Between waves the gate waits for bounces to be read and
pauses a campaign above its ceiling. Every post is logged before the server is
asked, counted against a daily quota and spaced by a pause.

Two routes send, both from a wave's page, both through the runner after a
precheck. `TRANSMITTING` names them and six tests that used to assert "nothing
sends" now assert "only these two".

### The live run, 13.10.2026

| | |
|---|---|
| migration | clients v10, six tables |
| DRYMOS-1, firms in scope | 357 |
| kept | 158 |
| `info@saas-fee.ch` | kept once, two siblings covered by name |
| Valais kept, German / French | 22 / 4 |
| DRYMOS-A | 10 waves, canton-blocks, pilot of 10 |
| render of DRYMOS-A | refused, naming `templates/drymos/de.md` and `fr.md` |
| LIMEN-VERIF test | one message to the operator, signature, logo and opt-out read back from Sent |
| messages sent to any firm | 0 |
| tests | 1'300 passed |

LIMEN-VERIF was re-planned after its test, which discarded its 25 unsent drafts
so they hold no mailbox, and then paused with its reason.

### Found and fixed on the way

- The foreign-key rule ranked `crm` beside `intel`; it is derived from module
  ownership now, and still refuses `intel → crm`.
- `[smtp] signature_image` was never read from the file.
- `--muted` and `--panel` were used on three pages and defined nowhere; a test
  now refuses any undefined token, and it was made to fail once on purpose.
- The runner lost a failed stage's error text without a rollback.
- `send.run` credited `[enrol] programme` whatever the message belonged to, and
  so did the console's "mark sent".
- `campaign pause` asked for a wave number it does not take. Found by running
  it for real; no test had run it.

### Waits for the operator

The Drymos templates, once the app can be installed. The first real wave, which
nobody should send for you.
