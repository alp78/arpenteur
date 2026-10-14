# Board

The companies you decided to pursue, one column per state, one card per company. The list under Companies answers "what is out there"; the Board answers the morning question, "what is moving and what has stalled".

Only what somebody put here is on it. A company is on the Board because it has a pipeline state, not because it scored well: were a good score enough, the counts would describe the scorer rather than the work.

## The columns

Each column carries a count and a one-line gloss:

- new: on the board, not yet looked at
- researching: being read, the site, the notices, who decides
- contacted: written to or called, nothing back yet
- replied: they answered, the conversation is live
- meeting: a meeting is agreed, or has happened
- won: work agreed, promote it to a client
- lost: not going ahead, kept, with the reason in the notes
- parked: real, but not now

The count in a column head is the number that matters. Five contacted and nothing replied is a different instruction from five replied and no meeting booked.

## The cards

A card shows the name, the canton and legal form, the score, how long it has sat in its column, and two marks: @ when an address is held, w when the website was researched. From three weeks in one column the age turns red. The next action and its date show under the name, red once overdue. A card that is already a client carries the client chip; a won card that is not yet one offers "make a client".

## Moving a card

Drag a card to another column; the state is saved as it is dropped. The fold marked "move" under each card does the same with a select and a button, so the Board works from a keyboard, without JavaScript, and on a phone.

A card cannot be dropped into a different swimlane: a swimlane is a reading of the cards, not a state.

## The filters and swimlanes

- Search, canton, sector, segment, fit band and owner narrow the cards shown.
- Swimlanes cut the Board horizontally by something a state change does not alter: what the next action is doing (overdue, this week, later, none), the fit band, the canton, the owner or the sector.

Overdue sits at the top of the first swimlane on purpose: a board is read downwards, and the thing that slipped should not sit below the thing that did not. There is no swimlane by segment, because a company carries several segments and its card would appear in several lanes; the segment stays a filter.

## Bringing in a shortlist

The form under the columns takes the best-scoring companies of a fit band that are not already on the Board and puts them in "new". It narrows by sector, segment and canton, and takes at most 200 at a time. It only ever inserts: running it again after another sweep cannot walk a company at "meeting" back to the start. When every matching company is already here, or none is scored in that band, the page says so.

## Where the cards come from

- Saving a pipeline state on a company's page puts it on the Board.
- The shortlist form puts in the companies the scoring ranked highest.
- Marking a draft as sent, on the Drafts page, moves the programme card rather than this one. The Board is the survey's pipeline; a programme has its own.

## Related pages

- [Companies](companies.md), where a state is set on one company
- [Clients](clients.md), where a won company continues
- [Programmes](programmes.md), the other kind of pipeline
