"""Reading addresses off a company's own page.

The cases here are the ones that make the difference between a contact list
and a list of image files. Every false positive in this parser becomes a row
in `contact` that looks exactly like a fact.
"""

from __future__ import annotations

from arpenteur.sources import web

PAGE = """
<html><head>
  <style>.logo{background:url(sprite@2x.png)}</style>
  <script>var t="tracker@sentry.io";</script>
</head><body>
  <img src="logo@2x.png">
  <a href="/nous-contacter">Nous contacter</a>
  <a href="/a-propos">A propos</a>
  <a href="/services">Services</a>
  <a href="https://autre.ch/contact">Un partenaire</a>
  <p>Ecrivez-nous : <a href="mailto:info@etude.ch?subject=Bonjour">info@etude.ch</a></p>
  <p>Direction : marie.perret&#64;etude.ch</p>
</body></html>
"""


# --- addresses ----------------------------------------------------------------


def test_a_mailto_comes_before_an_address_merely_written_on_the_page() -> None:
    """Order is why this returns a list. A mailto is an address published as
    a way to reach the company, which is a stronger claim than one that
    happens to appear in the text, and the caller keeps the first."""
    assert web.emails_in(PAGE)[0] == "info@etude.ch"


def test_an_html_entity_address_is_found() -> None:
    """`&#64;` for @ is a standard way of writing one, and it is invisible to
    any pattern applied to raw HTML."""
    assert "marie.perret@etude.ch" in web.emails_in(PAGE)


def test_a_retina_image_is_not_a_contact() -> None:
    """`logo@2x.png` matches any sane address pattern. A page full of them
    yields a contact list of image files."""
    found = web.emails_in(PAGE)
    assert not [a for a in found if a.endswith(".png")]


def test_the_site_tooling_is_not_the_company() -> None:
    """A Sentry DSN in a script tag is not a person one may write to."""
    assert "tracker@sentry.io" not in web.emails_in(PAGE)


def test_an_address_is_reported_once_however_often_it_appears() -> None:
    page = "<a href='mailto:a@b.ch'>a@b.ch</a> a@b.ch A@B.CH"
    assert web.emails_in(page) == ["a@b.ch"]


def test_common_obfuscation_is_undone() -> None:
    page = "<p>contact [at] bureau [dot] ch</p>"
    assert web.emails_in(page) == ["contact@bureau.ch"]


def test_the_word_at_in_prose_is_not_an_address() -> None:
    """The reason bare ' at ' is not in the substitution table: it turns
    ordinary English into a contact."""
    assert web.emails_in("<p>consulting at its best, ch</p>") == []


def test_a_mailto_carrying_several_addresses_yields_all_of_them() -> None:
    page = "<a href='mailto:a@b.ch,c@d.ch'>write</a>"
    assert web.emails_in(page) == ["a@b.ch", "c@d.ch"]


def test_a_trailing_full_stop_is_not_part_of_the_address() -> None:
    assert web.emails_in("<p>Ecrivez a info@etude.ch.</p>") == ["info@etude.ch"]


def test_a_page_with_nothing_on_it_is_not_an_error() -> None:
    assert web.emails_in("") == []
    assert web.emails_in("<html><body><p>Bonjour</p></body></html>") == []


# --- links --------------------------------------------------------------------


def test_contact_pages_are_preferred_over_about_pages() -> None:
    """The budget is a handful of fetches, so the ranking decides what gets
    read at all."""
    links = web.contact_links(PAGE, "https://etude.ch/")
    assert links[0] == "https://etude.ch/nous-contacter"
    assert "https://etude.ch/a-propos" in links


def test_a_link_off_the_site_is_not_followed() -> None:
    """Reading a partner's contact page would file their address under this
    company, which is worse than finding nothing."""
    links = web.contact_links(PAGE, "https://etude.ch/")
    assert not any("autre.ch" in link for link in links)


def test_an_unrelated_page_is_not_read() -> None:
    assert "https://etude.ch/services" not in web.contact_links(PAGE, "https://etude.ch/")


def test_www_and_bare_host_are_the_same_site() -> None:
    assert web.same_site("https://www.etude.ch/contact", "https://etude.ch/")
    assert not web.same_site("https://etude.ch/", "https://etude.fr/")


def test_a_relative_link_is_resolved_against_the_page_it_was_on() -> None:
    page = "<a href='contact.html'>ici</a>"
    assert web.contact_links(page, "https://etude.ch/fr/") == [
        "https://etude.ch/fr/contact.html"
    ]


def test_the_link_budget_is_honoured() -> None:
    page = "".join(f"<a href='/contact-{i}'>c</a>" for i in range(10))
    assert len(web.contact_links(page, "https://etude.ch/", limit=3)) == 3


def test_an_accented_path_matches_an_unaccented_hint() -> None:
    """The first live run of the stage found no address on a site that
    publishes one, because the page is called `mentions-légales` and a hint
    list is naturally written in ASCII."""
    page = "<a href='/mentions-légales'>Mentions</a>"
    assert web.contact_links(page, "https://etude.ch/") == [
        "https://etude.ch/mentions-légales"
    ]


def test_a_percent_encoded_path_matches_too() -> None:
    """Which is how the same link is actually served."""
    page = "<a href='/mentions-l%C3%A9gales'>Mentions</a>"
    assert web.contact_links(page, "https://etude.ch/")


# --- sitemaps -----------------------------------------------------------------

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://etude.ch/tarifs</loc></url>
  <url><loc>https://etude.ch/mentions-l%C3%A9gales</loc></url>
  <url><loc>https://etude.ch/blog</loc></url>
</urlset>
"""

INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://etude.ch/blog-posts-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://etude.ch/pages-sitemap.xml</loc></sitemap>
</sitemapindex>
"""


def test_a_sitemap_index_is_told_from_a_sitemap() -> None:
    assert web.is_sitemap_index(INDEX)
    assert not web.is_sitemap_index(SITEMAP)


def test_the_contact_page_is_picked_out_of_a_sitemap() -> None:
    """The way in when the home page has no links to read at all."""
    assert web.sitemap_links(SITEMAP, "https://etude.ch/") == [
        "https://etude.ch/mentions-l%C3%A9gales"
    ]


def test_a_sitemap_naming_another_site_is_ignored() -> None:
    xml = "<urlset><url><loc>https://autre.ch/contact</loc></url></urlset>"
    assert web.sitemap_links(xml, "https://etude.ch/") == []
