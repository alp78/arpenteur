"""Reading a reply to the enrolment message.

What is worth testing is not that a regex matches. It is the four judgements
this module makes, each of which was chosen over an easier one:

that a reply naming both platforms names neither, because enrolling a tester on
the wrong store track costs a place in a cohort of twelve; that agreement to the
dated window comes back as a hint and never as a verdict, because a reply
declining the dates carries every token that accepting them carries; that the
address offered as the tester's account is never the firm's own, which appears
in nearly every body because a reply quotes what it answers; and that a refusal
settles the message before anything else in it is read.
"""

from __future__ import annotations

from arpenteur.sources import enrol


# --- the platform -------------------------------------------------------------


def test_a_platform_is_read_in_three_languages() -> None:
    assert enrol.platform_in("Nous testerons sur Android")[0] == enrol.ANDROID
    assert enrol.platform_in("Wir nehmen das iPhone")[0] == enrol.IOS
    assert enrol.platform_in("Useremo un iPad")[0] == enrol.IOS


def test_testflight_names_ios_without_the_word() -> None:
    """Apple's product names carry across untranslated, which is the point."""
    platform, why = enrol.platform_in("D'accord pour TestFlight")
    assert platform == enrol.IOS
    assert "testflight" in why


def test_a_reply_naming_both_platforms_names_neither() -> None:
    platform, why = enrol.platform_in("iPhone ou Android, comme vous voulez")
    assert platform == "", "a coin toss would put a tester on the wrong track"
    assert "both" in why


def test_google_alone_names_no_platform() -> None:
    """An Android tester sends a Google account, so the bare word proves little."""
    assert enrol.platform_in("mon compte Google est pret")[0] == ""


def test_an_accented_reply_still_reads() -> None:
    assert enrol.platform_in("Téléphone Android, d'accord")[0] == enrol.ANDROID


# --- the tester's account -----------------------------------------------------


def test_the_firms_own_address_is_never_the_testers_account() -> None:
    body = (
        "Bonjour, oui pour Android.\n\n"
        "> De: alexis@peringer.ch\n"
        "> A: info@regie-x.ch\n"
    )
    at, why = enrol.account_in(
        body, ours=frozenset({"peringer.ch"}),
        theirs=frozenset({"info@regie-x.ch"}),
    )
    assert at == "", why


def test_a_google_account_wins_over_any_other_address() -> None:
    body = "Mon collegue jean.dupont@regie-x.ch teste, compte jd1978@gmail.com"
    at, why = enrol.account_in(body, theirs=frozenset())
    assert at == "jd1978@gmail.com"
    assert "Google" in why


def test_an_address_at_the_operators_domain_is_never_offered() -> None:
    at, _ = enrol.account_in(
        "ecrivez a alexis@peringer.ch", ours=frozenset({"peringer.ch"})
    )
    assert at == ""


def test_a_trailing_full_stop_is_not_part_of_the_address() -> None:
    at, _ = enrol.account_in("compte: jd@gmail.com.")
    assert at == "jd@gmail.com"


# --- the dated window --------------------------------------------------------


def test_agreement_to_the_dates_is_a_hint_and_never_a_verdict() -> None:
    reading = enrol.read("Re: Limen", "D'accord pour Android, jd@gmail.com")
    assert reading.window_said, "the token is reported"
    assert not hasattr(reading, "window_ok"), (
        "the reading never carries a verdict on the dates; a person sets it"
    )


def test_a_reply_declining_the_dates_carries_the_same_tokens() -> None:
    """Why the hint is not a verdict, stated as a test."""
    reading = enrol.read(
        "Re: Limen",
        "Android nous convient, mais pas ces dates: fevrier serait mieux.",
    )
    assert reading.verdict == enrol.ANSWER
    assert reading.window_said, (
        "a declining reply matches the same token, so acting on it would "
        "enrol a tester who said no"
    )


# --- the whole reading -------------------------------------------------------


def test_a_refusal_settles_the_message_before_anything_else_is_read() -> None:
    reading = enrol.read("Re: Limen", "STOP. Nous testons sur Android.")
    assert reading.verdict == enrol.REFUSAL
    assert reading.platform == "", (
        "somebody asking to be left alone has not enrolled, whatever else "
        "the message mentions"
    )
    assert reading.refusal == "stop"


def test_an_android_answer_needs_the_account_to_be_enrollable() -> None:
    bare = enrol.read("Re: Limen", "Nous prenons Android.")
    assert bare.verdict == enrol.ANSWER
    assert bare.platform == enrol.ANDROID
    assert not bare.enrollable, "Google opens the track to named accounts only"

    whole = enrol.read("Re: Limen", "Android, compte jd@gmail.com")
    assert whole.enrollable


def test_an_ios_answer_is_enrollable_with_nothing_else() -> None:
    """TestFlight is a public link and no address reaches us."""
    reading = enrol.read("Re: Limen", "Volontiers, sur iPhone.")
    assert reading.enrollable
    assert reading.account == ""


def test_the_senders_own_address_is_searched_for_the_account() -> None:
    reading = enrol.read(
        "Re: Limen", "Oui pour Android, voici mon compte.",
        sender="jd1978@gmail.com",
    )
    assert reading.account == "jd1978@gmail.com", (
        "a tester writing from their Google account gave it in the envelope"
    )


def test_a_reply_with_neither_platform_nor_account_is_not_an_answer() -> None:
    reading = enrol.read("Re: Limen", "Merci, nous revenons vers vous.")
    assert reading.verdict == enrol.OTHER
    assert not reading.enrollable


def test_every_reading_says_why() -> None:
    reading = enrol.read("Re: Limen", "Android, jd@gmail.com, d'accord")
    assert len(reading.why) >= 3, (
        "the page shows these under the proposal, so a reviewer can disagree "
        "with the reading rather than only with the result"
    )


def test_an_out_of_office_enrols_nobody() -> None:
    reading = enrol.read(
        "Absence du bureau",
        "Je suis absent jusqu'au 20 octobre. En cas d'urgence, "
        "appelez le secretariat.",
    )
    assert reading.verdict == enrol.OTHER
