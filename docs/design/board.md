# The board

> The figures on this page were measured on the first deployment, in 2026, over a survey of five cantons of French-speaking Switzerland. Yours will differ.

The survey list answers "what is out there" and sorts 184'939 rows by score.
That is the wrong shape for the question asked every morning, which is "what is
moving and what has stalled". A list cannot show it: a company contacted in
June looks exactly like one contacted yesterday, and the eight waiting on a
reply are scattered through nine hundred nobody has touched.

So the board is one column per state, from `new` to `parked`, and one card per company, moved
by dragging. The count in a column head is the number that matters: five
contacted and nothing replied is a different instruction from five replied and
no meetings booked.

**Only what somebody put there is on it.** A company is on the board because it
has a state, not because it scored well. Were a good score enough, the counts
would describe the scorer rather than the work. *Bring in a shortlist* is the
step between the two: it takes the best-scoring companies of a band that are
not already there and puts them in `new`. It only ever inserts, so running it
again after another sweep cannot walk a company at `meeting` back to the start.

**Swimlanes** cut the board horizontally by something a state change does not
alter: how overdue the next action is, fit band, canton, owner, or sector. The
first is the one the option exists for: overdue at the top, because a board is
read downwards and the thing that has slipped should not sit below the thing
that has not. Grouping by *segment* is deliberately not offered, and the reason
is arithmetic rather than taste: a company carries several segments, its card
would appear in several lanes, and a card you can drag in one lane while a
stale copy sits in another is worse than no grouping. A company carries one
sector, so a sector lane holds each card exactly once. Segment stays a filter,
where one company is one row.

Dragging is the fast way to move a card and not the only one: each card has a
plain form under it that posts the same change, so the board works from a
keyboard, without JavaScript, and on a phone. A card in `won` offers the
handoff, opening a client record that keeps the file rather than copying the
name.

The board is a scout view. It reads `intel.prospect` and knows nothing about
`crm`; the client chip on a won card is looked up by the console, which is
allowed to know about both modules where `modules/scout` is not.
