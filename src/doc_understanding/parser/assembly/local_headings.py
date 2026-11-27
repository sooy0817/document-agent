"""Recover bounded, source-supported sections in otherwise unassigned content."""
from collections import defaultdict
import re


def recover_unassigned_headings(contents):
    visible = [i for i, c in enumerate(contents) if not c.duplicate_of_table_ids
               and (c.kind != "paragraph" or (c.text_raw or "").strip())]
    marked = defaultdict(list)
    supported = []
    for i, j in zip(visible, visible[1:]):
        title, body = contents[i], contents[j]
        if title.section_id or body.section_id or title.kind != "paragraph":
            continue
        a, b = title.source_references[0], body.source_references[0]
        if a.page_number != b.page_number or not -2 <= b.bbox.y0 - a.bbox.y1 <= 32:
            continue
        text = (title.text_raw or "").strip()
        marker = re.fullmatch(r"([■‣])\s*([가-힣]+(?: [가-힣]+){0,2})", text)
        if marker and body.kind == "table" and abs(a.bbox.x0 - b.bbox.x0) <= 32:
            marked[a.page_number, marker[1]].append((i, j))
        article = re.fullmatch(r"제\s*\d+\s*조\s+([가-힣]{2,12})", text)
        if (article and body.kind == "paragraph" and abs(a.bbox.x0 - b.bbox.x0) <= 24
                and re.match(re.escape(article[1]) + r"(?:은|는|이|가)\s", (body.text_raw or "").strip())):
            supported.append((i, j))
    for pairs in marked.values():
        if len({contents[i].text_raw.strip() for i, _ in pairs}) >= 2:
            supported.extend(pairs)
    specs = []
    for i, j in supported:
        title = contents[i]
        sid = title.content_id + "/section"
        specs.append(dict(section_id=sid, parent_id=None, title_raw=title.text_raw,
                          source_references=title.source_references, reading_order=title.reading_order))
        for k in (i, j):
            contents[k] = contents[k].model_copy(update={"section_id": sid})
    return contents, specs
