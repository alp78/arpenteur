"""Reading a registered purpose: parsing only, no I/O.

The register cannot be searched by activity, so a company's `but statutaire` is
the only evidence of what it does, and every campaign that grades companies ends
up reading the same text the same way. Three rules were earned the hard way by
`scripts/classify_regies.py` over twenty thousand Swiss purposes, and they are
here so a second classifier inherits them instead of rediscovering them.

**The permissions clause is cut off.** A Swiss purpose states what the company
is for, then lists what a notary permits it to do. Graded whole, a transport
firm, a jewellery school and a dental practice all read as property companies,
because every one of them may buy premises and every one says so.

**Two words only count when they share a clause.** `AAB Group Sàrl` graded as a
managing agency on "mise en gérance et exploitation d'établissements publics
tels que kiosques", which is the management of kiosks. `A7 Services Sàrl` graded
as a property broker on "courtage dans les domaines des assurances". In both
cases both words were present and they described different things.

**Accents fold before matching.** A purpose is filed in whatever spelling the
cantonal office used, so `gérance` and `gerance` have to be one word to a
pattern.
"""

from __future__ import annotations

import re
import unicodedata

# Where a Swiss registered purpose stops describing the business and starts
# listing what the company is permitted to do.
#
# This clause is the single largest source of false positives, and it is
# boilerplate a notary adds to almost every Sàrl: "elle peut acquérir, détenir
# et aliéner des immeubles". So the text is cut here and only the head is
# graded. What a company is for comes first in a purpose; what it may also do
# comes after one of these.
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

# How close an activity word has to sit to the subject of the campaign before
# the two are read as one statement.
#
# Sixty characters is about a clause. It keeps "gérance d'immeubles" and
# "Verwaltung von Liegenschaften" together while separating two items of a list.
NEAR = 60


def fold(text: str) -> str:
    """Accents away, so `gérance` and `gerance` are one word to a pattern."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(ch)
    )


def head(purpose: str, *, boilerplate: re.Pattern[str] = BOILERPLATE) -> str:
    """The purpose, folded, up to the permissions boilerplate."""
    text = fold(purpose or "")
    cut = boilerplate.search(text)
    return text[: cut.start()] if cut else text


def near(
    activity: re.Pattern[str],
    anchor: re.Pattern[str],
    text: str,
    *,
    window: int = NEAR,
) -> bool:
    """Whether an activity word and a subject word share a clause.

    `anchor` is the gate: the pattern that says the purpose is about this
    campaign's subject at all. Without it `Verwaltung` matches every holding
    company in the country and `Förderung` matches every association.
    """
    spots = [m.start() for m in anchor.finditer(text)]
    if not spots:
        return False
    for hit in activity.finditer(text):
        start = hit.start()
        if any(abs(start - spot) <= window for spot in spots):
            return True
    return False
