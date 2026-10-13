"""Reading a campaign's templates.

The Limen files are read as they stand, with property checks only, so the
operator's edits to their wording never break a test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arpenteur.sources import template as T

ROOT = Path(__file__).resolve().parents[1]

GOOD = """# notes for whoever edits this

---

Objet : Un essai : deux points dans le sujet

Madame, Monsieur,

Le message.

---

## Après le second séparateur

Ceci ne part jamais.
"""


def test_the_message_is_between_the_first_two_rules() -> None:
    subject, body = T.parse(GOOD, language="fr")
    assert body.startswith("Madame, Monsieur,") and body.endswith("Le message.")


def test_a_subject_may_contain_colons() -> None:
    assert T.parse(GOOD, language="fr")[0] == "Un essai : deux points dans le sujet"


def test_nothing_after_the_second_rule_leaks() -> None:
    assert "ne part jamais" not in T.parse(GOOD, language="fr")[1]


def test_the_label_must_match_the_language() -> None:
    with pytest.raises(T.TemplateError, match="the fr label"):
        T.parse(GOOD, language="de", path="templates/x/de.md")


def test_a_file_without_two_rules_is_refused_by_name() -> None:
    with pytest.raises(T.TemplateError, match="templates/x/fr.md"):
        T.parse("Objet : x\n\ntexte\n", language="fr", path="templates/x/fr.md")


def test_a_marker_is_refused() -> None:
    unfinished = GOOD.replace("Le message.", "Le lien : [[ MANQUE lien ]]")
    with pytest.raises(T.TemplateError, match="MANQUE"):
        T.parse(unfinished, language="fr")


def test_an_empty_message_is_refused() -> None:
    with pytest.raises(T.TemplateError):
        T.parse("---\nObjet : x\n---\n", language="fr")


def test_a_segment_file_overrides_the_language_file(tmp_path: Path) -> None:
    folder = tmp_path / "templates" / "c"
    folder.mkdir(parents=True)
    (folder / "de.md").write_text(GOOD.replace("Objet", "Betreff"), encoding="utf-8")
    (folder / "de.mountain-transport.md").write_text(
        GOOD.replace("Objet", "Betreff").replace("Le message.", "Für Bergbahnen."),
        encoding="utf-8")
    found = T.load(folder, root=tmp_path)
    assert "Bergbahnen" in T.pick(found, "de", "mountain-transport").body
    assert "Bergbahnen" not in T.pick(found, "de", "tourism-office").body
    assert T.pick(found, "de", "mountain-transport").variant == "de.mountain-transport"


def test_a_missing_language_is_named_by_file() -> None:
    gaps = T.missing({("it", None): 3, ("de", None): 1}, {}, "templates/drymos")
    assert gaps == ["templates/drymos/de.md (1 recipient)",
                    "templates/drymos/it.md (3 recipients)"]


def test_the_readme_is_not_a_template(tmp_path: Path) -> None:
    folder = tmp_path / "templates" / "c"
    folder.mkdir(parents=True)
    (folder / "README.md").write_text("no rules here", encoding="utf-8")
    assert T.load(folder, root=tmp_path) == {}


def test_one_byte_changes_the_digest(tmp_path: Path) -> None:
    folder = tmp_path / "templates" / "c"
    folder.mkdir(parents=True)
    (folder / "fr.md").write_text(GOOD, encoding="utf-8")
    before = T.digest(T.load(folder, root=tmp_path).values())
    (folder / "fr.md").write_text(GOOD + " ", encoding="utf-8")
    assert T.digest(T.load(folder, root=tmp_path).values()) != before


@pytest.mark.parametrize("language", ["fr", "de", "it"])
def test_the_limen_templates_parse_as_they_stand(language: str) -> None:
    found = T.load(ROOT / "templates" / "limen", root=ROOT)
    t = T.pick(found, language, None)
    assert t is not None and t.subject and t.body.strip()
    assert t.path == f"templates/limen/{language}.md"
    assert "---" not in t.body
