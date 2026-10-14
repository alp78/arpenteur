# Companies

The register as you swept it. Every company the sweep found is a row here, and every later stage adds a stamp to the row: detail fetched, sorted, researched, scored, an address looked for. This is where a survey is read, and where a single company is opened to be corrected or worked.

## The list

Each row shows the company with its UID, the canton, the sector, a progress stamp, its segments, the fit score, the website and the pipeline state. A score marked with a star was set by hand.

The progress stamp spells out how far the pipeline has taken the company, one letter per stage:

- D: detail fetched from the register
- S: sorted into a sector and segments
- W: website researched
- F: scored against the scoring brief
- @: an address was looked for

Rows come best fit score first, then by name. A search orders them by how closely the name matches. At most 200 rows are shown; narrow the filter to see the rest.

## The filters

The filter bar keeps its values in the address bar, so a filtered list can be bookmarked or sent to someone.

- Search, by name or by UID
- Canton
- Sector, shown once sectors are defined under Settings
- Segment
- Fit band: high, medium, low or excluded
- Pipeline state, which is the column the company sits in on the Board
- Progress, which selects companies by how far the pipeline has taken them, such as "Detail fetched, not yet segmented" or "Has a usable email address"

The progress filter is the one to use before starting a stage on Operations: it answers "how many are waiting for this step" with the rows themselves.

## The company page

Opening a row shows everything held about one company, grouped in cards.

**The actions at the top**

- Make this a client, which opens a client card that keeps the whole file. A company that is already a client shows a link to its card instead.
- Research this website, Score this company and Find an address each run that one stage on this company alone, and open the run's page. The research costs about a franc and takes between seven and forty-five minutes per company; the other two are free or cost a few centimes.
- While a stage is running, these three buttons wait: one stage at a time.

A company that is winding up or struck off the register says so under its name. It is kept for the record and left out of the stages that cost money.

**The cards**

- Fit: the score out of 100, the band, the reasoning, what matched the brief and what argued against, and a draft opening line. The opening is a draft for a person to rewrite; nothing on this page sends it.
- Pipeline: state, owner, next action and its date. Saving a state puts the company on the Board.
- Sector: one choice from the list under Settings. A sector set here is marked as set by hand, and the classifier never overwrites it.
- Segments: any number of the labels defined under Settings. A change here survives re-running the classifier.
- Registered purpose, as the register publishes it.
- Officers, parsed from the gazette notices: name, role, since when, and how many other boards each sits on. Officers who left are greyed. The parse is not the certified excerpt, and the card says so.
- Shares an officer with: the companies connected through a common officer, each with a link to the route between the two.
- Web research: the website found, how sure the match is, the summary and the signals. A company not yet researched says so.
- Contacts: every address held, where it was found, and its state. Suppress removes an address from every list and every draft; a suppressed address is never restored by a later run. Restore undoes a suppression.
- Mail recognised by: the addresses and domains mail is matched on. Forget stops future matching and keeps what was filed.
- Notes: a text box with a kind, a date and an author. The notes follow the company when it becomes a client.
- Register notices: the last eight publications, with the attribution the register requires.

## The route between two companies

From the "shares an officer with" card, the route page walks up to four hops of shared officers between two companies and lists each chain with the people it passes through. When there is none within four boards the page says so: a longer chain is a chain of strangers.

## Where the rows come from

- The Sweep the register card on Operations fills the list. Until it has run, the page offers that card.
- Fetch company detail adds the purpose, the address and the notices.
- Sort into segments adds the sector and the segments.
- Research websites, Score against the brief and Find an address add the rest.

A correction made here goes through the same code the stages use, so it is the same row a re-run has to respect.

## Related pages

- [Board](board.md), the companies with a pipeline state
- [People](people.md), the officers across companies
- [Operations](operations.md), where each stage is run
