"""Grading an organisation by what its registered purpose says.

Every purpose quoted below is a real register entry, abbreviated. The false
positives are real too: each one came out of a run over the live corpus, and
each is here so the rule that rejected it stays rejected.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from classify_tourism import BANDS, grade, in_cohort, name_pattern  # noqa: E402

WORDS = name_pattern(Path(__file__).resolve().parents[1] / "words" / "drymos.txt")


def band(purpose: str, name: str = "") -> str | None:
    verdict = grade(purpose, name)
    return verdict[0] if verdict else None


# --- tourism offices ---------------------------------------------------------

def test_a_tourism_office_that_runs_the_counter() -> None:
    """Moosalp Tourismus AG."""
    assert band(
        "bezweckt die gemeinsame Förderung des Tourismus in der Destination "
        "Moosalpregion, Betrieb der Tourismusbüros, Gästeinformation und "
        "Gästebetreuung",
        "Moosalp Tourismus AG",
    ) == "tourism-office"


def test_a_tourism_office_that_collects_the_visitor_tax() -> None:
    """Zermatt Tourismus. Only a body with a public mandate says this."""
    assert band(
        "Inkasso der Kurtaxen, Inkasso der Tourismusförderungstaxen",
        "Zermatt Tourismus",
    ) == "tourism-office"


def test_a_french_tourism_office() -> None:
    """Nyon Région Tourisme."""
    assert band(
        "la promotion touristique de la région en Suisse et sur les marchés "
        "définis, l'accueil, l'information et l'assistance touristique aux hôtes",
        "Nyon Région Tourisme",
    ) == "tourism-office"


def test_an_italian_tourism_office() -> None:
    """ENTE TURISTICO REGIONALE DEL MOESANO SA."""
    assert band(
        "Lo scopo della società consiste nella promozione del turismo nella "
        "frazione del Comune politico di Mesocco",
        "ENTE TURISTICO REGIONALE DEL MOESANO SA",
    ) == "tourism-office"


# --- nature parks ------------------------------------------------------------

def test_a_regional_nature_park() -> None:
    """Verein Naturpark Beverin, reciting the act that created the category."""
    assert band(
        "Zweck des Vereins «Naturpark Beverin» ist der Aufbau und Betrieb eines "
        "regionalen Naturparks im Sinne des revidierten eidgenössischen "
        "Natur- und Heimatschutzgesetzes",
        "Verein Naturpark Beverin",
    ) == "nature-park"


def test_a_french_regional_park() -> None:
    """Parc naturel régional de la Vallée du Trient."""
    assert band(
        "L'association a pour buts la création et la gestion d'un Parc naturel "
        "régional, afin de favoriser l'essor économique et social des communes "
        "membres",
        "Parc naturel régional de la Vallée du Trient",
    ) == "nature-park"


# --- mountain transport ------------------------------------------------------

def test_a_cable_car_operator() -> None:
    """Luftseilbahn Dallenwil-Wirzweli AG."""
    assert band(
        "bezweckt die Erstellung und den Betrieb einer Luftseilbahn von "
        "Dallenwil nach Wirzweli sowie weiterer touristischer Anlagen",
        "Luftseilbahn Dallenwil-Wirzweli AG",
    ) == "mountain-transport"


def test_a_french_lift_operator() -> None:
    """Bugnenets-Savagnières S.A."""
    assert band(
        "l'exploitation de remontées mécaniques ainsi que toutes activités de "
        "loisirs et de tourisme",
        "Bugnenets-Savagnières S.A.",
    ) == "mountain-transport"


def test_an_italian_lift_operator() -> None:
    """Monte Tamaro SA."""
    assert band(
        "la costruzione di sciovie, impianti di risalita e l'esercizio di "
        "questi impianti",
        "Monte Tamaro SA",
    ) == "mountain-transport"


def test_a_lift_company_that_markets_its_own_destination_is_still_a_lift() -> None:
    """Jaun-Gastlosen Bergbahnen AG.

    The purpose names tourism marketing, so the office rung would take it. The
    name settles it, which is the move `classify_regies.VEHICLE_NAME` makes.
    """
    assert band(
        "Die Gesellschaft bezweckt die Entwicklung, den Bau, den Betrieb, die "
        "Vermarktung und die Verwaltung von touristischen Infrastrukturen",
        "Jaun-Gastlosen Bergbahnen AG",
    ) == "mountain-transport"


# --- the trades that are not this one ----------------------------------------

def test_a_travel_agency_is_not_a_tourism_office() -> None:
    """Airpur Voyages Sàrl, which the first run over the corpus labelled."""
    assert band(
        "activité d'agent de voyage; production et vente de voyages ou séjours, "
        "individuels ou collectifs; production et vente de forfaits touristiques",
        "Airpur Voyages Sàrl",
    ) is None


def test_a_lift_manufacturer_is_not_a_lift_operator() -> None:
    """The Doppelmayr shape: builds and services them for other people."""
    assert band(
        "Herstellung, Montage und Revision von Seilbahnen und Skiliften",
        "Musterfirma Seilbahnen AG",
    ) is None


def test_a_landscaper_is_not_a_nature_park() -> None:
    """ALESSANDRO LORDELLI architecte paysagiste SARL."""
    assert band(
        "toute activité d'un bureau d'architecture du paysage soit la "
        "planification et l'aménagement du paysage, la vente de matériaux",
        "ALESSANDRO LORDELLI architecte paysagiste SARL",
    ) is None


def test_a_food_importer_named_for_a_footpath_is_not_in_the_trade() -> None:
    """Les Sentiers Pourpres Sàrl, caught by the name and rejected by the purpose."""
    assert band(
        "la sélection, l'importation, l'exportation, la commercialisation et la "
        "promotion de denrées alimentaires, de boissons alcoolisées",
        "Les Sentiers Pourpres Sàrl",
    ) is None


def test_a_trekking_tour_operator_is_not_a_guide() -> None:
    """Lucht Trekking Reisen GmbH. `Trekkingreisen` is one word in German."""
    assert band(
        "Planung, Organisation und Durchführung von weltweiten Wander- und "
        "Trekkingreisen",
        "Lucht Trekking Reisen GmbH",
    ) is None


def test_a_food_marketer_inside_a_biosphere_is_not_a_park() -> None:
    """Biosphäre Markt AG."""
    assert band(
        "Gemeinsame Vermarktung der Produkte von Verarbeitern aus der UNESCO "
        "Biosphäre Entlebuch",
        "Biosphäre Markt AG",
    ) is None


def test_the_permissions_clause_cannot_place_a_company_in_the_trade() -> None:
    """A dentist may buy a chalet, and says so in the notary's own words."""
    assert band(
        "Exploitation d'un cabinet dentaire. Elle peut exploiter des "
        "remontées mécaniques et promouvoir le tourisme.",
        "Cabinet dentaire Sàrl",
    ) is None


# --- the cohort gate ---------------------------------------------------------

def test_the_cohort_is_what_the_name_search_collected() -> None:
    assert in_cohort("Zermatt Tourismus", "", WORDS) is True
    assert in_cohort("Bellwald Gondelbahn AG", "", WORDS) is True
    assert in_cohort("Aubert Fiduciaire Sàrl", "", WORDS) is False


def test_a_strong_purpose_admits_a_company_the_name_would_miss() -> None:
    """Schilthornbahn is the shape: a destination body with no keyword in its name."""
    assert in_cohort("Musterbahn AG", "betrieb des tourismusburo", WORDS) is True


def test_the_words_file_and_the_pattern_agree_on_accents() -> None:
    """The register answers `*télécabine*` and not `*telecabine*`, and the name
    pattern reads the same file the search does, after folding both sides."""
    assert WORDS.search("telecabine")
    assert WORDS.search("Société de la Télécabine de Vercorin SA".lower()) is None, (
        "the pattern runs on folded text, so the caller folds first"
    )


def test_every_band_the_script_writes_is_declared_in_the_config() -> None:
    from arpenteur.config import load_settings

    declared = load_settings().segments
    assert [b for b in BANDS if b not in declared] == []
