"""Fetch a Wikipedia article through the Wikimedia Enterprise On-demand API.

Structured Contents returns the article already split into sections and paragraphs, so we
can hand the model clean text instead of wikitext or 650 KB of HTML.
"""

import os
import time
from urllib.parse import unquote, urlparse

import httpx

AUTH_URL = "https://auth.enterprise.wikimedia.com/v1/login"
ARTICLE_URL = "https://api.enterprise.wikimedia.com/v2/structured-contents/{name}"
# Enough for a 3-5 slide deck. The whole of a long article would crowd the context.
MAX_CHARS = 12_000
# The articles this one links to, in order. Enough to pick topics from.
MAX_LINKS = 100

# Access tokens last 24 hours. Log in once per process and reuse the token.
_token = {"value": None, "expires": 0.0}


def _access_token() -> str:
    if _token["value"] and time.time() < _token["expires"]:
        return _token["value"]
    if not os.environ.get("WIKIMEDIA_USERNAME") or not os.environ.get("WIKIMEDIA_PASSWORD"):
        raise RuntimeError("Set WIKIMEDIA_USERNAME and WIKIMEDIA_PASSWORD in this example's .env file.")
    response = httpx.post(
        AUTH_URL,
        # The API wants the username in lowercase.
        json={"username": os.environ["WIKIMEDIA_USERNAME"].lower(), "password": os.environ["WIKIMEDIA_PASSWORD"]},
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    _token.update(value=body["access_token"], expires=time.time() + body.get("expires_in", 86_400) - 60)
    return _token["value"]


def title_from_url(url: str) -> str:
    """'https://en.wikipedia.org/wiki/Ada_Lovelace#Life' -> 'Ada_Lovelace'. A bare title passes through."""
    path = urlparse(url).path
    title = path.split("/wiki/", 1)[1] if "/wiki/" in path else url
    return unquote(title).strip().replace(" ", "_")


def _flatten(parts: list, depth: int, lines: list[str], links: dict[str, str]) -> None:
    """Walk the section tree: headings for sections, text for everything that has a value, and
    every link to another article (url -> link text)."""
    for part in parts:
        for link in part.get("links", []):
            # Skip footnotes ('#cite_note-...'), which link back to this article and have no text
            if link.get("text") and "#" not in link["url"]:
                # Encode parentheses: markdown ends a link's URL at the first ")"
                links.setdefault(link["url"].replace("(", "%28").replace(")", "%29"), link["text"])
        if part.get("type") == "section" and part.get("name"):
            lines.append("\n" + "#" * depth + " " + part["name"])
            _flatten(part.get("has_parts", []), depth + 1, lines, links)
        elif part.get("value"):
            lines.append(part["value"])
        elif part.get("has_parts"):
            _flatten(part["has_parts"], depth, lines, links)


def fetch_article(url: str) -> dict:
    """Return the English Wikipedia article at url as {title, url, description, text, links}."""
    response = httpx.post(
        ARTICLE_URL.format(name=title_from_url(url)),
        headers={"Authorization": f"Bearer {_access_token()}", "accept": "application/json"},
        json={"filters": [{"field": "is_part_of.identifier", "value": "enwiki"}], "limit": 1},
        timeout=60,
    )
    response.raise_for_status()
    results = response.json()
    if not results:
        raise LookupError(f"No English Wikipedia article for {url}")
    article = results[0]

    lines: list[str] = []
    links: dict[str, str] = {}
    _flatten(article.get("sections", []), 2, lines, links)
    text = "\n".join(lines).strip()
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[Article truncated.]"
    return {
        "title": article["name"],
        "url": article["url"],
        "description": article.get("description", ""),
        "text": text,
        "links": [{"text": t, "url": u} for u, t in list(links.items())[:MAX_LINKS]],
    }


if __name__ == "__main__":
    # Smoke test: uv run wikipedia.py https://en.wikipedia.org/wiki/Ada_Lovelace
    import sys

    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")
    a = fetch_article(sys.argv[1] if len(sys.argv) > 1 else "https://en.wikipedia.org/wiki/Ada_Lovelace")
    assert a["title"] and a["text"], "empty article"
    print(a["title"], "|", a["description"], "|", len(a["text"]), "chars")
    print(a["text"][:600])
    print(len(a["links"]), "links, e.g.", a["links"][:3])
