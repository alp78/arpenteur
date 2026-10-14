"""Whether an address could receive mail, and what "verified" honestly means.

Three checks, and the gap between them is the whole point of this module.

**Shape.** A string that is not an address cannot be one. Free, certain, and
it rejects the filenames and template placeholders that a page scrape drags in.

**Deliverable domain.** A domain with no MX record and no A record accepts no
mail from anyone, so every address at it is dead. This is a DNS lookup: free,
polite, cached by the resolver, and it settles a whole domain in one query
rather than one per candidate.

**The mailbox itself, which is not checked here and cannot be.** MX answers
for the domain, never for the local part: `info@nicod.ch` and `zzz@nicod.ch`
give the same answer. The only protocol-level way to ask about one mailbox is
an SMTP `RCPT TO` probe, and this module does not make one. Large providers
answer every recipient with a 250 to defeat exactly that probe, greylisting
makes a single answer unreliable, and the practice gets a sending address onto
blocklists. A paid verifier is the alternative and it is a purchase, not a
function.

So an inferred address that passes everything here is a **candidate**, not a
fact. `verify` says so in its own return value rather than leaving the caller
to remember, because a dataset that cannot distinguish a published address
from a plausible one has stopped being evidence.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

import dns.exception
import dns.resolver

from . import web

# Local parts a company publishes for the company rather than for a person.
# These are what an inference should try, in this order, and the ordering is
# the point: `info` is the Swiss default across all four languages, and a
# German-speaking firm answers `kontakt` where a French one answers `contact`.
ROLE_PARTS = (
    "info", "contact", "kontakt", "contatto", "office", "mail", "admin",
    "welcome", "hello", "sekretariat", "secretariat", "reception",
)

# Role parts by the language of the region, so an inference tries the ones a
# firm in that language actually uses before the ones it does not. The generic
# heads stay first everywhere because `info@` outnumbers everything else.
ROLE_PARTS_BY_LANGUAGE = {
    "fr": ("info", "contact", "secretariat", "reception", "administration"),
    "de": ("info", "kontakt", "office", "sekretariat", "verwaltung"),
    "it": ("info", "contatto", "contatti", "amministrazione", "segreteria"),
    "rm": ("info", "contact", "kontakt"),
}

# The desks a firm runs that are not the switchboard. A régie prints a mailbox
# per function, and `locations@`, `courtage@` and `bewirtschaftung@` reach a
# department exactly as `info@` does.
#
# WHY THIS IS SEPARATE FROM ROLE_PARTS. Those are the heads an inference tries
# when it has to guess a mailbox, and guessing `courtage@` for a firm that has
# no brokerage desk wastes a probe. These are only ever recognised, never
# guessed, so the list can be as wide as the language allows.
#
# Kept because the alternative was measured: with only ROLE_PARTS, the send
# list filed 590 addresses as reaching a named person, and `locations@
# agenceacc.ch` and `courtage@imogroup-geneve.ch` were among them. A send that
# skips those loses real desks; a send that treats them as people misreads what
# the nLPD asks about.
DESK_FUNCTIONS = frozenset({
    # French
    "location", "locations", "gerance", "gerances", "courtage", "vente",
    "ventes", "administration", "direction", "comptabilite", "facturation",
    "technique", "accueil", "agence", "immobilier", "regie",
    # German
    "vermietung", "bewirtschaftung", "verwaltung", "verkauf", "buchhaltung",
    "empfang", "liegenschaften", "immobilien", "mieten", "service",
    # Italian
    "locazione", "locazioni", "amministrazione", "segreteria", "vendite",
    "immobili",
    # Shared, and the abbreviations a domain keeps
    "immo", "office", "team", "support", "rent", "sales",
    # Tourism and mountain transport. Measured on the tourism cohort, where the
    # list above read `bahnen@belalp.ch` and `event@crans-montana.ch` as
    # unclear: a lift company names its mailbox after the trade, a destination
    # after the function its visitors write to.
    "tourismus", "tourisme", "turismo", "bergbahnen", "bahnen", "seilbahn",
    "sportbahnen", "skilift", "betrieb", "kasse", "ticket", "tickets",
    "billetterie", "biglietteria", "reservation", "reservations",
    "reservationen", "buchung", "booking", "prenotazioni", "marketing",
    "event", "events", "erlebnis", "willkommen", "email", "gaesteinfo",
    "ferien",
})

# Consumer mail providers. An address here belongs to a person, not to a
# company, whatever the company publishes it as.
#
# WHY THIS IS A CATEGORY AND NOT A QUALITY JUDGEMENT. A one-person régie that
# prints `arben72@msn.com` on its own website really has published it as its
# business contact, so `published` is the honest provenance and the address is
# not a mistake. What it is not is a role address: `info@naef.ch` reaches a
# desk and survives the person leaving, while this reaches a named individual's
# private mailbox. Under the nLPD those are different kinds of data, and a cold
# approach to one is not the same act as a cold approach to the other.
#
# Swiss-specific entries matter as much as the global ones: `bluewin.ch` is the
# Swisscom consumer mailbox and is the second most common of these in the
# survey, ahead of every provider except Gmail.
FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.ch", "hotmail.fr",
    "hotmail.it", "hotmail.de", "outlook.com", "outlook.fr", "outlook.de",
    "live.com", "live.fr", "msn.com", "yahoo.com", "yahoo.fr", "yahoo.de",
    "yahoo.it", "icloud.com", "me.com", "mac.com", "aol.com", "protonmail.com",
    "proton.me", "gmx.ch", "gmx.net", "gmx.de", "gmx.at", "web.de",
    "bluewin.ch", "bluemail.ch", "sunrise.ch", "hispeed.ch", "vtxnet.ch",
    "netplus.ch", "romandie.com", "worldcom.ch", "freesurf.ch",
})


def is_personal(address: str) -> bool:
    """True when the address is at a consumer mail provider.

    Used to keep a private mailbox out of an outreach export without throwing
    the fact away: the row is suppressed, not deleted, so the survey still
    records that this is the only address the firm publishes.
    """
    return domain_of(address) in FREE_MAIL


# An address that reaches a mailbox nobody reads for this purpose, or that
# belongs to the site's tooling. Rejected before anything is stored.
NOT_A_CONTACT = frozenset({
    "postmaster", "abuse", "hostmaster", "webmaster", "noreply", "no-reply",
    "donotreply", "do-not-reply", "mailer-daemon", "bounce", "bounces",
    "root", "www-data", "nobody",
})


@dataclass(frozen=True)
class Verdict:
    """What could be established about one address, and what could not.

    `deliverable` means the domain accepts mail. `certain` means the address
    was observed rather than constructed. Nothing here ever sets `certain` on
    an inference: only the caller that read it off a page can say that.
    """

    address: str
    ok: bool
    deliverable: bool
    certain: bool
    reason: str

    @property
    def method(self) -> str:
        return "published" if self.certain else "inferred"


def shape_ok(address: str) -> bool:
    """A syntactically usable address that is not a filename or a placeholder."""
    address = (address or "").strip().lower()
    if not address or address.count("@") != 1:
        return False
    if not re.fullmatch(web.EMAIL, address):
        return False
    local, _, domain = address.partition("@")
    if local in NOT_A_CONTACT:
        return False
    if domain in web.NOT_A_COMPANY:
        return False
    return not domain.endswith(web.ASSET_SUFFIXES)


def domain_of(address: str) -> str:
    return (address or "").strip().lower().rpartition("@")[2]


def accepts_mail(
    domain: str, *, timeout: float = 5.0, cache: dict[str, bool] | None = None
) -> bool:
    """Whether this domain accepts mail at all.

    MX first, then A: a domain with no MX but an address record still takes
    mail there, which RFC 5321 §5.1 requires a sender to try and which small
    Swiss hosts still rely on. One query settles every candidate at that
    domain, so the caller passes a cache and a hundred guesses cost one lookup.
    """
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain:
        return False
    if cache is not None and domain in cache:
        return cache[domain]

    answer = False
    resolver = dns.resolver.Resolver()
    resolver.timeout = timeout
    resolver.lifetime = timeout
    for record in ("MX", "A"):
        try:
            if resolver.resolve(domain, record):
                answer = True
                break
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
            continue
        except (dns.exception.Timeout, dns.resolver.NoNameservers):
            # A resolver that did not answer is not a domain that refused.
            # Recorded as undeliverable for this run rather than cached, so a
            # transient failure does not become a permanent verdict.
            return False
        except dns.exception.DNSException:
            continue
    if cache is not None:
        cache[domain] = answer
    return answer


def verify(
    address: str, *, certain: bool, cache: dict[str, bool] | None = None,
    resolve: Callable[..., bool] = accepts_mail,
) -> Verdict:
    """Check one address as far as it can honestly be checked.

    `certain` is the caller's assertion that this address was read off a page
    rather than constructed. It is passed in rather than inferred because only
    the caller knows, and it is the difference between a fact and a guess.
    """
    address = (address or "").strip().lower()
    if not shape_ok(address):
        return Verdict(address, False, False, False, "not an address")
    if not resolve(domain_of(address), cache=cache):
        return Verdict(address, False, False, certain, "domain takes no mail")
    return Verdict(
        address, True, True, certain,
        "published on the site" if certain else "constructed, domain takes mail",
    )


# Words that are in a registered name and never in the domain somebody chose
# for it. Legal forms first, then the filler a register keeps and a domain
# drops.
# Spelt without punctuation because `WORD` is `[a-z0-9]+`: "S.A.R.L." arrives
# here as four separate tokens, so a dotted entry could never match one. The
# single letters it does produce are dropped by the length filter at each call
# site instead.
NAME_NOISE = frozenset({
    "sa", "ag", "sarl", "sagl", "gmbh", "llc", "ltd",
    "societe", "société", "society", "gesellschaft", "societa", "società",
    "cooperative", "coopérative", "genossenschaft", "cooperativa",
    "holding", "group", "groupe", "gruppe", "gruppo",
    "en", "et", "and", "und", "e", "de", "du", "des", "la", "le", "les",
    "der", "die", "das", "il", "lo", "di",
})

WORD = re.compile(r"[a-z0-9]+")


def deaccent(text: str) -> str:
    """Fold accents away before a name is cut into words.

    Not cosmetic. `[a-z0-9]+` over "Neuchâtel" returns "neuch" and "tel",
    and the second of those became `info@tel.com`, an address at somebody
    else's domain, on the first live run. A Swiss corpus is full of accented
    place and family names, so the folding happens before any splitting.
    """
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(ch)
    )

# Words that describe the trade rather than name the firm. They may sit inside
# a compound stem, never stand alone as one.
#
# The rule earned itself immediately: "TM Immobilien AG" reduced to its last
# distinctive word gives `immobilien.ch`, which resolves, accepts mail, and
# belongs to somebody else entirely. Guessing a domain is only safe while the
# guess stays specific to the firm, and an industry noun is the opposite of
# specific. This is the same failure `enrich` guards against when it refuses a
# namesake's website.
TRADE_WORDS = frozenset({
    "immobilien", "immobilier", "immobiliere", "immobiliare", "immobili",
    "immo", "regie", "gerance", "verwaltung", "verwaltungs", "liegenschaft",
    "liegenschaften", "bewirtschaftung", "hausverwaltung", "gestion",
    "gestioni", "gestione", "stabili", "agence", "agentur", "agenzia",
    "treuhand", "fiduciaire", "fiduciaria", "consulting", "conseil",
    "services", "service", "partners", "partner", "invest", "investment",
    "finance", "capital", "management", "bau", "construction",
    # Generic business nouns. The danger is not that they appear in a name but
    # that they are the only word left in one: "A.M.Y Office Sàrl" reduces to
    # `office`, and a page at office.ch contains the word "office", so the
    # confirmation passes on a tautology. Live run produced `info@office.ch`,
    # `info@commerces.ch` and `lotten@bergman.com`, none of them the firm's.
    "office", "commerce", "commerces", "bureau", "buro", "centre", "center",
    "atelier", "boutique", "maison", "haus", "casa", "home", "solutions",
    "solution", "concept", "concepts", "project", "projects", "projekt",
    "swiss", "suisse", "schweiz", "svizzera", "international", "global",
    "expert", "experts", "expertise", "team", "trading", "commercial",
    "developpement", "development", "entwicklung", "sviluppo", "holdings",
})


def _drop_place(
    words: list[str], seat: str, places: frozenset[str] = frozenset()
) -> list[str]:
    """Take the company's own town out of its name.

    A branch office is named for where it is, and the town's own domain is
    real, resolves and accepts mail: guessing from "Naef Immobilier Nyon SA"
    produced `info@nyon.ch`, the commune. No general word list catches that,
    but the register already knows the answer, because every company row
    carries its `legal_seat`. Using it costs nothing and is exact.

    The town is only dropped while something else survives. A firm actually
    called after its town keeps the word rather than losing its whole name.
    """
    place = {
        w for w in WORD.findall(deaccent(seat or "").lower()) if len(w) > 2
    }
    kept = [w for w in words if w not in place and w not in places]
    return kept or words


def domain_candidates(
    name: str, *, seat: str = "", places: frozenset[str] = frozenset(),
    tlds: tuple[str, ...] = ("ch", "com"),
) -> list[str]:
    """Domains a company of this name plausibly registered, best first.

    Ordered so the likeliest is tried first, because each one costs a DNS
    lookup and the point of the ordering is to stop early. A firm called
    "Bernard Nicod SA" is `nicod.ch` before `bernardnicod.ch` before
    `bernard-nicod.ch`, because Swiss firms register the distinctive word.

    This produces a guess about which domain belongs to whom, and a domain that
    resolves is not a domain that belongs to this company. The caller has to
    confirm that separately; `accepts_mail` says only that somebody's mail
    server answers.
    """
    words = [
        w for w in WORD.findall(deaccent(name or "").lower())
        if w not in NAME_NOISE
    ]
    words = _drop_place([w for w in words if len(w) > 1], seat, places)
    if not words:
        return []
    stems: list[str] = []

    def offer(stem: str, *, alone: bool = False) -> None:
        if not stem or stem in stems or not 2 < len(stem) < 40:
            return
        # A single word that names the trade is not this firm's domain.
        if alone and stem in TRADE_WORDS:
            return
        stems.append(stem)

    # The last distinctive word first: Swiss firms named after a person
    # register the surname, and "Bernard Nicod SA" is nicod.ch.
    if len(words) > 1:
        offer(words[-1], alone=True)
    # `alone` when there is only one word, because then the compound *is* that
    # word and would otherwise slip past the trade-word guard: "A.M.Y Office
    # Sàrl" reduces to ["office"] and offered `office.ch`.
    offer("".join(words), alone=len(words) == 1)
    offer("-".join(words), alone=len(words) == 1)
    if len(words) > 1:
        offer(words[0], alone=True)
        offer("".join(words[:2]))
        offer("-".join(words[:2]))
    return [f"{stem}.{tld}" for stem in stems for tld in tlds]


def candidates(domain: str, *, language: str = "") -> list[str]:
    """Role addresses to try at a known domain, best first.

    Only role addresses. A personal address inferred from an officer's name is
    a guess about a named individual, and writing to a guess about a person is
    the failure that no later correction reaches. `info@` is a company saying
    where to write to it.
    """
    domain = (domain or "").strip().lower().lstrip("@")
    if not domain:
        return []
    ordered = list(ROLE_PARTS_BY_LANGUAGE.get(language, ()))
    for part in ROLE_PARTS:
        if part not in ordered:
            ordered.append(part)
    return [f"{part}@{domain}" for part in ordered]


def distinctive_words(
    name: str, *, seat: str = "", places: frozenset[str] = frozenset()
) -> list[str]:
    """The words that identify this firm rather than its trade or its town.

    What is left of a registered name after the legal form, the filler and the
    trade nouns are taken out: "Naef Immobilier Fribourg SA" keeps `naef` and
    `fribourg`, and `naef` is the one that says which firm.

    Longest first, because the longest surviving word is the most specific and
    is what a confirmation should look for.
    """
    words = _drop_place([
        w for w in WORD.findall(deaccent(name or "").lower())
        if w not in NAME_NOISE and w not in TRADE_WORDS and len(w) > 2
    ], seat, places)
    return sorted(dict.fromkeys(words), key=lambda w: (-len(w), w))


def domain_echoes_name(domain: str, name: str) -> int:
    """How many of the firm's own name words the domain reproduces.

    Corroboration that costs nothing and is often stronger than the page.
    `celik-immobilier.ch` reproduces both words of "A.Celik immobilier Sàrl",
    which a different Celik would not have registered; `clerc.ch` reproduces
    one word of "A-Clerc Immobilier Sàrl" and could be any Clerc in
    Switzerland.
    """
    stem = deaccent(domain or "").lower().rpartition(".")[0] or domain or ""
    words = {
        w for w in WORD.findall(deaccent(name or "").lower())
        if w not in NAME_NOISE and len(w) > 2
    }
    return sum(1 for w in words if w in stem)


DESK = "desk"
PERSON = "person"
UNCLEAR = "unclear"


def desk_parts() -> tuple[str, ...]:
    """Every local part that reaches a desk rather than a named person.

    The union of the generic heads and all four language sets, so `verwaltung`,
    `segreteria` and `administration` count as desks and not as people. Built
    here rather than in the store, which may not import `sources`, and shared
    by the command line and the console's draft stage.
    """
    parts = set(ROLE_PARTS) | set(DESK_FUNCTIONS)
    for group in ROLE_PARTS_BY_LANGUAGE.values():
        parts.update(group)
    return tuple(sorted(parts))


def mailbox_kind(address: str, name: str = "") -> str:
    """Whether this address reaches a desk, a named person, or something unclear.

    The distinction the nLPD and UWG Art. 3(1)(o) turn on, and the reason it is
    three values and not two. `info@naef.ch` reaches a function and survives the
    person leaving. `silvia.vigo@regiegraber.ch` reaches an identified
    individual. `aa@afadesign.com` is neither, and calling it either would be
    the program asserting something it cannot see.

    A measured binary got both ends wrong. Reading only `ROLE_PARTS`, the send
    list called `locations@agenceacc.ch` a person, and it also called
    `actimmo@actimmo-ge.ch` one, which is the firm's own name as a mailbox.
    """
    local = (address or "").strip().lower().partition("@")[0]
    if not local:
        return UNCLEAR
    if local in ROLE_PARTS or local in DESK_FUNCTIONS:
        return DESK
    bare = deaccent(local)
    # A dotted or hyphenated local part made of two name-shaped halves is a
    # person: `prenom.nom`, and `l.moillen` with an initial. Checked before the
    # name echo, because a one-person firm's own name is also its owner's and
    # `andre.nogueira@andrehomes.ch` reaches André, not a desk.
    halves = [h for h in re.split(r"[._-]", bare) if h]
    if len(halves) >= 2 and all(h.isalpha() for h in halves):
        if any(len(h) == 1 for h in halves) or len(halves) == 2:
            if not any(h in ROLE_PARTS or h in DESK_FUNCTIONS for h in halves):
                return PERSON
    # One word that reproduces the firm's own name is the company mailbox:
    # `cabinet@somn-ambul.ch`, `actimmo@actimmo-ge.ch`.
    words = distinctive_words(name) if name else []
    if words and any(word in bare for word in words):
        return DESK
    return UNCLEAR


# Three kinds of mailbox a firm's own site publishes that are still the wrong
# recipient for a first approach, each measured on the tourism cohort and each
# checked before the desk vocabulary, because some of them would pass it.
#
# A commune's administration. A lift company run by its village prints the
# commune's address, and `gemeinde@buchholterberg.ch` arrived filed under
# Skilift A.G. Heimenschwand. Writing to a commune about a lift company's app
# is a letter to a public authority, which is a different act.
PUBLIC_ADMINISTRATION_PARTS = frozenset({
    "gemeinde", "gemeindeverwaltung", "gemeindekanzlei", "gemeindeschreiber",
    "gemeindeschreiberei", "einwohnergemeinde", "burgergemeinde",
    "stadtkanzlei", "stadtverwaltung", "commune", "administrationcommunale",
    "greffe", "greffecommunal", "secretariatcommunal", "municipalite",
    "comune", "municipio", "cancelleria", "cancelleriacomunale",
})
# A role one person holds. `praesident@` and `vr@`, the board, reach an
# identified individual as surely as a first name does, and the nLPD reads
# them that way.
ROLE_PERSON_PARTS = frozenset({
    "praesident", "praesidentin", "prasident", "prasidentin", "president",
    "presidente", "vr", "verwaltungsrat", "huettenwart", "huettenwartin",
    "huttenwart", "huttenwartin", "kassier", "kassierin", "aktuar",
    "aktuarin", "gardien", "gardienne", "caissier", "tresorier", "direktor",
    "directeur", "direttore",
})
# The firm's own restaurant or bar. A real mailbox of the firm, and the wrong
# desk: `beizli@skiliftheiden.ch` reaches whoever serves the soup.
VENUE_PARTS = frozenset({
    "beizli", "skibeizli", "beiz", "restaurant", "bergrestaurant", "buvette",
    "ristorante", "kiosk", "bar", "bistro", "cafe", "gastro", "hotel",
})

ADMINISTRATION = "administration"
ROLE = "role"
VENUE = "venue"


def mailbox_role(address: str, name: str = "") -> tuple[str, str]:
    """What this mailbox reaches, in six answers, and the reason in words.

    `mailbox_kind` keeps its three answers because the send list and its tests
    are built on them. A campaign needs three more, checked first: a commune,
    a role held by one person, the firm's restaurant.

    The local part is compared bare as well, so `administration.communale` and
    `hüttenwart` meet the same entry as `administrationcommunale` and
    `huettenwart`.
    """
    local = (address or "").strip().lower().partition("@")[0]
    bare = re.sub(r"[._-]", "", deaccent(local))
    if local in PUBLIC_ADMINISTRATION_PARTS or bare in PUBLIC_ADMINISTRATION_PARTS:
        return ADMINISTRATION, "a commune's own mailbox, not the firm's"
    if bare in ROLE_PERSON_PARTS:
        return ROLE, "a role one identified person holds"
    if bare in VENUE_PARTS:
        return VENUE, "the firm's restaurant or bar, not its office"
    kind = mailbox_kind(address, name)
    return kind, {
        DESK: "a desk the firm runs",
        PERSON: "a named person",
        UNCLEAR: "neither clearly a desk nor a person",
    }[kind]


def page_confirms(
    page_text: str, name: str, *, seat: str = "", domain: str = "",
    places: frozenset[str] = frozenset(),
) -> bool:
    """Whether this page belongs to the firm whose domain was guessed.

    The check that makes guessing a domain safe at all, and its absence was a
    real failure rather than a theoretical one: guessing from branch names
    produced `info@lancy.ch` and `info@nyon.ch`, which are Swiss communes, and
    `info@fribourg.ch`, which is a city. Every one of them resolved, accepted
    mail, and would have been written to.

    DNS cannot catch that, because those domains are perfectly real. Only the
    page can: a commune's site does not say "Naef". So a guessed domain is
    fetched once and has to name the firm before any address at it is kept.
    """
    if not page_text:
        return False
    haystack = deaccent(page_text).lower()
    wanted = distinctive_words(name, seat=seat, places=places)
    if not wanted:
        return False
    # Every distinctive word, not merely the longest one. "Bernard Nicod SA"
    # reduced to its longest word is "bernard", and `bernard.ch` is a real site
    # that contains the word "bernard" and belongs to somebody else: it passed
    # a first-word check on the first live run. Requiring "nicod" as well is
    # what separates the firm from the forename.
    #
    # Capped at three, because a long registered name is mostly description and
    # a site rarely prints all of it on one page.
    if not all(word in haystack for word in wanted[:3]):
        return False
    # And the town, when one name-word is all the evidence there is.
    #
    # A single distinctive word makes the check above a tautology: a site at
    # `clerc.ch` contains "clerc" whoever owns it. Requiring the registered
    # commune as well makes it two independent facts agreeing, and a Swiss firm
    # prints its address on its own site. The cost is a rejected site that does
    # not, which is the right way round: a missing address is a gap and a wrong
    # one gets written to.
    if len(wanted) == 1:
        # Unless the domain already reproduces two words of the name, in which
        # case the domain is the second fact and the town is not needed. That
        # matters for a real site with no content on it: `celik-immobilier.ch`
        # is a parked page naming neither a town nor an address, and it is
        # still plainly that firm's domain.
        if domain and domain_echoes_name(domain, name) >= 2:
            return True
        if not seat:
            return False
        town = [w for w in WORD.findall(deaccent(seat).lower()) if len(w) > 3]
        return any(w in haystack for w in town) if town else False
    return True
