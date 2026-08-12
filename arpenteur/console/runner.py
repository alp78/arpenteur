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

import sqlite3
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import db
from ..config import Settings
from ..stages import enrich, match, people, profile, reach, source, sync


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
        done_sql="SELECT COUNT(*) FROM sync_day",
        # Days of gazette not yet read, which is what being behind means here.
        # The count of companies marked as changed belongs to Fetch detail,
        # and showing it against this stage said the gazette was behind when
        # what was behind was the re-reading.
        waiting_sql="SELECT MAX(0, CAST(julianday('now') "
                    "- julianday(COALESCE((SELECT MAX(sogc_date) FROM sync_day), "
                    "date('now'))) AS INTEGER))",
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
        needs="A sweep, from the command line: arpenteur source zefix --canton VD",
        produces="Purpose text, capital, address and gazette notices for each company.",
        cost="One register request per company. Free, but paced at about two a second, "
             "so ten thousand companies is roughly ninety minutes.",
        button="Fetch detail",
        button_does="Works through the companies that have no detail yet, plus any the "
                    "gazette marked as changed, starting with the changed ones.",
        done_sql="SELECT COUNT(*) FROM company WHERE hydrated_at IS NOT NULL",
        waiting_sql="SELECT COUNT(*) FROM company "
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
        done_sql="SELECT COUNT(DISTINCT company_id) FROM person",
        waiting_sql="SELECT COUNT(*) FROM company c WHERE EXISTS "
                    "(SELECT 1 FROM publication p WHERE p.company_id = c.id) "
                    "AND NOT EXISTS (SELECT 1 FROM person r WHERE r.company_id = c.id)",
    ),
    "profile": Stage(
        name="profile",
        title="Sort into segments",
        what="Read each company's registered purpose and apply your segment labels to it, "
             "so the survey can be sliced by the kind of business rather than by canton.",
        call=profile.run,
        fields=(
            SIZES,
            Field("reclassify", "Re-label companies already classified", "bool",
                  help="Use after editing the segment definitions in arpenteur.toml. "
                       "Labels you set by hand are never overwritten."),
            DRY_PROFILE,
        ),
        needs="Fetch company detail. The purpose text only arrives with it.",
        produces="Segment labels, which then filter every stage below.",
        cost="A cheap model call per batch of twenty companies. Roughly a franc "
             "per few thousand companies.",
        button="Sort into segments",
        button_does="Labels companies that have a purpose and no labels yet. "
                    "Companies in liquidation are skipped.",
        done_sql="SELECT COUNT(DISTINCT company_id) FROM segment",
        waiting_sql="SELECT COUNT(*) FROM company c WHERE c.purpose IS NOT NULL "
                    "AND c.status = 'ACTIVE' AND NOT EXISTS "
                    "(SELECT 1 FROM segment s WHERE s.company_id = c.id)",
    ),
    "enrich": Stage(
        name="enrich",
        title="Research websites",
        what="Search for each company's own website, read it, and record what it sells, "
             "what it runs on, who it partners with, and anything that argues against "
             "approaching it.",
        call=enrich.run,
        fields=(CANTON, SEGMENT, UIDS, SIZES,
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
        done_sql="SELECT COUNT(*) FROM site",
        waiting_sql="SELECT COUNT(*) FROM company c WHERE c.status = 'ACTIVE' "
                    "AND EXISTS (SELECT 1 FROM segment s WHERE s.company_id = c.id) "
                    "AND NOT EXISTS (SELECT 1 FROM site w WHERE w.company_id = c.id)",
        needs_limit=True,
    ),
    "match": Stage(
        name="match",
        title="Score against the campaign",
        what="Read campaign.md and each company's file together, and answer three "
             "questions: how good a fit, why, and what you would actually say first.",
        call=match.run,
        fields=(
            SEGMENT, UIDS, SIZES,
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
        done_sql="SELECT COUNT(*) FROM fit",
        waiting_sql="SELECT COUNT(*) FROM site w JOIN company c ON c.id = w.company_id "
                    "WHERE c.status = 'ACTIVE' AND NOT EXISTS "
                    "(SELECT 1 FROM fit f WHERE f.company_id = w.company_id)",
    ),
    "reach": Stage(
        name="reach",
        title="Find an address",
        what="Open the websites already found and take the email addresses the company "
             "publishes on them, recording which page each one came from.",
        call=reach.run,
        fields=(SEGMENT, BAND, UIDS, SIZES,
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
        done_sql="SELECT COUNT(*) FROM contact WHERE kind = 'email'",
        waiting_sql="SELECT COUNT(*) FROM site WHERE status = 'found' "
                    "AND url IS NOT NULL AND url <> '' AND reached_at IS NULL",
    ),
}

# The order the operations page shows them in, which is the order they run in.
ORDER = ("sync", "hydrate", "people", "profile", "enrich", "match", "reach")

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


def options_for(conn: sqlite3.Connection, settings: Settings) -> dict[str, tuple]:
    """The contents of every dropdown, read from the survey as it stands.

    Live rather than hardcoded: a canton list typed into the source drifts the
    first time the territory changes, and a segment list that does not match
    arpenteur.toml offers filters that select nothing.
    """
    cantons = [
        (row[0], row[0])
        for row in conn.execute(
            "SELECT DISTINCT canton FROM company WHERE canton IS NOT NULL ORDER BY canton"
        )
    ]
    segments = [(name, name) for name in sorted(settings.segments)]
    return {
        "cantons": (("", "All cantons"), *cantons),
        "segments": (("", "All segments"), *segments),
        "bands": BAND_CHOICES,
        "limits": LIMIT_CHOICES,
    }


def status_of(conn: sqlite3.Connection, stage: Stage) -> dict[str, int]:
    """How much this stage has done and how much is queued for it.

    The number that turns a form into an instrument: "3 done, 41'238 waiting"
    says what to press next, where a bare button says nothing.
    """
    def count(sql: str) -> int:
        if not sql:
            return 0
        try:
            return int(conn.execute(sql).fetchone()[0])
        except sqlite3.Error:
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
        raw = form.get(field_.name)
        if field_.kind == "bool":
            kwargs[field_.name] = bool(raw)
            continue
        if raw is None or str(raw).strip() == "":
            continue
        value = str(raw).strip()
        # `limit` reaches the stage as a number whether it came from a list of
        # sizes or a typed box, so the control can change without the stages
        # learning about it.
        if field_.kind == "int" or field_.name == "limit":
            try:
                kwargs[field_.name] = int(value)
            except ValueError as exc:
                raise ValueError(f"{field_.label} must be a whole number") from exc
        elif field_.name == "uids":
            kwargs["uids"] = [part.strip() for part in value.split(",") if part.strip()]
        else:
            kwargs[field_.name] = value
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


def close_orphans(conn: sqlite3.Connection) -> int:
    """Settle console runs whose thread died with the process. Returns how many.

    A run is a thread inside the console, so stopping the console kills it
    mid-flight and leaves a row that says "running" with nothing running. The
    progress view would poll it forever and the history would be a lie.

    Only console runs. A `run` row from the command line may belong to a
    process still working in another terminal, and marking that interrupted
    would be inventing a fact about someone else's job.
    """
    cur = conn.execute(
        "UPDATE run SET finished_at = ?, error = ? "
        "WHERE finished_at IS NULL AND started_by = 'console'",
        (db.now(), "interrupted: the console stopped while this was running"),
    )
    conn.commit()
    return int(cur.rowcount or 0)


@dataclass
class Runner:
    """One console's worth of running. Holds the lock and the current run."""

    settings: Settings
    lock: threading.Lock = field(default_factory=threading.Lock)
    current: int | None = None
    thread: threading.Thread | None = None

    def running(self) -> int | None:
        with self.lock:
            if self.thread is not None and not self.thread.is_alive():
                self.current, self.thread = None, None
            return self.current

    def start(self, name: str, form: dict[str, Any], *, by: str = "console") -> int:
        stage = STAGES.get(name)
        if stage is None:
            raise ValueError(f"unknown stage {name!r}")
        kwargs = coerce(stage, form)
        check(stage, kwargs)

        with self.lock:
            if self.thread is not None and not self.thread.is_alive():
                self.current, self.thread = None, None
            if self.current is not None:
                raise RuntimeError(
                    f"Run {self.current} is still going. One at a time: two runs of "
                    "the same stage would spend twice and interleave their writes."
                )
            conn = db.open_db(self.settings.db_path)
            try:
                run_id = db.start_run(conn, name, dict(form), started_by=by)
            finally:
                conn.close()
            self.current = run_id
            self.thread = threading.Thread(
                target=self._work, args=(stage, run_id, kwargs), daemon=True,
                name=f"arpenteur-{name}-{run_id}",
            )
            self.thread.start()
            return run_id

    def _work(self, stage: Stage, run_id: int, kwargs: dict[str, Any]) -> None:
        # Its own connection: sqlite3 objects belong to the thread that made
        # them, and the console must keep answering while this works.
        conn = db.open_db(self.settings.db_path)

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
            result = stage.call(self.settings, conn, log=log, **kwargs)
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
            # The message goes to both places on purpose: the log is what the
            # watcher is reading, the run row is what the history shows.
            try:
                log(f"! {exc}")
                log(traceback.format_exc().strip().splitlines()[-1])
            except sqlite3.Error:
                pass
            db.finish_run(conn, run_id, error=str(exc))
        finally:
            conn.close()
            with self.lock:
                self.current, self.thread = None, None
