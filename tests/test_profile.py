from __future__ import annotations

import sqlite3

from arpenteur.llm import extract_json
from arpenteur.stages.profile import build_prompt, parse_reply

SEGMENTS = {
    "sme-owner": "A small owner-run business.",
    "data-driven": "Work that produces or depends on substantial data.",
    "ai-adopter": "Software, automation or AI is part of what the company does.",
}


def row(**kwargs) -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    keys = ", ".join(kwargs)
    marks = ", ".join("?" for _ in kwargs)
    conn.execute(f"CREATE TABLE t ({keys})")
    conn.execute(f"INSERT INTO t ({keys}) VALUES ({marks})", tuple(kwargs.values()))
    return conn.execute("SELECT * FROM t").fetchone()


def test_extract_json_survives_prose_and_fences() -> None:
    assert extract_json('[{"uid": "a"}]') == [{"uid": "a"}]
    assert extract_json('Here you go:\n```json\n[{"uid": "b"}]\n```') == [{"uid": "b"}]
    assert extract_json('Result: [{"uid": "c"}] — hope that helps') == [{"uid": "c"}]


def test_prompt_lists_segments_and_truncates_purpose() -> None:
    long_purpose = "x" * 2000
    prompt = build_prompt(SEGMENTS, [row(uid="CHE-1", name="A", legal_form_label="Sàrl", purpose=long_purpose)])
    assert "sme-owner: A small owner-run business." in prompt
    assert "CHE-1" in prompt
    assert len(prompt) < 1500, "a runaway purpose must not blow up the prompt"


def test_parse_reply_drops_unknown_segments() -> None:
    reply = """[
      {"uid": "CHE-1", "segments": [
        {"segment": "data-driven", "confidence": 0.8, "rationale": "laboratory work"},
        {"segment": "invented-label", "confidence": 0.9, "rationale": "nope"}
      ]},
      {"uid": "CHE-2", "segments": []}
    ]"""
    parsed = parse_reply(reply, set(SEGMENTS))

    assert [s["segment"] for s in parsed["CHE-1"]] == ["data-driven"]
    assert parsed["CHE-2"] == [], "no segments is a valid answer, not a failure"


def test_parse_reply_clamps_confidence() -> None:
    reply = '[{"uid": "CHE-1", "segments": [{"segment": "sme-owner", "confidence": 7}]}]'
    assert parse_reply(reply, set(SEGMENTS))["CHE-1"][0]["confidence"] == 1.0

    reply = '[{"uid": "CHE-1", "segments": [{"segment": "sme-owner", "confidence": "n/a"}]}]'
    assert parse_reply(reply, set(SEGMENTS))["CHE-1"][0]["confidence"] == 0.0


def test_parse_reply_accepts_a_single_object() -> None:
    reply = '{"uid": "CHE-9", "segments": [{"segment": "ai-adopter", "confidence": 0.5}]}'
    assert "CHE-9" in parse_reply(reply, set(SEGMENTS))
