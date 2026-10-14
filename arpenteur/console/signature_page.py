"""The signature every message carries, edited as rich text.

A signature is written once and changed rarely, which makes a TOML string the
wrong home for it: the thing a person actually wants to do is paste what their
mail client already shows and see it come out the same. So the editor is
`contenteditable`, the result is HTML, and the file is the record.

**What is pasted is cleaned before it is stored.** A signature lifted from a
web page or an Outlook message arrives with scripts, a tracking pixel and
absolute positioning. `letter.clean_signature` reduces it to a short tag
allow-list, which is both a safety rule and a deliverability one: a cold
message carrying a tracking pixel gives a filter a reason to drop it.

**Inline style survives, declaration by declaration.** A signature pasted from
a mail client is nothing else, so dropping the attribute outright left the
right words in none of the right shapes. `letter.clean_style` keeps the
typography and the box properties and refuses the positioning family, along
with anything that fetches or runs.

**The preview is the message's own styling.** The panel below the editor renders
the fragment inside the same container `letter.wrap` builds, so the width, the
font and the rule above the signature are what a recipient will see rather than
an approximation in the console's own stylesheet.
"""

from __future__ import annotations

from typing import Any

from ..sources import letter
from . import ui
from .ui import esc
from .render import page

# The preview reproduces the message rather than the console, so its colours
# and its font come from `letter`, which is where they are defined for the mail
# itself. Spelled here they would be a second definition, and the preview would
# start lying about what a recipient sees the first time one of them changed.
PREVIEW = (
    f".sig-preview .inner {{ background:{letter.PAGE}; color:{letter.INK};"
    f" font-family:{letter.FAMILY}; font-size:{letter.SIZE};"
    f" line-height:{letter.LEADING}; padding:18px 16px; }}"
    f".sig-preview .rule {{ border-top:1px solid {letter.RULE};"
    f" margin-top:1.1rem; padding-top:1.1rem; font-size:13.5px;"
    f" color:{letter.MUTED}; }}"
)

STYLES = """
.sig-wrap { max-width:46rem; }
.sig-edit { min-height:9rem; padding:.9rem 1rem; border:1px solid var(--line);
            border-radius:6px; background:var(--soft); font-size:.95rem;
            line-height:1.55; outline:none; }
.sig-edit:focus { border-color:var(--accent); }
.sig-bar { display:flex; gap:.4rem; margin:.6rem 0; flex-wrap:wrap; }
.sig-bar button { font-size:.8rem; padding:.2rem .55rem; }
.sig-preview { margin-top:1.4rem; border:1px solid var(--line);
               border-radius:6px; padding:0; overflow:hidden; }
.sig-src { width:100%; min-height:7rem; font-family:ui-monospace, monospace;
           font-size:.78rem; }
""" + PREVIEW


def shell(body: str, chrome: Any = None) -> str:
    return page("", body, chrome, css=STYLES)


# The editor writes into a hidden field on submit, because a contenteditable
# div is not a form control and posts nothing on its own.
SCRIPT = """
<script>
(function () {
  var edit = document.getElementById('sig-edit');
  var field = document.getElementById('sig-html');
  var form = document.getElementById('sig-form');
  if (!edit || !field || !form) return;
  form.addEventListener('submit', function () { field.value = edit.innerHTML; });
  document.querySelectorAll('[data-cmd]').forEach(function (b) {
    b.addEventListener('click', function (e) {
      e.preventDefault();
      var cmd = b.getAttribute('data-cmd');
      if (cmd === 'createLink') {
        var url = window.prompt('Link address');
        if (!url) return;
        document.execCommand('createLink', false, url);
      } else {
        document.execCommand(cmd, false, null);
      }
      edit.focus();
    });
  });
})();
</script>
"""


def signature_page(fragment: str) -> str:
    """The editor, the HTML behind it, and a preview in the message's styling."""
    banners = ""
    if not fragment.strip():
        banners += ui.banner(
            "No signature is written yet. Anything saved here is appended to "
            "every message a campaign wave sends, below a thin rule.",
            kind="note",
        )
    buttons = "".join(
        f'<button type="button" data-cmd="{cmd}" title="{esc(title)}" '
        f'aria-label="{esc(title)}">{esc(label)}</button>'
        for cmd, label, title in (
            ("bold", "B", "Bold"),
            ("italic", "I", "Italic"),
            ("underline", "U", "Underline"),
            ("createLink", "Link", "Add a link"),
            ("removeFormat", "Clear", "Remove the formatting"),
            ("insertUnorderedList", "List", "Bulleted list"),
        )
    )
    return (
        '<div class="sig-wrap">'
        "<h1>Signature</h1>"
        "<p class='lede'>Appended to every message a "
        "<a href='/campaigns'>campaign</a> wave sends. Paste from your mail "
        "client, or type here. What is saved is cleaned down to a short tag "
        "allow-list, so a script or a tracking pixel never survives.</p>"
        + banners
        + f'<form method="post" action="/signature" id="sig-form">'
        f'<div class="sig-bar">{buttons}</div>'
        f'<div class="sig-edit" id="sig-edit" contenteditable="true" '
        'role="textbox" aria-multiline="true" aria-label="Signature">'
        f"{fragment}</div>"
        '<input type="hidden" name="html" id="sig-html">'
        '<div class="row" style="margin-top:.9rem;gap:.5rem">'
        '<button type="submit">Save</button></div></form>'
        + _preview(fragment)
        + _source(fragment)
        + "</div>"
        + SCRIPT
    )


def for_preview(fragment: str) -> str:
    """The fragment with `cid:` swapped for the route that serves the picture.

    A message attaches its logo and points at `cid:signature`, which resolves
    inside the message and nowhere else. A browser shown that markup draws a
    broken icon, so the preview, whose whole job is to show what a recipient
    will see, would be lying in the one place it must not.
    """
    return fragment.replace('src="cid:signature"', 'src="/signature/image"')


def _preview(fragment: str) -> str:
    """The signature as a recipient sees it, in the message's own container."""
    fragment = for_preview(fragment)
    return (
        "<h2 style='margin-top:1.6rem'>as a recipient sees it</h2>"
        '<div class="sig-preview"><div class="inner">'
        "<p style='margin:0'>Avec mes meilleures salutations,</p>"
        f'<div class="rule">{fragment}</div>'
        "</div></div>"
    )


def _source(fragment: str) -> str:
    """The stored HTML, for anybody who would rather edit it directly."""
    return (
        "<h2 style='margin-top:1.4rem'>the stored fragment</h2>"
        '<form method="post" action="/signature">'
        + ui.sr_label("The stored fragment", "sig-source")
        + f'<textarea class="sig-src" id="sig-source" name="html">'
        f"{esc(fragment)}</textarea>"
        '<div class="row" style="margin-top:.5rem">'
        '<button type="submit">Save this instead</button></div></form>'
    )


def plain_text(fragment: str) -> str:
    """What the text part of a message carries, for the page to show."""
    return letter.to_text(fragment)
