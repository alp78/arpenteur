# Clients

The record of each engagement. A client is the company, not a copy of it: promoting a prospect keeps everything already gathered, the register entry, the officers, the research and the notes, and adds the projects, the timeline, the dossier and the papers.

## The portfolio

Three counts open the page: clients, projects, and what is in the diary over the next thirty days. The table lists each client with its code, its status, how many projects are active, when you last spoke and what comes next.

## Adding a client

A client comes from three places:

- the "Make this a client" action on a company's page
- the "make a client" button on a won card of the Board
- the form at the foot of this page, which takes a Swiss UID, fetches the company from the register and opens the client, for a client the survey never swept

The third is the ordinary case: a survey covers a region and a set of legal forms, and a client is often outside both.

## The client's card

The header restates the register entry and links to the company's record. The strip under it links to the dossier, the interview preparation and the notes, and says how far each has got.

**Projects**

A project has its own code, because it outlives the conversation that created it and has to be nameable in a contract and on an invoice. The fold "New project" takes a name, a billing mode, a budget, a start and a due date, and a description. A project's page shows its own entries of the record, and lets its status, billing, budget and dates be changed; a status change is logged as a decision. Deleting a project asks for a reason and detaches its entries rather than destroying them.

With the sales module, the project's page also keeps the time spent on it: a date, a number of hours and a note per entry, each marked billable or not. "Bill the time" turns every billable hour not yet billed into one line of a new draft invoice at the hourly rate given, and marks the entries so no afternoon is billed twice. The Reports page sets hours logged against hours billed.

**Sites**

Every place the client operates, each placed on the map as it is saved. The registered address arrives when the geocode stage has run; the other sites are typed here. A site whose address does not resolve is kept without a point, and the card says so.

**The record**

One timeline of everything that happened with this client, newest first: emails filed from Mail, calls, meetings, deadlines, notes, documents, decisions and milestones. A planned entry can be marked as happened or cancelled. Any entry can be deleted with a reason, which leaves a line saying what went and why. Notes written while the company was still a prospect are shown read-only, with a link to the company's record.

The form "record something" takes the kind, whether it happened or is planned, the date, the direction for a message, the project, who it was with, a subject, a body and the Message-ID of an email. An email recorded with its Message-ID can never be filed twice.

**The standing brief**

What stays true about this client, the owner and the status: active, prospect, dormant or former.

**Quotes and invoices**

With the sales module, the card lists the client's quotes and invoices and offers to start one of each. A new quote opens with a first draft version; a new invoice opens as a draft, named after what it bills.

## The notes

A client's notes are Markdown, written in the box or imported from a file. An imported file stays the master: importing it again after an edit updates the note rather than writing a second one. Each note can be opened, read back as Markdown, pinned when it is the standing brief rather than another meeting note, and deleted with a reason. Notes are searched in full text, and the suggested approach reads them above the client's own documents, as instruction rather than evidence.

## The dossier and the approach

The dossier is where what the client runs, who decides, what is exposed and what was tried is written down, in named sections grouped as the organisation, the systems and the engagement. Each section carries its source, in your own words: observed on the network, said by the director on the call, read in the annual report, inferred. Documents the client sends are dropped on the page, stored once by their content, and turned into text so the model can read them.

Suggest an approach runs a model over the dossier, the documents and the notes, and returns where the client stands, how to open, what to raise, what to offer, what it costs, what could go wrong and what is still unknown. Prices come from the rate card under Settings and from nothing else: work the card does not cover comes back unpriced rather than invented. The button waits until a section is filled or a document is held, because advice from a register entry alone is a horoscope. Each suggestion is kept as a version, and Mark as used records which one went into a real proposal.

## The interview preparation

Extract the vocabulary reads the same material and returns the terms a meeting may turn on: what each is, why it is in this client's file, what to be able to say without looking it up, one question it lets you ask, and where to check it. Above the glossary sits the order to revise in. Ticking a term as known survives every later run.

## Related pages

- [Calendar](calendar.md), the planned entries of every client
- [Mail](mail.md), where messages are filed to the record
- [Quotes](quotes.md) and [Invoices](invoices.md)
- [Settings](settings.md), under Rate card, for the prices the approach may quote
