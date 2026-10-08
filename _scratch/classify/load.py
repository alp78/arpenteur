"""Write agent-produced classifications into the database.

Deliberately thin. Everything that decides whether a label is acceptable lives
in `stages.profile.parse_reply` and `db.save_segments` / `db.save_sector`, and
this reuses both rather than reimplementing them: the closed vocabulary, the
confidence clamping and the rule that a hand-set label outranks a model's are
the same code the real stage runs, so a row written here is indistinguishable
in kind from a row written by `arpenteur profile`.

What it does NOT do is pretend about provenance. These classifications came
from Claude Code subagents rather than from the classify stage's own API call,
so that is what goes in the `model` column. A later reader asking "what
produced this row" gets the true answer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from arpenteur import db
from arpenteur.config import load_settings
from arpenteur.stages.profile import parse_reply

MODEL = "claude-opus-5 (claude-code agent)"
HERE = Path(__file__).parent


def main(patterns: list[str]) -> int:
    settings = load_settings()
    conn = db.open_db(settings.database_url)
    valid, sectors = set(settings.segments), set(settings.sectors)

    # uid -> id, once. Looking each company up by uid one at a time would be a
    # round trip per row and there are seven thousand of them.
    ids = {
        row["uid"]: row["id"]
        for row in conn.execute("SELECT id, uid FROM core.company")
    }

    totals = {"files": 0, "rows": 0, "sectors": 0, "labels": 0, "unknown_uid": 0,
              "no_sector": 0}
    for pattern in patterns:
        for path in sorted(HERE.glob(pattern)):
            payload = json.loads(path.read_text(encoding="utf-8"))
            # Back through the stage's own parser, which drops any sector or
            # segment name that is not in the vocabulary.
            parsed = parse_reply(json.dumps(payload), valid, sectors)
            for uid, verdict in parsed.items():
                company_id = ids.get(uid)
                if company_id is None:
                    totals["unknown_uid"] += 1
                    continue
                db.save_segments(conn, company_id, verdict.segments, MODEL)
                if verdict.sector:
                    db.save_sector(
                        conn, company_id, verdict.sector["sector"],
                        confidence=verdict.sector["confidence"],
                        rationale=verdict.sector["rationale"], model=MODEL,
                    )
                    totals["sectors"] += 1
                else:
                    totals["no_sector"] += 1
                totals["rows"] += 1
                totals["labels"] += len(verdict.segments)
            conn.commit()
            totals["files"] += 1
            print(f"  {path.name}: {len(parsed)} verdicts")

    conn.close()
    print(json.dumps(totals, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["result_*.json"]))
