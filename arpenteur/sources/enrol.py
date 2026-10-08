"""Reading a reply to the enrolment message.

`limen.md` asks every reply for four points: the firm, the platform, the Google
account when the platform is Android, and agreement to the dated window. This
module reads a stored message and says what it finds. It decides nothing.

**Three of the four points are read, the fourth is asked.** A platform is a
closed vocabulary, an account is a pattern, and both survive translation into
German and Italian. Agreement to a window is a sentence, and a reply saying
« la période de février nous irait mieux » carries every token that agreement
carries while meaning its opposite. So the tokens found are reported as a hint
for the person reading, and `window_ok` stays unset until somebody sets it.

**A reply naming both platforms names neither.** Enrolling a tester on the
wrong track costs a round trip and, on Android, a place in a cohort of twelve.
Two platforms in one message is a question for the operator rather than a
coin toss, so `platform` comes back empty with the reason attached.

**The account is never the firm's own address.** A reply quotes the message it
answers, so the mailbox written to appears in almost every body. Addresses the
survey already holds for that firm are excluded, as is the operator's own
domain, which leaves the address a person typed on purpose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import bounce, mail, mailcheck

# What this module concludes about one message.
ANSWER = "answer"
REFUSAL = "refusal"
OTHER = "other"

IOS = "ios"
ANDROID = "android"

# The platform, in the three languages the draft stage writes. Apple's own
# product names carry across untranslated, which is what makes this closed.
_IOS = (
    "ios", "iphone", "ipad", "apple", "testflight", "test flight",
    "app store", "appstore",
)
# "google" alone is excluded on purpose: a Google account is what an Android
# tester sends, so the bare word appears in replies about either platform.
_ANDROID = (
    "android", "google play", "play store", "playstore", "samsung", "galaxy",
    "pixel", "huawei", "xiaomi", "oneplus", "motorola",
)

# Agreement to the dated window, read as a hint and never as a verdict.
_WINDOW = (
    "d'accord", "daccord", "ok pour", "ca me convient", "cela me convient",
    "ca nous convient", "cela nous convient", "convient", "entendu",
    "c'est bon pour", "disponible",
    "einverstanden", "passt", "in ordnung", "geht klar", "verfugbar",
    "va bene", "d'accordo", "daccordo", "mi va bene", "disponibile",
)

# Google requires a Google account, which is most often one of these. An
# address at one of them is preferred over any other candidate in the body.
_GOOGLE = frozenset({"gmail.com", "googlemail.com"})

_ADDRESS = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")


def _folded(text: str) -> str:
    return mailcheck.deaccent(text or "").lower()


def _token_in(text: str, tokens: tuple[str, ...]) -> str:
    """The first token present as a whole word, or an empty string."""
    for token in tokens:
        if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", text):
            return token
    return ""


@dataclass(frozen=True)
class Reading:
    """What one message yields, with the reasons for each part of it."""

    verdict: str
    platform: str = ""
    account: str = ""
    window_said: str = ""
    refusal: str = ""
    why: tuple[str, ...] = field(default_factory=tuple)

    @property
    def enrollable(self) -> bool:
        """Whether this reading holds what enrolling the tester needs.

        iOS needs nothing from the tester: TestFlight is a public link and no
        address reaches us. Android cannot start without the account, because
        Google opens the closed track to named accounts and to nobody else.
        """
        if self.verdict != ANSWER:
            return False
        if self.platform == IOS:
            return True
        return bool(self.platform == ANDROID and self.account)


def platform_in(text: str) -> tuple[str, str]:
    """The platform this text names, and why that answer was reached."""
    folded = _folded(text)
    apple = _token_in(folded, _IOS)
    google = _token_in(folded, _ANDROID)
    if apple and google:
        return "", f"names both platforms ({apple!r} and {google!r})"
    if apple:
        return IOS, f"{apple!r} in the message"
    if google:
        return ANDROID, f"{google!r} in the message"
    return "", "names no platform"


def account_in(
    text: str, *, ours: frozenset[str] = frozenset(),
    theirs: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    """The tester's own account address in this text, and why.

    Preference goes to a Google domain, since that is the account Google's
    closed track accepts. Where none appears, any address that is neither the
    operator's nor one the survey already holds for the firm is offered, and
    the reason says which rule produced it.
    """
    seen: list[str] = []
    for raw in _ADDRESS.findall(text or ""):
        at = raw.strip().lower().strip(".")
        if at in seen:
            continue
        domain = at.rpartition("@")[2]
        if domain in ours or at in theirs:
            continue
        seen.append(at)
    for at in seen:
        if at.rpartition("@")[2] in _GOOGLE:
            return at, "a Google account in the message"
    if seen:
        return seen[0], "the only address in the message that is not the firm's"
    return "", "carries no address of its own"


def window_in(text: str) -> str:
    """A token suggesting agreement to the dated window, or an empty string."""
    return _token_in(_folded(text), _WINDOW)


def read(
    subject: str,
    body: str,
    *,
    sender: str = "",
    ours: frozenset[str] = frozenset(),
    theirs: frozenset[str] = frozenset(),
) -> Reading:
    """Classify one inbound message and read what it carries.

    A refusal is settled before anything else is read. Somebody who asks to be
    left alone has not enrolled, whatever else the message mentions, and
    reading a platform out of that reply would put them in a cohort.

    The sender's own address joins the text before any address is looked for.
    A tester who writes from their Google account and ends with "voici mon
    compte" has given the address in the envelope rather than in the body.
    """
    refusal = bounce.refusal_token(subject) or bounce.refusal_token(body[:400])
    if refusal:
        return Reading(REFUSAL, refusal=refusal,
                       why=(f"{refusal!r} asks to be left alone",))

    whole = f"{subject}\n{body}"
    platform, platform_why = platform_in(whole)
    at = (sender or "").strip().lower()
    hunted = f"{whole}\n{at}" if at and at.rpartition("@")[2] not in ours else whole
    account, account_why = account_in(hunted, ours=ours, theirs=theirs)
    window = window_in(whole)

    why = [platform_why, account_why]
    if window:
        why.append(f"{window!r} may accept the dates")
    if not platform and not account:
        return Reading(OTHER, window_said=window, why=tuple(why))
    return Reading(
        ANSWER, platform=platform, account=account, window_said=window,
        why=tuple(why),
    )


def free_domains() -> frozenset[str]:
    """The free-mail domains, as the mail source already lists them."""
    return mail.FREE_MAIL
