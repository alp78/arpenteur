"""Markdown in, safe HTML out, and the small readings a note needs.

Notes are written in whatever editor the operator already uses and stored
exactly as typed. This module is the one place that turns them into a page, so
the safety decision is made once: raw HTML is escaped rather than passed
through.

That is a deliberate departure from CommonMark, which specifies the opposite.
The reasoning is that the console renders these into its own pages, a note may
be pasted from anywhere, and no note has ever needed a `<div>`. Losing raw HTML
costs nothing anyone will notice; passing it through would put script injection
one paste away.
"""

from __future__ import annotations

import hashlib
import re

from markdown_it import MarkdownIt

# `html=False` escapes raw HTML. Tables are enabled because they are the one
# construct people reach for that CommonMark leaves out; linkify is not,
# because it needs another dependency to turn a bare URL into a link and the
# link syntax already works.
_RENDERER = MarkdownIt("commonmark", {"html": False}).enable("table")

_HEADING = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$")
_MARKUP = re.compile(r"[*_`>#\[\]]|\!\[|\]\(")


def render(text: str) -> str:
    """One note as HTML. Never stored: a stored rendering drifts from its
    source the first time the source is edited."""
    return _RENDERER.render(text or "")


def digest(text: str) -> str:
    """The body's fingerprint, so a re-import of an unchanged file can say so
    rather than bumping a timestamp and claiming work that did not happen."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def title_of(text: str, fallback: str = "Note") -> str:
    """The note's own first heading, or its first line of prose.

    Taken from the document rather than asked for in a field. A note written
    in an editor already begins with its title, and making somebody type it
    twice is how the two come to disagree.
    """
    heading = _HEADING.search(text or "")
    if heading:
        return _strip(heading.group(1))[:200]
    for line in (text or "").splitlines():
        if line.strip():
            return _strip(line)[:200]
    return fallback


def summary(text: str, *, chars: int = 220) -> str:
    """The first paragraph that is prose, for a list of notes.

    Headings, bullets and rules are skipped: a listing whose every line reads
    "1. Séquence : le périmètre d'abord" tells you the shape of the document
    and nothing about it.
    """
    for block in re.split(r"\n\s*\n", text or ""):
        line = block.strip()
        if not line or line.startswith(("#", "-", "*", ">", "|", "```")):
            continue
        flat = _strip(" ".join(line.split()))
        if flat:
            return flat[:chars] + ("…" if len(flat) > chars else "")
    return ""


def _strip(text: str) -> str:
    """Markup characters removed for a plain-text context.

    Not a parse. This is for a title attribute and a listing line, where
    `**Rôles à demander**` should read as words; anywhere the result is shown
    as a document it goes through `render` instead.
    """
    without_links = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text or "")
    return _MARKUP.sub("", without_links).strip()
