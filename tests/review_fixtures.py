"""tests / review_fixtures."""

import fitz
import json
import pytest
from doc_understanding.review import error_basket


@pytest.fixture
def saved(tmp_path):
    folder = tmp_path / "job"
    folder.mkdir()
    with fitz.open() as pdf:
        pdf.new_page(width=200, height=200).insert_text((20, 40), "Source 17")
        pdf.save(folder / "input.pdf")
    bbox = dict(x0=10, y0=10, x1=180, y1=100)
    table = dict(
        table_id="T",
        page_number=1,
        bounding_box=bbox,
        cells=[dict(cell_id="C", source_bbox=bbox, original_text="17", span_refs=["S"])],
    )
    doc = dict(
        document_id="D",
        physical_tables=[table],
        table_structure=[
            dict(table_id="T", cell_mappings=[dict(cell_id="C", row_path=["R"], column_path=[])])
        ],
    )
    (folder / "document.json").write_text(json.dumps(doc), encoding="utf8")
    (folder / "nested.json").write_text('{"cells":[]}', encoding="utf8")
    job = dict(id="a" * 32, name="test document", pages=1, status="done", review=None)
    request = error_basket.CollectionRequest(
        page=1,
        scope="cell",
        table_id="T",
        cell_id="C",
        error_type="column_path",
        note="Header is prose",
    )
    return tmp_path / "basket", folder, job, request


@pytest.fixture
def source(saved):
    root, folder, job, req = saved
    folder.rename(folder.parent / job["id"])
    folder = folder.parent / job["id"]
    doc = json.loads((folder / "document.json").read_text(encoding="utf8"))
    doc["document_ir"] = {
        "pages": [
            dict(
                page_number=1,
                width=200,
                height=200,
                tables=[],
                blocks=[
                    dict(
                        spans=[
                            dict(
                                span_id="S", text_raw="17", bbox=dict(x0=10, y0=10, x1=180, y1=100)
                            )
                        ]
                    )
                ],
            )
        ]
    }
    doc["table_structure"][0].update(row_tree=[], column_tree=[])
    (folder / "document.json").write_text(json.dumps(doc), encoding="utf8")
    item = error_basket.collect(folder.parent / "error-basket", folder, job, req)
    record = error_basket.detail(folder.parent / "error-basket", item["id"])
    return folder.parent, record


def analyst_outputs():
    issue = dict(
        issue_id="I1",
        stage="axis_role",
        target_ids=["C"],
        observation="헤더 근거 부족",
        proposal="원본 확인",
        relation="none",
        source_id=None,
        target_id=None,
        citations=[dict(source_id="C", quote="17")],
        certainty="supported_candidate",
    )
    a = dict(summary="검토 후보", issues=[issue], missing_evidence=[])
    j = dict(
        summary="검수 의견",
        assessments=[
            dict(
                issue_id="I1",
                verdict="agree",
                reason="원본 대조 후보",
                citations=[dict(source_id="C", quote="17")],
            )
        ],
        additional_concerns=[],
        missing_evidence=[],
    )
    return a, j


def fake_transport(counter, answers=None):
    answers = answers or analyst_outputs()

    def send(payload, key, timeout):
        counter.append(payload)
        answer = answers[0 if payload["text"]["format"]["name"] == "review_analyst" else 1]
        return dict(
            status="completed",
            usage=dict(input_tokens=100, output_tokens=20),
            output=[dict(content=[dict(type="output_text", text=json.dumps(answer))])],
        )

    return send
