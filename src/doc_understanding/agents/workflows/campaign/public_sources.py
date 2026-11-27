"""Discover candidate report pages from bounded, official public indexes.

Candidates are links only: no PDF download, licensing determination, or dataset assignment.
"""
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from doc_understanding.runtime.catalogs import read_catalog


def configured_sources():
    sources = read_catalog('DOCUMENT_PUBLIC_SOURCE_CATALOG')
    ids = set()
    for source in sources:
        parts = urlsplit(source['url'])
        if (not source['id'] or source['id'] in ids or parts.scheme != 'https'
                or parts.hostname != source['host'] or parts.username or parts.password
                or parts.port not in (None, 443) or not source['path'].startswith('/')):
            raise ValueError('INVALID_PUBLIC_SOURCE')
        ids.add(source['id'])
        markers = source.get('rights_markers')
        if markers is not None and (not isinstance(markers, list) or not markers
                or any(not isinstance(m, str) or not m.strip() for m in markers)):
            raise ValueError('INVALID_RIGHTS_MARKERS')
    return sources


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_index(url):
    request = Request(url, headers={"User-Agent": "NHDocumentAgent/1.0"})
    with build_opener(NoRedirect).open(request, timeout=8) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("INDEX_TOO_LARGE")
        return raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None
        self.words = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.words = []

    def handle_data(self, data):
        if self.href is not None:
            self.words.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href is not None:
            self.links.append((self.href, " ".join(" ".join(self.words).split())))
            self.href = None


def discover(store, campaign_id, *, fetch=fetch_index, limit_per_source=5, sources=None):
    """Record official detail-page candidates and explicit per-source outcomes."""
    for source in configured_sources() if sources is None else sources:
        try:
            html = fetch(source["url"])
            parser = Links()
            parser.feed(html)
            found = 0
            for href, title in parser.links:
                url = urljoin(source["url"], href).split("#", 1)[0]
                parts = urlsplit(url)
                if (parts.scheme != "https" or parts.hostname != source["host"]
                        or parts.username or parts.password
                        or parts.port not in (None, 443)
                        or parts.path.lower().removesuffix(".aspx")
                        != source["path"].lower().removesuffix(".aspx") or not title):
                    continue
                if store.add_candidate(campaign_id, source, url, title):
                    found += 1
                if found >= limit_per_source:
                    break
            store.event(campaign_id, "collect", f"{source['id']}_candidates={found}")
        except (OSError, URLError, HTTPError, ValueError):
            store.event(campaign_id, "collect", f"{source['id']}_source_unavailable")
