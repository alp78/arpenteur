"""Scoring as parts: what is judged, what is counted, and what is kept.

The old score was one integer from one model call. It ranked a list, which was
what it was for, and it could not answer the question anybody asks of a ranked
list: why is this one above that one.

Worse, it averaged things that move independently. The tests below are mostly
about that: an excellent fit nobody can reach and an excellent fit whose
chairman sits with a client both used to be 60, and 60 tells you to do nothing
in particular. Separated, one says find a contact and the other says ask for an
introduction.

Two further properties are checked because they are quiet and expensive to get
wrong. A missing component must not be counted as zero, or an unresearched
company looks actively bad rather than unknown. And the propensity at selection
has to be written down at the moment somebody picks a company out of the list,
because the scores themselves move and it cannot be reconstructed afterwards.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection
from arpenteur.modules.scout import scoring, store as scout

pytestmark = pytest.mark.pg


def _company(conn: Connection, uid: str = "CHE1", name: str = "Fiduciaire Sàrl") -> int:
    return db.upsert_company(conn, {"uid": uid, "name": name, "canton": "GE"})


# --- combining ---------------------------------------------------------------


def test_components_combine_by_weight():
    total, band = scoring.combine(
        {"icp_fit": 80, "need": 80, "timing": 80, "value": 80,
         "reach": 80, "relationship": 80}
    )
    assert total == Decimal("80.00") and band == "high"


def test_an_unreachable_prospect_scores_below_the_models_opinion_of_it():
    """The whole point of splitting the score. Both of these used to be one
    number in the sixties, and the two want completely different actions."""
    unreachable, _ = scoring.combine(
        {"icp_fit": 90, "need": 85, "timing": 70, "value": 70,
         "reach": 0, "relationship": 0}
    )
    connected, _ = scoring.combine(
        {"icp_fit": 90, "need": 85, "timing": 70, "value": 70,
         "reach": 90, "relationship": 85}
    )
    assert unreachable < connected
    assert unreachable < Decimal("70")


def test_a_missing_component_is_unknown_rather_than_zero():
    """Counting it as zero makes an unresearched company look actively bad,
    and an unresearched company is exactly the one worth going to look at."""
    partial, _ = scoring.combine({"icp_fit": 80, "need": 80})
    zeroed, _ = scoring.combine(
        {"icp_fit": 80, "need": 80, "timing": 0, "value": 0, "reach": 0,
         "relationship": 0}
    )
    assert partial == Decimal("80.00")
    assert zeroed < partial


def test_a_fit_of_nearly_zero_excludes_whatever_else_is_true():
    """The brief said this is not who the campaign serves. No amount of
    reachability changes that, and averaging would let five good components
    bury it."""
    _total, band = scoring.combine(
        {"icp_fit": 2, "need": 90, "timing": 90, "value": 90, "reach": 90,
         "relationship": 90}
    )
    assert band == "excluded"


def test_a_score_outside_the_scale_is_refused():
    with pytest.raises(scoring.ScoringError, match="outside 0 to 100"):
        scoring.combine({"icp_fit": 140})


def test_a_score_with_no_components_is_not_a_score():
    with pytest.raises(scoring.ScoringError):
        scoring.combine({})


def test_weights_are_renormalised_rather_than_demanded_exact():
    found = scoring.normalise({"icp_fit": 2, "need": 1, "reach": 1})
    assert sum(found.values()) == Decimal("1.000")
    assert found["icp_fit"] == Decimal("0.500")


def test_a_weight_set_of_nothing_usable_is_refused():
    with pytest.raises(scoring.ScoringError, match="no usable weights"):
        scoring.normalise({"nonsense": 1})


# --- the two that are counted ------------------------------------------------


def test_reach_counts_a_named_person_above_a_general_address():
    """Both are contacts. Only one of them reaches somebody who can decide."""
    named, _r, _e = scoring.reach_score(
        [{"kind": "email", "value": "a@f.ch", "person_id": 4, "suppressed": False}]
    )
    general, _r, _e = scoring.reach_score(
        [{"kind": "email", "value": "info@f.ch", "person_id": None,
          "suppressed": False}]
    )
    assert named > general > 0


def test_a_suppressed_address_counts_against_rather_than_being_ignored():
    """Somebody said do not write here. The one route that existed has been
    closed, which is worse than never having had one."""
    only_suppressed, reason, _e = scoring.reach_score(
        [{"kind": "email", "value": "a@f.ch", "suppressed": True}]
    )
    assert only_suppressed == Decimal(0)
    assert "having said not to write" in reason


def test_no_contacts_is_zero_and_says_so():
    score, reason, _e = scoring.reach_score([])
    assert score == Decimal(0) and "no contacts" in reason


def test_relationship_counts_a_current_seat_above_a_past_one():
    """"They sat together at a company neither is at now" is a real link and a
    poor introduction, and the two must not look the same."""
    current, _r, _e = scoring.relationship_score(
        [{"full_name": "A. Dupont", "other_name": "Client SA", "worked": True,
          "left_on": None}]
    )
    past, _r, _e = scoring.relationship_score(
        [{"full_name": "A. Dupont", "other_name": "Client SA", "worked": True,
          "left_on": "2019-04-01"}]
    )
    assert current > past > 0


def test_a_connection_to_nothing_being_worked_counts_for_little():
    score, reason, _e = scoring.relationship_score(
        [{"full_name": "A. Dupont", "other_name": "Autre SA", "worked": False,
          "left_on": None}]
    )
    assert Decimal(0) < score <= Decimal(25)
    assert "none at a company being worked" in reason


# --- storing a run -----------------------------------------------------------


def test_a_run_keeps_its_components_and_their_evidence(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn)
    run_id = scout.save_score_run(conn, company_id, {
        "total": Decimal("72.50"), "band": "high", "model": "test",
        "reasoning": "Fits the brief.",
        "components": [
            {"component": "icp_fit", "score": 85, "weight": Decimal("0.30"),
             "origin": "model", "reasoning": "In the target size.",
             "evidence": [
                 {"direction": "for", "statement": "fiduciaire de 20 personnes"},
                 {"direction": "against", "statement": "peut-être déjà accompagnée"},
             ]},
            {"component": "reach", "score": 0, "weight": Decimal("0.10"),
             "origin": "computed", "reasoning": "nothing usable held",
             "evidence": ["nothing usable held"]},
        ],
    })
    parts = scout.components_of(conn, run_id)
    assert [p["component"] for p in parts] == ["icp_fit", "reach"]
    assert parts[0]["origin"] == "model" and parts[1]["origin"] == "computed"

    evidence = scout.evidence_of(conn, int(parts[0]["id"]))
    assert {e["direction"] for e in evidence} == {"for", "against"}
    conn.close()


def test_a_run_projects_onto_the_score_every_list_still_ranks_by(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn)
    scout.save_score_run(conn, company_id, {
        "total": Decimal("72.50"), "band": "high", "model": "test",
        "matched": ["site ancien"], "against": ["déjà accompagnée"],
        "components": [{"component": "icp_fit", "score": 85}],
    })
    fit = scout.fit_for(conn, company_id)
    assert fit["score"] == 72 and fit["band"] == "high"
    conn.close()


def test_a_manual_score_still_outranks_a_run(db_path):
    """The guarantee that made the console a tool rather than a display. It
    has to survive the score becoming six numbers instead of one."""
    conn = db.open_db(db_path)
    company_id = _company(conn)
    scout.set_fit_manually(conn, company_id, score=10, band="low",
                           reasoning="already a client")
    scout.save_score_run(conn, company_id, {
        "total": Decimal("90"), "band": "high", "model": "test",
        "components": [{"component": "icp_fit", "score": 90}],
    })
    fit = scout.fit_for(conn, company_id)
    assert (fit["score"], fit["source"]) == (10, "manual")
    # And the run is still recorded, so the disagreement is visible rather
    # than lost.
    assert scout.latest_score(conn, company_id)["total"] == Decimal("90.00")
    conn.close()


def test_rescoring_keeps_the_earlier_run(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn)
    for total in (60, 75):
        scout.save_score_run(conn, company_id, {
            "total": Decimal(total), "band": "medium", "model": "test",
            "components": [{"component": "icp_fit", "score": total}],
        })
    history = scout.score_history(conn, company_id)
    assert [int(r["total"]) for r in history] == [75, 60]
    conn.close()


# --- weight sets ---------------------------------------------------------------


def test_a_weight_set_is_versioned_rather_than_edited(db_path):
    """Re-weighting a campaign must not rewrite what last month's ranking
    was, and a run cites the set it used."""
    conn = db.open_db(db_path)
    first = scout.save_weight_set(conn, "campaign", {"icp_fit": 1, "need": 1})
    second = scout.save_weight_set(conn, "campaign", {"icp_fit": 3, "need": 1})
    assert first != second
    sets = {(s["name"], s["version"]): s for s in scout.weight_sets(conn)}
    assert ("campaign", 1) in sets and ("campaign", 2) in sets
    assert not sets[("campaign", 1)]["active"]
    assert sets[("campaign", 2)]["active"]
    conn.close()


def test_only_one_weight_set_is_ever_in_force(db_path):
    conn = db.open_db(db_path)
    scout.save_weight_set(conn, "a", {"icp_fit": 1})
    scout.save_weight_set(conn, "b", {"need": 1})
    active = [s for s in scout.weight_sets(conn) if s["active"]]
    assert len(active) == 1 and active[0]["name"] == "b"
    conn.close()


def test_with_no_weight_set_the_default_is_used_and_says_it_is_a_default(db_path):
    conn = db.open_db(db_path)
    set_id, weights = scout.active_weights(conn)
    assert set_id is None
    assert weights == scoring.DEFAULT_WEIGHTS
    assert "starting point" in scoring.DEFAULT_WEIGHT_SET
    conn.close()


# --- observations ----------------------------------------------------------------


def test_two_sources_may_disagree_without_either_being_destroyed(db_path):
    """The case the table exists for. Today's `intel.site` has one row per
    company and the last writer wins."""
    conn = db.open_db(db_path)
    company_id = _company(conn)
    register = scout.save_source_document(conn, {
        "company_id": company_id, "kind": "register", "licence": "zefix",
    })
    site = scout.save_source_document(conn, {
        "company_id": company_id, "kind": "site", "url": "https://f.ch",
        "content_sha256": "abc",
    })
    scout.observe(conn, company_id, {
        "attribute": "staff.count", "value": "12", "value_num": 12,
        "confidence": 0.6, "source_document_id": register,
    })
    scout.observe(conn, company_id, {
        "attribute": "staff.count", "value": "20", "value_num": 20,
        "confidence": 0.8, "source_document_id": site,
    })
    held = scout.observations_for(conn, company_id, attribute="staff.count")
    assert len(held) == 2
    # Ordered by confidence, so a reader taking the first gets the best claim
    # without the other having been thrown away.
    assert held[0]["value"] == "20"

    contested = scout.disagreements(conn, company_id)
    assert [row["attribute"] for row in contested] == ["staff.count"]
    conn.close()


def test_superseding_keeps_what_was_once_believed(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn)
    old = scout.observe(conn, company_id, {"attribute": "site.url",
                                           "value": "https://old.ch"})
    new = scout.observe(conn, company_id, {"attribute": "site.url",
                                           "value": "https://new.ch"})
    scout.supersede(conn, old, by=new)

    live = scout.observations_for(conn, company_id, attribute="site.url")
    assert [row["value"] for row in live] == ["https://new.ch"]
    everything = scout.observations_for(conn, company_id, attribute="site.url",
                                        include_superseded=True)
    assert len(everything) == 2
    conn.close()


def test_the_same_document_twice_is_one_row(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn)
    first = scout.save_source_document(conn, {
        "company_id": company_id, "kind": "site", "url": "https://f.ch",
        "content_sha256": "abc",
    })
    second = scout.save_source_document(conn, {
        "company_id": company_id, "kind": "site", "url": "https://f.ch",
        "content_sha256": "abc",
    })
    assert first == second
    # A changed page is a different document, because a claim read from it was
    # read from those bytes and not from these.
    third = scout.save_source_document(conn, {
        "company_id": company_id, "kind": "site", "url": "https://f.ch",
        "content_sha256": "def",
    })
    assert third != first
    conn.close()


def test_an_attribute_prefix_fetches_the_family(db_path):
    conn = db.open_db(db_path)
    company_id = _company(conn)
    scout.observe(conn, company_id, {"attribute": "site.signal", "value": "old"})
    scout.observe(conn, company_id, {"attribute": "site.summary", "value": "x"})
    scout.observe(conn, company_id, {"attribute": "staff.count", "value": "12"})
    found = scout.observations_for(conn, company_id, attribute="site")
    assert {row["attribute"] for row in found} == {"site.signal", "site.summary"}
    conn.close()


# --- the propensity at selection --------------------------------------------------


def test_picking_a_company_records_where_it_stood(db_path):
    """Outcomes are only ever seen for what was approached, and what was
    approached was chosen by score. Without this, anything later learned from
    those outcomes is learned from the system's own past choices."""
    conn = db.open_db(db_path)
    high = _company(conn, "CHE1", "Top")
    middle = _company(conn, "CHE2", "Middle")
    _low = _company(conn, "CHE3", "Bottom")
    for company_id, score in ((high, 90), (middle, 60), (_low, 20)):
        scout.save_fit(conn, company_id, {"score": score, "band": "medium"})

    scout.set_prospect(conn, middle, state="contacted", by="alex")
    row = scout.prospect_for(conn, middle)
    assert row["selected_at"] is not None
    assert row["selection_score"] == 60
    # Two of the three scored at or below it.
    assert float(row["selection_p"]) == pytest.approx(2 / 3, abs=0.01)
    conn.close()


def test_the_propensity_is_written_once_and_never_updated(db_path):
    """What was true at the moment of the decision. A later scoring is a
    different fact and belongs in a different row."""
    conn = db.open_db(db_path)
    company_id = _company(conn)
    scout.save_fit(conn, company_id, {"score": 40, "band": "low"})
    scout.set_prospect(conn, company_id, state="contacted")
    first = scout.prospect_for(conn, company_id)["selection_p"]

    scout.save_fit(conn, company_id, {"score": 95, "band": "high"})
    scout.set_prospect(conn, company_id, state="meeting")
    assert scout.prospect_for(conn, company_id)["selection_p"] == first
    assert scout.prospect_for(conn, company_id)["selection_score"] == 40
    conn.close()


def test_arriving_on_the_board_as_new_is_not_a_selection(db_path):
    """Nobody has picked it out of anything yet."""
    conn = db.open_db(db_path)
    company_id = _company(conn)
    scout.save_fit(conn, company_id, {"score": 40, "band": "low"})
    scout.set_prospect(conn, company_id, state="new")
    assert scout.prospect_for(conn, company_id)["selected_at"] is None
    conn.close()


def test_an_unscored_company_records_no_propensity(db_path):
    """Rather than a zero, which would read as "certainly not selected" for a
    company that was in fact selected."""
    conn = db.open_db(db_path)
    company_id = _company(conn)
    scout.set_prospect(conn, company_id, state="contacted")
    assert scout.prospect_for(conn, company_id)["selection_p"] is None
    conn.close()


# --- the page --------------------------------------------------------------------


def test_the_company_page_shows_what_the_score_is_made_of(db_path):
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from arpenteur.config import Territory
    from arpenteur.console.app import create_app

    conn = db.open_db(db_path)
    company_id = _company(conn)
    db.save_contact(conn, company_id=company_id, kind="email", value="a@f.ch",
                    method="published")
    scout.save_score_run(conn, company_id, {
        "total": Decimal("61.50"), "band": "medium", "model": "test",
        "components": [
            {"component": "icp_fit", "score": 85, "weight": Decimal("0.30"),
             "origin": "model", "reasoning": "In the target size.",
             "evidence": [{"direction": "against",
                           "statement": "peut-être déjà accompagnée"}]},
            {"component": "reach", "score": 0, "weight": Decimal("0.10"),
             "origin": "computed", "reasoning": "nothing usable held",
             "evidence": ["nothing usable held"]},
        ],
    })
    conn.commit()
    conn.close()

    settings = SimpleNamespace(
        database_url=db_path,
        territory=Territory(cantons=("GE",), legal_forms=("0107",), name="geneve"),
        segments={},
    )
    page = TestClient(create_app(settings)).get(f"/company/{company_id}").text
    assert "how the score is made" in page
    # The distinction a reader needs in order to know which numbers to argue
    # with.
    assert "computed" in page and "model" in page
    assert "peut-être déjà accompagnée" in page or "accompagn" in page
    assert "nothing usable held" in page
