from pathlib import Path

import pymupdf
import pytest

from doc_understanding.parser.assembly.document import build_from_run
from doc_understanding.parser.assembly.continued_sections import coalesce_reprints
from doc_understanding.parser.pipeline import prepare


@pytest.mark.parametrize("heading,intervening,y,merged", [
    ("2. 거래조건(계속)", None, 60, True),
    ("3. 거래조건(계속)", None, 60, False),
    ("2. 다른조건(계속)", None, 60, False),
    ("2. 거래 조건", None, 60, False),
    ("2. 거래조건(계속)", "3. 부록", 90, False),
    ("2. 거래조건(계속)", None, 420, False),
])
def test_reprinted_section_requires_explicit_title_and_page_edge(tmp_path: Path, heading, intervening, y, merged):
    pdf = tmp_path / "terms.pdf"
    with pymupdf.open() as raw:
        first = raw.new_page()
        first.insert_text((40, 110), "2. 거래 조건", fontname="korea")
        first.insert_text((40, 130), "첫 문장", fontname="korea")
        second = raw.new_page()
        if intervening:
            second.insert_text((40, 40), intervening, fontname="korea")
        second.insert_text((40, y), heading, fontname="korea")
        second.insert_text((40, y+24), "다음 문장", fontname="korea")
        raw.save(pdf)
    run,_ = prepare(pdf,tmp_path)
    doc = build_from_run(run)
    first = doc.sections[0]
    last = doc.contents[-1]
    assert (first.section_id == last.section_id) == merged
    assert (len(first.source_references) > 1) == merged
    assert coalesce_reprints(doc) is doc


def test_article_requires_support_and_form_revision_is_not_a_title(tmp_path):
    pdf = tmp_path / "article.pdf"
    with pymupdf.open() as raw:
        page = raw.new_page()
        for y, line in enumerate(("제1조 거래조건", "거래조건은 다음과 같습니다.", "기타 문장",
                                  "제2조 참고문헌", "관련 없는 설명", "5-16-0152(3-1) (2024.09 개정)"), 1):
            page.insert_text((40, 50 + y * 25), line, fontname="korea")
        raw.save(pdf)
    run, _ = prepare(pdf, tmp_path)
    doc = build_from_run(run)
    section, = doc.sections
    assert section.title_raw.strip() == "제1조 거래조건"
    assert len(section.content_ids) == 2


def test_marked_headings_require_peer_and_only_claim_adjacent_table():
    from doc_understanding.parser.assembly.local_headings import recover_unassigned_headings
    from doc_understanding.domain.pipeline_contracts import Content
    from doc_understanding.domain.models import BBox, SourceRef

    def item(i, kind, text, page=1):
        box = BBox(x0=20, x1=100, y0=20*i, y1=20*i+10)
        ref = SourceRef(document_id="test", page_number=page, bbox=box)
        return Content(content_id=f"c{i}", kind=kind, text_raw=text, reading_order=i, section_id=None,
                       table_id=f"t{i}" if kind=="table" else None, source_references=(ref,))
    contents = [item(0,"paragraph","■ 기본 정보"), item(1,"table",None), item(2,"table",None),
                item(3,"paragraph","■ 유의 사항"), item(4,"table",None),
                item(5,"paragraph","■ 다른 항목",2), item(6,"table",None,2)]
    result, sections = recover_unassigned_headings(contents)
    assert len(sections) == 2
    assert [c.section_id for c in result] == ["c0/section","c0/section",None,"c3/section","c3/section",None,None]
