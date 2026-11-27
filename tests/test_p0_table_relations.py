"""Regressions for panel-local interval lookups and named duration axes."""
import json

import pytest

from doc_understanding.parser.structure.analyzer import SemanticTableAnalyzer
from doc_understanding.rag.agentic.grid import GridAnswer, check, read_tables
from doc_understanding.rag.evidence.periods import mismatched_duration_axis, period_conflict, physical_interval
from doc_understanding.rag.generation.answer import generate
from doc_understanding.rag.service import generate_answer
from tests.rag_assessment_helpers import receipt
from doc_understanding.domain.models import BBox, SourceRef
from doc_understanding.parser.physical.physical_models import GridBand, PhysicalCell
from tests.semantic_fixtures import compound


@pytest.fixture
def early():
    # Original row/column text from the F01 audit. Coordinates and IDs are local to the test.
    matrix = [
        ["경과기간", "중도해지이율", "경과기간", "중도해지이율"],
        ["7일 미만", "무이자", "1개월 미만", "약정이율 1/10"],
        ["3개월 미만", "약정이율 3/10", "6개월 미만", "약정이율 4/10"],
        ["12개월 미만", "약정이율 6/10", "", ""],
    ]
    bands = tuple(GridBand(index=i, start=i, end=i+1) for i in range(4))
    cells = []
    for r, row in enumerate(matrix):
        for c, value in enumerate(row):
            box = BBox(x0=c, y0=r, x1=c+1, y1=r+1)
            cid = f"r{r}c{c}"
            cells.append(PhysicalCell(cell_id=cid, row_index=r, column_index=c, rowspan=1, colspan=1,
                                      original_text=value, source_bbox=box, extraction_method="vector_grid",
                                      source=SourceRef(document_id="F01", page_number=1, bbox=box, cell_id=cid),
                                      status="resolved", occupied_slots=((r, c),)))
    physical = compound()[0].model_copy(update=dict(document_id="F01", table_id="early", rows=bands,
                                                     columns=bands, cells=tuple(cells)))
    cells = [dict(id=c.cell_id, r=c.row_index, c=c.column_index, rs=c.rowspan,
                  cs=c.colspan, text=c.original_text) for c in physical.cells]
    return physical, dict(table_id=physical.table_id, rows=len(physical.rows), columns=len(physical.columns), cells=cells)


@pytest.mark.parametrize("point,row,expected,old_row,old_value", [
    pytest.param("20일", 1, "약정이율 1/10", 2, "약정이율 3/10", id="h-03"),
    pytest.param("5개월", 2, "약정이율 4/10", 3, "약정이율 6/10", id="h-04"),
])
def test_interval_keeps_the_qualifying_row_header_and_value_together(early, point, row, expected, old_row, old_value, tmp_path):
    physical, grid = early
    question = f"하나은행 외화고단위플러스 정기예금을 {point} 만에 중도해지하면 중도해지이율은?"
    semantic = SemanticTableAnalyzer().analyze(physical, header_rows=1, stub_columns=1)
    relation = physical_interval(question, grid)
    mapped = next(m for m in semantic.cell_mappings if m.cell_id == relation[2])
    nodes = {n.node_id: n for n in semantic.row_tree}
    assert nodes[mapped.row_path[-1]].label_raw.strip() == ("1개월 미만" if row == 1 else "6개월 미만")
    stale = dict(run_id="source", table_id=physical.table_id, row_path=["3개월 미만"],
                 table_context=["경과기간", "중도해지이율"] * 2, value_raw=old_value)
    held = generate_answer(question, [stale], tmp_path)
    assert held["selection"]["abstained"] and held["interval_tables"] == [("source", physical.table_id)]

    def validate(value_row, col, label_col, text):
        parsed = GridAnswer(kind="answered", answer_cells=[f"T0:r{value_row}c{col}"],
                            row_label_cells=[f"T0:r{value_row}c{label_col}"], column_label_cells=[f"T0:r0c{col}"],
                            answer_text=text, explanation="")
        return check(parsed, [(0, grid)], {}, question, requirements=[f"{point} 만에 중도해지하면", "중도해지이율"])

    # These two questions have a uniquely supported source value and require no answer-model call.
    verdict, cited, trace = read_tables(question, held["interval_tables"], lambda *_: dict(grid, page=1), None,
                                        None, tmp_path, interval_required=True)
    assert verdict["answer_text"] == expected and verdict["answer_text_source"] == "source_cells"
    assert cited[0]["cell_id"] == relation[2] and trace["call"] is None
    assert validate(row, 3, 2, expected)["passed"]
    assert "PHYSICAL_INTERVAL_NOT_SUPPORTED" in validate(old_row, 1, 0, old_value)["reasons"]
    assert "ROW_LABEL_IN_OTHER_PANEL" in validate(row, 3, 0, expected)["reasons"]


@pytest.mark.parametrize("point", ["29일", "12개월", "1개월 미만", "2개월 또는 5개월", "1년 5개월", "1~5개월"])
def test_ambiguous_calendar_boundary_or_non_point_is_withheld(early, point):
    assert physical_interval(point, early[1]) == []


def test_duplicate_cutoff_or_missing_cell_is_withheld(early):
    _, grid = early
    next(c for c in grid["cells"] if c["r"] == 2 and c["c"] == 2)["text"] = "3개월 미만"
    assert physical_interval("5개월", grid) == []
    assert physical_interval("20일", dict(grid, cells=grid["cells"][:-1])) == []


def test_g21_supported_model_response_from_wrong_duration_column_is_withheld(tmp_path):
    question = "카카오뱅크 정기예금과 신한 My플러스 정기예금 중 가입기간을 더 길게 정할 수 있는 상품은 무엇인가요?"
    def evidence(i, raw, row, column, headers):
        return dict(evidence_id=f"E{i}", run_id=str(i), table_id=f"t{i}", cell_id=f"c{i}", page=1,
                    source_name=str(i), lookup_eligible=True, value_raw=raw, row_path=[row],
                    column_path=column, table_context=headers)
    first = evidence(1, "1개월 이상 36개월 이하", "가입기간", [], [])
    other = evidence(2, "3개월", "6개월, 12개월", ["최소 실적 충족 개월 수"], ["가입기간", "최소 실적 충족 개월 수"])
    def transport(payload, key, timeout):
        data = json.loads(payload["input"][1]["content"])
        selected = [s for s in data["sources"] if s["source_id"] in ("E1", "E2")]
        draft = dict(evidence_ids=["E1", "E2"], values_raw=[first["value_raw"], other["value_raw"]], abstained=False,
                     answer_text="1개월 이상 36개월 이하, 3개월", alternatives=[], assessment=receipt(question, selected))
        return dict(status="completed", model="mock", usage={}, output=[dict(type="message", content=[
            dict(type="output_text", text=json.dumps(draft))])])
    def run(folder):
        return generate(question, [first, other], tmp_path / folder, allow_external=True, transport=transport,
                        input_usd_per_million=1, output_usd_per_million=1, presentation=True)
    assert period_conflict(question, [first], [first, other]) == "COMPARISON_DURATION_MISSING"
    held = run("wrong")
    assert held["assessment_validation"]["reasons"] == ["REQUESTED_AXIS_NOT_CITED"]
    assert held["selection"]["abstained"] and not held["answer_text"] and "3개월" not in held["answer"]
    assert json.loads((tmp_path / "wrong/answer.json").read_bytes())["selection"] == held["selection"]
    other.update(row_path=["가입기간"], column_path=[], table_context=[], value_raw="12개월")
    assert not run("contract")["selection"]["abstained"]


def test_another_duration_table_checks_the_requested_axis_even_with_one_source():
    cell = dict(table_id="warranty", table_context=["보증기간", "검사 주기"], column_path=["검사 주기"],
                row_path=["제품 A"], value_raw="3개월")
    assert mismatched_duration_axis("제품 A의 보증기간은?", [cell])
    assert not mismatched_duration_axis("제품 A의 검사 주기는?", [cell])
    assert mismatched_duration_axis("계약기간은?", [dict(cell, table_context=["가입기간", "최소 실적 충족 개월 수"],
                                                       column_path=["최소 실적 충족 개월 수"])])

@pytest.mark.parametrize("question,correct,adjacent,value", [
    ("계약기간을 500일로 하면 적용 이자율은 얼마인가요?", "367일 이상 ~ 555일 미만", "274일 이상 ~ 367일 미만", "1.25"),
    ("고정금리형을 7개월 예치하고 중도해지하면 이율은 어떻게 계산되나요?", "6개월이상~8개월미만", "8개월이상~10개월미만", "만기이율 X 60%"),
])
def test_kb_development_document_rejects_adjacent_duration_band(question, correct, adjacent, value):
    evidence = dict(table_id="kb", column_path=["이율"], value_raw=value, row_path=[correct])
    assert period_conflict(question, [evidence]) is None
    assert period_conflict(question, [dict(evidence, row_path=[adjacent])]) == "REQUESTED_INTERVAL_NOT_CITED"

def test_g21_grid_comparison_requires_duration_evidence_for_both_documents():
    def table(run_id, value):
        cells = [dict(id=run_id+str(i), r=i//2, c=i%2, rs=1, cs=1, text=text)
                 for i, text in enumerate(["상품", "가입기간", run_id, value])]
        return dict(run_id=run_id, table_id=run_id, cells=cells)
    grids = [(0, table("하나", "36개월")), (1, table("둘", "12개월"))]
    parsed = GridAnswer(kind="answered", answer_cells=["T0:r1c1"], row_label_cells=["T0:r1c0"],
                        column_label_cells=["T0:r0c1"], answer_text="36개월", explanation="")
    checked = check(parsed, grids, {}, "두 상품 중 가입기간을 더 길게 정할 수 있는 상품은?")
    assert "COMPARISON_DURATION_MISSING" in checked["reasons"]
