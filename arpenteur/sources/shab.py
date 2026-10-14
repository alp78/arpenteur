"""SHAB notices: reading officers out of the commercial register's own prose.

Zefix has no officer field. It does carry `sogcPub`, the Feuille officielle
suisse du commerce notices filed on every mutation, and those name people in a
consistent registrar's French:

    Associé-gérant: Turbé Christian, de France, à Genève, pour 200 parts de
    CHF 100, avec signature individuelle.

This module turns that text into rows. It is pure functions over strings with
no I/O, so it tests against recorded notices and a better parser never needs a
new fetch.

The grammar, as measured over 120 notices across the five romand cantons on
11.08.2026:

  role label      gendered and pluralised without limit: Associé, Associée,
                  Associés, Associé-gérant, Associée-gérante, Gérants,
                  Nouveaux gérants, Liquidateurs.
  several people  one clause, joined by "et", "," or "ainsi que".
  back-reference  "Gérant: l'associé Alanbagi Abdelhadi" names someone already
                  introduced above, without repeating their origin.
  trailing scope  "tous deux avec signature individuelle" applies to every
                  person in the clause, not just the last.

The trap that shapes the whole design: "Communication aux associés: par
lettre, téléfax ou email" is a role label followed by a *communication method*,
and it appeared 42 times against 92 real name clauses. Keying on the label
alone would have made a third of the output fictional people. So a candidate is
only accepted when the text after the label is shaped like a name, and the
roster of non-role labels below is refused outright.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Iterator

# Labels that look like roles but never introduce a person. "Communication aux
# associés" is the dangerous one; the rest are ordinary notice sections that
# happen to sit in the same "Label: value" shape.
NOT_A_ROLE = re.compile(
    r"^(?:communications?\s+aux\s+associ|organe\s+de\s+publication|but\b"
    r"|capital|statuts?|date\s+des\s+statuts|nouveau\s+but|nouvelle?\s+adresse"
    r"|nouveau\s+si[èe]ge|autre\s+adresse|modification"
    # "Personnes radiées" lists officers who have *left*. Recording them as
    # current would be worse than missing them: the tool would name the wrong
    # person to approach, with the register cited as the authority.
    r"|personnes?\s+radi[ée]es?|personnes?\s+sortantes?)",
    re.IGNORECASE,
)

# A shareholder or officer can be another company. Those are real register
# entries but they are not people to write to, and storing them in `person`
# would put a legal entity where the draft stage expects a human name.
LEGAL_ENTITY = re.compile(
    r"\b(?:s[àa]rl|s\.?a\.?|ag|gmbh|sagl|scrl|holding|group(?:e)?|association"
    r"|fondation|coop[ée]rative|succursale|trust|ltd|inc|llc)\b\.?$",
    re.IGNORECASE,
)

# The officer vocabulary, allowing the gender and number suffixes the register
# actually uses, plus the "Nouveau"/"Autre" prefixes that mark a mutation.
#
# Compound forms are listed before their own prefixes. Regex alternation is
# first-match-wins, so putting "associé" before "associé-gérant" would match
# the shorter one and leave "-gérant:" stranded, losing the role entirely.
#
# There is deliberately no sentence anchor. Labels appear mid-sentence after a
# full stop, a semicolon or nothing at all, and requiring a boundary matched
# none of the 120 recorded notices.
ROLE_LABEL = re.compile(
    r"((?:nouvelles?\s+|nouveaux\s+|autres?\s+)?"
    r"(?:associ[ée]e?s?-g[ée]rant(?:e)?s?|g[ée]rant(?:e)?s?|associ[ée]e?s?"
    r"|administrat(?:eur|rice)s?|liquidat(?:eur|rice)s?"
    r"|vice-pr[ée]sident(?:e)?s?|pr[ée]sident(?:e)?s?"
    r"|direct(?:eur|rice)s?|secr[ée]taires?|r[ée]viseurs?"
    r"|personnes?\s+inscrites?(?:\s+modifi[ée]es?)?)"
    r")\s*:\s*",
    re.IGNORECASE,
)

# "Surname Firstname" — two capitalised words, allowing particles and the
# compound given names that are common here ("Blanc Dimitri Albert Gérard").
# Anchored on capitalisation because that is what separates a person from
# "par lettre, téléfax ou email".
NAME = re.compile(
    r"(?:l['’]associ[ée]\s+|les\s+associ[ée]s\s+|l['’])?"
    r"((?:[A-ZÀ-Ý][\wÀ-ÿ'’-]+|d[ei]|van|von|de|da|dos|del|le|la)"
    r"(?:\s+(?:[A-ZÀ-Ý][\wÀ-ÿ'’-]+|d[ei]|van|von|de|da|dos|del|le|la)){1,4})"
)

SIGNATURE = re.compile(
    r"signature\s+(individuelle|collective\s+à\s+deux|collective\s+à\s+trois"
    r"|collective|sociale)",
    re.IGNORECASE,
)

# "de France, à Genève" / "de et à Genève" / "de Carouge (GE), à Lancy" /
# "d'Arabie Saoudite, à Genève".
#
# `_PLACE_END` is where a place name stops, and it is not only punctuation.
# The register writes "à Carouge GE avec signature individuelle" without a
# comma, and a pattern that only stopped at one swallowed the signature into
# the domicile. It also has to stop a place before "pour 20 parts".
_PLACE = r"[A-ZÀ-Ý][\wÀ-ÿ'’ ()-]{1,40}?"
_PLACE_END = r"(?=[,.;]|\s+(?:avec|pour|qui|dont|qu[ie]|en\s+qualit[ée])\b|$)"

ORIGIN_DOMICILE = re.compile(
    rf"\bde\s+et\s+à\s+({_PLACE}){_PLACE_END}"
    # The elided article is ordinary French and half the countries take it:
    # "d'Ethiopie", "d'Arabie Saoudite", "d'Italie". Without it the origin was
    # missed and the country was then read as a person's name.
    rf"|\bd(?:[eu]\s+|['’]\s*)({_PLACE})\s*,\s*à\s+({_PLACE}){_PLACE_END}",
    re.IGNORECASE,
)

# Words that can start a capitalised run but are never a surname. Without this
# "Selon déclaration" and "Date des statuts" read as people.
STOPWORDS = {
    "selon", "date", "statuts", "capital", "but", "la", "le", "les", "société",
    "sàrl", "sa", "suisse", "communication", "communications", "organe",
    "publication", "feuille", "officielle", "commerce", "nouvelle", "nouveau",
    "nouveaux", "modification", "inscription", "radiation", "transfert",
    "signature", "individuelle", "collective", "président", "présidente",
    "parts", "chf", "avec", "sans", "tous", "toutes", "deux", "trois", "et",
}


# A departure is written as a sentence about the person, not as a role label:
# "Pascual Carlos n'est plus associé ni gérant; ses pouvoirs sont radiés".
# It appeared in 28 of 139 notices. Matching the name that *precedes* this
# phrase is the only way to tell a departure from an appointment, because the
# role vocabulary on both sides is identical.
DEPARTURE = re.compile(
    r"([A-ZÀ-Ý][\wÀ-ÿ'’-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'’-]+){1,4})"
    r"\s+(?i:n['’]est\s+plus|ne\s+sont\s+plus|"
    r"a\s+d[ée]missionn[ée]|ont\s+d[ée]missionn[ée])",
)


# A status statement without a role label: "Adriaansen Sarina est maintenant
# associée", "au gérant Chubatyuk Andriy maintenant associé". These are the
# register's way of recording a role change, and they matter because the same
# person is often removed and reinstated in adjacent notices. 11 of 139
# notices used this form. The role follows the name here, where the labelled
# grammar puts it before.
REINSTATEMENT = re.compile(
    # The sentence often leads with the person's previous role ("au gérant
    # Chubatyuk Andriy maintenant associé"), which must not become part of
    # the name. Consumed outside the capture group.
    r"(?:\b(?:au|à|le|la|l['’]|du|de\s+la)\s+)?"
    r"(?:(?:associ[ée]e?|g[ée]rante?|administrat(?:eur|rice)|pr[ée]sidente?)\s+)?"
    # (?!est\b) stops the name from swallowing the copula: without it
    # "Adriaansen Sarina est maintenant" captures "Adriaansen Sarina est".
    r"((?!est\b)[A-ZÀ-Ý][\wÀ-ÿ'’-]+(?:\s+(?!est\b)[A-ZÀ-Ý][\wÀ-ÿ'’-]+){1,4})"
    r"\s+(?:est\s+)?maintenant\s+"
    r"(associ[ée]e?|g[ée]rante?|administrat(?:eur|rice)|pr[ée]sidente?)",
    re.IGNORECASE,
)


@dataclass
class Officer:
    """One person named in a notice, with what the notice said about them."""

    full_name: str
    role: str | None = None
    origin: str | None = None
    domicile: str | None = None
    signature: str | None = None
    # When the register first and last named them in this role, and the date of
    # the notice that announced they had gone. Filled by `from_publications`,
    # which is the only caller that sees more than one notice; a single notice
    # cannot say when something started.
    first_seen: str | None = None
    last_seen: str | None = None
    left_on: str | None = None

    def key(self) -> tuple[str, str]:
        """Identity for deduplication: name and role, accent- and case-folded.

        The same person recurs across a company's notices, sometimes with an
        accent typed differently. Folding here keeps one row per human.
        """
        return (fold(self.full_name), fold(self.role or ""))


def fold(text: str) -> str:
    """Casefold and strip accents, for comparison only, never for storage."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


# Mojibake sequences seen in the register's own archive: UTF-8 bytes that were
# decoded as Latin-1 somewhere upstream before we ever saw them. Measured on
# 11.08.2026, 6 of 139 notices carried these, and always mixed with correctly
# encoded text in the same string, which rules out a fault on our side.
MOJIBAKE = {
    "Ã©": "é", "Ã¨": "è", "Ãª": "ê", "Ã«": "ë",
    "Ã ": "à", "Ã¢": "â", "Ã´": "ô", "Ã®": "î", "Ã¯": "ï",
    "Ã»": "û", "Ã¹": "ù", "Ã§": "ç", "Ã¼": "ü", "Ã¶": "ö", "Ã¤": "ä",
}


def repair_encoding(text: str) -> str:
    """Undo double-encoded accents that arrive corrupt from the register.

    Applied per known sequence rather than by re-decoding the whole string:
    the corruption is partial, so "DAPLOÃD Sàrl" has a broken byte and a
    correct one side by side, and a blanket latin-1 round trip raises on the
    valid half.
    """
    if "Ã" not in text:
        return text
    for broken, correct in MOJIBAKE.items():
        text = text.replace(broken, correct)
    return text


def plain_text(message: str) -> str:
    """Strip the notice's HTML, repair upstream mojibake, normalise whitespace.

    Notices arrive wrapped in <FT> layout tags. Entities are unescaped after
    tag removal so an escaped angle bracket in the prose is not mistaken for
    markup.
    """
    without_tags = re.sub(r"<[^>]+>", " ", message)
    unescaped = html.unescape(without_tags)
    return re.sub(r"\s+", " ", repair_encoding(unescaped)).strip()


def normalise_role(label: str) -> str:
    """Reduce a gendered, pluralised, prefixed label to a canonical role.

    "Nouveaux gérants", "Gérant" and "gérante" are one role. Keeping the
    surface form would fragment every later filter and count.
    """
    text = fold(label)
    text = re.sub(r"^(?:nouvelles?|nouveaux|autres?)\s+", "", text)
    if "associ" in text and "gerant" in text:
        return "associé-gérant"
    for stem, canonical in (
        ("associ", "associé"),
        ("gerant", "gérant"),
        ("administrat", "administrateur"),
        ("liquidat", "liquidateur"),
        ("vice-president", "vice-président"),
        ("president", "président"),
        ("directeur", "directeur"),
        ("directrice", "directeur"),
        ("secretaire", "secrétaire"),
        ("reviseur", "réviseur"),
        ("personne", "personne inscrite"),
    ):
        if text.startswith(stem) or stem in text:
            return canonical
    return text or "?"


def _looks_like_name(candidate: str) -> bool:
    """Reject capitalised runs that are prose rather than a person.

    The register writes names as "Surname Firstname". Anything whose first
    word is a stopword, or which carries no lowercase-free capitalised token,
    is notice prose that happened to start a sentence.
    """
    words = candidate.split()
    if len(words) < 2:
        return False
    if fold(words[0]) in STOPWORDS:
        return False
    if any(fold(w) in STOPWORDS for w in words[:2]):
        return False
    # A company can hold shares or a mandate. It belongs in the register but
    # not in `person`, which the draft stage reads expecting a human.
    if LEGAL_ENTITY.search(candidate):
        return False
    # At least two words must start uppercase; particles like "de" do not.
    capitalised = sum(1 for w in words if w[:1].isupper())
    return capitalised >= 2


def _clause_bounds(text: str) -> Iterator[tuple[str, str]]:
    """Yield (role label, clause) for each officer section of a notice.

    A clause runs from its label to the next label or the end of the sentence,
    because that is the span over which "tous deux avec signature individuelle"
    applies.
    """
    matches = list(ROLE_LABEL.finditer(text))
    for index, match in enumerate(matches):
        label = match.group(1)
        if NOT_A_ROLE.match(label.strip()):
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        clause = text[start:end]
        # A clause ends at the first full stop that is not inside an
        # abbreviation; beyond that the notice has moved on to another topic.
        stop = re.search(r"\.\s+[A-ZÀ-Ý]", clause)
        if stop:
            clause = clause[: stop.start()]
        yield label, clause


def departures(text: str) -> set[str]:
    """Names the notice says have left, folded for comparison.

    A notice announcing a departure usually also names the incoming officer
    and restates the remaining ones, so the departing name has to be removed
    by name rather than by skipping the notice.
    """
    return {fold(match.group(1)) for match in DEPARTURE.finditer(text)}


def officers(message: str) -> list[Officer]:
    """Every person a single notice names as currently holding a role.

    Returns an empty list rather than raising for a notice that names nobody:
    most notices are address or capital changes, and that is not an error.

    People the notice announces as departing are excluded. Recording them
    would be worse than missing them, because the draft stage would then
    address someone who left, citing the federal register as the source.
    """
    text = plain_text(message)
    # A role change is written as a removal and a re-addition in the same
    # notice ("Dupont Marie n'est plus gérante. Dupont Marie est maintenant
    # associée"). The reinstatement wins, so it is resolved before the
    # departures are applied.
    reinstated = {fold(m.group(1)) for m in REINSTATEMENT.finditer(text)}
    gone = departures(text) - reinstated
    found: list[Officer] = []
    seen: set[tuple[str, str]] = set()

    for label, clause in _clause_bounds(text):
        role = normalise_role(label)
        # The signature clause is written once for the whole clause
        # ("tous deux avec signature individuelle"), so it is read per clause
        # and applied to everyone in it.
        signature_match = SIGNATURE.search(clause)
        signature = (
            re.sub(r"\s+", " ", signature_match.group(1)).lower()
            if signature_match
            else None
        )

        # Where the clause states someone's origin and domicile, so those
        # places are not read as further people. "Aslan Arzu, de Turquie, à
        # Carouge GE" names one officer, and the name pattern matched two:
        # `Carouge GE` is a capitalised run of two words like any surname and
        # first name. Left alone it accumulated across the survey into an
        # officer sitting on twenty-three boards, which is exactly the shape
        # of a well-connected person and would have become the most valuable
        # node in a relationship graph built on nothing.
        taken = [
            span
            for match in ORIGIN_DOMICILE.finditer(clause)
            for span in (match.span(1), match.span(2), match.span(3))
            if span[0] >= 0
        ]

        # The people this clause names, in the order it names them. Collected
        # before any place is read, because where one person's description
        # stops is where the next one's name starts and that cannot be known
        # while still scanning.
        named = []
        for name_match in NAME.finditer(clause):
            candidate = name_match.group(1).strip(" ,;")
            if not _looks_like_name(candidate):
                continue
            start, end = name_match.span(1)
            if any(start < place_end and place_start < end
                   for place_start, place_end in taken):
                continue
            named.append((name_match, candidate))

        for index, (name_match, candidate) in enumerate(named):
            if fold(candidate) in gone:
                continue
            origin = domicile = None
            # Bounded by the next person named, not by a fixed window. The
            # register introduces someone once and afterwards refers to them
            # without repeating their place: "Gérants: l'associé Boccard
            # Christian, président et Serodino Christophe, d'Onsernone, à
            # Thônex". Reading 120 characters past the first name gave Boccard
            # the second man's origin and domicile, and party resolution keyed
            # on those would then have split one person into two, or worse,
            # merged two into one.
            stop = (
                named[index + 1][0].start(1) if index + 1 < len(named)
                else len(clause)
            )
            after = name_match.end(1)
            tail = clause[after : min(stop, after + 120)]
            place = ORIGIN_DOMICILE.search(tail)
            if place:
                if place.group(1):  # "de et à Genève"
                    origin = domicile = place.group(1).strip()
                else:
                    origin = (place.group(2) or "").strip() or None
                    domicile = (place.group(3) or "").strip() or None

            officer = Officer(
                full_name=candidate,
                role=role,
                origin=origin,
                domicile=domicile,
                signature=signature,
            )
            if officer.key() in seen:
                continue
            seen.add(officer.key())
            found.append(officer)

    # Role changes stated without a label. Run after the labelled clauses so a
    # person named both ways keeps the richer attributes from the clause.
    for match in REINSTATEMENT.finditer(text):
        candidate = match.group(1).strip(" ,;")
        if not _looks_like_name(candidate) or fold(candidate) in gone:
            continue
        officer = Officer(full_name=candidate, role=normalise_role(match.group(2)))
        if officer.key() in seen:
            continue
        seen.add(officer.key())
        found.append(officer)

    return found


def from_publications(
    publications: list[dict[str, Any]], *, include_past: bool = False
) -> list[Officer]:
    """Merge the officers named across a company's notices, newest first.

    Notices are processed newest first so the most recent statement of a
    person's role and signature wins. An officer who only ever appears in an
    old notice is still returned: the register does not restate unchanged
    officers on every mutation, so absence from the latest notice is not
    evidence of departure.

    Each officer comes back with the dates the notices give: the oldest notice
    naming them in that role, the newest, and — for those who left — the date
    of the notice that said so. That is what makes this board history rather
    than a snapshot, and it costs nothing extra because the walk already visits
    every notice in order.

    `include_past` decides whether the people who have gone come back too. It
    is off by default and every caller that addresses a human leaves it off:
    recording a departed officer is worse than missing them, because a draft
    would then be addressed to somebody who left, citing the federal register.
    """
    ordered = sorted(
        publications,
        key=lambda p: (p.get("sogc_date") or p.get("sogcDate") or ""),
        reverse=True,
    )
    merged: dict[tuple[str, str], Officer] = {}
    spans: dict[tuple[str, str], list[str | None]] = {}
    departed: dict[tuple[str, str], str | None] = {}
    # Someone who left is gone from every earlier notice too. Walking newest
    # first and accumulating departures means a 2022 "n'est plus associé"
    # suppresses the 2021 notice that appointed them, without needing to
    # understand the register's full mutation history.
    gone: set[str] = set()
    # When each departure was announced, taken from the newest notice that
    # says so. Somebody appointed, gone and appointed again is already current
    # by the time the older departure is read, so they are not marked.
    left: dict[str, str | None] = {}
    for publication in ordered:
        message = publication.get("message") or ""
        stamp = publication.get("sogc_date") or publication.get("sogcDate") or None
        text = plain_text(message)
        for officer in officers(message):
            key = officer.key()
            if key in merged:
                # Walking backwards, so every later sighting is older than the
                # one before it and the last write is the first appearance.
                spans[key][0] = stamp or spans[key][0]
                continue
            merged[key] = officer
            spans[key] = [stamp, stamp]
            if fold(officer.full_name) in gone:
                departed[key] = left.get(fold(officer.full_name))
        # Applied after this notice's own officers, so a notice that both
        # removes and reappoints a person (a role change) keeps them. A name
        # reinstated in this notice is not carried back as departed, because
        # the register removes and re-adds the same person on a role change.
        reinstated = {fold(m.group(1)) for m in REINSTATEMENT.finditer(text)}
        for name in departures(text) - reinstated:
            left.setdefault(name, stamp)
        gone |= departures(text) - reinstated

    out = []
    for key, officer in merged.items():
        first, last = spans[key]
        if key in departed and not include_past:
            continue
        out.append(
            replace(
                officer, first_seen=first, last_seen=last,
                left_on=departed.get(key),
            )
        )
    return out
