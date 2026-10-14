# Clients, projects and the record

Everything above finds companies. This is the half that starts when one says
yes, and it is a different job: a client has projects, a project has a history,
and the history has to survive an audit rather than merely jog a memory.

```bash
uv run arpenteur import CHE-100.000.011 --client   # a client the survey never saw
uv run arpenteur console                           # then Clients, Calendar
```

**Clients** is the portfolio: who, which code, how many projects running, when
you last spoke and what is next. **A client card** carries the register entry,
the officers, the research already done, the projects, and the record.
**A project** has its own reference (`CLAIR-LOGIS-01`), because it outlives the
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
committed to on a stated date is what a diary is for, plus meetings and
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
was nowhere. Every transition now goes into `audit.state_change`, a prospect
moving, a company becoming a client, a project going active, a meeting
cancelled, so a card can say `4w`, and a client page can show how it got where
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
