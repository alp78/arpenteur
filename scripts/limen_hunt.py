"""Run the address cascade over the Limen bands, in shards, with a heartbeat.

    uv run python scripts/limen_hunt.py --shard 0 --of 2
    uv run python scripts/limen_hunt.py --shard 0 --of 2 --no-search   # free only
    uv run python scripts/limen_hunt.py --shard 0 --of 2 --dry-run

In `scripts/` rather than a scratch file under /tmp, which is where this lived
until a machine restart took both the process and its log with it. A run that
takes a day is a part of how this survey is operated, not a throwaway.

**Why shards rather than one process.** The paid tier is a subprocess per
company and spends about forty-five seconds on each, so a single worker over
several thousand companies is a week of wall time. Workers are interleaved by
row number rather than given contiguous ranges: the ordering is by canton, so a
contiguous split hands one worker the whole of Zurich and another the whole of
Uri.

**Why not many shards.** Every worker draws on the same Claude plan as the
operator's own sessions. Six workers finished a national run in six hours and
left nothing for anyone else; two take longer and stay usable. The number is a
quota decision, not a throughput one.

**The heartbeat exists because a stall is silent.** A worker waiting on a
server that answered `Retry-After: 86400` looks exactly like a worker doing
slow work: the process is alive, the log is quiet, and nothing reports a fault.
Two shards of a six-way run sat like that for an hour unnoticed. So each worker
writes a line at a fixed interval whether or not it has finished a company, and
a silent log now means a stopped worker rather than a patient one.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arpenteur import db  # noqa: E402
from arpenteur.config import load_settings  # noqa: E402
from arpenteur.stages import reach  # noqa: E402

BANDS = ("regie-core", "regie-letting")


def queue(conn, *, bands: list[str], shard: int, of: int,
          need_site: bool | None) -> list[int]:
    """The companies this worker owns.

    `need_site` splits the two queues that cost different amounts: True selects
    companies whose website is known, which tier 1 reads for the price of a
    fetch; False selects the ones where only a search can find the domain.
    """
    where = [
        "c.status = 'ACTIVE'",
        """NOT EXISTS (SELECT 1 FROM core.contact k WHERE k.company_id = c.id
           AND k.kind = 'email' AND NOT k.suppressed)""",
        # A settled outcome is any outcome but `partial`; `partial` means the
        # looking is unfinished and the company stays selectable.
        """NOT EXISTS (SELECT 1 FROM intel.address_hunt h
           WHERE h.company_id = c.id AND h.outcome <> 'partial')""",
    ]
    site = """EXISTS (SELECT 1 FROM intel.site s WHERE s.company_id = c.id
              AND s.url IS NOT NULL AND s.url <> '')"""
    if need_site is True:
        where.append(site)
    elif need_site is False:
        where.append(site.replace("EXISTS", "NOT EXISTS", 1))

    rows = conn.execute(f"""
        SELECT id FROM (
          SELECT c.id, row_number() OVER (ORDER BY c.canton, c.name) AS rn
            FROM core.company c
            JOIN intel.segment g ON g.company_id = c.id AND g.segment = ANY(%s)
           WHERE {' AND '.join(where)}
        ) t WHERE MOD(rn, %s) = %s""", (bands, of, shard)).fetchall()
    return [int(r["id"]) for r in rows]


def source_watch() -> Callable[[], str | None]:
    """Report the first loaded module whose file has changed since import.

    The same argument as the heartbeat below, for the same reason. Python reads
    a module once, so editing a stage mid-run leaves the worker on the code it
    loaded while the git log and the working tree both say otherwise, and
    nothing announces the difference. On 07.10.2026 a domain rule was fixed at
    18:43 and these shards had loaded `reach` at 18:14: the run then spent six
    hours filing addresses the fix was written to reject.

    Stopping is the right answer rather than warning. A warning goes to a log
    that is read when something already looks wrong, which is exactly what did
    not happen. Stopping costs almost nothing: each company commits on its own
    and the queue is read from the database, so the operator restarts and the
    run picks up where it left off, on the new code.
    """
    watched: dict[str, tuple[Path, float]] = {}
    for name, module in list(sys.modules.items()):
        if not name.startswith("arpenteur"):
            continue
        spec = getattr(module, "__file__", None)
        if not spec:
            continue
        path = Path(spec)
        try:
            watched[name] = (path, path.stat().st_mtime)
        except OSError:
            continue

    def changed() -> str | None:
        for name, (path, was) in watched.items():
            try:
                now = path.stat().st_mtime
            except OSError:
                return f"{name} has gone from disk"
            if now != was:
                return (f"{name} was edited "
                        f"{(now - was) / 60:.0f} min after this worker loaded it, "
                        "so the run is on the old code")
        return None

    return changed


def heartbeat(state: dict, every: int, log) -> threading.Thread:
    """Say something at a fixed interval, so silence means stopped."""

    def beat() -> None:
        while not state["done"]:
            time.sleep(every)
            if state["done"]:
                return
            idle = int(time.time() - state["last"])
            log(f"  .. alive, {state['n']} done, {idle}s since the last company")

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    return thread


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--of", type=int, default=1)
    ap.add_argument("--band", action="append", default=[])
    ap.add_argument("--no-search", action="store_true",
                    help="free tiers only; never spends plan quota")
    ap.add_argument("--with-site", dest="site", action="store_true",
                    help="only companies whose website is already known")
    ap.add_argument("--without-site", dest="nosite", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--heartbeat", type=int, default=300)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bands = args.band or list(BANDS)
    need_site = True if args.site else (False if args.nosite else None)

    settings = load_settings()
    conn = db.open_db(settings.database_url)
    ids = queue(conn, bands=bands, shard=args.shard, of=args.of,
                need_site=need_site)
    if args.limit:
        ids = ids[: args.limit]

    paid = "ON (spends plan quota)" if not args.no_search else "OFF (free)"
    print(f"shard {args.shard}/{args.of}: {len(ids)} companies, search tier {paid}",
          flush=True)
    if args.dry_run:
        for row in conn.execute(
            "SELECT uid, name, canton FROM core.company WHERE id = ANY(%s)"
            " ORDER BY canton, name LIMIT 15", (ids,)
        ):
            print(f"  {row['uid']}  {row['canton']}  {row['name'][:48]}")
        print(f"  ... {max(0, len(ids) - 15)} more")
        conn.close()
        return 0

    state = {"done": False, "n": 0, "last": time.time()}

    def log(message: str) -> None:
        if message.startswith("  ") and not message.startswith("  .."):
            state["n"] += 1
            state["last"] = time.time()
        print(message, flush=True)

    started = time.time()
    heartbeat(state, args.heartbeat, log)
    try:
        stats = reach.hunt_all(
            settings, conn, company_ids=ids,
            allow_search=not args.no_search, log=log,
            stop_when=source_watch(),
        )
    finally:
        state["done"] = True
    print({k: v for k, v in stats.items() if v}, flush=True)
    print(f"shard {args.shard} finished in {(time.time() - started) / 60:.1f} min",
          flush=True)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
