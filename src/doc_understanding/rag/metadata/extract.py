# 역할: 문서 앞부분에서 메타데이터(기관·문서 이름·종류·기준 시점)를 모델로 뽑고, 인용이 원문에 있는 것만 남긴다.
"""문서 메타데이터 추출.

문서마다 한 번 실행한다. 모델에는 앞쪽 두 쪽의 글과 파일 이름만 준다. 모델이 낸 값은 그대로 믿지 않는다:
기관·문서 이름·종류·기준 시점은 인용 문구가 그 쪽 원문에 있고 값이 그 인용의 일부일 때만 남긴다(지어낸 이름을 막는다).
다른 표기(aliases)와 한 줄 설명은 원문에 없을 수 있어 '확인되지 않음'으로 따로 표시한다.
결과는 사람 검증 전 후보이며, 화면에서 고친 값이 이 값보다 우선한다(rag.metadata.store).
"""
import hashlib
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from doc_understanding.rag.prompts.metadata import SYSTEM
from doc_understanding.runtime.structured_call import structured_call

PAGES = 2
PAGE_CHARS = 4000
CITED = ("organization", "title", "document_type", "as_of")
PROMPT_SHA256 = hashlib.sha256(SYSTEM.encode("utf8")).hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Cited(Strict):
    value: str = Field(max_length=200)
    quote: str = Field(max_length=300)
    page: int = Field(ge=0, le=PAGES)


class Extracted(Strict):
    organization: Cited
    title: Cited
    document_type: Cited
    as_of: Cited
    aliases: list[str] = Field(max_length=6)
    summary: str = Field(max_length=300)


def norm(text):
    """비교용: 전각·반각을 맞추고 공백을 없애고 대소문자를 무시한다."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).casefold()


def first_pages(doc, pages=PAGES, limit=PAGE_CHARS):
    """앞쪽 pages개 쪽의 글(쪽 번호 → 글). 글자가 없는 쪽은 빈 문자열."""
    out = {}
    for page in doc.document_ir.pages[:pages]:
        text = "\n".join((block.text_raw or "").strip() for block in page.blocks if (block.text_raw or "").strip())
        out[page.page_number] = text[:limit]
    return out


def check(raw, pages):
    """모델 응답을 원문과 대조한다. 반환: 남긴 항목과 버린 이유.

    - 인용이 어느 쪽 원문에도 없으면 버린다(QUOTE_NOT_IN_SOURCE). 쪽 번호만 틀렸으면 실제 쪽으로 고친다.
    - 값이 인용의 일부가 아니면 버린다(VALUE_NOT_IN_QUOTE).
    """
    text = {number: norm(body) for number, body in pages.items()}
    fields, dropped = {}, []
    for name in CITED:
        item = getattr(raw, name)
        value, quote = item.value.strip(), item.quote.strip()
        if not value:
            continue
        if not quote or norm(value) not in norm(quote):
            dropped.append(dict(field=name, reason="VALUE_NOT_IN_QUOTE"))
            continue
        found = [number for number, body in text.items() if norm(quote) in body]
        if not found:
            dropped.append(dict(field=name, reason="QUOTE_NOT_IN_SOURCE"))
            continue
        fields[name] = dict(value=value, quote=quote, page=item.page if item.page in found else found[0])
    everything = "".join(text.values())
    organization = norm((fields.get("organization") or {}).get("value"))
    aliases, seen = [], {organization}
    for alias in raw.aliases:
        alias = " ".join(alias.split())[:60]
        if alias and norm(alias) not in seen:
            seen.add(norm(alias))
            # 원문에 나오는 표기인지 표시한다. 나오지 않는 표기는 모델의 추정이라 약한 근거로만 쓴다.
            aliases.append(dict(text=alias, in_source=norm(alias) in everything))
    return dict(fields=fields, dropped=dropped, aliases=aliases, summary=" ".join(raw.summary.split()))


def extract(doc, policy, output, *, allow_external=False, ledger=None, transport=None):
    """문서 한 건의 메타데이터를 뽑는다. 반환값은 저장용 사전(store.put_extracted)과 호출 기록.

    글자가 없는 문서(스캔)는 모델을 부르지 않고 빈 결과를 낸다.
    """
    pages = first_pages(doc)
    base = dict(source_name=doc.document_ir.source_name, pages_read=sorted(pages), prompt_sha256=PROMPT_SHA256)
    if not any(pages.values()):
        return dict(base, status="no_text", fields={}, dropped=[], aliases=[], summary=""), None
    user = dict(file_name=doc.document_ir.source_name,
                pages=[dict(page=number, text=body) for number, body in pages.items()])
    kwargs = dict(allow_external=allow_external, ledger=ledger)
    if transport:
        kwargs["transport"] = transport
    raw, record = structured_call("document_metadata", SYSTEM, user, Extracted, policy, output, **kwargs)
    checked = check(raw, pages)
    status = "extracted" if checked["fields"] else "empty"
    return dict(base, status=status, model=policy.model, **checked), record
