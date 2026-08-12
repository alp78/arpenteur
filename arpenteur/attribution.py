"""Attribution and the limits of what the register's data means.

Zefix grants REST access free, on conditions. Two of them are not about
politeness but about what may be said with the data afterwards, and they bind
anything this tool emits: a drafted email, an export, a console page.

    Si les données sont utilisées ultérieurement, leur origine doit être
    indiquée et il faut faire référence à une éventuelle modification des
    données.

    Les données et références accessibles via l'interface REST sont sans
    garantie et n'ont aucun effet juridique. Sont contraignants seulement
    l'extrait certifié du registre du commerce cantonal et le texte de la
    publication dans la feuille officielle suisse du commerce (FOSC).

So origin must be stated, alteration must be declared, and nothing here may
be presented as authoritative. That last point has teeth for this tool in
particular: the officers in `person` are not registry fields. They are a
regex parse of French registrar prose, and a parse can be wrong in ways that
matter, such as naming someone who resigned. Calling that "the register says"
in an email to a stranger would be both a false statement and a breach of the
terms. Hence DERIVED below, and hence `method` on every row.

The strings are French because the correspondence, the register and the
recipients are.
"""

from __future__ import annotations

# Where a fact came from, keyed by the `method` column that every table
# holding an outside fact already carries. Origin is per row because the
# rows do not share one: a purpose is the register's words, a website
# summary is a model's reading of a company's own page.
ORIGIN = {
    "zefix": "Registre du commerce, via Zefix (OFRC)",
    "shab": "FOSC / SHAB, via Zefix (OFRC)",
    "web-search": "Site web de l'entreprise",
    "site": "Site web de l'entreprise",
    "published": "Site web de l'entreprise",
}

# Methods whose rows are our reading of a source rather than the source, and
# so must be declared as modified under the second condition above.
#
# `published` is deliberately absent. An address lifted verbatim from a
# `mailto:` link on a company's own contact page is not our reading of
# anything: it is the string the company published, copied. The line is
# between transcription and interpretation, not between registry and web.
DERIVED = frozenset({"shab", "web-search", "site"})

SOURCE_NOTICE = (
    "Source des données d'entreprise : registre du commerce suisse, "
    "obtenues via l'interface REST publique de Zefix (Office fédéral du "
    "registre du commerce)."
)

# Required wherever registry-derived data is shown or sent. The wording
# tracks the terms rather than paraphrasing them.
NO_WARRANTY_NOTICE = (
    "Ces données sont fournies sans garantie et n'ont aucun effet juridique. "
    "Seuls l'extrait certifié du registre du commerce cantonal et le texte "
    "publié dans la FOSC font foi."
)

MODIFIED_NOTICE = (
    "Certaines informations (dirigeants, segments, résumés d'activité) sont "
    "dérivées par traitement automatique des textes publiés et ne sont pas "
    "des champs du registre."
)


def notice(methods: object = ()) -> str:
    """The attribution to attach to anything leaving the tool.

    Takes the `method` values behind whatever is being shown, so an output
    built only from verbatim registry fields does not carry a modification
    disclaimer it does not need, and one built on a SHAB parse always does.
    """
    lines = [SOURCE_NOTICE, NO_WARRANTY_NOTICE]
    if any(str(method) in DERIVED for method in methods):
        lines.append(MODIFIED_NOTICE)
    return "\n".join(lines)


def is_derived(method: str | None) -> bool:
    """True when a row is our reading of a source rather than the source."""
    return str(method or "") in DERIVED
