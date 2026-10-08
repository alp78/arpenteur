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
from collections import Counter

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from arpenteur import db  # noqa: E402
from arpenteur.config import load_settings  # noqa: E402
from arpenteur.modules.scout import store as scout_store  # noqa: E402
from arpenteur.sources.purpose import fold, head, near  # noqa: E402

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

# The Swiss single-property vehicle, and the one shape the purpose text cannot
# be trusted on.
#
# "Société Immobilière Acacias-Lièvre B SA" owns one building. Its registered
# purpose is a notary's list — "achat, vente, construction, exploitation et
# gérance d'immeubles" — and `gérance` there means managing the building it
# owns, not managing anyone else's. On the text alone it is indistinguishable
# from an agency, and 198 of them were sitting in the core band on that word.
#
# THE TEXT CANNOT SETTLE IT, SO THE NAME DOES. Reading the purposes side by
# side, no wording separates the two shapes: GRANGE Immobilier SA, a large
# Geneva agency, says "gérance d'immeubles, promotion, courtage, achat, vente"
# — the same verbs in the same order. What separates them is the naming
# convention: `Société immobilière` followed by a place or a building is how
# Switzerland names a holding vehicle, and agencies do not use it.
#
# Nearly French-only in practice: 198 of these against two German forms and no
# Italian ones, which is why the pattern is not symmetrical across languages.
VEHICLE_NAME = re.compile(
    r"^\s*(?:soci[eé]t[eé]\s+immobili[eè]re|immobiliengesellschaft"
    r"|societ[aà]\s+immobiliare)",
    re.IGNORECASE,
)

# The escape hatch, and it is deliberately narrow. A vehicle that really does
# manage for other people says so in words that the boilerplate never uses:
# third parties by name, condominium administration, letting management as a
# service. `courtage` and `promotion` are NOT here — they sit inside the same
# notarial list as `gérance` and separate nothing.
FOR_OTHERS = re.compile(
    r"pour\s+(?:le\s+compte\s+)?(?:de\s+)?tiers|\bde\s+tiers\b"
    r"|f[uü]r\s+Dritte|di\s+terzi|copropri[eé]t|\bPPE\b|stockwerkeigentum"
    r"|condomini|gestion\s+locative|r[eé]gie\s+immobili[eè]re",
    re.IGNORECASE,
)

# A name that presents the firm as a property professional. Not evidence of
# what it does, which is the purpose's job, but evidence that it says so on the
# door: a shortlist sorted on band alone puts an architecture practice that
# mentions property beside a firm called "Régie du Centre".
TRADE_NAME = re.compile(
    r"regie|gerance|immobili|liegenschaft|verwaltung|bewirtschaft|hausverwalt"
    r"|gestion|stabili|\bimmo\b|immo[-]",
    re.IGNORECASE,
)

# The permissions clause, the clause-proximity rule and the accent fold all
# moved to `arpenteur/sources/purpose.py` when the tourism classifier needed the
# same three. They were earned here, over this corpus; the module carries the
# reasoning with them.


def _near(activity: re.Pattern[str], text: str) -> bool:
    """Whether an activity word and a property word share a clause."""
    return near(activity, PROPERTY, text)


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
    # A holding vehicle is graded on its name before its purpose, because its
    # purpose is boilerplate that reads like an agency's. The one thing that
    # overrides the name is the company saying, in terms the boilerplate does
    # not use, that it acts for other people.
    if VEHICLE_NAME.search(name or "") and not FOR_OTHERS.search(purpose or ""):
        return "regie-owner", (
            "named as a single-property vehicle and the purpose never says it "
            "acts for third parties, so the gérance is of its own building"
        )
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
