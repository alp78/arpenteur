# Reports

What the survey, the correspondence and the sales add up to. Every section is a view over the other modules' tables; the application writes nothing to this page.

## The sections

Each section is one table, captioned with the name of the view behind it, up to 200 rows.

- How far the survey has been taken
- The funnel, and how long each step took
- Score bands, and how reachable each one is
- The survey by canton
- The survey by sector
- The survey by segment
- What was picked, and what came of it
- On the board and not moving
- What each stage has cost
- Correspondence read, matched and filed, with the clients module
- Each engagement's file, with the clients module
- What is out, and what it is worth, with the sales module
- Billed and received, by month, with the sales module
- Issued and not settled, with the sales module
- Hours logged against hours billed, with the sales module

A section whose module is not installed is listed under "Not installed" rather than drawn empty.

## Refreshing the figures

The aggregates are computed when you ask, not on every page load. The button at the top refreshes every view and reports how many were refreshed, or why one failed. The figures on the page are as old as the last refresh.

## Asking something the page does not answer

The box at the foot shows how to open the database read-only with DuckDB and query the same views, for a question the page was not built for.

## Related pages

- [Operations](operations.md), whose cost the spend report sums
- [Invoices](invoices.md), where what is owed is settled
