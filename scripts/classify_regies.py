"""Grade property companies by how central a flat handover is to their business.

Rules over a registered purpose, not a model, and the reason is availability
rather than preference: the register cannot be searched by activity, so the
purpose text is the only evidence, and it is text a rule can read. A model
would also cost per company across twenty thousand of them and would not be
reproducible three months later, where a rule is both.

The grading question is narrow. Who runs an entry and an exit inspection often
enough for an app to matter?

  regie-core       manages property for third parties. Every tenancy it
                   administers produces two inspections.
  regie-letting    lets property but does not name administration. Handovers
                   happen; the paperwork may sit with somebody else.
  regie-brokerage  brokerage, sale or development. A handover at a sale is a
                   different document and a rarer event.
  regie-owner      owns or invests and names neither. The inspections happen
                   on its buildings, run by whichever agency it appoints.

Core outranks the rest: a purpose naming both `gérance` and `courtage` is a
managing agency that also brokers, not a broker.

Labels are written to `intel.segment` with `source = 'manual'`, which is what
stops a later `profile` run overwriting them. That is the same protection a
hand-set label gets, and it is honest: these came from a stated rule rather
than from a judgement, and either way a person decided the rule.

    uv run python scripts/classify_regies.py --dry-run
    uv run python scripts/classify_regies.py
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from arpenteur import db  # noqa: E402
from arpenteur.config import load_settings  # noqa: E402
from arpenteur.modules.scout import store as scout_store  # noqa: E402

# Managing somebody else's property. The words a Swiss register actually uses,
# in the four languages a purpose is filed in.
CORE = re.compile(
    r"gerance|administration\s+(?:de\s+)?(?:biens|immeubles|immobili)"
    r"|gestion\s+(?:locative|d[eu']\s*immeubles|immobiliere|de\s+biens)"
    r"|regie\s+immobiliere"
    r"|liegenschaftsverwaltung|immobilienverwaltung|hausverwaltung"
    r"|verwaltung\s+von\s+(?:liegenschaften|immobilien)"
    r"|bewirtschaftung|mietverwaltung|stockwerkeigentum"
    r"|amministrazione\s+(?:di\s+)?(?:immobili|stabili)"
    r"|gestione\s+(?:di\s+)?(?:immobili|stabili)",
    re.IGNORECASE,
)

LETTING = re.compile(
    r"\blocation\b|mise\s+en\s+location|louer"
    r"|vermietung|vermieten|verpachtung"
    r"|locazione|affitto",
    re.IGNORECASE,
)

BROKERAGE = re.compile(
    r"courtage|promotion\s+immobiliere|intermediaire|negoce"
    r"|vermittlung|makler|maklerei|verkauf"
    r"|mediazione|intermediazione|promozione",
    re.IGNORECASE,
)

OWNER = re.compile(
    r"acquisition|detention|possession|\bdetenir\b|participations"
    r"|erwerb|halten|besitz|beteiligung"
    r"|acquisto|detenzione|partecipazioni",
    re.IGNORECASE,
)

# A purpose has to be about property at all. "Verwaltung" alone appears in the
# purpose of every holding company in the country.
PROPERTY = re.compile(
    r"immobili|immeuble|liegenschaft|grundstuck|wohnung|appartement|"
    r"batiment|gebaude|stabili|foncier|locaux|mieter|locataire|bien\s+immobilier",
    re.IGNORECASE,
)

BANDS = ("regie-core", "regie-letting", "regie-brokerage", "regie-owner")

# A name that presents the firm as a property professional. Not evidence of
# what it does, which is the purpose's job, but evidence that it says so on the
# door: a shortlist sorted on band alone puts an architecture practice that
# mentions property beside a firm called "Régie du Centre".
TRADE_NAME = re.compile(
    r"regie|gerance|immobili|liegenschaft|verwaltung|bewirtschaft|hausverwalt"
    r"|gestion|stabili|\bimmo\b|immo[-]",
    re.IGNORECASE,
)

# Where a Swiss registered purpose stops describing the business and starts
# listing what the company is permitted to do.
#
# This clause is the single largest source of false positives, and it is
# boilerplate a notary adds to almost every Sàrl: "elle peut acquérir, détenir
# et aliéner des immeubles". Graded on the whole text, a transport firm, a
# jewellery school and a dental practice all read as property companies,
# because every one of them may buy premises and every one of them says so.
#
# So the text is cut here and only the head is graded. What a company is for
# comes first in a purpose; what it may also do comes after one of these.
BOILERPLATE = re.compile(
    r"\b(?:elle\s+peut|la\s+societe\s+peut|elle\s+pourra|"
    r"pour\s+atteindre\s+ce\s+but|peut\s+en\s+outre|elle\s+peut\s+en\s+outre|"
    r"sie\s+kann|die\s+gesellschaft\s+kann|kann\s+sie\s+auch|"
    r"essa\s+puo|la\s+societa\s+puo|puo\s+inoltre|"
    r"vollstandige\s+zweckumschreibung|"
    r"ainsi\s+que\s+toutes\s+operations|toutes\s+operations\s+"
    r"(?:commerciales\s+)?(?:financieres|mobilieres)\s+et\s+immobilieres)\b",
    re.IGNORECASE,
)


def head(purpose: str) -> str:
    """The purpose up to the permissions boilerplate."""
    text = fold(purpose or "")
    cut = BOILERPLATE.search(text)
    return text[: cut.start()] if cut else text


def fold(text: str) -> str:
    """Accents away, so `gérance` and `gerance` are one word to a pattern."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(ch)
    )


# How close an activity word has to sit to a property word before the two are
# read as one statement.
#
# Testing them independently was the first version and it was wrong in both
# directions at once. `AAB Group Sàrl` graded as a managing agency on "mise en
# gérance et exploitation d'établissements publics tels que kiosques", which is
# the management of kiosks; `A7 Services Sàrl` graded as a property broker on
# "courtage dans les domaines des assurances"; a dental practice graded as a
# property owner because its purpose mentioned acquiring premises. In every
# case both words were present and they were about different things.
#
# Sixty characters is about a clause. It keeps "gérance d'immeubles" and
# "Verwaltung von Liegenschaften" while separating two items of a list.
NEAR = 60


def _near(activity: re.Pattern[str], text: str) -> bool:
    """Whether an activity word and a property word share a clause."""
    spots = [m.start() for m in PROPERTY.finditer(text)]
    if not spots:
        return False
    for hit in activity.finditer(text):
        start = hit.start()
        if any(abs(start - spot) <= NEAR for spot in spots):
            return True
    return False


def grade(purpose: str, name: str) -> tuple[str, str] | None:
    """The band and the reason, or None when this is not a property company.

    Read from the head of the purpose alone, before the permissions clause.

    The name is not graded, only used to say how strongly the two agree. A firm
    called "Régie X" whose purpose names gérance is a managing agency twice
    over; "Abazi Architecture Sàrl" whose purpose mentions property among eight
    other activities is a maybe, and the difference is worth carrying rather
    than flattening. Precision here is good and not perfect: a long purpose
    that lists property among many trades still lands in a band, so the
    agreement flag is what a shortlist should sort on.

    The reason is returned and stored because a label whose grounds are not
    recorded cannot be argued with, and every one of these will be argued with
    the first time a shortlist looks wrong.
    """
    text = head(purpose)
    if not PROPERTY.search(text):
        return None
    named = bool(TRADE_NAME.search(fold(name or "")))
    agrees = "name agrees" if named else "name is silent"
    if _near(CORE, text):
        return "regie-core", f"purpose names management of property for others, {agrees}"
    if _near(LETTING, text):
        return "regie-letting", f"purpose names letting but not administration, {agrees}"
    if _near(BROKERAGE, text):
        return "regie-brokerage", f"purpose names brokerage or development only, {agrees}"
    if _near(OWNER, text):
        return "regie-owner", f"purpose names holding or investment only, {agrees}"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="count, write nothing")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    settings = load_settings()
    conn = db.open_db(settings.database_url)
    sql = """
        SELECT id, name, canton, purpose FROM core.company
         WHERE status = 'ACTIVE' AND hydrated_at IS NOT NULL
           AND purpose IS NOT NULL AND purpose <> ''
           AND legal_form_uid IN ('0106', '0107', '0108')
         ORDER BY id
    """
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = conn.execute(sql).fetchall()

    tally: Counter[str] = Counter()
    by_canton: dict[str, Counter[str]] = {}
    written = 0
    for row in rows:
        verdict = grade(row["purpose"] or "", row["name"] or "")
        if verdict is None:
            tally["not property"] += 1
            continue
        band, why = verdict
        tally[band] += 1
        by_canton.setdefault(row["canton"] or "??", Counter())[band] += 1
        if not args.dry_run:
            # Every other band is cleared first, so re-running after a purpose
            # amendment moves a company rather than leaving it in two bands.
            for other in BANDS:
                if other != band:
                    scout_store.set_segment_manually(
                        conn, int(row["id"]), other, present=False
                    )
            scout_store.set_segment_manually(
                conn, int(row["id"]), band, rationale=why, author="classify_regies"
            )
            written += 1
            if written % 500 == 0:
                conn.commit()
                print(f"  {written} labelled")
    if not args.dry_run:
        conn.commit()

    print(f"read {len(rows):,} hydrated companies\n")
    for band in BANDS:
        print(f"  {band:18} {tally[band]:>6,}")
    print(f"  {'not property':18} {tally['not property']:>6,}")
    print(f"\nwritten: {written:,}" if not args.dry_run else "\n(dry run, nothing written)")

    FR = {"GE", "VD", "NE", "JU", "FR", "VS"}
    print("\nregie-core by canton:")
    core = sorted(
        ((c, t["regie-core"]) for c, t in by_canton.items() if t["regie-core"]),
        key=lambda kv: -kv[1],
    )
    for canton, n in core:
        lang = "fr" if canton in FR else ("it" if canton == "TI" else "de")
        print(f"  {canton}  {lang}  {n:>5,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
