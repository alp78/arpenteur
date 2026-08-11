"""Territory: where a survey applies, and what that has to be safe against.

The territory is asked two different questions by two different stages. The
sweep asks what to fetch; the sync stage asks whether a company appearing in
the national feed belongs here. They must get the same answer, or a canton
gets swept and then never kept current, or kept current and never swept.

The failure worth guarding hardest is the quiet one. An empty canton list
means "no restriction", which is right for a survey of all Switzerland and
catastrophic as the result of a typo, so a name that resolves to nothing has
to raise rather than resolve.
"""

from __future__ import annotations

import pytest

from arpenteur.config import Territory, resolve_territory

REGIONS = {
    "romandie": ["GE", "VD", "FR", "NE", "VS"],
    "geneve": ["GE"],
    "suisse": ["ZH", "BE", "GE", "VD", "FR", "NE", "VS", "TI"],
}


def resolve(**kwargs) -> Territory:
    kwargs.setdefault("regions", REGIONS)
    kwargs.setdefault("region", None)
    kwargs.setdefault("cantons", None)
    kwargs.setdefault("legal_forms", ["0107"])
    return resolve_territory(**kwargs)


def test_a_region_name_resolves_to_its_cantons() -> None:
    assert resolve(region="romandie").cantons == ("GE", "VD", "FR", "NE", "VS")


def test_an_unknown_region_raises_rather_than_widening_the_survey() -> None:
    """The dangerous typo. Resolving to an empty list would read as "every
    canton", so `--region romandy` would silently sweep Switzerland."""
    with pytest.raises(RuntimeError, match="Unknown region"):
        resolve(region="romandy")

    # And the message has to say what the choices were, or the next attempt
    # is another guess.
    try:
        resolve(region="romandy")
    except RuntimeError as exc:
        assert "romandie" in str(exc)


def test_explicit_cantons_beat_a_region() -> None:
    got = resolve(region="romandie", cantons=["ge"])
    assert got.cantons == ("GE",)


def test_cantons_are_normalised_so_config_casing_cannot_matter() -> None:
    assert resolve(cantons=[" vd ", "ge"]).cantons == ("VD", "GE")


def test_a_canton_list_matching_a_region_is_named_after_it() -> None:
    """So a log line reads "romandie" rather than five codes."""
    assert resolve(cantons=["VS", "NE", "FR", "VD", "GE"]).name == "romandie"


# --- what the sync stage asks of it ------------------------------------------


def test_membership_is_both_canton_and_legal_form() -> None:
    territory = resolve(region="geneve", legal_forms=["0107", "0106"])
    assert territory.contains("GE", "0107")
    assert not territory.contains("ZH", "0107"), "wrong canton"
    assert not territory.contains("GE", "0109"), "wrong legal form"


def test_membership_is_case_insensitive_on_canton() -> None:
    """The feed's canton comes off a notice, not from our own config."""
    assert resolve(region="geneve").contains("ge", "0107")


def test_an_empty_axis_means_no_restriction_on_it() -> None:
    """A survey of every legal form in one canton, and of one form everywhere,
    are both things somebody will want."""
    assert Territory(cantons=("GE",), legal_forms=()).contains("GE", "0109")
    assert Territory(cantons=(), legal_forms=("0107",)).contains("ZH", "0107")
    assert not Territory(cantons=(), legal_forms=("0107",)).contains("ZH", "0109")


def test_a_missing_canton_is_outside_a_restricted_territory() -> None:
    """Feed entries can arrive without one; that must not admit them."""
    assert not resolve(region="geneve").contains(None, "0107")
