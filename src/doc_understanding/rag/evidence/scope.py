# 역할: 구조 검토 후보(ADR-078)가 표에 남긴 적용 범위·레이아웃 판정을 읽는다. 판정을 새로 만들지 않는다.
import json

SCOPE_CODE = "STRUCTURE_REVIEW_SCOPE"
LAYOUT_CODE = "STRUCTURE_REVIEW_LAYOUT"


def table_scope(table):
    """검토 후보 적용 범위 [{content_id, text_raw, source, status}]. 없으면 빈 목록."""
    out = []
    for issue in table.issues:
        if issue.code != SCOPE_CODE:
            continue
        data = json.loads(issue.message)
        out.append(dict(content_id=data.get("content_id"), text_raw=(data.get("text_raw") or "").strip(),
                        decision_source=data.get("source"), status="structure_review_candidate",
                        source_references=[r.model_dump(mode="json") for r in issue.source_references]))
    return out


def layout_reviewed(table):
    return any(i.code == LAYOUT_CODE for i in table.issues)
