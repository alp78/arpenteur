"""The shell as every page meets it: a title, one banner, a way to help.

These walk the header rather than naming pages, so a page added to a module
is covered the day it appears in the menu.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arpenteur import db
from arpenteur.config import load_settings
from arpenteur.console import create_app

TOML = """
[survey]
cantons = ["VD"]
legal_forms = ["0107"]

[segments]
sme-owner = "A small owner-run business."

[sectors]
hospitality = "Restaurants, cafés and hotels."

[zefix]
base_url = "https://example.test/ZefixPublicREST"
rate_limit = 2.0
max_retries = 0
contact = "tests"
"""


@pytest.fixture
def client(tmp_path: Path, db_path: str) -> TestClient:
    """Real settings from a file, so the Settings page renders too."""
    (tmp_path / "arpenteur.toml").write_text(TOML, encoding="utf-8")
    conn = db.open_db(db_path)
    db.upsert_company(
        conn,
        {"uid": "CHE1", "name": "Auberge Sàrl", "canton": "VD", "status": "ACTIVE",
         "method": "zefix"},
    )
    conn.commit()
    conn.close()
    return TestClient(create_app(load_settings(tmp_path)))


def _header_links(client: TestClient) -> list[str]:
    header = client.get("/").text.split("<header>")[1].split("</header>")[0]
    return re.findall(r'<a href="(/[^"]*)"', header)


def test_every_page_in_the_header_names_itself_in_the_tab(client) -> None:
    """A tab reading "arpenteur" eighteen times over is eighteen tabs nobody
    can tell apart."""
    seen = {}
    for href in _header_links(client):
        page = client.get(href)
        assert page.status_code == 200, href
        title = re.search(r"<title>([^<]*)</title>", page.text).group(1)
        assert title.endswith(" | Arpenteur") and title != " | Arpenteur", (href, title)
        seen[href] = title
    assert len(set(seen.values())) == len(seen), seen


def test_every_page_in_the_header_draws_the_refusal_exactly_once(client) -> None:
    """The shell draws `?error=`; a page that also draws it says it twice."""
    for href in _header_links(client):
        body = client.get(href, params={"error": "Refused here"}).text
        assert body.count("Refused here") == 1, href
        assert 'class="banner banner-bad"' in body, href


def test_every_page_in_the_header_links_its_own_guide(client) -> None:
    for href in _header_links(client):
        body = client.get(href).text
        guide = re.search(r'<a class="help" href="/help/([^"]+)"', body).group(1)
        expected = "index" if href in ("/", "/help") else "guide/"
        assert guide.startswith(expected), (href, guide)
