# Settings

What the register is swept for, how companies are read and scored, who is billing and how mail leaves. Everything here decides what the stages on Operations do, and every section is saved in the database and read by the next run without a restart.

The index at the top of the page jumps to each section. [The setup page](../setup.md) walks through them in the order a new installation fills them in; this page says what each one is for.

## Territory

Which cantons the register is swept for, and which legal forms. A legal form that is not ticked is never asked for, in any canton: this is the setting that decides whether foundations, associations and cooperatives exist for this tool at all. The legal form catalogue is fetched from the register with the button under the list, so a code that changes there stops being silently wrong here.

Narrowing the territory never loses data: companies already held are kept.

## Sectors and segments

Two vocabularies, each a list of names with a one-line definition, and the definitions are the classifier's prompt word for word.

- A sector is what a company does for a living: exactly one per company, from the list. Keep an entry for the purposes too general to place.
- A segment is how a company fits your work: any number per company.

A changed definition applies when Sort into segments is run again with its re-label box ticked. Labels set by hand are never overwritten.

## Research

The sentence the website research reads every company against. It must also ask for the evidence that argues against approaching, because the scoring cannot weigh what was never collected.

## Scoring brief

What companies are scored against: what is on offer, who it serves well, who it serves badly, what to look for, what warns off, and how to open a conversation. The brief is written here and kept in the file `campaign.md` at the root of the project, outside git, so a surprising run can be answered with a diff. Every score stores a digest of the brief it was taken against; rewriting the brief makes the old scores stale, and the next scoring run re-takes exactly those.

## Weights

What each part of a score is worth: fit, need, timing, value, reach and relationship. They are scaled to add up to one. Saving opens a new weight set when they changed; scores already made keep the set they cite.

## Identity, with the sales module

Who is billing: the name, the address, the IBAN, the VAT number, the payment terms and the days to pay. Every field is printed on paper that goes to a client. The IBAN is checked when saved. An invoice issued after a change carries the new details; one already issued keeps the ones it was issued with.

## Catalogue, with the sales module

What you sell and what it costs, as lines: a code, a name, a unit, a price or a price range, and what the price depends on. A quote line taken from the catalogue carries its price. Saving opens a new rate card when the lines changed; a quote already sent keeps the card it cites.

## Rate card

The same prices in prose, in your own words, for the suggested approach: it prices work from this text and from nothing else, must name the line it used, and leaves work the card does not cover unpriced. An empty card is a legitimate choice: the advice then identifies the work and prices none of it.

## Sending

How a campaign's messages leave: the From address and name, the Reply-To, the security of the connection, the folder a copy is filed in, the test recipient, the messages per run and per day, the seconds between messages, and the signature picture. The server and its password are in the environment file; a field the environment decides is shown greyed, with the name of the variable.

The daily figure is your host's limit, from your plan. Mail sent by hand counts against it too.

## Mailbox

Your own domains, one per line. Mail from these is never matched to a company. The server and the password are in the environment file.

## Enrolment

What a drafted message is about: the programme its sends move, the message brief the draft stage writes from, the segments it writes to, whether to add the invitation, and the values the invitation's placeholders take. The brief is a file under the project folder; the draft stage refuses while it is empty.

## Register

The contact sent as the User-Agent on every request to the register and to the map services, so their operators can reach whoever is asking. A name and an address they can write to.

## Model access

Which model does which job, and which account pays: the subscription through the `claude` command, the API key in the environment, or the subscription when signed in and otherwise the key. One model id per role, each starting with `claude-`, and the effort for the two stages that take one.

## Secrets

Which environment variables are set and which are not. Values are never shown and never stored in the database.

## Signature

The closing block appended to every message a campaign sends, with its own page and a preview of what a recipient sees.

## Related pages

- [Setup](../setup.md), the sections in the order to fill them
- [Operations](operations.md), the stages these settings drive
