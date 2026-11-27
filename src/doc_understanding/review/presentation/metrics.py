"""공통 페이지 표시 결과에서 표 수·경로 연결 수를 집계한다. 정답 정확도 점수가 아니다."""
from doc_understanding.review.presentation.document import page_view


def page_metrics(document, nested):
    rows = []
    for page in document["document_ir"]["pages"]:
        number = page["page_number"]
        view = page_view(document, nested, number)
        tables = [t for t in document["physical_tables"] if t["page_number"] == number]
        ids = {c["cell_id"] for t in tables for c in t["cells"]}
        mappings = [
            m
            for t in document["table_structure"]
            for m in t["cell_mappings"]
            if m["cell_id"] in ids
        ]
        rows.append(
            dict(
                page=number,
                physical_cells=len(ids),
                displayed_nested_tables=view["nested_tables"],
                top_level_tables=view["top_level_tables"],
                unresolved_links=sum(
                    link.get("containment_status") != "linked"
                    for link in nested["cells"]
                    if link["page_number"] == number
                ),
                both_paths=sum(
                    bool(m.get("row_path")) and bool(m.get("column_path")) for m in mappings
                ),
                mapped_cells=len(mappings),
            )
        )
    return rows
