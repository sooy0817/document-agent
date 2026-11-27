"""문서 메타데이터: 인용이 원문에 있는 값만 남기고, 사람이 고친 값이 모델 값보다 우선한다."""
import json
from types import SimpleNamespace

import pytest

from doc_understanding.rag.metadata import extract as extraction
from doc_understanding.rag.metadata import store
from doc_understanding.rag.metadata.extract import Cited, Extracted, check, first_pages
from doc_understanding.runtime.structured_call import CallPolicy

PAGES = {1: "카카오뱅크 정기예금 상품설명서 (요약)\n(2025.11.12. 현재 기준, 세금공제 전)\n가입대상 만14 세 이상의 실명의 개인",
         2: "예금보험공사 보호금융상품 1인당 최고 1억원"}
EMPTY = dict(value="", quote="", page=0)


def raw(**fields):
    base = dict(organization=EMPTY, title=EMPTY, document_type=EMPTY, as_of=EMPTY, aliases=[], summary="")
    return Extracted.model_validate({**base, **fields})


def document(pages, name="input.pdf"):
    return SimpleNamespace(document_id="sha256:doc", document_ir=SimpleNamespace(source_name=name, pages=[
        SimpleNamespace(page_number=i, blocks=[SimpleNamespace(text_raw=line) for line in text.split("\n")])
        for i, text in pages.items()]))


def test_only_values_quoted_from_the_pages_are_kept():
    result = check(raw(
        organization=dict(value="카카오뱅크", quote="카카오뱅크 정기예금 상품설명서 (요약)", page=1),
        title=dict(value="카카오뱅크 정기예금", quote="카카오뱅크  정기예금 상품설명서", page=2),   # 공백 차이·쪽 번호 오류
        document_type=dict(value="약관", quote="카카오뱅크 정기예금 상품설명서 (요약)", page=1),      # 값이 인용에 없음
        as_of=dict(value="2024.01.01", quote="2024.01.01 현재", page=1),                              # 인용이 원문에 없음
        aliases=["kakaobank", "카카오뱅크", "예금보험공사", " "], summary=" 정기예금의  가입 조건 안내 "), PAGES)
    assert result["fields"] == {
        "organization": dict(value="카카오뱅크", quote="카카오뱅크 정기예금 상품설명서 (요약)", page=1),
        "title": dict(value="카카오뱅크 정기예금", quote="카카오뱅크  정기예금 상품설명서", page=1)}
    assert result["dropped"] == [dict(field="document_type", reason="VALUE_NOT_IN_QUOTE"),
                                 dict(field="as_of", reason="QUOTE_NOT_IN_SOURCE")]
    # 다른 표기: 기관 이름과 같은 것·빈 것은 빼고, 원문에 나오는 표기인지 표시한다.
    assert result["aliases"] == [dict(text="kakaobank", in_source=False), dict(text="예금보험공사", in_source=True)]
    assert result["summary"] == "정기예금의 가입 조건 안내"


def test_first_pages_reads_two_pages_and_bounds_their_length():
    doc = document({1: "가\n\n나", 2: "다", 3: "라"})
    assert first_pages(doc) == {1: "가\n나", 2: "다"}
    assert first_pages(document({1: "글" * 9000}))[1] == "글" * 4000


def test_extract_sends_only_the_first_pages_and_checks_the_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    seen = {}

    def transport(payload, key, timeout):
        seen["user"] = json.loads(payload["input"][1]["content"])
        reply = raw(organization=dict(value="카카오뱅크", quote="카카오뱅크 정기예금 상품설명서 (요약)", page=1),
                    title=dict(value="지어낸 이름", quote="지어낸 이름", page=1)).model_dump()
        return dict(status="completed", usage=dict(input_tokens=100, output_tokens=50),
                    output=[dict(type="message", content=[dict(type="output_text", text=json.dumps(reply))])])

    policy = CallPolicy(model="m", input_usd_per_million=1, output_usd_per_million=1)
    result, record = extraction.extract(document({**PAGES, 3: "셋째 쪽"}), policy, tmp_path / "call", allow_external=True,
                                        transport=transport)
    assert seen["user"]["file_name"] == "input.pdf" and [p["page"] for p in seen["user"]["pages"]] == [1, 2]
    assert result["status"] == "extracted" and list(result["fields"]) == ["organization"]
    assert result["dropped"] == [dict(field="title", reason="QUOTE_NOT_IN_SOURCE")]
    assert result["prompt_sha256"] == extraction.PROMPT_SHA256 and record["status"] == "completed"


def test_a_document_without_text_is_not_sent_to_the_model(tmp_path):
    policy = CallPolicy(model="m", input_usd_per_million=1, output_usd_per_million=1)
    result, record = extraction.extract(document({1: "", 2: " "}), policy, tmp_path / "call", allow_external=True,
                                        transport=lambda *a: pytest.fail("no call for a scanned document"))
    assert result["status"] == "no_text" and record is None and not (tmp_path / "call").exists()


def test_response_schema_is_strict_and_bounded():
    schema = Extracted.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    with pytest.raises(ValueError):
        Cited(value="x", quote="x", page=3)


def test_human_edits_win_and_survive_a_new_extraction():
    extracted = dict(status="extracted", summary="요약", dropped=[], fields={
        "organization": dict(value="예금보험공사", quote="예금보험공사 보호금융상품", page=2),
        "title": dict(value="정기예금", quote="정기예금 상품설명서", page=1)},
        aliases=[dict(text="KDIC", in_source=False), dict(text="예보", in_source=True)])
    merged = store.merge(extracted, {})
    assert merged["organization"] == "예금보험공사" and merged["origin"] == dict(organization="model", title="model")
    assert merged["evidence"]["title"] == dict(quote="정기예금 상품설명서", page=1) and not merged["needs_review"]
    assert merged["aliases"] == [dict(text="KDIC", origin="model_unverified"), dict(text="예보", origin="model")]
    edited = store.merge(extracted, dict(organization="카카오뱅크", as_of="", aliases=["한국카카오은행"]))
    assert edited["organization"] == "카카오뱅크" and edited["origin"]["organization"] == "human"
    assert edited["as_of"] == "" and edited["origin"]["as_of"] == "human" and "organization" not in edited["evidence"]
    assert edited["aliases"] == [dict(text="한국카카오은행", origin="human")] and edited["title"] == "정기예금"
    # 기관도 문서 이름도 없으면 가리킬 수 없는 문서다.
    blank = store.merge(dict(status="empty", fields={}), {})
    assert blank["needs_review"] and store.merge(None, None)["status"] == "missing"


def test_store_keeps_edits_apart_from_extraction(monkeypatch):
    table = {}

    statements = []

    def sql_params(statement, params):
        # DB 흉내: 추출은 덮어쓰고, 수정은 저장된 값에서 지울 항목을 빼고 바뀐 항목만 덮는다(문장 하나).
        statements.append(statement)
        row = table.setdefault(params[0], dict(document_id=params[0], extracted=None, edited={}))
        if "extracted" in statement.split("VALUES")[0]:
            row["extracted"] = json.loads(params[1])
        else:
            row["edited"] = {**{k: v for k, v in row["edited"].items() if k not in json.loads(params[2])},
                             **json.loads(params[1])}

    monkeypatch.setattr(store, "ensure", lambda: None)
    monkeypatch.setattr(store, "sql_params", sql_params)
    monkeypatch.setattr(store, "query_params", lambda statement, params: [table[d] for d in params[0] if d in table])
    store.put_extracted("d1", dict(status="extracted", prompt_sha256="p1", fields={
        "organization": dict(value="A은행", quote="A은행", page=1)}))
    store.put_edit("d1", dict(organization="B은행", aliases=["비 은행", "비 은행", ""], unknown="x"))
    store.put_extracted("d1", dict(status="extracted", prompt_sha256="p2", fields={
        "organization": dict(value="C은행", quote="C은행", page=1)}))
    view = store.view(["d1", "d2"])
    assert view["d1"]["organization"] == "B은행" and view["d1"]["aliases"] == [dict(text="비 은행", origin="human")]
    assert view["d2"]["status"] == "missing"
    assert store.extracted_with(["d1", "d2"], "p2") == {"d1"} and store.extracted_with(["d1"], "p1") == set()
    store.put_edit("d1", dict(organization=None))   # 수정을 지우면 모델 값으로 돌아간다
    assert store.view(["d1"])["d1"]["organization"] == "C은행"
    assert store.view(["d1"])["d1"]["aliases"] == [dict(text="비 은행", origin="human")]  # 다른 항목의 수정은 남는다
    # 수정은 읽고 고쳐 쓰지 않고 문장 하나로 합친다(동시에 다른 항목을 고쳐도 서로 지우지 않는다).
    edit_statement = statements[-1]
    assert "rag.document_metadata.edited" in edit_statement and "|| EXCLUDED.edited" in edit_statement
    assert edit_statement.count("SELECT") == 1 and "jsonb_array_elements_text" in edit_statement
