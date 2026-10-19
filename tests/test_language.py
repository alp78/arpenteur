"""Which language a firm is written to in, read from what it calls itself.

The names below are real register entries from the tourism cohort. The case
that made this module necessary is the first: a German firm in Valais, which
the canton rule would have written to in French.
"""

from __future__ import annotations

import pytest

from arpenteur.sources.language import BILINGUAL, by_canton, guess
from arpenteur.stages.draft import language_of

CANTONS = (
    "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU", "LU", "NE",
    "NW", "OW", "SG", "SH", "SO", "SZ", "TG", "TI", "UR", "VD", "VS", "ZG", "ZH",
)


def test_a_german_trade_word_beats_a_french_canton() -> None:
    g = guess("Blatten-Belalp Tourismus AG", "VS")
    assert (g.language, g.basis, g.evidence) == ("de", "name", "tourismus")
    assert g.certain


def test_a_french_trade_word_beats_a_german_canton() -> None:
    g = guess("Téléskis SA Les Prés-d'Orvin", "BE")
    assert (g.language, g.basis) == ("fr", "name")


def test_an_italian_name_in_grisons_is_italian() -> None:
    assert guess("Valposchiavo Turismo", "GR").language == "it"
    assert guess("ENTE TURISTICO REGIONALE DEL MOESANO SA", "GR").language == "it"


def test_a_german_compound_is_read_inside_the_word() -> None:
    assert guess("Skiliftgenossenschaft Wilern", "VS").language == "de"
    assert guess("Luftseilbahn Leukerbad-Gemmipass AG", "VS").language == "de"


def test_skilift_alone_is_not_evidence() -> None:
    """Used in the French-speaking Gruyère as readily as in Bern."""
    g = guess("Skilifts Rathvel S.A.", "FR")
    assert (g.language, g.basis, g.evidence) == ("fr", "legal form", "SA")


def test_sa_is_french_in_the_west_and_italian_in_ticino() -> None:
    assert guess("Musterlift SA", "VD").language == "fr"
    assert guess("Musterlift SA", "TI").language == "it"


def test_sa_settles_nothing_in_grisons() -> None:
    g = guess("Musterlift SA", "GR")
    assert (g.basis, g.language) == ("canton", "de")


def test_ag_and_gmbh_say_german() -> None:
    assert guess("Skilift Bumps AG", "BE").evidence == "AG"
    assert guess("Skilift Erli GmbH", "VS").language == "de"
    assert guess("Skilift A.G. Heimenschwand", "BE").evidence == "AG"


def test_sagl_says_italian_and_sarl_french() -> None:
    assert guess("Mustergiro Sagl", "GR").language == "it"
    assert guess("Musterrando Sàrl", "BE").language == "fr"


def test_the_canton_is_the_last_resort_and_says_so() -> None:
    g = guess("Musterverwaltung", "ZH")
    assert (g.language, g.basis, g.evidence, g.certain) == ("de", "canton", "ZH", True)


def test_a_canton_guess_is_uncertain_where_the_canton_is_bilingual() -> None:
    for canton in BILINGUAL:
        assert not guess("Muster", canton).certain, canton


def test_a_name_against_a_one_language_canton_is_kept_but_doubted() -> None:
    g = guess("European Outdoor Conservation Association", "ZG")
    assert g.language == "fr" and not g.certain


def test_a_german_name_in_valais_is_not_doubted() -> None:
    assert guess("Zermatt Bergbahnen AG", "VS").certain


def test_a_tie_between_two_languages_falls_to_the_suffix() -> None:
    g = guess("Tourismus Tourisme AG", "VS")
    assert g.basis == "legal form" and g.language == "de"


def test_accents_and_case_do_not_matter() -> None:
    assert guess("REMONTÉES MÉCANIQUES", "FR").language == "fr"
    assert guess("remontees mecaniques", "FR").language == "fr"


def test_every_guess_names_its_evidence() -> None:
    for name, canton in (("Zermatt Tourismus", "VS"), ("Skilift Bumps AG", "BE"),
                         ("Muster", "ZH")):
        assert guess(name, canton).label.split(": ")[1]


@pytest.mark.parametrize("canton", CANTONS)
def test_the_canton_rule_agrees_with_the_draft_stage(canton: str) -> None:
    """Repeated here because a source may not import a stage."""
    assert by_canton(canton) == language_of(canton)


def test_every_bilingual_canton_names_its_official_languages() -> None:
    """A campaign writing a canton in each of its languages reads them here."""
    from arpenteur.sources import language

    assert set(language.OFFICIAL) == language.BILINGUAL
    assert all(len(langs) >= 2 for langs in language.OFFICIAL.values())
