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
    """One control on the start form, and how it reaches the stage."""

    name: str
    label: str
    kind: str = "text"  # text | int | bool | choice
    choices: tuple[str, ...] = ()
    help: str = ""
    default: str = ""


@dataclass(frozen=True)
class Stage:
    name: str
    summary: str
    call: Callable[..., dict[str, Any]]
    fields: tuple[Field, ...] = ()
    # What it spends. Shown on the form, because "costs money per company" is
    # the one thing an operator must not have to remember.
    cost: str = ""
    # Paid stages must be given a size before they start.
    needs_limit: bool = False
    # Most stages return a stats dict. `hydrate` returns a bare count, and
    # without this the runner recorded `{}` for it: a finished run whose
    # result line said nothing at all, which is worse than no view.
    result_key: str = ""


LIMIT = Field("limit", "limit", "int", help="how many companies at most")
DRY = Field("dry_run", "dry run", "bool", default="1",
            help="say what would happen, spend nothing")

# `profile` is the exception and it is the expensive kind of exception. Its
# dry run still calls the model for every batch; what it skips is the write.
# An operator reading "spend nothing" and ticking it over sixty thousand
# companies would be billed for the entire classification.
DRY_PROFILE = Field("dry_run", "dry run", "bool", default="1",
                    help="classify and print without writing (still calls the model)")

STAGES: dict[str, Stage] = {
    "sync": Stage(
        "sync",
        "Read the FOSC change feed and mark what moved.",
        sync.run,
        (
            Field("since", "since", help="first FOSC day, YYYY-MM-DD (blank: resume)"),
            Field("until", "until", help="last FOSC day (blank: today)"),
            DRY,
        ),
        cost="one request per day of the feed, free",
    ),
    "hydrate": Stage(
        "hydrate",
        "Fetch full detail for companies that lack it, and for those a notice said changed.",
        source.hydrate_pending,
        (
            LIMIT,
            Field("bulk", "bulk", "bool",
                  help="accept a run over the polite ceiling, deliberately"),
        ),
        cost="one Zefix request per company, free but slow: about 2 a second",
        result_key="hydrated",
    ),
    "people": Stage(
        "people",
        "Parse officers out of the SHAB notices already stored.",
        people.run,
        (LIMIT, Field("reparse", "reparse", "bool",
                      help="re-read companies that already have people"), DRY),
        cost="no network, no model, free",
    ),
    "profile": Stage(
        "profile",
        "Sort companies into segments from their registered purpose.",
        profile.run,
        (LIMIT, Field("reclassify", "reclassify", "bool",
                      help="include companies already classified"), DRY_PROFILE),
        cost="one cheap model call per batch of companies, dry run included",
    ),
    "enrich": Stage(
        "enrich",
        "Find and read a company's own website.",
        enrich.run,
        (
            Field("canton", "canton"),
            Field("segment", "segment"),
            Field("uids", "uids", help="comma separated, overrides the filters"),
            LIMIT,
            Field("refresh", "refresh", "bool", help="re-research companies already done"),
            DRY,
        ),
        cost="about 40 centimes and 7 minutes per company",
        needs_limit=True,
    ),
    "match": Stage(
        "match",
        "Score companies against campaign.md, with the approach advice.",
        match.run,
        (
            Field("segment", "segment"),
            Field("uids", "uids", help="comma separated, overrides the filters"),
            LIMIT,
            Field("researched_only", "researched only", "bool", default="1",
                  help="off: score on the register alone, without waiting for enrich"),
            Field("rescore", "rescore", "bool", help="re-score companies already scored"),
            DRY,
        ),
        cost="one model call per company",
        needs_limit=True,
    ),
    "reach": Stage(
        "reach",
        "Read those websites for a published address.",
        reach.run,
        (
            Field("segment", "segment"),
            Field("band", "band", "choice", ("", "high", "medium", "low", "excluded")),
            LIMIT,
            Field("refresh", "refresh", "bool", help="re-read sites already looked at"),
            DRY,
        ),
        cost="a handful of requests per company, no model, free",
    ),
}


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
        if field_.kind == "int":
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
            log(f"{stage.name}: {stage.summary}")
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
