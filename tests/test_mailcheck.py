"""The name-to-domain guesses, and the legal forms they have to ignore.

`NAME_NOISE` held `"s.a"` and `"s.a.r.l"` for a while, which looked like care
and matched nothing: `WORD` is `[a-z0-9]+`, so a token never carries a dot.
The tests here pin what the list is actually for, which is that a Swiss legal
form never reaches a domain guess, in each of the four languages the register
writes names in.
"""

from __future__ import annotations

from arpenteur.sources import mailcheck


def test_the_tokeniser_never_yields_a_dotted_token():
    """The reason a dotted entry in NAME_NOISE could never match."""
    assert mailcheck.WORD.findall("immobiliare s.a.g.l.") == [
        "immobiliare", "s", "a", "g", "l"
    ]
    assert not any("." in word for word in mailcheck.NAME_NOISE)


def test_no_legal_form_survives_into_a_domain_guess():
    """Each language's form, dotted and plain, drops out of the stems."""
    for name in (
        "Régie du Lac SA",
        "Régie du Lac S.A.",
        "Seeblick Immobilien AG",
        "Clerc Immobilier Sàrl",
        "Clerc Immobilier S.à.r.l.",
        "Immobiliare Ceresio Sagl",
        "Immobiliare Ceresio S.A.G.L.",
        "Verwaltung Muster GmbH",
    ):
        stems = mailcheck.domain_candidates(name, seat="", places=frozenset())
        joined = " ".join(stems)
        for form in ("sa", "ag", "sarl", "sagl", "gmbh"):
            assert f" {form} " not in f" {joined} ", (
                f"{name!r} leaked the legal form {form!r} into {stems}"
            )
        assert stems, f"{name!r} produced no candidate at all"


def test_the_distinctive_word_is_the_firms_own():
    """What a page confirmation looks for, with the form and trade stripped."""
    words = mailcheck.distinctive_words(
        "Immobiliare Ceresio Sagl", seat="Lugano", places=frozenset({"lugano"})
    )
    assert "ceresio" in words
    assert "sagl" not in words
    assert "lugano" not in words


def test_a_duplicate_entry_cannot_hide_in_the_set():
    """`sagl` was listed twice, which a frozenset silently absorbs.

    The literal is what has to be checked, since the set itself cannot show a
    duplicate once it is built.
    """
    source = (
        __import__("pathlib").Path(mailcheck.__file__).read_text(encoding="utf-8")
    )
    block = source.partition("NAME_NOISE = frozenset({")[2].partition("})")[0]
    words = [w.strip().strip('"').strip("'") for w in block.split(",")]
    words = [w for w in words if w and not w.startswith("#")]
    assert len(words) == len(set(words)), (
        f"NAME_NOISE repeats: {sorted(w for w in words if words.count(w) > 1)}"
    )


def test_a_consumer_mailbox_is_recognised():
    """The nLPD distinction the survey suppresses on."""
    assert mailcheck.is_personal("arben72@msn.com")
    assert mailcheck.is_personal("muster@bluewin.ch")
    assert not mailcheck.is_personal("info@regie-du-lac.ch")


def test_a_domain_that_echoes_the_name_scores_for_each_word():
    assert mailcheck.domain_echoes_name("regie-du-lac.ch", "Régie du Lac SA") >= 1
    assert mailcheck.domain_echoes_name("example.ch", "Régie du Lac SA") == 0
