"""The three rules that read a registered purpose.

Each of these was earned by a false positive in `classify_regies`, and the
examples below are the purposes that produced them. The point of the file is
that lifting the rules into `sources/purpose.py` for a second classifier did not
change what they do.
"""

from __future__ import annotations

import re

from arpenteur.sources.purpose import NEAR, fold, head, near

PROPERTY = re.compile(r"immobili|immeuble|liegenschaft|wohnung", re.IGNORECASE)
GERANCE = re.compile(r"gerance|verwaltung", re.IGNORECASE)


def test_fold_puts_accented_and_bare_spellings_on_one_word() -> None:
    assert fold("gérance") == "gerance"
    assert fold("Tourismusförderung") == "Tourismusforderung"
    assert fold("Gäste") == "Gaste"
    assert fold("remontées mécaniques") == "remontees mecaniques"


def test_fold_of_nothing_is_nothing() -> None:
    assert fold("") == ""
    assert fold(None) == ""  # type: ignore[arg-type]


def test_head_stops_at_the_notarial_permissions_clause() -> None:
    """The clause that made a dental practice read as a property company."""
    text = head(
        "Exploitation d'un cabinet dentaire. Elle peut acquérir, détenir et "
        "aliéner des immeubles."
    )
    assert "cabinet dentaire" in text
    assert "immeubles" not in text


def test_head_keeps_a_purpose_that_has_no_such_clause() -> None:
    """Case survives the fold, which is why every pattern carries IGNORECASE."""
    assert head("Gérance d'immeubles pour le compte de tiers").startswith("Gerance")


def test_head_cuts_the_german_and_italian_forms_too() -> None:
    assert "liegenschaften" not in head(
        "Betrieb einer Schreinerei. Sie kann Liegenschaften erwerben."
    )
    assert "immobili" not in head(
        "Gestione di un ristorante. Essa può acquistare immobili."
    )


def test_near_requires_both_words_in_one_clause() -> None:
    """The kiosk case: both words present, each about something else."""
    apart = fold(
        "mise en gérance et exploitation d'établissements publics tels que "
        "kiosques, buvettes et tea-rooms, ainsi que l'achat de tout immeuble"
    )
    together = fold("gérance d'immeubles pour le compte de tiers")
    assert near(GERANCE, PROPERTY, together) is True
    assert near(GERANCE, PROPERTY, apart) is False


def test_near_is_false_when_the_anchor_is_absent() -> None:
    assert near(GERANCE, PROPERTY, fold("gérance de fortune")) is False


def test_near_window_is_the_published_one() -> None:
    """Sixty characters, and a caller may narrow it without editing the module."""
    text = "verwaltung" + "x" * 40 + "liegenschaft"
    assert NEAR == 60
    assert near(GERANCE, PROPERTY, text) is True
    assert near(GERANCE, PROPERTY, text, window=10) is False
