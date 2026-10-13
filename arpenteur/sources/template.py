"""Reading a campaign's message templates: parsing only, no I/O beyond the files.

**The convention**, chosen so a template stays a document a person edits and
not a format a program owns:

  templates/<campaign>/<lang>.md            the message for that language
  templates/<campaign>/<lang>.<segment>.md  optional, a whole-file override
                                            for one band, e.g. `de.mountain-transport.md`

The message is the text between the first two lines that are exactly `---`.
Anything before is notes for the person editing; anything after is ignored,
which is where the Limen files keep their prose about a variant paragraph.
The message's first line is the language's subject label and a colon, and
everything after that first colon is the subject, colons included:
`Objet : États des lieux locatifs : invitation à tester Limen`.

**No placeholders.** Every recipient of a variant receives the same words, so
the message a test send delivers to the operator is the message every firm in
the wave receives, header aside. That is a promise a test can check, and a
template with `{firm}` in it could not make it.

**Refused rather than guessed.** A file without two rules, with another
language's label, with nothing in it, or still carrying an unfinished marker is
an error naming the file. A campaign never sends a message its author had not
finished.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

# The subject label each language's templates open with.
LABELS: dict[str, tuple[str, ...]] = {
    "fr": ("Objet",),
    "de": ("Betreff",),
    "it": ("Oggetto",),
    "rm": ("Object",),
    "en": ("Subject",),
}

# What an unfinished template carries. The draft stage's own marker, and the
# phrase the briefs use for a fact nobody has supplied yet.
MARKERS = ("[[ MANQUE", "À compléter")

_RULE = re.compile(r"^---\s*$")
_SUBJECT = re.compile(
    r"^\s*(?P<label>[^\W\d_]+)[ \t  ]*:[ \t  ]*(?P<subject>.*)$"
)
_FILE = re.compile(r"^(?P<lang>[a-z]{2})(?:\.(?P<segment>[a-z0-9-]+))?\.md$")


class TemplateError(ValueError):
    """A template that cannot be sent as it stands, naming the file and why."""


@dataclass(frozen=True)
class Template:
    path: str            # relative to the survey root: templates/limen/fr.md
    language: str
    segment: str | None
    subject: str
    body: str
    sha: str             # of the file's bytes, so any edit changes it

    @property
    def variant(self) -> str:
        return f"{self.language}.{self.segment}" if self.segment else self.language


def parse(text: str, *, language: str, path: str = "") -> tuple[str, str]:
    """The subject and the body of one template file."""
    where = path or f"the {language} template"
    lines = text.splitlines()
    rules = [i for i, line in enumerate(lines) if _RULE.match(line)]
    if len(rules) < 2:
        raise TemplateError(
            f"{where}: the message goes between two lines of `---`; "
            f"found {len(rules)}."
        )
    block = lines[rules[0] + 1:rules[1]]
    first = next((i for i, line in enumerate(block) if line.strip()), None)
    if first is None:
        raise TemplateError(f"{where}: nothing between the two `---` lines.")
    found = _SUBJECT.match(block[first])
    expected = LABELS.get(language, ())
    if not found:
        raise TemplateError(
            f"{where}: the message must open with "
            f"`{expected[0] if expected else 'Subject'} : …`."
        )
    label = found["label"]
    if label.lower() not in {e.lower() for e in expected}:
        owner = next((lang for lang, names in LABELS.items()
                      if label.lower() in {n.lower() for n in names}), None)
        raise TemplateError(
            f"{where}: opens with `{label}`"
            + (f", the {owner} label," if owner else ",")
            + f" where a {language} template opens with `{expected[0] if expected else '?'}`."
        )
    subject = found["subject"].strip()
    body = "\n".join(block[first + 1:]).strip("\n")
    if not subject:
        raise TemplateError(f"{where}: the subject line is empty.")
    if not body.strip():
        raise TemplateError(f"{where}: the message has a subject and no text.")
    for marker in MARKERS:
        if marker in subject or marker in body:
            raise TemplateError(f"{where}: still marked `{marker}`; finish it first.")
    return subject, body


def file_names(language: str, segment: str | None) -> list[str]:
    """The files that would serve this language and band, most specific first."""
    names = [f"{language}.{segment}.md"] if segment else []
    return names + [f"{language}.md"]


def load(folder: Path, *, root: Path) -> dict[tuple[str, str | None], Template]:
    """Every template in a campaign's folder, keyed by language and band.

    The README is notes, never a template. A file whose name follows no part of
    the convention is ignored; one that does and fails to parse is an error,
    because a broken template in a campaign's folder is worth fixing before
    anything is sent from it.
    """
    templates: dict[tuple[str, str | None], Template] = {}
    if not folder.is_dir():
        return templates
    for path in sorted(folder.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        named = _FILE.match(path.name)
        if not named:
            continue
        language, segment = named["lang"], named["segment"]
        raw = path.read_bytes()
        relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
        subject, body = parse(raw.decode("utf-8"), language=language, path=relative)
        templates[(language, segment)] = Template(
            relative, language, segment, subject, body,
            hashlib.sha256(raw).hexdigest(),
        )
    return templates


def pick(templates: Mapping[tuple[str, str | None], Template], language: str,
         segment: str | None) -> Template | None:
    """The band's own file if there is one, else the language's."""
    return templates.get((language, segment)) or templates.get((language, None))


def missing(needs: Mapping[tuple[str, str | None], int],
            templates: Mapping[tuple[str, str | None], Template],
            folder: str) -> list[str]:
    """The files a wave needs and the folder lacks, with how many each serves."""
    short: dict[str, int] = {}
    for (language, segment), count in needs.items():
        if pick(templates, language, segment) is None:
            name = f"{folder.rstrip('/')}/{language}.md"
            short[name] = short.get(name, 0) + count
    return [f"{name} ({n} recipient{'s' if n != 1 else ''})"
            for name, n in sorted(short.items())]


def digest(used: Iterable[Template]) -> str:
    """One fingerprint over every template a wave was rendered from."""
    pairs = sorted({(t.path, t.sha) for t in used})
    return hashlib.sha256(repr(pairs).encode()).hexdigest()[:16]
