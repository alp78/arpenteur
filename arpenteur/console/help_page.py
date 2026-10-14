"""Help: the documentation, read inside the application.

The pages are Markdown files under `docs/`, the same files the repository
ships, rendered through the note renderer so a page cannot carry a script.
A link written for the file tree (`guide/companies.md`) is rewritten to the
route that renders it, so the documentation reads the same in a browser as in
an editor and is written once.
"""

from __future__ import annotations

import re
from pathlib import Path
from posixpath import normpath
from typing import Any

from .. import manual, markdown
from . import ui
from .render import esc, page

STYLES = """
.doc { max-width:72ch; }
.doc h1 { margin-top:0; }
.doc h2 { margin-top:var(--s5); }
.doc pre { background:var(--paper); border:1px solid var(--line); padding:.7rem .9rem;
           overflow:auto; }
.doc code { font-size:.92em; }
.doc table { margin:var(--s3) 0; }
.doc img { max-width:100%; }
"""

_LINK = re.compile(r'href="([^"]+)"')


def shell(body: str, chrome: Any = None) -> str:
    return page("", body, chrome, css=STYLES)


clean = manual.clean


def _rewrite(href: str, here: str) -> str:
    """A link as written in the file, as the route that serves it."""
    if re.match(r"^[a-z][a-z0-9+.-]*:", href) or href.startswith(("/", "#")):
        return href
    target, _hash, fragment = href.partition("#")
    if target.endswith(".md"):
        target = target[:-3]
    base = here.rsplit("/", 1)[0] if "/" in here else ""
    joined = manual.clean(normpath(f"{base}/{target}" if base else target).lstrip("./"))
    if not joined:
        return href
    return f"/help/{joined}" + (f"#{fragment}" if fragment else "")


def render_doc(root: Path, path: str) -> str | None:
    """The page's HTML, or None when there is no such page."""
    file = manual.path_of(path, root)
    if file is None:
        return None
    name = manual.clean(path)
    html = markdown.render(file.read_text(encoding="utf-8"))
    html = _LINK.sub(lambda m: f'href="{_rewrite(m.group(1), name)}"', html)
    crumb = (
        ui.crumbs([("/help", "Help"), ("", name.rsplit("/", 1)[-1].replace("-", " "))])
        if name != "index" else ""
    )
    return f'{crumb}<article class="doc">{html}</article>'


def missing(path: str) -> str:
    return (
        "<h1>No help page here</h1>"
        f"<p>There is no page at <code>{esc(path)}</code>. "
        '<a href="/help">The index</a> lists what there is.</p>'
    )
