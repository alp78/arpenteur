# Glossary

One word per concept, used in the header, the pages, this manual and the commands.

**The register**

- UID: the identifier of every registered entity in Switzerland, written `CHE-123.456.789`. The tool stores it without dots or dashes and accepts either spelling.
- Zefix: the federal index of commercial register entries, and the source of every company held. Credentials are free.
- Legal form: the kind of entity, coded as in the eCH-0097 standard: 0107 for a Sàrl, 0106 for an SA, 0101 for a sole proprietorship. The catalogue is fetched from the register under Settings.
- Gazette: the official journal, FOSC in French, SHAB in German, where every change to a register entry is published. Officers are parsed from its notices.
- Sweep: the stage that asks the register for every company in a territory and stores the names. Resumable.
- Detail: a company's full entry, fetched one company at a time: purpose, capital, address, notices.
- Sync: reading the gazette day by day to mark the companies that changed, one request per day.

**The survey**

- Territory: the cantons and legal forms a survey covers, under Settings.
- Sector: what a company does for a living, one per company, from your list.
- Segment: how a company fits your work, any number per company, from your list.
- Research: the stage that finds and reads a company's website with a model, and what it returned.
- Signal: what the research looks for, as one sentence under Settings.
- Scoring brief: the text companies are scored against, written under Settings and kept in `campaign.md`.
- Fit score: 0 to 100, with a band: high, medium, low or excluded. A score set by hand is marked and never overwritten.
- Opening: the draft first line the scoring proposes, for a person to rewrite.
- Weights: what each part of a score is worth; a saved set is cited by every score made with it.
- Officer: a person named in a notice with a role at a company. A party is one person across their officer rows, after the resolve stage.
- Route: the chain of shared officers between two companies, up to four hops.

**Reaching companies**

- Address: an email address held for a company, with the page it was published on and how it was found.
- Verdict: what the domain name system said about an address's domain: deliverable, maybe, dead or unknown. Only deliverable addresses reach a list or a draft.
- Suppressed: an address a person withdrew. Never restored by a run.
- Bounce: a delivery failure read from the mailbox and recorded on the address.
- List: a curated mailing list, one chosen mailbox per firm, with the reason for every firm left out. Frozen once a campaign sends from it.
- Pin: a person's decision on a list, to keep or drop a firm or to fix its language or address, with a reason.
- Campaign: a message plan over one list, sent in waves.
- Wave: one batch of a campaign, rendered, tested, approved and sent, in that order.
- Strategy: how a list is cut into waves.
- Quota: the messages per day the host allows, under Settings, Sending.
- Draft: one proposed message for one company, written by a model for a person to read. Never sent by itself.
- Message brief: the text the draft stage writes from, named under Settings, Enrolment.
- Invitation: a text per language appended to drafts for a programme, with placeholders filled from Settings.
- Signature: the closing block appended to every message a campaign sends.
- Opt-out: the sentence appended to every message naming the address a recipient writes to in order to be left alone.

**The pipeline**

- Board: the companies with a pipeline state, one column per state.
- Pipeline state: one of new, researching, contacted, replied, meeting, won, lost or parked.
- Programme: one endeavour across many companies, each a member at its own stage.
- Member: a company on a programme. Not a client.
- Stage: a column of a programme, in your words. Also a step of the pipeline on Operations; the page says which.
- Ledger: the table of where each member stands, in and out.
- Enrolment: what a member answered to an invitation: the platform and the tester's account.

**Clients**

- Client: a company with an engagement. The same row as the company, with more held on it.
- Project: one piece of work for a client, with its own code.
- Record: the timeline of everything that happened with a client. One table, read as a story on the card and as a diary on the Calendar.
- Note: your own writing about a client, in Markdown.
- Dossier: what the client runs, who decides, what is exposed and what was tried, in named sections, each with its source.
- Document: a file the client sent, stored once by its content and turned into text.
- Approach: a model's suggestion of how to open and what to offer, priced from the rate card. Versioned.
- Preparation: the vocabulary a meeting may turn on, extracted from the dossier.

**Sales**

- Catalogue: what you sell, as lines with a price or a price range.
- Rate card: the catalogue in force at a date, cited by every quote; also the prose version the approach reads.
- Quote: a priced proposal, versioned. A sent version never changes.
- Invoice: a draft until issued. Issuing takes the next rank and freezes it.
- Reference: the number the client sees on an invoice.
- Rank: the invoice's position in the register. Never printed.
- Credit note: the document that reverses an issued invoice.
- Payment part: the Swiss QR bill printed at the foot of an invoice.

**The system**

- Operations: the page where stages are run.
- Run: one execution of a stage, with its log, listed under History.
- Dry run: a run that reads and reports and writes nothing.
- Lane: the runs that cannot overlap: the pipeline, the sends and the dossier stages each have one.
- Module: a part of the application with its own schema: scout, geo, clients, sales and analytics.
- Overlay: a setting saved in the database, applied over the file's value.
- Doctor: the check of everything a first run needs.
- Workspace: the archive that carries an installation's data to another machine.
