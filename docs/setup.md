# Setup

[Installation](install.md) leaves a running console with nothing configured. This page fills it in, in the order a first run needs: the environment file, then the Settings page section by section, then the doctor.

Every setting on the Settings page is saved in the database and read by the next run. The file `arpenteur.toml` at the root of the project ships the defaults with their explanations, and stays as the reference; a section saved on the page is applied over it.

## The environment file

`.env` holds every credential and nothing else. The application reads it from the project folder; a real environment variable of the same name wins over the file. Each name is explained in `.env.example`, and the Secrets section of Settings says which are set.

**The database**

- `DATABASE_URL`: where PostgreSQL listens. Empty for the container the compose file starts.
- `POSTGRES_PASSWORD`: the password the compose file gives the container. Change both together.

**The register**

- `ZEFIX_USER` and `ZEFIX_PASSWORD`: the credentials the register issues, free, by email to zefix@bj.admin.ch.
- `ZEFIX_URL`: another host for the register's API, for a mirror. Empty for the official one.

**Model access**

- `ANTHROPIC_API_KEY`: a key from console.anthropic.com, used when Model access is set to the key, or to the subscription when there is no sign-in. Empty when running on a subscription.

**The mailbox**

- `MAIL_IMAP_SERVER` and `MAIL_IMAP_PORT`: the IMAP server and its port, 993 as a rule.
- `MAIL_USERNAME` and `MAIL_PASSWORD`: the account. Most Swiss hosts issue one password per application and refuse the account password over IMAP; ask your host for an application password.

**Sending**

- `SMTP_SERVER` and `SMTP_PORT`: the outgoing server and its port, 587 for STARTTLS or 465 for SSL.
- `SMTP_USERNAME` and `SMTP_PASSWORD`: the account, when it differs from the mailbox. Each of these falls back to the mailbox value when empty.
- `SMTP_SECURITY`: `starttls` or `ssl`. Empty for STARTTLS.
- `SMTP_SENDER` and `SMTP_REPLY_TO`: the From address and the Reply-To. Both can be set under Settings, Sending instead; a value in the environment wins and the field on the page is greyed.

**Infomaniak aliases**

- `INFOMANIAK_USERNAME`, `INFOMANIAK_PASSWORD`, `INFOMANIAK_IMAP_SERVER`, `INFOMANIAK_IMAP_PORT`, `INFOMANIAK_SMTP_SERVER` and `INFOMANIAK_SMTP_PORT`: a mailbox hosted at Infomaniak can keep its credentials under these names. They are read when the generic names above are empty.

The Reply-To must be the mailbox Arpenteur reads, or an opt-out arrives where nothing reads it. The doctor checks this.

## The Settings page, section by section

Open the console and go to Settings. The index at the top jumps to each section.

**Territory**

Tick the cantons to survey. Press "Refresh from the register" under the legal forms once, which fetches the register's own catalogue, then tick the legal forms to include. The usual company forms are the Sàrl, code 0107, and the SA, code 0106. The sole proprietorship, code 0101, is registered in a person's own name: its entry is personal data in a way a company's is not, and a campaign addressing sole traders carries obligations under the data protection act that one addressing legal entities does not. The tool ships with it on and does not decide for you.

**Sectors and segments**

Replace the shipped examples with your own vocabularies. A sector is what a company does for a living, one per company; a segment is how a company fits your work, any number per company. Each definition is one concrete line, and the classifier sees nothing else. Keep a sector for the purposes too general to place.

**Research**

Rewrite the sentence the website research reads every company against, so it names what you are scouting for and asks for what argues against approaching.

**Scoring brief**

Write the brief the scoring reads: what you offer, who it serves well, who it serves badly, what to look for, what warns off, how you open a conversation. The file `campaign.example.md` at the root of the project is a worked example for a fictional business; copy its structure and replace every section. A brief written for someone else's business produces confident scores built on nothing. Write it in the language you will write to companies in.

**Weights**

The shipped weights are a starting point. Change them once you have read a few scores and know which part you trust.

**Identity, with the sales module**

Your name as it appears on an invoice, the address, the IBAN, the VAT number or none, the payment terms and the days to pay. The IBAN is checked when saved; an invoice cannot be issued without a valid one.

**Catalogue and rate card, with the sales module**

The catalogue is what you sell, as lines with a price or a price range, for quotes and invoices. The rate card is the same prices in prose, for the suggested approach on a client's dossier. Leave the rate card empty rather than guessing: the advice then identifies the work and prices nothing.

**Sending**

The From name, the folder a copy is filed in, the test recipient, and the pace: messages per run, messages per day, seconds between messages. The daily figure is your host's limit, from your plan, and mail sent by hand counts against it too.

**Mailbox**

Your own domains, one per line, so that mail from your website's contact form is never matched to a company.

**Enrolment**

Only needed for drafts. Name the message brief, a Markdown file under the project folder that says what the drafted messages are about, and the programme the sends move. If the drafts carry an invitation, write it under `templates/<programme in lower case>/` as `invite.fr.md`, `invite.de.md` and `invite.it.md`, and fill the placeholders' values here. The example folder `templates/example/README.md` describes the file.

**Register**

The contact sent as the User-Agent on every request to the register and to the map services: a name and an address their operators can write to. Replace the shipped placeholder with yours.

**Model access**

The shipped defaults name the current model family, one model per role, and the subscription as the account. Change the account to the key when you set one. The two efforts can stay empty.

**Signature**

Write the closing block a campaign appends to every message, on its own page. A campaign cannot render without one.

## The message templates

A campaign reads its messages from a folder under `templates/`, one file per language. "Start from the example" on the new-campaign form copies `templates/example/` under the campaign's code; then open each message from the campaign's page and replace the text. The folders under `templates/` other than the example stay out of git.

## Trying it before the credentials arrive

Two recorded register answers ship with the code, so the sweep and the detail fetch can be exercised end to end with no credential and no network:

```bash
uv run arpenteur source zefix --fixture
uv run arpenteur stats --fixture
```

The fixture writes to a separate database, `arpenteur_fixture`, created on the same server when first asked for, and never to the live survey. Every row it writes carries a provenance that names the fixture.

## The doctor

```bash
uv run arpenteur doctor
```

The doctor signs in to the mailbox, opens a session with the mail server without sending, and checks every setting a first run needs. Each line names the piece, its state and the place to set it. It exits with 1 while something is missing. The same check runs from the Home page, under "Run the full check".

## The first survey

With the register credentials in place, the first run is on Operations:

1. Sweep the register, for the configured territory. Free, minutes.
2. Fetch company detail, 250 at a time to begin with. Free.
3. Sort into segments, with a dry run first to read the labels, then for real.
4. Score against the brief, with "only companies with website research" unticked, to get a first ranking from the register alone.

Research websites comes after, on the companies the first scoring ranked highest, with a size of ten: it costs about a franc per company. [The guide to Operations](guide/operations.md) describes every card.
