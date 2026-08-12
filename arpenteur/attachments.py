"""Documents a client sends: stored once, readable by an agent.

Two jobs, deliberately separate. Storage puts the bytes somewhere durable and
says where. Extraction turns them into text an agent can be given.

**Content-addressed.** The file on disk is named by the sha256 of its contents,
so the same document sent twice is one file, and `Rapport 2025 VF (2).pdf` is
visibly the same document as `Rapport 2025 final.pdf` rather than a second one.
The name the client used is kept in the database, because that is how they will
refer to it on the phone.

**Outside the database.** A twenty megabyte PDF in a row makes every query that
touches the table heavier and turns the backup of a working database into a
document archive. The database holds the path, the digest and the extracted
text; the bytes live under `data/attachments/`.

**Extracted once, at upload.** Not per read. Extraction fails on scanned paper
and on formats nothing here can open, and a failure recorded once against the
row is answerable, where one that happens silently every time an agent reads is
not. A file that cannot be read is still stored: it can be opened by a person,
and saying so is more useful than refusing the upload.

Nothing here reaches the network and nothing executes what it reads. The
formats handled are the ones a Swiss SME actually sends: PDF, Word, and plain
text in its several disguises.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

# What one document may weigh. Not a technical limit: a hundred megabyte file
# is nearly always a scan of something that should have been sent as a
# spreadsheet, and refusing it early is kinder than storing it and finding out
# at extraction time that it holds no text at all.
MAX_BYTES = 25 * 1024 * 1024

# How much extracted text a single document contributes to a prompt. A 200-page
# rapport de gestion is perfectly normal and would dominate everything else the
# advise stage was given. The cut is recorded in the row, so a proposal built
# on a truncated reading can be told from one built on the whole document.
PROMPT_CHARS = 24_000

TEXTUAL = {
    "text/plain", "text/markdown", "text/csv", "text/tab-separated-values",
    "application/json", "text/html", "application/xml", "text/xml",
}

TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".html",
    ".htm", ".log", ".ics", ".eml",
}

# What a document is, for the operator and for the prompt. A contract and a
# marketing brochure carry very different weight in a proposal, and the model
# should not have to infer which it is holding from the prose.
KINDS: tuple[tuple[str, str], ...] = (
    ("", "Not classified"),
    ("contract", "Contract or engagement letter"),
    ("accounts", "Accounts, budget or financial statement"),
    ("report", "Annual report or activity report"),
    ("policy", "Policy, procedure or internal rule"),
    ("inventory", "System inventory, licence list or asset register"),
    ("architecture", "Architecture, network or process diagram"),
    ("correspondence", "Correspondence, printed or exported"),
    ("tender", "Tender, specification or requirements document"),
    ("invoice", "Invoice or quotation, theirs or a supplier's"),
    ("other", "Something else"),
)

KIND_LABELS = dict(KINDS)


@dataclass(frozen=True)
class Stored:
    sha256: str
    path: Path
    bytes: int
    # True when this exact content was already on disk. Surfaced rather than
    # swallowed: "you already have this" is the useful answer when someone
    # re-sends a document, not a silent second row.
    existed: bool


def root_for(data_dir: Path) -> Path:
    return Path(data_dir) / "attachments"


def store(data_dir: Path, content: bytes, filename: str) -> Stored:
    """Put the bytes on disk under their own digest, and say where.

    The suffix is kept on the stored name purely so a person browsing the
    directory can tell a PDF from a spreadsheet. Nothing reads it back: the
    media type in the database is what the code trusts.
    """
    if len(content) > MAX_BYTES:
        raise ValueError(
            f"{filename} is {len(content) / 1e6:.1f} MB, over the {MAX_BYTES / 1e6:.0f} MB "
            "limit. A file this size is usually a scan; ask for the original."
        )
    if not content:
        raise ValueError(f"{filename} is empty.")

    digest = hashlib.sha256(content).hexdigest()
    suffix = Path(filename).suffix.lower()[:8]
    if not re.fullmatch(r"\.[a-z0-9]+", suffix or ""):
        suffix = ""
    target = root_for(data_dir) / digest[:2] / f"{digest}{suffix}"
    if target.exists():
        return Stored(digest, target, len(content), existed=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Written to a neighbouring name and moved, so a crash mid-write cannot
    # leave a truncated file sitting at a digest that promises whole contents.
    scratch = target.with_suffix(target.suffix + ".part")
    scratch.write_bytes(content)
    scratch.replace(target)
    return Stored(digest, target, len(content), existed=False)


def extract(content: bytes, filename: str, media_type: str = "") -> tuple[str, str]:
    """Text from a document, and the reason if there is none.

    Returns `(text, error)`. Exactly one is ever non-empty, and an empty
    extraction from a readable format is reported as an error rather than as
    success with nothing in it: a scanned PDF yields zero characters, and
    silently storing that as "extracted" is how a document nobody can read gets
    fed to a model as though it had been read.
    """
    suffix = Path(filename).suffix.lower()
    media = (media_type or "").split(";")[0].strip().lower()

    try:
        if media == "application/pdf" or suffix == ".pdf":
            text = _from_pdf(content)
        elif suffix == ".docx" or media == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            text = _from_docx(content)
        elif media in TEXTUAL or suffix in TEXT_SUFFIXES:
            text = _from_text(content, suffix)
        else:
            return "", (
                f"No text extractor for {suffix or media or 'this format'}. The "
                "file is stored and can be opened by hand. Spreadsheets read "
                "far better as CSV; scans need to be re-sent as the original."
            )
    except Exception as exc:  # noqa: BLE001 - the reason is the deliverable
        return "", f"{type(exc).__name__}: {exc}"

    text = _tidy(text)
    if not text:
        return "", (
            "The file opened but holds no text. This is what a scan or a "
            "photographed page looks like: ask for the file it was printed "
            "from, or have it put through OCR first."
        )
    return text, ""


def _from_pdf(content: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency is declared
        raise RuntimeError(
            "PDF text extraction needs pypdf. Run: uv sync"
        ) from exc

    reader = PdfReader(BytesIO(content))
    pages = []
    for number, page in enumerate(reader.pages, 1):
        try:
            body = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad page must not lose the rest
            body = ""
        if body.strip():
            # Numbered because a proposal citing a document should be able to
            # say where in it, and a page number is the only reference a client
            # can check against their own copy.
            pages.append(f"[page {number}]\n{body.strip()}")
    return "\n\n".join(pages)


def _from_docx(content: bytes) -> str:
    """Word, through the standard library.

    A .docx is a zip holding XML. python-docx would be tidier and is another
    dependency for one function; the paragraph structure is recoverable from
    the markup directly, and losing a table's cell borders is not a loss when
    the destination is a prompt.
    """
    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = [
            name for name in archive.namelist()
            if name == "word/document.xml"
            or name.startswith(("word/header", "word/footer"))
        ]
        if "word/document.xml" not in archive.namelist():
            raise ValueError("not a Word document (no word/document.xml)")
        chunks = []
        for name in sorted(names, key=lambda n: n != "word/document.xml"):
            xml = archive.read(name).decode("utf-8", errors="replace")
            # Paragraph and row ends become newlines before the tags go, or the
            # whole document arrives as one line.
            xml = re.sub(r"</w:(p|tr)>", "\n", xml)
            xml = re.sub(r"</w:tc>", "\t", xml)
            chunks.append(re.sub(r"<[^>]+>", "", xml))
    import html as html_mod

    return html_mod.unescape("\n".join(chunks))


def _from_text(content: bytes, suffix: str) -> str:
    text = content.decode("utf-8", errors="replace")
    if suffix in (".html", ".htm"):
        import html as html_mod

        text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", text)
        text = html_mod.unescape(re.sub(r"<[^>]+>", " ", text))
    elif suffix == ".json":
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=1)
        except ValueError:
            pass
    return text


def _tidy(text: str) -> str:
    """Collapse the whitespace a PDF extractor leaves behind.

    Not cosmetic: a page of justified text arrives with runs of spaces between
    every word, and those are tokens the advise stage pays for and learns
    nothing from.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def for_prompt(text: str | None, *, limit: int = PROMPT_CHARS) -> tuple[str, bool]:
    """What of a document goes into a prompt, and whether it was cut."""
    body = (text or "").strip()
    if len(body) <= limit:
        return body, False
    return body[:limit], True
