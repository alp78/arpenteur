# ARPENTEUR

Copyright (c) 2026 Alexis Peringer. All rights reserved. See [LICENSE](LICENSE).

The arpenteur is the surveyor who paces out a territory parcel by parcel. This one surveys a market from the Swiss commercial register, ranks what it finds against your own brief, reaches the companies worth a conversation, and follows each client from the first meeting to the paid invoice. It runs on your own machine, on your own data, for one independent.

What the survey is for is configuration rather than code: the territory, the sector and segment vocabularies, what the research looks for and what the scoring reads all live under Settings, so the same tool serves a consultancy scouting for clients, a recruiter reading for hiring signals or an association looking for members.

## The parts

**Prospecting**

- Companies, swept from the register by canton and legal form, then fetched one by one, sorted into your sectors and segments, researched on their own website, scored against your brief
- The board of the companies you pursue, the officers behind them and the routes between them, the map of their establishments, and the reports

**Outreach**

- Lists that keep one mailbox per firm and say why every other firm was left out
- Campaigns sent in waves, each rendered, tested to you, approved and only then sent
- Drafts, one proposed message per company for a person to read, never sent by themselves
- Mail, read from your mailbox and matched to the company it came from

**Clients**

- The record of each engagement: projects, one timeline, the calendar, notes in Markdown
- The dossier of what the client runs, the documents they send, a suggested approach priced from your rate card, and the vocabulary to revise before a meeting
- Programmes, one endeavour across many companies, each at its own stage

**Sales**

- Quotes, versioned, and invoices with the Swiss QR payment part, a register and credit notes

Every model call is reviewed by a person before it reaches a client, and the two places that send email are the only ones that can: a test keeps it so.

## Starting

Three things are needed from outside: Docker for the database, uv for Python, and either the `claude` command signed in to a Claude subscription or an API key. [The installation page](docs/install.md) covers Linux, macOS and Windows under WSL2.

```bash
git clone <the repository address> arpenteur
cd arpenteur
uv sync
docker compose up -d
cp .env.example .env
uv run arpenteur init-db
uv run arpenteur doctor --quick
uv run arpenteur console
```

The console answers at `http://127.0.0.1:8000`, on the machine itself only. The doctor names what a first run still needs; [the setup page](docs/setup.md) fills it in, section by section, and [the index of the manual](docs/index.md) has a ten-minute path to a first ranked list.

## The manual

The manual is the folder `docs/`, read inside the console under Help, or in a terminal with `arpenteur help`:

- [Installation](docs/install.md), [setup](docs/setup.md) and [running it](docs/operations.md)
- One guide per page of the console, under [docs/guide/](docs/index.md)
- [The command reference](docs/cli.md), generated from the commands themselves
- [The glossary](docs/glossary.md) and [the law](docs/legal.md)
- [The design pages](docs/index.md), the reasoning behind the tool, with the measurements of the first deployment

## Modules

Arpenteur is composed of five modules, each owning one PostgreSQL schema and its own numbered migrations:

- scout: the survey, the officers, the scoring, the drafts, and the record of every state change
- geo: the geocoding, the establishments, the map
- clients: clients, projects, programmes, the timeline, notes, the dossier, mail, lists and campaigns
- sales: the catalogue, rate cards, quotes, invoices, time entries
- analytics: views over everything else, written to by nothing

Dependencies point one way, from sales to clients to scout, and a test reads the import graph and the database's constraint catalogue to keep it so. Modules meet on a page through named slots: the client page asks for its panels, and the geo module answers with the sites, the sales module with the quotes and invoices, without the page importing either. A module that is off in the configuration mounts no routes, draws no panels and has no schema. `arpenteur modules list` says what is installed and how far each schema has got.

## Tests

```bash
uv run pytest
```

The suite needs the database container and no credential: the register client runs on recorded fixtures, the model stages on a fake provider, the address stage on a fake HTTP client that records every URL it asked for. Without the container, every test that needs the database skips, and a suite that finishes in a second has skipped them all.

## Licence

Proprietary, all rights reserved, as stated in [LICENSE](LICENSE). The tool reads public registers and third-party services under their own terms; [the legal page](docs/legal.md) says which, and what stays the operator's decision.
