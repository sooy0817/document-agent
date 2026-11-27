"""Collect PDFs under an explicitly configured source policy, with provenance."""
import hashlib
import sqlite3
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import Request, build_opener

import fitz

from .public_sources import Links, NoRedirect, fetch_index, configured_sources

MAX_BYTES = 20 * 1024 * 1024


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, value):
        self.parts.append(value)


def download_pdf(url):
    req = Request(url, headers={"User-Agent": "NHDocumentAgent/1.0"})
    with build_opener(NoRedirect).open(req, timeout=15) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or not data.startswith(b"%PDF-"):
        raise ValueError("INVALID_OR_OVERSIZED_PDF")
    return data


def allowed_pdf(page_url, href, *, host):
    url = urljoin(page_url, href)
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != host or parts.username or parts.password
            or parts.port not in (None, 443) or not parts.path.lower().endswith(".pdf")):
        return None
    return quote(url.split("#", 1)[0], safe=":/%?=&+@,;~-._")


def collect_public_pdfs(store, campaign_id, *, fetch_page=fetch_index, fetch_pdf=download_pdf,
                        limit=3, sources=None):
    """Read detail pages with explicit rights text; leave uncertain sources untouched."""
    downloaded = 0
    policies = {s['id']: s for s in (configured_sources() if sources is None else sources)
                if s.get('rights_markers')}
    candidates = store.get(campaign_id)["source_candidates"]
    for candidate in candidates:
        policy = policies.get(candidate['source_id'])
        if (policy is None or candidate["status"] != "link_only"
                or downloaded >= limit):
            continue
        try:
            origin = urlsplit(candidate["url"])
            if (origin.scheme != "https" or origin.hostname != policy['host'] or origin.username
                    or origin.password or origin.port not in (None, 443)
                    or origin.path.lower().removesuffix(".aspx") != policy['path'].lower().removesuffix('.aspx')):
                raise ValueError("UNTRUSTED_DETAIL_URL")
            page = fetch_page(candidate["url"])
            content = PageText()
            content.feed(page)
            text = " ".join(" ".join(content.parts).split())
            if not all(s in text for s in policy['rights_markers']):
                store.event(campaign_id, "collect", "rights_unconfirmed")
                continue
            parser = Links()
            parser.feed(page)
            urls = list(dict.fromkeys(u for href, _ in parser.links
                                      if (u := allowed_pdf(candidate["url"], href, host=policy['host']))))
            if not urls:
                store.event(campaign_id, "collect", "pdf_link_unavailable")
                continue
            data = fetch_pdf(urls[0])
            if len(data) > MAX_BYTES or not data.startswith(b"%PDF-"):
                raise ValueError("INVALID_OR_OVERSIZED_PDF")
            with fitz.open(stream=data, filetype="pdf") as pdf:
                if pdf.needs_pass or len(pdf) < 1:
                    raise ValueError("INVALID_PDF")
            sha = hashlib.sha256(data).hexdigest()
            directory = store.root / "public-sources" / sha
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "source.pdf"
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise ValueError("SOURCE_CONFLICT")
            if not path.exists():
                with path.open("xb") as f:
                    f.write(data)
            metadata = {"title": candidate["title"], "organization": candidate["organization"],
                        "page_url": candidate["url"], "pdf_url": urls[0], "sha256": sha,
                        "rights_observation": list(policy['rights_markers']),
                        "rights_page_sha256": hashlib.sha256(page.encode("utf8")).hexdigest(),
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "dataset_role": "development"}
            # Unique source identity and provenance are stored together for audit.
            sample_id = store.add_public_sample(campaign_id, path, candidate["id"], metadata)
            if sample_id:
                downloaded += 1
                store.event(campaign_id, "collect", "public_pdf_registered", sample_id)
        except (OSError, ValueError, sqlite3.Error, RuntimeError, fitz.FileDataError):
            store.event(campaign_id, "collect", "public_pdf_failed")
    return downloaded
