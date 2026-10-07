"""What the running address-hunt workers are doing right now.

    uv run python scripts/limen_run.py

Separate from `limen_status.py`, which answers the other question. That one
reports where the survey stands whether or not anything is running: coverage
per band, and the split between the companies a fetch can reach for free and
the ones that need a paid call. This one reports the health of the workers
themselves, and is silent about strategy.

**The queue count is imported, not rewritten.** `limen_hunt.queue` decides what
the workers select; this report calls that same function. A status line that
counts the queue by its own rules is worse than no status line, because it
disagrees with the thing it claims to describe and gives no sign of doing so.

**The window comes from the workers themselves.** Each shard's start time is
read from `/proc`, so the figures cover the current run with no date to pass
and no chance of quoting a stale one. Reading `/proc` rather than shelling out
to `pgrep -f`, which matches its own command line and has reported a worker
alive after it exited.

**It checks whether the workers are running the code on disk.** Python loads a
module once, so editing a stage mid-run leaves the workers on the old code
while the git log says otherwise. That happened on 07.10.2026: a domain rule
was fixed at 18:43 and the shards had loaded the module at 18:14, so the run
kept filing addresses the fix was written to reject, and nothing said so for
six hours. Comparing each process start against the newest source mtime turns
that into a line of output.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arpenteur import db  # noqa: E402
from arpenteur.config import load_settings  # noqa: E402

from limen_hunt import BANDS, queue  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKER = "limen_hunt.py"


def _boot_time() -> float:
    for line in (Path("/proc/stat").read_text().splitlines()):
        if line.startswith("btime "):
            return float(line.split()[1])
    raise RuntimeError("/proc/stat carries no btime")


def workers() -> list[dict]:
    """The running shards, each with its start time and command line.

    Read from `/proc` rather than by shelling out to `pgrep`, which matches its
    own command line and has reported a worker alive after it exited.
    """
    found, boot = [], _boot_time()
    ticks = 100.0  # os.sysconf("SC_CLK_TCK") is 100 on every Linux in practice
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().decode().split("\0")
            if not any(WORKER in part for part in cmdline):
                continue
            if not any(part.endswith("python") or part.endswith("python3")
                       for part in cmdline[:1]):
                continue  # the `uv run` wrapper, not the interpreter
            stat = (entry / "stat").read_text()
            after = stat[stat.rindex(")") + 2:].split()
            found.append({
                "pid": int(entry.name),
                "started": boot + float(after[19]) / ticks,
                "args": [p for p in cmdline if p],
            })
        except (OSError, ValueError, UnicodeDecodeError):
            continue  # the process went away, or it is not ours to read
    return sorted(found, key=lambda w: w["args"])


def newest_source() -> tuple[Path, float]:
    newest, when = ROOT, 0.0
    for path in (ROOT / "arpenteur").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        stamp = path.stat().st_mtime
        if stamp > when:
            newest, when = path, stamp
    return newest, when


def shard_of(args: list[str]) -> str:
    for index, part in enumerate(args):
        if part == "--shard" and index + 1 < len(args):
            return args[index + 1]
    return "?"


def main() -> int:
    running = workers()
    settings = load_settings()
    conn = db.open_db(settings.database_url)

    if running:
        since = min(w["started"] for w in running)
        window = f"the launch at {time.strftime('%H:%M', time.localtime(since))}"
    else:
        since = time.time() - 24 * 3600
        window = "the last 24h (no worker is running)"

    stamp = time.strftime("%Y-%m-%d %H:%M:%S%z", time.localtime(since))
    hunts = conn.execute("""
        SELECT count(*) FILTER (WHERE outcome <> 'partial')            AS settled,
               count(*) FILTER (WHERE outcome IN ('site','search','inferred'))
                                                                       AS found,
               count(*) FILTER (WHERE outcome = 'partial')             AS unfinished,
               coalesce(sum(searches), 0)                              AS searches
          FROM intel.address_hunt WHERE hunted_at >= %s""", (stamp,)).fetchone()
    mail = conn.execute("""
        SELECT count(*) AS addresses, count(DISTINCT company_id) AS firms
          FROM core.contact
         WHERE kind = 'email' AND NOT suppressed AND fetched_at >= %s""",
                        (stamp,)).fetchone()
    bank = conn.execute("""
        SELECT count(*) AS addresses, count(DISTINCT company_id) AS firms
          FROM core.contact WHERE kind = 'email' AND NOT suppressed""").fetchone()
    left = len(queue(conn, bands=list(BANDS), shard=0, of=1, need_site=None))

    minutes = (time.time() - since) / 60
    settled, found = hunts["settled"], hunts["found"]
    rate = settled / minutes * 60 if minutes else 0.0
    hit = found / settled * 100 if settled else 0.0

    print(f"since {window}, {minutes / 60:.1f}h")
    print(f"  firms settled      {settled}")
    print(f"  with an address    {found}  ({hit:.1f}%)")
    print(f"  new addresses      {mail['addresses']} across {mail['firms']} firms")
    print(f"  searches           {hunts['searches']}")
    print(f"  left unfinished    {hunts['unfinished']}  (stays in the queue)")
    print(f"  queue remaining    {left}")
    # Only while a worker is running. Over the idle window the same division
    # averages work against hours that did none, and reads as a forecast.
    if rate and running:
        print(f"  rate               {rate:.0f} firms/h"
              f"  ->  {left / rate:.1f}h to go")
    print(f"  whole bank         {bank['addresses']} addresses, {bank['firms']} firms")

    newest, edited = newest_source()
    print()
    if not running:
        print("no worker is running")
    for worker in running:
        shard = shard_of(worker["args"])
        log = ROOT / "data" / "logs" / f"hunt-{shard}.log"
        quiet = time.time() - log.stat().st_mtime if log.exists() else None
        age = "no log" if quiet is None else f"log {quiet:.0f}s old"
        print(f"shard {shard}: pid {worker['pid']}, "
              f"up {(time.time() - worker['started']) / 3600:.1f}h, {age}")
        if edited > worker["started"]:
            print(f"  ! started before {newest.relative_to(ROOT)} was edited "
                  f"{(edited - worker['started']) / 60:.0f} min later, so it is "
                  f"running the code as it was, not as it is on disk")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
