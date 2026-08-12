"""Reading a company's own website: links out, addresses out. No I/O.

Everything here is a pure function over a page of HTML, for the same reason
`shab.py` is: the interesting failures are in the parsing, and a parser with
no network in it can be tested exhaustively on fixtures.

The one judgement worth stating is why this is regex and not a model. The
`enrich` stage already asks a model to read these sites, at about forty
centimes a company, and across the three companies it has run on it returned
no email at all. An address is not a thing that needs interpreting: it is a
`mailto:` href, or it is a string with an `@` in it. A regex finds it for
nothing, always gives the same answer twice, and can point at the exact page
it came from, which is what `source_url` is supposed to mean.

The cost of that choice is the obfuscated ones. A site that writes its
address as an image, or assembles it in JavaScript, is invisible here and a
model reading the rendered page would have found it. That is the trade, and
`pattern` is the tier that exists to cover the remainder.
"""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import unquote, urljoin, urlsplit

# A deliberately ordinary address pattern. Being stricter than this mostly
# rejects real addresses; being looser mostly collects filenames.
EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,}")

HREF = re.compile(r"""<a\b[^>]*\bhref\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
TAG = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
ANY_TAG = re.compile(r"<[^>]+>")

# `logo@2x.png` and `sprite@3x.svg` match any sane address pattern, and a page
# full of them yields a contact list of image files.
ASSET_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js",
    ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".pdf", ".avif",
)

# Addresses that belong to the site's tooling rather than to the company, and
# placeholders left in a template. Matched on the domain.
NOT_A_COMPANY = frozenset({
    "example.com", "example.org", "domain.com", "yourdomain.com", "email.com",
    "sentry.io", "wixpress.com", "wix.com", "squarespace.com", "godaddy.com",
    "schema.org", "w3.org", "sentry-next.wixpress.com",
})

# Where a Swiss company puts its address, in the four languages one is likely
# to be written in. Matched against the href after `fold`, so the accented
# and percent-encoded spellings of the same path match the same hint.
#
# The order is the ranking: the budget is a handful of fetches, so a page
# actually named "contact" is read before a legal notice, and a legal notice
# before an "about" page. That ordering is not decoration. The first live run
# found nothing on a site whose address sat on `mentions-légales`.
CONTACT_HINTS = (
    "contact", "kontakt", "contatti", "contatto", "contatta", "nous-joindre",
    "impressum", "imprint", "mentions", "legales", "legal",
    "assistance", "support", "aide",
    "about", "a-propos", "apropos", "propos", "ueber-uns", "uber-uns",
    "chi-siamo", "equipe", "team",
)

LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)

# Obfuscations common enough to be worth undoing, and narrow enough not to
# corrupt ordinary prose. Bare " at " is deliberately absent: it turns
# "consulting at its best" into an address.
DEOBFUSCATE = (
    (re.compile(r"\s*[\(\[\{]\s*(?:at|@|arobase)\s*[\)\]\}]\s*", re.IGNORECASE), "@"),
    (re.compile(r"\s*[\(\[\{]\s*(?:dot|point|punkt)\s*[\)\]\}]\s*", re.IGNORECASE), "."),
)


def visible_text(page: str) -> str:
    """The page with its scripts, styles and tags removed, entities resolved.

    Entity decoding matters more than it looks: an address written
    `bureau&#64;etude.ch` is invisible to any pattern applied to raw HTML,
    and it is a standard way of writing one.
    """
    without_code = TAG.sub(" ", page)
    return html.unescape(ANY_TAG.sub(" ", without_code))


def _clean(address: str) -> str | None:
    address = address.strip().strip(".,;:!?)(<>\"'").lower()
    if address.count("@") != 1:
        return None
    local, _, domain = address.partition("@")
    if not local or not domain or ".." in address:
        return None
    if domain.endswith(ASSET_SUFFIXES) or domain in NOT_A_COMPANY:
        return None
    # A bare TLD-looking domain with no dot cannot be reached, and a trailing
    # hyphen is always the tail of a wrapped line rather than a real host.
    if "." not in domain or domain.endswith("-"):
        return None
    return address


def emails_in(page: str, *, deobfuscate: bool = True) -> list[str]:
    """Every address on the page, `mailto:` links first, in order and unique.

    Order is the whole point of returning a list rather than a set. A
    `mailto:` is an address the company published *as a way to reach it*,
    which is a stronger claim than an address that merely appears somewhere
    in the text, and the caller records the first as the primary one.
    """
    found: list[str] = []

    def add(candidate: str) -> None:
        cleaned = _clean(candidate)
        if cleaned and cleaned not in found:
            found.append(cleaned)

    for href in HREF.findall(page):
        target = html.unescape(href).strip()
        if target.lower().startswith("mailto:"):
            # mailto:a@b.ch?subject=... , and some sites list several.
            for part in target[7:].split("?", 1)[0].split(","):
                add(part.strip())

    text = visible_text(page)
    if deobfuscate:
        for pattern, replacement in DEOBFUSCATE:
            text = pattern.sub(replacement, text)
    for match in EMAIL.findall(text):
        add(match)
    return found


def same_site(one: str, other: str) -> bool:
    """Same registrable-ish host, ignoring a leading www.

    Not a public-suffix implementation, and it does not need to be: it decides
    whether to follow a link on the page we are already reading, so the cost
    of being slightly conservative is a page not read.
    """
    def host(url: str) -> str:
        name = (urlsplit(url).hostname or "").lower()
        return name[4:] if name.startswith("www.") else name

    return host(one) == host(other) and host(one) != ""


def fold(url: str) -> str:
    """A URL flattened to something the hints can be matched against.

    Percent-decoded and stripped of accents, because `/mentions-légales` is
    served as `/mentions-l%C3%A9gales` and neither spelling contains the
    ASCII "legales" a hint list is naturally written in. The first live run
    of this stage found no address on a site that publishes one, for exactly
    that reason.
    """
    decoded = unquote(url).lower()
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", decoded)
        if not unicodedata.combining(character)
    )


def _rank(url: str) -> int | None:
    """Position in CONTACT_HINTS, or None when the URL matches no hint."""
    folded = fold(url)
    for rank, hint in enumerate(CONTACT_HINTS):
        if hint in folded:
            return rank
    return None


def contact_links(page: str, base_url: str, *, limit: int = 4) -> list[str]:
    """Links from this page that look like they carry the contact details.

    Same site only, and ranked by CONTACT_HINTS.
    """
    return _pick(
        (urljoin(base_url, html.unescape(href).strip()) for href in HREF.findall(page)
         if html.unescape(href).strip()
         and not html.unescape(href).strip().startswith(
             ("mailto:", "tel:", "javascript:", "#"))),
        base_url,
        limit=limit,
    )


def locations(xml: str) -> list[str]:
    """Every <loc> in a sitemap or sitemap index."""
    return [html.unescape(loc) for loc in LOC.findall(xml)]


def is_sitemap_index(xml: str) -> bool:
    """Whether a sitemap lists other sitemaps rather than pages."""
    return "<sitemapindex" in xml.lower()


def sitemap_links(xml: str, base_url: str, *, limit: int = 4) -> list[str]:
    """Pages in a sitemap that look like they carry the contact details.

    The way in when the home page has no links to read. A site built by a
    page builder serves its navigation as JavaScript, so the markup that
    arrives contains no `<a href>` at all, while the same site publishes a
    complete list of its pages at a well known address for exactly this kind
    of reader. Cheaper and politer than crawling, and it is what the sitemap
    is for.
    """
    return _pick(locations(xml), base_url, limit=limit)


def _pick(urls: object, base_url: str, *, limit: int) -> list[str]:
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for candidate in urls:  # type: ignore[union-attr]
        absolute = str(candidate).split("#", 1)[0].rstrip("/")
        if not absolute.startswith(("http://", "https://")):
            continue
        if not same_site(absolute, base_url) or absolute in seen:
            continue
        rank = _rank(absolute)
        if rank is None:
            continue
        seen.add(absolute)
        scored.append((rank, absolute))
    scored.sort(key=lambda pair: pair[0])
    return [url for _rank, url in scored[:limit]]
