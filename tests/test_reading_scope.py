"""읽기 범위 넓히기: 선별된 칸이 속한 표를 통째로 근거에 넣는다. 값은 저장된 출처 그대로."""
import pytest

from doc_understanding.rag.agentic import reading
from doc_understanding.rag.agentic.reading import expand_units


@pytest.fixture(autouse=True)
def plain_sources(monkeypatch):
    # 이 파일의 가짜 출처는 이미 출처 모양이다(문서를 출처로 감싸는 단계는 건너뛴다).
    monkeypatch.setattr(reading, "as_source", lambda value: value)


class Source:
    """저장된 근거 출처 흉내: 표 ID → 칸 목록."""

    def __init__(self, tables, nested=None):
        self.tables, self.nested = tables, nested or {}

    def cells(self, table_id):
        return [dict(c) for c in self.tables[table_id]]

    def nested_context(self, table_id):
        return self.nested.get(table_id)


def cell(table, number, eligible=True, text=None):
    return dict(table_id=table, cell_id=f"{table}/c{number}", value_raw=text or f"값{number}", row_path=[f"행{number}"],
                column_path=["내용"], lookup_eligible=eligible, page=1)


def picked(item, **extra):
    return dict(item, run_id="r1", **extra)


def test_selected_cells_bring_in_every_row_of_their_table_in_table_order():
    table = [cell("T", n) for n in range(1, 13)]
    evidence = [picked(table[6], evidence_id="E1"), picked(table[0], evidence_id="E2"),
                dict(evidence_id="E3", run_id="r1", content_id="b1", value_raw="본문", page=1)]
    out, record = expand_units(evidence, load=lambda rid: Source(dict(T=table)))
    assert [e["cell_id"] for e in out[:12]] == [c["cell_id"] for c in table]      # 12행 전부, 표 순서
    assert out[12]["content_id"] == "b1"                                           # 본문 근거는 그대로
    assert [e["evidence_id"] for e in out] == [f"E{i}" for i in range(1, 14)]      # 다시 번호
    assert all(e["run_id"] == "r1" for e in out)
    assert record["tables"] == [dict(run_id="r1", table_id="T", cells=12, selected=2, expanded=True, reason=None, withheld=0)]
    assert record["before"] == 3 and record["after"] == 13
    assert evidence[0]["evidence_id"] == "E1"                                      # 입력은 바꾸지 않는다


def test_values_are_restored_as_stored_and_ineligible_cells_are_not_added():
    table = [cell("T", 1), cell("T", 2, eligible=False), cell("T", 3, text="1,234")]
    out, _ = expand_units([picked(table[0], evidence_id="E1")], load=lambda rid: Source(dict(T=table)))
    assert [e["cell_id"] for e in out] == ["T/c1", "T/c3"] and out[1]["value_raw"] == "1,234"
    # 근거로 쓸 수 없어 빠진 칸은 수를 남긴다(전체를 읽었다고 하지 않기 위해). 내용을 담는 칸(중복)은 세지 않는다.
    table.append(dict(cell("T", 4, eligible=False), container_duplicate=True))
    _, record = expand_units([picked(table[0], evidence_id="E1")], load=lambda rid: Source(dict(T=table)))
    assert record["tables"][0]["withheld"] == 1 and record["tables"][0]["expanded"] is True


def test_a_table_too_large_is_left_as_selected_instead_of_being_cut():
    table = [cell("T", n) for n in range(1, 31)]
    evidence = [picked(table[3], evidence_id="E1")]
    out, record = expand_units(evidence, load=lambda rid: Source(dict(T=table)), max_cells_per_table=20)
    assert [e["cell_id"] for e in out] == ["T/c4"]
    assert record["tables"][0]["expanded"] is False and record["tables"][0]["reason"] == "TABLE_TOO_LARGE"


def test_budget_expands_earlier_tables_first_and_never_partially():
    first = [cell("A", n, text="가" * 30) for n in range(1, 6)]
    second = [cell("B", n, text="나" * 30) for n in range(1, 6)]
    evidence = [picked(first[0], evidence_id="E1"), picked(second[0], evidence_id="E2")]
    out, record = expand_units(evidence, load=lambda rid: Source(dict(A=first, B=second)), max_bytes=900)
    assert [t["expanded"] for t in record["tables"]] == [True, False] and record["tables"][1]["reason"] == "BUDGET"
    assert [e["cell_id"] for e in out] == [c["cell_id"] for c in first] + ["B/c1"]


def test_nested_table_cells_keep_their_container_context_and_each_run_is_read_once():
    table = [cell("T", n) for n in range(1, 4)]
    loads = []

    def load(rid):
        loads.append(rid)
        return Source(dict(T=table, U=[cell("U", 1)]), nested=dict(T=dict(path=[dict(label="담은 칸")])))

    out, _ = expand_units([picked(table[0], evidence_id="E1"), picked(cell("U", 1), evidence_id="E2")], load=load)
    assert loads == ["r1"]
    assert out[1]["nested_context"] == dict(path=[dict(label="담은 칸")]) and "nested_context" not in out[3]
