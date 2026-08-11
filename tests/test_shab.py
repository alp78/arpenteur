"""The SHAB parser, against notices recorded from the live register.

This parser is the only officer source the tool has, so its failure modes are
asymmetric. Missing a person costs a prospect. Inventing one, or naming an
officer who has left, puts a wrong human in a drafted email with the federal
register cited as the authority. The tests below weight accordingly: the
false-positive cases are asserted individually, recall is asserted in bulk.

Every string here is real text from the corpus in fixtures, not invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arpenteur.sources import shab

FIXTURE = Path(__file__).parent / "fixtures" / "shab_publications.json"


@pytest.fixture(scope="module")
def corpus() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def parse(company: dict) -> list[shab.Officer]:
    return shab.from_publications(
        [{"message": p["message"], "sogcDate": p["sogcDate"]} for p in company["sogcPub"]]
    )


# --- the traps ---------------------------------------------------------------


def test_communication_clause_yields_no_people() -> None:
    """The trap that shaped the design.

    "Communication aux associés: par lettre, téléfax ou email" is a role label
    followed by a delivery method. It appeared 42 times against 92 real name
    clauses in the corpus, so keying on the label alone would have made a
    third of the output fictional.
    """
    notice = (
        "Organe de publication: Feuille officielle suisse du Commerce. "
        "Communication aux associés: par lettre, téléfax ou email."
    )
    assert shab.officers(notice) == []


def test_struck_off_officers_are_not_recorded() -> None:
    """"Personnes radiées" lists people who have left the company.

    Recording them is worse than missing them: it would name the wrong person
    to approach and cite the register as the source.
    """
    notice = (
        "Personnes radiées: Sinani Arbenit, gérant, signature collective à deux."
    )
    assert shab.officers(notice) == []


def test_departing_officer_is_not_recorded_as_current() -> None:
    """A notice names both the arrivals and the departure, in the same prose.

    Real text. Pascual Carlos "n'est plus associé ni gérant" while Lüscher and
    Castella are current. Before this was handled the parser recorded him as a
    sitting associé, which is the worst failure available here: a drafted email
    addressed to someone who left, citing the federal register as authority.
    """
    notice = (
        "Associés: Lüscher Sabine, de Genève, à Satigny, pour 6 parts de "
        "CHF 1'000 et Castella Gilbert pour 6 parts de CHF 1'000. "
        "Pascual Carlos n'est plus associé ni gérant; ses pouvoirs sont radiés."
    )
    names = {o.full_name for o in shab.officers(notice)}
    assert names == {"Lüscher Sabine", "Castella Gilbert"}


def test_a_later_departure_overrides_an_earlier_appointment() -> None:
    """Merging across notices has to respect the mutation order.

    The 2021 notice appoints; the 2022 notice removes. Walking newest first
    and accumulating departures suppresses the stale appointment.
    """
    appointed = {
        "sogcDate": "2021-06-15",
        "message": "Associé: Chubatyuk Andriy, de Versoix, à Versoix, pour 200 parts de CHF 100.",
    }
    removed = {
        "sogcDate": "2022-01-07",
        "message": "Chubatyuk Andriy n'est plus associé, ses 200 parts ont été cédées.",
    }
    assert shab.from_publications([appointed, removed]) == []


def test_role_change_stated_without_a_label() -> None:
    """The register records role changes as prose, not as a labelled clause.

    11 of 139 notices used this form. Missing it lost officers who had been
    removed and reinstated in adjacent notices, which is the common shape of
    a role change.
    """
    assert [(o.full_name, o.role) for o in shab.officers(
        "au gérant Chubatyuk Andriy maintenant associé pour 100 parts de CHF 100"
    )] == [("Chubatyuk Andriy", "associé")]
    # The copula must not end up inside the name.
    assert [o.full_name for o in shab.officers(
        "Adriaansen Sarina est maintenant associée"
    )] == ["Adriaansen Sarina"]
    # A company reinstated as shareholder is still not a person.
    assert shab.officers(
        "Business Innovation Group SA ( CHE-113.588.997 ) est maintenant associée"
    ) == []


def test_reinstatement_cancels_a_departure_in_the_same_notice() -> None:
    """A role change removes and re-adds the same person in one notice.

    Treating the removal alone would drop a sitting officer.
    """
    notice = (
        "Dupont Marie n'est plus gérante. "
        "Dupont Marie est maintenant associée pour 50 parts de CHF 100."
    )
    assert [o.full_name for o in shab.officers(notice)] == ["Dupont Marie"]


def test_upstream_mojibake_is_repaired() -> None:
    """Some archived notices arrive with double-encoded accents.

    Corruption is partial: "AdminTech SÃ rl" carries a broken byte beside a
    correct one, so a blanket latin-1 round trip raises on the valid half.
    Repair is per known sequence for that reason.
    """
    assert "Sàrl" in shab.plain_text("<FT>AdminTech SÃ rl</FT>")
    assert "société" in shab.plain_text("sociÃ©tÃ©")
    # A clean string must pass through untouched.
    assert shab.repair_encoding("Société Sàrl à Genève") == "Société Sàrl à Genève"


def test_a_company_shareholder_is_not_a_person() -> None:
    """A legal entity can hold shares; `person` is for humans.

    Real notice text: a Sàrl holding 100 parts alongside a human gérant. The
    human must survive, the company must not.
    """
    notice = (
        "Personnes inscrites: SiBois Sàrl ( CHE-363.257.204 ), associée, "
        "100 parts de CHF 100; Sinani Arbenit, gérant, signature collective à deux."
    )
    names = {o.full_name for o in shab.officers(notice)}
    assert "Sinani Arbenit" in names
    assert not any("Sàrl" in n for n in names)


# --- the grammar -------------------------------------------------------------


def test_compound_role_is_not_truncated_to_its_prefix() -> None:
    """Regex alternation is first-match-wins.

    Listing "associé" before "associé-gérant" matches the shorter form and
    strands "-gérant:", losing the role. This asserts the ordering holds.
    """
    notice = (
        "Associé-gérant: Turbé Christian, de France, à Genève, pour 200 parts "
        "de CHF 100, avec signature individuelle."
    )
    officer = shab.officers(notice)[0]
    assert officer.full_name == "Turbé Christian"
    assert officer.role == "associé-gérant"
    assert officer.signature == "individuelle"
    assert officer.origin == "France"
    assert officer.domicile == "Genève"


def test_plural_and_gendered_labels_reduce_to_one_role() -> None:
    """The register writes six surface forms for one role.

    Keeping them apart would fragment every later count and filter.
    """
    for label in ("Gérant", "Gérants", "gérante", "Nouveaux gérants", "Nouveau gérant"):
        assert shab.normalise_role(label) == "gérant"
    assert shab.normalise_role("Associées-gérantes") == "associé-gérant"


def test_several_people_in_one_clause() -> None:
    notice = (
        "Nouveaux gérants: Blanc Dimitri Albert Gérard, de France, à "
        "Saint-Laurent-de-Mure, FRA et Vachod Patissier Léa, de France, à Mions, FRA."
    )
    names = {o.full_name for o in shab.officers(notice)}
    assert "Blanc Dimitri Albert Gérard" in names
    assert "Vachod Patissier Léa" in names


def test_clause_signature_applies_to_everyone_in_it() -> None:
    """"tous deux avec signature individuelle" is written once for the pair."""
    notice = (
        "Nouveaux gérants: l'associé Fernandes Ferreira Pedro Jorge, président, "
        "et Taurino Rosa, d'Italie, à Vernier, tous deux avec signature individuelle."
    )
    found = shab.officers(notice)
    assert len(found) == 2
    assert all(o.signature == "individuelle" for o in found)


def test_de_et_a_sets_origin_and_domicile_together() -> None:
    notice = "Associé-gérant: Haro Hugo Emilio, de et à Genève, président."
    officer = shab.officers(notice)[0]
    assert officer.origin == "Genève"
    assert officer.domicile == "Genève"


def test_html_is_stripped_and_entities_unescaped() -> None:
    assert "<FT>" not in shab.plain_text('<FT TYPE="N">Associé</FT>')
    assert shab.plain_text("Soci&eacute;t&eacute;") == "Société"


# --- deduplication -----------------------------------------------------------


def test_a_person_holding_two_roles_is_two_rows() -> None:
    """Associé and gérant are different facts about the same human.

    Both matter when deciding whom to approach, so the key is (name, role).
    """
    notice = (
        "Associé: Tricot Aurélien, de France, à Brenthonne, FRA, pour 20 parts "
        "de CHF 1'000. Gérants: l'associé Tricot Aurélien, président, et "
        "Jacquet Marc, de Plan-les-Ouates, à Lancy, tous deux avec signature individuelle."
    )
    roles = {(o.full_name, o.role) for o in shab.officers(notice)}
    assert ("Tricot Aurélien", "associé") in roles
    assert ("Tricot Aurélien", "gérant") in roles


def test_accent_variants_fold_to_one_person() -> None:
    assert shab.fold("Turbé Christian") == shab.fold("TURBE CHRISTIAN")


def test_newest_notice_wins_across_publications() -> None:
    """Officers change. The most recent statement is the current one."""
    old = {
        "sogcDate": "2019-01-01",
        "message": "Gérant: Ancien Pierre, de Genève, à Genève, avec signature collective à deux.",
    }
    new = {
        "sogcDate": "2026-01-01",
        "message": "Gérant: Ancien Pierre, de Genève, à Genève, avec signature individuelle.",
    }
    merged = shab.from_publications([old, new])
    assert [o.signature for o in merged] == ["individuelle"]


# --- the corpus as a whole ---------------------------------------------------


def test_corpus_yields_officers_without_junk(corpus: list[dict]) -> None:
    """Bulk recall and precision over 120 recorded notices.

    Roughly half the companies have a notice naming someone; the rest are
    address or capital changes that genuinely name nobody, which is why the
    bar is not higher.
    """
    officers = [o for company in corpus for o in parse(company)]
    assert len(officers) >= 50

    with_people = sum(1 for company in corpus if parse(company))
    assert with_people >= len(corpus) // 3

    for officer in officers:
        assert len(officer.full_name.split()) >= 2
        assert not any(ch.isdigit() for ch in officer.full_name)
        assert not shab.LEGAL_ENTITY.search(officer.full_name)
        assert officer.role


def test_every_role_is_in_the_known_vocabulary(corpus: list[dict]) -> None:
    """A role outside this set means the register used wording we have not seen.

    Worth failing on: it is the signal to go and read the new notice, rather
    than let an unrecognised role reach the database silently.
    """
    known = {
        "associé", "gérant", "associé-gérant", "administrateur", "liquidateur",
        "président", "directeur", "secrétaire", "réviseur", "personne inscrite",
    }
    seen = {o.role for company in corpus for o in parse(company)}
    assert seen <= known, f"unrecognised roles: {seen - known}"
