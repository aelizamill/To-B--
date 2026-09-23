"""Book metadata lookup for To B--.

Goodreads retired its public API in December 2020 -- no new keys, and the
old ones stopped working. This module uses the two live replacements:

  * Google Books  -- best descriptions and thumbnails, no key needed for
                     light personal use.
  * Open Library  -- free, no key, good cover images, better coverage of
                     older and more obscure titles.

Search hits Google Books first and falls back to Open Library, and any
result missing a cover gets one from Open Library's ISBN cover endpoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Any

import requests

GOOGLE_URL = "https://www.googleapis.com/books/v1/volumes"
OPENLIB_URL = "https://openlibrary.org/search.json"
OPENLIB_COVER_ISBN = "https://covers.openlibrary.org/b/isbn/{}-L.jpg"
OPENLIB_COVER_ID = "https://covers.openlibrary.org/b/id/{}-L.jpg"

# Optional. Not required, but raises your daily quota if you have one.
GOOGLE_API_KEY = os.environ.get("GOOGLE_BOOKS_API_KEY")

TIMEOUT = 10


@dataclass
class Candidate:
    """One search result, ready to be shown or saved."""

    source_id: str
    title: str
    author: str
    year: str = ""
    cover_url: str | None = None
    summary: str = ""
    provider: str = "google"

    @property
    def label(self) -> str:
        bits = [self.title]
        if self.author:
            bits.append(f"by {self.author}")
        if self.year:
            bits.append(f"({self.year})")
        return " ".join(bits)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _https(url: str | None) -> str | None:
    """Google returns http:// thumbnails, which browsers block on https pages."""
    if url and url.startswith("http://"):
        return "https://" + url[len("http://") :]
    return url


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------

def _search_google(query: str, limit: int) -> list[Candidate]:
    params: dict[str, Any] = {"q": query, "maxResults": min(limit, 40)}
    if GOOGLE_API_KEY:
        params["key"] = GOOGLE_API_KEY

    response = requests.get(GOOGLE_URL, params=params, timeout=TIMEOUT)
    response.raise_for_status()

    candidates: list[Candidate] = []
    for item in response.json().get("items", [])[:limit]:
        info = item.get("volumeInfo", {})
        isbn = next(
            (
                ident["identifier"]
                for ident in info.get("industryIdentifiers", [])
                if ident.get("type") in ("ISBN_13", "ISBN_10")
            ),
            None,
        )
        cover = _https(info.get("imageLinks", {}).get("thumbnail"))
        if not cover and isbn:
            cover = OPENLIB_COVER_ISBN.format(isbn)

        candidates.append(
            Candidate(
                source_id=f"google:{item['id']}",
                title=info.get("title", "Unknown title"),
                author=", ".join(info.get("authors", [])),
                year=(info.get("publishedDate") or "")[:4],
                cover_url=cover,
                summary=info.get("description", "") or "",
                provider="google",
            )
        )
    return candidates


def _search_openlibrary(query: str, limit: int) -> list[Candidate]:
    params = {
        "q": query,
        "limit": limit,
        "fields": "key,title,author_name,first_publish_year,cover_i,isbn",
    }
    response = requests.get(OPENLIB_URL, params=params, timeout=TIMEOUT)
    response.raise_for_status()

    candidates: list[Candidate] = []
    for doc in response.json().get("docs", [])[:limit]:
        cover = None
        if doc.get("cover_i"):
            cover = OPENLIB_COVER_ID.format(doc["cover_i"])
        elif doc.get("isbn"):
            cover = OPENLIB_COVER_ISBN.format(doc["isbn"][0])

        candidates.append(
            Candidate(
                source_id=f"openlibrary:{doc.get('key', '')}",
                title=doc.get("title", "Unknown title"),
                author=", ".join(doc.get("author_name", []) or []),
                year=str(doc.get("first_publish_year") or ""),
                cover_url=cover,
                summary="",  # search results carry no description
                provider="openlibrary",
            )
        )
    return candidates


def fetch_openlibrary_summary(work_key: str) -> str:
    """Pull a description from an Open Library work page.

    Open Library's search endpoint omits descriptions, so this is a second
    request made only for the title the user actually picked.
    """
    key = work_key.replace("openlibrary:", "")
    if not key.startswith("/works/"):
        return ""
    try:
        response = requests.get(
            f"https://openlibrary.org{key}.json", timeout=TIMEOUT
        )
        response.raise_for_status()
        description = response.json().get("description", "")
    except (requests.RequestException, ValueError):
        return ""
    if isinstance(description, dict):
        return description.get("value", "")
    return description or ""


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------

def search(query: str, limit: int = 6) -> list[Candidate]:
    """Return candidate matches for a title the user typed.

    Your flowchart assumed one result comes back. In practice a title like
    "Circe" returns the novel, a study guide, and three editions -- so this
    returns a list and the UI asks you to pick.
    """
    query = query.strip()
    if not query:
        return []

    results: list[Candidate] = []
    try:
        results = _search_google(query, limit)
    except requests.RequestException:
        pass

    if not results:
        try:
            results = _search_openlibrary(query, limit)
        except requests.RequestException:
            return []

    return results


def enrich(candidate: Candidate) -> Candidate:
    """Fill in a missing summary once a candidate has been chosen."""
    if not candidate.summary and candidate.provider == "openlibrary":
        candidate.summary = fetch_openlibrary_summary(candidate.source_id)
    return candidate
