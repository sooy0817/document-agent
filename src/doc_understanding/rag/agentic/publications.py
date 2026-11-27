"""Compare alternate renderings of a publication, without changing stored evidence.

Fail closed: title/issuer alone never establish equivalence. An HTML article must
share the publisher, dated release header and every narrative passage with its PDF.
PDF-only appendices and page furniture are allowed; citations retain both originals.
"""
import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from doc_understanding.storage.staging.local import query_params

MAX_CONTENTS = 10000
MAX_CHARACTERS = 600000


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).casefold()


def load(run_id):
    # Project only the fields needed here; never hydrate tables, grids or the whole IR.
    rows = query_params("""SELECT a.payload->>'source_uri' AS uri,
      a.payload->'document_ir'->'source'->>'media_type' AS media_type,
      jsonb_path_query_array(a.payload, '$.contents[*].text_raw') AS texts,
      jsonb_path_query_array(a.payload, '$.contents[*].kind') AS kinds,
      jsonb_path_query_array(a.payload, '$.contents[*].content_id') AS ids,
      jsonb_path_query_array(a.payload, '$.sections[*].content_ids[0]') AS headings
      FROM staging.artifact a WHERE a.run_id=%s AND a.kind='CanonicalDocument'
      AND jsonb_array_length(a.payload->'contents') <= %s""", [run_id, MAX_CONTENTS])
    if len(rows) != 1:
        return None
    row = rows[0]
    row['texts'] = [t or '' for t in row['texts']]
    row['kinds'] = ['heading' if cid in row['headings'] else kind
                    for cid, kind in zip(row.pop('ids'), row['kinds'])]
    row.pop('headings')
    if sum(len(t or "") for t in row["texts"]) > MAX_CHARACTERS:
        return None
    return row


def passage_in(text, full, lines=()):
    """Exact text, allowing at most one PDF page/footnote interruption.

    This is not fuzzy matching: every character of the HTML passage must occur in
    order. Tiny fragments cannot bridge a changed number/word to a nearby match.
    """
    if text in full:
        return True
    # A wrapped table label can be interrupted by a neighbouring column. Both
    # fragments must be complete stored lines, not arbitrary matching substrings.
    for i, line in enumerate(lines):
        if len(line) >= 6 and text.startswith(line):
            if any(line + other == text for other in lines[i + 1:i + 4] if len(other) >= 6):
                return True
    if len(text) < 64:
        return False
    blocks = SequenceMatcher(None, text, full, autojunk=False).get_matching_blocks()[:-1]
    return (len(blocks) == 2 and sum(b.size for b in blocks) == len(text)
            and all(b.size >= 32 for b in blocks)
            and blocks[1].b - (blocks[0].b + blocks[0].size) <= 600)


def same_publication(left, right, loader=load):
    if not left.get("title") or not left.get("organization"):
        return False
    if any(compact(left.get(k)) != compact(right.get(k)) for k in ("title", "organization")):
        return False
    a, b = loader(left["run_id"]), loader(right["run_id"])
    if not a or not b or {a["media_type"], b["media_type"]} != {"application/pdf", "text/html"}:
        return False
    host = lambda row: (urlsplit(row.get("uri") or "").hostname or "").removeprefix("www.")
    if not host(a) or host(a) != host(b):
        return False
    pdf, html = (a, b) if a["media_type"] == "application/pdf" else (b, a)
    title = compact(left["title"])
    starts = [i for i, (t, k) in enumerate(zip(html["texts"], html["kinds"]))
              if k == "heading" and compact(t) == title]
    pdf_starts = [i for i, t in enumerate(pdf["texts"]) if compact(t) == title]
    if len(starts) != 1 or not pdf_starts:
        return False
    start, pdf_start = starts[0], pdf_starts[0]
    # A dated header must be identical, not an inferred reporting period (as_of).
    dated = lambda texts: {compact(t) for t in texts if re.search(r"\b(?:19|20)\d{2}\b", t or "")
                           and len(compact(t)) >= 20}
    if not (dated(html["texts"][max(0, start - 12):start])
            & dated(pdf["texts"][:pdf_start])):
        return False
    lines = [compact(t) for t in pdf["texts"][pdf_start:] if compact(t)]
    full = ''.join(lines)
    passages = []
    for t, kind in zip(html["texts"][start + 1:], html["kinds"][start + 1:]):
        # Attachment/navigation sections follow the article. Only explicit file
        # format labels establish this boundary; an unfamiliar content heading does not.
        if kind == "heading" and re.search(r"\b(?:PDF|XLSX?|CSV|DOCX?)\s*$", t or "", re.I):
            break
        if kind != "table" and compact(t):
            passages.append(compact(t))
    long = [t for t in passages if len(t) >= 60]
    return len(long) >= 3 and sum(map(len, long)) >= 500 and all(passage_in(t, full, lines) for t in passages)


def equivalent(documents):
    """A complete pairwise match, never a transitive similarity chain."""
    if not 1 < len(documents) <= 5:
        return False
    cache = {}
    def cached(run_id):
        if run_id not in cache:
            cache[run_id] = load(run_id)
        return cache[run_id]
    return all(same_publication(a, b, cached) for i, a in enumerate(documents) for b in documents[i + 1:])
