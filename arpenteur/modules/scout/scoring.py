"""What a score is made of, and how the parts combine.

Six components, each 0 to 100 on its own scale, weighted into a total. No
database here: the definitions, the arithmetic and the two components that can
be computed from facts rather than judged, so all three can be checked without
a connection in the way.

The reason for six rather than one is that a single figure conflates things
that move independently and want different actions. A company can be:

  an excellent fit nobody can reach          fit 90, reach 0
  a mediocre fit whose chairman knows us     fit 45, relationship 90
  exactly right and two years early          fit 90, timing 10

All three average to something near 60, and 60 tells you to do nothing in
particular. Separated, the first says find a contact, the second says ask for
an introduction, the third says diarise it.

**Two of the six are computed, not judged.** Reachability and relationship are
facts already in this database: how many usable addresses are held, and whether
anybody on the board sits with anybody already being worked. A model cannot see
either and, asked, will produce a number anyway. That number would look exactly
like the four it is entitled to have an opinion about, which is worse than not
having it.

**Weights are a campaign's opinion, not a constant.** A campaign selling to
whoever will answer weights reachability heavily; one selling a six-figure
programme weights value and ignores it. The default below is a starting point
that says so in its own name.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class Component:
    """One axis of a score, and what a person reading it needs to know."""

    key: str
    name: str
    # What it measures, in one sentence. Goes into the model's prompt for the
    # judged ones and onto the page for all of them.
    asks: str
    # 'model' or 'computed'.
    origin: str = "model"


COMPONENTS: tuple[Component, ...] = (
    Component(
        "icp_fit", "Fit",
        "How closely this company matches who the brief says the campaign "
        "serves well, and how far it matches who it says are served badly.",
    ),
    Component(
        "need", "Need",
        "Evidence that this company actually has the problem the campaign "
        "solves. Not whether it could benefit in principle: whether something "
        "in the material says it has the problem now.",
    ),
    Component(
        "timing", "Timing",
        "Signals that this is the moment rather than some moment: a move, a "
        "hire, a change of officers, a system visibly aging, a deadline in "
        "their sector.",
    ),
    Component(
        "value", "Value",
        "The commercial size of the opportunity if it converts, judged from "
        "what the company is and does rather than from what one would like.",
    ),
    Component(
        "reach", "Reach",
        "Whether there is a way to reach a person who can decide. Counted "
        "from the contacts held, not judged.",
        origin="computed",
    ),
    Component(
        "relationship", "Relationship",
        "Whether anyone here is connected to anyone already being worked, "
        "through a shared board. Counted from the officer graph, not judged.",
        origin="computed",
    ),
)

BY_KEY = {component.key: component for component in COMPONENTS}
JUDGED = tuple(c.key for c in COMPONENTS if c.origin == "model")
COMPUTED = tuple(c.key for c in COMPONENTS if c.origin == "computed")

# A starting point, and named so nobody mistakes it for a finding. Fit and need
# lead because a campaign that gets those wrong is wasting every call; reach
# and relationship are modest because they change what you do rather than
# whether the company is worth doing it to.
DEFAULT_WEIGHTS: dict[str, Decimal] = {
    "icp_fit": Decimal("0.30"),
    "need": Decimal("0.25"),
    "timing": Decimal("0.15"),
    "value": Decimal("0.10"),
    "reach": Decimal("0.10"),
    "relationship": Decimal("0.10"),
}

DEFAULT_WEIGHT_SET = "starting point"

# Where the bands fall. Chosen so that "high" is a shortlist rather than a
# third of the survey: a band that contains everything is a band nobody acts
# on differently.
BANDS: tuple[tuple[str, Decimal], ...] = (
    ("high", Decimal("70")),
    ("medium", Decimal("50")),
    ("low", Decimal("0")),
)

# Any component may veto. A disqualifier in the brief is not something to be
# averaged away by five good scores, and a fit of zero means the brief said
# this is not who the campaign serves.
EXCLUDE_BELOW = Decimal("5")


class ScoringError(ValueError):
    """A score that must not be recorded as though it were computed."""


def band_of(total: Decimal | float | int, *, excluded: bool = False) -> str:
    if excluded:
        return "excluded"
    value = Decimal(str(total))
    for name, floor in BANDS:
        if value >= floor:
            return name
    return "low"


def combine(
    scores: dict[str, Decimal | float | int | None],
    weights: dict[str, Decimal] | None = None,
) -> tuple[Decimal, str]:
    """The weighted total and its band.

    A component with no score is left out and the weights of the rest are
    renormalised, rather than counted as zero. Counting a missing component as
    zero is the mistake that makes an unresearched company look actively bad
    instead of unknown, and an unresearched company is exactly the one somebody
    should go and look at.

    A judged fit of nearly zero excludes outright: the brief said this is not
    who the campaign is for, and no amount of reachability changes that.
    """
    weights = weights or DEFAULT_WEIGHTS
    present = {
        key: Decimal(str(value))
        for key, value in scores.items()
        if value is not None and key in weights
    }
    if not present:
        raise ScoringError("a score with no components is not a score")
    for key, value in present.items():
        if not (Decimal(0) <= value <= Decimal(100)):
            raise ScoringError(f"{key} is {value}, outside 0 to 100")

    mass = sum((weights[key] for key in present), Decimal(0))
    if mass <= 0:
        raise ScoringError("the weights of the components present sum to zero")
    total = sum((present[key] * weights[key] for key in present), Decimal(0)) / mass
    total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    excluded = present.get("icp_fit", Decimal(100)) < EXCLUDE_BELOW
    return total, band_of(total, excluded=excluded)


def normalise(weights: dict[str, Decimal | float | int]) -> dict[str, Decimal]:
    """Weights as Decimals summing to one, refusing what cannot be one.

    Renormalising rather than demanding the caller do the arithmetic: a person
    editing a weight set wants to say "twice as much as the others", and making
    them work out the sixths is how a weight set ends up wrong by 0.02 and
    nobody notices because it still ranks.
    """
    found = {
        key: Decimal(str(value)) for key, value in weights.items()
        if key in BY_KEY and Decimal(str(value)) > 0
    }
    if not found:
        raise ScoringError(
            "no usable weights. Names must be among "
            + ", ".join(BY_KEY) + " and each must be above zero."
        )
    mass = sum(found.values(), Decimal(0))
    return {
        key: (value / mass).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
        for key, value in found.items()
    }


# --- the two that are counted rather than judged ---------------------------------


def reach_score(contacts: list[dict] | list) -> tuple[Decimal, str, list[str]]:
    """How reachable a decision-maker is, from the contacts held.

    Returns the score, the reasoning, and the evidence. A suppressed address
    counts against rather than being ignored: somebody said do not write here,
    and a company whose only address is suppressed is less reachable than one
    with none, because the one route that existed has been closed.

    A named person's address outranks info@. Both are contacts; only one of
    them reaches somebody who can decide.
    """
    usable = [c for c in contacts if not _get(c, "suppressed")]
    suppressed = [c for c in contacts if _get(c, "suppressed")]
    emails = [c for c in usable if _get(c, "kind") == "email"]
    named = [c for c in emails if _get(c, "person_id")]
    phones = [c for c in usable if _get(c, "kind") == "phone"]

    if not contacts:
        # Distinct from holding contacts that are all unusable: nothing has
        # been looked for here, which is a reason to go and look rather than a
        # finding about the company.
        return Decimal(0), "no contacts held", []

    evidence: list[str] = []
    score = Decimal(0)
    if named:
        score = Decimal(90)
        evidence.append(f"{len(named)} address(es) attached to a named person")
    elif emails:
        score = Decimal(55)
        evidence.append(f"{len(emails)} address(es), none tied to a person")
    if phones:
        score = max(score, Decimal(40))
        evidence.append(f"{len(phones)} telephone number(s)")
    if not usable:
        score = Decimal(0)
        evidence.append("nothing usable held")
    if suppressed:
        score = max(Decimal(0), score - Decimal(25))
        evidence.append(
            f"{len(suppressed)} address(es) suppressed, which is a person "
            "having said not to write"
        )
    return score, "; ".join(evidence), evidence


def relationship_score(
    overlaps: list[dict] | list, *, worked_only: bool = True
) -> tuple[Decimal, str, list[str]]:
    """Whether anybody here is connected to anybody already being worked.

    `overlaps` is what `board_overlap` returns: people who sit on this
    company's board and on another company's. A shared seat at a company being
    worked is a warm introduction; a shared seat somewhere irrelevant is a fact
    about the register and worth nothing here.

    A past seat counts for less than a current one and is not nothing. "They
    sat together at a company neither is at now" is a real link and a poor
    introduction, which is a distinction the officer dates already make.
    """
    if not overlaps:
        return Decimal(0), "no shared board seats found", []

    live = [o for o in overlaps if not _get(o, "left_on")]
    relevant = [o for o in (live or overlaps)
                if _get(o, "worked") or not worked_only]
    evidence: list[str] = []
    if relevant:
        score = Decimal(85) if any(not _get(o, "left_on") for o in relevant) else Decimal(55)
        for row in relevant[:3]:
            name = _get(row, "full_name") or "someone"
            other = _get(row, "other_name") or "another company"
            when = " (a past seat)" if _get(row, "left_on") else ""
            evidence.append(f"{name} also sits at {other}{when}")
        return score, "; ".join(evidence), evidence

    # Connected to the register at large but to nothing being worked. Worth a
    # little: a person on several boards is easier to be introduced to than one
    # on none, even when none of those boards is a client.
    evidence.append(
        f"{len(overlaps)} shared seat(s), none at a company being worked"
    )
    return Decimal(20), evidence[0], evidence


def _get(row: object, key: str) -> object:
    """A field from a Row, a dict, or an object, whichever the caller had."""
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)
