# 역할: 질문이 가리킨 대상(기관·상품·문서 이름)을 문서 메타데이터와 맞춰 검색할 문서를 정한다. 모델을 부르지 않는다.
"""문서 고르기.

질문 이해 단계가 질문에서 대상 문구를 그대로 뽑아 주면(plan.subjects), 여기서 그 문구를 문서의 이름들과 글자로 맞춘다.
같은 양식의 문서가 기관만 바꿔 여러 벌 있을 때, 전체에서 그냥 찾으면 다른 기관 문서의 값이 섞인다. 문서를 먼저 정하면
검색과 답변이 그 문서 안에서만 일어난다.

맞춤은 글자 비교뿐이다. 낱말의 뜻, 문서 종류, 특정 기관 이름에 따른 규칙은 없다.
- 강한 맞춤: 기관·문서 이름·표시 이름·파일 이름, 사람이 넣은 표기, 원문에 나오는 다른 표기가 대상의 모든 낱말을 덮는다.
- 약한 맞춤: 모델이 추정한 표기(원문에 없음)까지 써야 덮이거나, 긴 낱말이 한 글자만 다르다.
결과는 사람 검증을 거친 판단이 아니다. 화면은 고른 문서를 항상 보여 주고 사용자가 바꿀 수 있어야 한다.
"""
import re
import unicodedata

ASK_UP_TO = 5        # 같은 정도로 맞는 문서가 이 수 이하면 되묻고, 넘으면 그 문서들 안에서 찾는다.
SHORT = 2            # 이 길이 이하의 낱말은 이름 낱말과 같거나 그 앞·뒤에 붙어 있을 때만 맞은 것으로 본다(긴 낱말 한가운데에 우연히 든 경우는 제외).
TYPO_MIN = 5         # 한 글자 차이를 오타로 보는 최소 길이. 짧은 이름은 한 글자 차이가 다른 대상인 경우가 많다.
STRONG_ORIGINS = ("human", "model")


def norm(text):
    """비교용: 전각·반각 통일, 대소문자 무시, 글자·숫자만 남긴다(공백·괄호·기호 제거)."""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text or "").casefold())


def words(text):
    """글자·숫자가 이어진 덩어리들(비교용으로 정리한 것). 빈 것은 뺀다."""
    return [w for w in (norm(part) for part in re.split(r"[\s\W_]+", unicodedata.normalize("NFKC", text or ""))) if w]


def names_of(document):
    """문서를 가리킬 수 있는 이름들: (글, 강한 근거인가)."""
    strong = [document.get("organization"), document.get("title"), document.get("name"), document.get("source_name")]
    out = [(text, True) for text in strong if text]
    for alias in document.get("aliases") or []:
        if alias.get("text"):
            out.append((alias["text"], alias.get("origin") in STRONG_ORIGINS))
    return out


def within_one_edit(a, b):
    """두 글이 한 글자 고치기·넣기·빼기 이내인가(같으면 거짓)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:] if len(a) < len(b) else a[i + 1:] == b[i + 1:]


def word_in(word, name):
    """대상의 낱말이 이 이름에 들어 있는가. 짧은 낱말은 이름 낱말과 같거나 그 낱말의 앞·뒤에 붙어 있어야 한다
    (두 글자 낱말이 긴 낱말의 한가운데에 우연히 들어 있는 것은 맞은 것으로 보지 않는다)."""
    if len(word) <= SHORT:
        return any(other.startswith(word) or other.endswith(word) for other in words(name))
    return word in norm(name)


def typo_in(word, name):
    return len(word) >= TYPO_MIN and any(within_one_edit(word, other) for other in words(name) + [norm(name)])


def coverage(subject, document):
    """이 문서의 이름들이 대상의 낱말을 얼마나 덮는가: (강한 이름만으로 덮인 낱말, 약한 근거까지 써서 덮인 낱말, 전체 낱말)."""
    parts = words(subject)
    names = names_of(document)
    whole = norm(subject)
    # 띄어쓰기만 다른 경우: 대상 전체를 붙여 쓴 글이 이름 안에 그대로 있으면 모든 낱말이 덮인 것이다.
    if len(whole) > SHORT and any(whole in norm(text) for text, firm in names if firm):
        return parts, parts, parts
    strong = [w for w in parts if any(word_in(w, text) for text, firm in names if firm)]
    loose = [w for w in parts if w in strong or any(word_in(w, text) or typo_in(w, text) for text, _ in names)]
    if len(whole) > SHORT and any(whole in norm(text) for text, _ in names):
        loose = parts
    return strong, loose, parts


def card(document, **extra):
    """화면에 보여 줄 문서 한 줄."""
    return dict(run_id=document["run_id"], document_id=document.get("document_id"), name=document.get("name"),
                organization=document.get("organization") or "", title=document.get("title") or "",
                document_type=document.get("document_type") or "", as_of=document.get("as_of") or "",
                pages=document.get("pages"), **extra)


def resolve(subject, documents, mentions=None, equivalent=None):
    """대상 문구 하나를 문서들과 맞춘다.

    반환 status:
    - selected: 강하게 맞는 문서가 하나, 또는 ASK_UP_TO를 넘게 많다(그 문서들 안에서 찾는다)
    - ambiguous: 강하게 맞는 문서가 2~ASK_UP_TO개(되묻는다)
    - unconfirmed: 약하게만 맞거나 일부 낱말만 맞는다(좁히지 않는다)
    - not_found: 대상의 낱말 중 어느 문서 이름에도, 문서 본문에도 없는 것이 있다
    - not_a_document: 비교할 글자가 없는 문구다
    """
    parts = words(subject)
    if not parts:
        return dict(subject=subject, status="not_a_document", documents=[])
    scored = [(document, *coverage(subject, document)[:2]) for document in documents]
    full = [d for d, strong, _ in scored if len(strong) == len(parts)]
    if full:
        status = "ambiguous" if 1 < len(full) <= ASK_UP_TO else "selected"
        if status == "ambiguous" and equivalent is not None and equivalent(full):
            status = "selected"
        return dict(subject=subject, status=status, documents=[card(d, match="strong") for d in full])
    loose = [d for d, _, covered in scored if len(covered) == len(parts)]
    if loose:
        return dict(subject=subject, status="unconfirmed", documents=[card(d, match="weak") for d in loose[:ASK_UP_TO]])
    named = {w for d, _, covered in scored for w in covered}
    missing = [w for w in parts if w not in named]
    # 이름에 없는 낱말이 본문 어디에도 없으면 우리 문서에 없는 대상이다. 본문에 있으면 문서 이름이 아닌 내용 문구다.
    absent = [w for w in missing if len(w) > SHORT and mentions is not None and not mentions(w)]
    partial = [d for d, _, covered in sorted(scored, key=lambda row: -len(row[2])) if covered][:ASK_UP_TO]
    if absent:
        return dict(subject=subject, status="not_found", missing=absent, documents=[card(d, match="partial") for d in partial])
    # 일부 낱말만 이름과 맞거나(예: 기관은 있는데 그 상품 문서는 없음), 이름에는 없고 본문에만 나오는 문구다
    # (문서 이름이 아닌 내용일 수도, 이름표에 적히지 않은 대상일 수도 있다). 좁히지 않고, 답한 뒤 인용 문서를 확인한다.
    return dict(subject=subject, status="unconfirmed", documents=[card(d, match="partial") for d in partial])


def question_subjects(subjects, question):
    """Keep literal names; allow coordinated names to share a trailing phrase in the question."""
    tokens = [s.split() for s in subjects]
    shared = 0
    if len(tokens) > 1:
        for endings in zip(*(reversed(parts) for parts in tokens)):
            if len({norm(word) for word in endings}) != 1:
                break
            shared += 1
    text = norm(question)
    if shared and all(len(parts) > shared for parts in tokens):
        heads = [" ".join(parts[:-shared]) for parts in tokens]
        # The final name and shared qualifier must occur together, after all the other names.
        for i, subject in enumerate(subjects):
            end = text.rfind(norm(subject))
            if end >= 0 and all(norm(h) and norm(h) in text[:end] for j, h in enumerate(heads) if j != i):
                return list(zip(subjects, heads))
    return [(s, None) for s in subjects if norm(s) and norm(s) in text]


def select(subjects, documents, mentions=None, *, question=None, equivalent=None):
    """질문의 대상들을 맞춰 검색 범위를 정한다.

    반환 status와 그래프의 처리:
    - none: 대상이 없다 → 전체에서 찾는다
    - selected: run_ids의 문서 안에서만 찾는다
    - needs_choice: 되묻는다(candidates). 검색·답변을 하지 않는다
    - not_found: 해당 문서가 없다고 답한다(similar). 검색·답변을 하지 않는다
    - unconfirmed: 전체에서 찾되, 답한 뒤 인용 문서가 대상을 언급하는지 확인한다(check_citations)
    """
    subjects = list(dict.fromkeys(s.strip() for s in subjects if s and s.strip()))
    anchored = question_subjects(subjects, question) if question is not None else [(s, None) for s in subjects]
    resolved = []
    for subject, head in anchored:
        match = resolve(subject, documents, mentions, equivalent)
        if head and match["status"] == "unconfirmed":
            owner = resolve(head, documents)
            if owner["status"] == "selected" and len(owner["documents"]) == 1:
                target = owner["documents"][0]["run_id"]
                # The common qualifier can be in the document body instead of its title.
                valid, _ = check_citations(dict(subjects=[match]), [target], documents, mentions)
                if valid:
                    match = dict(owner, subject=subject)
        resolved.append(match)
    named = [r for r in resolved if r["status"] != "not_a_document"]
    base = dict(subjects=resolved, run_ids=None, candidates=[], similar=[])
    if not named:
        return dict(base, status="none")
    missing = [r for r in named if r["status"] == "not_found"]
    if missing:
        return dict(base, status="not_found", similar=missing[0]["documents"], subject=missing[0]["subject"])
    unclear = [r for r in named if r["status"] == "ambiguous"]
    if unclear:
        return dict(base, status="needs_choice", candidates=unclear[0]["documents"], subject=unclear[0]["subject"])
    if any(r["status"] == "unconfirmed" for r in named):
        return dict(base, status="unconfirmed")
    chosen = list(dict.fromkeys(d["run_id"] for r in named for d in r["documents"]))
    return dict(base, status="selected", run_ids=chosen)


def check_citations(selection, cited_run_ids, documents, mentions_in=None):
    """대상 문서를 확정하지 못하고 전체에서 찾았을 때: 인용한 문서들이 질문의 대상을 실제로 언급하는가.

    대상마다 그 낱말을 모두(이름이나 본문에서) 언급하는 인용 문서가 하나는 있어야 한다. 없으면 다른 대상의 문서에서
    값을 가져온 것이다. mentions_in(낱말, run_id)는 그 문서 본문에 낱말이 나오는지 알려 준다.
    반환: (통과 여부, 언급이 없는 대상 문구 목록)
    """
    by_run = {d["run_id"]: d for d in documents}
    failed = []
    for subject in selection.get("subjects") or []:
        if subject["status"] == "not_a_document":
            continue
        if subject["status"] == "selected":
            if not any(d["run_id"] in cited_run_ids for d in subject["documents"]):
                failed.append(subject["subject"])
            continue
        parts, whole = words(subject["subject"]), norm(subject["subject"])

        def says(run_id, word):
            document = by_run.get(run_id)
            named = document is not None and any(word_in(word, text) or typo_in(word, text) or
                                                 (len(whole) > SHORT and whole in norm(text))
                                                 for text, _ in names_of(document))
            return named or (mentions_in is not None and mentions_in(word, run_id))

        if not any(all(says(run_id, w) for w in parts) for run_id in dict.fromkeys(cited_run_ids)):
            failed.append(subject["subject"])
    return not failed, failed
