"""A person's judgement outranks the model's, permanently.

The defect this pins was real and quiet: `save_segments` did an unconditional
ON CONFLICT DO UPDATE, so correcting a label and then re-running `profile`
restored the model's answer with no error and no trace. That makes the
correction feel like work and be worthless, which is worse than not offering
it, and it is exactly the kind of thing nobody notices until they have done it
fifty times.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arpenteur import db
from arpenteur.kernel.db import Connection, Row


@pytest.fixture
def conn(db_path: Path) -> Connection:
    connection = db.open_db(db_path)
    db.upsert_company(
        connection,
        {"uid": "CHE1", "name": "Atelier Sàrl", "canton": "VD", "method": "zefix"},
    )
    connection.commit()
    return connection


def classify(conn, segment: str, confidence: float = 0.6) -> None:
    db.save_segments(
        conn, 1, [{"segment": segment, "confidence": confidence,
                   "rationale": "the model's reading"}], "claude-haiku-4-5",
    )


# --- the defect ---------------------------------------------------------------


def test_a_manual_label_survives_re_classification(conn) -> None:
    """The whole reason this migration exists."""
    classify(conn, "sme-owner")
    db.set_segment_manually(conn, 1, "sme-owner", rationale="spoke to the owner")

    classify(conn, "sme-owner", confidence=0.2)

    row = conn.execute("SELECT * FROM intel.segment").fetchone()
    assert row["source"] == "manual"
    assert row["rationale"] == "spoke to the owner"
    assert row["confidence"] == 1.0, "the model's confidence overwrote a human's"


def test_the_model_may_still_correct_its_own_earlier_answer(conn) -> None:
    """The guard must not freeze everything, only what a person touched."""
    classify(conn, "sme-owner", confidence=0.4)
    classify(conn, "sme-owner", confidence=0.9)
    row = conn.execute("SELECT confidence, source FROM intel.segment").fetchone()
    assert (row["confidence"], row["source"]) == (0.9, "model")


def test_a_label_added_by_hand_is_marked_as_such(conn) -> None:
    """Without the mark there is no way to tell a correction from a guess,
    and the console cannot show which is which."""
    db.set_segment_manually(conn, 1, "compliance-sector", author="alex")
    row = conn.execute("SELECT * FROM intel.segment").fetchone()
    assert row["source"] == "manual" and row["model"] == "alex"


def test_a_label_removed_by_hand_goes_away(conn) -> None:
    """The false positive case: the model saw a segment that is not there."""
    classify(conn, "ai-adopter")
    db.set_segment_manually(conn, 1, "ai-adopter", present=False)
    assert conn.execute("SELECT COUNT(*) AS n FROM intel.segment").fetchone()[0] == 0


# --- the follow-up log --------------------------------------------------------


def test_notes_accumulate_rather_than_replace(conn) -> None:
    """Two calls on the same day are two facts."""
    db.add_note(conn, 1, "Called, no answer.", kind="call")
    db.add_note(conn, 1, "Called again, spoke to the owner.", kind="call")
    assert len(db.notes_for(conn, 1)) == 2


def test_a_note_records_when_it_happened_not_when_it_was_typed(conn) -> None:
    """A call logged the next morning belongs on the day of the call."""
    db.add_note(conn, 1, "Coffee at their office.", kind="meeting",
                occurred_at="2026-08-04T09:00:00+00:00")
    row = db.notes_for(conn, 1)[0]
    assert row["occurred_at"].startswith("2026-08-04")
    assert row["created_at"] != row["occurred_at"]


def test_notes_die_with_the_company_row(conn) -> None:
    """Cascade, so a deleted company cannot leave orphaned personal data."""
    db.add_note(conn, 1, "Anything.")
    conn.execute("DELETE FROM core.company WHERE id = 1")
    assert conn.execute("SELECT COUNT(*) AS n FROM intel.note").fetchone()[0] == 0


# --- pipeline state -----------------------------------------------------------


def test_a_company_nobody_has_looked_at_has_no_prospect_row(conn) -> None:
    """Absence means untouched, which "new" for all 183k would not."""
    assert conn.execute("SELECT COUNT(*) AS n FROM intel.prospect").fetchone()[0] == 0


def test_a_partial_edit_leaves_the_other_fields_alone(conn) -> None:
    """The bug a plain UPDATE with defaults makes on every form submission:
    setting a next action would silently reset the state to 'new'."""
    db.set_prospect(conn, 1, state="meeting", owner="alex")
    db.set_prospect(conn, 1, next_action="send the proposal", next_at="2026-08-20")
    row = conn.execute("SELECT * FROM intel.prospect").fetchone()
    assert row["state"] == "meeting"
    assert row["owner"] == "alex"
    assert row["next_action"] == "send the proposal"
