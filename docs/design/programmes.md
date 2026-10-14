# Programmes

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
deliberately thin, a kind, a body, a date and an author, because the moment it
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
    --platform android --account tester@example.ch --window-ok
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
