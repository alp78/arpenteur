"""Starting a stage from the console, and watching it work.

The console previously refused to start anything, on the grounds that a web
form spending four searches a company is too easy to submit twice. That
reasoning was about the money, not about the button, so what is here is the
button plus the guards that made the command line safe:

  one at a time      A second start while one is running is refused. Two
                     concurrent enrich runs would double the spend and
                     interleave their writes for no benefit.
  a declared scope   The paid stages will not start without an explicit
                     limit. The form offers one and the server checks it,
                     because a disabled field is not a guard.
  dry run first      Every stage that supports it exposes it, and it is the
                     default selection in the form.
  the stage's own    The cost refusals inside the stages are untouched and
                     still apply. This adds guards, it does not replace them.

Each run gets a thread and its own SQLite connection: connections are not
shareable across threads, and the console must keep answering while a stage
works. The stage's `log` output is written to `run_line`, which is what the
progress view reads.
"""

from __future__ import annotations

import psycopg
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import db
from ..config import Settings
from ..kernel.db import Connection
from ..stages import (
    audience, boundaries, bounces, campaign, enrich, geocode, mail, match,
    people, profile, reach, resolve, source, sync,
)


@dataclass(frozen=True)
class Field:
    """One control on the operations page, and how it reaches the stage.

    The metadata here is not decoration. A field called `uid` with no example
    is unusable by anyone who has not read the source: it does not say what a
    UID looks like, where to get one, or whether several are allowed. So every
    field carries where the value comes from and what it looks like, and
    anything with a knowable set of values is a list to pick from rather than
    a box to type in.
    """

    name: str
    label: str
    # text | int | bool | date | select
    kind: str = "text"
    # For select: either literal choices, or the name of a set the console
    # fills from the database (cantons, segments, bands, limits, sizes).
    choices: tuple[tuple[str, str], ...] = ()
    source: str = ""
    help: str = ""
    example: str = ""
    where: str = ""
    default: str = ""
    # The stage's keyword, when the control is not named after it. `canton`
    # reads better on a form than `cantons`, and the stage should not have to
    # care what a form called its input.
    param: str = ""
    # For a checkbox that reads better as the opposite of the stage's flag:
    # "Names only, skip the detail" is `hydrate=False`.
    invert: bool = False
    # Several values allowed. Reaches the stage as a list.
    multi: bool = False


@dataclass(frozen=True)
class Stage:
    """One step of the pipeline, and everything the page needs to explain it."""

    name: str
    title: str
    # What it does, in a sentence anyone can read.
    what: str
    call: Callable[..., dict[str, Any]] = None  # type: ignore[assignment]
    fields: tuple[Field, ...] = ()
    # What must already have happened. Shown so an empty result reads as
    # "nothing to do yet" rather than as a broken stage.
    needs: str = ""
    # What appears in the database afterwards, named in the words the rest of
    # the console uses for it.
    produces: str = ""
    # What it spends. "Costs money per company" is the one thing an operator
    # must not have to remember.
    cost: str = ""
    # The button, and exactly what pressing it does.
    button: str = ""
    button_does: str = ""
    # How many are done and how many are waiting, as two SQL counts, with the
    # word for what is being counted. Most stages count companies; `sync`
    # counts days of gazette, and labelling its backlog "companies waiting"
    # would report the hydration queue as if the gazette were behind.
    done_sql: str = ""
    waiting_sql: str = ""
    done_label: str = "done"
    waiting_label: str = "waiting"
    # Paid stages must be given a size before they start.
    needs_limit: bool = False
    # Most stages return a stats dict. `hydrate` returns a bare count, and
    # without this the runner recorded `{}` for it: a finished run whose
    # result line said nothing at all, which is worse than no view.
    result_key: str = ""
    # Which module owns it. A stage whose module is not enabled is not offered:
    # its tables do not exist, and a button that can only fail is worse than an
    # absent one.
    module: str = "scout"
    # Offered on the operations page and startable by its generic route. The
    # campaign stages are not: they are started from a wave's own page, after
    # the checks that page makes, and a form posted anywhere else is refused.
    listed: bool = True
    # Runs hold one lock per lane. A send must never wait an hour behind an
    # enrich run, and two sends must never overlap.
    lane: str = "pipeline"
    # Called with the settings and the posted form before any thread starts,
    # so a refusal reaches the person as a sentence on the page they came from
    # rather than as a failed run in the history.
    precheck: Callable[[Settings, dict[str, Any]], None] | None = None


# --- the controls shared between stages ---------------------------------------

SIZES = Field(
    "limit", "How many companies", "select", source="limits",
    help="Start small. You can run it again; every stage picks up where it left off.",
)

DRY = Field(
    "dry_run", "Dry run", "bool", default="1",
    help="List what would be processed and stop. Nothing is written and nothing is spent.",
)

# `profile` is the exception and it is the expensive kind of exception. Its
# dry run still calls the model for every batch; what it skips is the write.
# An operator reading "spend nothing" and ticking it over sixty thousand
# companies would be billed for the entire classification.
DRY_PROFILE = Field(
    "dry_run", "Dry run", "bool", default="1",
    help="Show the labels each company would get, without saving them. "
         "This still calls the model, so it still costs: it saves the writing, not the money.",
)

CANTON = Field(
    "canton", "Canton", "select", source="cantons",
    help="Restrict to one canton. Leave on 'All cantons' for the whole territory.",
)

SEGMENT = Field(
    "segment", "Segment", "select", source="segments",
    help="Only companies carrying this label. Segments come from profile, "
         "and are defined in arpenteur.toml.",
)

SECTOR = Field(
    "sector", "Sector", "select", source="sectors",
    help="Only companies in this line of business. One per company, set by "
         "profile from the registered purpose, and correctable on any company "
         "page.",
)

BAND = Field(
    "band", "Fit band", "select", source="bands",
    help="Only companies whose match score falls in this band.",
)

UIDS = Field(
    "uids", "Specific companies", "text",
    example="CHE164075515, CHE112691919",
    where="Copy the UID from any company page in this console, or from zefix.ch. "
          "Separate several with commas.",
    help="Runs on exactly these companies and ignores the filters above.",
)


STAGES: dict[str, Stage] = {
    "source": Stage(
        name="source",
        title="Sweep the register",
        what="Ask the commercial register for every company in the chosen cantons "
             "and legal forms, and store their names. This is where the survey "
             "comes from: nothing below can run on a company this has not found.",
        call=source.run_zefix,
        fields=(
            Field("canton", "Cantons", "select", source="cantons_multi",
                  param="cantons", multi=True,
                  help="Hold Ctrl (Cmd on a Mac) to pick several. "
                       "Leave nothing selected to use the configured territory."),
            Field("legal_form", "Legal forms", "select", source="forms_multi",
                  param="legal_forms", multi=True,
                  help="Which kinds of entity to look for.",
                  where="Codes come from the register's own catalogue, kept "
                        "current in Settings. A form that is not selected is "
                        "never asked for, in any canton."),
            Field("no_hydrate", "Names only, skip the detail", "bool", default="1",
                  param="hydrate", invert=True,
                  help="Recommended. The sweep is a few hundred requests; "
                       "fetching detail is one per company and belongs in its "
                       "own run where you can size it."),
            Field("no_resume", "Start again from the beginning", "bool",
                  param="resume", invert=True,
                  help="Off by default: a sweep is resumable and re-running "
                       "skips the name prefixes already covered."),
        ),
        needs="Zefix credentials, which are free. Nothing else.",
        produces="Company names, legal forms and seats. Everything else comes "
                 "from Fetch company detail.",
        cost="A few hundred register requests per canton and form. Free, and "
             "minutes rather than hours.",
        button="Sweep the register",
        button_does="Walks the alphabet for each canton and legal form chosen, "
                    "storing every company it finds. Safe to re-run: it skips "
                    "what it has already covered.",
        done_sql="SELECT COUNT(*) FROM core.company",
        waiting_sql="SELECT 0",
        done_label="companies found",
        waiting_label="",
    ),
    "sync": Stage(
        name="sync",
        title="Sync the register",
        what="Read the official gazette (FOSC/SHAB) day by day and mark every company "
             "whose entry changed, so the local copy stops drifting from the register.",
        call=sync.run,
        fields=(
            Field("since", "From date", "date",
                  where="Leave blank to carry on from the last day already read.",
                  help="First gazette day to read."),
            Field("until", "To date", "date",
                  where="Leave blank for today.", help="Last gazette day to read."),
            DRY,
        ),
        needs="Nothing. This is the stage that keeps everything else current.",
        produces="Companies marked as changed, ready for Fetch detail to re-read.",
        cost="One request per day of gazette. Free, and a few seconds a day.",
        button="Read the gazette",
        button_does="Fetches each day's notices and flags the companies they mention. "
                    "It does not re-fetch the companies themselves; that is Fetch detail.",
        done_sql="SELECT COUNT(*) FROM intel.sync_day",
        # Days of gazette not yet read, which is what being behind means here.
        # The count of companies marked as changed belongs to Fetch detail,
        # and showing it against this stage said the gazette was behind when
        # what was behind was the re-reading.
        waiting_sql="SELECT GREATEST(0, current_date "
                    "- COALESCE((SELECT MAX(sogc_date) FROM intel.sync_day), "
                    "current_date))",
        done_label="days read",
        waiting_label="days behind",
    ),
    "hydrate": Stage(
        name="hydrate",
        title="Fetch company detail",
        what="Ask the register for each company's full entry: its purpose, capital, "
             "address, officers and status. The sweep only collects names, so nothing "
             "else can be done until this has run.",
        call=source.hydrate_pending,
        fields=(
            SIZES,
            Field("bulk", "Allow a very large run", "bool",
                  help="Required above 2000 companies. The register asks users to avoid "
                       "regular mass querying, so a run that size has to be deliberate."),
        ),
        needs="Sweep the register, above.",
        produces="Purpose text, capital, address and gazette notices for each company.",
        cost="One register request per company. Free, but paced at about two a second, "
             "so ten thousand companies is roughly ninety minutes.",
        button="Fetch detail",
        button_does="Works through the companies that have no detail yet, plus any the "
                    "gazette marked as changed, starting with the changed ones.",
        done_sql="SELECT COUNT(*) FROM core.company WHERE hydrated_at IS NOT NULL",
        waiting_sql="SELECT COUNT(*) FROM core.company "
                    "WHERE hydrated_at IS NULL OR stale_since IS NOT NULL",
        result_key="hydrated",
    ),
    "people": Stage(
        name="people",
        title="Extract officers",
        what="Read the gazette notices already stored and pull out the named officers: "
             "who manages the company, in what role, with what signature rights.",
        call=people.run,
        fields=(
            SIZES,
            Field("reparse", "Re-read companies that already have officers", "bool",
                  help="Use after the parser has been improved. Otherwise only "
                       "companies with notices and no officers yet are read."),
            DRY,
        ),
        needs="Fetch company detail, which is what brings the notices in.",
        produces="Named officers on the company page.",
        cost="Nothing at all. No network, no model: it re-reads text already stored.",
        button="Extract officers",
        button_does="Parses stored notices into officer records. Safe to run any time.",
        done_sql="SELECT COUNT(DISTINCT company_id) FROM core.person",
        waiting_sql="SELECT COUNT(*) FROM core.company c WHERE EXISTS "
                    "(SELECT 1 FROM core.publication p WHERE p.company_id = c.id) "
                    "AND NOT EXISTS (SELECT 1 FROM core.person r WHERE r.company_id = c.id)",
    ),
    "profile": Stage(
        name="profile",
        title="Sort into segments",
        what="Read each company's registered purpose and say two things about it: which "
             "line of business it is in, one from your sector list, and which of your "
             "segments it fits, any number of them. Both from the same call, so the "
             "survey can be sliced by what a company does as well as by canton.",
        call=profile.run,
        fields=(
            SIZES,
            Field("reclassify", "Re-label companies already classified", "bool",
                  help="Use after editing the sector or segment definitions in "
                       "Settings. Labels you set by hand are never overwritten."),
            DRY_PROFILE,
        ),
        needs="Fetch company detail. The purpose text only arrives with it.",
        produces="A sector and any number of segment labels, which then filter "
                 "every stage below.",
        cost="A cheap model call per batch of twenty companies. Roughly a franc "
             "per few thousand companies.",
        button="Sort into segments",
        button_does="Labels companies that have a purpose and have not been "
                    "classified yet. Companies in liquidation are skipped.",
        # Counted on the sector rather than on segments, because that is the
        # mark the stage leaves on every company it has looked at. A company it
        # found nothing to label has no segment rows, and counting those as
        # untouched is what made the queue never empty.
        done_sql="SELECT COUNT(*) FROM intel.sector",
        waiting_sql="SELECT COUNT(*) FROM core.company c WHERE c.purpose IS NOT NULL "
                    "AND c.status = 'ACTIVE' AND NOT EXISTS "
                    "(SELECT 1 FROM intel.sector x WHERE x.company_id = c.id)",
    ),
    "enrich": Stage(
        name="enrich",
        title="Research websites",
        what="Search for each company's own website, read it, and record what it sells, "
             "what it runs on, who it partners with, and anything that argues against "
             "approaching it.",
        call=enrich.run,
        fields=(CANTON, SECTOR, SEGMENT, UIDS, SIZES,
                Field("refresh", "Research again", "bool",
                      help="Re-read companies already researched. Their previous "
                           "reading is replaced, and their fit score is re-taken."),
                DRY),
        needs="Sort into segments, so there is something to narrow by.",
        produces="Website, summary, signals and any published email address.",
        # Measured, not estimated. One company on 12.08.2026: 465'812 input
        # tokens, 7'106 output, 4 searches, 3 fetches, 45 minutes, and it
        # found nothing. The earlier guess of forty centimes came from a
        # single half-remembered figure and was out by three and a half times.
        cost="THE EXPENSIVE ONE. About 1 franc and 7 to 45 minutes per company, "
             "measured. A hundred companies is upwards of a hundred francs and "
             "can be a full day. Searching hard and finding nothing costs the most.",
        button="Research websites",
        button_does="Searches the web for each company, opens what it finds, and stores "
                    "one reading per company. Will not start without a size or a dry run.",
        done_sql="SELECT COUNT(*) FROM intel.site",
        waiting_sql="SELECT COUNT(*) FROM core.company c WHERE c.status = 'ACTIVE' "
                    "AND EXISTS (SELECT 1 FROM intel.segment s WHERE s.company_id = c.id) "
                    "AND NOT EXISTS (SELECT 1 FROM intel.site w WHERE w.company_id = c.id)",
        needs_limit=True,
    ),
    "match": Stage(
        name="match",
        title="Score against the campaign",
        what="Read campaign.md and each company's file together, and answer three "
             "questions: how good a fit, why, and what you would actually say first.",
        call=match.run,
        fields=(
            SECTOR, SEGMENT, UIDS, SIZES,
            Field("researched_only", "Only companies with website research", "bool",
                  default="1",
                  help="Turn off to score on the register entry alone. Weaker, but it "
                       "costs nothing to research and is how you choose what to research."),
            Field("rescore", "Score again", "bool",
                  help="Not usually needed: a score expires by itself when campaign.md "
                       "is rewritten or the research is re-run."),
            DRY,
        ),
        needs="A written campaign.md. Website research is optional but makes it far better.",
        produces="A 0-100 score, a band, the reasoning, and the opening line.",
        cost="One model call per company. A few centimes each.",
        button="Score companies",
        button_does="Scores companies not yet scored against the current campaign brief. "
                    "Scores you set by hand are never touched.",
        done_sql="SELECT COUNT(*) FROM intel.fit",
        waiting_sql="SELECT COUNT(*) FROM intel.site w JOIN core.company c ON c.id = w.company_id "
                    "WHERE c.status = 'ACTIVE' AND NOT EXISTS "
                    "(SELECT 1 FROM intel.fit f WHERE f.company_id = w.company_id)",
    ),
    "reach": Stage(
        name="reach",
        title="Find an address",
        what="Open the websites already found and take the email addresses the company "
             "publishes on them, recording which page each one came from.",
        call=reach.run,
        fields=(SECTOR, SEGMENT, BAND, UIDS, SIZES,
                Field("refresh", "Look again", "bool",
                      help="Re-read sites already looked at. A site that published "
                           "nothing last time usually still publishes nothing."),
                DRY),
        needs="Research websites. There has to be a site to open.",
        produces="Email addresses, each with the page that published it.",
        cost="Free. A handful of ordinary web requests per company, no model.",
        button="Find addresses",
        button_does="Reads each company's home page and its contact pages, honouring "
                    "robots.txt, and stores any address published there.",
        done_sql="SELECT COUNT(*) FROM core.contact WHERE kind = 'email'",
        waiting_sql="SELECT COUNT(*) FROM intel.site WHERE status = 'found' "
                    "AND url IS NOT NULL AND url <> '' AND reached_at IS NULL",
    ),
    "resolve": Stage(
        name="resolve",
        title="Resolve the people",
        what="Work out which officer rows are the same human, and record the "
             "boards each of them sits on. This is what makes 'who else does "
             "this director sit with' a question the survey can answer.",
        call=resolve.run,
        fields=(DRY,),
        needs="Officers, from the people stage. Nothing else.",
        produces="One party per person, and an edge per role, each carrying how "
                 "sure the identity is and what it was decided on.",
        cost="Free. No request and no model: a few thousand local rows, seconds.",
        button="Resolve",
        button_does="Rebuilds the party table from the officer rows. The rule "
                    "splits when unsure, because a person wrongly merged is a "
                    "false statement about a named individual.",
        done_sql="SELECT COUNT(*) FROM core.party",
        waiting_sql="SELECT GREATEST(0, (SELECT COUNT(*) FROM core.person)"
                    " - (SELECT COUNT(*) FROM core.party_role))",
        done_label="people",
        waiting_label="unresolved",
    ),
    "geocode": Stage(
        name="geocode",
        title="Put them on the map",
        what="Turn the postal address the register gives into a coordinate, through "
             "the Confederation's own address search, so companies and clients can "
             "be seen on a map and asked about by distance.",
        call=geocode.run,
        fields=(CANTON, SECTOR, SEGMENT, BAND, UIDS,
                Field("worked", "Only what is on the board", "bool",
                      help="The companies with a pipeline state. This is the "
                           "shortlist a map is actually read for, and it is a "
                           "few dozen requests rather than a few thousand."),
                SIZES, DRY),
        needs="Fetched detail. An address only arrives with hydration, and 3'690 "
              "of the companies held carry one.",
        produces="An establishment with a point, and the precision of the match: "
                 "a door, a street, or merely the right town.",
        cost="Free. One request per company to api3.geo.admin.ch, no model, no key.",
        button="Geocode",
        button_does="Records each company's registered address and asks swisstopo "
                    "where it is. An address that resolves to nothing is left "
                    "without a point rather than guessed at.",
        done_sql="SELECT COUNT(*) FROM geo.establishment WHERE point IS NOT NULL",
        waiting_sql="SELECT COUNT(*) FROM core.company c WHERE c.address_json IS NOT NULL"
                    " AND NOT EXISTS (SELECT 1 FROM geo.establishment e"
                    " WHERE e.company_id = c.id AND e.is_seat AND e.point IS NOT NULL)",
        done_label="placed",
        module="geo",
    ),
    "boundaries": Stage(
        name="boundaries",
        title="Draw the cantons",
        what="Fetch the cantonal boundaries from swisstopo's own "
             "swissBOUNDARIES3D, so the map shows the territory and a point "
             "can be asked which canton it is actually in.",
        call=boundaries.run,
        fields=(
            Field("canton", "Cantons", "select", source="cantons_multi",
                  param="cantons", multi=True,
                  help="Leave nothing selected to take the configured "
                       "territory. There is no use fetching Appenzell's "
                       "outline for a survey of Romandie."),
            Field("refresh", "Take them again", "bool",
                  help="Boundaries change when a commune merges, which is a "
                       "few times a year and never on the outer cantonal "
                       "ring. Held ones are skipped unless this is ticked."),
            DRY,
        ),
        needs="Nothing. It reads a public federal layer and no company data.",
        produces="One outline per canton, and the map draws the territory "
                 "instead of implying it.",
        cost="Free. One request per canton to a public swisstopo layer, and "
             "the answer is cached on disk.",
        button="Fetch boundaries",
        button_does="Asks swisstopo for each canton's outline and checks the "
                    "abbreviation in the reply against the one asked for, so "
                    "a wrong number cannot file one canton under another.",
        done_sql="SELECT COUNT(*) FROM geo.region WHERE kind = 'canton'"
                 " AND shape IS NOT NULL",
        done_label="drawn",
        waiting_label="to fetch",
        module="geo",
    ),
    "mail": Stage(
        name="mail",
        title="Read the mailbox",
        what="Import correspondence from the mail server, match it to a company "
             "by address or domain, and hold it for review. Nothing is filed "
             "into a client's timeline here, and nothing in the mailbox is "
             "marked read, moved or deleted.",
        call=mail.run,
        fields=(
            Field("days", "How far back", "int", default=str(mail.DEFAULT_DAYS),
                  help="Days of mail to look at. The server compares whole "
                       "days, so this is a way of not asking for ten years "
                       "rather than a precise cursor.",
                  example="90"),
            Field("folders", "Folders", "text", multi=True,
                  help="Blank reads everything except Spam, Trash, Drafts and "
                       "the promotional folders, which are what somebody has "
                       "already decided against.",
                  example="INBOX"),
            SIZES, DRY,
        ),
        needs="A mailbox in .env. Most Swiss hosts issue one password per "
              "application and refuse the account password over IMAP.",
        produces="Messages waiting on the Mail page, each with the company it "
                 "was matched to and the rule that matched it.",
        cost="Free. Your own mail server, one session per run.",
        button="Read mail",
        button_does="Fetches envelopes read-only. The text of a message is "
                    "downloaded only when it matched a company, or later when "
                    "you file it by hand.",
        done_sql="SELECT COUNT(*) FROM crm.email_message WHERE state = 'filed'",
        waiting_sql="SELECT COUNT(*) FROM crm.email_message WHERE state = 'pending'",
        done_label="filed",
        waiting_label="to review",
        module="clients",
    ),
    "bounces": Stage(
        name="bounces",
        title="Read delivery failures",
        what="Read the mailbox for delivery reports and STOP replies, and record "
             "what each settles: a bounced mailbox, a refusal. A campaign's next "
             "wave waits until this has run after the last one.",
        call=bounces.run,
        fields=(
            Field("folder", "Folder", "text", default="INBOX",
                  help="Where delivery reports arrive.", example="INBOX"),
            DRY,
        ),
        needs="A mailbox in .env.",
        produces="Bounced and refused verdicts on the addresses, which every "
                 "list and every send then honours.",
        cost="Free. Your own mail server, read-only.",
        button="Read failures",
        button_does="Fetches envelopes read-only and opens only the reports.",
        module="clients",
    ),
    "audience-verify": Stage(
        name="audience-verify",
        title="Refresh a list's addresses",
        what="Resolve again the domains one curated list relies on whose verdict "
             "is missing or older than the list allows.",
        call=audience.refresh,
        fields=(Field("audience", "List", "text"), DRY),
        cost="Free. One DNS lookup per domain.",
        module="clients",
        listed=False,
        lane="send",
    ),
    "campaign-test": Stage(
        name="campaign-test",
        title="Test a wave",
        what="Send the operator one message per variant of a wave, marked "
             "[TEST], exactly as recipients will receive it.",
        call=campaign.test_wave,
        fields=(Field("campaign", "Campaign", "text"), Field("wave", "Wave", "int")),
        cost="Counts against the daily quota.",
        module="clients",
        listed=False,
        lane="send",
        precheck=campaign.precheck_test,
    ),
    "campaign-send": Stage(
        name="campaign-send",
        title="Send a wave",
        what="Post an approved wave's messages, paused between each, under every "
             "guard the send stage applies.",
        call=campaign.send_wave,
        fields=(Field("campaign", "Campaign", "text"), Field("wave", "Wave", "int"),
                Field("confirm", "Typed confirmation", "text")),
        cost="Counts against the daily quota.",
        module="clients",
        listed=False,
        lane="send",
        precheck=campaign.precheck_send,
    ),
}

# The stages that hand anything to an SMTP server. A test asserts nothing else
# in the registry can.
TRANSMITTING_STAGES = frozenset({"campaign-test", "campaign-send"})

# The order the operations page shows them in, which is the order they run in.
ORDER = ("source", "sync", "hydrate", "people", "resolve", "profile", "enrich",
         "match", "reach", "geocode", "boundaries", "mail")

# Sizes offered instead of a free number box. "No limit" is last and named
# rather than blank, so choosing it is a decision rather than an oversight.
LIMIT_CHOICES: tuple[tuple[str, str], ...] = (
    ("10", "10 companies"),
    ("25", "25 companies"),
    ("50", "50 companies"),
    ("100", "100 companies"),
    ("250", "250 companies"),
    ("500", "500 companies"),
    ("1000", "1000 companies"),
    ("", "No limit, run the whole queue"),
)

BAND_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "Any band"),
    ("high", "High"),
    ("medium", "Medium"),
    ("low", "Low"),
    ("excluded", "Excluded"),
)


def options_for(conn: Connection, settings: Settings) -> dict[str, tuple]:
    """The contents of every dropdown, read from the survey as it stands.

    Live rather than hardcoded: a canton list typed into the source drifts the
    first time the territory changes, and a segment list that does not match
    arpenteur.toml offers filters that select nothing.
    """
    cantons = [
        (row[0], row[0])
        for row in conn.execute(
            "SELECT DISTINCT canton FROM core.company WHERE canton IS NOT NULL ORDER BY canton"
        )
    ]
    segments = [(name, name) for name in sorted(settings.segments)]
    sectors = [(name, name) for name in sorted(settings.sectors)]
    # Every Swiss canton, not only the ones already swept: this list is what a
    # sweep is aimed at, and offering only cantons already in the database
    # would make it impossible to add one.
    all_cantons = [
        (code, code) for code in (
            "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU",
            "LU", "NE", "NW", "OW", "SG", "SH", "SO", "SZ", "TG", "TI", "UR",
            "VD", "VS", "ZG", "ZH",
        )
    ]
    forms = [
        (str(f.get("uid")), f"{f.get('uid')}  {f.get('label') or f.get('name') or ''}")
        for f in db.catalogue(conn)
        if f.get("uid")
    ]
    if not forms:
        # Before the catalogue has been fetched, offer what the territory is
        # already set to rather than an empty list that looks broken.
        forms = [(code, code) for code in settings.territory.legal_forms]
    return {
        "cantons": (("", "All cantons"), *cantons),
        "cantons_multi": tuple(all_cantons),
        "forms_multi": tuple(forms),
        "segments": (("", "All segments"), *segments),
        "sectors": (("", "All sectors"), *sectors),
        "bands": BAND_CHOICES,
        "limits": LIMIT_CHOICES,
    }


def status_of(conn: Connection, stage: Stage) -> dict[str, int]:
    """How much this stage has done and how much is queued for it.

    The number that turns a form into an instrument: "3 done, 41'238 waiting"
    says what to press next, where a bare button says nothing.
    """
    def count(sql: str) -> int:
        if not sql:
            return 0
        try:
            return int(next(iter(conn.execute(sql).fetchone().values())))
        except psycopg.Error:
            # A failed statement leaves the transaction poisoned, and every
            # later count on this connection would then fail too: one absent
            # table would report the whole pipeline as empty rather than the
            # one stage that cannot answer.
            conn.rollback()
            return 0

    return {"done": count(stage.done_sql), "waiting": count(stage.waiting_sql)}


def coerce(stage: Stage, form: dict[str, Any]) -> dict[str, Any]:
    """Form strings to the keyword arguments the stage actually takes.

    Blank means "not given" rather than empty string, so an untouched field
    leaves the stage's own default in place instead of overriding it with
    something falsy that means something else.
    """
    kwargs: dict[str, Any] = {}
    for field_ in stage.fields:
        key = field_.param or field_.name
        raw = form.get(field_.name)
        if field_.kind == "bool":
            ticked = bool(raw)
            kwargs[key] = (not ticked) if field_.invert else ticked
            continue
        if field_.multi:
            # A multi-select posts one value per selection. Nothing selected
            # means "whatever the territory says", not "no cantons at all",
            # which would sweep nothing and look like a broken run.
            chosen = form.get(field_.name + "[]") or raw
            values = chosen if isinstance(chosen, list) else (
                [chosen] if chosen else []
            )
            values = [str(v).strip() for v in values if str(v).strip()]
            if values:
                kwargs[key] = values
            continue
        if raw is None or str(raw).strip() == "":
            continue
        value = str(raw).strip()
        # `limit` reaches the stage as a number whether it came from a list of
        # sizes or a typed box, so the control can change without the stages
        # learning about it.
        if field_.kind == "int" or field_.name == "limit":
            try:
                kwargs[key] = int(value)
            except ValueError as exc:
                raise ValueError(f"{field_.label} must be a whole number") from exc
        elif field_.name == "uids":
            kwargs["uids"] = [part.strip() for part in value.split(",") if part.strip()]
        else:
            kwargs[key] = value
    return kwargs


def check(stage: Stage, kwargs: dict[str, Any]) -> None:
    """The guards that made the command line safe, restated for a form.

    Checked here rather than in the markup: a required attribute and a
    disabled button are conveniences for the person, not constraints on the
    request.
    """
    if stage.needs_limit and not kwargs.get("dry_run") and not kwargs.get("uids"):
        if not kwargs.get("limit"):
            raise ValueError(
                f"{stage.name} costs {stage.cost}. Give it a limit, name the "
                "companies, or run it dry first."
            )


def close_orphans(conn: Connection) -> int:
    """Settle console runs whose thread died with the process. Returns how many.

    A run is a thread inside the console, so stopping the console kills it
    mid-flight and leaves a row that says "running" with nothing running. The
    progress view would poll it forever and the history would be a lie.

    Only console runs. A `run` row from the command line may belong to a
    process still working in another terminal, and marking that interrupted
    would be inventing a fact about someone else's job.
    """
    return db.close_orphans(conn, "console")


@dataclass
class Runner:
    """One console's worth of running: one lock, one run per lane at a time."""

    settings: Settings
    lock: threading.Lock = field(default_factory=threading.Lock)
    # The run each lane holds, and its thread. `pipeline` is every stage the
    # operations page offers; `send` is a campaign's test and send, so a
    # wave never waits behind an enrich run and two sends never overlap.
    current: dict[str, int] = field(default_factory=dict)
    threads: dict[str, threading.Thread] = field(default_factory=dict)

    def _settle(self) -> None:
        """Forget runs whose thread has ended. Called with the lock held."""
        for lane, thread in list(self.threads.items()):
            if not thread.is_alive():
                self.threads.pop(lane, None)
                self.current.pop(lane, None)

    def running(self, lane: str = "pipeline") -> int | None:
        with self.lock:
            self._settle()
            return self.current.get(lane)

    def is_live(self, run_id: int) -> bool:
        """Whether this console is running that run right now."""
        with self.lock:
            self._settle()
            return run_id in self.current.values()

    def start(self, name: str, form: dict[str, Any], *, by: str = "console",
              internal: bool = False) -> int:
        stage = STAGES.get(name)
        if stage is None:
            raise ValueError(f"unknown stage {name!r}")
        if not stage.listed and not internal:
            raise ValueError(
                f"{stage.title} is started from its own page, after the checks "
                "that page makes, and not from here."
            )
        kwargs = coerce(stage, form)
        check(stage, kwargs)
        if stage.precheck is not None:
            stage.precheck(self.settings, dict(form))

        with self.lock:
            self._settle()
            busy = self.current.get(stage.lane)
            if busy is not None:
                raise RuntimeError(
                    f"Run {busy} is still going. One at a time: two runs of "
                    "the same stage would spend twice and interleave their writes."
                )
            conn = db.open_db(self.settings.database_url)
            try:
                run_id = db.start_run(conn, name, dict(form), started_by=by)
            finally:
                conn.close()
            self.current[stage.lane] = run_id
            thread = threading.Thread(
                target=self._work, args=(stage, run_id, kwargs), daemon=True,
                name=f"arpenteur-{name}-{run_id}",
            )
            self.threads[stage.lane] = thread
            thread.start()
            return run_id

    def _work(self, stage: Stage, run_id: int, kwargs: dict[str, Any]) -> None:
        # Its own connection: sqlite3 objects belong to the thread that made
        # them, and the console must keep answering while this works.
        # Read once: the console rebinds its settings when they are saved, and
        # one run sees one object from its first line to its last.
        settings = self.settings
        conn = db.open_db(settings.database_url)

        def log(message: str) -> None:
            # Committed line by line. A run that dies at company 4'000 should
            # leave four thousand lines explaining how it got there, not an
            # empty log and a traceback.
            db.append_line(conn, run_id, str(message))
            conn.commit()

        try:
            log(f"{stage.title}: {stage.what}")
            if kwargs:
                log("  " + ", ".join(f"{k}={v}" for k, v in sorted(kwargs.items())))
            result = stage.call(settings, conn, log=log, **kwargs)
            stats = (
                result
                if isinstance(result, dict)
                else {stage.result_key or "result": result}
            )
            # The log always ends with what happened. A stage whose progress
            # lines are pitched for sixty thousand companies says nothing at
            # all on a run of five, and a finished view that reports nothing
            # is worse than no view.
            log("done: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
            db.finish_run(conn, run_id, stats)
        except Exception as exc:
            # A failed statement leaves the transaction aborted, and every
            # statement after it fails too, the log line included. Rolled back
            # first, or the reason a send stopped would be lost with it.
            try:
                conn.rollback()
            except psycopg.Error:
                pass
            # The message goes to both places on purpose: the log is what the
            # watcher is reading, the run row is what the history shows.
            try:
                log(f"! {exc}")
                log(traceback.format_exc().strip().splitlines()[-1])
            except psycopg.Error:
                pass
            db.finish_run(conn, run_id, error=str(exc))
        finally:
            conn.close()
            with self.lock:
                self.current.pop(stage.lane, None)
                self.threads.pop(stage.lane, None)
