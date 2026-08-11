"""Attribution is a condition of Zefix access, so it is tested like one.

The register grants REST access free and asks two things in return about how
the data is used afterwards: state where it came from, and declare any
alteration. Anything this tool emits has to satisfy both, so the rules live
in code with tests rather than in a habit of remembering.
"""

from __future__ import annotations

from arpenteur import attribution


def test_every_notice_names_the_register_as_the_origin() -> None:
    """The first condition: origin must be indicated."""
    assert "Zefix" in attribution.notice()
    assert "registre du commerce" in attribution.notice().lower()


def test_every_notice_says_the_data_is_not_authoritative() -> None:
    """Only the certified cantonal excerpt and the FOSC text are binding.

    A drafted email that reads as though the register vouches for it would
    misstate what this data is, which the terms address directly.
    """
    text = attribution.notice().lower()
    assert "sans garantie" in text
    assert "fosc" in text


def test_registry_fields_alone_carry_no_modification_disclaimer() -> None:
    """The second condition is conditional, and saying it anyway would dilute it."""
    assert attribution.MODIFIED_NOTICE not in attribution.notice(["zefix"])


def test_parsed_officers_force_a_modification_disclaimer() -> None:
    """The case the disclaimer exists for.

    `person` rows are a regex parse of registrar prose, not registry fields.
    A parse can name someone who has resigned, so an output built on one must
    not read as the register speaking.
    """
    assert attribution.MODIFIED_NOTICE in attribution.notice(["zefix", "shab"])


def test_web_research_is_declared_as_derived_too() -> None:
    assert attribution.is_derived("web-search")
    assert attribution.is_derived("site")
    assert not attribution.is_derived("zefix")
    assert not attribution.is_derived(None)


def test_every_method_the_schema_writes_has_a_stated_origin() -> None:
    """A row whose origin cannot be named cannot be published under these terms."""
    for method in ("zefix", "shab", "web-search", "site"):
        assert attribution.ORIGIN.get(method), method
