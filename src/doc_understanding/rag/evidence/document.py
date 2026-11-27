# 역할: 문서를 가리키는 이름(파일 이름 + 첫 쪽 제목)을 원문에서 뽑는다. 검색 항목과 재정렬 요약이 함께 쓴다.
"""문서 이름표.

파일 이름만으로는 어느 문서인지 알 수 없는 경우가 많다(input.pdf, N11.pdf). 질문은 보통 기관·상품·보고서 이름으로
문서를 가리키므로, 첫 쪽에서 제목으로 보이는 글을 함께 둔다. 글의 뜻은 보지 않고 모양만 쓴다:
- 첫 쪽에서 글자가 가장 큰 글(제목)
- 첫 쪽의 첫 글(발행 기관·간행물 이름이 자주 놓이는 자리)
원문을 그대로 옮긴 검색 보조 정보이며, 기관·상품을 확정한 사실이 아니다.
"""

MAX_PART_CHARS = 80
MAX_TITLE_PARTS = 3


def document_title(doc):
    """첫 쪽의 제목 후보 글(원문, 공백 정리, 최대 3개). 글자가 없는 문서(스캔)는 빈 목록."""
    pages = doc.document_ir.pages
    if not pages:
        return []
    blocks = []
    for block in pages[0].blocks:
        text = " ".join((block.text_raw or "").split())
        if text and block.spans:
            blocks.append((max((s.font_size or 0) for s in block.spans), text[:MAX_PART_CHARS]))
    if not blocks:
        return []
    largest = max(size for size, _ in blocks)
    # 제목(가장 큰 글자, 앞에서 두 개까지) 다음에 첫 글을 둔다. 글자 크기가 모두 같으면 앞의 글들이 된다.
    parts = [text for size, text in blocks if size == largest][:2] + [blocks[0][1]]
    return list(dict.fromkeys(parts))[:MAX_TITLE_PARTS]


def document_label(doc):
    """파일 이름과 제목 후보를 이은 한 줄. 검색 항목 맨 앞과 재정렬 후보의 문서 표시에 쓴다."""
    return " | ".join([doc.document_ir.source_name, *document_title(doc)])
