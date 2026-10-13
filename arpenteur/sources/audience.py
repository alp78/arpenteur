"""Curating a list: whether a firm is written to, at which mailbox, and in what order.

Deciding only, no I/O. The stage reads the rows, this decides, the store writes
the result.

**Every firm in scope gets exactly one row and exactly one reason.** A list that
silently leaves firms out cannot be argued with; a list that says "57 dropped:
only an inferred address" can. So `judge` returns the first check a firm fails,
in a fixed order, and `funnel` counts them so the counts sum to the firms in
scope.

**A firm without an acceptable address reports the furthest any address got.**
A firm holding a dead `info@` and an inferred `kontakt@` is reported as "only
an inferred address", because that is the one decision the operator could
change.

**One message per mailbox, and by default one per domain.** Sibling firms share
mailboxes: `info@saas-fee.ch` sits under three Saastal companies, and
`valsurses.ch` publishes sixteen addresses under two. `assign` claims greedily,
best score first: each firm takes its best address whose mailbox, and whose
domain, is still free, and only when none is left is it covered by the firm
holding it. Without the domain pass, the second `valsurses.ch` firm would simply
move to `ferien@` and the same office would get two letters.

**The score is arithmetic on stored facts**, not a model's opinion, and every
part is kept so a rank can be explained. It ranks a desk above a named person,
the reverse of `scoring.reach_score`, because a first approach to a stranger
should reach a function rather than an individual.

**An operator's pin outranks the rule and survives a rebuild**, with two limits
no pin crosses: a mailbox that asked to be left alone, bounced, was refused or
no longer resolves is never written to, and no two firms share a mailbox.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Iterable, Mapping, Sequence

from . import mailcheck
from .language import Guess, guess

PAGE, HEARSAY, INFERRED = "page", "hearsay", "inferred"

# What `reach` writes on a hunt whose addresses came from a search result whose
# page was never read. The address is real enough to be reported and nobody saw
# it on the firm's own site.
HEARSAY_NOTE = "reported by search, page not readable"

KEPT = "kept"
DROPPED = "dropped"

# The order checks run in. Firm checks first; a firm failing one never has its
# addresses read.
FIRM_CHECKS: tuple[str, ...] = (
    "out-of-scope", "inactive", "legal-form", "programme-stage", "cooldown",
    "no-address",
)
# Then each address, in this order. A firm with no acceptable address reports
# the address that got furthest down this list, so the order is also a ranking
# of how close an address came: one stopped only by the cooldown had passed
# everything else.
ADDRESS_CHECKS: tuple[str, ...] = (
    "suppressed", "bounced", "refused", "dead", "not-a-contact", "verdict",
    "stale", "provenance", "administration", "role", "venue", "person",
    "unclear", "free-mail", "cooldown",
)
# The funnel reads in the same order, then deduplication, then the operator.
REASONS: tuple[str, ...] = (
    FIRM_CHECKS
    + tuple(c for c in ADDRESS_CHECKS if c not in FIRM_CHECKS)
    + ("covered-mailbox", "covered-domain", "operator", KEPT)
)

# A pin cannot reach past these. Each is a fact about the mailbox that a
# person's wish to write to it does not change.
ABSOLUTE = frozenset({"suppressed", "bounced", "refused", "dead", "not-a-contact"})

# How much each fact about an address is worth, between 0 and 1.
PROVENANCE_FACTOR = {PAGE: 1.0, HEARSAY: 0.6, INFERRED: 0.3}
KIND_FACTOR = {
    mailcheck.DESK: 1.0, mailcheck.UNCLEAR: 0.5, mailcheck.VENUE: 0.4,
    mailcheck.PERSON: 0.3, mailcheck.ROLE: 0.3, mailcheck.ADMINISTRATION: 0.2,
}
VERDICT_FACTOR = {"deliverable": 1.0, "maybe": 0.7}

# The size part, when used: the log of the registered capital between these.
SIZE_FLOOR, SIZE_CEILING = 20_000.0, 10_000_000.0


@dataclass(frozen=True)
class Weights:
    band: float = 50.0
    address: float = 25.0
    freshness: float = 10.0
    language: float = 10.0
    size: float = 5.0


@dataclass(frozen=True)
class Rule:
    """What a list is built from. Stored whole, so a rebuild is reproducible."""

    # The bands in scope and what each is worth, between 0 and 1.
    segments: Mapping[str, float]
    cantons: tuple[str, ...] = ()
    programme: str = ""
    members_only: bool = False
    # 0101 is a sole proprietorship, registered in a person's own name: writing
    # to one carries nLPD obligations that writing to a company does not.
    exclude_legal_forms: tuple[str, ...] = ("0101",)
    verdicts: tuple[str, ...] = ("deliverable",)
    provenance: tuple[str, ...] = (PAGE,)
    kinds: tuple[str, ...] = (mailcheck.DESK,)
    free_mail: bool = False
    max_age_days: int = 30
    cooldown_days: int = 180
    dedup_domain: bool = True
    use_size: bool = False
    weights: Weights = field(default_factory=Weights)

    def to_json(self) -> dict:
        raw = asdict(self)
        raw["segments"] = dict(sorted(self.segments.items()))
        for key in ("cantons", "exclude_legal_forms", "verdicts", "provenance", "kinds"):
            raw[key] = list(raw[key])
        return raw

    @classmethod
    def from_json(cls, raw: Mapping) -> "Rule":
        """A stored rule, with today's defaults for anything it predates."""
        base = cls(segments={})
        weights = {**asdict(base.weights), **dict(raw.get("weights") or {})}
        return cls(
            segments={str(k): float(v) for k, v in dict(raw.get("segments") or {}).items()},
            cantons=tuple(str(c).upper() for c in raw.get("cantons") or ()),
            programme=str(raw.get("programme") or ""),
            members_only=bool(raw.get("members_only", base.members_only)),
            exclude_legal_forms=tuple(raw.get("exclude_legal_forms", base.exclude_legal_forms)),
            verdicts=tuple(raw.get("verdicts", base.verdicts)),
            provenance=tuple(raw.get("provenance", base.provenance)),
            # A desk is always acceptable: a rule that refused desks would set
            # aside `info@` for being `info@`.
            kinds=tuple(dict.fromkeys((mailcheck.DESK, *raw.get("kinds", base.kinds)))),
            free_mail=bool(raw.get("free_mail", base.free_mail)),
            max_age_days=int(raw.get("max_age_days", base.max_age_days)),
            cooldown_days=int(raw.get("cooldown_days", base.cooldown_days)),
            dedup_domain=bool(raw.get("dedup_domain", base.dedup_domain)),
            use_size=bool(raw.get("use_size", base.use_size)),
            weights=Weights(**{k: float(v) for k, v in weights.items()}),
        )

    def digest(self) -> str:
        canonical = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Address:
    contact_id: int
    value: str
    status: str | None
    status_at: str | None
    method: str | None
    suppressed: bool = False
    refused: bool = False
    written_at: str | None = None


@dataclass(frozen=True)
class Firm:
    company_id: int
    name: str
    canton: str | None
    legal_form_uid: str | None
    active: bool
    segments: tuple[str, ...]
    addresses: tuple[Address, ...] = ()
    capital: float | None = None
    hunt_note: str | None = None
    written_at: str | None = None
    programme_stage: str | None = None
    first_stage: str | None = None


@dataclass(frozen=True)
class Option:
    """One address, as the rule sees it."""

    contact_id: int
    address: str
    domain: str
    kind: str
    provenance: str
    verdict: str
    age_days: int | None
    # None when the rule accepts it; otherwise the reason it was set aside,
    # which a pin may overrule unless the reason is absolute.
    rejected: str | None = None
    why: str = ""


@dataclass(frozen=True)
class Judgement:
    company_id: int
    name: str
    canton: str
    reason_code: str
    reason: str
    options: tuple[Option, ...] = ()       # accepted, best first
    pinnable: tuple[Option, ...] = ()      # set aside for a reason a pin may overrule
    segment: str | None = None
    band_weight: float = 0.0
    language: Guess | None = None
    capital: float | None = None


@dataclass(frozen=True)
class Pin:
    verdict: str | None = None        # kept | dropped
    language: str | None = None
    contact_id: int | None = None
    why: str = ""


@dataclass(frozen=True)
class Outcome:
    company_id: int
    verdict: str
    reason_code: str
    reason: str
    option: Option | None = None
    covered_by: int | None = None     # the company whose mailbox or domain covers this one
    score: float = 0.0
    parts: dict = field(default_factory=dict)
    language: str = ""
    language_basis: str = ""
    segment: str | None = None
    pinned: bool = False


# --- reading one firm -----------------------------------------------------------


def _age_days(stamp: str | None, today: date) -> int | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, (today - when.date()).days)


def provenance(address: Address, hunt_note: str | None) -> str:
    """Read off the firm's own page, reported by a search, or constructed."""
    if (address.method or "") == "inferred":
        return INFERRED
    if (hunt_note or "").strip() == HEARSAY_NOTE:
        return HEARSAY
    return PAGE


def _rank(option: Option) -> tuple:
    """Best first: a desk, read off the page, confirmed, then the house default."""
    order = (mailcheck.DESK, mailcheck.UNCLEAR, mailcheck.VENUE, mailcheck.PERSON,
             mailcheck.ROLE, mailcheck.ADMINISTRATION)
    local = option.address.partition("@")[0]
    role_rank = (mailcheck.ROLE_PARTS.index(local)
                 if local in mailcheck.ROLE_PARTS else len(mailcheck.ROLE_PARTS))
    return (
        order.index(option.kind) if option.kind in order else len(order),
        (PAGE, HEARSAY, INFERRED).index(option.provenance),
        0 if option.verdict == "deliverable" else 1,
        role_rank, len(option.address), option.address,
    )


def _examine(address: Address, firm: Firm, rule: Rule, today: date,
             recent_domains: Mapping[str, str]) -> Option:
    """One address against the rule: accepted, or set aside with its reason."""
    value = address.value.strip().lower()
    local, _, domain = value.partition("@")
    kind, kind_why = mailcheck.mailbox_role(value, firm.name)
    where = provenance(address, firm.hunt_note)
    verdict = (address.status or "").lower()
    age = _age_days(address.status_at, today)

    def option(rejected: str | None, why: str) -> Option:
        return Option(address.contact_id, value, domain, kind, where, verdict, age,
                      rejected, why)

    if address.suppressed:
        return option("suppressed", "asked to be left alone")
    if verdict == "bounced":
        return option("bounced", "the mailbox bounced")
    if address.refused:
        return option("refused", "a draft to this mailbox was refused")
    if verdict == "dead":
        return option("dead", "the domain no longer resolves")
    if local in mailcheck.NOT_A_CONTACT:
        return option("not-a-contact", "a technical mailbox nobody reads")
    if verdict not in rule.verdicts:
        return option("verdict", "never verified" if not verdict
                      else f"the domain's verdict is {verdict}")
    if age is None or age > rule.max_age_days:
        return option("stale", "never verified" if age is None
                      else f"verified {age} days ago; refresh the list")
    if where not in rule.provenance:
        return option("provenance", {
            HEARSAY: "only reported by a search; the page was never read",
            INFERRED: "only constructed at the domain, never published",
        }.get(where, where))
    if kind not in rule.kinds:
        return option(kind, kind_why)
    if domain in mailcheck.FREE_MAIL and not rule.free_mail:
        return option("free-mail", "a consumer mailbox, not the firm's")
    for stamp in (address.written_at, recent_domains.get(domain)):
        written = _age_days(stamp, today)
        if written is not None and written < rule.cooldown_days:
            return option("cooldown", f"written to {written} days ago")
    return option(None, kind_why)


def judge(firm: Firm, rule: Rule, *, today: date,
          recent_domains: Mapping[str, str] | None = None) -> Judgement:
    """The firm's one reason, and the addresses the rule accepts, best first."""
    recent_domains = recent_domains or {}
    canton = (firm.canton or "").upper()
    weights = {s: rule.segments[s] for s in firm.segments if s in rule.segments}
    segment = max(weights, key=lambda s: (weights[s], s)) if weights else None
    base = dict(company_id=firm.company_id, name=firm.name, canton=canton,
                segment=segment, band_weight=weights.get(segment, 0.0) if segment else 0.0,
                language=guess(firm.name, firm.canton), capital=firm.capital)

    def stop(code: str, why: str, **extra) -> Judgement:
        return Judgement(reason_code=code, reason=why, **base, **extra)

    if segment is None or (rule.cantons and canton not in rule.cantons):
        return stop("out-of-scope", "outside the rule's bands or cantons")
    if not firm.active:
        return stop("inactive", "struck off or winding up in the register")
    if (firm.legal_form_uid or "") in rule.exclude_legal_forms:
        return stop("legal-form", "a sole trader, registered in a person's own name"
                    if firm.legal_form_uid == "0101"
                    else f"legal form {firm.legal_form_uid} is excluded by the rule")
    if (firm.programme_stage and firm.first_stage
            and firm.programme_stage != firm.first_stage):
        return stop("programme-stage",
                    f"already at '{firm.programme_stage}' on {rule.programme}")
    written = _age_days(firm.written_at, today)
    if written is not None and written < rule.cooldown_days:
        return stop("cooldown", f"written to {written} days ago")
    if not firm.addresses:
        return stop("no-address", "no email address held")

    examined = sorted(
        (_examine(a, firm, rule, today, recent_domains) for a in firm.addresses),
        key=_rank,
    )
    accepted = tuple(o for o in examined if o.rejected is None)
    pinnable = tuple(o for o in examined
                     if o.rejected is not None and o.rejected not in ABSOLUTE)
    if accepted:
        best = accepted[0]
        return stop(KEPT, _kept_reason(best), options=accepted, pinnable=pinnable)
    furthest = max(examined, key=lambda o: ADDRESS_CHECKS.index(o.rejected))
    return stop(furthest.rejected, f"{furthest.address}: {furthest.why}",
                options=(), pinnable=pinnable)


def _kept_reason(option: Option) -> str:
    seen = {PAGE: "read off its own page", HEARSAY: "reported by a search",
            INFERRED: "constructed at its domain"}[option.provenance]
    return f"{option.why}, {seen}, {option.verdict}"


# --- the score ----------------------------------------------------------------


def _language_factor(language: Guess | None) -> float:
    if language is None:
        return 0.0
    if language.certain:
        return 1.0
    return 0.3 if language.basis == "canton" else 0.6


def _size_factor(capital: float | None) -> float:
    if not capital or capital <= 0:
        return 0.5
    low, high = math.log10(SIZE_FLOOR), math.log10(SIZE_CEILING)
    return min(1.0, max(0.0, (math.log10(capital) - low) / (high - low)))


def score(option: Option, *, band_weight: float, language: Guess | None,
          capital: float | None, rule: Rule) -> tuple[float, dict]:
    """The total out of 100 and its parts, which sum to it.

    Each part is a factor between 0 and 1 times its weight, rescaled over the
    weights in use, so switching size off leaves a score still out of 100.
    """
    w = rule.weights
    factors = {
        "band": band_weight,
        "address": (PROVENANCE_FACTOR.get(option.provenance, 0.0)
                    * KIND_FACTOR.get(option.kind, 0.0)
                    * VERDICT_FACTOR.get(option.verdict, 0.0)),
        "freshness": (0.0 if option.age_days is None
                      else max(0.0, 1 - option.age_days / max(1, rule.max_age_days))),
        "language": _language_factor(language),
    }
    weights = {"band": w.band, "address": w.address,
               "freshness": w.freshness, "language": w.language}
    if rule.use_size:
        factors["size"] = _size_factor(capital)
        weights["size"] = w.size
    total_weight = sum(weights.values()) or 1.0
    parts = {
        name: {"factor": round(factors[name], 3),
               "points": round(100 * weights[name] * factors[name] / total_weight, 2)}
        for name in factors
    }
    return round(sum(p["points"] for p in parts.values()), 1), parts


# --- deduplication ------------------------------------------------------------


def assign(judged: Sequence[Judgement], rule: Rule,
           pins: Mapping[int, Pin] | None = None) -> list[Outcome]:
    """Who keeps which mailbox, once each, best score first.

    Pinned firms claim first, then the rest by score. Each takes its best
    address whose mailbox is free and, unless pinned, whose domain is free.
    A firm left with nothing is covered by whichever firm holds its best
    address, and says so.
    """
    pins = pins or {}
    planned: list[tuple[Judgement, list[Option], Pin | None, float, dict]] = []
    for j in judged:
        pin = pins.get(j.company_id)
        candidates = list(j.options)
        if pin and pin.verdict == KEPT:
            candidates = list(j.options) + list(j.pinnable)
            if pin.contact_id is not None:
                chosen = [o for o in candidates if o.contact_id == pin.contact_id]
                candidates = chosen + [o for o in candidates if o.contact_id != pin.contact_id]
        best = candidates[0] if candidates else None
        total, parts = (score(best, band_weight=j.band_weight, language=j.language,
                              capital=j.capital, rule=rule) if best else (0.0, {}))
        planned.append((j, candidates, pin, total, parts))

    planned.sort(key=lambda p: (
        0 if (p[2] and p[2].verdict == KEPT) else 1, -p[3], p[0].company_id))

    mailboxes: dict[str, int] = {}
    domains: dict[str, int] = {}
    outcomes: list[Outcome] = []
    for j, candidates, pin, total, parts in planned:
        language = j.language
        lang, basis = ((pin.language, "operator") if pin and pin.language
                       else ((language.language, language.label) if language else ("", "")))
        common = dict(company_id=j.company_id, score=total, parts=parts,
                      language=lang, language_basis=basis, segment=j.segment,
                      pinned=bool(pin))

        if pin and pin.verdict == DROPPED:
            outcomes.append(Outcome(verdict=DROPPED, reason_code="operator",
                                    reason=f"dropped by operator: {pin.why} (rule: {j.reason})",
                                    option=candidates[0] if candidates else None, **common))
            continue
        if not candidates:
            outcomes.append(Outcome(verdict=DROPPED, reason_code=j.reason_code,
                                    reason=j.reason, **common))
            continue

        forced = bool(pin and pin.verdict == KEPT)
        chosen = None
        for option in candidates:
            if option.address in mailboxes:
                continue
            shared_domain = (rule.dedup_domain and option.domain not in mailcheck.FREE_MAIL
                             and option.domain in domains)
            if shared_domain and not forced:
                continue
            chosen = option
            break

        if chosen is None:
            first = candidates[0]
            if first.address in mailboxes:
                code, holder = "covered-mailbox", mailboxes[first.address]
                why = f"{first.address} is already written to for another firm"
            else:
                code, holder = "covered-domain", domains.get(first.domain)
                why = f"{first.domain} is already written to for another firm"
            outcomes.append(Outcome(verdict=DROPPED, reason_code=code, reason=why,
                                    option=first, covered_by=holder, **common))
            continue

        mailboxes[chosen.address] = j.company_id
        domains.setdefault(chosen.domain, j.company_id)
        if forced:
            reason = (f"kept by operator: {pin.why}" if j.reason_code == KEPT
                      else f"kept by operator: {pin.why} (rule: {j.reason})")
        else:
            reason = (j.reason if chosen is candidates[0] else
                      f"{_kept_reason(chosen)}; {candidates[0].address} went to another firm")
        if chosen is not candidates[0] or forced:
            total, parts = score(chosen, band_weight=j.band_weight, language=j.language,
                                 capital=j.capital, rule=rule)
            common.update(score=total, parts=parts)
        outcomes.append(Outcome(verdict=KEPT, reason_code=KEPT, reason=reason,
                                option=chosen, **common))
    return outcomes


def funnel(outcomes: Iterable[Outcome]) -> list[tuple[str, int]]:
    """How many firms each reason accounts for, in check order. Sums to the firms."""
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome.reason_code] = counts.get(outcome.reason_code, 0) + 1
    return [(code, counts[code]) for code in REASONS if code in counts]
