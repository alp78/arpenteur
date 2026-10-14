# People

The officers behind the companies: who manages what, who sits on several boards, and which companies are connected through a common person. Nothing here was fetched: the register publishes a notice on every change to a company, those notices name the officers, and two free stages read them.

## The list

Three counts open the page: people, the ones on several boards, and roles. The table under them lists each person with their registered origin, their domicile, the number of boards they sit on, and how the identity was decided.

The last column is the one to read before trusting a row. Two officer rows are one person when their names fold to the same string and their registered origins agree; the origin is a legal attribute of a Swiss person, printed in the notice, that does not change when they move house. A row with no origin joins the others of its name only when exactly one origin is known among them. The column says which branch of the rule decided, and the weaker ones are marked:

- one row: the person appears once, so nothing was merged
- name and origin: the strongest case
- sole origin: the name matched and only one origin was known
- name only: the weakest case, shown rather than hidden
- set by hand

The rule splits when unsure, on purpose. A person wrongly split is a missing edge, recoverable. A person wrongly merged is a false statement about a named individual in a tool that goes on to propose writing to them.

## The filters

- Search, by surname and first name
- Sitting on at least, a number of boards from 1 to 20

The list sorts by the number of boards, most first. The most connected name is the first thing to check after a resolve: the first time that was done on the first deployment, the top entry was a town, read by the parser as a person.

## The person's page

Opening a row shows the name, the method the identity rests on, the origin and domicile, and two tables:

- Boards: company, role, since when, the company's fit and its pipeline state. Seats that ended are greyed and kept, because "they sat together at a company neither is at now" is a real link and a poor introduction, and the two must not look the same.
- Possibly the same person: rows with the same name and a different stated origin. They are shown and never merged by the program.

## Where the rows come from

- Extract officers, on Operations, parses the notices already stored. Free.
- Resolve the people works out which officer rows are the same human and records the boards. Free, seconds.
- Fetch company detail must have run first; it is what brings the notices in.

Both stages are safe to run again. A re-run of the parser after an improvement can withdraw a row and correct an attribute; a resolve rebuilds the people from the officer rows.

## Related pages

- [Companies](companies.md), where a company's officers are listed with the route to the companies they share
- [Operations](operations.md), where the two stages are run
