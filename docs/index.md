# Help

Arpenteur is a prospecting and client tool for independents. It runs on your machine, on your data.

It surveys the Swiss commercial register. It ranks what it finds against your own brief. It reaches the companies worth a conversation. It follows each client from the first meeting to the paid invoice.

## Ten minutes to a first list

With [the installation](install.md) done and the register credentials in `.env`:

1. Open the console at `http://127.0.0.1:8000`. Home lists what the first run still needs.
2. Under Settings, tick the cantons and the legal forms of your territory, then write the scoring brief from the example at the root of the project.
3. On Operations, press Sweep the register. It stores the names of every company in the territory, in minutes.
4. Press Fetch company detail with a size of 250. Each company's purpose and address arrive.
5. Press Sort into segments, dry run first to read the labels, then for real.
6. Press Score against the brief with "only companies with website research" unticked.
7. Open Companies and sort by fit: the first ranking, from the register alone.

The website research, at about a franc per company, comes after, on the companies that ranking put first. [The setup page](setup.md) walks through every section before the first campaign.

## The pages

The header follows the work. Each entry has a guide, reached from the "?" on the page itself.

**Prospecting**

- [Companies](guide/companies.md): the register as you swept it, filtered and searched
- [Board](guide/board.md): the companies you decided to pursue, each at one state
- [People](guide/people.md): the officers behind the companies
- [Map](guide/map.md): the territory, drawn
- [Reports](guide/reports.md): what the survey adds up to

**Outreach**

- [Lists](guide/lists.md): curated mailing lists
- [Campaigns](guide/campaigns.md): email sent in tested, approved waves
- [Drafts](guide/drafts.md): one proposal per firm, written for your review
- [Mail](guide/mail.md): replies, matched to the company they came from

**Clients**

- [Clients](guide/clients.md): the record of each engagement
- [Programmes](guide/programmes.md): one endeavour across many companies
- [Calendar](guide/calendar.md): what is planned

**Sales**

- [Quotes](guide/quotes.md): priced proposals, versioned
- [Invoices](guide/invoices.md): issued, paid, credited

**System**

- [Operations](guide/operations.md): run a stage
- [History](guide/history.md): what every run did
- [Settings](guide/settings.md): the territory, the vocabularies, the brief, the identity

## Setting up and running it

- [Installation](install.md): Docker, uv, the `claude` command or an API key, on Linux, macOS or Windows
- [Setup](setup.md): the environment file and every Settings section, in the order a first run needs
- [Running it](operations.md): backup and restore, updates, moving machines, uninstalling, and what to do when something refuses
- [The command reference](cli.md): every command, generated from the program itself
- [Glossary](glossary.md): one word per concept
- [The law](legal.md): data protection, mass email, the register's terms, and the operator's checklist

## Why it is built this way

The design pages are the reasoning behind the tool, kept with the measurements of the first deployment. They are not needed to use it.

- [The survey, and the register it reads](design/survey.md)
- [The board](design/board.md)
- [People, and who reaches whom](design/people.md)
- [The map](design/map.md)
- [Lists and campaigns](design/campaigns.md)
- [Clients, projects and the record](design/record.md)
- [Programmes](design/programmes.md)
- [Notes](design/notes.md)
- [The dossier, the papers and the approach](design/dossier.md)
- [Settings as an overlay](design/settings.md)
- [The console](design/console.md)
- [Moving an installation to another machine](design/workspace.md)

In a terminal, `arpenteur help` prints this page, `arpenteur help --list` names every page, and `arpenteur help guide/companies` prints one.
