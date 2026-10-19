# Command reference

Every command runs as `uv run arpenteur <command>` from the project folder, or as `arpenteur <command>` once the virtual environment is active. `arpenteur <command> --help` prints the same text in the terminal.

This page is generated from the commands themselves by `scripts/cli_reference.py`, and a test refuses a copy that no longer matches them. The pages under [the guide](index.md) say when to run each one.

## arpenteur advise

Suggest an approach for a client from their dossier and papers.

```text
arpenteur advise CLIENT [--dry-run]
```

- `CLIENT`: client code, UID, or part of the name
- `--dry-run`: report what would be read and its size, spend nothing

## arpenteur answers

Read the replies to the enrolment message and propose what each one enrols.

```text
arpenteur answers [--programme PROGRAMME] [--from-stage FROM_STAGE] [--limit LIMIT] [--dry-run]
```

- `--programme PROGRAMME`: the programme code, or [enrol] programme from arpenteur.toml
- `--from-stage FROM_STAGE`: the stage whose members are read (default contacted)
- `--limit LIMIT`: how many messages to read (default 200)
- `--dry-run`: report everything, suppress nothing

## arpenteur audience

A curated list: one chosen mailbox per firm, ranked, deduplicated.

```text
arpenteur audience {list,new,show,refresh,build,keep,drop,language,address,unpin,export} [REST ...] [--segment SEGMENT] [--canton CANTON] [--programme PROGRAMME] [--members-only] [--allow ALLOW] [--cooldown COOLDOWN] [--max-age MAX_AGE] [--size] [--per-canton PER_CANTON] [--dropped] [--reason REASON] [--why WHY] [--out OUT] [--dry-run]
```

- `{list,new,show,refresh,build,keep,drop,language,address,unpin,export}`: list: every list. new CODE "Name" --segment ...: start one. refresh CODE: check its addresses with DNS again. build CODE: choose one mailbox per firm. show CODE: what it keeps and drops. keep, drop CODE UID --why ...: pin a firm either way. language, address CODE UID VALUE: pin a firm's language or mailbox. unpin CODE UID: let the rules decide again. export CODE --out FILE: write it as CSV
- `REST ...`: the list's code first, then a firm's UID for a pin
- `--segment SEGMENT`: bands and their weights, e.g. tourism-office=1,mountain-transport=0.7
- `--canton CANTON`: comma separated
- `--programme PROGRAMME`: the programme the list feeds
- `--members-only`: only firms already on the programme
- `--allow ALLOW`: widen the defaults: unclear, hearsay, inferred, maybe, sole-traders
- `--cooldown COOLDOWN`: days a mailbox, firm or domain rests after a message (default 180)
- `--max-age MAX_AGE`: days a DNS verdict stays good enough (default 30)
- `--size`: let registered capital count
- `--per-canton PER_CANTON`: for new: the most firms kept in each canton, best score first
- `--dropped`: for show: the dropped firms too
- `--reason REASON`: for show: one drop reason only
- `--why WHY`: for keep and drop: the reason, required
- `--out OUT`: for export: the CSV to write
- `--dry-run`: for build and refresh: report, write nothing

## arpenteur bounces

Read delivery failures from the mailbox into the address verdicts.

```text
arpenteur bounces [--folder FOLDER] [--since YYYY-MM-DD] [--limit LIMIT] [--dry-run]
```

- `--folder FOLDER`: which folder to read (default INBOX)
- `--since YYYY-MM-DD`: only messages the server dates on or after this day
- `--limit LIMIT`: how many of the most recent messages to screen (default 500)
- `--dry-run`: report what would be recorded, write nothing

## arpenteur boundaries

Fetch the cantonal outlines from swisstopo (free, one-off).

```text
arpenteur boundaries [--canton CANTON] [--refresh] [--dry-run]
```

- `--canton CANTON`: comma separated cantons; blank takes the configured territory
- `--refresh`: take boundaries already held again
- `--dry-run`: say what would be fetched

## arpenteur campaign

A message plan over one list, sent in waves after a test and an approval.

```text
arpenteur campaign {list,strategies,new,show,plan,render,test,approve,send,pause,resume,results} [REST ...] [--audience AUDIENCE] [--templates TEMPLATES] [--programme PROGRAMME] [--strategy STRATEGY] [--pilot PILOT] [--wave-size WAVE_SIZE] [--gap GAP] [--start START] [--bounce-ceiling BOUNCE_CEILING] [--settle SETTLE] [--cooldown COOLDOWN] [--bilingual BILINGUAL] [--to TO] [--read] [--confirm CONFIRM] [--why WHY] [--yes] [--dry-run]
```

- `{list,strategies,new,show,plan,render,test,approve,send,pause,resume,results}`: list: every campaign. strategies: the ways waves can be cut. new CODE "Name" --audience LIST --templates DIR: start one. plan CODE: cut the list into waves. render, test, approve, send CODE N: walk wave N through its four steps, none skipped. show, results CODE: where it stands. pause, resume CODE --why: hold the next wave or release it
- `REST ...`: the campaign's code first, then a wave number
- `--audience AUDIENCE`: for new: the list's code
- `--templates TEMPLATES`: for new: the folder holding one message per language, e.g. templates/example
- `--programme PROGRAMME`: for new: the programme it moves
- `--strategy STRATEGY`: how waves are cut; `arpenteur campaign strategies` lists them
- `--pilot PILOT`: firms in the first wave
- `--wave-size WAVE_SIZE`: firms per wave
- `--gap GAP`: days between waves
- `--start START`: the first wave's date, YYYY-MM-DD
- `--bounce-ceiling BOUNCE_CEILING`: pause above this share of bounces, e.g. 0.05
- `--settle SETTLE`: hours bounces get to arrive before the next wave
- `--cooldown COOLDOWN`: days a mailbox, firm or domain rests after a message
- `--bilingual BILINGUAL`: for new and plan: cantons whose firms read every official language in one message, e.g. FR,VS; an empty value clears it
- `--to TO`: for test: where the test goes
- `--read`: for approve: you received and read every test message
- `--confirm CONFIRM`: for send: the wave typed back, CODE/N
- `--why WHY`: for pause and resume: the reason
- `--yes`: for test and send: actually post
- `--dry-run`: for plan: show the cut only

## arpenteur clients

The client portfolio, one line each.

```text
arpenteur clients
```

## arpenteur console

Serve the browser console on localhost.

```text
arpenteur console [--host HOST] [--port PORT]
```

- `--host HOST`: the address to listen on; anything but 127.0.0.1 exposes a console with no sign-in, so put a proxy with one in front (default 127.0.0.1)
- `--port PORT`: the port to listen on (default 8000)

## arpenteur docs

The client's documents, and the folders they are filed in.

```text
arpenteur docs CLIENT {add,list,folders,mkdir,file} [PATH ...] [--folder FOLDER] [--kind KIND] [--note NOTE]
```

- `CLIENT`: client code, UID or name
- `{add,list,folders,mkdir,file}`: store files, list what is held, list folders, make a folder, or move a document into one
- `PATH ...`: files for add; the folder name for mkdir; a document id and a folder name for file (default [])
- `--folder FOLDER`: file everything added into this folder, making it if needed
- `--kind KIND`: what it is: contract, accounts, report, policy, inventory, architecture, correspondence, tender, invoice, other
- `--note NOTE`: where it came from, for the model

## arpenteur doctor

Check everything a first run needs, and say what is missing.

```text
arpenteur doctor [--quick]
```

- `--quick`: read settings and files only; do not sign in to the mailbox or open a session with the mail server

## arpenteur dossier

Print everything held on a client, for reading or for an agent.

```text
arpenteur dossier CLIENT [--documents]
```

- `CLIENT`: client code, UID, or part of the name
- `--documents`: include the full extracted text of every document held

## arpenteur draft

Propose one message per firm, for review; sends nothing.

```text
arpenteur draft [--segment SEGMENT] [--canton CANTON] [--limit LIMIT] [--yes] [--dry-run]
```

- `--segment SEGMENT`: comma separated
- `--canton CANTON`: comma separated
- `--limit LIMIT`: how many to draft (default 25, the safe batch)
- `--yes`: acknowledge the batch size; see LCD art. 3 al. 1 let. o
- `--dry-run`: list what would be drafted, call no model

## arpenteur enrich

Find and read company websites (costs money per company).

```text
arpenteur enrich [--uid UID] [--canton CANTON] [--segment SEGMENT] [--sector SECTOR] [--limit LIMIT] [--refresh] [--dry-run]
```

- `--uid UID`: comma separated UIDs, e.g. CHE123...,CHE456...
- `--canton CANTON`: comma separated, e.g. VD,GE; narrows the shortlist
- `--segment SEGMENT`: only companies carrying this segment
- `--sector SECTOR`: only companies in this line of business
- `--limit LIMIT`: required for runs over 200 companies
- `--refresh`: re-research companies already done
- `--dry-run`: list the shortlist, spend nothing

## arpenteur geocode

Place companies on the map from the address the register gives (free).

```text
arpenteur geocode [--uid UID] [--canton CANTON] [--segment SEGMENT] [--sector SECTOR] [--band BAND] [--worked] [--limit LIMIT] [--dry-run]
```

- `--uid UID`: comma separated UIDs
- `--canton CANTON`: only companies in this canton
- `--segment SEGMENT`: only companies carrying this segment
- `--sector SECTOR`: only companies in this line of business
- `--band BAND`: only companies whose fit is in this band
- `--worked`: only companies with a pipeline state, which is the shortlist a map is read for
- `--limit LIMIT`: required for runs over 500 companies
- `--dry-run`: list the addresses, ask nothing

## arpenteur help

Read a page of the manual in the terminal.

```text
arpenteur help [TOPIC] [--list]
```

- `TOPIC`: the page, e.g. guide/companies or install; the index when left out (default index)
- `--list`: name every page and stop

## arpenteur hydrate

Fetch detail for companies missing it.

```text
arpenteur hydrate [--region REGION] [--canton CANTON] [--legal-form LEGAL_FORM] [--limit LIMIT] [--name-like NAME_LIKE] [--bulk] [--fixture]
```

- `--region REGION`: a name from [regions] in arpenteur.toml, e.g. romandie
- `--canton CANTON`: comma separated, e.g. VD,GE (overrides --region)
- `--legal-form LEGAL_FORM`: comma separated eCH-0097 uids, e.g. 0107,0106
- `--limit LIMIT`: stop after this many companies; the rest wait for the next run
- `--name-like NAME_LIKE`: only companies whose name matches this POSIX regex, which is how a national run stays inside the politeness budget. Word boundaries are \m and \M; \b is a backspace and matches nothing
- `--bulk`: accept a run over 2000 companies (Zefix asks users to avoid regular mass querying)
- `--fixture`: replay bundled details from the separate fixture database

## arpenteur import

Fetch one company by UID, inside the territory or not.

```text
arpenteur import UID [--client]
```

- `UID`: e.g. CHE-100.000.011 or CHE100000011
- `--client`: also open a client record for it

## arpenteur init-db

Create or migrate the database.

```text
arpenteur init-db [--fixture]
```

- `--fixture`: use the separate database reserved for recorded fixture data

## arpenteur legal-forms

The registry's legal form catalogue, with uid codes.

```text
arpenteur legal-forms
```

## arpenteur mail

Read the mailbox into the review queue (read-only, files nothing).

```text
arpenteur mail [--folder FOLDER] [--days DAYS] [--limit LIMIT] [--dry-run]
```

- `--folder FOLDER`: comma separated folders; blank reads all but Spam and Trash
- `--days DAYS`: how far back to look, in days (default 90)
- `--limit LIMIT`: stop after this many new messages
- `--dry-run`: list what would be queued, write nothing

## arpenteur match

Score companies against campaign.md, with approach advice.

```text
arpenteur match [--uid UID] [--segment SEGMENT] [--sector SECTOR] [--include-unresearched] [--limit LIMIT] [--rescore] [--dry-run]
```

- `--uid UID`: comma separated UIDs
- `--segment SEGMENT`: only companies carrying this segment
- `--sector SECTOR`: only companies in this line of business
- `--include-unresearched`: score on the register alone, without waiting for enrich
- `--limit LIMIT`: score at most this many companies; a run that is not a dry run and names no UIDs needs this, because each company is one model call
- `--rescore`: re-score companies already scored
- `--dry-run`: list the shortlist, spend nothing

## arpenteur modules

What is installed, and what it owns.

```text
arpenteur modules {list,drop} ...
```

### arpenteur modules drop

Delete a module's schema and all of its data.

```text
arpenteur modules drop NAME [--yes]
```

- `NAME`: the module to drop, e.g. sales; its schema and every row in it go
- `--yes`: required: this cannot be undone

### arpenteur modules list

Every module, its schema and its version.

```text
arpenteur modules list
```

## arpenteur note

Notes on a client, written in Markdown.

```text
arpenteur note CLIENT {add,list,show} [PATH] [--title TITLE] [--tag TAG] [--pin]
```

- `CLIENT`: client code, UID or name
- `{add,list,show}`: add a file, list what is held, or print one back
- `PATH`: the .md file for add, the note id for show
- `--title TITLE`: overrides the note's own first heading
- `--tag TAG`: comma separated
- `--pin`: keep it at the top

## arpenteur people

Officers from the register's SHAB notices.

```text
arpenteur people [--limit LIMIT] [--reparse] [--dry-run]
```

- `--limit LIMIT`: stop after this many companies; the rest wait for the next run
- `--reparse`: re-read companies that already have people (use after parser changes)
- `--dry-run`: print, write nothing

## arpenteur prep

Extract a client's vocabulary and what to revise before meeting them.

```text
arpenteur prep CLIENT [--list] [--dry-run]
```

- `CLIENT`: client code, UID, or part of the name
- `--list`: print what has already been extracted, without calling a model
- `--dry-run`: report what would be read and its size, spend nothing

## arpenteur profile

Sort companies into sectors and segments.

```text
arpenteur profile [--limit LIMIT] [--reclassify] [--dry-run] [--fixture]
```

- `--limit LIMIT`: stop after this many companies; the rest wait for the next run
- `--reclassify`: include already classified
- `--dry-run`: print, write nothing
- `--fixture`: read from the separate fixture database

## arpenteur programme

One endeavour across many companies, each member at its own stage.

```text
arpenteur programme {list,new,show,add,bring-in,move,say,stages,replies,ledger,history,answer} [REST ...] [--what WHAT] [--contact CONTACT] [--kind {note,bug,request,praise}] [--on ON] [--why WHY] [--sector SECTOR] [--segment SEGMENT] [--canton CANTON] [--band BAND] [--legal-form LEGAL_FORM] [--limit LIMIT] [--from-stage FROM_STAGE] [--to-stage TO_STAGE] [--yes] [--flag FLAG] [--stage STAGE] [--platform {,ios,android}] [--account ACCOUNT] [--window-ok] [--window-no]
```

- `{list,new,show,add,bring-in,move,say,stages,replies,ledger,history,answer}`: list them; start one; show one; add companies; add a shortlist from the survey; move a member; keep what a member said; read or rewrite the stages; list who has written back; read the status of every member; read one firm's mail; record what one answered
- `REST ...`: the programme code first, then what the action needs: a company and a stage for move, a company and the words for say (default [])
- `--what WHAT`: what the programme is, for new
- `--contact CONTACT`: who you deal with, for add
- `--kind {note,bug,request,praise}`: what sort of feedback, for say (default note)
- `--on ON`: when they said it, for say
- `--why WHY`: the reason for a move
- `--sector SECTOR`: for bring-in
- `--segment SEGMENT`: for bring-in
- `--canton CANTON`: comma separated, for bring-in
- `--band BAND`: fit band, for bring-in
- `--legal-form LEGAL_FORM`: comma separated eCH-0097 uids, for bring-in. Naming any excludes the rest, 0101 included: a sole trader is a named person, and writing to one carries nLPD obligations a company does not
- `--limit LIMIT`: how many, for bring-in (default 25)
- `--from-stage FROM_STAGE`: for replies: the stage to look at (default contacted)
- `--to-stage TO_STAGE`: for replies: where they move (default replied)
- `--yes`: for replies: apply the moves, having read the list
- `--flag FLAG`: for ledger: one of answered, silent, failed, refused, enrollable
- `--stage STAGE`: for ledger: one stage only
- `--platform {,ios,android}`: for answer: which store track the tester enrols on
- `--account ACCOUNT`: for answer: the tester's Google account, which Android needs
- `--window-ok`: for answer: the tester accepted the dated window
- `--window-no`: for answer: the tester declined the dated window

## arpenteur rates

The service catalogue and its prices.

```text
arpenteur rates {load,show}
```

- `{load,show}`: load the [[rates.service]] entries from arpenteur.toml into a new rate card, or print the card in force

## arpenteur reach

Read a company's own site for a published address (no model cost).

```text
arpenteur reach [--uid UID] [--segment SEGMENT] [--sector SECTOR] [--band BAND] [--limit LIMIT] [--refresh] [--dry-run]
```

- `--uid UID`: comma separated UIDs
- `--segment SEGMENT`: only companies carrying this segment
- `--sector SECTOR`: only companies in this line of business
- `--band BAND`: only companies whose fit is in this band
- `--limit LIMIT`: required for runs over 200 companies
- `--refresh`: re-read sites already looked at
- `--dry-run`: list the sites, fetch nothing

## arpenteur regions

Territories defined in arpenteur.toml, and the active one.

```text
arpenteur regions
```

## arpenteur report

The analytics views: the funnel, the spend, the pipeline.

```text
arpenteur report {list,show,refresh} [NAME] [--limit LIMIT] [--csv]
```

- `{list,show,refresh}`: list what this deployment has, print one, or bring the materialised views up to date
- `NAME`: which report, for show
- `--limit LIMIT`: rows to print (default 200)
- `--csv`: write CSV to stdout

## arpenteur resolve

Work out which officer rows are the same person (free, seconds).

```text
arpenteur resolve [--dry-run]
```

- `--dry-run`: say what it would do

## arpenteur send

Post an approved draft, or one message typed on the command line.

```text
arpenteur send [--draft ID] [--to TO] [--subject SUBJECT] [--body BODY] [--body-file BODY_FILE] [--yes] [--plain] [--programme PROGRAMME]
```

- `--draft ID`: an approved draft to post; repeatable (default [])
- `--to TO`: one recipient, for a one-off
- `--subject SUBJECT`: the subject, for a one-off
- `--body BODY`: the text, for a one-off
- `--body-file BODY_FILE`: read the text from this file; stdin when neither is given
- `--yes`: actually post, having read what a dry run listed
- `--plain`: send text only, without the formatted part
- `--programme PROGRAMME`: the programme whose card a sent draft moves; '' for none. Without it, the one [enrol] names, and the log says which

## arpenteur sendlist

One address per firm, desk address preferred, for a staged send.

```text
arpenteur sendlist [--segment SEGMENT] [--canton CANTON] [--status STATUS] [--out OUT] [--limit LIMIT]
```

- `--segment SEGMENT`: comma separated
- `--canton CANTON`: comma separated
- `--status STATUS`: which verdicts to accept (default deliverable; add maybe to widen)
- `--out OUT`: write a CSV here instead of printing
- `--limit LIMIT`: how many to print

## arpenteur settings

The operator settings held in the database.

```text
arpenteur settings {import-toml,show} [--force]
```

- `{import-toml,show}`: move the configuration tables from arpenteur.toml into the database, once, or print what it holds
- `--force`: overwrite a section the database already holds

## arpenteur source

Fill the company table from a register.

```text
arpenteur source [{zefix}] [--region REGION] [--canton CANTON] [--legal-form LEGAL_FORM] [--no-resume] [--no-hydrate] [--limit LIMIT] [--name NAME] [--name-file NAME_FILE] [--dry-run] [--bulk] [--fixture]
```

- `{zefix}`: the register to read; zefix is the only one (default zefix)
- `--region REGION`: a name from [regions] in arpenteur.toml, e.g. romandie
- `--canton CANTON`: comma separated, e.g. VD,GE (overrides --region)
- `--legal-form LEGAL_FORM`: comma separated eCH-0097 uids, e.g. 0107,0106
- `--no-resume`: re-sweep completed prefixes
- `--no-hydrate`: skip the detail pass
- `--limit LIMIT`: cap the detail pass
- `--name NAME`: comma separated name patterns, e.g. '*tourisme*,*bergbahn*'. The registry accepts a leading wildcard, so this finds a trade instead of sweeping a territory, across every legal form
- `--name-file NAME_FILE`: a file of name patterns, one per line, # for a comment
- `--dry-run`: with --name: search and count, write nothing
- `--bulk`: accept a detail pass over 2000+ companies
- `--fixture`: replay bundled VD/Sàrl responses without credentials or network

## arpenteur stats

What the survey holds.

```text
arpenteur stats [--fixture]
```

- `--fixture`: read from the separate fixture database

## arpenteur sync

Consume the FOSC change feed: one request per day, not per company.

```text
arpenteur sync [--region REGION] [--canton CANTON] [--legal-form LEGAL_FORM] [--since SINCE] [--until UNTIL] [--no-resume] [--no-admit] [--dry-run]
```

- `--region REGION`: a name from [regions] in arpenteur.toml, e.g. romandie
- `--canton CANTON`: comma separated, e.g. VD,GE (overrides --region)
- `--legal-form LEGAL_FORM`: comma separated eCH-0097 uids, e.g. 0107,0106
- `--since SINCE`: first FOSC date, YYYY-MM-DD (default: resume)
- `--until UNTIL`: last FOSC date, YYYY-MM-DD (default: today)
- `--no-resume`: re-read days already consumed
- `--no-admit`: do not add companies not already held, even inside the territory
- `--dry-run`: say what would be fetched

## arpenteur verify

Check every address as far as DNS can settle it, and record it.

```text
arpenteur verify [--refresh] [--stale-after DAYS] [--limit LIMIT] [--dry-run]
```

- `--refresh`: re-check every domain, not only the ones with a stale verdict
- `--stale-after DAYS`: how old a verdict may be before it is checked again (default 30)
- `--limit LIMIT`: stop after N domains
- `--dry-run`: list, resolve nothing

## arpenteur weights

What each part of a score is worth to this campaign.

```text
arpenteur weights {load,show}
```

- `{load,show}`: open a new version from [scoring.weights] in arpenteur.toml, or print the set in force

## arpenteur workspace

Carry this installation's data to another machine, as one file.

```text
arpenteur workspace {export,inspect,import} [PATH ...] [--out OUT] [--with-secrets] [--no-attachments] [--note NOTE] [--force]
```

- `{export,inspect,import}`: write a workspace file, read its manifest, or restore from it
- `PATH ...`: the file, for inspect and import (default [])
- `--out OUT`: where export writes; defaults to the date
- `--with-secrets`: include .env, which holds the Zefix and Infomaniak passwords
- `--no-attachments`: leave out the documents clients have sent
- `--note NOTE`: a line recorded in the manifest
- `--force`: let import replace a database that already holds data
