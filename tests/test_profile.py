from __future__ import annotations


from arpenteur.kernel.db import Record
from arpenteur.llm import extract_json
from arpenteur.stages.profile import (
    SYSTEM, build_prompt, parse_reply, system_prompt,
)

SEGMENTS = {
    "sme-owner": "A small owner-run business.",
    "data-driven": "Work that produces or depends on substantial data.",
    "ai-adopter": "Software, automation or AI is part of what the company does.",
}

SECTORS = {
    "hospitality": "Restaurants, cafés, bars, catering, hotels.",
    "construction": "Building and civil works, and the trades on site.",
    "other": "Too general to place.",
}


def row(**kwargs) -> Record:
    """A stand-in row. build_prompt only reads keys, so it needs no database."""
    return Record(kwargs)


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

    assert [s["segment"] for s in parsed["CHE-1"].segments] == ["data-driven"]
    assert parsed["CHE-2"].segments == [], "no segments is a valid answer, not a failure"


def test_parse_reply_clamps_confidence() -> None:
    reply = '[{"uid": "CHE-1", "segments": [{"segment": "sme-owner", "confidence": 7}]}]'
    assert parse_reply(reply, set(SEGMENTS))["CHE-1"].segments[0]["confidence"] == 1.0

    reply = '[{"uid": "CHE-1", "segments": [{"segment": "sme-owner", "confidence": "n/a"}]}]'
    assert parse_reply(reply, set(SEGMENTS))["CHE-1"].segments[0]["confidence"] == 0.0


def test_parse_reply_accepts_a_single_object() -> None:
    reply = '{"uid": "CHE-9", "segments": [{"segment": "ai-adopter", "confidence": 0.5}]}'
    assert "CHE-9" in parse_reply(reply, set(SEGMENTS))


# --- the sector, which is one per company rather than several -----------------


def test_prompt_asks_for_a_sector_only_when_there_is_a_vocabulary() -> None:
    company = [row(uid="CHE-1", name="A", legal_form_label="Sàrl", purpose="Café.")]

    without = build_prompt(SEGMENTS, company)
    assert "Sectors" not in without
    assert "hospitality" not in without

    with_sectors = build_prompt(SEGMENTS, company, SECTORS)
    assert "hospitality: Restaurants" in with_sectors
    assert "choose exactly one" in with_sectors
    # Both questions in one prompt is the whole reason the sector costs
    # nothing extra: one call reads the purpose, not two.
    assert "sme-owner" in with_sectors


def test_system_prompt_is_unchanged_without_sectors() -> None:
    """A survey with no sector list must get exactly the prompt it always got."""
    assert system_prompt(False) == SYSTEM
    assert "sector" not in SYSTEM
    assert "sector_confidence" in system_prompt(True)


def test_parse_reply_reads_the_sector() -> None:
    reply = """[{"uid": "CHE-1", "sector": "hospitality",
                 "sector_confidence": 0.9, "sector_rationale": "exploitation d'un café",
                 "segments": [{"segment": "sme-owner", "confidence": 0.7}]}]"""
    verdict = parse_reply(reply, set(SEGMENTS), set(SECTORS))["CHE-1"]

    assert verdict.sector["sector"] == "hospitality"
    assert verdict.sector["confidence"] == 0.9
    assert verdict.sector["rationale"] == "exploitation d'un café"
    assert [s["segment"] for s in verdict.segments] == ["sme-owner"]


def test_parse_reply_drops_an_invented_sector() -> None:
    """A stray sector would put a name in the filter that nothing defines."""
    reply = '[{"uid": "CHE-1", "sector": "restauration-de-luxe", "segments": []}]'
    assert parse_reply(reply, set(SEGMENTS), set(SECTORS))["CHE-1"].sector is None


def test_parse_reply_ignores_a_sector_when_none_are_configured() -> None:
    reply = '[{"uid": "CHE-1", "sector": "hospitality", "segments": []}]'
    assert parse_reply(reply, set(SEGMENTS))["CHE-1"].sector is None


def test_a_missing_sector_is_not_invented() -> None:
    """`other` means unplaceable, not unanswered, and the two must not merge.

    Writing `other` for a reply that named no sector would mark the company
    classified and it would never be asked again, on the strength of an answer
    the model did not give.
    """
    reply = '[{"uid": "CHE-1", "segments": [{"segment": "sme-owner"}]}]'
    assert parse_reply(reply, set(SEGMENTS), set(SECTORS))["CHE-1"].sector is None
