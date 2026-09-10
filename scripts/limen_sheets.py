"""One folder per canton, one sheet per agency, for the Limen rollout.

Files rather than a table, because a sheet is what gets opened next to a phone
call: the register identity, why this firm was graded as it was, its own words
about what it does, and where to write. A spreadsheet row holds the same facts
and none of the reasoning, and the reasoning is what stops a shortlist being
re-litigated every time it looks wrong.

Laid out as language region, then canton, because that is the order the work is
done in: an approach is written in one language and sent to one canton's firms.

    uv run python scripts/limen_sheets.py --out /path/to/LIMEN
    uv run python scripts/limen_sheets.py --out /path/to/LIMEN --band regie-core

Re-runnable. Sheets are rewritten from the database each time, so a fact
corrected in the register or an address found later shows up on the next run
rather than being patched by hand.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arpenteur import db  # noqa: E402
from arpenteur.config import load_settings  # noqa: E402

# Majority language per canton. An approximation, and wrong at four known
# edges: Bern has a French Jura bernois, Fribourg and Valais are officially
# bilingual, and Grisons is trilingual. It decides which folder a sheet lands
# in, so being wrong costs a filing rather than a fact.
FR = {"GE", "VD", "NE", "JU", "FR", "VS"}
IT = {"TI"}

BANDS = ("regie-core", "regie-letting", "regie-brokerage", "regie-owner")
BAND_NOTE = {
    "regie-core": "manages property for third parties, so runs inspections routinely",
    "regie-letting": "lets property; the administration may sit elsewhere",
    "regie-brokerage": "brokerage or development, so a handover is occasional",
    "regie-owner": "owns or invests; its appointed agency runs the handovers",
}


def language(canton: str) -> str:
    return "fr" if canton in FR else ("it" if canton in IT else "de")


def slug(text: str, *, limit: int = 60) -> str:
    folded = "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(ch)
    )
    out = re.sub(r"[^A-Za-z0-9]+", "-", folded).strip("-").lower()
    return (out[:limit].rstrip("-") or "sans-nom")


def address_lines(raw: dict | None) -> str:
    if not raw:
        return "not in the register entry"
    street = " ".join(
        part for part in (raw.get("street"), raw.get("houseNumber")) if part
    ).strip()
    town = " ".join(
        part for part in (raw.get("swissZipCode"), raw.get("city")) if part
    ).strip()
    return ", ".join(part for part in (street, raw.get("addon"), town) if part) or "incomplete"


def sheet(row: dict) -> str:
    """One agency, as a page."""
    band = row["band"] or "ungraded"
    addresses = [a for a in (row["addresses"] or []) if a]
    contacts = [a for a in (row["contacts"] or []) if a]
    every = list(dict.fromkeys(addresses + contacts))

    # A dash, and it means something: every tier ran and this firm publishes
    # nothing findable. Distinct from a blank, which would mean nobody looked.
    if every:
        mail_block = "\n".join(f"- `{a}`" for a in every)
    elif row["outcome"] == "none":
        mail_block = "- (dash) every tier ran and found nothing published"
    elif row["outcome"]:
        mail_block = f"- nothing yet, the hunt stopped at `{row['outcome']}`"
    else:
        mail_block = "- not looked for yet"

    purpose = (row["purpose"] or "").strip() or "not in the register entry"
    return f"""# {row["name"]}

| | |
|---|---|
| UID | `{row["uid"]}` |
| Legal form | {row["legal_form_label"] or ""} |
| Canton | {row["canton"] or ""} ({language(row["canton"] or "")}) |
| Registered seat | {row["legal_seat"] or ""} |
| Address | {address_lines(row["address_json"])} |
| Register status | {row["status"] or ""} |

## Relevance for Limen

**{band}** — {BAND_NOTE.get(band, "not graded")}

{row["rationale"] or "No grading recorded."}

## Where to write

{mail_block}

{f"Website: `{row['domain']}`" if row["domain"] else "No website resolved."}

## What the register says it does

> {purpose}

## Sources

- Register entry: {row["zefix_detail_url"] or "not recorded"}
- Grading: rule over the registered purpose, see `scripts/classify_regies.py`
- Address search: `intel.address_hunt`, outcome `{row["outcome"] or "not run"}`

The register carries no warranty and has no legal effect; only the certified
cantonal excerpt and the FOSC publication text are binding.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="root folder to write into")
    ap.add_argument(
        "--band", action="append", default=[],
        help="only these bands, repeatable; default is core and letting",
    )
    ap.add_argument("--all-bands", action="store_true")
    args = ap.parse_args()
    wanted = args.band or (list(BANDS) if args.all_bands else ["regie-core", "regie-letting"])

    settings = load_settings()
    conn = db.open_db(settings.database_url)
    rows = conn.execute(
        """
        SELECT c.uid, c.name, c.canton, c.legal_seat, c.legal_form_label,
               c.status, c.purpose, c.address_json, c.zefix_detail_url,
               s.segment AS band, s.rationale,
               h.outcome, h.domain, h.addresses,
               ARRAY(SELECT value FROM core.contact
                      WHERE company_id = c.id AND kind = 'email'
                        AND NOT suppressed) AS contacts
          FROM core.company c
          JOIN intel.segment s ON s.company_id = c.id AND s.segment = ANY(%s)
          LEFT JOIN intel.address_hunt h ON h.company_id = c.id
         WHERE c.status = 'ACTIVE'
         ORDER BY c.canton, s.segment, c.name
        """,
        (wanted,),
    ).fetchall()

    root = Path(args.out)
    written = 0
    per_canton: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        canton = row["canton"] or "XX"
        per_canton.setdefault((language(canton), canton), []).append(row)

    for (lang, canton), group in sorted(per_canton.items()):
        folder = root / lang / canton
        folder.mkdir(parents=True, exist_ok=True)
        for row in group:
            (folder / f"{slug(row['name'])}.md").write_text(sheet(row), encoding="utf-8")
            written += 1
        # An index per canton, ordered by band, so the folder is readable
        # without opening thirty files to find which three matter.
        lines = [f"# {canton} · {len(group)} firms\n"]
        for band in BANDS:
            here = [r for r in group if r["band"] == band]
            if not here:
                continue
            lines.append(f"\n## {band} ({len(here)})\n")
            lines.append(f"{BAND_NOTE[band]}\n")
            for row in here:
                mail = list(dict.fromkeys(
                    [a for a in (row["addresses"] or []) if a]
                    + [a for a in (row["contacts"] or []) if a]
                ))
                shown = mail[0] if mail else ("-" if row["outcome"] == "none" else "")
                extra = f" (+{len(mail) - 1})" if len(mail) > 1 else ""
                lines.append(
                    f"- [{row['name']}]({slug(row['name'])}.md) · "
                    f"{row['legal_seat'] or ''} · `{shown}{extra}`"
                )
        (folder / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    counts = {
        band: sum(1 for r in rows if r["band"] == band) for band in BANDS
    }
    have_mail = sum(
        1 for r in rows if (r["addresses"] or []) or (r["contacts"] or [])
    )
    readme = [
        "# Limen · Swiss rental agencies\n",
        "One folder per language region, then one per canton, then one sheet "
        "per firm. Every sheet is generated from the register and rewritten on "
        "each run, so corrections belong upstream rather than in these files.\n",
        f"- firms: **{len(rows):,}**",
        f"- with an address found: **{have_mail:,}**",
        "",
        "## Bands\n",
    ]
    for band in BANDS:
        if counts.get(band):
            readme.append(f"- **{band}** ({counts[band]:,}) — {BAND_NOTE[band]}")
    readme += [
        "",
        "## What a dash means\n",
        "A sheet showing `(dash)` had every tier of the address search run "
        "against it and publishes nothing findable. That is a settled result, "
        "not a gap: it stops the paid tier being spent on the same firm twice.\n",
        "## The Romansh gap\n",
        "Grisons sits under `de`, its majority language. Romansh is spoken by "
        "a minority of one canton in named valleys, so no canton list selects "
        "it. The register stores each firm's commune in `legal_seat`, which is "
        "the column a real Romansh list would be built from.\n",
    ]
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")

    print(f"{written:,} sheets in {len(per_canton)} canton folders under {root}")
    for band in BANDS:
        if counts.get(band):
            print(f"  {band:18} {counts[band]:>6,}")
    print(f"  with an address    {have_mail:>6,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
