"""Cutting a curated list into waves, and deciding whether the next may go.

Deciding only, no I/O.

**The strategy is the operator's choice per campaign**, never one rule written
into the code. Each strategy is one function registered under a name, with a
label and a sentence saying what it does, so the console can offer them and
adding one is one decorated function:

  canton-blocks      one language per wave, cantons kept together
  canton-per-wave    one canton and one language per wave
  score-by-language  the best scores first, one language per wave
  score-overall      the best scores first, languages mixed

**Ordering is deterministic.** Groups are ordered by their best score, then by
size, then by name; people by score, then by company name, then by row. Two
plans of the same list are the same plan, which is what lets a plan be read
before it is kept.

**The pilot is wave one.** The first people in the strategy's own order go out
first and alone, so a bad batch of addresses shows in a handful of bounces
rather than in a whole wave's. The gate below is what makes it a pilot rather
than just a small first wave: the next wave waits until the pilot's bounces
have had time to arrive, and the campaign pauses itself above a ceiling.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from . import bounce
from .mailcheck import deaccent


@dataclass(frozen=True)
class Recipient:
    member_id: int
    company: str
    canton: str
    language: str
    segment: str | None
    score: float


@dataclass(frozen=True)
class Wave:
    number: int
    pilot: bool
    planned_for: date | None
    member_ids: tuple[int, ...]
    label: str


Order = Callable[[Sequence[Recipient], int], list[list[Recipient]]]


@dataclass(frozen=True)
class Strategy:
    name: str
    label: str
    what: str
    order: Order


STRATEGIES: dict[str, Strategy] = {}


def strategy(name: str, label: str, what: str) -> Callable[[Order], Order]:
    """Register one way of cutting waves. The name is what a campaign stores."""
    def register(order: Order) -> Order:
        if name in STRATEGIES:
            raise ValueError(f"wave strategy {name!r} is registered twice")
        STRATEGIES[name] = Strategy(name, label, what, order)
        return order
    return register


# --- ordering helpers ---------------------------------------------------------


def _people(group: Sequence[Recipient]) -> list[Recipient]:
    return sorted(group, key=lambda r: (-r.score, r.company, r.member_id))


def _groups(people: Sequence[Recipient], key: Callable[[Recipient], str]) -> list[list[Recipient]]:
    """People grouped by `key`, the groups ordered by best score, size, then key."""
    found: dict[str, list[Recipient]] = {}
    for person in people:
        found.setdefault(key(person), []).append(person)
    ordered = sorted(
        found.items(),
        key=lambda kv: (-max(r.score for r in kv[1]), -len(kv[1]), kv[0]),
    )
    return [_people(group) for _, group in ordered]


def _chunks(people: Sequence[Recipient], size: int) -> list[list[Recipient]]:
    return [list(people[i:i + size]) for i in range(0, len(people), size)]


def _pack(blocks: Sequence[Sequence[Recipient]], size: int) -> list[list[Recipient]]:
    """Fill waves with whole blocks; a block larger than a wave spans several.

    A block that no longer fits in the wave being filled starts the next one,
    so no block is cut unless it alone exceeds the size. A block that does is
    cut into full waves, and its remainder stays open for the next small block
    to join: left alone, a 30-firm canton produced a wave of 5 followed by a
    wave of 2.
    """
    waves: list[list[Recipient]] = []
    current: list[Recipient] = []
    for block in blocks:
        if len(block) > size:
            if current:
                waves.append(current)
            pieces = _chunks(block, size)
            waves.extend(pieces[:-1])
            current = pieces[-1]
            continue
        if len(current) + len(block) > size:
            waves.append(current)
            current = []
        current.extend(block)
    if current:
        waves.append(current)
    return waves


# --- the strategies -----------------------------------------------------------


@strategy(
    "canton-blocks", "Cantons together, by language",
    "Each wave is one language. Cantons are kept whole inside a wave where they "
    "fit, the strongest canton first, the best score first inside each.",
)
def canton_blocks(people: Sequence[Recipient], size: int) -> list[list[Recipient]]:
    waves: list[list[Recipient]] = []
    for language in _groups(people, lambda r: r.language):
        waves.extend(_pack(_groups(language, lambda r: r.canton), size))
    return waves


@strategy(
    "canton-per-wave", "One canton per wave",
    "A regional rollout: each wave is one canton in one language, the strongest "
    "first. A bilingual canton becomes two waves, and a canton larger than a "
    "wave spans several.",
)
def canton_per_wave(people: Sequence[Recipient], size: int) -> list[list[Recipient]]:
    waves: list[list[Recipient]] = []
    for group in _groups(people, lambda r: f"{r.canton}/{r.language}"):
        waves.extend(_chunks(group, size))
    return waves


@strategy(
    "score-by-language", "Best score within each language",
    "Each wave is one language and takes the best-scoring firms left in it, "
    "wherever they are.",
)
def score_by_language(people: Sequence[Recipient], size: int) -> list[list[Recipient]]:
    waves: list[list[Recipient]] = []
    for language in _groups(people, lambda r: r.language):
        waves.extend(_chunks(language, size))
    return waves


@strategy(
    "score-overall", "Best score overall",
    "Each wave takes the best-scoring firms left, in any language. A wave can "
    "need several templates.",
)
def score_overall(people: Sequence[Recipient], size: int) -> list[list[Recipient]]:
    return _chunks(_people(people), size)


# --- the plan -----------------------------------------------------------------


def label(people: Sequence[Recipient], *, pilot: bool = False) -> str:
    """`pilot · de · VS, BE`: what a wave holds, in the order it holds it."""
    languages: dict[str, int] = {}
    cantons: list[str] = []
    for person in people:
        languages[person.language] = languages.get(person.language, 0) + 1
        if person.canton not in cantons:
            cantons.append(person.canton)
    tongue = "+".join(sorted(languages, key=lambda k: (-languages[k], k)))
    shown = ", ".join(cantons[:4]) + (f" +{len(cantons) - 4}" if len(cantons) > 4 else "")
    return " · ".join(part for part in ("pilot" if pilot else "", tongue, shown) if part)


def plan(people: Sequence[Recipient], *, strategy: str, wave_size: int,
         pilot_size: int = 0, start: date | None = None,
         gap_days: int = 7) -> list[Wave]:
    """The waves, numbered from one, the pilot first when there is one."""
    if strategy not in STRATEGIES:
        raise ValueError(
            f"unknown wave strategy {strategy!r}; known: {', '.join(sorted(STRATEGIES))}"
        )
    if wave_size < 1:
        raise ValueError("a wave holds at least one firm")
    waves = STRATEGIES[strategy].order(people, wave_size)
    if pilot_size > 0:
        flat = [person for wave in waves for person in wave]
        pilot = flat[:pilot_size]
        chosen = {p.member_id for p in pilot}
        rest = [[p for p in wave if p.member_id not in chosen] for wave in waves]
        waves = [pilot] + [wave for wave in rest if wave]
    out = []
    for index, wave in enumerate(waves):
        is_pilot = pilot_size > 0 and index == 0
        out.append(Wave(
            number=index + 1,
            pilot=is_pilot,
            planned_for=start + timedelta(days=gap_days * index) if start else None,
            member_ids=tuple(p.member_id for p in wave),
            label=label(wave, pilot=is_pilot),
        ))
    return out


# --- results and the gate -----------------------------------------------------


@dataclass(frozen=True)
class Tally:
    sent: int = 0
    bounced: int = 0
    deferred: int = 0
    replied: int = 0
    auto: int = 0
    refused: int = 0
    enrolled: int = 0

    @property
    def bounce_rate(self) -> float:
        return self.bounced / self.sent if self.sent else 0.0


@dataclass(frozen=True)
class Gate:
    open: bool
    pause: bool
    why: str


def gate(tally: Tally, *, ceiling: float, settled: bool) -> Gate:
    """Whether the wave after this one may go.

    Settled means the mailbox was read for bounces long enough after the last
    post for them to have arrived; a zero read too early is not a zero. At the
    ceiling exactly the gate is open, above it the campaign pauses.
    """
    if tally.sent == 0:
        return Gate(False, False, "nothing has been sent in this wave yet")
    if not settled:
        return Gate(False, False, "waiting for this wave's bounces to arrive")
    shown = f"{tally.bounced} of {tally.sent} bounced"
    if tally.bounce_rate > ceiling:
        return Gate(False, True, f"{shown}, above the {ceiling:.0%} ceiling")
    return Gate(True, False, f"{shown}, within the {ceiling:.0%} ceiling")


_AUTO = (
    "automatic reply", "auto-reply", "autoreply", "out of office", "absence",
    "abwesend", "abwesenheit", "automatische antwort", "reponse automatique",
    "risposta automatica", "assente", "assenza", "ferienabwesenheit",
    "nicht im buro",
)


def classify_inbound(subject: str, sender: str) -> str:
    """`report` for a delivery report, `auto` for an absence notice, else `reply`."""
    if bounce.looks_like_report(subject, sender):
        return "report"
    flat = deaccent(subject or "").lower()
    if any(word in flat for word in _AUTO):
        return "auto"
    return "reply"


def campaign_state(campaign: Mapping, waves: Sequence[Mapping]) -> str:
    """unplanned | planned | running | paused | done | closed, from timestamps."""
    if campaign.get("closed_at"):
        return "closed"
    if not waves:
        return "unplanned"
    if campaign.get("paused_at"):
        return "paused"
    sent = [w for w in waves if w.get("sent_at")]
    if len(sent) == len(waves):
        return "done"
    return "running" if sent else "planned"
