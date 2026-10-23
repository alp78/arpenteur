"""Curating a list: which mailbox, whether at all, in what order.

Pure: every firm here is built in the test. The shapes are the ones measured on
the tourism cohort, `info@saas-fee.ch` under three Saastal firms first among
them.
"""

from __future__ import annotations

from datetime import date

from arpenteur.sources import audience as A

TODAY = date(2026, 10, 13)
FRESH = "2026-10-12T09:00:00+00:00"
RULE = A.Rule(segments={"tourism-office": 1.0, "mountain-transport": 0.7})


def address(cid: int, value: str, *, status: str = "deliverable", at: str | None = FRESH,
            method: str = "published", **extra) -> A.Address:
    return A.Address(cid, value, status, at, method, **extra)


def firm(cid: int, name: str, *addresses: A.Address, segment: str = "tourism-office",
         canton: str = "VS", **extra) -> A.Firm:
    return A.Firm(cid, name, canton, extra.pop("legal_form_uid", "0106"),
                  extra.pop("active", True), (segment,), tuple(addresses), **extra)


def run(*firms: A.Firm, rule: A.Rule = RULE, pins=None, recent=None) -> dict[int, A.Outcome]:
    judged = [A.judge(f, rule, today=TODAY, recent_domains=recent) for f in firms]
    return {o.company_id: o for o in A.assign(judged, rule, pins)}


# --- one reason per firm ------------------------------------------------------

def test_every_firm_gets_exactly_one_reason_and_the_funnel_sums() -> None:
    firms = [
        firm(1, "Zermatt Tourismus", address(11, "info@zermatt.ch")),
        firm(2, "Musterlift AG", active=False),
        firm(3, "Muster Tourismus"),
        firm(4, "Saastal Tourismus AG", address(41, "info@saas-fee.ch")),
        firm(5, "Saastal Bergbahnen AG", address(51, "info@saas-fee.ch"),
             segment="mountain-transport"),
    ]
    outcomes = run(*firms)
    assert len(outcomes) == len(firms)
    assert sum(n for _, n in A.funnel(outcomes.values())) == len(firms)


def test_firm_checks_come_before_its_addresses() -> None:
    o = run(firm(1, "Muster Tourismus", address(11, "info@x.ch"), active=False))[1]
    assert (o.verdict, o.reason_code) == ("dropped", "inactive")


def test_a_sole_trader_is_dropped_unless_the_rule_allows() -> None:
    one = firm(1, "Muster Bergführer", address(11, "info@x.ch"), legal_form_uid="0101")
    assert run(one)[1].reason_code == "legal-form"
    allowing = A.Rule(segments=RULE.segments, exclude_legal_forms=())
    assert run(one, rule=allowing)[1].verdict == "kept"


def test_a_firm_outside_the_bands_is_out_of_scope() -> None:
    assert run(firm(1, "X", address(11, "info@x.ch"), segment="regie-core"))[1].reason_code \
        == "out-of-scope"


def test_a_firm_past_the_first_programme_stage_is_dropped() -> None:
    o = run(firm(1, "Muster Tourismus", address(11, "info@x.ch"),
                 programme_stage="contacted", first_stage="selected"))[1]
    assert o.reason_code == "programme-stage"


# --- which address ------------------------------------------------------------

def test_a_dead_unverified_or_stale_address_is_never_chosen() -> None:
    assert run(firm(1, "T", address(11, "info@x.ch", status="dead")))[1].reason_code == "dead"
    assert run(firm(1, "T", address(11, "info@x.ch", status=None)))[1].reason_code == "verdict"
    old = address(11, "info@x.ch", at="2026-08-01T00:00:00+00:00")
    assert run(firm(1, "T", old))[1].reason_code == "stale"


def test_only_page_read_addresses_by_default() -> None:
    hearsay = firm(1, "T", address(11, "info@x.ch"), hunt_note=A.HEARSAY_NOTE)
    inferred = firm(2, "U", address(21, "info@y.ch", method="inferred"))
    out = run(hearsay, inferred)
    assert out[1].reason_code == out[2].reason_code == "provenance"
    loose = A.Rule(segments=RULE.segments, provenance=(A.PAGE, A.HEARSAY, A.INFERRED))
    assert run(hearsay, inferred, rule=loose)[1].verdict == "kept"


def test_a_desk_beats_an_unclear_address_at_the_same_firm() -> None:
    loose = A.Rule(segments=RULE.segments, kinds=("desk", "unclear"))
    o = run(firm(1, "T", address(11, "web@x.ch"), address(12, "info@x.ch")), rule=loose)[1]
    assert o.option.address == "info@x.ch"


def test_a_holiday_desk_is_a_desk() -> None:
    """`ferien@valsurses.ch` is where Val Surses takes bookings."""
    assert run(firm(1, "Tourismus Val Surses", address(11, "ferien@valsurses.ch"),
                    canton="GR"))[1].verdict == "kept"


def test_a_communes_mailbox_is_set_aside() -> None:
    o = run(firm(1, "Skilift A.G. Heimenschwand", address(11, "gemeinde@buchholterberg.ch"),
                 canton="BE"))[1]
    assert o.reason_code == "administration"


def test_the_address_that_got_furthest_gives_the_reason() -> None:
    """A dead `info@` and an inferred `kontakt@`: only the second is a decision."""
    o = run(firm(1, "T", address(11, "info@x.ch", status="dead"),
                 address(12, "kontakt@x.ch", method="inferred")))[1]
    assert o.reason_code == "provenance"


def test_the_cooldown_covers_the_firm_and_the_domain_and_the_mailbox_is_spent() -> None:
    recent = "2026-09-01T00:00:00+00:00"
    assert run(firm(1, "T", address(11, "info@x.ch"), written_at=recent))[1].reason_code \
        == "cooldown"
    assert run(firm(1, "T", address(11, "info@x.ch", written_at=recent)))[1].reason_code \
        == "written"
    assert run(firm(1, "T", address(11, "info@x.ch")),
               recent={"x.ch": recent})[1].reason_code == "cooldown"


# --- one message per mailbox ----------------------------------------------------

def test_a_shared_mailbox_goes_to_the_higher_score() -> None:
    out = run(
        firm(1, "Saastal Bergbahnen AG", address(11, "info@saas-fee.ch"),
             segment="mountain-transport"),
        firm(2, "Saastal Tourismus AG", address(21, "info@saas-fee.ch")),
    )
    assert out[2].verdict == "kept"
    assert (out[1].reason_code, out[1].covered_by) == ("covered-mailbox", 2)


def test_a_firm_falls_back_to_its_own_mailbox_before_being_covered() -> None:
    """The lift firm's best mailbox is the shared one, and the office outranks it."""
    out = run(
        firm(1, "Lift AG", address(11, "info@sh.ch"), address(12, "kontakt@liftbahn.ch"),
             segment="mountain-transport"),
        firm(2, "Shared Tourismus", address(21, "info@sh.ch")),
    )
    assert out[2].option.address == "info@sh.ch"
    assert out[1].verdict == "kept" and out[1].option.address == "kontakt@liftbahn.ch"
    assert "went to another firm" in out[1].reason


def test_one_domain_one_message_by_default() -> None:
    a = firm(1, "Val Surses Tourismus", address(11, "info@valsurses.ch"))
    b = firm(2, "Savognin Bergbahnen AG", address(21, "kontakt@valsurses.ch"),
             segment="mountain-transport")
    assert run(a, b)[2].reason_code == "covered-domain"
    apart = A.Rule(segments=RULE.segments, dedup_domain=False)
    assert run(a, b, rule=apart)[2].verdict == "kept"


def test_free_mail_is_never_deduplicated_by_domain() -> None:
    rule = A.Rule(segments=RULE.segments, free_mail=True)
    out = run(firm(1, "A Tourismus", address(11, "a.tourismus@gmail.com")),
              firm(2, "B Tourismus", address(21, "b.tourismus@gmail.com")),
              rule=A.Rule(**{**rule.__dict__, "kinds": ("desk", "unclear", "person")}))
    assert out[1].verdict == out[2].verdict == "kept"


# --- the operator -------------------------------------------------------------

def test_a_pin_outranks_the_rule_and_says_so() -> None:
    o = run(firm(1, "T", address(11, "flury@x.ch")),
            pins={1: A.Pin(verdict="kept", why="the owner reads it")})[1]
    assert o.verdict == "kept" and o.pinned
    assert "kept by operator" in o.reason and "rule:" in o.reason


def test_no_pin_reaches_a_suppressed_bounced_or_refused_mailbox() -> None:
    for bad in (address(11, "info@x.ch", suppressed=True),
                address(11, "info@x.ch", status="bounced"),
                address(11, "info@x.ch", refused=True)):
        o = run(firm(1, "T", bad), pins={1: A.Pin(verdict="kept", why="please")})[1]
        assert o.verdict == "dropped", bad


def test_a_mailbox_written_to_once_is_never_kept_again() -> None:
    """The cooldown rests a firm and a domain; a mailbox that received a message
    is spent, however long ago, and no pin brings it back."""
    long_ago = "2025-08-01T00:00:00+00:00"
    spent = address(11, "info@x.ch", written_at=long_ago)
    assert run(firm(1, "T", spent))[1].reason_code == "written"
    pinned = run(firm(1, "T", spent), pins={1: A.Pin(verdict="kept", why="please")})[1]
    assert pinned.verdict == "dropped"
    unwritten = A.Rule(segments=RULE.segments, cooldown_days=0)
    assert run(firm(1, "T", spent), rule=unwritten)[1].reason_code == "written"


def test_a_firm_past_its_cooldown_is_kept_on_another_mailbox() -> None:
    long_ago = "2025-08-01T00:00:00+00:00"
    o = run(firm(1, "T", address(11, "info@x.ch", written_at=long_ago),
                 address(12, "contact@x.ch"), written_at=long_ago))[1]
    assert o.verdict == "kept" and o.option.address == "contact@x.ch"


def test_a_pin_cannot_put_two_firms_on_one_mailbox() -> None:
    out = run(firm(1, "A Tourismus", address(11, "info@shared.ch")),
              firm(2, "B Tourismus", address(21, "info@shared.ch")),
              pins={1: A.Pin(verdict="kept", why="x"), 2: A.Pin(verdict="kept", why="y")})
    assert sorted(o.verdict for o in out.values()) == ["dropped", "kept"]


def test_a_pinned_drop_is_reported_as_the_operators() -> None:
    o = run(firm(1, "T", address(11, "info@x.ch")),
            pins={1: A.Pin(verdict="dropped", why="a client already")})[1]
    assert (o.verdict, o.reason_code) == ("dropped", "operator")


def test_a_pinned_language_overrides_the_guess() -> None:
    o = run(firm(1, "Zermatt Tourismus", address(11, "info@zermatt.ch")),
            pins={1: A.Pin(language="fr", why="writes in French")})[1]
    assert (o.language, o.language_basis) == ("fr", "operator")


# --- the score ----------------------------------------------------------------

def test_the_parts_sum_to_the_score() -> None:
    o = run(firm(1, "Zermatt Tourismus", address(11, "info@zermatt.ch")))[1]
    assert abs(sum(p["points"] for p in o.parts.values()) - o.score) < 0.11


def test_a_perfect_firm_scores_a_hundred_with_size_off() -> None:
    o = run(firm(1, "Zermatt Tourismus", address(11, "info@zermatt.ch",
                                                  at="2026-10-13T08:00:00+00:00")))[1]
    assert o.score == 100.0


def test_missing_capital_is_neutral_when_size_counts() -> None:
    rule = A.Rule(segments=RULE.segments, use_size=True)
    o = run(firm(1, "Zermatt Tourismus", address(11, "info@zermatt.ch")), rule=rule)[1]
    assert o.parts["size"]["factor"] == 0.5


def test_a_named_person_never_outranks_a_desk() -> None:
    desk = A.Option(1, "info@x.ch", "x.ch", "desk", A.PAGE, "deliverable", 1)
    person = A.Option(2, "anna.muster@x.ch", "x.ch", "person", A.PAGE, "deliverable", 1)
    common = dict(band_weight=1.0, language=None, capital=None, rule=RULE)
    assert A.score(desk, **common)[0] > A.score(person, **common)[0]


# --- the rule -----------------------------------------------------------------

def test_the_rule_round_trips_and_fills_defaults() -> None:
    assert A.Rule.from_json(RULE.to_json()) == RULE
    assert A.Rule.from_json({"segments": {"tourism-office": 1}}).max_age_days == 30
    assert RULE.digest() == A.Rule.from_json(RULE.to_json()).digest()


def test_a_rule_always_accepts_a_desk() -> None:
    assert "desk" in A.Rule.from_json({"segments": {}, "kinds": ["unclear"]}).kinds


# --- the places per canton ----------------------------------------------------

def test_a_canton_keeps_no_more_firms_than_its_places() -> None:
    """A pilot of N per canton: the best N of each canton keep their mailbox, and
    every firm past them says its canton was full rather than vanishing."""
    rule = A.Rule(segments={"tourism-office": 1.0}, per_canton=2)
    out = run(firm(1, "A Tourismus", address(11, "info@a.ch"), canton="VS"),
              firm(2, "B Tourismus", address(21, "info@b.ch"), canton="VS"),
              firm(3, "C Tourismus", address(31, "info@c.ch"), canton="VS"),
              firm(4, "D Tourisme", address(41, "info@d.ch"), canton="GE"),
              rule=rule)
    kept = sorted(cid for cid, o in out.items() if o.verdict == "kept")
    assert kept == [1, 2, 4]
    assert out[3].reason_code == "canton-full"
    assert "VS" in out[3].reason and "2" in out[3].reason
    assert sum(n for _, n in A.funnel(out.values())) == 4


def test_a_firm_left_out_of_a_full_canton_frees_its_mailbox() -> None:
    rule = A.Rule(segments={"tourism-office": 1.0}, per_canton=1)
    out = run(firm(1, "A Tourismus", address(11, "info@a.ch"), canton="VS"),
              firm(2, "B Tourismus", address(21, "info@b.ch"), canton="VS"),
              rule=rule)
    assert out[2].reason_code == "canton-full" and out[2].covered_by is None


def test_a_pinned_firm_takes_a_place_and_is_never_left_out() -> None:
    rule = A.Rule(segments={"tourism-office": 1.0}, per_canton=1)
    out = run(firm(1, "A Tourismus", address(11, "info@a.ch"), canton="VS"),
              firm(2, "B Tourismus", address(21, "info@b.ch"), canton="VS"),
              rule=rule, pins={2: A.Pin(verdict="kept", why="met at a fair")})
    assert out[2].verdict == "kept"
    assert out[1].reason_code == "canton-full"


def test_no_places_means_no_limit_and_the_rule_round_trips() -> None:
    assert RULE.per_canton == 0
    capped = A.Rule(segments={"tourism-office": 1.0}, per_canton=20)
    assert A.Rule.from_json(capped.to_json()) == capped
    assert A.Rule.from_json({"segments": {}}).per_canton == 0
