"""Which language to write to a firm in, read from what it calls itself.

Parsing only, no I/O.

`draft.language_of` places a firm by its canton's majority language, which is
right in Zurich and wrong at every bilingual edge. Measured on the tourism
cohort, 21 of the 38 reachable firms in Valais carry German names
(`Blatten-Belalp Tourismus AG`, `Bergbahnen Hohsaas AG`) and would have
received a French letter. A firm's registered name is written in its own
language, so the name is the first evidence and the canton the last.

**Three kinds of evidence, in this order of trust.**

  name        A trade or legal word only one language uses: `Tourismus`,
              `Remontées`, `Funivia`, `Genossenschaft`, `Société`. Then the
              function words a name is built with: `und`, `de`, `della`.
  legal form  The suffix in the name. `AG` and `GmbH` are German, `Sàrl`
              French, `Sagl` Italian. `SA` is French or Italian, and the canton
              decides which.
  canton      The majority language, which `draft.language_of` also uses.

**Words both sides use are not evidence.** `Skilift` names a lift in Fribourg's
French-speaking Gruyère (`Skilifts Rathvel S.A.`) as readily as in Bern, and
`Tourist` is everyone's. Only words one language owns are listed.

**`legal_form_label` is not used.** The register stores the French catalogue
label for every firm whatever its language, so `Société anonyme` sits on a
Zurich AG as well. The suffix has to be read from the name.

Every guess names its evidence, so a letter in the wrong language can be traced
to the word that chose it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .purpose import fold

# The cantons by majority language, the same placement `draft.language_of`
# makes. Repeated here because a source may not import a stage; a test holds
# the two together.
ROMAND = frozenset({"GE", "VD", "NE", "JU", "FR", "VS"})
ITALIAN = frozenset({"TI"})

# Where the canton alone cannot be trusted, because two languages are each
# spoken by a large share: Bern with its Jura bernois, Fribourg and Valais on
# either side of the Sarine and the Raspille, Grisons with three.
BILINGUAL = frozenset({"BE", "FR", "VS", "GR"})

# The official languages of each of those cantons, the majority's first. A
# campaign that writes a canton in all of them follows the firm's own language
# with the others in this order.
OFFICIAL: dict[str, tuple[str, ...]] = {
    "BE": ("de", "fr"),
    "FR": ("fr", "de"),
    "VS": ("fr", "de"),
    "GR": ("de", "rm", "it"),
}

# Stems matched inside a word, because German writes its trades as compounds:
# `Luftseilbahn`, `Skiliftgenossenschaft`, `Tourismusverein`.
STEMS: dict[str, tuple[str, ...]] = {
    "de": (
        "tourismus", "touristisch", "bahn", "genossenschaft", "verein",
        "stiftung", "verband", "wanderweg", "bergfuhrer", "bergsteig",
        "alpinschule", "skischule", "naturpark", "ferien", "verkehrs",
        "erlebnis", "skilifte", "gemeinde",
    ),
    "fr": (
        "tourisme", "touristique", "remontee", "telesk", "telecabine",
        "telesiege", "telepherique", "funiculaire", "societe", "association",
        "fondation", "cooperative", "developpement", "randonnee",
    ),
    "it": (
        "turismo", "turistic", "funivi", "sciovi", "seggiovi", "impiant",
        "associazione", "fondazione", "cooperativa", "societa", "escursion",
    ),
    "rm": ("turissem", "pendicular", "societad"),
}

# Whole words only: short enough that a stem would match inside other words.
# `parc` sits inside `parco`, `sentier` inside `sentieri`.
WORDS: dict[str, frozenset[str]] = {
    "de": frozenset({"und", "der", "die", "das", "fur", "im", "am"}),
    "fr": frozenset({"de", "des", "du", "et", "les", "parc", "sentier", "sentiers",
                     "freres", "naturel"}),
    "it": frozenset({"di", "del", "della", "dei", "delle", "parco", "sentieri",
                     "ente", "alpini"}),
    "rm": frozenset(),
}

# The suffix a name carries, and how it is printed back as evidence. `SA` is
# left open: French in the west, Italian in Ticino and the Italian valleys of
# Grisons.
SUFFIXES: dict[str, tuple[str | None, str]] = {
    "ag": ("de", "AG"), "gmbh": ("de", "GmbH"), "sarl": ("fr", "Sàrl"),
    "sagl": ("it", "Sagl"), "sa": (None, "SA"),
}

_TOKEN = re.compile(r"[a-z]+")


@dataclass(frozen=True)
class Guess:
    language: str          # fr | de | it | rm
    basis: str             # name | legal form | canton
    evidence: str          # the word, the suffix or the canton that decided
    certain: bool

    @property
    def label(self) -> str:
        """What the console shows: `name: Bergbahnen`, `canton: VS`."""
        return f"{self.basis}: {self.evidence}"


def by_canton(canton: str | None) -> str:
    canton = (canton or "").upper()
    if canton in ROMAND:
        return "fr"
    if canton in ITALIAN:
        return "it"
    return "de"


def _tokens(name: str) -> list[str]:
    return _TOKEN.findall(fold(name or "").lower())


def _markers(tokens: list[str]) -> dict[str, list[str]]:
    """Every language the name's own words point at, with the words."""
    found: dict[str, list[str]] = {}
    for language in STEMS:
        hits = [
            token for token in tokens
            if token in WORDS[language]
            or any(stem in token for stem in STEMS[language])
        ]
        if hits:
            found[language] = hits
    return found


def _suffix(tokens: list[str], canton: str | None) -> tuple[str, str] | None:
    """The language a legal suffix in the name points at, with the suffix.

    `S.A.` folds to the two tokens `s` and `a`, so it is read back together.
    """
    joined = " ".join(tokens)
    for suffix, (language, printed) in SUFFIXES.items():
        if suffix == "ag" and re.search(r"(?:^| )a g(?: |$)", joined):
            return language, printed                  # `A.G.`, folded to two tokens
        if suffix != "sa":
            if suffix in tokens:
                return language, printed
            continue
        if not re.search(r"(?:^| )(?:sa|s a)(?: |$)", joined):
            continue
        canton = (canton or "").upper()
        if canton in ITALIAN:
            return "it", printed
        if canton == "GR":
            # The Italian valleys of Grisons use SA, and so do its German
            # and Romansh firms: the suffix settles nothing there.
            return None
        return "fr", printed
    return None


def guess(name: str, canton: str | None) -> Guess:
    """The language a firm writes in, and what says so.

    A name whose words point at one language decides, certainly. A name whose
    words point at two decides by the larger count, uncertainly, and a tie
    falls through to the suffix. The canton is the last resort, and certain
    only where one language is spoken by nearly everyone.
    """
    tokens = _tokens(name)
    markers = _markers(tokens)
    if markers:
        ranked = sorted(markers.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        best, words = ranked[0]
        tied = len(ranked) > 1 and len(ranked[1][1]) == len(words)
        if not tied:
            # A name that contradicts the majority of a one-language canton is
            # kept but not trusted: `European Outdoor Conservation Association`
            # in Zug reads as French on a word English shares. In a bilingual
            # canton the contradiction is ordinary, and is no reason to doubt.
            code = (canton or "").upper()
            odd = bool(code) and code not in BILINGUAL and best != by_canton(code)
            return Guess(best, "name", words[0],
                         certain=len(ranked) == 1 and not odd)
    suffix = _suffix(tokens, canton)
    if suffix is not None:
        language, which = suffix
        return Guess(language, "legal form", which, certain=False)
    code = (canton or "").upper() or "?"
    return Guess(by_canton(canton), "canton", code,
                 certain=bool(canton) and code not in BILINGUAL)
