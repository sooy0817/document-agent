"""Join explicitly reprinted numbered headings while retaining each printed content line."""
import re
from collections import defaultdict


def coalesce_reprints(doc):
    sections = {s.section_id: s for s in doc.sections}
    headings = {s.section_id.removesuffix("/section"): s for s in sections.values()}
    heights = {p.page_number: p.height for p in doc.document_ir.pages}
    aliases, repeats = {}, defaultdict(list)
    previous_section = previous_page = None
    for item in sorted(doc.contents, key=lambda c: c.reading_order):
        if not item.source_references or (item.kind == "paragraph" and not (item.text_raw or "").strip()):
            continue
        ref = item.source_references[0]
        heading = headings.get(item.content_id)
        original = sections.get(previous_section)
        if (heading and original and heading.evidence_state == original.evidence_state == "inferred"
                and previous_page == ref.page_number - 1
                and item.kind == "paragraph" and not item.duplicate_of_table_ids
                and ref.bbox.y1 <= heights[ref.page_number] * .15
                and heading.section_id == item.section_id and heading.parent_id == original.parent_id):
            # A printed marker, matching number and title, and uninterrupted page edge
            # are all required. Unnumbered or independently repeated titles stay separate.
            match = re.fullmatch(r"\s*(\d+[.)]\s*.+?)\s*\(\s*계속\s*\)\s*", item.text_raw or "")
            if match and "".join(match[1].split()) == "".join(original.title_raw.split()):
                aliases[heading.section_id] = original.section_id
                repeats[original.section_id].extend(heading.source_references)
        previous_section = aliases.get(item.section_id, item.section_id)
        previous_page = ref.page_number
    if not aliases:
        return doc
    contents = tuple(c.model_copy(update={"section_id": aliases[c.section_id]})
                     if c.section_id in aliases else c for c in doc.contents)
    members = defaultdict(list)
    for c in contents:
        members[c.section_id].append(c.content_id)
    retained = tuple(s.model_copy(update={
        "parent_id": aliases.get(s.parent_id, s.parent_id),
        "content_ids": tuple(members[s.section_id]),
        "source_references": (*s.source_references, *repeats[s.section_id]),
    }) for s in doc.sections if s.section_id not in aliases)
    by_id = {c.content_id: c for c in contents}
    links = tuple(link.model_copy(update={"target_id": aliases[link.target_id]})
                  if link.target_id in aliases else link for link in doc.context_links)
    return doc.model_copy(update=dict(contents=contents, sections=retained, context_links=links,
                                      notes=tuple(by_id[n.content_id] for n in doc.notes)))
