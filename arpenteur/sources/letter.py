"""Turning a written message into the two parts an email carries.

A message is composed as text, in `templates/limen/*.md`, because that is what
a person can read and correct. A recipient opening it in Outlook should see a
letter rather than a terminal, so this renders that text to HTML and keeps the
text itself as the alternative part.

**Both parts, always.** `multipart/alternative` with text first and HTML
second is what a mail client expects, and it is also the shape that survives a
reader whose client refuses HTML. Sending HTML alone is one of the few
structural things a spam filter still counts against a cold message.

**Inline styles only.** Gmail strips a `<style>` block, Outlook ignores much of
what survives, and no email client guarantees a web font. So every rule is an
attribute on the element that needs it, and the family is a stack ending in a
generic that exists everywhere.

**Helvetica first, which is not an accident.** The reader is a Swiss property
manager and the letter is Swiss correspondence. The stack falls back through
Arial to the system sans, so a Windows reader sees Arial and nobody sees
Times New Roman by surprise.

**The converter handles what the templates contain and nothing else.**
Paragraphs, ordered lists, bare URLs. A general Markdown implementation would
accept constructs no template uses and render them in ways nobody checked,
which is a larger surface for no gain.
"""

from __future__ import annotations

import html as _html
import re

# The one place the look is decided. A reader sees a letter, so the measure is
# narrow, the leading is generous and the colour is off-black rather than pure
# black, which reads as softer on a white background.
FAMILY = (
    "'Helvetica Neue', Helvetica, Arial, 'Segoe UI', Roboto, sans-serif"
)
INK = "#1f2328"
MUTED = "#57606a"
LINK = "#0b5ea8"
RULE = "#d8dee4"
PAGE = "#ffffff"
SIZE = "15px"
LEADING = "1.65"
MEASURE = "620px"

_URL = re.compile(r"(https?://[^\s<>\"]+)")
_ORDERED = re.compile(r"^(\d+)[.)]\s+(.*)$")
_BULLET = re.compile(r"^[-*]\s+(.*)$")


def _link(text: str) -> str:
    """Escape, then make a bare URL clickable.

    The escape runs first so the pattern sees escaped text and cannot reopen a
    tag, which is the same ordering `draft_page` uses for its markers.
    """
    safe = _html.escape(text)
    return _URL.sub(
        f'<a href="\\1" style="color:{LINK};text-decoration:underline">\\1</a>',
        safe,
    )


def to_html(body: str) -> str:
    """The message's own body, as HTML fragments.

    Blank lines separate blocks. A block whose every line is numbered becomes
    an ordered list, one dashed becomes an unordered list, anything else is a
    paragraph with its internal line breaks folded away so the client reflows
    the text at the reader's width.
    """
    blocks: list[str] = []
    for raw in re.split(r"\n\s*\n", (body or "").strip()):
        lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
        if not lines:
            continue
        ordered = [_ORDERED.match(line) for line in lines]
        bullets = [_BULLET.match(line) for line in lines]
        if all(ordered):
            items = "".join(
                f'<li style="margin:0 0 .45rem">{_link(m.group(2))}</li>'
                for m in ordered if m
            )
            blocks.append(
                f'<ol style="margin:0 0 1.1rem;padding-left:1.4rem;'
                f'color:{INK}">{items}</ol>'
            )
            continue
        if all(bullets):
            items = "".join(
                f'<li style="margin:0 0 .45rem">{_link(m.group(1))}</li>'
                for m in bullets if m
            )
            blocks.append(
                f'<ul style="margin:0 0 1.1rem;padding-left:1.4rem;'
                f'color:{INK}">{items}</ul>'
            )
            continue
        # A block of short lines is an address or a bare URL, where the line
        # breaks carry meaning. Prose is folded so the client reflows it.
        if len(lines) > 1 and all(len(line) < 46 for line in lines):
            joined = "<br>".join(_link(line) for line in lines)
        else:
            joined = _link(" ".join(lines))
        blocks.append(
            f'<p style="margin:0 0 1.1rem;color:{INK}">{joined}</p>'
        )
    return "".join(blocks)


def wrap(body_html: str, *, signature_html: str = "") -> str:
    """One complete document, styled, with the signature below a thin rule."""
    signature = ""
    if signature_html.strip():
        signature = (
            f'<div style="margin-top:1.6rem;padding-top:1.1rem;'
            f'border-top:1px solid {RULE};font-size:13.5px;color:{MUTED};'
            f'line-height:1.55">{signature_html.strip()}</div>'
        )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "</head>"
        f'<body style="margin:0;padding:0;background:{PAGE}">'
        f'<div style="margin:0 auto;padding:24px 16px;max-width:{MEASURE};'
        f'font-family:{FAMILY};font-size:{SIZE};line-height:{LEADING};'
        f'color:{INK}">'
        f"{body_html}{signature}"
        "</div></body></html>"
    )


def render(body: str, *, signature_html: str = "") -> str:
    """A written message, ready to send as the HTML part."""
    return wrap(to_html(body), signature_html=signature_html)


_TAG = re.compile(r"<[^>]+>")
_BREAK = re.compile(r"(?i)<\s*(br|/p|/div|/li|/h[1-6])\s*/?\s*>")


def to_text(fragment: str) -> str:
    """An HTML fragment as plain text, for the alternative part.

    Used on the signature, which a person may have pasted from a rich-text
    editor. The text part has to carry the same words, since a reader whose
    client refuses HTML would otherwise receive an unsigned letter.
    """
    if not (fragment or "").strip():
        return ""
    text = _BREAK.sub("\n", fragment)
    text = _TAG.sub("", text)
    text = _html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


# --- the signature ------------------------------------------------------------

# What a signature may contain. Narrow because the fragment is pasted from a
# rich-text editor, which emits whatever the source document carried: a copied
# web page brings scripts, tracking pixels and absolute positioning with it.
ALLOWED_TAGS = frozenset({
    "a", "b", "strong", "i", "em", "u", "s", "br", "p", "div", "span",
    "ul", "ol", "li", "small", "img", "table", "tr", "td", "tbody",
})
# Attributes kept per tag. Everything else goes, including every `on*` handler
# and every `style` that could reposition the fragment over the message.
ALLOWED_ATTRS = {
    "a": ("href", "title"),
    "img": ("src", "alt", "width", "height"),
    "td": ("align", "valign", "width"),
    "table": ("cellpadding", "cellspacing", "border", "width"),
}

_TAG_OPEN = re.compile(r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)([^>]*?)(/?)\s*>")
_ATTR = re.compile(r'([a-zA-Z-]+)\s*=\s*("[^"]*"|\'[^\']*\'|[^\s">]+)')
_DANGEROUS_BLOCK = re.compile(
    r"(?is)<\s*(script|style|iframe|object|embed|noscript)\b.*?<\s*/\s*\1\s*>"
)
_URL_SAFE = re.compile(r"(?i)^\s*(https?:|mailto:|tel:|/|#|data:image/)")


def clean_signature(fragment: str) -> str:
    """One pasted fragment, reduced to what a signature needs.

    Written because the editor hands over whatever was copied. A signature
    lifted from a web page arrives with scripts, a tracking pixel and absolute
    positioning, and a cold message carrying any of those is a cold message a
    filter has a reason to drop.
    """
    text = _DANGEROUS_BLOCK.sub("", fragment or "")

    def keep(found: re.Match) -> str:
        closing, name, attrs, selfclose = found.groups()
        tag = name.lower()
        if tag not in ALLOWED_TAGS:
            return ""
        if closing:
            return f"</{tag}>"
        kept = []
        for attr, value in _ATTR.findall(attrs or ""):
            key = attr.lower()
            if key not in ALLOWED_ATTRS.get(tag, ()):
                continue
            raw = value.strip("\"'")
            if key in ("href", "src") and not _URL_SAFE.match(raw):
                continue
            kept.append(f'{key}="{_html.escape(raw, quote=True)}"')
        body = (" " + " ".join(kept)) if kept else ""
        shut = "/" if (selfclose or tag in ("br", "img")) else ""
        return f"<{tag}{body}{shut}>"

    return _TAG_OPEN.sub(keep, text).strip()


def load_signature(path) -> str:
    """The signature fragment, or an empty string when none is written."""
    try:
        return clean_signature(path.read_text(encoding="utf-8"))
    except OSError:
        return ""


def store_signature(path, fragment: str) -> str:
    """Write a signature, cleaned. Returns what was written."""
    cleaned = clean_signature(fragment)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cleaned + "\n", encoding="utf-8")
    return cleaned
