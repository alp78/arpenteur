"""Cutting a list into waves, and the gate between them."""

from __future__ import annotations

from datetime import date

import pytest

from arpenteur.sources import waves as W


def people() -> list[W.Recipient]:
    """Three cantons, two languages, scores that interleave across cantons."""
    rows = [
        (1, "Zermatt Tourismus", "VS", "de", 95), (2, "Saastal Tourismus", "VS", "de", 90),
        (3, "Grindelwald Tourismus", "BE", "de", 92), (4, "Adelboden Tourismus", "BE", "de", 70),
        (5, "Arosa Tourismus", "GR", "de", 60), (6, "Champéry Tourisme", "VS", "fr", 88),
        (7, "Nendaz Tourisme", "VS", "fr", 80), (8, "Lausanne Tourisme", "VD", "fr", 85),
    ]
    return [W.Recipient(i, n, c, lang, "tourism-office", s) for i, n, c, lang, s in rows]


def members(plan: list[W.Wave]) -> list[int]:
    return [m for wave in plan for m in wave.member_ids]


def test_every_strategy_has_a_label_and_an_explanation() -> None:
    assert len(W.STRATEGIES) >= 4
    for s in W.STRATEGIES.values():
        assert s.label and len(s.what) > 30, s.name


def test_a_strategy_is_one_decorated_function() -> None:
    @W.strategy("test-reversed", "Reversed", "The worst first, for a test of the registry only.")
    def reversed_order(ps, size):
        return W._chunks(sorted(ps, key=lambda r: r.score), size)
    try:
        assert W.plan(people(), strategy="test-reversed", wave_size=8)[0].member_ids[0] == 5
    finally:
        del W.STRATEGIES["test-reversed"]


def test_a_name_registered_twice_is_refused() -> None:
    with pytest.raises(ValueError):
        W.strategy("score-overall", "x", "y")(lambda ps, size: [])


@pytest.mark.parametrize("name", sorted(W.STRATEGIES))
def test_every_member_lands_in_exactly_one_wave(name: str) -> None:
    plan = W.plan(people(), strategy=name, wave_size=3, pilot_size=2)
    assert sorted(members(plan)) == list(range(1, 9))


@pytest.mark.parametrize("name", sorted(W.STRATEGIES))
def test_no_wave_exceeds_its_size(name: str) -> None:
    assert all(len(w.member_ids) <= 3 for w in W.plan(people(), strategy=name, wave_size=3))


def test_canton_blocks_never_mix_languages_and_keep_a_canton_together() -> None:
    by_id = {p.member_id: p for p in people()}
    plan = W.plan(people(), strategy="canton-blocks", wave_size=3)
    for wave in plan:
        assert len({by_id[m].language for m in wave.member_ids}) == 1
    vs_de = [w.number for w in plan for m in w.member_ids
             if by_id[m].canton == "VS" and by_id[m].language == "de"]
    assert len(set(vs_de)) == 1, "the two German Valais firms travel together"


def test_a_large_canton_spans_consecutive_waves() -> None:
    big = [W.Recipient(i, f"F{i}", "BE", "de", None, 100 - i) for i in range(1, 8)]
    numbers = [w.number for w in W.plan(big, strategy="canton-blocks", wave_size=3)]
    assert numbers == [1, 2, 3]


def test_one_canton_per_wave_never_mixes_cantons_or_languages() -> None:
    by_id = {p.member_id: p for p in people()}
    for wave in W.plan(people(), strategy="canton-per-wave", wave_size=10):
        assert len({(by_id[m].canton, by_id[m].language) for m in wave.member_ids}) == 1


def test_score_by_language_orders_inside_each_language() -> None:
    plan = W.plan(people(), strategy="score-by-language", wave_size=10)
    assert plan[0].member_ids == (1, 3, 2, 4, 5)


def test_score_overall_orders_by_score_only() -> None:
    plan = W.plan(people(), strategy="score-overall", wave_size=3)
    assert plan[0].member_ids == (1, 3, 2)


def test_the_pilot_is_wave_one_and_first_in_order() -> None:
    plan = W.plan(people(), strategy="score-overall", wave_size=3, pilot_size=2)
    assert plan[0].pilot and plan[0].member_ids == (1, 3)
    assert not any(w.pilot for w in plan[1:])
    assert plan[0].label.startswith("pilot")


def test_dates_step_by_the_gap() -> None:
    plan = W.plan(people(), strategy="score-overall", wave_size=3,
                  start=date(2026, 11, 2), gap_days=7)
    assert [w.planned_for for w in plan] == [date(2026, 11, 2), date(2026, 11, 9),
                                             date(2026, 11, 16)]


def test_ties_are_deterministic() -> None:
    tied = [W.Recipient(i, f"F{9 - i}", "BE", "de", None, 50) for i in range(1, 6)]
    first = W.plan(tied, strategy="score-overall", wave_size=5)
    assert first == W.plan(list(reversed(tied)), strategy="score-overall", wave_size=5)


def test_an_unknown_strategy_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="canton-blocks"):
        W.plan(people(), strategy="nope", wave_size=3)


def test_the_gate_waits_until_bounces_are_settled() -> None:
    g = W.gate(W.Tally(sent=10), ceiling=0.05, settled=False)
    assert not g.open and not g.pause


def test_the_gate_pauses_above_the_ceiling() -> None:
    g = W.gate(W.Tally(sent=10, bounced=1), ceiling=0.05, settled=True)
    assert not g.open and g.pause and "above" in g.why


def test_the_gate_opens_at_the_ceiling() -> None:
    assert W.gate(W.Tally(sent=20, bounced=1), ceiling=0.05, settled=True).open


def test_a_bounce_report_or_auto_reply_is_not_a_reply() -> None:
    assert W.classify_inbound("Undeliverable: Limen", "mailer-daemon@x.ch") == "report"
    assert W.classify_inbound("Abwesenheitsnotiz: Ihre Nachricht", "info@x.ch") == "auto"
    assert W.classify_inbound("Réponse automatique : Limen", "info@x.ch") == "auto"
    assert W.classify_inbound("Re: Limen", "info@x.ch") == "reply"


def test_the_campaign_state_comes_from_its_timestamps() -> None:
    assert W.campaign_state({}, []) == "unplanned"
    assert W.campaign_state({}, [{"sent_at": None}]) == "planned"
    assert W.campaign_state({}, [{"sent_at": "x"}, {"sent_at": None}]) == "running"
    assert W.campaign_state({"paused_at": "x"}, [{"sent_at": None}]) == "paused"
    assert W.campaign_state({}, [{"sent_at": "x"}]) == "done"
    assert W.campaign_state({"closed_at": "x"}, [{"sent_at": None}]) == "closed"


def test_a_large_cantons_remainder_takes_in_the_next_canton() -> None:
    """A 7-firm canton in waves of 3 leaves one firm over; the next canton joins it."""
    big = [W.Recipient(i, f"B{i}", "BE", "de", None, 100 - i) for i in range(1, 8)]
    small = [W.Recipient(20, "G1", "GR", "de", None, 10)]
    sizes = [len(w.member_ids) for w in W.plan(big + small, strategy="canton-blocks",
                                                wave_size=3)]
    assert sizes == [3, 3, 2]
