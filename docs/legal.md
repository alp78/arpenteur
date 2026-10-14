# The law, and what the tool does about it

Arpenteur reads a public register, keeps personal data about the people named in it, reads other companies' websites and sends email to strangers. Each of those has a legal frame in Switzerland. This page says what the tool does, which rules it was built against, and which questions stay the operator's. It is not legal advice; the articles cited are the place to start.

## The data held

- Companies, from the commercial register. Public by law, free to read.
- Officers, parsed from the gazette notices: names, roles, registered origins and domiciles. These are personal data about identifiable people. The parse is a reading of prose, and the pages that show it say so.
- Sole proprietorships, legal form 0101, are registered in a person's own name. Their entry is personal data in a way a company's is not. The territory setting decides whether they are swept at all.
- Email addresses taken from companies' own websites, each with the page it was published on.
- Website research written by a model, from pages the model's provider fetched.
- Everything a client tells you, in the dossier and the documents.

Every row that holds a fact about the outside world carries where it came from and when. Under the Federal Act on Data Protection, in force since 1 September 2023, that answer is the difference between a dataset that can be accounted for and one that cannot.

## Where the data goes

- The database runs on your own machine and listens on it alone.
- The stages that call a model send what they read to the model's provider: the registered purpose for the sorting, the company's file for the scoring and the drafts, the dossier and the documents for the approach and the preparation. The provider processes that text outside Switzerland. Read the provider's data processing terms before putting a client's documents through the approach, and before including sole traders in a survey.
- The geocoding sends addresses to the Confederation's own address search and to nothing else.
- Nothing is sent to any other third party.

## Mass advertising email

The Federal Act against Unfair Competition, LCD art. 3 al. 1 let. o, makes it unfair to send mass advertising email without the recipient's consent, without a correct sender and without a free and easy way to refuse further messages. A campaign in Arpenteur is built to stay on the right side of each clause:

- every message names the sender and carries an opt-out sentence with a working address
- a refusal is honoured on sight: the address is suppressed, and no run restores it
- a wave goes only after a person received and read the test and typed the wave's code
- the batches are small, the pace is slow, and the next wave waits for the bounces of the last
- a draft is read by a person before anything is sent

What the tool cannot decide is whether a given recipient may be written to at all: whether the message is advertising within the meaning of the act, whether an existing relation or a published invitation to contact covers it, and whether the recipient is a company or a person. Those questions are the operator's, per campaign.

## The register's terms

Access to the register's API is granted free on conditions stated when the credentials are issued:

- the origin of the data must be indicated, and any modification referenced; the tool prints the notice on every page and document built from register fields, and a second notice where something was derived
- regular mass querying must be renounced; the sweep is a few hundred requests, the sync is one request per day, and the detail fetch refuses to run over 2'000 companies unless the box saying so is ticked
- the data carries no warranty and has no legal effect; only the certified cantonal excerpt and the gazette text are binding
- no support is provided

A user who disturbs the service can be blocked. The tool paces every request, backs off when told to, and caches what it fetched.

## Other people's websites

The research stage runs through the model provider's own fetching. The address stage opens websites itself, and it is the one place the tool reads a stranger's site: it reads the robots.txt first and obeys it, opens a handful of pages, and stores the page each address came from.

The map's base layer and the geocoder are swisstopo, published as open government data on the condition that the source is named, which the map does.

## The operator's checklist

Before the first campaign:

- decide whether sole proprietorships are in the territory, and why
- write the opt-out address into the mailbox settings and confirm the mailbox is read
- keep the register's notice on every output that leaves the tool
- read the model provider's data processing terms
- keep the weekly export encrypted and off the machine
- answer a request from a person named in the data from the database itself: every row about them can be found by name, and the suppression of an address is one click

The licence under which Arpenteur is provided is in the file `LICENSE` at the root of the project.
