# 역할: 문서 메타데이터 저장소(rag.document_metadata). 모델이 뽑은 값과 사람이 고친 값을 따로 두고 합쳐서 보여 준다.
"""문서 메타데이터 저장.

문서(document_id = 원본 파일 해시)마다 한 줄이다. 실행이 아니라 문서에 붙으므로 다시 적재해도 남는다.
- extracted: 모델이 뽑고 원문 대조를 통과한 값(rag.metadata.extract). 다시 뽑으면 덮어쓴다.
- edited: 사람이 화면에서 고친 값. 모델 값보다 우선하고, 다시 뽑아도 지워지지 않는다.
view()가 둘을 합쳐 항목마다 어디서 온 값인지(origin) 알려 준다. 어느 쪽도 사람 검증을 거친 사실로 공개하는 값이 아니다.
"""
import json
import threading

from doc_understanding.storage.staging.local import query_params, sql, sql_params

FIELDS = ("organization", "title", "document_type", "as_of")
TABLE = """CREATE TABLE IF NOT EXISTS rag.document_metadata (
 document_id text PRIMARY KEY,
 extracted jsonb,
 edited jsonb NOT NULL DEFAULT '{}'::jsonb,
 extracted_at timestamptz,
 edited_at timestamptz
);"""

_ready = threading.Event()
_lock = threading.Lock()


def ensure():
    if _ready.is_set():
        return
    with _lock:
        if not _ready.is_set():
            sql(TABLE)
            _ready.set()


def put_extracted(document_id, extracted):
    """모델이 뽑은 결과를 저장한다(같은 문서의 이전 결과를 덮어쓴다). 사람이 고친 값은 건드리지 않는다."""
    ensure()
    sql_params("INSERT INTO rag.document_metadata(document_id, extracted, extracted_at) VALUES (%s, %s::jsonb, now()) "
               "ON CONFLICT (document_id) DO UPDATE SET extracted=EXCLUDED.extracted, extracted_at=EXCLUDED.extracted_at",
               [document_id, json.dumps(extracted, ensure_ascii=False)])


def put_edit(document_id, changes):
    """사람이 고친 값을 저장한다. changes: 항목 → 글자(빈 글자면 '없음'으로 고정), None이면 그 항목의 수정을 지운다.
    aliases는 글자 목록이다."""
    allowed = {k: v for k, v in changes.items() if k in FIELDS + ("aliases",)}
    if not allowed:
        return
    ensure()
    changed, removed = {}, []
    for key, value in allowed.items():
        if value is None:
            removed.append(key)
        elif key == "aliases":
            changed[key] = list(dict.fromkeys(" ".join(str(a).split())[:60] for a in value if str(a).strip()))[:12]
        else:
            changed[key] = " ".join(str(value).split())[:200]
    # 읽고 고쳐 쓰지 않는다: 저장된 수정에서 지울 항목을 빼고 바뀐 항목을 덮는 일을 문장 하나로 한다.
    # 두 사람이 같은 문서의 다른 항목을 동시에 고쳐도 서로의 수정을 지우지 않는다.
    sql_params("INSERT INTO rag.document_metadata(document_id, edited, edited_at) VALUES (%s, %s::jsonb, now()) "
               "ON CONFLICT (document_id) DO UPDATE SET edited=(rag.document_metadata.edited "
               "- ARRAY(SELECT jsonb_array_elements_text(%s::jsonb))) || EXCLUDED.edited, edited_at=EXCLUDED.edited_at",
               [document_id, json.dumps(changed, ensure_ascii=False), json.dumps(removed)])


def _rows(document_ids):
    ids = list(dict.fromkeys(d for d in document_ids if d))
    if not ids:
        return {}
    ensure()
    rows = query_params("SELECT document_id, extracted, edited FROM rag.document_metadata WHERE document_id = ANY(%s)",
                        [ids])
    return {r["document_id"]: r for r in rows}


def extracted_with(document_ids, prompt_sha256):
    """이 지시문으로 이미 뽑은 문서 ID(다시 뽑지 않기 위해)."""
    return {d for d, r in _rows(document_ids).items()
            if (r.get("extracted") or {}).get("prompt_sha256") == prompt_sha256
            and (r["extracted"].get("status") in ("extracted", "empty", "no_text"))}


def merge(extracted, edited):
    """모델 값과 사람이 고친 값을 합친 한 문서의 메타데이터."""
    extracted, edited = extracted or {}, edited or {}
    fields = extracted.get("fields") or {}
    out, origin, evidence = {}, {}, {}
    for name in FIELDS:
        if name in edited:
            out[name], origin[name] = edited[name], "human"
        elif name in fields:
            out[name], origin[name] = fields[name]["value"], "model"
            evidence[name] = dict(quote=fields[name]["quote"], page=fields[name]["page"])
        else:
            out[name] = ""
    if "aliases" in edited:
        aliases = [dict(text=a, origin="human") for a in edited["aliases"]]
    else:
        aliases = [dict(text=a["text"], origin="model" if a.get("in_source") else "model_unverified")
                   for a in extracted.get("aliases") or []]
    # 기관과 문서 이름이 모두 없으면 이 문서를 가리킬 수 없다. 화면이 '확인 필요'로 표시한다.
    needs_review = not out["organization"] and not out["title"]
    return dict(out, aliases=aliases, summary=extracted.get("summary") or "", origin=origin, evidence=evidence,
                status=extracted.get("status") or ("edited" if edited else "missing"), needs_review=needs_review,
                dropped=extracted.get("dropped") or [])


def view(document_ids):
    """문서 ID 몇 개의 합쳐진 메타데이터. 저장된 것이 없는 문서는 status='missing'으로 준다."""
    rows = _rows(document_ids)
    return {d: merge((rows.get(d) or {}).get("extracted"), (rows.get(d) or {}).get("edited"))
            for d in dict.fromkeys(i for i in document_ids if i)}
