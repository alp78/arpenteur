# Operations

Every stage, with how much it has done, how much is waiting for it, and the control to run it. The cards read top to bottom in the order the pipeline runs; running a stage twice is safe and costs nothing extra, because every stage picks up where it left off.

## A card

Each card states what the stage does, what must have happened first, what it produces, what it costs, and exactly what pressing the button will do. The two counts, done and waiting, are the number that makes the button meaningful: "3 done, 41'238 waiting" says what to press next.

**The shared controls**

- How many companies: a size, from ten to a thousand, or the whole queue. Start small; the rest waits for the next run.
- Dry run, ticked by default: list what would be processed and stop. Nothing is written and nothing is spent. One card, Sort into segments, says that its dry run still calls the model and saves only the writing.
- Canton, Sector, Segment, Fit band: narrow the companies a stage runs on.
- Specific companies: a list of UIDs, which ignores the filters above.

## The stages

**Finding companies, all free**

1. Sweep the register asks the commercial register for every company in the chosen cantons and legal forms, and stores their names. A sweep is resumable and skips what it covered.
2. Sync the register reads the official gazette day by day and marks every company whose entry changed. One request per day.
3. Fetch company detail asks the register for each company's full entry. One request per company, paced at about two a second; above 2'000 companies a box must be ticked, because the register asks users to avoid regular mass querying.
4. Extract officers parses the notices already stored into officers. No network.
5. Resolve the people works out which officer rows are the same person.

**Reading companies, with a model**

6. Sort into segments reads each registered purpose and gives the company one sector and any number of segments. A cheap model call per batch of twenty.
7. Research websites searches for each company's website, reads it, and records what it sells, what it runs on and what argues against approaching it. About a franc and between seven and forty-five minutes per company, measured on the first deployment. It refuses to start without a size, a list of UIDs or a dry run.
8. Score against the brief reads the scoring brief and each company's file together and returns a score, a band, the reasoning and an opening line. One model call per company.

**Reaching companies**

9. Find an address opens the websites already found and takes the addresses the company publishes, with the page each came from. Free, and robots.txt is honoured.
10. Check the addresses asks the domain name system whether each domain takes mail. Free. Lists and drafts take only addresses it found deliverable.
11. Draft the first messages writes one proposal per company for the Drafts page. One model call per company, capped at 25 by default; it sends nothing.

**The map, with the geo module**

12. Put them on the map turns registered addresses into coordinates through the Confederation's address search. Free.
13. Draw the cantons fetches the cantonal outlines from swisstopo. Free, once.

**The mailbox, with the clients module**

14. Read the mailbox imports correspondence read-only and matches it to companies, for the Mail page.
15. Read delivery failures reads bounces and refusals into the address verdicts. A campaign's next wave waits for it.

Four more stages run from their own pages, after the checks those pages make: the suggested approach and the interview preparation from a client's dossier, a list's address refresh from the list, and a wave's test and send from the wave.

## The guards

- One stage at a time per lane. While a run is going, the other buttons wait and say which run to watch.
- The two stages that cost the most, Research websites and Draft the first messages, refuse to start without a declared size, a list of UIDs or a dry run.
- Every stage's own refusal still applies: a research run over more than 200 companies is refused by the stage itself.

Starting a stage opens its run page, where the log fills in as the stage talks. The run can be stopped from there.

## Related pages

- [History](history.md), every run and its log
- [Settings](settings.md), the territory and the vocabularies the stages read
- [The command reference](../cli.md), the same stages from a terminal
