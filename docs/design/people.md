# People, and who reaches whom

> The figures on this page were measured on the first deployment, in 2026, over a survey of five cantons of French-speaking Switzerland. Yours will differ.

The register publishes a notice on every mutation, and those notices name
officers. Parsed, they are 4'943 rows across 2'530 companies, obtained without
a single request: the notices were already stored, and the `people` stage reads
them. `resolve` then works out which of those rows are the same human, and the
edges between them are what turn a list of companies into a map of who reaches
whom.

**The resolution splits when unsure, on purpose.** Two officer rows are one
person when their names fold to the same string *and* their registered origins
agree. The origin is what makes this possible at all: it is a legal attribute
of a Swiss person, printed in the notice, and unlike the domicile it does not
change when they move house. A row with no origin joins the others of its name
only when exactly one origin is known among them.

The direction of error is chosen. A person wrongly split is a missing edge: a
gap in a suggestion, and recoverable. A person wrongly merged is a false
statement about a named individual in a tool that goes on to propose writing to
them, and no later correction reaches the letter that was sent. So every party
records which branch of the rule produced it and how much to believe it, and
the weaker kinds say so where they are read rather than in a footnote.

Two live splits are worth knowing about, because neither is a fault in the
rule: `Macédoine` and `Macédoine du Nord` are one country renamed in 2019, and
the commune of `Rossens` was absorbed into `Gibloux` in 2016. Both move a
person's stated origin without moving the person. A rule loose enough to join
them would also join two genuinely different people, so the split stands and
the candidates are offered to a human on the party's page.

**Before trusting any of it, look at the most connected name.** The first time
that was done here it was `Carouge GE`, on twenty-three boards. It is a town:
a domicile is a capitalised run of two words exactly like a surname and a first
name, and the officer parser had been reading them as people. It would have
become the most valuable node in the graph and it was nobody at all. Three
parser faults and two write faults came out of that afternoon, and they are why
`people --reparse` can now withdraw a row and overwrite an attribute rather
than only add.

**Board history, not a snapshot.** Each role carries the date of the oldest
notice naming the person in it and, where the register says so, the date they
left. Departed officers used to be dropped at parse time, on the grounds that
addressing someone who left would be worse than missing them. That reasoning
stands and is why `left_date` is a column rather than a deletion: `people_for`
returns current officers unless asked otherwise, so nothing that addresses a
human sees a departure by accident. What it stops doing is throwing the history
away, because "they sat together at a company neither is at now" is a real link
and a poor introduction, and the two must not look the same in a list.

From 5'688 officer rows, of which 745 are seats somebody has since left: 4'440
people, 176 of them on more than one board. A company page names its officers
as people, says since when and how many other boards each sits on, and lists
the companies it shares one with. `/company/A/route/B` walks up to four hops of
shared officers and reports no route as an answer, because a chain longer than
that is a chain of strangers.
