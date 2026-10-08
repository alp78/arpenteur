"""Grade organisations by how close they sit to advising a visitor about nature.

Rules over a registered purpose, not a model, for the reason
`scripts/classify_regies.py` gives: the register cannot be searched by activity,
the purpose text is the only evidence, and a rule is both cheaper and
reproducible three months later.

The grading question is narrow. Who tells a visitor where the lakes, the
summits, the woods and the protected areas are?

  tourism-office      promotes a place or informs its visitors. The office
                      itself is not the reader; it recommends to the reader.
  nature-park         runs or interprets a protected area, which is the
                      atlas's own subject matter.
  mountain-transport  carries walkers up. Publishes route information as a
                      side effect of selling the ride.
  outdoor-guide       guides walkers, or keeps the marked routes.

**The name casts the net and the purpose grades the catch.** `*outdoor*` answers
with 147 companies nationally and most are gyms, grills, clothing and billboard
advertising. `*destination*` answers with 41 and some are travel wholesalers.
Neither is separable by name, and both are separable by purpose.

**One band per company, by precedence**, which is a choice and not a finding. A
mountain railway that also runs its destination organisation is genuinely both,
and `intel.segment` would hold both. The ladder picks the function that advises
a visitor, because that is the one a message would be written to: park, then
office, then transport, then guide.

**Every band is graded against an anchor**, the way `regie-core` is graded
against a property word. `Betrieb` alone is the purpose of every company in the
country, and `promotion` alone is the purpose of every marketing agency.

Labels are written to `intel.segment` with `source = 'manual'`, which is what
stops a later `profile` run overwriting them.

    uv run python scripts/classify_tourism.py --dry-run
    uv run python scripts/classify_tourism.py --dry-run --show 8
    uv run python scripts/classify_tourism.py
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from arpenteur import db  # noqa: E402
from arpenteur.config import load_settings  # noqa: E402
from arpenteur.modules.scout import store as scout_store  # noqa: E402
from arpenteur.sources.purpose import fold, head, near  # noqa: E402

BANDS = ("nature-park", "tourism-office", "mountain-transport", "outdoor-guide")

# Patterns are written without accents because `head()` folds the purpose before
# anything reads it, so `Tourismusförderung` arrives as `Tourismusforderung` and
# a pattern spelling the umlaut would never match.

# The gate: is this purpose about the trade at all. Permissive on purpose, since
# the band patterns below carry the discrimination; what this rejects is the
# advertising firm, the gym and the grill shop that the name search caught.
SUBJECT = re.compile(
    r"tourisme|tourismus|turismo|tourist|touristi"
    r"|\bhote(?:s|lerie)?\b|\bgaste\b|besucher|visiteur|visitatori|ospiti"
    r"|montagne|montagna|gebirge|alpin|alpstein|\balpes\b|\balpen\b"
    r"|randonn|wanderweg|wanderroute|sentier|escursion|trekking|bergsteig"
    r"|telepherique|seilbahn|bergbahn|skilift|sesselbahn|remontee|funicul|funivia"
    r"|sciovie|seggiovia|impianti\s+di\s+risalita"
    r"|parc\s+naturel|naturpark|naturzentrum|biosph|riserva|naturschutz"
    r"|reserve\s+naturelle"
    r"|station\s+(?:touristique|de\s+ski|climatique)|domaine\s+skiable|skigebiet"
    r"|loisir|freizeit|erholung|ausflug|excursion",
    re.IGNORECASE,
)

# Each band's anchor, narrower than SUBJECT: the noun the band is about.
TOURISM = re.compile(
    r"tourisme|tourismus|turismo|tourist|touristi"
    r"|\bhote(?:s)?\b|\bgaste\b|besucher|visiteur|ospiti"
    r"|station\s+(?:touristique|climatique)|destination",
    re.IGNORECASE,
)

NATURE = re.compile(
    r"parc\s+naturel|parco\s+naturale|naturpark|naturzentrum|naturerlebnis"
    r"|biosph|reserve\s+naturelle|riserva\s+naturale|naturschutzgebiet"
    r"|naturschutz|patrimoine\s+naturel|naturerbe"
    r"|parc\s+national|nationalpark|geopark|pro\s+natura",
    re.IGNORECASE,
)

CABLE = re.compile(
    r"telepherique|telecabine|seilbahn|luftseilbahn|standseilbahn|sesselbahn"
    r"|gondelbahn|bergbahn|sportbahn|skilift|schlepplift|remontee"
    r"|remonte[-\s]?pente|funicul|funivia|funivie|sciovie|seggiovia"
    r"|impianti\s+di\s+risalita"
    r"|transport\s+(?:a\s+)?cable|installations?\s+de\s+transport",
    re.IGNORECASE,
)

ROUTE = re.compile(
    r"randonn|wanderweg|wanderroute|wanderland|sentier|escursion|trekking"
    r"|bergsteig|bergfuhrer|guide\s+de\s+montagne|alpinschule|bergschule"
    r"|accompagnateur|accompagnatore|itineraire|itinerari|balisage"
    r"|chemins?\s+pedestres|fusswege|velo|bike|raquette"
    r"|wandern|wandertourismus|langsamverkehr",
    re.IGNORECASE,
)

# What the organisation does with its anchor.
# What a tourism office does with its place. Every generic verb is out: a purpose
# naming `organisation` or `développement` beside the word `touristique` is a
# travel agency as often as an office, and both were in the first run's output.
PROMOTES = re.compile(
    # Stems, not dictionary forms. `Villars Loisirs et Tourisme SA` says
    # "développer et promouvoir le tourisme à Villars-sur-Ollon" and was
    # rejected, because the noun `promotion` was here and the verb was not.
    r"promotion|promouv|promeuv|promozion|promuov|forder|vermarkt"
    r"|information|informazione|accueil|empfang|renseignement"
    r"|marketing|attractivite|rayonnement|accueillir"
    r"|verkehrsbuero|verkehrsburo|office\s+du\s+tourisme|ente\s+turistico"
    r"|societe\s+de\s+developpement|destinationsmanagement",
    re.IGNORECASE,
)

TENDS = re.compile(
    r"gestion|gere|exploitation|exploiter|betrieb|betreiben|tragerschaft"
    r"|verwaltung|bewirtschaftung|valorisation|valoriser|aufwertung"
    r"|sensibilisation|sensibilisierung|vermittlung"
    r"|protection|schutz|erhaltung|conservation|entretien|unterhalt|pflege",
    re.IGNORECASE,
)

CARRIES = re.compile(
    r"\bbau\b|bauen|erstellung|betrieb|betreiben|exploitation"
    r"|exploiter|transport|beforderung|personenbeforderung|erschliessung"
    r"|unterhalt|entretien|gestion|betreibung|esercizio|gestione|costruzione",
    re.IGNORECASE,
)

# Guiding, teaching and keeping a route. `organisation`, `cours`, `location` and
# `exploitation` were here and each of them matched a travel organiser or a
# buvette, so none of them is.
LEADS = re.compile(
    r"guidage|accompagnement|accompagner|accompagnateur|accompagnatore"
    r"|encadrement|fuhrung|fuhren|gefuhrt|begleitung|begleitet"
    r"|bergfuhrer|guide\s+de\s+montagne|alpinschule|bergschule|bergsportschule"
    r"|wanderleiter|balisage|signalisation|markierung|baliser"
    r"|entretien|unterhalt|pflege|ausbau|forderung\s+des\s+wander",
    re.IGNORECASE,
)

# The phrases that settle a tourism office on their own, taken from register
# entries rather than imagined. A body with a public mandate says so: it runs a
# `Tourismusbüro`, it collects a `Kurtaxe` or a `taxe de séjour`, it acts under
# the `législation sur le tourisme`. None of these appears in a travel agency.
STRONG_OFFICE = re.compile(
    r"tourismusburo|gasteinformation|gastebetreuung|kurtaxe|kurtaxen"
    r"|beherbergungsabgabe|tourismusforderung|tourismusforderungsabgabe"
    r"|destinationsmanagement|tourismusorganisation|verkehrsverein"
    r"|office\s+du\s+tourisme|taxe\s+de\s+sejour|taxes\s+touristiques"
    r"|legislation\s+sur\s+le\s+tourisme|promotion\s+touristique"
    r"|promozione\s+turistica|promozione\s+del\s+turismo|ente\s+turistico"
    r"|ufficio\s+turistico|societe\s+de\s+developpement",
    re.IGNORECASE,
)

# A regional nature park recites the federal act that created the category, and
# the recital is close to a fingerprint: the act, a perimeter, and the communes
# that joined.
STRONG_PARK = re.compile(
    r"regionaler\s+naturpark|naturpark|parc\s+naturel|parc\s+national"
    r"|nationalpark|parco\s+naturale|naturzentrum|naturerlebniszentrum",
    re.IGNORECASE,
)

# What the name search catches and this trade is not.
#
# `tourisme` alone sits in travel agencies, medical tourism, coach operators,
# car dealers called Gran Turismo and hotel schools. `Seilbahn` alone sits in
# the firms that build and service lifts, Doppelmayr among them, rather than
# the firms that run one. `Wanderwege` and `sentiers pédestres` sit in
# trail-building and landscaping contractors. Each of those is a family, not a
# stray row, so each is named here instead of being corrected one company at a
# time.
NOT_THIS = re.compile(
    r"agen(?:ce|t)\s+de\s+voyage|\bvoyages?\b|voyagiste|forfaits?\s+touristique"
    r"|tour[-\s]?operat|reisebuero|reiseburo|\breisen\b|reisevermittlung"
    r"|agenzia\s+(?:di\s+)?viaggi|\bviaggi\b|viaggi\s+e\s+turismo"
    r"|transport\s+de\s+personnes|personentransport|autocar|\btaxi\b|chauffeur"
    r"|tourisme\s+medical|medizinischer\s+tourismus|medical\s+tourism"
    r"|gran\s+turismo|handelsschule|ecole\s+de\s+commerce"
    r"|herstellung|fabrication|montage\s+von|seilbahnbau|revision\s+von"
    r"|wartung\s+von|installateur|ingenieurbuero"
    r"|espaces\s+verts|paysagiste|jardinier|gartenbau|gartenunterhalt"
    r"|tiefbau|holzbau|zimmerei|forstunternehmen|felssicherung|seilarbeiten"
    r"|baumpflege|elagage"
    # Travel sold as a package. `\breisen\b` missed `Trekkingreisen`, which is
    # one word in German, so the boundary is on the right only.
    r"|reisen\b|reiseveranstalt|wanderreise|trekkingreise"
    # Property development that happens to name tourism, and the food trade that
    # happens to be called Les Sentiers something.
    r"|maitrise\s+d.ouvrage|promotion\s+immobiliere|denrees\s+alimentaires"
    r"|boissons|\bvins\b|importation|exportation",
    re.IGNORECASE,
)


def grade(purpose: str, name: str) -> tuple[str, str] | None:
    """The band and the reason, or None when this is not one of the four.

    Read from the head of the purpose alone, before the permissions clause.

    The reason is returned and stored because a label whose grounds are not
    recorded cannot be argued with, and every one of these will be argued with
    the first time a shortlist looks wrong.
    """
    text = head(purpose)
    if not SUBJECT.search(text):
        return None
    if NOT_THIS.search(text):
        return None
    if STRONG_PARK.search(text):
        return "nature-park", "purpose names a park, a reserve or the act behind one"
    if near(TENDS, NATURE, text):
        return "nature-park", "purpose names a protected area and what it does with it"
    if CABLE.search(fold(name or "")) and CARRIES.search(text):
        return "mountain-transport", "named as a lift company, and the purpose runs one"
    if ROUTE.search(fold(name or "")) and near(LEADS, ROUTE, text):
        return "outdoor-guide", "named for the routes, and the purpose keeps them"
    if STRONG_OFFICE.search(text):
        return "tourism-office", "purpose names a mandate only a tourism office holds"
    if near(PROMOTES, TOURISM, text):
        return "tourism-office", "purpose names promoting or informing about a place"
    if near(CARRIES, CABLE, text):
        return "mountain-transport", "purpose names running or maintaining a lift"
    if near(LEADS, ROUTE, text):
        return "outdoor-guide", "purpose names guiding walkers or keeping routes"
    return None


SELECT = """
    SELECT id, uid, name, canton, purpose FROM core.company
     WHERE status = 'ACTIVE' AND hydrated_at IS NOT NULL
       AND purpose IS NOT NULL AND purpose <> ''
     ORDER BY id
"""

WORDS_FILE = "words/drymos.txt"


def name_pattern(path: Path) -> re.Pattern[str]:
    """The words the register was searched with, as one pattern over a name.

    Read from the file `arpenteur source --name-file` uses, so what is graded is
    what was collected. A second list written here would disagree with that one
    inside a week, and the disagreement would be invisible.
    """
    words = []
    for line in path.read_text(encoding="utf-8").splitlines():
        word = line.split("#", 1)[0].strip().strip("*")
        if word:
            words.append(re.escape(fold(word)))
    return re.compile("|".join(dict.fromkeys(words)), re.IGNORECASE)


def in_cohort(name: str, text: str, names: re.Pattern[str]) -> bool:
    """Whether this company belongs to the population being graded.

    The first run graded all 33'212 hydrated companies and the output was mostly
    Geneva service firms: a landscaper on `paysage`, a travel agent on
    `forfaits touristiques`, a sports agency on `encadrement`. The rules were
    loose, and they have been tightened, but the deeper fault was the scope. The
    cohort is what the name search went looking for.

    A purpose carrying one of the strong phrases is admitted whatever its name,
    because `Tourismusbüro` and `taxe de séjour` are not words another trade
    uses, and that is how a destination organisation with a name like
    Schilthornbahn still arrives.
    """
    if names.search(fold(name or "")):
        return True
    return bool(STRONG_OFFICE.search(text) or STRONG_PARK.search(text))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="count, write nothing")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--words",
        default=WORDS_FILE,
        help=f"the name words the cohort was collected with (default {WORDS_FILE})",
    )
    ap.add_argument(
        "--show",
        type=int,
        default=0,
        help="print this many graded companies per band, for reading by hand",
    )
    args = ap.parse_args()

    settings = load_settings()
    conn = db.open_db(settings.database_url)
    sql = SELECT + (f" LIMIT {int(args.limit)}" if args.limit else "")
    rows = conn.execute(sql).fetchall()

    names = name_pattern(Path(settings.root) / args.words)

    tally: Counter[str] = Counter()
    samples: dict[str, list[tuple[str, str, str]]] = {band: [] for band in BANDS}
    written = 0
    for row in rows:
        if not in_cohort(row["name"] or "", head(row["purpose"] or ""), names):
            tally["outside the cohort"] += 1
            continue
        verdict = grade(row["purpose"] or "", row["name"] or "")
        if verdict is None:
            tally["not this trade"] += 1
            continue
        band, why = verdict
        tally[band] += 1
        if len(samples[band]) < args.show:
            samples[band].append(
                (row["name"] or "", row["canton"] or "??", (row["purpose"] or "")[:220])
            )
        if not args.dry_run:
            # Every other band is cleared first, so re-running after a purpose
            # amendment moves a company rather than leaving it in two bands.
            for other in BANDS:
                if other != band:
                    scout_store.set_segment_manually(
                        conn, int(row["id"]), other, present=False
                    )
            scout_store.set_segment_manually(
                conn, int(row["id"]), band, rationale=why, author="classify_tourism"
            )
            written += 1
            if written % 200 == 0:
                conn.commit()
    if not args.dry_run:
        conn.commit()

    print(f"read {len(rows):,} hydrated companies\n")
    for band in BANDS:
        print(f"  {band:20} {tally[band]:>6,}")
    print(f"  {'not this trade':20} {tally['not this trade']:>6,}")
    print(f"  {'outside the cohort':20} {tally['outside the cohort']:>6,}")
    print(f"\nwritten: {written:,}" if not args.dry_run else "\n(dry run, nothing written)")

    for band in BANDS:
        if not samples[band]:
            continue
        print(f"\n--- {band} ---")
        for company, canton, purpose in samples[band]:
            print(f"  {canton}  {company}")
            print(f"      {purpose}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
